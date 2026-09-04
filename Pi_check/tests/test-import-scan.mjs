/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 */

import test from "node:test";
import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SKIP = new Set(["node_modules", "data", "runtime", ".git"]);

async function walk(dir, files = []) {
  const entries = await readdir(dir, { withFileTypes: true });
  for (const entry of entries) {
    if (SKIP.has(entry.name)) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) await walk(full, files);
    else if (/\.(mjs|js|cjs|ts)$/.test(entry.name)) files.push(full);
  }
  return files;
}

test("11. Pi_check 不得引用目录外的录制模块或项目模块", async () => {
  const files = await walk(ROOT);
  const importRe = /(?:import|export)\s+[\s\S]*?from\s+["']([^"']+)["']|require\(\s*["']([^"']+)["']\s*\)/g;
  const violations = [];
  for (const file of files) {
    const text = await readFile(file, "utf8");
    for (const match of text.matchAll(importRe)) {
      const spec = match[1] || match[2];
      if (!spec) continue;
      if (spec.startsWith("node:") || spec.startsWith("@") || !spec.startsWith(".")) {
        if (spec.startsWith("dano") || spec.includes("onboarding") || spec.includes("recording_gateway") || spec.includes("recording_pi")) {
          violations.push(`${file}: ${spec}`);
        }
        continue;
      }
      const resolved = path.resolve(path.dirname(file), spec);
      const rel = path.relative(ROOT, resolved);
      if (rel.startsWith("..") || path.isAbsolute(rel)) {
        violations.push(`${file}: ${spec} -> ${resolved}`);
      }
    }
  }
  assert.deepEqual(violations, []);
});
