import { readFile } from "node:fs/promises";
import path from "node:path";
import { skillDir } from "../paths.mjs";
import { listEvidence } from "../evidence/store.mjs";
import { handbookChanged, writeRuntimeConfig } from "./files.mjs";
import { hasCredentialHeaders } from "../auth-vault.mjs";
import { upsertCatalog } from "./catalog.mjs";

const SOURCES = new Set(["caller", "current_user", "now", "previous_response", "other_api", "constant", "unknown"]);
const REQUIRED = new Set(["page", "caller_all", "server_verified", "server_unknown"]);

function section(text, title) {
  const match = text.match(new RegExp(`## ${title}\\n([\\s\\S]*?)(?=\\n## |$)`));
  return match ? match[1] : "";
}

function blocks(body, startKey) {
  const rows = [];
  let current = null;
  for (const line of body.split(/\n/)) {
    const item = line.match(/^- ([a-z_]+):\s*(.*)$/);
    if (!item) continue;
    if (item[1] === startKey) {
      current = {};
      rows.push(current);
    }
    if (!current) continue;
    current[item[1]] = item[2].trim();
  }
  return rows;
}

function readCommands(skillMd) {
  return skillMd.split(/\n/).map((line) => line.trim()).filter((line) => (
    /^python3?\s+scripts\/client\.py\b/.test(line) && !line.includes("--confirm")
  ));
}

function idsOf(value) {
  return String(value || "").split(/[\s,]+/).map((item) => item.trim()).filter(Boolean);
}

async function requestStrings(recordingId) {
  const rows = await listEvidence(recordingId, { kinds: ["network"], limit: 0 });
  const values = [];
  for (const row of rows) {
    try {
      const full = JSON.parse(await readFile(path.join(
        (await import("../paths.mjs")).recordingDir(recordingId),
        "blobs",
        `${row.id}.json`,
      ), "utf8"));
      const body = full.body?.post_data || full.body?.response_body || full.post_data || "";
      collectStrings(body, values);
    } catch {
      // 没有正文就没有可比的字面量
    }
  }
  return values;
}

function collectStrings(value, out) {
  if (typeof value === "string") {
    const trimmed = value.trim();
    if ((trimmed.startsWith("{") || trimmed.startsWith("["))) {
      try {
        collectStrings(JSON.parse(trimmed), out);
        return;
      } catch {
        // 不是 JSON
      }
    }
    if (trimmed.length >= 8) out.push(trimmed);
    return;
  }
  if (Array.isArray(value)) {
    for (const item of value) collectStrings(item, out);
    return;
  }
  if (value && typeof value === "object") {
    for (const [key, item] of Object.entries(value)) {
      if (typeof item === "string" && item.length >= 8) out.push({ key, value: item });
      else collectStrings(item, out);
    }
  }
}

function pythonDefaults(source) {
  const found = [];
  const re = /(\w+)\s*=\s*"([^"]+)"|'([^']+)'/g;
  const lineRe = /(\w+)\s*=\s*(?:"([^"]*)"|'([^']*)')/g;
  for (const line of source.split(/\n/)) {
    if (!line.includes("def ") && !line.includes("=")) continue;
    lineRe.lastIndex = 0;
    let match;
    const scoped = line.includes("def ") ? line : "";
    const text = scoped || "";
    while ((match = lineRe.exec(text))) {
      const value = match[2] ?? match[3] ?? "";
      if (value.length >= 8) found.push({ name: match[1], value });
    }
  }
  return found;
}

export async function verifySkill(skillDirPath, recordingId, recording = {}) {
  const dir = skillDirPath || skillDir(recording.skillId || "");
  const errors = [];
  const files = ["SKILL.md", "scripts/client.py", "references/api.md", "config/runtime.json", "config/auth.local.json"];
  const texts = {};
  for (const rel of files) {
    try {
      texts[rel] = await readFile(path.join(dir, rel), "utf8");
    } catch {
      errors.push({ code: "missing_file", path: rel });
      texts[rel] = "";
    }
  }
  if (await handbookChanged(recordingId, dir)) errors.push({ code: "handbook_rewritten" });
  const secret = /Bearer\s+[A-Za-z0-9._\-]+|password\s*[:=]\s*\S+|cookie\s*[:=]\s*\S{8,}/i;
  for (const rel of ["SKILL.md", "scripts/client.py", "references/api.md"]) {
    if (secret.test(texts[rel] || "")) errors.push({ code: "secret_in_source", path: rel });
  }
  const api = texts["references/api.md"] || "";
  const fields = blocks(section(api, "字段"), "page_name");
  const bindings = blocks(section(api, "绑定"), "from");
  const unresolved = blocks(section(api, "未解决"), "field");
  const verifiedLines = section(api, "已验证读命令").split(/\n/).map((line) => line.trim()).filter((line) => line.startsWith("python"));
  const evidence = await listEvidence(recordingId, { limit: 0 });
  const known = new Set(evidence.map((row) => row.id));
  for (const group of [...fields, ...bindings, ...unresolved]) {
    for (const id of idsOf(group.evidence_ids)) {
      if (!known.has(id)) errors.push({ code: "evidence_missing", id });
    }
  }
  for (const field of fields) {
    if (field.source && !SOURCES.has(field.source)) continue;
    if (field.required_kind && !REQUIRED.has(field.required_kind)) continue;
  }
  const reads = readCommands(texts["SKILL.md"] || "");
  const verifies = evidence.filter((row) => row.kind === "verify" && row.ok === true);
  const failed = evidence.filter((row) => row.kind === "verify" && row.ok === false);
  for (const line of reads) {
    const ran = verifies.some((row) => (row.argv || []).join(" ") === line);
    const listed = verifiedLines.some((item) => item === line);
    if (!ran || !listed) errors.push({ code: "command_not_run", command: line });
  }
  for (const row of failed) {
    const blob = await import("../evidence/store.mjs").then((mod) => mod.getEvidence(recordingId, row.id)).catch(() => null);
    const text = `${blob?.stdout || ""} ${blob?.stderr || ""}`;
    if (/\b401\b/.test(text)) errors.push({ code: "auth_expired" });
  }
  const skillText = texts["SKILL.md"] || "";
  for (const item of unresolved) {
    const field = item.field || "";
    if (field && new RegExp(`\\b${field.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`).test(skillText)) {
      errors.push({ code: "unknown_in_command", field });
    }
  }
  const literals = await requestStrings(recordingId);
  for (const item of pythonDefaults(texts["scripts/client.py"] || "")) {
    const hit = literals.find((entry) => entry.value === item.value || entry === item.value);
    const key = hit?.key || item.name;
    const declared = fields.some((field) => (
      field.source === "constant"
      && field.constant_reason
      && field.constant_reason !== "录到的就是这个"
      && (field.caller_name === key || field.page_name === item.name)
    ));
    if (hit && !declared) errors.push({ code: "recorded_literal_default", name: item.name });
  }
  let headers = {};
  try {
    headers = JSON.parse(texts["config/auth.local.json"] || "{}").headers || {};
  } catch {
    headers = {};
  }
  let status = "verify_failed";
  if (!errors.length && hasCredentialHeaders(headers)) status = "skill_ready";
  else if (!errors.length) status = "skill_written_needs_auth";
  if (errors.some((item) => item.code === "auth_expired")) status = "verify_failed";
  const verify = { ok: status !== "verify_failed", status, errors };
  if (status === "skill_ready" || status === "skill_written_needs_auth") {
    await upsertCatalog({ ...recording, id: recordingId, skillId: recording.skillId, status, skillDir: dir });
  }
  return verify;
}

export async function finishSkill(recording) {
  const dir = skillDir(recording.skillId);
  await writeRuntimeConfig(recording, recording.skillId);
  return verifySkill(dir, recording.id, recording);
}
