import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { createApp, listen } from "../src/server.mjs";
import { isInternalDataPath } from "../src/paths.mjs";
import { applyVerifyToRecording, exportedPackageName, exportSkill, loadRecording, releaseVerifiedSkill } from "../src/session.mjs";
import {
  LINUX_EXPORT_DIRECTORY,
  defaultExportDirectory,
  pickExportDirectory,
  persistExportDirectory,
  resolveExportDirectory,
  usableExportDir,
} from "../src/token-store.mjs";
import { exportedFolderToRemove, readCatalog, upsertCatalog } from "../src/skillpack/catalog.mjs";
import { authHeader, jsonRequest, makeTenant, withData } from "./helpers.mjs";

async function seedReadySkill(root, {
  recordingId = "rec_export1",
  skillId = "app.rec_export1",
  skillName = "oa-work-report",
  tenant = "acme",
} = {}) {
  const source = path.join(root, "skills", skillId);
  await mkdir(path.join(source, "scripts"), { recursive: true });
  await mkdir(path.join(source, "references"), { recursive: true });
  await writeFile(path.join(source, "SKILL.md"), `---\nname: ${skillName}\ndescription: test\n---\n\n# ${skillName}\n`);
  await writeFile(path.join(source, "scripts", "client.py"), "print(0)\n");
  await writeFile(path.join(source, "references", "api.md"), "# 字段\n# 绑定\n# 未解决\n# 已验证读命令\n");
  await mkdir(path.join(root, "recordings", recordingId), { recursive: true });
  await writeFile(path.join(root, "recordings", recordingId, "state.json"), JSON.stringify({
    id: recordingId,
    tenant,
    subsystem: "app",
    title: "识别当前页面",
    startUrl: "http://example.test/",
    status: "skill_ready",
    skillId,
    skillDir: source,
    finished: true,
    wroteSkill: true,
    verify: { ok: true, status: "skill_ready", errors: [] },
  }, null, 2));
  await upsertCatalog({
    id: recordingId,
    skillId,
    tenant,
    subsystem: "app",
    title: "识别当前页面",
    skillDir: source,
  });
  return { recordingId, skillId, source };
}

test("linux default export dir is dano agents skills", () => {
  assert.equal(defaultExportDirectory("linux", {}), LINUX_EXPORT_DIRECTORY);
  assert.equal(usableExportDir("E:\\\\python\\\\export", "linux"), "");
  assert.equal(pickExportDirectory("", "E:\\\\python\\\\export", "linux", {}), LINUX_EXPORT_DIRECTORY);
  assert.equal(pickExportDirectory("/tmp/out", "", "linux", {}), "/tmp/out");
  assert.equal(exportedPackageName("---\nname: oa-work-report\n---\n", "app.rec_1"), "oa-work-report");
});

test("export copies into a package folder and freeze does not delete the source", async () => {
  await withData(async (root) => {
    const { recordingId, skillId, source } = await seedReadySkill(root);
    const outDir = path.join(root, "agents-skills");
    const outcome = await exportSkill(recordingId, outDir);
    const dest = path.join(outDir, "oa-work-report");
    assert.equal(outcome.status, "exported");
    assert.equal(outcome.export_path, dest);
    assert.equal(outcome.out_dir, outDir);
    assert.equal(existsSync(path.join(dest, "SKILL.md")), true);
    assert.equal(existsSync(path.join(outDir, "SKILL.md")), false);
    assert.equal(existsSync(path.join(source, "SKILL.md")), true);
    const catalog = await readCatalog();
    assert.equal(catalog[0].export_path, dest);
    assert.equal(exportedFolderToRemove(catalog[0]), dest);
    assert.equal(isInternalDataPath(source), true);
    assert.equal(exportedFolderToRemove({ export_path: source }), "");

    const server = createApp();
    const address = await listen(server, 0);
    try {
      const tenant = await makeTenant(address.port, "acme");
      const frozen = await jsonRequest(address.port, "POST", `/v1/skills/${encodeURIComponent(skillId)}/freeze`, {}, authHeader(tenant.api_key));
      assert.equal(frozen.status, 200);
      assert.equal(frozen.data.frozen, true);
      assert.deepEqual(frozen.data.removed_folders, [dest]);
      assert.equal(existsSync(dest), false);
      assert.equal(existsSync(path.join(source, "SKILL.md")), true);
      const byRecording = await jsonRequest(address.port, "POST", `/v1/skills/${encodeURIComponent(recordingId)}/resume`, {}, authHeader(tenant.api_key));
      assert.equal(byRecording.status, 200);
      assert.equal(byRecording.data.frozen, false);
    } finally {
      await new Promise((resolve) => server.close(resolve));
    }
  });
});

test("catalog export and directory endpoints use the resolved out dir", async () => {
  await withData(async (root) => {
    const { recordingId } = await seedReadySkill(root);
    const outDir = path.join(root, "agents-skills");
    await persistExportDirectory(outDir);
    const server = createApp();
    const address = await listen(server, 0);
    try {
      const tenant = await makeTenant(address.port, "acme");
      const headers = authHeader(tenant.api_key);
      const dir = await jsonRequest(address.port, "GET", "/export/directory", undefined, headers);
      assert.equal(dir.data.out_dir, outDir);
      const listed = await jsonRequest(address.port, "POST", "/v1/skills/export", {}, headers);
      assert.equal(listed.status, 200);
      assert.equal(listed.data.count, 1);
      assert.equal(listed.data.out_dir, outDir);
      assert.equal(listed.data.written[0], path.join(outDir, "oa-work-report"));
      const handbook = await readFile(path.join(outDir, "oa-work-report", "SKILL.md"), "utf8");
      assert.match(handbook, /name: oa-work-report/);
      await jsonRequest(address.port, "POST", `/v1/skills/${encodeURIComponent(recordingId)}/freeze`, {}, headers);
      const skipped = await jsonRequest(address.port, "POST", "/v1/skills/export", {}, headers);
      assert.equal(skipped.data.count, 0);
    } finally {
      await new Promise((resolve) => server.close(resolve));
    }
  });
});

test("verify success copies the package into the resolved export dir without ending the session", async () => {
  await withData(async (root) => {
    const { recordingId, source } = await seedReadySkill(root, { recordingId: "rec_auto_export", skillId: "app.rec_auto_export" });
    const outDir = path.join(root, "agents-skills");
    await persistExportDirectory(outDir);
    const recording = await loadRecording(recordingId);
    recording.finished = false;
    applyVerifyToRecording(recording, { status: "skill_ready", errors: [] }, source);
    assert.equal(recording.finished, false);
    assert.equal(recording.skillReady, true);
    const outcome = await releaseVerifiedSkill(recording);
    assert.equal(outcome.status, "exported");
    assert.equal(existsSync(path.join(outDir, "oa-work-report", "SKILL.md")), true);
    assert.equal(existsSync(path.join(outDir, "oa-work-report", "config", "auth.local.json")), true);
    assert.equal(recording.finished, false);
    assert.equal(recording.export.status, "exported");
  });
});

test("resolveExportDirectory falls back to the platform default", async () => {
  await withData(async (root) => {
    assert.equal(await resolveExportDirectory(), defaultExportDirectory());
    assert.ok(String(await persistExportDirectory("")).length > 0);
    assert.equal(path.basename(root).startsWith("cabp-data-"), true);
  });
});
