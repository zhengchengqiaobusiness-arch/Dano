import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { writeSkillFile } from "../src/skillpack/files.mjs";
import { resultLine } from "../src/agent/pi-session.mjs";
import { actionEvidenceSummary, apiFieldRows, ASK_PROTOCOL, askCoverageObservations, askProtocolMissing, authOverlayParseError, authStoppedOutput, fieldAlignment, frontmatterErrors, hardcodedOriginError, jsonDefault, jsonWording, overlayLayersFromBodies, skillCommandLines, squashProtocol, structureErrorsForFile, verifySkill } from "../src/skillpack/verify.mjs";
import { jsNonStdlibImports, nonStdlibImports } from "../src/skillpack/stdlib-imports.mjs";
import { docDir, skillDir } from "../src/paths.mjs";
import { appendEvidence } from "../src/evidence/store.mjs";
import { GUIDE_NAMES } from "../src/agent/guides.mjs";
import { applyVerifyToRecording, publicSessionStatus } from "../src/session.mjs";

const recording = { id: "rec_verify", tenant: "", subsystem: "app", skillId: "app.rec_verify", startUrl: "http://127.0.0.1/" };
const API_HEADINGS = "## 字段\n\n## 绑定\n\n## 未解决\n\n## 已验证读命令\n";

async function useData() {
  process.env.CABP_DATA = await mkdtemp(path.join(os.tmpdir(), "cabp-verify-"));
}

test("verify reports each code", async () => {
  await useData();
  const dir = skillDir(recording.skillId);
  let result = await verifySkill(dir, recording.id, recording);
  assert.ok(result.errors.some((item) => item.code === "missing_file"));

  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "Bearer abc.def-token\n");
  await writeFile(path.join(dir, "scripts", "client.py"), "");
  await writeFile(path.join(dir, "references/api.md"), "");
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
  result = await verifySkill(dir, recording.id, recording);
  assert.ok(result.errors.some((item) => item.code === "secret_in_source"));

  await writeFile(path.join(dir, "SKILL.md"), "python scripts/client.py list\n");
  result = await verifySkill(dir, recording.id, recording);
  assert.ok(result.errors.some((item) => item.code === "command_not_run"));

  await writeFile(path.join(dir, "references/api.md"), "## 字段\n- page_name: p\n- evidence_ids: missing_id\n\n## 未解决\n- field: pendingField\n\n## 已验证读命令\n");
  await writeFile(path.join(dir, "SKILL.md"), "python scripts/client.py list\npendingField\n");
  result = await verifySkill(dir, recording.id, recording);
  assert.ok(result.errors.some((item) => item.code === "evidence_missing"));

  await writeSkillFile(recording.id, recording.skillId, "SKILL.md", "python scripts/client.py list\n");
  await writeFile(path.join(dir, "SKILL.md"), "python scripts/client.py list\nchanged\n");
  result = await verifySkill(dir, recording.id, recording);
  assert.ok(result.errors.some((item) => item.code === "handbook_rewritten"));
});

test("templates and writes are not re-run, filled names stay on the result", async () => {
  await useData();
  const dir = skillDir(recording.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\n---\npython scripts/client.py list-statistics <deptId>\npython scripts/client.py submit-report {\"a\":1}\npython3 scripts/client.py dept-list\n标题\n工作内容\n");
  await writeFile(path.join(dir, "scripts/client.py"), "def list_statistics():\n    method = \"GET\"\n\ndef submit_report():\n    method = \"POST\"\n\ndef dept_list():\n    return \"/admin-api/system/dept/simple-list\"\n");
  await writeFile(path.join(dir, "references/api.md"), "");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
  await appendEvidence(recording.id, {
    kind: "verify",
    ok: true,
    argv: ["python", "scripts/client.py", "list-statistics", "104"],
    summary: "python scripts/client.py list-statistics 104",
  });
  await appendEvidence(recording.id, {
    kind: "verify",
    ok: true,
    argv: ["python", "scripts/client.py", "dept-list"],
    summary: "python scripts/client.py dept-list",
  });
  await appendEvidence(recording.id, { kind: "action", summary: "fill:textbox \"标题\"|textbox \"备注\"|textbox \"请输入工作内容\"" });
  await appendEvidence(recording.id, {
    kind: "network",
    summary: "GET /admin-api/system/dept/simple-list",
    body: {
      method: "GET",
      path: "/admin-api/system/dept/simple-list",
      response_body: JSON.stringify({ data: [{ id: 1, name: "甲" }, { id: 2, name: "乙" }] }),
    },
  });
  await appendEvidence(recording.id, {
    kind: "network",
    summary: "GET /admin-api/system/dict-type/list-all-simple",
    body: {
      method: "GET",
      path: "/admin-api/system/dict-type/list-all-simple",
      response_body: JSON.stringify({ data: [{ id: 1, name: "甲" }, { id: 2, name: "乙" }] }),
    },
  });
  const result = await verifySkill(dir, recording.id, recording);
  assert.equal(result.errors.some((item) => item.command && item.command.includes("list-statistics")), false);
  assert.equal(result.errors.some((item) => item.command && item.command.includes("submit-report")), false);
  assert.equal(result.errors.some((item) => item.code === "caller_field_missing" || item.code === "live_options_missing"), false);
  assert.ok(result.filled.some((label) => label.includes("标题")));
  assert.ok(result.filled.some((label) => label.includes("工作内容")));
  assert.ok(result.requests.some((item) => item.path === "/admin-api/system/dept/simple-list"));
});

test("verify returns the request keys and filled names without judging them", async () => {
  await useData();
  const shape = { id: "rec_verify_shape", tenant: "", subsystem: "app", skillId: "app.rec_verify_shape", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\n---\npython scripts/client.py show-config\n| 今日工作内容 | 是 |\n| 协调事项 | 否 |\n");
  await writeFile(path.join(dir, "scripts/client.py"), 'def show_config():\n    payload = {"subsystem": "oa", "owner": "1", "ownerName": "", "groupId": 103}\n    page = 20\n');
  await writeFile(path.join(dir, "references/api.md"), "## 未解决\n- path: /api/noise\n\nfiles[].kind\n");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
  await appendEvidence(shape.id, {
    kind: "network",
    summary: "GET /api/session",
    body: { method: "GET", path: "/api/session", response_body: JSON.stringify({ data: { id: 1, groupId: 103, name: "甲", zone: "oa", pageSize: 20 } }) },
  });
  await appendEvidence(shape.id, {
    kind: "network",
    summary: "GET /api/options",
    body: { method: "GET", path: "/api/options", response_body: JSON.stringify({ data: [{ id: 10, name: "甲" }, { id: 11, name: "乙" }] }) },
  });
  await appendEvidence(shape.id, {
    kind: "network",
    summary: "GET /api/noise",
    body: { method: "GET", path: "/api/noise", response_body: JSON.stringify({ data: [{ id: 3, name: "丙" }, { id: 4, name: "丁" }] }) },
  });
  await appendEvidence(shape.id, {
    kind: "network",
    summary: "POST /api/upload",
    body: { method: "POST", path: "/api/upload", response_body: JSON.stringify({ data: "https://files.example/a.bin" }) },
  });
  await appendEvidence(shape.id, {
    kind: "network",
    summary: "POST /api/save",
    body: {
      method: "POST",
      path: "/api/save",
      post_data: JSON.stringify({ owner: "1", ownerName: "甲", groupId: 103, lines: [{ kind: 1, text: "甲" }] }),
      response_body: JSON.stringify({ data: 1 }),
    },
  });
  await appendEvidence(shape.id, { kind: "action", summary: "fill:textbox \"请输入协调事项\"|textbox \"请输入工作内容\"" });
  await appendEvidence(shape.id, { kind: "action", summary: "click", body: { uploaded: true, clicked: "button \"附件\"" } });
  const result = await verifySkill(dir, shape.id, shape);
  const judged = new Set(["caller_field_missing", "caller_field_optional", "caller_arg_missing", "sample_literal", "live_options_missing", "upload_not_written", "key_omitted", "follow_get_missing", "unresolved_adopted", "extra_required"]);
  assert.equal(result.errors.some((item) => judged.has(item.code)), false);
  assert.ok(result.filled.some((label) => label.includes("工作内容")));
  assert.ok(result.requests.some((item) => item.path === "/api/save" && item.keys.includes("owner")));
});

test("a key counts only inside the function that cites the path", async () => {
  await useData();
  const shape = { id: "rec_verify_gate", tenant: "", subsystem: "app", skillId: "app.rec_verify_gate", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\n---\n| 工作内容 | 是 |\n");
  await writeFile(path.join(dir, "scripts/client.py"), [
    "def save_item():",
    "    path = \"/api/save\"",
    "    if args.owner:",
    "        body[\"owner\"] = args.owner",
    "",
    "def list_items():",
    "    return item.get(\"state\")",
    "",
    "def dict_rows():",
    "    return \"/api/dict\"",
    "",
    "def main():",
    "    if args.command == \"dict-rows\":",
    "        dict_rows()",
    "    p = sub.add_parser(\"dict-rows\")",
    "    p.add_argument(\"--kind\", required=True, help=\"类别\")",
    "    p.set_defaults(func=dict_rows)",
    "    q = sub.add_parser(\"save\")",
    "    q.add_argument(\"--title\", required=True, help=\"汇报标题\")",
    "    q.set_defaults(func=save_item)",
  ].join("\n"));
  await writeFile(path.join(dir, "references/api.md"), "## 未解决\n- path: /api/dict\n");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
  await appendEvidence(shape.id, {
    kind: "network",
    summary: "POST /api/save",
    body: {
      method: "POST",
      path: "/api/save",
      post_data: JSON.stringify({ owner: "甲", state: 2 }),
      response_body: JSON.stringify({ data: 41 }),
    },
  });
  await appendEvidence(shape.id, {
    kind: "network",
    summary: "GET /api/item",
    body: { method: "GET", path: "/api/item", query: "id=41", response_body: JSON.stringify({ data: { id: 41 } }) },
  });
  await appendEvidence(shape.id, {
    kind: "network",
    summary: "GET /api/dict",
    body: { method: "GET", path: "/api/dict", query: "", response_body: JSON.stringify({ data: {} }) },
  });
  await appendEvidence(shape.id, { kind: "action", summary: "fill:textbox \"请输入工作内容\"" });
  const result = await verifySkill(dir, shape.id, shape);
  assert.ok(result.errors.some((item) => item.code === "key_not_written" && item.key === "state"));
  assert.equal(result.errors.some((item) => item.key === "owner"), false);
  assert.equal(result.errors.some((item) => ["key_omitted", "follow_get_missing", "unresolved_adopted", "extra_required", "caller_arg_missing"].includes(item.code)), false);
  assert.ok(result.filled.some((label) => label.includes("工作内容")));
  assert.ok(result.requests.some((item) => item.path === "/api/save" && item.keys.includes("state") && item.keys.includes("owner")));
});

test("a click that writes a file is recorded as an upload, and a credential request must be replayed", async () => {
  assert.equal(actionEvidenceSummary("click", { uploaded: true, clicked: "button \"上传\"" }), "upload:button \"上传\"");
  assert.equal(actionEvidenceSummary("fill", { filled: ["textbox \"标题\""] }), "fill:textbox \"标题\"");
  await useData();
  const shape = { id: "rec_verify_cred", tenant: "", subsystem: "app", skillId: "app.rec_verify_cred", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\n");
  await writeFile(path.join(dir, "scripts/client.py"), "import json\n");
  await writeFile(path.join(dir, "references/api.md"), "");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
  await appendEvidence(shape.id, {
    kind: "network",
    summary: "POST /session/refresh",
    body: {
      method: "POST",
      path: "/session/refresh",
      response_body: JSON.stringify({ data: { accessToken: "abc12345", refreshToken: "ref12345" } }),
    },
  });
  const headersOnly = await verifySkill(dir, shape.id, shape);
  assert.equal(headersOnly.errors.some((item) => item.code === "credential_not_used"), false);
  assert.ok(headersOnly.requests.some((item) => item.issues_credential === true));
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{},\"credential\":{\"url\":\"/session/refresh\",\"method\":\"POST\"}}\n");
  const missing = await verifySkill(dir, shape.id, shape);
  assert.ok(missing.errors.some((item) => item.code === "credential_not_used"));
  await writeFile(path.join(dir, "scripts/client.py"), "import json\ncredential = auth['credential']\n");
  const used = await verifySkill(dir, shape.id, shape);
  assert.equal(used.errors.some((item) => item.code === "credential_not_used"), false);
});

test("import probe falls back when a python binary name is missing", async () => {
  assert.deepEqual(jsNonStdlibImports("import json\nimport urllib.request\nfrom pathlib import Path\nimport requests\n"), ["requests"]);
  const probed = await nonStdlibImports("import json\nimport urllib.request\n");
  assert.equal((probed.imports || []).includes("json"), false);
  assert.equal((probed.imports || []).includes("urllib"), false);
});

test("a script that imports outside the standard library cannot be called", async () => {
  await useData();
  const shape = { id: "rec_verify_stdlib", tenant: "", subsystem: "app", skillId: "app.rec_verify_stdlib", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\n");
  await writeFile(path.join(dir, "scripts/client.py"), "import json\nimport urllib.request\ntry:\n    import requests\nexcept ImportError:\n    requests = None\n");
  await writeFile(path.join(dir, "references/api.md"), "");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
  const blocked = await verifySkill(dir, shape.id, shape);
  assert.ok(blocked.errors.some((item) => item.code === "import_not_available" && item.name === "requests"));
  await writeFile(path.join(dir, "scripts/client.py"), "import json\nimport urllib.request\n");
  const allowed = await verifySkill(dir, shape.id, shape);
  assert.equal(allowed.errors.some((item) => item.code === "import_not_available"), false);
});

test("a GET command listed in the module docstring is still a read that must be run", async () => {
  await useData();
  const shape = { id: "rec_verify_docstring_read", tenant: "", subsystem: "app", skillId: "app.rec_verify_docstring_read", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\npython scripts/client.py departments\npython scripts/client.py create --confirm\n");
  await writeFile(path.join(dir, "scripts/client.py"), [
    '"""python scripts/client.py departments',
    "python scripts/client.py create",
    '"""',
    'PATH_LIST = "/api/list"',
    'PATH_SAVE = "/api/save"',
    "def cmd_departments():",
    '    call("GET", PATH_LIST)',
    "def cmd_create():",
    '    call("POST", PATH_SAVE)',
    '    body["title"] = args.title',
  ].join("\n"));
  await writeFile(path.join(dir, "references/api.md"), "");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{\"Authorization\":\"Bearer tok\"}}\n");
  await seedGuides(shape.id);
  await appendEvidence(shape.id, {
    kind: "network",
    summary: "GET /api/list",
    body: { method: "GET", path: "/api/list", query: "t=1" },
  });
  await appendEvidence(shape.id, {
    kind: "network",
    summary: "POST /api/save",
    body: { method: "POST", path: "/api/save", post_data: JSON.stringify({ title: "a" }) },
  });
  const result = await verifySkill(dir, shape.id, shape);
  assert.ok(result.errors.some((item) => item.code === "command_not_run" && String(item.command || "").includes("departments")));
});

test("a handbook sentence is not a command to run", () => {
  const md = [
    "---",
    "name: sample",
    "---",
    "所有命令通过 `python scripts/client.py <子命令> [参数]` 调用。",
    "python scripts/client.py depts",
  ].join("\n");
  const lines = skillCommandLines(md);
  assert.deepEqual(lines, ["python scripts/client.py depts"]);
  assert.equal(lines.some((line) => line.includes("子命令")), false);
});

test("a read command in a handbook table still must be run", async () => {
  await useData();
  const shape = { id: "rec_verify_table_read", tenant: "", subsystem: "app", skillId: "app.rec_verify_table_read", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  const handbook = [
    "---",
    "name: sample",
    "description: sample",
    "---",
    "python scripts/client.py bm-list",
    "| 列表 | python scripts/client.py list --id <部门id> |",
  ].join("\n");
  assert.ok(skillCommandLines(handbook).some((line) => line.includes("list --id")));
  await writeFile(path.join(dir, "SKILL.md"), `${handbook}\n`);
  await writeFile(path.join(dir, "scripts/client.py"), [
    "def cmd_bm_list():",
    '    call("GET", "/api/bm")',
    "def cmd_list():",
    '    call("GET", "/api/list")',
  ].join("\n"));
  await writeFile(path.join(dir, "references/api.md"), "");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
  await seedGuides(shape.id);
  await appendEvidence(shape.id, {
    kind: "network",
    summary: "GET /api/bm",
    body: { method: "GET", path: "/api/bm" },
  });
  await appendEvidence(shape.id, {
    kind: "network",
    summary: "GET /api/list",
    body: { method: "GET", path: "/api/list" },
  });
  await appendEvidence(shape.id, {
    kind: "verify",
    ok: true,
    argv: ["python", "scripts/client.py", "bm-list"],
  });
  const result = await verifySkill(dir, shape.id, shape);
  assert.ok(result.errors.some((item) => item.code === "command_not_run" && String(item.command || "").includes("list --id")));
});

test("a write-only skill is not forced to invent a GET", async () => {
  await useData();
  const shape = { id: "rec_verify_writeonly", tenant: "", subsystem: "app", skillId: "app.rec_verify_writeonly", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: save-item\ndescription: 调用方保存一条记录\n---\n{\"title\":\"保存\",\"questions\":[{\"id\":\"title\",\"question\":\"标题\",\"inputType\":\"text\",\"required\":true,\"default\":\" \"}]}\npython scripts/client.py save --confirm\n");
  await writeFile(path.join(dir, "scripts/client.py"), "def save():\n    method = \"POST\"\n    path = \"/api/save\"\n    body[\"title\"] = args.title\n");
  await writeFile(path.join(dir, "references/api.md"), "## 字段\n- page_name: 标题\n- source: caller\n- evidence_ids: req_save\n\n## 绑定\n\n## 未解决\n\n## 已验证读命令\n");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
  await appendEvidence(shape.id, {
    id: "req_save",
    kind: "network",
    summary: "POST /api/save",
    body: {
      method: "POST",
      path: "/api/save",
      post_data: JSON.stringify({ title: "hello" }),
      response_body: JSON.stringify({ data: 1 }),
    },
  });
  const result = await verifySkill(dir, shape.id, shape);
  assert.equal(result.errors.some((item) => item.code === "command_not_run"), false);
  assert.equal(result.errors.some((item) => item.code === "follow_get_missing"), false);
});

async function seedGuides(recordingId) {
  for (const name of GUIDE_NAMES) {
    await appendEvidence(recordingId, { kind: "guide", summary: name, body: name });
  }
}

test("authStoppedOutput matches auth_expired 401 403 and 400 with token", () => {
  assert.equal(authStoppedOutput("auth_expired"), true);
  assert.equal(authStoppedOutput("AuthExpired"), true);
  assert.equal(authStoppedOutput("status 401"), true);
  assert.equal(authStoppedOutput("status 403"), true);
  assert.equal(authStoppedOutput("400 refresh"), true);
  assert.equal(authStoppedOutput("400 credential"), true);
  assert.equal(authStoppedOutput("账号未登录"), false);
  assert.equal(authStoppedOutput("凭证缺失：请提供 token"), false);
});

test("a later successful run is not auth_expired, and a GET subcommand is not a write because its name sits near a POST path", async () => {
  await useData();
  const shape = { id: "rec_verify_auth_latest", tenant: "", subsystem: "app", skillId: "app.rec_verify_auth_latest", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\npython scripts/client.py whoami\npython scripts/client.py statistics\n");
  await writeFile(path.join(dir, "scripts/client.py"), [
    "STATS = \"/api/stats\"",
    "SAVE = \"/api/save\"",
    "def cmd_whoami():",
    "    return True",
    "def cmd_statistics():",
    "    request(STATS)",
    "    params[\"deptId\"] = args.dept_id",
    "def cmd_submit():",
    "    request(SAVE)",
    "    body[\"title\"] = args.title",
    "credential = auth['credential']",
  ].join("\n"));
  await writeFile(path.join(dir, "references/api.md"), API_HEADINGS);
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{\"Authorization\":\"Bearer tok\"}}\n");
  await seedGuides(shape.id);
  await appendEvidence(shape.id, {
    kind: "network",
    summary: "POST /api/save",
    body: { method: "POST", path: "/api/save", post_data: JSON.stringify({ title: "a" }), response_body: JSON.stringify({ data: { accessToken: "abc12345", refreshToken: "ref12345" } }) },
  });
  await appendEvidence(shape.id, {
    kind: "network",
    summary: "GET /api/stats",
    body: { method: "GET", path: "/api/stats", query: "deptId=1" },
  });
  await appendEvidence(shape.id, {
    kind: "verify",
    ok: false,
    argv: ["python", "scripts/client.py", "whoami"],
    stdout: "401 账号未登录",
  });
  await appendEvidence(shape.id, {
    kind: "verify",
    ok: true,
    argv: ["python", "scripts/client.py", "whoami"],
  });
  await appendEvidence(shape.id, {
    kind: "verify",
    ok: true,
    argv: ["python", "scripts/client.py", "statistics"],
  });
  const result = await verifySkill(dir, shape.id, shape);
  assert.equal(result.errors.some((item) => item.code === "auth_expired"), false);
  assert.equal(result.errors.some((item) => item.code === "command_not_run"), false);
  assert.equal(result.status, "skill_ready");
});

test("only the latest auth stop is auth_expired and does not fail the structure", async () => {
  await useData();
  const shape = { id: "rec_verify_auth_stop", tenant: "", subsystem: "app", skillId: "app.rec_verify_auth_stop", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\npython scripts/client.py whoami\n");
  await writeFile(path.join(dir, "scripts/client.py"), "credential = auth['credential']\n");
  await writeFile(path.join(dir, "references/api.md"), API_HEADINGS);
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{\"Authorization\":\"Bearer tok\"}}\n");
  await seedGuides(shape.id);
  await appendEvidence(shape.id, {
    kind: "network",
    summary: "POST /session/refresh",
    body: { method: "POST", path: "/session/refresh", response_body: JSON.stringify({ data: { accessToken: "abc12345", refreshToken: "ref12345" } }) },
  });
  await appendEvidence(shape.id, {
    kind: "verify",
    ok: true,
    argv: ["python", "scripts/client.py", "whoami"],
  });
  await appendEvidence(shape.id, {
    kind: "verify",
    ok: false,
    argv: ["python", "scripts/client.py", "whoami"],
    stdout: "{\"ok\": false, \"error\": \"refresh-token failed: 400 无效的刷新令牌\"}",
  });
  const result = await verifySkill(dir, shape.id, shape);
  assert.equal(result.errors.some((item) => item.code === "command_not_run"), false);
  assert.ok(result.errors.every((item) => item.code === "auth_expired"));
  assert.equal(result.status, "skill_written_needs_auth");
  const rec = { status: "recording", finished: false, verify: null, skillDir: "" };
  applyVerifyToRecording(rec, result, dir);
  assert.equal(rec.status, "skill_ready");
  assert.equal(rec.skillReady, true);
  assert.equal(rec.finished, false);
  assert.equal(publicSessionStatus(rec), "skill_ready");
  const failed = { status: "recording", finished: false };
  applyVerifyToRecording(failed, { status: "verify_failed", errors: [{ code: "command_not_run" }] });
  assert.equal(failed.status, "recording");
  assert.equal(failed.finished, false);
  assert.equal(publicSessionStatus(failed), "recording");
  assert.equal(publicSessionStatus({ status: "verify_failed" }), "failed");
  assert.equal(publicSessionStatus({ status: "waiting_operator" }), "recording");
});

test("a later script crash is not_invocable even if an earlier run stopped on 401", async () => {
  await useData();
  const shape = { id: "rec_verify_crash_latest", tenant: "", subsystem: "app", skillId: "app.rec_verify_crash_latest", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\npython scripts/client.py departments\n");
  await writeFile(path.join(dir, "scripts/client.py"), "credential = auth['credential']\n");
  await writeFile(path.join(dir, "references/api.md"), "");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{\"Authorization\":\"Bearer tok\"}}\n");
  await seedGuides(shape.id);
  await appendEvidence(shape.id, {
    kind: "verify",
    ok: false,
    argv: ["python", "scripts/client.py", "departments"],
    stdout: "{\"msg\": \"您的账号已经超时，请重新登录\", \"code\": 401}",
  });
  await appendEvidence(shape.id, {
    kind: "verify",
    ok: false,
    argv: ["python", "scripts/client.py", "departments"],
    stderr: "Traceback (most recent call last):\n  File \"scripts/client.py\", line 1, in <module>\nAttributeError: 'Namespace' object has no attribute 'func'",
  });
  const result = await verifySkill(dir, shape.id, shape);
  assert.ok(result.errors.some((item) => item.code === "not_invocable"));
  assert.equal(result.errors.some((item) => item.code === "auth_expired"), false);
  assert.equal(result.status, "verify_failed");
});

test("ATX headings, table source, argparse dispatch, and matching subcommands are structural", async () => {
  await useData();
  const shape = { id: "rec_verify_atx", tenant: "", subsystem: "app", skillId: "app.rec_verify_atx", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: list-items\ndescription: 调用方列出记录\n---\npython3 scripts/client.py list --id <id>\n{\"title\":\"列出\",\"questions\":[{\"id\":\"id\",\"question\":\"记录\",\"inputType\":\"text\",\"required\":true,\"default\":\" \"},{\"id\":\"title\",\"question\":\"标题\",\"inputType\":\"text\",\"required\":true,\"default\":\" \"}]}\n");
  await writeFile(path.join(dir, "scripts/client.py"), [
    "import argparse",
    "def cmd_list(args):",
    "    return True",
    "p = argparse.ArgumentParser()",
    "sub = p.add_subparsers()",
    "q = sub.add_parser(\"list\")",
  ].join("\n"));
  await writeFile(path.join(dir, "references/api.md"), "# 字段\n| 标题 | caller |\n\n# 绑定\n\n# 未解决\n\n# 已验证读命令\npython3 scripts/client.py list --id 1\n");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{\"Authorization\":\"Bearer tok\"}}\n");
  await seedGuides(shape.id);
  await appendEvidence(shape.id, {
    kind: "verify",
    ok: true,
    argv: ["python", "scripts/client.py", "list", "--id", "1"],
  });
  let result = await verifySkill(dir, shape.id, shape);
  assert.ok(result.errors.some((item) => item.code === "not_invocable" && String(item.hint || "").includes("set_defaults")));
  assert.equal(result.errors.some((item) => item.code === "command_not_run"), false);
  assert.equal(result.errors.some((item) => item.code === "field_source_invalid"), false);

  await writeFile(path.join(dir, "scripts/client.py"), [
    "import argparse",
    "def cmd_list(args):",
    "    return True",
    "p = argparse.ArgumentParser()",
    "sub = p.add_subparsers()",
    "q = sub.add_parser(\"list\")",
    "q.set_defaults(func=cmd_list)",
  ].join("\n"));
  await writeFile(path.join(dir, "references/api.md"), "# 字段\n| 页面字段 | 来源 |\n| 标题 | invented |\n\n# 绑定\n\n# 未解决\n\n# 已验证读命令\npython3 scripts/client.py list --id 1\n");
  result = await verifySkill(dir, shape.id, shape);
  assert.ok(result.errors.some((item) => item.code === "field_source_invalid"));

  await writeFile(path.join(dir, "references/api.md"), "# 字段\n- page_name: 标题\n- source: caller\n\n# 绑定\n\n# 未解决\n\n# 已验证读命令\npython3 scripts/client.py list --id 1\n");
  result = await verifySkill(dir, shape.id, shape);
  assert.equal(result.errors.some((item) => item.code === "field_source_invalid"), false);
  assert.equal(result.errors.some((item) => item.code === "command_not_run"), false);
  assert.equal(result.errors.some((item) => item.code === "not_invocable"), false);
});

test("caller fields without questions[] or choice options are not invocable", async () => {
  await useData();
  const shape = { id: "rec_verify_invoke", tenant: "", subsystem: "app", skillId: "app.rec_verify_invoke", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\npython scripts/client.py save --title x --confirm\n");
  await writeFile(path.join(dir, "scripts/client.py"), [
    "import argparse",
    "def cmd_save(args):",
    "    method = \"POST\"",
    "p = argparse.ArgumentParser()",
    "sub = p.add_subparsers()",
    "q = sub.add_parser(\"save\")",
    "q.add_argument(\"--title\")",
    "q.set_defaults(func=cmd_save)",
  ].join("\n"));
  await writeFile(path.join(dir, "references/api.md"), [
    "## 字段",
    "- page_name: 标题",
    "- source: caller",
    "- control: textbox",
    "",
    "- page_name: 部门",
    "- source: caller",
    "- control: combobox",
    "",
    "## 绑定",
    "",
    "## 未解决",
    "",
    "## 已验证读命令",
  ].join("\n"));
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{\"Authorization\":\"Bearer tok\"}}\n");
  await seedGuides(shape.id);
  let result = await verifySkill(dir, shape.id, shape);
  assert.ok(result.errors.some((item) => item.code === "not_invocable" && item.file === "SKILL.md" && String(item.hint || "").includes("questions[]")));
  assert.ok(result.errors.some((item) => item.code === "not_invocable" && item.field === "部门"));
  assert.ok(result.errors.some((item) => item.code === "not_invocable" && item.command === "save" && String(item.hint || "").includes("--confirm")));

  const questionsOnly = "---\nname: sample\ndescription: sample\n---\n{\"title\":\"办理\",\"questions\":[{\"id\":\"title\",\"question\":\"标题\",\"inputType\":\"text\",\"required\":true,\"default\":\" \"},{\"id\":\"dept\",\"question\":\"部门\",\"inputType\":\"select\",\"required\":true,\"options\":[],\"default\":\" \"}]}\npython scripts/client.py save --title x --confirm\n";
  await writeFile(path.join(dir, "SKILL.md"), questionsOnly);
  await writeFile(path.join(dir, "scripts/client.py"), [
    "import argparse",
    "def cmd_save(args):",
    "    method = \"POST\"",
    "p = argparse.ArgumentParser()",
    "sub = p.add_subparsers()",
    "q = sub.add_parser(\"save\")",
    "q.add_argument(\"--title\")",
    "q.add_argument(\"--confirm\", action=\"store_true\")",
    "q.set_defaults(func=cmd_save)",
  ].join("\n"));
  result = await verifySkill(dir, shape.id, shape);
  // 写能力的 SKILL.md 没写调用方怎么问（confirm + formIds）时不可调用
  assert.ok(result.errors.some((item) => item.file === "SKILL.md" && String(item.hint || "").includes("调用方怎么问")));
  await writeFile(path.join(dir, "SKILL.md"), `${questionsOnly}\n${ASK_PROTOCOL}\n`);
  await writeFile(path.join(dir, "references/api.md"), [
    "## 字段",
    "- page_name: 标题",
    "- source: caller",
    "- control: textbox",
    "",
    "- page_name: 部门",
    "- source: other_api",
    "- control: combobox",
    "",
    "## 绑定",
    "",
    "## 未解决",
    "",
    "## 已验证读命令",
  ].join("\n"));
  result = await verifySkill(dir, shape.id, shape);
  assert.equal(result.errors.some((item) => item.file === "SKILL.md" && String(item.hint || "").includes("questions[]")), false);
  assert.equal(result.errors.some((item) => item.field === "部门"), false);
  assert.equal(result.errors.some((item) => item.command === "save"), false);
});

test("api.md without the four structural headings is not invocable", async () => {
  await useData();
  const shape = { id: "rec_verify_headings", tenant: "", subsystem: "app", skillId: "app.rec_verify_headings", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\n");
  await writeFile(path.join(dir, "scripts/client.py"), "import json\n");
  await writeFile(path.join(dir, "references/api.md"), "## GET /api/list\n无参数。\n");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
  await seedGuides(shape.id);
  const result = await verifySkill(dir, shape.id, shape);
  const headings = result.errors.filter((item) => item.code === "not_invocable" && item.file === "references/api.md" && item.heading).map((item) => item.heading);
  assert.ok(headings.includes("字段"));
  assert.ok(headings.includes("绑定"));
  assert.ok(headings.includes("未解决"));
  assert.ok(headings.includes("已验证读命令"));
});

test("a questions[] skill with only path dumps in the field section is not invocable", async () => {
  await useData();
  const shape = { id: "rec_verify_field_rows", tenant: "", subsystem: "app", skillId: "app.rec_verify_field_rows", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\n{\"title\":\"办理\",\"questions\":[{\"id\":\"title\",\"question\":\"标题\",\"inputType\":\"text\",\"required\":true,\"default\":\" \"}]}\npython scripts/client.py save --confirm\n");
  await writeFile(path.join(dir, "scripts/client.py"), [
    "import argparse",
    "def cmd_save(args):",
    "    method = \"POST\"",
    "p = argparse.ArgumentParser()",
    "sub = p.add_subparsers()",
    "q = sub.add_parser(\"save\")",
    "q.add_argument(\"--confirm\", action=\"store_true\")",
    "q.set_defaults(func=cmd_save)",
  ].join("\n"));
  await writeFile(path.join(dir, "references/api.md"), "## 字段\nGET /api/save\n\n## 绑定\n\n## 未解决\n\n## 已验证读命令\n");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{\"Authorization\":\"Bearer tok\"}}\n");
  await seedGuides(shape.id);
  const result = await verifySkill(dir, shape.id, shape);
  assert.ok(result.errors.some((item) => item.code === "not_invocable" && item.file === "references/api.md" && String(item.hint || "").includes("page_name")));
});

test("a write helper reached from a subcommand still needs --confirm", async () => {
  await useData();
  const shape = { id: "rec_verify_write_helper", tenant: "", subsystem: "app", skillId: "app.rec_verify_write_helper", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\npython scripts/client.py upload-file --file-path x\n");
  await writeFile(path.join(dir, "scripts/client.py"), [
    "import argparse",
    "def upload_one(path):",
    "    request(\"POST\", \"/api/file\")",
    "def cmd_upload_file(args):",
    "    upload_one(args.file_path)",
    "p = argparse.ArgumentParser()",
    "sub = p.add_subparsers()",
    "q = sub.add_parser(\"upload-file\")",
    "q.add_argument(\"--file-path\")",
    "q.set_defaults(func=cmd_upload_file)",
  ].join("\n"));
  await writeFile(path.join(dir, "references/api.md"), API_HEADINGS);
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{\"Authorization\":\"Bearer tok\"}}\n");
  await seedGuides(shape.id);
  const result = await verifySkill(dir, shape.id, shape);
  assert.ok(result.errors.some((item) => item.code === "not_invocable" && item.command === "upload-file" && String(item.hint || "").includes("--confirm")));
});

test("choice and date page controls cannot be asked as free text, and option ids must be in evidence", async () => {
  await useData();
  const shape = { id: "rec_verify_question_shape", tenant: "", subsystem: "app", skillId: "app.rec_verify_question_shape", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\n{\"title\":\"办理\",\"questions\":[{\"id\":\"dept\",\"question\":\"部门\",\"inputType\":\"text\",\"required\":true,\"default\":\" \"},{\"id\":\"start\",\"question\":\"开始日期\",\"inputType\":\"text\",\"required\":true,\"default\":\" \"}]}\npython scripts/client.py save --confirm\n");
  await writeFile(path.join(dir, "scripts/client.py"), [
    "import argparse",
    "def cmd_save(args):",
    "    method = \"POST\"",
    "p = argparse.ArgumentParser()",
    "sub = p.add_subparsers()",
    "q = sub.add_parser(\"save\")",
    "q.add_argument(\"--confirm\", action=\"store_true\")",
    "q.set_defaults(func=cmd_save)",
  ].join("\n"));
  await writeFile(path.join(dir, "references/api.md"), [
    "## 字段",
    "- page_name: 部门",
    "- source: caller",
    "- control: combobox",
    "- options: 甲,乙",
    "",
    "- page_name: 开始日期",
    "- source: caller",
    "- control: date",
    "",
    "## 绑定",
    "",
    "## 未解决",
    "",
    "## 已验证读命令",
  ].join("\n"));
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{\"Authorization\":\"Bearer tok\"}}\n");
  await seedGuides(shape.id);
  let result = await verifySkill(dir, shape.id, shape);
  assert.ok(result.observations.some((item) => item.code === "control_type_mismatch" && String(item.hint || "").includes("options")));
  assert.ok(result.observations.some((item) => item.code === "control_type_mismatch" && String(item.hint || "").includes("dateFormat")));
  assert.equal(result.errors.some((item) => /页面 combobox|页面日期控件|封闭枚举在 questions/.test(String(item.hint || ""))), false);

  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\n{\"title\":\"办理\",\"questions\":[{\"id\":\"dept\",\"question\":\"部门\",\"inputType\":\"select\",\"required\":true,\"options\":[{\"id\":\"from-list-command\",\"label\":\"运行 list\"}],\"default\":\"from-list-command\"},{\"id\":\"start\",\"question\":\"开始日期\",\"inputType\":\"date\",\"dateFormat\":\"yyyy-MM-dd\",\"required\":true,\"default\":\" \"}]}\npython scripts/client.py save --confirm\n");
  await writeFile(path.join(dir, "references/api.md"), [
    "## 字段",
    "- page_name: 部门",
    "- source: other_api",
    "- control: combobox",
    "",
    "- page_name: 开始日期",
    "- source: caller",
    "- control: date",
    "",
    "## 绑定",
    "",
    "## 未解决",
    "",
    "## 已验证读命令",
  ].join("\n"));
  await appendEvidence(shape.id, {
    kind: "network",
    summary: "GET /api/depts",
    body: {
      method: "GET",
      path: "/api/depts",
      response_body: JSON.stringify({ data: [{ id: 103, name: "甲部门名称足够长" }, { id: 104, name: "乙部门名称足够长" }] }),
    },
  });
  result = await verifySkill(dir, shape.id, shape);
  assert.ok(result.observations.some((item) => item.code === "live_options_listed" && item.field === "dept"));
  assert.equal(result.errors.some((item) => item.field === "dept" && /留空|占位/.test(String(item.hint || ""))), false);

  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\n{\"title\":\"办理\",\"questions\":[{\"id\":\"dept\",\"question\":\"部门\",\"inputType\":\"select\",\"required\":true,\"options\":[{\"id\":\"103\",\"label\":\"甲部门名称足够长\"}],\"default\":\"103\"},{\"id\":\"start\",\"question\":\"开始日期\",\"inputType\":\"date\",\"dateFormat\":\"yyyy-MM-dd\",\"required\":true,\"default\":\" \"}]}\npython scripts/client.py save --confirm\n");
  result = await verifySkill(dir, shape.id, shape);
  assert.ok(result.observations.some((item) => item.code === "sample_value" && item.field === "dept"));
  assert.equal(result.errors.some((item) => item.field === "dept" && /留空|业务 id/.test(String(item.hint || ""))), false);

  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\n{\"title\":\"办理\",\"questions\":[{\"id\":\"dept\",\"question\":\"部门\",\"inputType\":\"select\",\"required\":true,\"options\":[],\"default\":\" \"},{\"id\":\"start\",\"question\":\"开始日期\",\"inputType\":\"date\",\"dateFormat\":\"yyyy-MM-dd\",\"required\":true,\"default\":\" \"}]}\npython scripts/client.py save --confirm\n");
  result = await verifySkill(dir, shape.id, shape);
  assert.equal(result.errors.some((item) => item.file === "SKILL.md" && /留空|占位|业务 id|dateFormat/.test(String(item.hint || ""))), false);

  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\n{\"title\":\"办理\",\"questions\":[{\"id\":\"dept\",\"question\":\"部门\",\"inputType\":\"select\",\"required\":true,\"options\":[],\"default\":\"103\"},{\"id\":\"start\",\"question\":\"开始日期\",\"inputType\":\"date\",\"dateFormat\":\"yyyy-MM-dd\",\"required\":true,\"default\":\" \"}]}\npython scripts/client.py save --confirm\n");
  result = await verifySkill(dir, shape.id, shape);
  assert.ok(result.observations.some((item) => item.code === "sample_value" && item.field === "dept"));
  assert.equal(result.errors.some((item) => item.field === "dept" && String(item.hint || "").includes("业务 id")), false);

  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\n{\"title\":\"办理\",\"questions\":[{\"id\":\"items\",\"question\":\"明细\",\"inputType\":\"rows\",\"required\":true,\"default\":\" \"}]}\npython scripts/client.py save --confirm\n");
  await writeFile(path.join(dir, "references/api.md"), [
    "## 字段",
    "- page_name: 明细",
    "- source: caller",
    "- control: textbox",
    "",
    "## 绑定",
    "",
    "## 未解决",
    "",
    "## 已验证读命令",
  ].join("\n"));
  result = await verifySkill(dir, shape.id, shape);
  assert.ok(result.errors.some((item) => item.code === "not_invocable" && item.field === "items" && String(item.hint || "").includes("inputType")));
});

test("field tables with aliases and page_name= bullets are parsed, constant needs no reason gate", async () => {
  await useData();
  const shape = { id: "rec_verify_field_parse", tenant: "", subsystem: "app", skillId: "app.rec_verify_field_parse", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  const skillMd = [
    "---",
    "name: sample",
    "description: sample",
    "---",
    "{\"title\":\"办理\",\"questions\":[{\"id\":\"title\",\"question\":\"标题\",\"inputType\":\"text\",\"required\":true,\"default\":\" \"},{\"id\":\"remark\",\"question\":\"备注\",\"inputType\":\"text\",\"required\":false,\"default\":\" \"}]}",
    "python scripts/client.py save --title x --confirm",
  ].join("\n");
  const client = [
    "import argparse",
    "def cmd_save(args):",
    "    method = \"POST\"",
    "p = argparse.ArgumentParser()",
    "sub = p.add_subparsers()",
    "q = sub.add_parser(\"save\")",
    "q.add_argument(\"--title\")",
    "q.add_argument(\"--confirm\", action=\"store_true\")",
    "q.set_defaults(func=cmd_save)",
  ].join("\n");
  await writeFile(path.join(dir, "SKILL.md"), skillMd);
  await writeFile(path.join(dir, "scripts/client.py"), client);
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{\"Authorization\":\"Bearer tok\"}}\n");
  await seedGuides(shape.id);

  await writeFile(path.join(dir, "references/api.md"), [
    "## 字段",
    "| 页面字段 | 请求键 | 来源 | 原因 |",
    "| --- | --- | --- | --- |",
    "| 标题 | title | caller | 页面文本 |",
    "| 编号 | billCode | constant |",
    "",
    "## 绑定",
    "",
    "## 未解决",
    "",
    "## 已验证读命令",
  ].join("\n"));
  let result = await verifySkill(dir, shape.id, shape);
  assert.equal(result.errors.some((item) => item.code === "constant_reason_missing"), false);
  assert.equal(result.errors.some((item) => item.code === "not_invocable" && item.file === "references/api.md" && String(item.hint || "").includes("page_name")), false);

  await writeFile(path.join(dir, "references/api.md"), [
    "## 字段",
    "- page_name=标题, key=title, source=caller, reason=页面文本",
    "- page_name=编号, key=billCode, source=constant",
    "",
    "## 绑定",
    "",
    "## 未解决",
    "",
    "## 已验证读命令",
  ].join("\n"));
  result = await verifySkill(dir, shape.id, shape);
  assert.equal(result.errors.some((item) => item.code === "constant_reason_missing"), false);
  assert.equal(result.errors.some((item) => item.code === "not_invocable" && item.file === "references/api.md" && String(item.hint || "").includes("page_name")), false);
  assert.equal(result.errors.some((item) => item.code === "field_source_invalid"), false);

  await writeFile(path.join(dir, "references/api.md"), [
    "## 字段",
    "| page_name | source | 说明 |",
    "| --- | --- | --- |",
    "| 附件 | other_api（upload 返回） | 文件 URL |",
    "| 状态 | constant 未提交 | 服务端状态机 |",
    "",
    "## 绑定",
    "",
    "## 未解决",
    "",
    "## 已验证读命令",
  ].join("\n"));
  result = await verifySkill(dir, shape.id, shape);
  assert.equal(result.errors.some((item) => item.code === "field_source_invalid"), false);

  await writeFile(path.join(dir, "references/api.md"), [
    "## 字段",
    "按命令分组。",
    "",
    "### save（POST /save）",
    "| page_name | source | 说明 |",
    "| --- | --- | --- |",
    "| 标题 | caller | 文本 |",
    "",
    "### 备注",
    "  - page_name=备注, source=caller",
    "",
    "## 绑定",
    "",
    "## 未解决",
    "",
    "## 已验证读命令",
  ].join("\n"));
  result = await verifySkill(dir, shape.id, shape);
  assert.equal(result.errors.some((item) => item.code === "not_invocable" && item.file === "references/api.md" && String(item.hint || "").includes("page_name")), false);
  assert.equal(result.errors.some((item) => item.code === "field_source_invalid"), false);

  await writeFile(path.join(dir, "references/api.md"), [
    "## 字段",
    "| 键 | 类型 | 说明 |",
    "| --- | --- | --- |",
    "| id | int | 部门 |",
    "| name | string | 名称 |",
    "",
    "## 绑定",
    "",
    "## 未解决",
    "",
    "## 已验证读命令",
  ].join("\n"));
  result = await verifySkill(dir, shape.id, shape);
  assert.equal(result.errors.some((item) => item.code === "field_source_invalid" && item.field === "id"), false);
  assert.equal(result.errors.some((item) => item.code === "field_source_invalid" && item.field === "name"), false);
});

test("questions never show JSON to the caller, and a write skill must say how the caller asks", () => {
  assert.equal(jsonWording({ question: "已完成工作（JSON 数组，每项）", default: " " }), true);
  assert.equal(jsonDefault({ question: "附件", default: "[]" }), true);
  assert.equal(jsonWording({ question: "已完成工作（每行一项：工作内容 | 完成进度）", default: " " }), false);
  assert.equal(jsonDefault({ question: "标题", default: "周报" }), false);
  const read = "---\nname: a\ndescription: b\n---\npython3 scripts/client.py list\n";
  assert.equal(askProtocolMissing(read), false);
  const writeNoProtocol = "---\nname: a\ndescription: b\n---\n{\"title\":\"办理\",\"questions\":[]}\npython3 scripts/client.py save --confirm\n";
  assert.equal(askProtocolMissing(writeNoProtocol), true);
  const writeSpaced = `${writeNoProtocol}${ASK_PROTOCOL.replace(/。/g, "。\n  ")}\n`;
  assert.equal(askProtocolMissing(writeSpaced), false);
  const writeMissingSentence = `${writeNoProtocol}${ASK_PROTOCOL.replace("不要另造查询确认卡。", "")}\n`;
  assert.equal(askProtocolMissing(writeMissingSentence), true);
});

test("ask-user-question guide keeps the ask protocol verbatim", async () => {
  const guide = await readFile(path.join(docDir(), "skill-generator-ask-user-question-guide.md"), "utf8");
  assert.ok(squashProtocol(guide).includes(squashProtocol(ASK_PROTOCOL)));
});

test("a written file gets back only its own structural errors, never the run-time ones", () => {
  const errors = [
    { code: "not_invocable", file: "references/api.md", hint: "字段" },
    { code: "field_source_invalid", field: "部门", source: "GET /x" },
    { code: "evidence_missing", id: "req_9" },
    { code: "credential_not_used", file: "scripts/client.py" },
    { code: "import_not_available", name: "requests" },
    { code: "command_not_run", file: "SKILL.md", command: "python3 scripts/client.py list" },
    { code: "not_invocable", file: "SKILL.md", hint: "questions[]" },
    { code: "guide_not_read", name: "x" },
    { code: "auth_expired" },
  ];
  assert.deepEqual(structureErrorsForFile(errors, "references/api.md").map((item) => item.code), ["not_invocable", "field_source_invalid", "evidence_missing"]);
  assert.deepEqual(structureErrorsForFile(errors, "scripts/client.py").map((item) => item.code), ["credential_not_used", "import_not_available"]);
  assert.deepEqual(structureErrorsForFile(errors, "SKILL.md").map((item) => item.code), ["not_invocable"]);
});

test("DANO_AUTH_HEADERS overlay must be json.loads, not comma pairs", () => {
  assert.equal(authOverlayParseError("print(1)"), "");
  assert.equal(authOverlayParseError("raw = os.environ.get(\"DANO_AUTH_HEADERS\")\nheaders.update(json.loads(raw))"), "");
  assert.match(authOverlayParseError("env = os.environ.get(\"DANO_AUTH_HEADERS\",\"\")\nfor chunk in env.split(\",\"):\n    k,v = chunk.split(\"=\")"), /JSON 对象/);
  assert.match(authOverlayParseError("raw = os.environ.get(\"DANO_AUTH_HEADERS\")\nif raw:\n    return json.loads(raw)"), /覆盖同名|整袋/);
  assert.equal(hardcodedOriginError("base_url = os.environ.get(\"DANO_BASE_URL\") or runtime.get(\"base_url\") or \"\""), "");
  assert.match(hardcodedOriginError("DEFAULT_BASE_URL = \"https://example.test\""), /源站/);
  assert.match(hardcodedOriginError("    return \"https://example.test\""), /源站/);
  assert.equal(hardcodedOriginError("return os.environ.get(\"DANO_BASE_URL\") or runtime.get(\"base_url\") or \"\""), "");
});

test("questions[] must cover caller fields and the densest overlay layer", async () => {
  const fields = [
    { page_name: "标题", source: "caller", control: "textbox" },
    { page_name: "工作总结", source: "caller", control: "textbox" },
    { page_name: "作者", source: "current_user", control: "textbox" },
  ];
  const thin = [{ id: "title", question: "标题", inputType: "text" }];
  const layers = overlayLayersFromBodies([
    {
      overlay_editable: [
        { name: "textbox \"标题\"", role: "textbox" },
        { name: "textbox \"工作总结\"", role: "textbox" },
        { name: "textbox \"明日计划\"", role: "textbox" },
      ],
      requests: [{ method: "POST", path: "/api/save" }],
    },
  ]);
  const missing = askCoverageObservations(thin, fields, layers, { hasWriteConfirm: true });
  assert.ok(missing.some((item) => item.code === "ask_coverage" && item.field === "工作总结" && item.file === "SKILL.md"));
  assert.ok(missing.some((item) => item.code === "ask_coverage" && item.field === "明日计划" && item.file === "references/api.md"));
  assert.equal(missing.some((item) => item.field === "作者"), false);
  const full = [
    { id: "title", question: "标题", inputType: "text" },
    { id: "summary", question: "工作总结", inputType: "textarea" },
    { id: "plan", question: "明日计划", inputType: "textarea" },
  ];
  const fieldsFull = [...fields, { page_name: "明日计划", source: "caller", control: "textbox" }];
  assert.equal(askCoverageObservations(full, fieldsFull, layers, { hasWriteConfirm: true }).length, 0);

  await useData();
  const shape = { id: "rec_verify_overlay_cover", tenant: "", subsystem: "app", skillId: "app.rec_verify_overlay_cover", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: save-item\ndescription: 调用方保存\n---\n{\"title\":\"保存\",\"questions\":[{\"id\":\"title\",\"question\":\"标题\",\"inputType\":\"text\",\"required\":true,\"default\":\" \"}]}\npython scripts/client.py save --confirm\n调用方怎么问：读不问不确认。写第一次原样发出 questions[]；提交后只发 {\"confirm\":true,\"formIds\":[\"<formId>\"]}，再加 --confirm。\n");
  await writeFile(path.join(dir, "scripts/client.py"), "def save():\n    method = \"POST\"\n    path = \"/api/save\"\n    body[\"title\"] = args.title\n");
  await writeFile(path.join(dir, "references/api.md"), "## 字段\n- page_name: 标题\n- source: caller\n\n## 绑定\n\n## 未解决\n\n## 已验证读命令\n");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{\"Authorization\":\"Bearer tok\"}}\n");
  await seedGuides(shape.id);
  await appendEvidence(shape.id, {
    kind: "action",
    summary: "click:确定",
    body: {
      overlay_editable: [
        { name: "textbox \"标题\"", role: "textbox" },
        { name: "textbox \"工作总结\"", role: "textbox" },
      ],
      requests: [{ method: "POST", path: "/api/save" }],
    },
  });
  const result = await verifySkill(dir, shape.id, shape);
  assert.ok(result.observations.some((item) => item.code === "ask_coverage" && item.field === "工作总结"));
  assert.equal(result.errors.some((item) => item.field === "工作总结"), false);
});

test("### headings in 字段 are page_name, command headings are not, and required marks are not part of the name", async () => {
  const rows = apiFieldRows([
    "## 字段",
    "### 标题",
    "source: caller",
    "control: textbox",
    "",
    "### 工作总结",
    "- source: caller",
    "",
    "### report-statistics",
    "source: caller",
    "",
    "### save（POST /save）",
    "| page_name | source |",
    "| --- | --- |",
    "| 备注 | caller |",
    "",
    "## 绑定",
    "",
    "## 未解决",
    "",
    "## 已验证读命令",
  ].join("\n"), new Set(["report-statistics"]));
  assert.ok(rows.some((row) => row.page_name === "标题" && row.source === "caller"));
  assert.ok(rows.some((row) => row.page_name === "工作总结" && row.source === "caller"));
  assert.ok(rows.some((row) => row.page_name === "备注" && row.source === "caller"));
  assert.equal(rows.some((row) => row.page_name === "report-statistics"), false);
  assert.equal(rows.some((row) => /save/.test(row.page_name || "")), false);

  const overlay = overlayLayersFromBodies([{
    overlay_editable: [
      { name: "combobox \"* 汇报类型\" in \"单据信息\"", role: "combobox" },
      { name: "textbox \"标题\"", role: "textbox" },
    ],
    requests: [{ method: "POST", path: "/save" }],
  }]);
  const missing = askCoverageObservations(
    [{ id: "title", question: "标题", inputType: "text" }],
    [{ page_name: "标题", source: "caller" }],
    overlay,
    { hasWriteConfirm: true },
  );
  assert.ok(missing.some((item) => item.code === "ask_coverage" && item.field === "汇报类型"));
  assert.equal(missing.some((item) => String(item.field || "").includes("*")), false);
  const covered = askCoverageObservations(
    [
      { id: "title", question: "标题", inputType: "text" },
      { id: "type", question: "汇报类型", inputType: "select" },
    ],
    [
      { page_name: "标题", source: "caller" },
      { page_name: "汇报类型", source: "caller" },
    ],
    overlay,
    { hasWriteConfirm: true },
  );
  assert.equal(covered.filter((item) => item.field === "汇报类型").length, 0);

  const alignment = fieldAlignment(
    [{ page_name: "标题", source: "caller" }, { page_name: "汇报类型", source: "caller" }],
    [{ id: "title" }, { id: "type" }],
    overlay,
  );
  assert.ok(alignment.overlay_editable_names.includes("汇报类型"));
  assert.ok(alignment.overlay_editable_names.includes("标题"));
  assert.deepEqual(alignment.parsed_page_names, ["标题", "汇报类型"]);
  assert.deepEqual(alignment.question_ids, ["title", "type"]);

  await useData();
  const shape = { id: "rec_verify_heading_fields", tenant: "", subsystem: "app", skillId: "app.rec_verify_heading_fields", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: save-item\ndescription: 调用方保存\n---\n{\"title\":\"保存\",\"questions\":[{\"id\":\"title\",\"question\":\"标题\",\"inputType\":\"text\",\"required\":true,\"default\":\" \"}]}\npython scripts/client.py save --confirm\npython scripts/client.py report-statistics\n调用方怎么问：读不问不确认。写第一次原样发出 questions[]；提交后只发 {\"confirm\":true,\"formIds\":[\"<formId>\"]}，再加 --confirm。\n");
  await writeFile(path.join(dir, "scripts/client.py"), "def save():\n    method = \"POST\"\n    path = \"/api/save\"\n    body[\"title\"] = args.title\ndef report_statistics():\n    method = \"GET\"\n    path = \"/api/stats\"\n");
  await writeFile(path.join(dir, "references/api.md"), [
    "## 字段",
    "### 标题",
    "source: caller",
    "",
    "### report-statistics",
    "source: caller",
    "",
    "## 绑定",
    "",
    "## 未解决",
    "",
    "## 已验证读命令",
  ].join("\n"));
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{\"Authorization\":\"Bearer tok\"}}\n");
  await seedGuides(shape.id);
  await appendEvidence(shape.id, {
    kind: "action",
    summary: "click:确定",
    body: {
      overlay_editable: [{ name: "textbox \"标题\"", role: "textbox" }],
      requests: [{ method: "POST", path: "/api/save" }],
    },
  });
  await appendEvidence(shape.id, {
    kind: "verify",
    ok: true,
    argv: ["python", "scripts/client.py", "report-statistics"],
    summary: "python scripts/client.py report-statistics",
  });
  const result = await verifySkill(dir, shape.id, shape);
  assert.equal(result.errors.some((item) => String(item.hint || "").includes("收到下一个")), false);
  assert.equal(result.errors.some((item) => item.field === "标题" && String(item.hint || "").includes("page_name")), false);
  assert.equal(result.errors.some((item) => item.field === "report-statistics"), false);
  assert.ok(result.alignment.parsed_page_names.includes("标题"));
  assert.equal(result.alignment.parsed_page_names.includes("report-statistics"), false);
});

test("name mismatches go to observations and do not fail status", async () => {
  await useData();
  const shape = { id: "rec_verify_obs", tenant: "", subsystem: "app", skillId: "app.rec_verify_obs", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(shape.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  const skill = [
    "---",
    "name: save-item",
    "description: 调用方保存一条记录",
    "---",
    "{\"title\":\"保存\",\"questions\":[{\"id\":\"begin\",\"question\":\"开始日期\",\"inputType\":\"text\",\"required\":true,\"default\":\" \"}]}",
    "python scripts/client.py save --confirm",
    ASK_PROTOCOL,
  ].join("\n");
  await writeFile(path.join(dir, "SKILL.md"), `${skill}\n`);
  await writeFile(path.join(dir, "scripts/client.py"), "def save():\n    method = \"POST\"\n    path = \"/api/items\"\n    body[\"name\"] = args.name\n");
  await writeFile(path.join(dir, "references/api.md"), "## 字段\n- page_name: Start\n- source: caller\n- evidence_ids: req_save\n\n## 绑定\n\n## 未解决\n\n## 已验证读命令\n");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{\"Authorization\":\"Bearer tok\"}}\n");
  await seedGuides(shape.id);
  await appendEvidence(shape.id, {
    id: "req_save",
    kind: "network",
    summary: "POST /api/items",
    body: { method: "POST", path: "/api/items", post_data: JSON.stringify({ name: "a" }), response_body: "{}" },
  });
  await appendEvidence(shape.id, {
    kind: "action",
    summary: "click:save",
    body: {
      overlay_editable: [{ name: "textbox \"Start\"", role: "textbox" }],
      requests: [{ method: "POST", path: "/api/items" }],
    },
  });
  const result = await verifySkill(dir, shape.id, shape);
  assert.equal(result.errors.some((item) => item.code === "ask_coverage"), false);
  assert.ok(result.observations.some((item) => item.code === "ask_coverage"));
  assert.ok(result.status === "skill_ready" || result.status === "skill_written_needs_auth");
  assert.match(resultLine({ content: [{ type: "text", text: JSON.stringify(result) }] }), /待核对/);

  const icon = { id: "rec_verify_icon", tenant: "", subsystem: "app", skillId: "app.rec_verify_icon", startUrl: "http://127.0.0.1/" };
  const iconDir = skillDir(icon.skillId);
  await mkdir(path.join(iconDir, "scripts"), { recursive: true });
  await mkdir(path.join(iconDir, "references"), { recursive: true });
  await mkdir(path.join(iconDir, "config"), { recursive: true });
  await writeFile(path.join(iconDir, "SKILL.md"), "---\nname: save-item\ndescription: save\n---\npython scripts/client.py list\n");
  await writeFile(path.join(iconDir, "scripts/client.py"), "def list():\n    method = \"GET\"\n    path = \"/api/items\"\n");
  await writeFile(path.join(iconDir, "references/api.md"), API_HEADINGS);
  await writeFile(path.join(iconDir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(iconDir, "config/auth.local.json"), "{\"headers\":{}}\n");
  await seedGuides(icon.id);
  await appendEvidence(icon.id, {
    kind: "action",
    summary: "snapshot",
    body: { overlay_editable: [{ name: "textbox", role: "textbox", ref: "f0:e1@1" }] },
  });
  await appendEvidence(icon.id, {
    kind: "network",
    summary: "GET /api/items",
    body: { method: "GET", path: "/api/items" },
  });
  await appendEvidence(icon.id, { kind: "verify", ok: true, argv: ["python", "scripts/client.py", "list"] });
  const iconResult = await verifySkill(iconDir, icon.id, icon);
  assert.equal(iconResult.errors.length, 0);

  await writeFile(path.join(dir, "SKILL.md"), [
    "---",
    "name: save-item",
    "description: save",
    "---",
    JSON.stringify({
      title: "保存",
      questions: [
        { id: "name", question: "名称", inputType: "badtype", required: true, default: " " },
        { id: "kind", question: "种类", inputType: "select", required: true, options: [{ id: "a" }], default: " " },
        { id: "start", question: "开始", inputType: "date", required: true, default: " " },
        { id: "note", question: "备注", inputType: "text", dataSource: { url: "/x" }, default: "{}" },
      ],
    }),
    "python scripts/client.py save --confirm",
    ASK_PROTOCOL,
  ].join("\n"));
  const hard = await verifySkill(dir, shape.id, shape);
  assert.ok(hard.errors.some((item) => item.field === "name" && String(item.hint || "").includes("inputType")));
  assert.ok(hard.errors.some((item) => item.field === "kind" && String(item.hint || "").includes("id 和 label")));
  assert.ok(hard.errors.some((item) => item.field === "start" && String(item.hint || "").includes("dateFormat")));
  assert.ok(hard.errors.some((item) => item.field === "note" && String(item.hint || "").includes("dataSource")));
  assert.ok(hard.errors.some((item) => item.field === "note" && String(item.hint || "").includes("JSON")));
});

test("frontmatterErrors checks YAML structure only", () => {
  const badIndent = frontmatterErrors("---\nname: x\n description: y\n---\n");
  assert.equal(badIndent.length, 2);
  assert.ok(badIndent.every((item) => item.code === "not_invocable" && item.file === "SKILL.md"));
  assert.ok(badIndent.some((item) => item.line === "description: y"));
  assert.ok(badIndent.some((item) => item.key === "description"));

  assert.equal(frontmatterErrors("---\nname: x\ndescription: y\n---\nbody\n").length, 0);

  const folded = frontmatterErrors("---\nname: x\ndescription: |\n  first\n  second\n---\n");
  assert.equal(folded.length, 0);

  const unquoted = frontmatterErrors("---\nname: x\ndescription: note: value\n---\n");
  assert.equal(unquoted.length, 1);
  assert.equal(unquoted[0].key, "description");
  assert.equal(frontmatterErrors("---\nname: x\ndescription: \"note: value\"\n---\n").length, 0);

  assert.equal(frontmatterErrors("name: x\ndescription: y\n").length, 1);
  const noName = frontmatterErrors("---\ndescription: y\n---\n");
  assert.equal(noName.length, 1);
  assert.equal(noName[0].key, "name");
  const noDesc = frontmatterErrors("---\nname: x\n---\n");
  assert.equal(noDesc.length, 1);
  assert.equal(noDesc[0].key, "description");

  assert.equal(frontmatterErrors("---\r\nname: x\r\ndescription: y\r\n---\r\nbody\r\n").length, 0);
});

