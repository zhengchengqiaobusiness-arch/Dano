import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { writeSkillFile } from "../src/skillpack/files.mjs";
import { verifySkill } from "../src/skillpack/verify.mjs";
import { skillDir } from "../src/paths.mjs";

const recording = { id: "rec_verify", tenant: "", subsystem: "app", skillId: "app.rec_verify", startUrl: "http://127.0.0.1/" };

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
