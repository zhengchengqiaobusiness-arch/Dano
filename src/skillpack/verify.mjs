import { readFile, stat } from "node:fs/promises";
import path from "node:path";
import { skillDir } from "../paths.mjs";
import { listEvidence, getEvidence } from "../evidence/store.mjs";
import { handbookChanged, writeRuntimeConfig } from "./files.mjs";
import { credentialShape, hasCredentialHeaders } from "../auth-vault.mjs";
import { upsertCatalog } from "./catalog.mjs";
import { adoptedEvidencePaths, authHeaderOverlay, changedRequestKeys, citationErrors, citedPaths, executableSource, previousMatchingRequest, readsIssuedCredential, rebuildsCredentialPath, rebuildsCredentialQuery, requestIndexRow, windowsForPath, writerRequestRows } from "./request-keys.mjs";
import { fieldContractErrors, observeRequest, parseApiContract } from "./observations.mjs";
import { nonStdlibImports } from "./stdlib-imports.mjs";
import { guidesFor } from "../agent/guides.mjs";

export function commandAuthFailed(text) {
  const value = String(text || "");
  if (/AuthExpired|缺少鉴权|没有鉴权|请提供 token|账号未登录/.test(value)) return true;
  return /"code"\s*:\s*401\b/.test(value) || /"status"\s*:\s*401\b/.test(value) || /\b401\s+Unauthorized\b/.test(value);
}

export function authBlocksRun(blockedStamp, currentStamp, scriptStamp, blockedScriptStamp) {
  if (!blockedStamp || String(blockedStamp) !== String(currentStamp || "")) return false;
  if (scriptStamp && blockedScriptStamp && String(scriptStamp) !== String(blockedScriptStamp)) return false;
  return true;
}

function verifyOutput(blob) {
  const body = typeof blob?.body === "string" ? blob.body : "";
  return `${blob?.stdout || ""}\n${blob?.stderr || ""}\n${body}`;
}

function subcommand(line) {
  return (String(line).match(/scripts\/client\.py\s+(\S+)/) || [])[1] || "";
}

function listed(list, pick) {
  return [...new Set(list.map(pick).filter(Boolean))];
}

export function collapseVerifyErrors(errors) {
  const kept = [];
  const grouped = {
    evidence_not_opened: [],
    field_unaccounted: [],
    filled_not_written: [],
    option_unclassified: [],
    url_unclassified: [],
    source_unlinked: [],
    key_ambiguous: [],
    binding_unaccounted: [],
    caller_not_in_handbook: [],
  };
  const commands = new Map();
  const writes = new Map();
  for (const item of errors) {
    if (item.code === "key_not_written") {
      const id = `${item.file || ""}\n${item.path || ""}`;
      const list = writes.get(id) || [];
      list.push(item);
      writes.set(id, list);
      continue;
    }
    if (item.code === "command_not_run" && item.command) {
      const sub = subcommand(item.command);
      if (!commands.has(sub)) commands.set(sub, item);
      continue;
    }
    if (grouped[item.code]) {
      grouped[item.code].push(item);
      continue;
    }
    kept.push(item);
  }
  const oneOrMany = (list, build) => {
    if (list.length === 1) kept.push(list[0]);
    else if (list.length) kept.push(build(list));
  };
  oneOrMany(grouped.evidence_not_opened, (list) => ({
    code: "evidence_not_opened",
    ids: listed(list, (item) => item.id),
    file: "references/api.md",
    hint: "这些 id 写进了手册但全文还没打开。一次用 network_get 打开它们，或从手册删掉没采用的 id。对照填写值、changed_keys 和正文写来源。没分清就放未解决。",
  }));
  oneOrMany(grouped.field_unaccounted, (list) => ({
    code: "field_unaccounted",
    keys: listed(list, (item) => item.key),
    paths: listed(list, (item) => item.path),
    file: "references/api.md",
    hint: "这些键还没写完整。用 `- page_name:` 起一条，或用槽名做表头。每条要有 page_name、caller_name、request_path、request_key、source、required_kind、evidence_ids。constant 要写 constant_reason。unknown 放未解决。source 只能是 caller、current_user、now、previous_response、other_api、constant、unknown。required_kind 只能是 page、caller_all、server_verified、server_unknown。",
  }));
  oneOrMany(grouped.filled_not_written, (list) => ({
    code: "filled_not_written",
    field: list.map((item) => item.field).filter(Boolean).join("、"),
    file: "references/api.md",
    hint: list[0].hint,
  }));
  oneOrMany(grouped.option_unclassified, (list) => ({
    code: "option_unclassified",
    paths: listed(list, (item) => item.path),
    file: "references/api.md",
    hint: list[0].hint,
  }));
  oneOrMany(grouped.url_unclassified, (list) => ({
    code: "url_unclassified",
    paths: listed(list, (item) => item.path),
    file: "references/api.md",
    hint: list[0].hint,
  }));
  oneOrMany(grouped.source_unlinked, (list) => ({
    code: "source_unlinked",
    keys: listed(list, (item) => item.key),
    file: "references/api.md",
    hint: list[0].hint,
  }));
  oneOrMany(grouped.key_ambiguous, (list) => ({
    code: "key_ambiguous",
    keys: listed(list, (item) => item.key),
    file: "references/api.md",
    hint: list[0].hint,
  }));
  oneOrMany(grouped.binding_unaccounted, (list) => ({
    code: "binding_unaccounted",
    count: list.length,
    file: "references/api.md",
    hint: list[0].hint,
  }));
  oneOrMany(grouped.caller_not_in_handbook, (list) => ({
    code: "caller_not_in_handbook",
    caller_names: listed(list, (item) => item.caller_name),
    file: "SKILL.md",
    hint: list[0].hint,
  }));
  for (const item of commands.values()) kept.push(item);
  for (const list of writes.values()) {
    if (list.length === 1) {
      kept.push(list[0]);
      continue;
    }
    kept.push({
      code: "key_not_written",
      path: list[0].path,
      keys: listed(list, (item) => item.key),
      file: list[0].file,
      hint: list[0].hint,
    });
  }
  return kept;
}

function hasWord(text, token) {
  const escaped = String(token || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  if (!escaped) return false;
  return new RegExp(`(?:^|[^A-Za-z0-9_])${escaped}(?:$|[^A-Za-z0-9_])`).test(String(text || ""));
}

function writesRecordedPath(line, clientText, requests) {
  if (configOnly(line)) return false;
  const sub = subcommand(line);
  if (!sub) return false;
  const tokens = [sub, sub.replaceAll("-", "_")];
  const source = String(clientText || "");
  for (const row of requests || []) {
    if (!/^(POST|PUT|PATCH|DELETE)$/i.test(row.method || "")) continue;
    if (!row.path || !source.includes(row.path)) continue;
    if (windowsForPath(source, row.path).some((window) => tokens.some((token) => hasWord(window, token)))) return true;
  }
  return false;
}

function commandToken(line) {
  return subcommand(line).replace(/^-+/, "");
}

function configOnly(line) {
  return commandToken(line) === "show-config";
}

function leadingSpaces(line) {
  return (line.match(/^ */)?.[0] || "").length;
}

const WRITE_CALL = /(?:method\s*=\s*|\(\s*|api_request\(\s*|_request\(\s*)["'](?:POST|PUT|PATCH|DELETE)["']/i;

function functionBodies(source) {
  const lines = String(source || "").split(/\n/);
  const out = [];
  for (let index = 0; index < lines.length; index += 1) {
    const head = lines[index].match(/^(\s*)def ([A-Za-z_]\w*)\s*\(/);
    if (!head) continue;
    const indent = head[1].length;
    const body = [];
    for (let next = index + 1; next < lines.length; next += 1) {
      if (lines[next].trim() && leadingSpaces(lines[next]) <= indent) break;
      body.push(lines[next]);
    }
    out.push({ name: head[2], indent, body: body.join("\n"), lines: body });
  }
  return out;
}

export function writeOnDefault(source) {
  const code = executableSource(source);
  const functions = functionBodies(code);
  const writers = new Set(functions.filter((item) => WRITE_CALL.test(item.body)).map((item) => item.name));
  const main = [...functions].reverse().find((item) => item.name === "main" && item.indent === 0);
  if (!main) return false;
  const argvNames = new Set();
  for (const match of main.body.matchAll(/^\s*([A-Za-z_]\w*)\s*=\s*(?:sys\.argv\b|\w+\.parse_args\s*\()/gm)) argvNames.add(match[1]);
  const stack = [];
  for (const line of main.lines) {
    if (!line.trim()) continue;
    const indent = leadingSpaces(line);
    while (stack.length && stack[stack.length - 1].indent >= indent) stack.pop();
    const branch = line.match(/^\s*(?:if|elif)\b(.*):\s*(.*)$/);
    let protects = false;
    if (branch) {
      const condition = branch[1];
      const mentionsArgv = condition.includes("sys.argv") || [...argvNames].some((name) => new RegExp(`(?:^|[^A-Za-z0-9_])${name}(?:$|[^A-Za-z0-9_])`).test(condition));
      const compared = [...condition.matchAll(/["']([^"']+)["']/g)].map((item) => item[1].replace(/^-+/, ""));
      protects = mentionsArgv && compared.some((token) => token && token !== "show-config");
    }
    const protectedWrite = protects || stack.some((item) => item.protects);
    const callsWriter = [...writers].some((name) => new RegExp(`(?:^|[^A-Za-z0-9_])${name}\\s*\\(`).test(line));
    if (!protectedWrite && (callsWriter || WRITE_CALL.test(line))) return true;
    if (branch) stack.push({ indent, protects });
    else if (/^\s*(?:try|for|while|with|else)\b/.test(line)) stack.push({ indent, protects: false });
  }
  return false;
}

function commandWrites(line, clientText) {
  const sub = subcommand(line);
  if (!sub || configOnly(line)) return false;
  const needle = sub.replaceAll("-", "_");
  const at = clientText.indexOf(needle);
  const alt = clientText.indexOf(sub);
  const pos = at >= 0 ? at : alt;
  if (pos < 0) return false;
  const window = clientText.slice(pos, pos + 2500).split(/\ndef |\nasync def /)[0];
  return /(?:method\s*=\s*|\(\s*)["'](?:POST|PUT|PATCH|DELETE)["']/i.test(window);
}

export function sourceHasSecret(text) {
  return /Bearer\s+[A-Za-z0-9._\-]{8,}|password\s*[:=]\s*\S+|cookie\s*[:=]\s*\S{8,}|(?:refresh[_-]?token|access[_-]?token)\s*[:=]\s*(?:"[A-Za-z0-9._\-]{8,}"|'[A-Za-z0-9._\-]{8,}'|[A-Za-z0-9._\-]{20,})/i.test(String(text || ""));
}

function actionPlace(result) {
  return [result?.popup ? `popup="${result.popup}"` : "", result?.row ? `row="${result.row}"` : ""].filter(Boolean).join(" ");
}

export function actionEvidenceSummary(action, result) {
  if (result?.error === "needs_upload") return "needs_upload";
  const place = actionPlace(result);
  if (result?.uploaded) return `upload:${result.clicked || "file"}${place ? ` ${place}` : ""}`;
  const names = Array.isArray(result?.filled) ? result.filled.filter(Boolean) : [];
  if (names.length) return `${action}:${names.join("|")}`;
  if (result?.clicked) return `${action}:${result.clicked}${place ? ` ${place}` : ""}`;
  return action;
}

function namesWithoutButton(name, extras) {
  const list = [...new Set((extras || []).map((item) => String(item || "").trim()).filter((item) => item.length >= 6))]
    .sort((left, right) => right.length - left.length);
  const found = [];
  let text = String(name || "").trim();
  for (const extra of list) {
    const suffix = ` ${extra}`;
    if (!text.endsWith(suffix) || text.length <= suffix.length) continue;
    text = text.slice(0, -suffix.length).trim();
    const rest = text.replace(/^\*\s*/, "");
    if (rest) found.push(rest);
  }
  return found;
}

export function labelTexts(label, extras = []) {
  const bare = String(label).replace(/\s(?:popup|row)="[^"]*"/g, "");
  const quoted = [...bare.matchAll(/"([^"]+)"/g)].map((item) => item[1]);
  const raw = quoted.length ? quoted : [String(label || "")];
  const names = [];
  const add = (name) => {
    const text = String(name || "").trim();
    if (!text || names.includes(text)) return;
    names.push(text);
  };
  for (const name of raw) {
    add(name);
    const starless = name.replace(/^\*\s*/, "");
    if (starless !== name) add(starless);
    for (const rest of namesWithoutButton(name, extras)) add(rest);
    if (starless !== name) for (const rest of namesWithoutButton(starless, extras)) add(rest);
  }
  return names;
}

async function snapshotButtonNames(recordingId) {
  const names = new Set();
  let rows = [];
  try {
    rows = await listEvidence(recordingId, { kinds: ["snapshot"], limit: 0 });
  } catch {
    return [];
  }
  for (const row of rows) {
    let text = "";
    try {
      const blob = await getEvidence(recordingId, row.id);
      text = String(blob?.body?.text || "");
    } catch {
      continue;
    }
    for (const match of text.matchAll(/(?:button|link) "([^"]+)"/g)) {
      const name = match[1].trim();
      if (name.length >= 6) names.add(name);
    }
  }
  return [...names];
}

export function splitFields(text) {
  const parts = [];
  let current = "";
  let quoted = false;
  for (const char of String(text || "")) {
    if (char === '"') {
      quoted = !quoted;
      current += char;
      continue;
    }
    if (char === "|" && !quoted) {
      if (current.trim()) parts.push(current.trim());
      current = "";
      continue;
    }
    current += char;
  }
  if (current.trim()) parts.push(current.trim());
  return parts;
}

export function filledLabels(evidence) {
  const labels = [];
  for (const row of evidence || []) {
    if (row.kind !== "action") continue;
    const named = String(row.summary || "").match(/^(?:fill|fill_fields|select|upload):(.*)$/);
    if (!named) continue;
    labels.push(...splitFields(named[1]));
  }
  return labels;
}

function sameCommand(left, right) {
  const norm = (line) => String(line).trim().replace(/^python3\b/, "python");
  return norm(left) === norm(right);
}

export function readCommands(skillMd) {
  const commands = [];
  for (const raw of String(skillMd || "").split(/\n/)) {
    const line = raw.trim();
    if (!line || line.includes("--confirm")) continue;
    const found = line.match(/python3?\s+scripts\/client\.py(?:\s+[^\s`]+)*/);
    if (!found) continue;
    commands.push(found[0]);
  }
  return commands;
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
  for (const rel of ["SKILL.md", "scripts/client.py", "references/api.md"]) {
    if (sourceHasSecret(texts[rel] || "")) errors.push({ code: "secret_in_source", path: rel });
  }
  const api = texts["references/api.md"] || "";
  const parsed = parseApiContract(api);
  const fields = parsed.fields;
  const bindings = parsed.bindings;
  const unresolved = parsed.unresolved;
  const evidence = await listEvidence(recordingId, { limit: 0 });
  const known = new Set(evidence.map((row) => row.id));
  for (const group of [...fields, ...bindings, ...unresolved]) {
    for (const id of idsOf(group.evidence_ids)) {
      if (!known.has(id)) errors.push({ code: "evidence_missing", id });
    }
  }
  const skillMd = texts["SKILL.md"] || "";
  const buttonNames = await snapshotButtonNames(recordingId);
  for (const label of filledLabels(evidence)) {
    if (labelTexts(label, buttonNames).some((name) => api.includes(name))) continue;
    errors.push({
      code: "filled_not_written",
      field: label,
      file: "references/api.md",
      hint: "这个名字要写进字段或未解决。",
    });
  }
  const front = skillMd.match(/^---\r?\n([\s\S]*?)\r?\n---/);
  if (skillMd && (!front || !/^name:\s*\S/m.test(front[1]) || !/^description:\s*\S/m.test(front[1]))) {
    errors.push({
      code: "not_invocable",
      file: "SKILL.md",
      hint: "文件从 --- 开始，写 name 和 description，再写一行 ---。调用方靠 description 启用",
    });
  }
  if (/disable-model-invocation:\s*true/.test(skillMd)) {
    errors.push({ code: "not_invocable", file: "SKILL.md", hint: "不要写 disable-model-invocation，否则调用方的智能体不会启用" });
  }
  const reads = readCommands(skillMd);
  const businessReads = reads.filter((line) => !configOnly(line));
  const ran = [...new Set(evidence
    .filter((row) => row.kind === "verify" && Array.isArray(row.argv) && row.argv.length)
    .map((row) => row.argv.join(" "))
    .filter((line) => line && !/\bshow-config\b/.test(line)))];
  if (citedPaths(texts["scripts/client.py"] || "").length && !businessReads.length) {
    errors.push({
      code: "command_not_run",
      ...(ran.length ? { ran } : {}),
      file: "SKILL.md",
      hint: "show-config 只检查配置。再写一条打到证据路径的读命令，并用相同 argv 跑 run_skill_command。已经跑过的 argv 在 ran，写进 SKILL.md 的那一行要和它相同",
    });
  }
  if (!reads.length && skillMd) {
    errors.push({
      code: "command_not_run",
      ...(ran.length ? { ran } : {}),
      file: "SKILL.md",
      hint: ran.length
        ? "SKILL.md 里写一行和 ran 里相同的 python scripts/client.py。列表和反引号都可以"
        : "在 SKILL.md 写一条含 python scripts/client.py 的读命令，列表和反引号都可以，再用相同 argv 调用 run_skill_command",
    });
  }
  const detailed = [];
  const requestsForWrites = [];
  for (const row of evidence.filter((item) => item.kind === "network")) {
    const blob = await getEvidence(recordingId, row.id).catch(() => null);
    const body = blob?.body && typeof blob.body === "object" ? blob.body : blob;
    if (!body) continue;
    if (body.path) requestsForWrites.push({ method: body.method || "", path: body.path });
    if (body.path || body.post_data) detailed.push({ ...body, id: row.id });
  }
  const actionNames = new Map(evidence.filter((row) => row.kind === "action" && row.action_id).map((row) => [row.action_id, row.summary]));
  const indexed = detailed.map((row, at) => {
    const base = { ...requestIndexRow(row), ...observeRequest(row) };
    const name = actionNames.get(row.action_id || "");
    const tagged = name ? { ...base, action: name } : base;
    const prev = previousMatchingRequest(detailed, at);
    const changed = prev ? changedRequestKeys(prev, row) : [];
    return changed.length ? { ...tagged, changed_keys: changed } : tagged;
  });
  const requests = writerRequestRows(indexed);
  const readGuides = new Set(evidence.filter((row) => row.kind === "guide").map((row) => row.summary));
  for (const name of guidesFor(requests, filledLabels(evidence))) {
    if (!readGuides.has(name)) errors.push({ code: "guide_not_read", name });
  }
  const opened = new Set(evidence.filter((row) => row.kind === "read").map((row) => row.summary));
  const credentialIds = new Set(indexed.filter((row) => row.issues_credential).map((row) => row.id));
  const networkIds = new Set(evidence.filter((row) => row.kind === "network").map((row) => row.id));
  const cited = new Set();
  for (const group of [...fields, ...bindings, ...unresolved]) {
    for (const id of idsOf(group.evidence_ids)) cited.add(id);
  }
  for (const match of api.matchAll(/\b(?:req|ev)_[A-Za-z0-9]+\b/g)) cited.add(match[0]);
  for (const id of cited) {
    if (!networkIds.has(id) || credentialIds.has(id) || opened.has(id)) continue;
    errors.push({
      code: "evidence_not_opened",
      id,
      file: "references/api.md",
      hint: "这个 id 写进了手册，但全文还没打开。用 network_get 打开它，对照填写值、changed_keys 和正文写请求值从哪来。没分清就放未解决。",
    });
  }
  const clientText = texts["scripts/client.py"] || "";
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
  if (citedPaths(clientText).length && reads.length && !mustRun.length) {
    errors.push({
      code: "command_not_run",
      file: "SKILL.md",
      hint: "写命令不要为了验证再发一次。在 SKILL.md 再写一行 python scripts/client.py show-config，并用相同 argv 跑 run_skill_command",
    });
  }
  const verifyRows = evidence.filter((item) => item.kind === "verify");
  for (const line of mustRun) {
    let ran = false;
    const sub = subcommand(line);
    const runs = sub
      ? verifyRows.filter((item) => subcommand((item.argv || []).join(" ")) === sub)
      : verifyRows.filter((item) => sameCommand((item.argv || []).join(" "), line));
    for (const row of runs) {
      const blob = await getEvidence(recordingId, row.id).catch(() => null);
      const output = verifyOutput(blob);
      if (commandAuthFailed(output)) ran = true;
      else ran = row.ok === true;
    }
    if (!ran) {
      errors.push({
        code: "command_not_run",
        command: line,
        file: "SKILL.md",
        hint: sub
          ? "这个子命令跑过一次即可。会变的参数写成 <参数名>，跑的时候用证据里的值"
          : "run_skill_command 的 argv 用空格拼起来要等于这一行。没有鉴权而停止可以；其它失败要先改到能跑",
      });
    }
  }
  let authFile = null;
  try {
    authFile = credentialShape(JSON.parse(texts["config/auth.local.json"] || "{}"));
  } catch {
    authFile = null;
  }
  const rebuiltCredential = (Boolean(authFile?.path) && rebuildsCredentialPath(clientText, authFile.path)) || rebuildsCredentialQuery(clientText);
  if (clientText.trim() && writeOnDefault(clientText)) {
    errors.push({
      code: "write_on_default",
      file: "scripts/client.py",
      hint: "不带其它子命令运行时不要发出 POST、PUT、PATCH、DELETE。写入只放在 argv 选中的命令里。show-config 和 --show-config 只检查配置。",
    });
  }
  const headerOverlay = authHeaderOverlay(clientText);
  if (headerOverlay.missing || headerOverlay.replaces) {
    errors.push({
      code: "headers_not_overlaid",
      file: "scripts/client.py",
      hint: "DANO_AUTH_HEADERS 只覆盖同名头。先取 auth 里的 headers，再用环境变量里的同名头更新，不要 return 环境变量，也不要把整份 headers 赋成环境变量",
    });
  }
  if (requests.some((item) => item.issues_credential) && (!readsIssuedCredential(clientText) || rebuiltCredential)) {
    errors.push({
      code: "credential_not_used",
      file: "scripts/client.py",
      ...(authFile ? { auth_file: authFile } : {}),
      hint: rebuiltCredential
        ? "不要在脚本里写下 credential 的 path，也不要 split 之后用问号另拼查询串。重放只写 credential = auth[\"credential\"]，再 Request(credential[\"url\"], method=credential[\"method\"])。注释和文档字符串不算。新的刷新值只替换 credential[\"url\"] 里原来的查询值，再写回原文件"
        : "证据里有签发访问凭证的请求。读 config/auth.local.json 里的 credential，写 credential = auth[\"credential\"]，再用 Request(credential[\"url\"], method=credential[\"method\"]) 重放整段 url。注释和文档字符串不算。新的刷新值只替换 credential[\"url\"] 里原来的查询值，再写回原文件。失败或仍是 401 再停止",
    });
  }
  errors.push(...citationErrors(clientText, requests, "scripts/client.py"));
  const businessPaths = [...new Set(requests.filter((row) => row.path && !row.issues_credential).map((row) => row.path))];
  const citedInClient = new Set(citedPaths(clientText));
  if (clientText.trim() && businessPaths.length && !businessPaths.some((path) => citedInClient.has(path))) {
    errors.push({
      code: "path_not_in_evidence",
      file: "scripts/client.py",
      hint: "scripts/client.py 里要写索引中的 path，并用标准库发出这次录到的请求。只返回动作名字、脚本里没有这些 path，调用方打不到。",
    });
  }
  errors.push(...citationErrors(texts["references/api.md"] || "", requests, "references/api.md").filter((item) => item.code !== "key_not_written"));
  errors.push(...fieldContractErrors({
    fields,
    unresolved,
    unresolvedText: parsed.unresolvedText,
    bindings,
    requests,
    clientText,
    skillMd,
  }));
  for (const cited of adoptedEvidencePaths(citedPaths(clientText), requests)) {
    const hit = requests.some((row) => row.path === cited);
    if (!hit) continue;
    const near = [];
    let from = 0;
    while (from < api.length) {
      const at = api.indexOf(cited, from);
      if (at < 0) break;
      near.push(api.slice(Math.max(0, at - 500), at + cited.length + 500));
      from = at + cited.length;
    }
    const ids = [...near.join("\n").matchAll(/\b(?:req|ev)_[A-Za-z0-9]+\b/g)].map((item) => item[0]);
    if (!ids.some((id) => known.has(id))) {
      const credentialPath = Boolean(authFile?.path) && cited === authFile.path;
      errors.push({
        code: "evidence_missing",
        path: cited,
        file: "references/api.md",
        hint: credentialPath
          ? "这是 credential 的 path。不要写进脚本。重放 credential[\"url\"]，新的刷新值只替换这段 url 里原来的查询值"
          : "采用的 path 旁边写上这次证据的 id。字段写清是什么、调用方提供什么、请求值从哪来、依据是哪次动作和哪条请求",
      });
    }
  }
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
  let latestAuth = false;
  let authAt = 0;
  for (const row of [...verifyRows].reverse()) {
    const joined = (row.argv || []).join(" ");
    const blob = await getEvidence(recordingId, row.id).catch(() => null);
    const output = verifyOutput(blob);
    if (commandAuthFailed(output)) {
      latestAuth = true;
      authAt = Date.parse(row.at || "") || 0;
      break;
    }
    if (row.ok === true && !/\bshow-config\b/.test(joined)) break;
    if (row.ok === false) break;
  }
  if (latestAuth) {
    const clientMtime = await stat(path.join(skillDirPath, "scripts", "client.py")).then((info) => info.mtimeMs, () => 0);
    const scriptRewritten = authAt > 0 && clientMtime > authAt;
    errors.push(scriptRewritten ? {
      code: "auth_unproven",
      hint: "上次登录失败发生在当前脚本之前。用现在的脚本再跑一次非写入命令。这次仍失败或 401 就停止。",
    } : {
      code: "auth_expired",
      hint: "账号未登录或凭证失效。停止调用，等人更新凭证后再继续",
    });
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
    const key = [item.code, item.field, item.path, item.command, item.name, item.key, item.file, item.id, item.from].join("|");
    if (seenError.has(key)) continue;
    seenError.add(key);
    unique.push(item);
  }
  errors.length = 0;
  errors.push(...collapseVerifyErrors(unique));
  let status = "verify_failed";
  if (!errors.length && hasCredentialHeaders(headers)) status = "skill_ready";
  else if (!errors.length) status = "skill_written_needs_auth";
  if (errors.some((item) => item.code === "auth_expired")) status = "verify_failed";
  const verify = {
    ok: status !== "verify_failed",
    status,
    errors,
    requests,
    filled: filledLabels(evidence),
    ...(authFile ? { auth_file: authFile } : {}),
  };
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
