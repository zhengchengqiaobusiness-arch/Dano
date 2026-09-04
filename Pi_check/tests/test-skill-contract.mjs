/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 */

import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { buildPiInstructions, readRecordingSkill } from "../src/pi-session.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SKILL_PATH = path.join(ROOT, "skill", "RECORDING_CAPABILITY.md");

test("Skill 写死现有录制页能读到的信封，并禁止页面忽略的私有结构", async () => {
  const skill = await readFile(SKILL_PATH, "utf8");
  assert.match(skill, /input_schema\.properties/);
  assert.match(skill, /request_refs/);
  assert.match(skill, /step_id/);
  assert.match(skill, /exposed_to_user/);
  assert.match(skill, /enum_options/);
  assert.match(skill, /capabilities\[\]\.fields/);
  assert.match(skill, /不要写它们/);
  assert.match(skill, /字段对象数组/);
  assert.match(skill, /先建动作台账/);
  assert.match(skill, /不要按 URL 或 HTTP 方法合并/);
  assert.match(skill, /capability_id/);
  assert.match(skill, /selected_record_identity/);
  assert.doesNotMatch(skill, /to_flow_spec|compile_capabilities|inferCapability/);
  const loaded = await readRecordingSkill();
  assert.equal(loaded, skill);
  const instructions = buildPiInstructions(skill);
  assert.match(instructions, /不要写 capabilities\[\]\.fields/);
  assert.match(instructions, /input_schema\.properties/);
});
