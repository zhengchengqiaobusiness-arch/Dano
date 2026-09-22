import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile } from "node:fs/promises";
import { spawn } from "node:child_process";
import os from "os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { RecordingFiles } from "../src/fs-store.mjs";
import { exportRecordingSkill } from "../src/skill-export/start-export-session.mjs";
import { listExportedSkills, upsertExportedSkill, skillManifestFromExport } from "../src/skill-export/skill-catalog.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function jsonRequest(port, method, pathname, body) {
  return fetch(`http://127.0.0.1:${port}${pathname}`, {
    method,
    headers: body ? { "content-type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  }).then(async (res) => ({ status: res.status, data: await res.json() }));
}

async function waitHealth(port, timeoutMs = 8000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const hit = await jsonRequest(port, "GET", "/health");
      if (hit.status === 200 && hit.data?.ok && hit.data?.export_catalog) return;
    } catch {
      // 进程还没听端口
    }
    await new Promise((resolve) => setTimeout(resolve, 80));
  }
  throw new Error("Pi_check HTTP 未就绪");
}

test("HTTP 目录在导出写入后立刻能读到同一条，重导不另开一条", async () => {
  const dataDir = await mkdtemp(path.join(os.tmpdir(), "dano-skill-http-"));
  const files = new RecordingFiles(dataDir);
  const recordingId = "rec_http_list";
  const draft = {
    title: "转正办理",
    capabilities: [
      { capability_id: "query", name: "查询", kind: "query" },
      { capability_id: "submit", name: "提交", kind: "submit" },
    ],
    steps: [],
    links: [],
    capability_relations: [{ from: "query", to: "submit" }],
    unresolved: [],
  };
  const port = 18000 + Math.floor(Math.random() * 1000);
  const child = spawn(process.execPath, ["src/server.mjs"], {
    cwd: ROOT,
    env: {
      ...process.env,
      PI_CHECK_PORT: String(port),
      PI_CHECK_DATA_DIR: dataDir,
      DANO_SKILL_REFERENCE_DIR: path.resolve(ROOT, "..", "doc"),
    },
    stdio: "ignore",
  });
  try {
    await waitHealth(port);
    const rejected = await jsonRequest(port, "POST", "/v1/pi-recordings/not-a-recording/export-skill", { title: "x" });
    assert.equal(rejected.status, 400);
    assert.equal(rejected.data.status, "export_failed");
    const first = await exportRecordingSkill({
      files,
      recordingId,
      title: draft.title,
      tenant: "acme",
      subsystem: "oa",
      draft,
      outDir: path.join(dataDir, "out"),
      authHeaders: { Authorization: "Bearer test-token-value-12345" },
      existingSkillId: "oa.rec_http_list",
    });
    assert.equal(first.status, "exported", (first.errors || []).join("; "));
    const listed = await jsonRequest(port, "GET", "/v1/skills");
    assert.equal(listed.status, 200);
    assert.equal(listed.data.length, 1);
    assert.equal(listed.data[0].name, "oa.rec_http_list");
    assert.equal(listed.data[0].recording_id, recordingId);
    assert.equal(listed.data[0].version, 1);

    const overlay = {
      ...draft,
      capabilities: [...draft.capabilities, { capability_id: "withdraw", name: "撤回", kind: "delete" }],
    };
    const second = await exportRecordingSkill({
      files,
      recordingId,
      title: overlay.title,
      tenant: "acme",
      subsystem: "oa",
      draft: overlay,
      outDir: path.join(dataDir, "out"),
      authHeaders: { Authorization: "Bearer test-token-value-12345" },
    });
    assert.equal(second.status, "exported", (second.errors || []).join("; "));
    assert.equal(second.skill_id, "oa.rec_http_list");
    const again = await jsonRequest(port, "GET", "/v1/skills");
    assert.equal(again.data.length, 1);
    assert.equal(again.data[0].name, "oa.rec_http_list");
    assert.equal(again.data[0].version, 2);
    assert.equal((await listExportedSkills(files)).length, 1);
  } finally {
    child.kill();
    await new Promise((resolve) => {
      child.once("exit", resolve);
      setTimeout(resolve, 1000);
    });
  }
});

test("HTTP 目录快速导出不开 Skill 4，只写文件", async () => {
  const dataDir = await mkdtemp(path.join(os.tmpdir(), "dano-skill-http-dump-"));
  const files = new RecordingFiles(dataDir);
  const recordingId = "rec_http_dump";
  const draft = {
    title: "转正办理",
    capabilities: [
      { capability_id: "query", name: "查询", kind: "query" },
      { capability_id: "submit", name: "提交", kind: "submit" },
    ],
    steps: [],
    links: [],
    capability_relations: [{ from: "query", to: "submit" }],
    unresolved: [],
  };
  await files.writeDraft(recordingId, { draft, title: draft.title });
  await upsertExportedSkill(files, skillManifestFromExport({
    skillId: "oa.rec_http_dump",
    title: draft.title,
    subsystem: "oa",
    recordingId,
    exportPath: path.join(dataDir, "gone"),
    draft,
  }));
  const port = 18000 + Math.floor(Math.random() * 1000);
  const child = spawn(process.execPath, ["src/server.mjs"], {
    cwd: ROOT,
    env: {
      ...process.env,
      PI_CHECK_PORT: String(port),
      PI_CHECK_DATA_DIR: dataDir,
      DANO_SKILL_REFERENCE_DIR: path.resolve(ROOT, "..", "doc"),
    },
    stdio: "ignore",
  });
  try {
    await waitHealth(port);
    const dumped = await jsonRequest(port, "POST", "/v1/skills/export", {
      out_dir: path.join(dataDir, "out"),
      tenant: "acme",
      auth_headers: { Authorization: "Bearer http-dump-token", "tenant-id": "1" },
    });
    assert.equal(dumped.status, 200, JSON.stringify(dumped.data));
    assert.equal(dumped.data.mode, "dump");
    assert.equal(dumped.data.count, 1, (dumped.data.errors || []).join("; "));
    const dest = dumped.data.written[0];
    const handbook = await readFile(path.join(dest, "SKILL.md"), "utf8");
    assert.match(handbook, /立刻办理/);
    assert.match(handbook, /不要先 ls|禁止 ls/);
    const auth = JSON.parse(await readFile(path.join(dest, "config", "auth.local.json"), "utf8"));
    assert.equal(auth.headers.Authorization, "Bearer http-dump-token");
  } finally {
    child.kill();
    await new Promise((resolve) => {
      child.once("exit", resolve);
      setTimeout(resolve, 1000);
    });
  }
});
