import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import path from "node:path";
import { packageRoot } from "../src/paths.mjs";

test("web has three steps and no capability page", async () => {
  const html = await readFile(path.join(packageRoot(), "frontend", "src", "layout", "StudioHeader.tsx"), "utf8");
  assert.match(html, /录制准备/);
  assert.match(html, /页面录制/);
  assert.match(html, /Skill 目录/);
  assert.equal(html.includes("能力结果"), false);
  assert.equal(html.includes("产出 Skill"), false);
  const recorder = await readFile(path.join(packageRoot(), "frontend", "src", "components", "PageRecorder.tsx"), "utf8");
  assert.equal(recorder.includes("校验未通过"), false);
  assert.equal(recorder.includes("需要 token"), false);
  assert.match(recorder, /录制中/);
  assert.match(recorder, /Skill 产出完毕/);
  assert.match(recorder, /产出失败/);
});
