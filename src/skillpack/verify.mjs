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

function subcommand(line) {
  return (String(line).match(/scripts\/client\.py\s+(\S+)/) || [])[1] || "";
}

function writesRecordedPath(line, clientText, requests) {
  const sub = subcommand(line);
  if (!sub) return false;
  for (const row of requests || []) {
    if (!/^(POST|PUT|PATCH|DELETE)$/i.test(row.method || "")) continue;
    const at = String(clientText || "").indexOf(row.path || "");
    if (at < 0) continue;
    const near = String(clientText).slice(Math.max(0, at - 1800), at + String(row.path || "").length);
    if (near.includes(sub) || near.includes(sub.replaceAll("-", "_"))) return true;
  }
  return false;
}

function commandWrites(line, clientText) {
  const sub = subcommand(line);
  if (!sub || sub === "show-config") return false;
  const needle = sub.replaceAll("-", "_");
  const at = clientText.indexOf(needle);
  const alt = clientText.indexOf(sub);
  const pos = at >= 0 ? at : alt;
  if (pos < 0) return false;
  const window = clientText.slice(pos, pos + 2500).split(/\ndef |\nasync def /)[0];
  return /method\s*=\s*["'](POST|PUT|PATCH|DELETE)["']/i.test(window);
}

function isTemplate(line) {
  return /[<>[\]]/.test(line);
}

function controlName(label) {
  const quoted = String(label).match(/"([^"]+)"/);
  let name = (quoted ? quoted[1] : String(label)).trim().replace(/^\*\s*/, "");
  name = name.replace(/^(?:请输入|请选择|请填写|请搜索)/, "").trim();
  return name;
}

function sameCommand(left, right) {
  const norm = (line) => String(line).trim().replace(/^python3\b/, "python");
  return norm(left) === norm(right);
}

function skillCoversPath(skillMd, clientText, optionPath) {
  if (skillMd.includes(optionPath)) return true;
  for (const line of readCommands(skillMd)) {
    const sub = subcommand(line).replaceAll("-", "_");
    if (!sub) continue;
    const at = clientText.indexOf(sub);
    if (at < 0) continue;
    const window = clientText.slice(at, at + 2500).split(/\ndef |\nasync def /)[0];
    if (window.includes(optionPath)) return true;
  }
  return false;
}

function readCommands(skillMd) {
  return skillMd.split(/\n/).map((line) => line.trim()).filter((line) => (
    /^python3?\s+scripts\/client\.py\b/.test(line) && !line.includes("--confirm")
  ));
}

function looksLikeOptions(text) {
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    return false;
  }
  const list = Array.isArray(data) ? data : (Array.isArray(data?.data) ? data.data : null);
  if (!Array.isArray(list) || list.length < 2) return false;
  const row = list.find((item) => item && typeof item === "object");
  if (!row) return false;
  const hasId = row.id != null || row.value != null;
  const hasLabel = row.name != null || row.label != null || row.title != null;
  return hasId && hasLabel;
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
  const requestsForWrites = [];
  for (const row of evidence.filter((item) => item.kind === "network")) {
    const blob = await getEvidence(recordingId, row.id).catch(() => null);
    const body = blob?.body && typeof blob.body === "object" ? blob.body : blob;
    if (body?.path) requestsForWrites.push({ method: body.method || "", path: body.path });
  }
  const clientText = texts["scripts/client.py"] || "";
  const mustRun = reads.filter((line) => !commandWrites(line, clientText) && !writesRecordedPath(line, clientText, requestsForWrites));
  const verifyRows = evidence.filter((item) => item.kind === "verify");
  for (const line of mustRun) {
    let ran = false;
    const runs = isTemplate(line)
      ? verifyRows.filter((item) => subcommand((item.argv || []).join(" ")) === subcommand(line))
      : verifyRows.filter((item) => sameCommand((item.argv || []).join(" "), line));
    for (const row of runs) {
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
        hint: isTemplate(line)
          ? "这一行是说明。用真实参数跑同名子命令即可，不要把尖括号原样当参数"
          : "run_skill_command 的 argv 用空格拼起来要等于这一行。没有鉴权而停止可以；其它失败要先改到能跑",
      });
    }
  }
  const requests = [];
  for (const row of evidence.filter((item) => item.kind === "network")) {
    const blob = await getEvidence(recordingId, row.id).catch(() => null);
    const body = blob?.body && typeof blob.body === "object" ? blob.body : blob;
    if (body?.path || body?.post_data) requests.push(requestIndexRow({ ...body, id: row.id }));
  }
  const adopted = `${texts["scripts/client.py"] || ""}\n${texts["references/api.md"] || ""}`;
  const seenOptions = new Set();
  for (const row of evidence.filter((item) => item.kind === "network")) {
    const blob = await getEvidence(recordingId, row.id).catch(() => null);
    const body = blob?.body && typeof blob.body === "object" ? blob.body : blob;
    const text = String(body?.response_body || "");
    if (!looksLikeOptions(text)) continue;
    const optionPath = String(body?.path || "");
    if (!optionPath || seenOptions.has(optionPath) || !adopted.includes(optionPath) || skillCoversPath(skillMd, texts["scripts/client.py"] || "", optionPath)) continue;
    seenOptions.add(optionPath);
    errors.push({
      code: "live_options_missing",
      path: optionPath,
      file: "SKILL.md",
      hint: "脚本或 api.md 引用了这条候选项路径。提问前用打这条路径的命令现查，用 select 或 tree 显示名称，不要把录到的 id 做成文本框",
    });
  }
  errors.push(...citationErrors(texts["scripts/client.py"] || "", requests, "scripts/client.py"));
  errors.push(...citationErrors(texts["references/api.md"] || "", requests, "references/api.md"));
  const actionRows = evidence.filter((row) => row.kind === "action");
  for (const row of actionRows) {
    const summary = String(row.summary || "");
    const named = summary.match(/^(?:fill|fill_fields|select|upload):(.*)$/);
    if (!named) continue;
    for (const field of named[1].split("|").map((item) => controlName(item)).filter((item) => item.length >= 2)) {
      if (!skillMd.includes(field)) {
        errors.push({
          code: "caller_field_missing",
          field,
          file: "SKILL.md",
          hint: "这个控件在页面上填过，要在 SKILL.md 里作为调用方必答问题出现，不能只问其中一部分",
        });
      }
    }
  }
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
