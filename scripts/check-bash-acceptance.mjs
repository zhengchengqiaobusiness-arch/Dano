#!/usr/bin/env node
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const root = process.argv[2] || process.env.DANO_RUNTIME_DIR || "/opt/dano/runtime-data";
const expected = process.env.DANO_BASH_ACCEPTANCE_TEXT || "DANO_BASH_OK";
const bwrapError = /\bbwrap\b.*(error|failed|Operation not permitted|must be installed setuid)|bubblewrap.*(error|failed)/i;

function walk(dir, files = []) {
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry);
    const stat = statSync(path);
    if (stat.isDirectory()) walk(path, files);
    else if (entry.endsWith(".jsonl")) files.push(path);
  }
  return files;
}

function textOf(value) {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(textOf).join("\n");
  if (value && typeof value === "object") return Object.values(value).map(textOf).join("\n");
  return "";
}

if (!existsSync(root)) {
  throw new Error(`runtime directory not found: ${root}`);
}

let sawBashCall = false;
let sawSuccessfulResult = false;
let sawBwrapError = false;

for (const file of walk(root)) {
  for (const line of readFileSync(file, "utf8").split(/\r?\n/)) {
    if (!line.trim()) continue;
    const text = line;
    if (bwrapError.test(text)) sawBwrapError = true;

    let entry;
    try {
      entry = JSON.parse(line);
    } catch {
      continue;
    }

    if (entry.type === "toolCall" && entry.name === "bash") sawBashCall = true;
    if (entry.role === "toolResult" && entry.toolName === "bash") {
      const content = textOf(entry.content);
      if (entry.isError === false && content.includes(expected)) {
        sawSuccessfulResult = true;
      }
      if (bwrapError.test(content)) sawBwrapError = true;
    }
  }
}

console.log(`bash tool call: ${sawBashCall ? "yes" : "no"}`);
console.log(`bash result ${expected}: ${sawSuccessfulResult ? "yes" : "no"}`);
console.log(`bwrap errors: ${sawBwrapError ? "yes" : "no"}`);

if (!sawBashCall || !sawSuccessfulResult || sawBwrapError) {
  process.exit(1);
}
