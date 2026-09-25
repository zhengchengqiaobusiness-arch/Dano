import { readFile } from "node:fs/promises";
import path from "node:path";
import { skillDir } from "../paths.mjs";
import { listEvidence, getEvidence } from "../evidence/store.mjs";
import { handbookChanged, writeRuntimeConfig } from "./files.mjs";
import { hasCredentialHeaders } from "../auth-vault.mjs";
import { upsertCatalog } from "./catalog.mjs";
import { requestIndexRow, citationErrors, citedPaths } from "./request-keys.mjs";
import { GUIDE_NAMES } from "../agent/guides.mjs";

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
  const secret = /Bearer\s+[A-Za-z0-9._\-]{8,}|password\s*[:=]\s*\S+|cookie\s*[:=]\s*\S{8,}/i;
  for (const rel of ["SKILL.md", "scripts/client.py", "references/api.md"]) {
    if (secret.test(texts[rel] || "")) errors.push({ code: "secret_in_source", path: rel });
  }
  const api = texts["references/api.md"] || "";
  const fields = blocks(section(api, "字段"), "page_name");
  const bindings = blocks(section(api, "绑定"), "from");
  const unresolved = blocks(section(api, "未解决"), "field");
  const evidence = await listEvidence(recordingId, { limit: 0 });
  const known = new Set(evidence.map((row) => row.id));
  for (const group of [...fields, ...bindings, ...unresolved]) {
    for (const id of idsOf(group.evidence_ids)) {
      if (!known.has(id)) errors.push({ code: "evidence_missing", id });
    }
  }
  const readGuides = new Set(evidence.filter((row) => row.kind === "guide").map((row) => row.summary));
  for (const name of GUIDE_NAMES) {
    if (!readGuides.has(name)) errors.push({ code: "guide_not_read", name });
  }
  const skillMd = texts["SKILL.md"] || "";
  if (skillMd && !/^---\n[\s\S]*?\bname:\s*\S+/m.test(skillMd)) {
    errors.push({ code: "not_invocable", file: "SKILL.md", hint: "开头写 name 和 description，调用方靠 description 启用" });
  }
  if (/disable-model-invocation:\s*true/.test(skillMd)) {
    errors.push({ code: "not_invocable", file: "SKILL.md", hint: "不要写 disable-model-invocation，否则调用方的智能体不会启用" });
  }
  const reads = readCommands(skillMd);
  const businessReads = reads.filter((line) => !/^python3?\s+scripts\/client\.py\s+show-config\s*$/.test(line));
  if (citedPaths(texts["scripts/client.py"] || "").length && !businessReads.length) {
    errors.push({
      code: "command_not_run",
      file: "SKILL.md",
      hint: "show-config 只检查配置。再写一条打到证据路径的读命令，并用相同 argv 跑 run_skill_command",
    });
  }
  if (!reads.length && skillMd) {
    errors.push({
      code: "command_not_run",
      file: "SKILL.md",
      hint: "在 SKILL.md 写一行以 python scripts/client.py 开头的读命令，再用相同 argv 调用 run_skill_command",
    });
  }
  const failed = evidence.filter((row) => row.kind === "verify" && row.ok === false);
  for (const line of reads) {
    let ran = false;
    for (const row of evidence.filter((item) => item.kind === "verify" && (item.argv || []).join(" ") === line)) {
      if (row.ok === true) {
        ran = true;
        break;
      }
      const blob = await getEvidence(recordingId, row.id).catch(() => null);
      const output = `${blob?.stdout || ""}\n${blob?.stderr || ""}`;
      if (/AuthExpired|缺少鉴权|没有鉴权|请提供 token|账号未登录|\b401\b/.test(output)) {
        ran = true;
        break;
      }
    }
    if (!ran) {
      errors.push({
        code: "command_not_run",
        command: line,
        file: "SKILL.md",
        hint: "run_skill_command 的 argv 用空格拼起来要等于这一行。没有鉴权而停止可以；其它失败要先改到能跑",
      });
    }
  }
  const requests = [];
  for (const row of evidence.filter((item) => item.kind === "network")) {
    const blob = await getEvidence(recordingId, row.id).catch(() => null);
    const body = blob?.body && typeof blob.body === "object" ? blob.body : blob;
    if (body?.path || body?.post_data) requests.push(requestIndexRow({ ...body, id: row.id }));
  }
  errors.push(...citationErrors(texts["scripts/client.py"] || "", requests, "scripts/client.py"));
  errors.push(...citationErrors(texts["references/api.md"] || "", requests, "references/api.md"));
  for (const row of failed) {
    const blob = await getEvidence(recordingId, row.id).catch(() => null);
    const text = `${blob?.stdout || ""} ${blob?.stderr || ""}`;
    if (/\b401\b/.test(text)) errors.push({ code: "auth_expired" });
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
