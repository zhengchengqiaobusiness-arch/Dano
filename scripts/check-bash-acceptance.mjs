#!/usr/bin/env node
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const runtimeRoot = process.env.DANO_RUNTIME_DIR || "/opt/dano/runtime-data";
const root =
  process.argv[2] ||
  process.env.DANO_BASH_ACCEPTANCE_SESSION ||
  runtimeRoot;
const expected = process.env.DANO_BASH_ACCEPTANCE_TEXT || "DANO_BASH_OK";
const since = process.env.DANO_BASH_ACCEPTANCE_SINCE
  ? Date.parse(process.env.DANO_BASH_ACCEPTANCE_SINCE)
  : undefined;
const marker = process.env.DANO_BASH_ACCEPTANCE_MARKER;
const scanAll = process.env.DANO_BASH_ACCEPTANCE_SCAN_ALL === "1";
const requiredMarkers = parseMarkers(process.env.DANO_BASH_ACCEPTANCE_REQUIRED_MARKERS);
const forbiddenMarkers = parseMarkers(process.env.DANO_BASH_ACCEPTANCE_FORBIDDEN_MARKERS);
const hasExplicitScope =
  Boolean(process.argv[2]) ||
  Boolean(process.env.DANO_BASH_ACCEPTANCE_SESSION) ||
  since !== undefined ||
  Boolean(marker) ||
  scanAll;
const bwrapError = /\bbwrap\b.*(error|failed|Operation not permitted|must be installed setuid)|bubblewrap.*(error|failed)/i;

function parseMarkers(value) {
  if (!value) return [];
  return value
    .split(/[\n,]/)
    .map(item => item.trim())
    .filter(Boolean);
}

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

function messageOf(entry) {
  return entry?.type === "message" && entry.message ? entry.message : entry;
}

function bashToolCalls(message) {
  if (message?.type === "toolCall" && message.name === "bash") return [message];
  if (message?.role !== "assistant" || !Array.isArray(message.content)) return [];
  return message.content.filter(block => block?.type === "toolCall" && block.name === "bash");
}

function jsonlFiles(path) {
  const stat = statSync(path);
  if (stat.isDirectory()) {
    const directFiles = readdirSync(path)
      .filter(entry => entry.endsWith(".jsonl"))
      .map(entry => join(path, entry));
    if (directFiles.length > 0) return directFiles;
    if (scanAll || since !== undefined || marker) return walk(path);
    throw new Error(
      "session directory must contain JSONL files directly; set DANO_BASH_ACCEPTANCE_SCAN_ALL=1, DANO_BASH_ACCEPTANCE_SINCE, or DANO_BASH_ACCEPTANCE_MARKER to scan recursively",
    );
  }
  return path.endsWith(".jsonl") ? [path] : [];
}

function fileContains(file, text) {
  return readFileSync(file, "utf8").includes(text);
}

if (!hasExplicitScope) {
  throw new Error(
    "set DANO_BASH_ACCEPTANCE_SESSION, DANO_BASH_ACCEPTANCE_SINCE, DANO_BASH_ACCEPTANCE_MARKER, pass a session path, or set DANO_BASH_ACCEPTANCE_SCAN_ALL=1 for diagnostic runtime scans",
  );
}

if (since !== undefined && Number.isNaN(since)) {
  throw new Error(`invalid DANO_BASH_ACCEPTANCE_SINCE: ${process.env.DANO_BASH_ACCEPTANCE_SINCE}`);
}

if (!existsSync(root)) {
  throw new Error(`session path not found: ${root}`);
}

let sawBashCall = false;
let sawSuccessfulResult = false;
let sawBwrapError = false;
let sawMarker = !marker;
const bashToolCallIds = new Set();
const requiredMarkerHits = new Map(requiredMarkers.map(item => [item, false]));
const forbiddenMarkerHits = new Map(forbiddenMarkers.map(item => [item, false]));

function scanMarkerText(text) {
  for (const requiredMarker of requiredMarkers) {
    if (text.includes(requiredMarker)) requiredMarkerHits.set(requiredMarker, true);
  }
  for (const forbiddenMarker of forbiddenMarkers) {
    if (text.includes(forbiddenMarker)) forbiddenMarkerHits.set(forbiddenMarker, true);
  }
}

for (const file of jsonlFiles(root)) {
  if (since !== undefined && statSync(file).mtimeMs < since) continue;
  if (marker && !fileContains(file, marker)) continue;

  for (const line of readFileSync(file, "utf8").split(/\r?\n/)) {
    if (!line.trim()) continue;
    const text = line;
    if (marker && text.includes(marker)) sawMarker = true;
    scanMarkerText(text);
    if (bwrapError.test(text)) sawBwrapError = true;

    let entry;
    try {
      entry = JSON.parse(line);
    } catch {
      continue;
    }

    if (marker && textOf(entry).includes(marker)) sawMarker = true;
    scanMarkerText(textOf(entry));
    const message = messageOf(entry);
    const toolCalls = bashToolCalls(message);
    if (toolCalls.length > 0) {
      sawBashCall = true;
      for (const toolCall of toolCalls) {
        if (typeof toolCall.id === "string") bashToolCallIds.add(toolCall.id);
      }
    }
    if (
      message.role === "toolResult" &&
      (message.toolName === "bash" || bashToolCallIds.has(message.toolCallId))
    ) {
      const content = textOf(message.content);
      if (message.isError === false && content.includes(expected)) {
        sawSuccessfulResult = true;
      }
      if (bwrapError.test(content)) sawBwrapError = true;
    }
  }
}

console.log(`bash tool call: ${sawBashCall ? "yes" : "no"}`);
console.log(`bash result ${expected}: ${sawSuccessfulResult ? "yes" : "no"}`);
console.log(`bwrap errors: ${sawBwrapError ? "yes" : "no"}`);
if (marker) console.log(`marker: ${sawMarker ? "yes" : "no"}`);
for (const [requiredMarkerName, present] of requiredMarkerHits) {
  console.log(`required marker ${requiredMarkerName}: ${present ? "yes" : "no"}`);
}
for (const [forbiddenMarkerName, present] of forbiddenMarkerHits) {
  console.log(`forbidden marker ${forbiddenMarkerName}: ${present ? "yes" : "no"}`);
}

if (
  !sawBashCall ||
  !sawSuccessfulResult ||
  sawBwrapError ||
  !sawMarker ||
  [...requiredMarkerHits.values()].some(present => !present) ||
  [...forbiddenMarkerHits.values()].some(present => present)
) {
  process.exit(1);
}
