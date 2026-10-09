import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import path from "node:path";
import { packageRoot } from "../src/paths.mjs";
import { recordingSystemPrompt } from "../src/agent/pi-session.mjs";

test("skills are full text", async () => {
  const playwright = await readFile(path.join(packageRoot(), "skill", "playwright-cli", "SKILL.md"), "utf8");
  const derive = await readFile(path.join(packageRoot(), "skill", "derive-client", "SKILL.md"), "utf8");
  assert.ok(playwright.split(/\n/).length > 40);
  assert.match(playwright, /snapshot/);
  assert.match(playwright, /click/);
  assert.ok(derive.split(/\n/).length > 40);
  assert.match(derive, /HAR|record/);
  const packed = await recordingSystemPrompt({ goal_text: "KEEP_GOAL_TEXT", page_url: "http://example.test/" });
  assert.match(packed, /代码只运输/);
  assert.match(packed, /snapshot/);
  assert.match(packed, /HAR|record/);
  assert.match(packed, /KEEP_GOAL_TEXT/);
});
