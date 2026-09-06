import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import {
  defaultSkillCredentialRoot,
  defaultSkillOutputRoot,
  LINUX_SKILL_CREDENTIAL_ROOT,
  LINUX_SKILL_OUTPUT_ROOT
} from "../src/config.js";

test("uses the Dano runtime skill directory by default on Linux", () => {
  assert.equal(defaultSkillOutputRoot("/workspace/studio", "linux"), LINUX_SKILL_OUTPUT_ROOT);
  assert.equal(LINUX_SKILL_OUTPUT_ROOT, "/opt/dano/runtime-data/.agents/skills");
  assert.equal(defaultSkillCredentialRoot(LINUX_SKILL_OUTPUT_ROOT, "linux"), LINUX_SKILL_CREDENTIAL_ROOT);
  assert.equal(LINUX_SKILL_CREDENTIAL_ROOT, "/opt/dano/runtime-data/.agents/bak");
  assert.equal(defaultSkillCredentialRoot("/tmp/custom-skills", "linux"), LINUX_SKILL_CREDENTIAL_ROOT);
});

test("keeps the project-local default outside Linux", () => {
  assert.equal(
    defaultSkillOutputRoot("C:\\studio", "win32"),
    path.join("C:\\studio", "dist", "skills")
  );
});
