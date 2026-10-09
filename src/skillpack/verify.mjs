import { readFile } from "node:fs/promises";
import path from "node:path";
import { skillDir } from "../paths.mjs";
import { listEvidence, getEvidence } from "../evidence/store.mjs";
import { handbookChanged, writeRuntimeConfig } from "./files.mjs";
import { hasCredentialHeaders } from "../auth-vault.mjs";
import { upsertCatalog } from "./catalog.mjs";
import { requestIndexRow, citationErrors, citedPaths, windowsForPath } from "./request-keys.mjs";
import { nonStdlibImports } from "./stdlib-imports.mjs";
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

function subcommand(line) {
  return (String(line).match(/scripts\/client\.py\s+(\S+)/) || [])[1] || "";
}

function writesRecordedPath(line, clientText, requests) {
  const sub = subcommand(line);
  if (!sub) return false;
  const token = sub.replaceAll("-", "_");
  const impl = new RegExp(`(?:^|\\n)(?:async )?def\\s+(?:cmd_)?${token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`);
  for (const row of requests || []) {
    if (!/^(POST|PUT|PATCH|DELETE)$/i.test(row.method || "")) continue;
    if (!String(clientText || "").includes(row.path || "")) continue;
    if (windowsForPath(clientText, row.path).some((block) => impl.test(block))) return true;
  }
  return false;
}

function commandWrites(line, clientText) {
  const sub = subcommand(line);
  if (!sub || sub === "show-config") return false;
  const token = sub.replaceAll("-", "_");
  const named = new RegExp(`(?:^|\\n)(?:async )?def\\s+(?:cmd_)?${token}\\b`);
  const blocks = String(clientText || "").split(/(?=\n(?:async )?def )/);
  const hit = blocks.find((block) => named.test(block) || new RegExp(`add_parser\\(["']${sub.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}["']\\)`).test(block));
  if (!hit) return false;
  return /(?:method\s*=\s*["'](POST|PUT|PATCH|DELETE)["']|\b(?:POST|PUT|PATCH|DELETE)\b.*["']\/)/i.test(hit);
}

function isTemplate(line) {
  return /[<>[\]]/.test(line);
}

export function actionEvidenceSummary(action, result) {
  if (result?.error === "needs_upload") return "needs_upload";
  if (result?.uploaded) return `upload:${result.clicked || "file"}`;
  const names = Array.isArray(result?.filled) ? result.filled.filter(Boolean) : [];
  if (names.length) return `${action}:${names.join("|")}`;
  if (action === "click" && result?.clicked) return `click:${result.clicked}`;
  if (result?.actor === "human") return "manual_click";
  return action;
}

export function filledLabels(evidence) {
  const labels = [];
  for (const row of evidence || []) {
    if (row.kind !== "action") continue;
    const named = String(row.summary || "").match(/^(?:fill|fill_fields|select|upload):(.*)$/);
    if (!named) continue;
    for (const label of named[1].split("|")) {
      if (label) labels.push(label);
    }
  }
  return labels;
}

export function clickedLabels(evidence) {
  const labels = [];
  for (const row of evidence || []) {
    if (row.kind !== "action") continue;
    const summary = String(row.summary || "");
    if (summary.startsWith("click:") && summary.length > 6) labels.push(summary.slice(6));
    else if (summary === "manual_click" || row.body?.actor === "human") labels.push("actor=human");
  }
  return labels;
}

export function writeOnlySkill(skillMd, clientText, requests) {
  const lines = String(skillMd || "").split(/\n/).map((line) => line.trim()).filter((line) => (
    /^python3?\s+scripts\/client\.py\b/.test(line)
  ));
  if (!lines.length) return false;
  const recordedWrite = (requests || []).some((row) => /^(POST|PUT|PATCH|DELETE)$/i.test(row.method || ""));
  if (!recordedWrite) return false;
  const reads = lines.filter((line) => (
    !line.includes("--confirm")
    && !commandWrites(line, clientText)
    && !writesRecordedPath(line, clientText, requests)
  ));
  const writes = lines.filter((line) => (
    line.includes("--confirm")
    || commandWrites(line, clientText)
    || writesRecordedPath(line, clientText, requests)
  ));
  return writes.length > 0 && reads.length === 0;
}

function sameCommand(left, right) {
  const norm = (line) => String(line).trim().replace(/\r/g, "").replace(/^python3\b/, "python");
  return norm(left) === norm(right);
}

export function authStoppedOutput(output) {
  const text = String(output || "");
  if (/AuthExpired|缺少鉴权|没有鉴权|请提供 token|账号未登录|\b401\b/.test(text)) return true;
  return /\b400\b/.test(text) && /refresh|token|令牌|credential/i.test(text);
}

export function scriptCrashOutput(output) {
  const text = String(output || "");
  if (authStoppedOutput(text)) return false;
  return /Traceback \(most recent call last\)/.test(text);
}

function latestMatchingRun(verifyRows, line) {
  const runs = isTemplate(line)
    ? (verifyRows || []).filter((item) => subcommand((item.argv || []).join(" ")) === subcommand(line))
    : (verifyRows || []).filter((item) => sameCommand((item.argv || []).join(" "), line));
  return runs.at(-1) || null;
}

function latestVerifyByCommand(verifyRows) {
  const map = new Map();
  for (const row of verifyRows || []) {
    const key = subcommand((row.argv || []).join(" ")) || (row.argv || []).join(" ");
    map.set(key, row);
  }
  return [...map.values()];
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
    errors.push({ code: "not_invocable", file: "SKILL.md", hint: "SKILL.md 必须以 --- 开头的 YAML 包住 name 和 description，再写正文。调用方靠这段启用，不要写成页面点击步骤" });
  }
  if (/disable-model-invocation:\s*true/.test(skillMd)) {
    errors.push({ code: "not_invocable", file: "SKILL.md", hint: "不要写 disable-model-invocation，否则调用方的智能体不会启用" });
  }
  const reads = readCommands(skillMd);
  const requests = [];
  const requestsForWrites = [];
  for (const row of evidence.filter((item) => item.kind === "network")) {
    const blob = await getEvidence(recordingId, row.id).catch(() => null);
    const body = blob?.body && typeof blob.body === "object" ? blob.body : blob;
    if (!body) continue;
    if (body.path) requestsForWrites.push({ method: body.method || "", path: body.path });
    if (body.path || body.post_data) requests.push(requestIndexRow({ ...body, id: row.id }));
  }
  const clientText = texts["scripts/client.py"] || "";
  const writeOnly = writeOnlySkill(skillMd, clientText, requestsForWrites);
  const businessReads = reads.filter((line) => !/^python3?\s+scripts\/client\.py\s+show-config\s*$/.test(line));
  if (!writeOnly && citedPaths(clientText).length && !businessReads.length) {
    errors.push({
      code: "command_not_run",
      file: "SKILL.md",
      hint: "show-config 只检查配置。目标含读时再写一条打到证据路径的读命令，并用相同 argv 跑 run_skill_command。目标只要求写入时不要发明 GET。",
    });
  }
  if (!writeOnly && !reads.length && skillMd) {
    errors.push({
      code: "command_not_run",
      file: "SKILL.md",
      hint: "目标含读时，在 SKILL.md 写一行以 python scripts/client.py 开头的读命令，再用相同 argv 调用 run_skill_command。目标只要求写入时，写带 --confirm 的写命令即可，不要发明 GET。",
    });
  }
  if (clientText.trim()) {
    try {
      const probed = await nonStdlibImports(clientText);
      if (probed.syntax) {
        errors.push({
          code: "not_invocable",
          file: "scripts/client.py",
          hint: "scripts/client.py 无法被 Python 解析。调用方会直接执行这个文件。",
        });
      }
      for (const name of probed.imports || []) {
        errors.push({
          code: "import_not_available",
          name,
          file: "scripts/client.py",
          hint: "调用这份 Skill 的环境只有 Python 标准库。",
        });
      }
    } catch (error) {
      errors.push({
        code: "not_invocable",
        file: "scripts/client.py",
        hint: `没能检查脚本能否在只有标准库的环境里启动：${error.message}`,
      });
    }
  }
  const mustRun = reads.filter((line) => !commandWrites(line, clientText) && !writesRecordedPath(line, clientText, requestsForWrites));
  const verifyRows = evidence.filter((item) => item.kind === "verify");
  for (const line of mustRun) {
    const row = latestMatchingRun(verifyRows, line);
    if (!row) {
      errors.push({
        code: "command_not_run",
        command: line,
        file: "SKILL.md",
        hint: isTemplate(line)
          ? "这一行是说明。用真实参数跑同名子命令即可，不要把尖括号原样当参数"
          : "run_skill_command 的 argv 用空格拼起来要等于这一行。没有鉴权而停止可以；其它失败要先改到能跑",
      });
      continue;
    }
    if (row.ok === true) continue;
    const blob = await getEvidence(recordingId, row.id).catch(() => null);
    const output = `${blob?.stdout || ""}\n${blob?.stderr || ""}`;
    if (authStoppedOutput(output)) continue;
    const crashed = scriptCrashOutput(output);
    errors.push({
      code: crashed ? "not_invocable" : "command_not_run",
      command: line,
      file: crashed ? "scripts/client.py" : "SKILL.md",
      hint: crashed
        ? "最新一次 run_skill_command 在发请求前崩溃。先改到能启动再跑。"
        : (isTemplate(line)
          ? "这一行是说明。用真实参数跑同名子命令即可，不要把尖括号原样当参数"
          : "run_skill_command 的 argv 用空格拼起来要等于这一行。没有鉴权而停止可以；其它失败要先改到能跑"),
    });
  }
  if (requests.some((item) => item.issues_credential) && !clientText.includes("credential")) {
    errors.push({
      code: "credential_not_used",
      file: "scripts/client.py",
      hint: "证据里有签发访问凭证的请求。业务请求前按 config/auth.local.json 的 credential 重放，失败或仍是 401 再停止",
    });
  }
  errors.push(...citationErrors(clientText, requests, "scripts/client.py"));
  errors.push(...citationErrors(texts["references/api.md"] || "", requests, "references/api.md", { requireKeys: false }));
  const actionRows = evidence.filter((row) => row.kind === "action");
  const uploads = actionRows.filter((row) => String(row.summary || "").startsWith("upload:"));
  if (actionRows.some((row) => row.summary === "needs_upload") && !uploads.length) {
    errors.push({ code: "attachment_unresolved", hint: "文件选择已打开。对同一 ref 调用 upload，并采用这次发出的请求" });
  }
  for (const row of uploads) {
    const blob = await getEvidence(recordingId, row.id).catch(() => null);
    const fired = blob?.body?.requests;
    if (!Array.isArray(fired) || !fired.length) {
      errors.push({ code: "attachment_unresolved", hint: "upload 没有发出请求。附件要跟着这次请求写进提交正文，不能省略" });
    }
  }
  for (const row of latestVerifyByCommand(verifyRows)) {
    if (row.ok === true) continue;
    const blob = await getEvidence(recordingId, row.id).catch(() => null);
    const text = `${blob?.stdout || ""} ${blob?.stderr || ""}`;
    if (authStoppedOutput(text)) errors.push({ code: "auth_expired" });
  }
  let headers = {};
  try {
    headers = JSON.parse(texts["config/auth.local.json"] || "{}").headers || {};
  } catch {
    headers = {};
  }
  const unique = [];
  const seenError = new Set();
  for (const item of errors) {
    const key = [item.code, item.field, item.path, item.command, item.name, item.key, item.file].join("|");
    if (seenError.has(key)) continue;
    seenError.add(key);
    unique.push(item);
  }
  errors.length = 0;
  errors.push(...unique);
  const authOnly = errors.length > 0 && errors.every((item) => item.code === "auth_expired");
  let status = "verify_failed";
  if (!errors.length && hasCredentialHeaders(headers)) status = "skill_ready";
  else if (!errors.length || authOnly) status = "skill_written_needs_auth";
  const verify = { ok: status !== "verify_failed", status, errors, requests, filled: filledLabels(evidence) };
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
