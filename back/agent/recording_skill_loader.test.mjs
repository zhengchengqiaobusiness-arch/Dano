import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

import { DefaultResourceLoader, SettingsManager } from "@earendil-works/pi-coding-agent";
import { AgentSession } from "./node_modules/@earendil-works/pi-coding-agent/dist/core/agent-session.js";
import {
  beginRecordingToolTurn,
  endRecordingToolTurn,
  guardRecordingToolAccess,
} from "./recording_tools.mjs";

const AGENT_DIR = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"));
const BACK_DIR = path.dirname(AGENT_DIR);
const REPO_DIR = path.dirname(BACK_DIR);
const SKILL_NAME = "analyze-recording-evidence";
const SKILL_DIR = path.join(AGENT_DIR, "recording-pi", "skills", SKILL_NAME);
const SKILL_FILE = path.join(SKILL_DIR, "SKILL.md");
const RUNTIME_FILE = path.join(AGENT_DIR, "run_recording_pi.mjs");
const CLIENT_FILE = path.join(BACK_DIR, "dano", "onboarding", "recording_pi.py");
const PIPELINE_TEST_FILE = path.join(BACK_DIR, "tests", "test_recording_pipeline.py");
const TEST_TEMP_DIR = path.join(REPO_DIR, ".runtime", "node-tests");

async function makeTempDir(prefix) {
  await mkdir(TEST_TEMP_DIR, { recursive: true });
  return mkdtemp(path.join(TEST_TEMP_DIR, prefix));
}

function settings() {
  return SettingsManager.inMemory({
    skills: [], extensions: [], prompts: [], packages: [], themes: [],
  });
}

async function loadOnly(skillPath, { cwd = BACK_DIR, agentDir } = {}) {
  const loader = new DefaultResourceLoader({
    cwd,
    agentDir: agentDir || path.join(cwd, ".pi-recording-agent"),
    settingsManager: settings(),
    noExtensions: true,
    noSkills: true,
    additionalSkillPaths: [skillPath],
    noPromptTemplates: true,
    noThemes: true,
    noContextFiles: true,
  });
  await loader.reload();
  return { loader, result: loader.getSkills() };
}

function skillMarkdownBody(raw) {
  return raw.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n/, "").trim().replace(/\r\n/g, "\n");
}

function expandedSkillBody(expanded, task) {
  const match = expanded.match(/^<skill[\s\S]*?>\n?([\s\S]*?)<\/skill>/);
  assert.ok(match, "expanded Skill output must include a skill wrapper");
  assert.equal(expanded.slice(match.index + match[0].length).trim(), task.trim());
  return match[1]
    .replace(/^References are relative to[^\n]*\n\n/, "")
    .trim()
    .replace(/\r\n/g, "\n");
}

test("loads exactly the project recording analysis Skill from the required path", async () => {
  const { result } = await loadOnly(SKILL_DIR);

  assert.deepEqual(result.diagnostics, []);
  assert.equal(result.skills.length, 1);
  assert.equal(result.skills[0].name, SKILL_NAME);
  assert.equal(path.resolve(result.skills[0].filePath), path.resolve(SKILL_FILE));
  assert.doesNotMatch(result.skills[0].filePath, /onboard-system/i);
});

test("noSkills isolation excludes global, project-default, and retired Skills", async () => {
  const temp = await makeTempDir("dano-recording-skill-");
  try {
    const fakeGlobal = path.join(temp, "agent", "skills", "global-skill");
    const fakeProject = path.join(temp, "cwd", ".pi", "skills", "onboard-system");
    await mkdir(fakeGlobal, { recursive: true });
    await mkdir(fakeProject, { recursive: true });
    await writeFile(path.join(fakeGlobal, "SKILL.md"), "---\nname: global-skill\ndescription: global\n---\nglobal");
    await writeFile(path.join(fakeProject, "SKILL.md"), "---\nname: onboard-system\ndescription: retired\n---\nretired");

    const { result } = await loadOnly(SKILL_DIR, {
      cwd: path.join(temp, "cwd"),
      agentDir: path.join(temp, "agent"),
    });
    assert.deepEqual(result.skills.map((item) => item.name), [SKILL_NAME]);
  } finally {
    await rm(temp, { recursive: true, force: true });
  }
});

test("Pi native skill expansion places the complete Skill body before the task", async () => {
  const { loader } = await loadOnly(SKILL_DIR);
  const task = "执行本批录制分析";
  const expanded = AgentSession.prototype._expandSkillCommand.call({
    resourceLoader: loader,
    _extensionRunner: { emitError: (error) => assert.fail(String(error)) },
  }, `/skill:${SKILL_NAME} ${task}`);
  const raw = await readFile(SKILL_FILE, "utf8");
  const body = skillMarkdownBody(raw);

  assert.match(expanded, new RegExp(`<skill name="${SKILL_NAME}"`));
  assert.equal(expandedSkillBody(expanded, task), body);
  assert.ok(expanded.trim().endsWith(task));
});

test("runtime applies one Skill to all and only recording analysis phases", async () => {
  const runtime = await readFile(RUNTIME_FILE, "utf8");
  const client = await readFile(CLIENT_FILE, "utf8");
  const { loader } = await loadOnly(SKILL_DIR);
  const skillBody = skillMarkdownBody(await readFile(SKILL_FILE, "utf8"));

  assert.match(runtime, /noSkills:\s*true/);
  assert.match(runtime, /additionalSkillPaths:\s*\[RECORDING_ANALYSIS_SKILL_PATH\]/);
  assert.match(runtime, /\/skill:\$\{RECORDING_ANALYSIS_SKILL_NAME\}/);
  assert.match(runtime, /expandPromptTemplates:\s*usesRecordingSkill/);
  assert.match(runtime, /promptMode === "recording_analysis"/);
  for (const phase of ["base_state_analysis", "request_batch", "final_request_tail"]) {
    assert.ok(runtime.includes(`"${phase}"`));
    assert.ok(client.includes(`"${phase}"`));
    const task = `执行 ${phase} 阶段`;
    const expanded = AgentSession.prototype._expandSkillCommand.call({
      resourceLoader: loader,
      _extensionRunner: { emitError: (error) => assert.fail(String(error)) },
    }, `/skill:${SKILL_NAME} ${task}`);
    assert.equal(expandedSkillBody(expanded, task), skillBody, `${phase} did not receive the complete Skill body`);
    assert.ok(expanded.trim().endsWith(task), `${phase} task was not preserved after Skill expansion`);
  }
  assert.match(client, /prompt_mode="recording_analysis"/);
  assert.match(client, /else "request_batch"/);
  assert.doesNotMatch(runtime, /promptMode === "(plan|repair|review)"/);
});

test("recording analysis turn rejects tools outside its four-tool contract", () => {
  beginRecordingToolTurn({
    allowedTools: [
      "get_recording_state",
      "get_recording_delta",
      "submit_recording_plan",
      "ask_operator",
    ],
  });
  try {
    assert.doesNotThrow(() => guardRecordingToolAccess("get_recording_state"));
    assert.doesNotThrow(() => guardRecordingToolAccess("submit_recording_plan"));
    assert.throws(
      () => guardRecordingToolAccess("replay_request"),
      /not available in the current recording analysis phase/,
    );
  } finally {
    endRecordingToolTurn();
  }
});

test("recording analysis preserves images and retries the identical wrapped prompt once", async () => {
  const runtime = await readFile(RUNTIME_FILE, "utf8");

  assert.match(runtime, /const sessionPrompt = usesRecordingSkill/);
  assert.equal((runtime.match(/session\.prompt\(sessionPrompt, promptOptions\)/g) || []).length, 2);
  assert.match(runtime, /\.\.\.\(images\.length \? \{ images \} : \{\}\)/);
  assert.match(runtime, /actual\.includes\(`<skill name="\$\{skillName\}"`\)/);
  assert.match(runtime, /actual\.endsWith\(expected\)/);
});

test("missing and malformed Skills produce diagnostics and runtime rejects diagnostics", async () => {
  const temp = await makeTempDir("dano-recording-skill-invalid-");
  try {
    const missing = path.join(temp, "missing");
    const missingResult = (await loadOnly(missing, { cwd: temp })).result;
    assert.ok(missingResult.diagnostics.length > 0);

    const invalid = path.join(temp, "invalid");
    await mkdir(invalid, { recursive: true });
    await writeFile(path.join(invalid, "SKILL.md"), "---\nname: INVALID NAME\n---\nbody");
    const invalidResult = (await loadOnly(invalid, { cwd: temp })).result;
    assert.ok(invalidResult.diagnostics.length > 0);

    const runtime = await readFile(RUNTIME_FILE, "utf8");
    assert.match(runtime, /if \(result\.diagnostics\.length\)/);
    assert.match(runtime, /recording analysis Skill failed to load/);
  } finally {
    await rm(temp, { recursive: true, force: true });
  }
});

test("Skill contract preserves full capabilities, rejected operations, and the final tail", async () => {
  const skill = await readFile(SKILL_FILE, "utf8");
  const runtime = await readFile(RUNTIME_FILE, "utf8");

  assert.match(skill, /full replacement/i);
  assert.match(skill, /Preserve every still-grounded earlier capability/i);
  assert.match(skill, /must not erase other accepted capabilities/i);
  assert.match(skill, /`rejected` or `rolled_back`/i);
  assert.match(skill, /final_request_tail/);
  assert.match(skill, /until `has_more=false`/);
  assert.match(skill, /completing the turn without an accepted `submit_recording_plan` result/i);
  assert.match(skill, /Different concrete goal slots must not share one execute anchor/i);
  assert.match(skill, /captured while opening an edit form may also anchor a separately requested inspect\s+capability/i);
  assert.match(skill, /must not be presented\s+as instance progress/i);
  assert.match(skill, /Pass `plan` as a structured object/i);
  assert.match(skill, /record identity and several exact same-path values/i);
  assert.match(skill, /expose one\s+caller choice per required label/i);
  assert.match(skill, /confirmed captured response binding supplies a later request inside the same capability/i);
  assert.match(skill, /exact current request identities/i);
  assert.match(skill, /sole author of business semantics/i);
  assert.match(skill, /Never treat a mismatch between goal wording and observed actions as a failure/i);
  assert.match(skill, /source_request_id/i);
  assert.match(skill, /resubmit\s+the complete current `semantic_plan`/i);
  assert.match(skill, /Judge each field independently in this order/i);
  assert.match(runtime, /recording_submission_retry/);
  assert.match(runtime, /missing_submission/);
});

test("default-off machine verification retains the no-final-plan regression contract", async () => {
  const pipelineTest = await readFile(PIPELINE_TEST_FILE, "utf8");

  assert.match(pipelineTest, /test_default_off_machine_verification_exports_live_skill_without_final_plan/);
  assert.match(pipelineTest, /default-off mode must not run final Pi planning/);
  assert.match(pipelineTest, /events == \["materialize", "publish"\]/);
});

test("recording runtime contains no page- or business-specific analysis patch", async () => {
  const runtime = await readFile(RUNTIME_FILE, "utf8");
  const skill = await readFile(SKILL_FILE, "utf8");
  const combined = `${runtime}\n${skill}`;

  for (const forbidden of ["点狮", "请假", "oa_duty_leave", "Activity_", "dianshixinxi"]) {
    assert.ok(!combined.includes(forbidden), `business-specific patch found: ${forbidden}`);
  }
  assert.ok(REPO_DIR.endsWith("Dano"));
});
