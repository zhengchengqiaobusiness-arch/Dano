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
});
