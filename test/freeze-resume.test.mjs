import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { createApp, listen } from "../src/server.mjs";
import { exportCatalogSkills, exportSkill } from "../src/session.mjs";
import { freezeExportFolder, readCatalog, upsertCatalog } from "../src/skillpack/catalog.mjs";
import { persistExportDirectory, writeAuthLocalFile, writebackExportedPackages, writeTokenRecord } from "../src/token-store.mjs";
import { isInternalDataPath } from "../src/paths.mjs";
import { authHeader, jsonRequest, makeTenant, withData } from "./helpers.mjs";

async function seedReadySkill(root, {
  recordingId,
  skillId,
  skillName,
  tenant,
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
    title: skillName,
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
    title: skillName,
    skillDir: source,
  });
  return { recordingId, skillId, source };
}

test("imported freeze keeps the disk copy; recording freeze only removes the export", () => {
  assert.equal(freezeExportFolder({ source: "imported", export_path: "/opt/skills/pack" }), "");
  assert.equal(freezeExportFolder({ source: "playwright_cabp", export_path: "/opt/skills/pack" }), "/opt/skills/pack");
});

test("freeze does not delete an internal data/skills folder", async () => {
  await withData(async (root) => {
    const source = path.join(root, "skills", "app.rec_internal");
    assert.equal(isInternalDataPath(source), true);
    assert.equal(freezeExportFolder({ source: "playwright_cabp", export_path: source }), "");
  });
});

test("freeze removes export copy, resume republishes and catalog export writes again", async () => {
  await withData(async (root) => {
    const tenantName = "acme";
    const { recordingId, skillId, source } = await seedReadySkill(root, {
      recordingId: "rec_ffffffffffffffffffffffffffffffff",
      skillId: "app.rec_ffffffffffffffffffffffffffffffff",
      skillName: "leave-pack",
      tenant: tenantName,
    });
    const outDir = path.join(root, "agents-skills");
    await persistExportDirectory(outDir);
    const exported = await exportSkill(recordingId, outDir);
    const dest = exported.export_path;
    assert.equal(existsSync(path.join(dest, "SKILL.md")), true);

    const server = createApp();
    const address = await listen(server, 0);
    try {
      const tenant = await makeTenant(address.port, tenantName);
      const headers = authHeader(tenant.api_key);
      const frozen = await jsonRequest(address.port, "POST", `/v1/skills/${encodeURIComponent(skillId)}/freeze`, {}, headers);
      assert.equal(frozen.status, 200);
      assert.equal(frozen.data.state, "suspended");
      assert.equal(frozen.data.frozen, true);
      assert.deepEqual(frozen.data.removed_folders, [dest]);
      assert.equal(existsSync(dest), false);
      assert.equal(existsSync(path.join(source, "SKILL.md")), true);
      const catalog = await readCatalog();
      assert.equal(catalog.find((row) => row.skill_id === skillId).frozen, true);

      const skipped = await jsonRequest(address.port, "POST", "/v1/skills/export", {}, headers);
      assert.equal(skipped.data.count, 0);

      const resumed = await jsonRequest(address.port, "POST", `/v1/skills/${encodeURIComponent(recordingId)}/resume`, {}, headers);
      assert.equal(resumed.status, 200);
      assert.equal(resumed.data.state, "published");
      assert.equal(resumed.data.frozen, false);
      assert.deepEqual(resumed.data.removed_folders, []);
      assert.equal(existsSync(dest), false);

      const again = await jsonRequest(address.port, "POST", "/v1/skills/export", {}, headers);
      assert.equal(again.status, 200);
      assert.equal(again.data.count, 1);
      assert.equal(existsSync(path.join(dest, "SKILL.md")), true);
    } finally {
      await new Promise((resolve) => server.close(resolve));
    }
  });
});

test("imported skill freeze does not delete the package folder", async () => {
  await withData(async (root) => {
    const tenantName = "acme";
    const dest = path.join(root, "imported-pack");
    await mkdir(dest, { recursive: true });
    await writeFile(path.join(dest, "SKILL.md"), "---\nname: imported-pack\n---\n");
    await upsertCatalog({
      id: "rec_imported000000000000000000000001",
      skillId: "app.imported-pack",
      tenant: tenantName,
      subsystem: "app",
      title: "imported",
      skillDir: dest,
    });
    const items = await readCatalog();
    items[0].source = "imported";
    items[0].export_path = dest;
    await writeFile(path.join(root, "skill-catalog.json"), `${JSON.stringify(items, null, 2)}\n`);

    const server = createApp();
    const address = await listen(server, 0);
    try {
      const tenant = await makeTenant(address.port, tenantName);
      const frozen = await jsonRequest(
        address.port,
        "POST",
        "/v1/skills/app.imported-pack/freeze",
        {},
        authHeader(tenant.api_key),
      );
      assert.equal(frozen.status, 200);
      assert.equal(frozen.data.frozen, true);
      assert.deepEqual(frozen.data.removed_folders, []);
      assert.equal(existsSync(path.join(dest, "SKILL.md")), true);
    } finally {
      await new Promise((resolve) => server.close(resolve));
    }
  });
});

test("freeze/resume/delete/export stay inside the owning tenant", async () => {
  await withData(async (root) => {
    const { skillId } = await seedReadySkill(root, {
      recordingId: "rec_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      skillId: "app.rec_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      skillName: "acme-pack",
      tenant: "acme",
    });
    await seedReadySkill(root, {
      recordingId: "rec_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      skillId: "app.rec_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      skillName: "other-pack",
      tenant: "otherco",
    });
    await upsertCatalog({
      id: "rec_cccccccccccccccccccccccccccccccc",
      skillId: "app.rec_cccccccccccccccccccccccccccccccc",
      tenant: "",
      subsystem: "app",
      title: "unowned",
      skillDir: path.join(root, "skills", "app.rec_cccccccccccccccccccccccccccccccc"),
    });
    const outDir = path.join(root, "agents-skills");
    await persistExportDirectory(outDir);
    const server = createApp();
    const address = await listen(server, 0);
    try {
      const acme = await makeTenant(address.port, "acme");
      const other = await makeTenant(address.port, "otherco");
      const missingKey = await jsonRequest(address.port, "POST", `/v1/skills/${encodeURIComponent(skillId)}/freeze`);
      assert.equal(missingKey.status, 401);
      const stolenFreeze = await jsonRequest(
        address.port,
        "POST",
        `/v1/skills/${encodeURIComponent(skillId)}/freeze`,
        {},
        authHeader(other.api_key),
      );
      assert.equal(stolenFreeze.status, 404);
      const stolenResume = await jsonRequest(
        address.port,
        "POST",
        `/v1/skills/${encodeURIComponent(skillId)}/resume`,
        {},
        authHeader(other.api_key),
      );
      assert.equal(stolenResume.status, 404);
      const stolenDelete = await jsonRequest(
        address.port,
        "DELETE",
        `/v1/skills/${encodeURIComponent(skillId)}`,
        undefined,
        authHeader(other.api_key),
      );
      assert.equal(stolenDelete.status, 404);
      const listed = await jsonRequest(address.port, "GET", "/v1/skills", undefined, authHeader(acme.api_key));
      assert.deepEqual(listed.data.items.map((row) => row.skill_id), ["app.rec_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]);
      await jsonRequest(address.port, "POST", `/v1/skills/${encodeURIComponent(skillId)}/freeze`, {}, authHeader(acme.api_key));
      const afterFreeze = await jsonRequest(
        address.port,
        "GET",
        "/v1/skills?include_frozen=0",
        undefined,
        authHeader(acme.api_key),
      );
      assert.equal(afterFreeze.data.total, 0);
      const skipped = await exportCatalogSkills(outDir, { tenant: "acme" });
      assert.equal(skipped.count, 0);
      await jsonRequest(address.port, "POST", `/v1/skills/${encodeURIComponent(skillId)}/resume`, {}, authHeader(acme.api_key));
      const again = await exportCatalogSkills(outDir, { tenant: "acme" });
      assert.equal(again.count, 1);
      assert.match(again.written[0], /acme-pack/);
    } finally {
      await new Promise((resolve) => server.close(resolve));
    }
  });
});

test("token writeback does not rewrite another tenant's exported package", async () => {
  await withData(async (root) => {
    const outDir = path.join(root, "agents-skills");
    await persistExportDirectory(outDir);
    const acmeDir = path.join(outDir, "acme-pack");
    const otherDir = path.join(outDir, "other-pack");
    for (const [dir, tenant] of [[acmeDir, "acme"], [otherDir, "otherco"]]) {
      await mkdir(path.join(dir, "config"), { recursive: true });
      await writeFile(path.join(dir, "config", "runtime.json"), JSON.stringify({
        tenant,
        subsystem: "oa",
      }));
      await writeAuthLocalFile(dir, { Authorization: `Bearer ${tenant}-old` });
    }
    await writeTokenRecord("acme", "oa", { Authorization: "Bearer acme-new" });
    const result = await writebackExportedPackages({
      subsystem: "oa",
      headers: { Authorization: "Bearer acme-new" },
      tenant: "acme",
    });
    assert.deepEqual(result.updated, [acmeDir]);
    const acmeAuth = JSON.parse(await readFile(path.join(acmeDir, "config", "auth.local.json"), "utf8"));
    const otherAuth = JSON.parse(await readFile(path.join(otherDir, "config", "auth.local.json"), "utf8"));
    assert.match(acmeAuth.headers.Authorization, /acme-new/);
    assert.match(otherAuth.headers.Authorization, /otherco-old/);
  });
});

test("single recording export-skill stays open unless a tenant key mismatches", async () => {
  await withData(async (root) => {
    const { recordingId } = await seedReadySkill(root, {
      recordingId: "rec_dddddddddddddddddddddddddddddddd",
      skillId: "app.rec_dddddddddddddddddddddddddddddddd",
      skillName: "export-one",
      tenant: "acme",
    });
    const server = createApp();
    const address = await listen(server, 0);
    try {
      const acme = await makeTenant(address.port, "acme");
      const other = await makeTenant(address.port, "otherco");
      const open = await jsonRequest(
        address.port,
        "POST",
        `/v1/pi-recordings/${recordingId}/export-skill`,
        { out_dir: path.join(root, "agents-skills") },
      );
      assert.equal(open.status, 200);
      assert.equal(open.data.status, "exported");
      const stolen = await jsonRequest(
        address.port,
        "POST",
        `/v1/pi-recordings/${recordingId}/export-skill`,
        { out_dir: path.join(root, "agents-skills") },
        authHeader(other.api_key),
      );
      assert.equal(stolen.status, 404);
      const own = await jsonRequest(
        address.port,
        "POST",
        `/v1/pi-recordings/${recordingId}/export-skill`,
        { out_dir: path.join(root, "agents-skills") },
        authHeader(acme.api_key),
      );
      assert.equal(own.status, 200);
    } finally {
      await new Promise((resolve) => server.close(resolve));
    }
  });
});
