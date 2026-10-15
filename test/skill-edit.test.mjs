import assert from "node:assert/strict";
import test from "node:test";
import { hostTools } from "../src/agent/tools.mjs";
import { withData } from "./helpers.mjs";

const skillBody = `---
name: demo-pack
description: demo
---
alpha line
alpha line
`;

test("edit_skill_file replaces a unique span and refuses missing, duplicate, or frozen paths", async () => {
  await withData(async () => {
    const recording = { id: "rec_edit", subsystem: "app", tenant: "", startUrl: "http://example.test/" };
    const tools = hostTools(recording);
    const written = await tools.write_skill_file({ relative_path: "SKILL.md", contents: skillBody });
    assert.equal(written.ok, true);
    const missing = await tools.edit_skill_file({
      relative_path: "SKILL.md",
      old_text: "not-in-file",
      new_text: "x",
    });
    assert.equal(missing.ok, false);
    assert.equal(missing.error, "old_text_not_found");
    const dup = await tools.edit_skill_file({
      relative_path: "SKILL.md",
      old_text: "alpha line",
      new_text: "beta line",
    });
    assert.equal(dup.ok, false);
    assert.equal(dup.error, "old_text_not_unique");
    assert.equal(dup.count, 2);
    const ok = await tools.edit_skill_file({
      relative_path: "SKILL.md",
      old_text: "alpha line\nalpha line\n",
      new_text: "beta line\n",
    });
    assert.equal(ok.ok, true);
    assert.equal(Boolean(ok.structure_errors), Boolean(written.structure_errors));
    const read = await tools.read_skill_file({ relative_path: "SKILL.md" });
    assert.match(read.contents, /beta line/);
    assert.doesNotMatch(read.contents, /alpha line/);
    const frozen = await tools.edit_skill_file({
      relative_path: "config/runtime.json",
      old_text: "{",
      new_text: "{}",
    });
    assert.equal(frozen.ok, false);
    assert.equal(frozen.error, "frozen_file");
  });
});
