import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, writeFile, mkdir } from "node:fs/promises";
import { spawn } from "node:child_process";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { RecordingFiles } from "../src/fs-store.mjs";
import { writeSkillArtifact, runIsolatedScript, asStringArgs } from "../src/skill-package-tools.mjs";
import { exportRecordingSkill, reexportCatalogSkills, stableSkillId } from "../src/skill-export/start-export-session.mjs";
import { upsertExportedSkill, listExportedSkills, skillManifestFromExport } from "../src/skill-export/skill-catalog.mjs";
import { readGeneratorGuides, REQUIRED_GUIDE_FILES } from "../src/skill-export/read-guides.mjs";
import { validateSkillPackageDir } from "../src/skill-export/validator.mjs";
import { createExportToolHost, describeExportPiTools, describePiTools } from "../src/pi-tools.mjs";
import { packSkill4Artifacts } from "../src/skill-export/pack.mjs";
import { consumerContract } from "../src/skill-export/contract-materialize.mjs";
import { extractAuthHeadersFromEvidence } from "../src/skill-export/auth-resolve.mjs";
import { writeTokenRecord } from "../src/skill-export/token-store.mjs";
import { writeAuthVault, vaultFilePath, usableAuthHeaders } from "../src/auth-vault.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const DRAFT = {
  title: "日报填报",
  capabilities: [
    { capability_id: "query", name: "查询", kind: "query" },
    { capability_id: "create", name: "填报", kind: "create" },
  ],
  steps: [],
  links: [],
  capability_relations: [{ from: "query", to: "create" }],
  unresolved: [],
};

test("调查工具不含写包，出包工具不含浏览器", () => {
  const live = describePiTools().map((item) => item.name);
  assert.ok(live.includes("submit_recording_result"));
  assert.ok(!live.includes("write_skill_artifact"));
  assert.ok(!live.includes("read_export_contract"));
  const exported = describeExportPiTools().map((item) => item.name);
  assert.ok(exported.includes("read_export_contract"));
  assert.ok(exported.includes("read_skill_artifact"));
  assert.ok(exported.includes("submit_skill_export"));
  assert.ok(!exported.includes("control_in_app_browser"));
  assert.ok(!exported.includes("list_recording_index"));
  assert.ok(!exported.includes("read_evidence_item"));
  assert.ok(!exported.includes("submit_recording_result"));
});

test("出包只吃已交能力，没有能力就不开 Skill 4", async () => {
  const host = createExportToolHost({
    files: { readDraft: async () => null },
    recordingId: "rec_contract",
    draft: DRAFT,
  });
  assert.deepEqual(Object.keys(host).sort(), [
    "project_contract_to_request",
    "read_export_contract",
    "read_generator_guides",
    "read_skill_artifact",
    "run_isolated_script",
    "submit_skill_export",
    "validate_skill_package",
    "write_skill_artifact",
  ]);
  const contract = await host.read_export_contract();
  assert.deepEqual(Object.keys(contract).sort(), [
    "capabilities",
    "capability_relations",
    "links",
    "steps",
    "title",
    "unresolved",
  ]);
  assert.equal(contract.capabilities.length, 2);

  const root = await mkdtemp(path.join(os.tmpdir(), "dano-no-caps-"));
  const files = new RecordingFiles(root);
  let started = 0;
  const outcome = await exportRecordingSkill({
    files,
    recordingId: "rec_empty_caps",
    draft: { title: "空", capabilities: [] },
    createExportSession: async () => {
      started += 1;
      return { beginSkillExport: async () => {}, close: async () => {} };
    },
  });
  assert.equal(started, 0);
  assert.equal(outcome.status, "export_failed");
  assert.match(String(outcome.errors?.[0] || ""), /没有可导出的能力/);
});

test("doc/ 四份生成规范齐全", async () => {
  const guides = await readGeneratorGuides();
  assert.equal(guides.ok, true, guides.error);
  const names = new Set(guides.files.map((item) => path.posix.basename(item.path)));
  for (const name of REQUIRED_GUIDE_FILES) {
    assert.ok(names.has(name), name);
  }
});

test("缺少 SKILL.md 或鉴权节时校验失败", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "dano-validate-"));
  const missing = await validateSkillPackageDir(root);
  assert.equal(missing.ok, false);
  assert.ok(missing.issues.some((item) => item.code === "missing_file"));
});

test("目录按 recording_id 重导覆盖同一条并升 version", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "dano-catalog-"));
  const files = new RecordingFiles(root);
  const first = await upsertExportedSkill(files, skillManifestFromExport({
    skillId: "oa.rec_same",
    title: "日报",
    subsystem: "oa",
    recordingId: "rec_same",
    resultId: "uuid-1",
    exportPath: path.join(root, "out", "a"),
    draft: DRAFT,
  }));
  const second = await upsertExportedSkill(files, skillManifestFromExport({
    skillId: "oa.rec_same",
    title: "日报填报",
    subsystem: "oa",
    recordingId: "rec_same",
    resultId: "uuid-1",
    exportPath: path.join(root, "out", "b"),
    draft: DRAFT,
  }));
  const rows = await listExportedSkills(files);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].name, "oa.rec_same");
  assert.equal(rows[0].recording_id, "rec_same");
  assert.equal(rows[0].title, "日报填报");
  assert.equal(second.version, Number(first.version || 1) + 1);
});

test("每次导出都重开 Skill 4，不因旧 SKILL.md 跳过", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "dano-export-"));
  const files = new RecordingFiles(root);
  const recordingId = "rec_always";
  await files.writeDraft(recordingId, { draft: DRAFT, title: DRAFT.title });
  await writeSkillArtifact(files, recordingId, "SKILL.md", "# stale\n");
  let started = 0;
  const outcome = await exportRecordingSkill({
    files,
    recordingId,
    title: DRAFT.title,
    tenant: "acme",
    subsystem: "oa",
    existingSkillId: "oa.rec_always",
    outDir: path.join(root, "out"),
    createExportSession: async ({ tools }) => {
      started += 1;
      return {
        beginSkillExport: async () => {
          await tools.submit_skill_export({ ok: true, skill_id: "oa.rec_always", routes: [] });
        },
        close: async () => {},
      };
    },
    packArtifacts: async () => ({
      export_path: path.join(root, "out", "skill"),
      token_missing: true,
    }),
  });
  assert.equal(started, 1);
  assert.equal(outcome.status, "exported");
  assert.equal(outcome.skill_id, "oa.rec_always");
  assert.equal(outcome.catalog_item.recording_id, "rec_always");
});

test("目录重导沿用同一 skill_id，并使用 overlay 最新能力", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "dano-reexport-"));
  const files = new RecordingFiles(root);
  await upsertExportedSkill(files, skillManifestFromExport({
    skillId: "oa.keep_id",
    title: "日报",
    subsystem: "oa",
    recordingId: "rec_keep",
    resultId: "uuid-keep",
    exportPath: path.join(root, "old"),
    draft: DRAFT,
  }));
  const seen = [];
  const overlay = {
    ...DRAFT,
    title: "最新日报",
    capabilities: [...DRAFT.capabilities, { capability_id: "submit", name: "提交", kind: "submit" }],
  };
  const outcome = await reexportCatalogSkills({
    files,
    outDir: path.join(root, "out"),
    tenant: "acme",
    drafts: { rec_keep: overlay },
    exportOne: async (req) => {
      seen.push({
        existingSkillId: req.existingSkillId,
        title: req.draft?.title,
        capabilityCount: req.draft?.capabilities?.length,
      });
      return { status: "exported", export_path: path.join(root, "out", "skill") };
    },
  });
  assert.deepEqual(seen, [{ existingSkillId: "oa.keep_id", title: "最新日报", capabilityCount: 3 }]);
  assert.equal(outcome.count, 1);
  assert.equal(stableSkillId({ existing: "oa.keep_id", recordingId: "rec_other" }), "oa.keep_id");
});

const HANDBOOK = `# 日报填报

## 选择工作流
默认完整办理：先查询再填报。

## 执行协议
Done when: 填报成功。

## 按需读取资源
需要字段时读 INPUT_FORMS.md。

## 鉴权
没有 auth.local.json 则停止，要求提供 token。
`;

const CONTRACT = {
  capabilities: [
    {
      capability_id: "query",
      name: "查询",
      kind: "query",
      request_refs: [{ usage: "option_source", path: "http://oa.example.com/api/options" }],
    },
    { capability_id: "create", name: "填报", kind: "create" },
  ],
  routes: [{ route_id: "default", steps: ["query", "create"] }],
  steps: [{ step_id: "s1", path: "http://oa.example.com/api/list", usage: "execute" }],
};

async function writeValidSkill4Package(files, recordingId) {
  await writeSkillArtifact(files, recordingId, "SKILL.md", HANDBOOK);
}

test("overlay draft 优先于磁盘旧合同", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "dano-overlay-"));
  const files = new RecordingFiles(root);
  const recordingId = "rec_overlay";
  await files.writeDraft(recordingId, { draft: DRAFT, title: "旧合同" });
  const overlay = {
    ...DRAFT,
    title: "新合同",
    capabilities: [...DRAFT.capabilities, { capability_id: "submit", name: "提交", kind: "submit" }],
  };
  let contract = null;
  let packedDraft = null;
  const outcome = await exportRecordingSkill({
    files,
    recordingId,
    title: overlay.title,
    tenant: "acme",
    subsystem: "oa",
    draft: overlay,
    outDir: path.join(root, "out"),
    createExportSession: async ({ tools }) => ({
      beginSkillExport: async () => {
        contract = await tools.read_export_contract();
        await tools.submit_skill_export({ ok: true, skill_id: "oa.should_not_win", routes: [] });
      },
      close: async () => {},
    }),
    packArtifacts: async ({ draft }) => {
      packedDraft = draft;
      return { export_path: path.join(root, "out", "skill"), token_missing: true };
    },
  });
  assert.equal(outcome.status, "exported");
  assert.equal(contract?.title, "新合同");
  assert.equal(contract?.capabilities?.length, 3);
  assert.equal(packedDraft?.capabilities?.length, 3);
  const saved = await files.readDraft(recordingId);
  assert.equal(saved.draft.capabilities.length, 3);
});

test("Skill 4 产物打包注入 auth 并重导同一条", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "dano-pack-reexport-"));
  const files = new RecordingFiles(root);
  const recordingId = "rec_pack_same";
  const outDir = path.join(root, "out");
  const authHeaders = { Authorization: "Bearer test-token-value-12345", "Tenant-Id": "1" };
  const first = await exportRecordingSkill({
    files,
    recordingId,
    title: DRAFT.title,
    tenant: "acme",
    subsystem: "oa",
    draft: DRAFT,
    outDir,
    authHeaders,
    existingSkillId: "oa.rec_pack_same",
    createExportSession: async ({ tools }) => ({
      beginSkillExport: async () => {
        await writeValidSkill4Package(files, recordingId);
        await tools.submit_skill_export({ ok: true, skill_id: "oa.should_not_win", routes: CONTRACT.routes });
      },
      close: async () => {},
    }),
  });
  assert.equal(first.status, "exported", (first.errors || []).join("; "));
  assert.equal(first.skill_id, "oa.rec_pack_same");
  assert.equal(first.version, 1);
  const auth = JSON.parse(await readFile(path.join(first.export_path, "config", "auth.local.json"), "utf8"));
  assert.equal(auth.headers.Authorization, authHeaders.Authorization);
  const clientSrc = await readFile(path.join(first.export_path, "scripts", "client.py"), "utf8");
  assert.match(clientSrc, /"subsystem":\s*"oa"/);
  assert.doesNotMatch(clientSrc, /test-token-value-12345/);
  assert.doesNotMatch(clientSrc, /JSON\.stringify\(JSON\.stringify/);
  const handbook = await readFile(path.join(first.export_path, "SKILL.md"), "utf8");
  assert.match(handbook, /默认完整办理/);
  assert.doesNotMatch(handbook, /test-token-value-12345/);

  const overlay = {
    ...DRAFT,
    title: "日报填报（已改）",
    capabilities: [
      ...DRAFT.capabilities,
      { capability_id: "submit", name: "提交", kind: "submit", input_schema: { properties: { formId: { type: "string" } } } },
    ],
  };
  const second = await exportRecordingSkill({
    files,
    recordingId,
    title: overlay.title,
    tenant: "acme",
    subsystem: "oa",
    draft: overlay,
    outDir,
    authHeaders,
    createExportSession: async ({ tools }) => ({
      beginSkillExport: async () => {
        await writeValidSkill4Package(files, recordingId);
        await tools.submit_skill_export({ ok: true, skill_id: "oa.renamed_by_skill4", routes: CONTRACT.routes });
      },
      close: async () => {},
    }),
  });
  assert.equal(second.status, "exported", (second.errors || []).join("; "));
  assert.equal(second.skill_id, "oa.rec_pack_same");
  assert.equal(second.version, 2);
  assert.equal(second.catalog_item.name, first.catalog_item.name);
  assert.equal(second.catalog_item.recording_id, recordingId);
  assert.ok(second.catalog_item.parameters.properties.formId);
  const rows = await listExportedSkills(files);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].name, "oa.rec_pack_same");
  assert.equal(rows[0].version, 2);
});

test("没有能力拒绝猜编译，有合同则运输物化整包", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "dano-pack-"));
  const files = new RecordingFiles(root);
  await assert.rejects(
    () => packSkill4Artifacts({
      files,
      recordingId: "rec_empty",
      outDir: path.join(root, "out-empty"),
      skillId: "oa.empty",
      draft: { capabilities: [] },
      authHeaders: {},
    }),
    /没有可导出的能力/,
  );
  const packed = await packSkill4Artifacts({
    files,
    recordingId: "rec_from_contract",
    outDir: path.join(root, "out"),
    skillId: "oa.rec_from_contract",
    tenant: "acme",
    subsystem: "oa",
    draft: DRAFT,
    authHeaders: { Authorization: "Bearer pack-token-value-123" },
    baseUrl: "https://oa.example.com",
  });
  const contract = JSON.parse(await readFile(path.join(packed.export_path, "references", "CONTRACT.json"), "utf8"));
  assert.deepEqual(contract.capabilities.map((item) => item.capability_id), ["query", "create"]);
  assert.ok(contract.routes.some((item) => item.route_id === "default" && item.steps.length === 2));
  const flow = await readFile(path.join(packed.export_path, "scripts", "flow.py"), "utf8");
  assert.match(flow, /import runtime/);
  const clientSrc = await readFile(path.join(packed.export_path, "scripts", "client.py"), "utf8");
  assert.doesNotMatch(clientSrc, /def request\s*\(/);
  const runtime = await readFile(path.join(packed.export_path, "scripts", "runtime.py"), "utf8");
  assert.match(runtime, /load_contract/);
});

test("导出实现不引用 back", async () => {
  const files = [
    "src/skill-export/start-export-session.mjs",
    "src/skill-export/pack.mjs",
    "src/skill-export/contract-materialize.mjs",
    "src/skill-export/validator.mjs",
    "src/skill-export/skill-catalog.mjs",
    "src/skill-package-tools.mjs",
    "src/pi-tools.mjs",
  ];
  for (const rel of files) {
    const text = await readFile(path.join(ROOT, rel), "utf8");
    assert.doesNotMatch(text, /BACK_ROOT|from dano|back\/dano/);
  }
});

test("隔离运行拷到产物目录外，并展开对象参数", async () => {
  assert.deepEqual(asStringArgs([{ "--help": true }]), ["--help"]);
  assert.deepEqual(asStringArgs([{ args: "--help" }]), ["--help"]);
  assert.deepEqual(asStringArgs(["--route", "default"]), ["--route", "default"]);
  const root = await mkdtemp(path.join(os.tmpdir(), "dano-isolated-"));
  const files = new RecordingFiles(root);
  const recordingId = "rec_iso";
  await writeSkillArtifact(files, recordingId, "scripts/hello.py", [
    "import json, sys",
    "if '--help' in sys.argv:",
    "    print(json.dumps({'ok': True}))",
    "    raise SystemExit(0)",
    "raise SystemExit(2)",
    "",
  ].join("\n"));
  const ran = await runIsolatedScript(files, recordingId, "scripts/hello.py", [{ "--help": true }]);
  assert.equal(ran.ok, true, ran.error || ran.stderr);
  assert.match(String(ran.stdout || ""), /"ok":\s*true/);
  const parallel = await Promise.all([
    runIsolatedScript(files, recordingId, "scripts/hello.py", [{ args: "--help" }]),
    runIsolatedScript(files, recordingId, "scripts/hello.py", [{ args: "--help" }]),
    runIsolatedScript(files, recordingId, "scripts/hello.py", [{ args: "--help" }]),
  ]);
  for (const item of parallel) {
    assert.equal(item.ok, true, item.error || item.stderr);
  }
});

test("Skill 4 已提交后超时仍打包同一条", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "dano-timeout-pack-"));
  const files = new RecordingFiles(root);
  const recordingId = "rec_timeout_pack";
  await files.writeDraft(recordingId, { draft: DRAFT, title: DRAFT.title });
  const outDir = path.join(root, "out");
  const outcome = await exportRecordingSkill({
    files,
    recordingId,
    title: DRAFT.title,
    tenant: "acme",
    subsystem: "oa",
    outDir,
    existingSkillId: "oa.rec_timeout_pack",
    createExportSession: async ({ tools }) => ({
      beginSkillExport: async () => {
        await writeValidSkill4Package(files, recordingId);
        await tools.submit_skill_export({ ok: true, skill_id: "oa.should_keep", routes: CONTRACT.routes });
        throw new Error("Skill 4 出包超时");
      },
      close: async () => {},
    }),
  });
  assert.equal(outcome.status, "exported", (outcome.errors || []).join("; "));
  assert.equal(outcome.skill_id, "oa.rec_timeout_pack");
  assert.equal(outcome.version, 1);
  const rows = await listExportedSkills(files);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].name, "oa.rec_timeout_pack");
});

test("出包 PI 不共用录制 agent 目录", async () => {
  const text = await readFile(path.join(ROOT, "src/pi-session.mjs"), "utf8");
  assert.match(text, /exportPiAgentDir/);
  assert.match(text, /pi-agent-export/);
  assert.match(text, /recordingPiAgentDir/);
});

test("sealed 不算有证，登录响应写出完整 token", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "dano-auth-"));
  const files = new RecordingFiles(root);
  const recordingId = "rec_auth";
  await files.initialize(recordingId, { id: recordingId, targetUrl: "https://oa.example.com/web/" });
  await files.appendEvidence(recordingId, {
    kind: "network_request",
    payload: {
      url: "https://oa.example.com/admin-api/oa/x",
      headers: { authorization: "[sealed:auth_ref_abc]", "tenant-id": "1" },
    },
  });
  await files.appendEvidence(recordingId, {
    kind: "network_response",
    payload: {
      url: "https://oa.example.com/admin-api/system/auth/login",
      body: {
        stored: "inline",
        text: JSON.stringify({ code: 0, data: { accessToken: "real-token-value-aaa" } }),
      },
    },
  });
  const headers = await extractAuthHeadersFromEvidence({ files }, recordingId);
  assert.equal(headers.Authorization, "Bearer real-token-value-aaa");
  assert.equal(headers["tenant-id"], "1");
  assert.deepEqual(usableAuthHeaders({ authorization: "[sealed:auth_ref_abc]" }), {});

  const stored = await writeTokenRecord("acme", "oa", {
    authorization: "[sealed:auth_ref_abc]",
    "tenant-id": "1",
  }, { root, merge: false });
  assert.equal(stored.has_token, true);
  assert.equal(stored.headers.Authorization, undefined);
  assert.equal(stored.headers["tenant-id"], "1");
  const sealedOnly = await writeTokenRecord("empty", "oa", {
    authorization: "[sealed:auth_ref_abc]",
  }, { root, merge: false });
  assert.equal(sealedOnly.has_token, false);
});

test("vault 能解封证据里的 authorization", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "dano-vault-"));
  const files = new RecordingFiles(root);
  const recordingId = "rec_vault";
  await files.initialize(recordingId, { id: recordingId, targetUrl: "https://oa.example.com/" });
  await files.appendEvidence(recordingId, {
    kind: "network_request",
    payload: {
      url: "https://oa.example.com/admin-api/system/dept/simple-list",
      headers: { authorization: "[sealed:auth_ref_xyz]" },
    },
  });
  await writeAuthVault(vaultFilePath(files.directory(recordingId)), {
    refs: { auth_ref_xyz: { name: "authorization", value: "Bearer vault-token-value" } },
  });
  const headers = await extractAuthHeadersFromEvidence({ files }, recordingId);
  assert.equal(headers.Authorization, "Bearer vault-token-value");
});

test("写产物禁止套娃子包，打包会剥掉内层 SKILL.md", async () => {
  await assert.rejects(
    () => writeSkillArtifact({ directory: () => os.tmpdir() }, "rec_x", "oa-work-report/SKILL.md", "# no\n"),
    /禁止另开子包|禁止在子目录/,
  );
  const root = await mkdtemp(path.join(os.tmpdir(), "dano-nested-"));
  const files = new RecordingFiles(root);
  const recordingId = "rec_nested";
  await writeValidSkill4Package(files, recordingId);
  const nested = path.join(files.artifactDir(recordingId), "oa-work-report");
  await mkdir(nested, { recursive: true });
  await writeFile(path.join(nested, "SKILL.md"), "# inner\n", "utf8");
  const packed = await packSkill4Artifacts({
    files,
    recordingId,
    outDir: path.join(root, "out"),
    skillId: "oa.rec_nested",
    tenant: "acme",
    subsystem: "oa",
    draft: { ...DRAFT, steps: [{ path: "https://oa.example.com/admin-api/x" }] },
    authHeaders: { Authorization: "Bearer pack-token-value-999", "tenant-id": "1" },
    baseUrl: "https://oa.example.com",
  });
  await assert.rejects(() => readFile(path.join(packed.export_path, "oa-work-report", "SKILL.md"), "utf8"));
  const auth = JSON.parse(await readFile(path.join(packed.export_path, "config", "auth.local.json"), "utf8"));
  assert.equal(auth.headers.Authorization, "Bearer pack-token-value-999");
  const runtime = JSON.parse(await readFile(path.join(packed.export_path, "config", "runtime.json"), "utf8"));
  assert.equal(runtime.base_url, "https://oa.example.com");
  const sealedPack = await packSkill4Artifacts({
    files,
    recordingId,
    outDir: path.join(root, "out-sealed"),
    skillId: "oa.rec_nested",
    draft: DRAFT,
    authHeaders: { authorization: "[sealed:auth_ref_nope]" },
  });
  assert.equal(sealedPack.token_missing, true);
});

test("冻结 client 拒绝 sealed，且没有 request 别名", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "dano-client-"));
  const src = await readFile(path.join(ROOT, "src/skill-export/templates/client.py"), "utf8");
  await mkdir(path.join(root, "scripts"), { recursive: true });
  await mkdir(path.join(root, "config"), { recursive: true });
  await writeFile(path.join(root, "scripts", "client.py"), src.replace("__CONFIG__", JSON.stringify({
    tenant: "acme",
    subsystem: "oa",
    base_url: "https://oa.example.com",
  })), "utf8");
  await writeFile(path.join(root, "config", "auth.local.json"), `${JSON.stringify({
    headers: { authorization: "[sealed:auth_ref_x]" },
  })}\n`, "utf8");
  const sealed = await new Promise((resolve) => {
    const child = spawn("python", [path.join(root, "scripts", "client.py"), "--show-config"], { windowsHide: true });
    let stdout = "";
    child.stdout.on("data", (chunk) => { stdout += String(chunk); });
    child.on("close", (code) => resolve({ code, stdout }));
  });
  assert.match(sealed.stdout, /"has_auth_headers":\s*false/);
  await writeFile(path.join(root, "config", "auth.local.json"), `${JSON.stringify({
    headers: { Authorization: "Bearer live-token-value", "tenant-id": "1" },
  })}\n`, "utf8");
  const live = await new Promise((resolve) => {
    const child = spawn("python", [path.join(root, "scripts", "client.py"), "--show-config"], { windowsHide: true });
    let stdout = "";
    child.stdout.on("data", (chunk) => { stdout += String(chunk); });
    child.on("close", (code) => resolve({ code, stdout }));
  });
  assert.match(live.stdout, /"has_auth_headers":\s*true/);
  assert.doesNotMatch(live.stdout, /live-token-value/);
  assert.doesNotMatch(src, /def request\s*\(/);
});

test("写冻结执行器被拒绝", async () => {
  await assert.rejects(
    () => writeSkillArtifact({ directory: () => os.tmpdir() }, "rec_x", "scripts/flow.py", "print(1)\n"),
    /冻结文件/,
  );
  await assert.rejects(
    () => writeSkillArtifact({ directory: () => os.tmpdir() }, "rec_x", "references/CONTRACT.json", "{}"),
    /冻结文件/,
  );
});

test("物化字段与录制调用方字段一致，flow --help 可读", async () => {
  const resultPath = path.join(ROOT, "data", "rec_0fcf87ab2f2e42c8b5b4500d3c01ac6b", "pi-result.json");
  const draft = JSON.parse(await readFile(resultPath, "utf8"));
  const contract = consumerContract(draft);
  const query = contract.capabilities.find((item) => item.capability_id === "cap_report_statistics_query");
  const create = contract.capabilities.find((item) => item.capability_id === "cap_daily_report_create_submit");
  assert.deepEqual(query.caller_fields.map((item) => item.id), ["reportType", "deptId", "startDate", "endDate"]);
  assert.equal(query.execute.path, "/admin-api/oa/work-report/statistics");
  assert.equal(query.caller_fields.find((item) => item.id === "deptId").dataSource.childrenField, "children");
  assert.deepEqual(create.caller_fields.map((item) => item.id), [
    "startDate", "endDate", "title", "todayContent", "planContent", "issueContent", "remark", "approvalOpinion", "items",
  ]);
  assert.ok(!create.caller_fields.some((item) => ["creator", "companyId", "deptId", "reportType"].includes(item.id)));
  assert.equal(create.execute.path, "/admin-api/oa/work-report/submit");
  assert.ok(create.system_params.some((item) => item.key === "creator" && item.source_kind === "current_user"));
  assert.ok(create.system_params.some((item) => item.key === "processStatus" && item.default_value === -1));

  const root = await mkdtemp(path.join(os.tmpdir(), "dano-materialize-"));
  const files = new RecordingFiles(root);
  const packed = await packSkill4Artifacts({
    files,
    recordingId: "rec_0fcf87ab2f2e42c8b5b4500d3c01ac6b",
    outDir: path.join(root, "out"),
    skillId: "oa.rec_0fcf87ab2f2e42c8b5b4500d3c01ac6b",
    tenant: "acme",
    subsystem: "oa",
    draft,
    authHeaders: { Authorization: "Bearer materialize-token", "tenant-id": "1" },
    baseUrl: "https://ruoyioffice.com",
  });
  const packedContract = JSON.parse(await readFile(path.join(packed.export_path, "references", "CONTRACT.json"), "utf8"));
  assert.deepEqual(
    packedContract.capabilities.find((item) => item.capability_id === "cap_daily_report_create_submit").caller_fields.map((item) => item.id),
    create.caller_fields.map((item) => item.id),
  );
  const forms = await readFile(path.join(packed.export_path, "references", "INPUT_FORMS.md"), "utf8");
  assert.match(forms, /dataSource/);
  assert.match(forms, /childrenField/);
  const help = await new Promise((resolve) => {
    const child = spawn("python", [path.join(packed.export_path, "scripts", "flow.py"), "--help"], {
      cwd: packed.export_path,
      windowsHide: true,
    });
    let stdout = "";
    child.stdout.on("data", (chunk) => { stdout += String(chunk); });
    child.on("close", (code) => resolve({ code, stdout }));
  });
  assert.equal(help.code, 0, help.stdout);
  const payload = JSON.parse(help.stdout);
  assert.equal(payload.default_route, "default");
  assert.ok(payload.routes.some((item) => item.id === "default" && item.steps.includes("cap_daily_report_create_submit")));

  const flatten = await new Promise((resolve) => {
    const scriptsDir = path.join(packed.export_path, "scripts");
    const code = `import sys, json; sys.path.insert(0, ${JSON.stringify(scriptsDir)}); import client; print(json.dumps(client.flatten_options([{"id":1,"name":"总","children":[{"id":2,"name":"子"}]}], id_field="id", label_field="name", children_field="children")))`;
    const child = spawn("python", ["-c", code], { windowsHide: true });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += String(chunk); });
    child.stderr.on("data", (chunk) => { stderr += String(chunk); });
    child.on("close", (code) => resolve({ code, stdout, stderr }));
  });
  assert.equal(flatten.code, 0, flatten.stderr);
  assert.deepEqual(JSON.parse(flatten.stdout), [
    { id: 1, label: "总" },
    { id: 2, label: "子" },
  ]);
});
