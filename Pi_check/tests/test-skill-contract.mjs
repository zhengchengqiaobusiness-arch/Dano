/**
 * 只断言四份 Skill 被加载。禁止锁提示词/Skill 金句。
 */

import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, writeFile, mkdir } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  REQUIRED_SKILL_FILES,
  readRequiredSkills,
  buildPiInstructions,
  buildLiveDrivePrompt,
  buildFinalAnalysisPrompt,
} from "../src/pi-session.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SKILL_DIR = path.join(ROOT, "skill");

test("PI 必须加载且只加载四份 Skill", async () => {
  const loaded = await readRequiredSkills();
  assert.deepEqual(loaded.map((item) => item.name), REQUIRED_SKILL_FILES);
  assert.equal(loaded.length, 4);
  for (const item of loaded) {
    assert.ok(item.text.length > 20, item.name);
  }
  const instructions = buildPiInstructions(loaded);
  assert.match(instructions, /Business Skill Investigator/);
  assert.match(instructions, /control_in_app_browser/);
  assert.match(instructions, /project_contract_to_request/);
  assert.doesNotMatch(instructions, /RECORDING_CAPABILITY/);
  assert.doesNotMatch(instructions, /submit_recording_draft/);
});

test("缺一份 Skill 文件不准开录", async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "dano-skills-"));
  await mkdir(dir, { recursive: true });
  for (const name of REQUIRED_SKILL_FILES.slice(0, 3)) {
    await writeFile(path.join(dir, name), `# ${name}\n内容`, "utf8");
  }
  await assert.rejects(() => readRequiredSkills(dir), /缺少 Skill 文件/);
});

test("入口和定稿提示只留协调句，不含字段细则", () => {
  const drive = buildLiveDrivePrompt({ targetUrl: "http://example.com", goal: "做成能力" });
  assert.match(drive, /Investigator/);
  assert.match(drive, /做成能力/);
  assert.match(drive, /http:\/\/example.com/);
  assert.doesNotMatch(drive, /x-dano-section-titles/);
  assert.doesNotMatch(drive, /部门树/);
  assert.doesNotMatch(drive, /空表/);
  const prompt = buildFinalAnalysisPrompt(3);
  assert.match(prompt, /Skill 1/);
  assert.match(prompt, /seq=3/);
  assert.doesNotMatch(prompt, /x-dano-section-titles/);
  assert.doesNotMatch(prompt, /部门树/);
  assert.doesNotMatch(prompt, /确认弹层/);
});

test("仓库里没有旧 RECORDING_CAPABILITY", async () => {
  const { readdir } = await import("node:fs/promises");
  const names = await readdir(SKILL_DIR);
  assert.equal(names.includes("RECORDING_CAPABILITY.md"), false);
  assert.deepEqual(
    names.filter((name) => name.endsWith(".md")).sort(),
    REQUIRED_SKILL_FILES.slice().sort(),
  );
});
