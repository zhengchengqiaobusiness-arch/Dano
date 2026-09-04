/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 */

import test from "node:test";
import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SRC = path.join(ROOT, "src");

const FORBIDDEN = [
  /to_flow_spec/,
  /classify_network_request/,
  /ensure_grounded_capability/,
  /compile_capabilities/,
  /capability_compiler/,
  /recording_gateway/,
  /recording_pi\.py/,
  /grounded_action_fallback/,
  /apply_deterministic/,
  /materialize_recording/,
  /inferCapability/,
  /generateCapability/,
  /代码处理中/,
  /自动补齐中/,
  /本地修复中/,
];

async function walk(dir, files = []) {
  const entries = await readdir(dir, { withFileTypes: true });
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) await walk(full, files);
    else files.push(full);
  }
  return files;
}

test("12. 源码不存在本地能力生成、推断、补齐、编译、修复或回退入口", async () => {
  const files = await walk(SRC);
  const hits = [];
  for (const file of files) {
    const text = await readFile(file, "utf8");
    for (const pattern of FORBIDDEN) {
      if (pattern.test(text)) {
        hits.push(`${file}: ${pattern}`);
      }
    }
  }
  assert.deepEqual(hits, []);
  const controller = await readFile(path.join(SRC, "recording-controller.mjs"), "utf8");
  assert.match(controller, /旧录制逻辑绝不启动/);
  assert.match(controller, /createPi/);
  assert.match(controller, /唯一录制链路/);
});
