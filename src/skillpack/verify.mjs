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
  const escaped = String(title || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = String(text || "").match(new RegExp(`(?:^|\\n)(#{1,6})\\s+${escaped}\\s*(?:\\n|$)`));
  if (!match) return "";
  const level = match[1].length;
  const rest = String(text || "").slice(match.index + match[0].length);
  const next = rest.search(new RegExp(`\\n#{1,${level}}\\s+`));
  return next < 0 ? rest : rest.slice(0, next);
}

function firstFieldValue(rest) {
  return String(rest || "").split(/,\s*(?=[a-z_]+\s*[:=])/)[0].trim();
}

function extraFieldPairs(rest) {
  const chunks = String(rest || "").split(/,\s*(?=[a-z_]+\s*[:=])/).slice(1);
  const out = [];
  for (const chunk of chunks) {
    const item = chunk.match(/^([a-z_]+)\s*[:=]\s*(.*)$/);
    if (item) out.push([item[1], item[2].trim()]);
  }
  return out;
}

const SOURCE_TOKEN = /(previous_response|current_user|other_api|constant|unknown|caller|now)(?![A-Za-z_])/;

function sourceToken(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  if (FIELD_SOURCES.has(raw)) return raw;
  const hit = raw.match(SOURCE_TOKEN);
  return hit ? hit[1] : raw;
}

function assignFieldKey(row, key, value) {
  if (key === "source") row.source = sourceToken(value);
  else row[key] = value;
  if (key === "reason" || key === "constant_reason") row.constant_reason = value;
}

function blocks(body, startKey) {
  const rows = [];
  let current = null;
  for (const line of body.split(/\n/)) {
    const item = line.match(/^\s*- ([a-z_]+)\s*[:=]\s*(.*)$/);
    if (!item) continue;
    if (item[1] === startKey) {
      current = {};
      rows.push(current);
    }
    if (!current) continue;
    assignFieldKey(current, item[1], firstFieldValue(item[2]));
    for (const [key, value] of extraFieldPairs(item[2])) assignFieldKey(current, key, value);
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

function functionBlock(text, name) {
  const token = String(name || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  if (!token) return "";
  const match = String(text || "").match(new RegExp(`(?:^|\\n)(?:async )?def\\s+${token}\\b[\\s\\S]*?(?=(?:\\n(?:async )?def )|$)`));
  return match ? match[0] : "";
}

function blockWrites(block) {
  return /(?:method\s*=\s*["'](POST|PUT|PATCH|DELETE)["']|\b(?:POST|PUT|PATCH|DELETE)\b.*["']\/)/i.test(String(block || ""));
}

function commandWrites(line, clientText) {
  const sub = subcommand(line);
  if (!sub || sub === "show-config") return false;
  const token = sub.replaceAll("-", "_");
  const named = new RegExp(`(?:^|\\n)(?:async )?def\\s+(?:cmd_)?${token}\\b`);
  const blocks = String(clientText || "").split(/(?=\n(?:async )?def )/);
  const hit = blocks.find((block) => named.test(block) || new RegExp(`add_parser\\(["']${sub.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}["']\\)`).test(block));
  if (!hit) return false;
  if (blockWrites(hit)) return true;
  const skip = new Set([
    token, `cmd_${token}`, "print", "len", "str", "int", "json", "open", "dict", "list", "range",
    "enumerate", "zip", "min", "max", "sum", "abs", "bool", "float", "type", "isinstance",
    "hasattr", "getattr", "setattr", "super", "sorted", "reversed", "map", "filter", "any",
    "all", "format", "join", "load_runtime", "urlencode", "Request", "urlopen",
  ]);
  for (const call of hit.matchAll(/\b([A-Za-z_][A-Za-z0-9_]*)\s*\(/g)) {
    const name = call[1];
    if (skip.has(name)) continue;
    if (blockWrites(functionBlock(clientText, name))) return true;
  }
  return false;
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

function asCommandLine(raw) {
  return String(raw || "")
    .trim()
    .replace(/^[-*\d.)]+\s+/, "")
    .replace(/^`+|`+$/g, "")
    .trim()
    .replace(/^python3\b/, "python")
    .replace(/\s+/g, " ");
}

function realSubcommand(line) {
  const sub = (String(line).match(/scripts\/client\.py\s+(\S+)/) || [])[1] || "";
  return /^[a-z][a-z0-9-]*$/.test(sub);
}

export function skillCommandLines(skillMd) {
  const found = [];
  const seen = new Set();
  const add = (raw) => {
    const line = asCommandLine(raw);
    if (!/^python\s+scripts\/client\.py\b/.test(line) || !realSubcommand(line) || seen.has(line)) return;
    seen.add(line);
    found.push(line);
  };
  for (const line of String(skillMd || "").split(/\n/)) {
    const trimmed = line.trim();
    add(trimmed);
    if (trimmed.includes("|")) {
      for (const cell of trimmed.split("|")) add(cell);
    }
  }
  return found;
}

function readCommands(skillMd) {
  return skillCommandLines(skillMd).filter((line) => !line.includes("--confirm"));
}

export function writeOnlySkill(skillMd, clientText, requests) {
  const lines = skillCommandLines(skillMd);
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
  if (/AuthExpired|缺少鉴权|没有鉴权|请提供 token|账号未登录|\b401\b|凭证缺失|没有可用 token|没有可用鉴权/i.test(text)) return true;
  return /\b400\b/.test(text) && /refresh|token|令牌|credential/i.test(text);
}

export function scriptCrashOutput(output) {
  const text = String(output || "");
  if (authStoppedOutput(text)) return false;
  return /Traceback \(most recent call last\)/.test(text);
}

export function authOverlayParseError(clientText) {
  const text = String(clientText || "");
  if (!/\bDANO_AUTH_HEADERS\b/.test(text)) return "";
  const loads = /DANO_AUTH_HEADERS[\s\S]{0,400}json\.loads/.test(text)
    || /json\.loads\s*\(\s*(?:os\.environ\.get\(\s*[\"']DANO_AUTH_HEADERS[\"']|[^)]*DANO_AUTH_HEADERS)/.test(text);
  if (!loads) return "DANO_AUTH_HEADERS 是 JSON 对象，用 json.loads 覆盖同名头，不要按逗号拆。";
  const merged = /\.update\s*\(|headers\s*\[|for\s+\w+\s*,\s*\w+\s+in\s+/.test(text);
  const replaced = /return\s+json\.loads|headers\s*=\s*json\.loads/.test(text);
  if (replaced && !merged) {
    return "DANO_AUTH_HEADERS 是 JSON 对象，json.loads 后只覆盖同名头，不要整袋替换。";
  }
  return "";
}

export function hardcodedOriginError(clientText) {
  const text = String(clientText || "");
  if (/(?:DEFAULT_BASE_URL|base_url)\s*=\s*["']https?:\/\/(?!127\.0\.0\.1\b|localhost\b)/i.test(text)) {
    return "基地址只读 DANO_BASE_URL 或 config/runtime.json，不要写死源站。";
  }
  if (/(?:os\.environ\.get\(\s*[\"']DANO_BASE_URL[\"'][^)]*\)|DANO_BASE_URL)[^;\n]{0,120}or\s+["']https?:\/\/(?!127\.0\.0\.1\b|localhost\b)/i.test(text)) {
    return "基地址只读 DANO_BASE_URL 或 config/runtime.json，不要写死源站。";
  }
  return "";
}

function latestMatchingRun(verifyRows, line) {
  const sub = subcommand(line);
  const bySub = sub
    ? (verifyRows || []).filter((item) => subcommand((item.argv || []).join(" ")) === sub)
    : [];
  if (bySub.length) return bySub.at(-1);
  const runs = (verifyRows || []).filter((item) => sameCommand((item.argv || []).join(" "), line));
  return runs.at(-1) || null;
}

const FIELD_SOURCES = new Set(["caller", "current_user", "now", "previous_response", "other_api", "constant", "unknown"]);
const REQUIRED_KINDS = new Set(["page", "caller_all", "server_verified", "server_unknown"]);
const ON_MISMATCH = new Set(["stop", "ask"]);
const CHOICE_CONTROLS = new Set(["combobox", "listbox", "radio", "select", "tree", "treeselect"]);
const DATE_CONTROLS = new Set(["date", "datepicker", "datetime", "time", "datetimepicker"]);
const CHOICE_INPUTS = new Set(["select", "radio", "treeselect", "checkbox"]);
const DATE_INPUTS = new Set(["date"]);
const DANO_INPUTS = new Set(["text", "textarea", "date", "select", "radio", "treeselect", "checkbox"]);

function controlToken(value) {
  return String(value || "").toLowerCase().replace(/[\s_-]/g, "");
}

function fieldHeaderName(cell) {
  const raw = String(cell || "").trim();
  const compact = raw.toLowerCase().replace(/[\s_-]/g, "");
  if (compact === "pagename" || raw === "字段" || raw === "页面名" || (raw.includes("页面") && raw.includes("字段"))) return "page_name";
  if (compact === "source" || raw === "来源") return "source";
  if (compact === "control" || raw === "控件") return "control";
  if (compact === "options" || raw === "选项") return "options";
  if (compact === "constantreason" || compact === "reason" || raw === "原因") return "constant_reason";
  return "";
}

function tableFieldRows(body) {
  const rows = [];
  let headers = [];
  for (const line of String(body || "").split(/\n/)) {
    if (!/^\s*\|/.test(line) || /-{3,}/.test(line)) continue;
    const cells = line.split("|").map((cell) => cell.trim()).filter(Boolean);
    if (cells.length < 2) continue;
    if (cells.some((cell) => fieldHeaderName(cell))) {
      headers = cells.map((cell) => fieldHeaderName(cell) || cell.toLowerCase());
      continue;
    }
    if (headers.length) {
      const row = { page_name: "", source: "", control: "", options: "", constant_reason: "" };
      for (let i = 0; i < cells.length && i < headers.length; i += 1) {
        const key = headers[i];
        if (key === "page_name" || key === "字段") row.page_name = cells[i];
        else if (key === "source") row.source = sourceToken(cells[i]);
        else if (key === "control") row.control = cells[i];
        else if (key === "options") row.options = cells[i];
        else if (key === "constant_reason") row.constant_reason = cells[i];
      }
      if (!row.page_name) row.page_name = cells[0];
      if (!row.source) {
        const token = cells.map((cell) => sourceToken(cell)).find((cell) => FIELD_SOURCES.has(cell));
        if (token) row.source = token;
      }
      if (row.page_name) rows.push(row);
      continue;
    }
    const token = cells.map((cell) => sourceToken(cell)).find((cell) => FIELD_SOURCES.has(cell));
    if (!token) continue;
    const control = cells.find((cell) => CHOICE_CONTROLS.has(controlToken(cell)) || ["text", "textbox", "textarea"].includes(controlToken(cell))) || "";
    rows.push({ page_name: cells[0], source: token, control, options: "" });
  }
  return rows;
}

function callerFacing(row) {
  if (row.source === "caller") return true;
  return row.source === "other_api" && CHOICE_CONTROLS.has(controlToken(row.control));
}

function fieldShapeErrors(fields) {
  const errors = [];
  for (const row of fields || []) {
    if (row.source && !FIELD_SOURCES.has(row.source)) {
      errors.push({
        code: "field_source_invalid",
        field: row.page_name,
        source: row.source,
        hint: "source 只写 caller/current_user/now/previous_response/other_api/constant/unknown 这一词，说明放在旁边不要写进 source",
      });
    }
    if (row.required_kind && !REQUIRED_KINDS.has(row.required_kind)) {
      errors.push({ code: "required_kind_invalid", field: row.page_name, required_kind: row.required_kind });
    }
    if (row.on_mismatch && !ON_MISMATCH.has(row.on_mismatch)) {
      errors.push({ code: "on_mismatch_invalid", field: row.page_name, on_mismatch: row.on_mismatch });
    }
    if (CHOICE_CONTROLS.has(controlToken(row.control)) && !String(row.options || "").trim() && row.source !== "other_api") {
      errors.push({
        code: "not_invocable",
        field: row.page_name,
        hint: "封闭枚举在手册里要写 options；source=other_api 时 questions[] 的 options 留空，调用时先跑选项命令再填",
      });
    }
  }
  return errors;
}

function extractQuestions(skillMd) {
  const text = String(skillMd || "");
  const rows = [];
  let from = 0;
  while (from < text.length) {
    const idx = text.slice(from).search(/"questions"\s*:\s*\[/);
    if (idx < 0) break;
    const start = text.indexOf("[", from + idx);
    if (start < 0) break;
    let depth = 0;
    let end = -1;
    for (let i = start; i < text.length; i += 1) {
      if (text[i] === "[") depth += 1;
      else if (text[i] === "]") {
        depth -= 1;
        if (depth === 0) {
          end = i;
          break;
        }
      }
    }
    if (end < 0) break;
    try {
      const parsed = JSON.parse(text.slice(start, end + 1));
      if (Array.isArray(parsed)) rows.push(...parsed);
    } catch {
      // 这一块不是完整 JSON，跳过
    }
    from = end + 1;
  }
  return rows;
}

function fieldForQuestion(question, fields) {
  const id = String(question?.id || "").toLowerCase().replace(/[\s_-]/g, "");
  const asked = String(question?.question || "");
  return (fields || []).find((row) => {
    const name = String(row.page_name || "");
    const token = name.toLowerCase().replace(/[\s_-]/g, "");
    return (name && asked.includes(name)) || (token && id.includes(token)) || (token && token.includes(id));
  });
}

function liveChoiceQuestion(row, question) {
  if (row?.source === "other_api") return true;
  const control = controlToken(row?.control || question?.inputType);
  return control === "tree" || control === "treeselect";
}

export const ASK_PROTOCOL = "读：读命令没有调用方槽就直接执行，不发 ask_user_question，不发确认卡。写：第一次把 SKILL.md 里这张 title+questions[] 原样发出，id、question、inputType、options、required 不改，不增题、不减题、不另造确认题；对话里已确定的值放进对应 default，不再另问，上一步查到的业务日期直接作 default，不改成当天；调用方提交后同一轮只发 {\"confirm\":true,\"formIds\":[\"<formId>\"]}；确认后跑 python3 写命令加 --confirm。";
const JSON_QUESTION_HINT = "问句只写页面标签和列名，不写 JSON、数组、类型名；default 不写 JSON。多行明细用 textarea 一行一项、列之间用「|」，括号里只写列名顺序。";

export function jsonWordedQuestion(question) {
  const asked = String(question?.question || "");
  if (/json/i.test(asked)) return true;
  const value = String(question?.default ?? "").trim();
  return /^[\[{]/.test(value);
}

export function askProtocolMissing(skillMd) {
  const text = String(skillMd || "");
  if (!/--confirm/.test(text)) return false;
  return !(/formIds/.test(text) && /"confirm"\s*:\s*true|confirm\s*:\s*true/.test(text));
}

function recordedSampleDefault(value, haystack) {
  const text = String(value ?? "").trim();
  if (!text || text === " " || text === "[]") return false;
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(text) && !/^\d{3,}$/.test(text)) return false;
  return String(haystack || "").includes(text);
}

function questionShapeErrors(questions, fields, haystack, overlay) {
  const errors = [];
  const list = Array.isArray(questions) ? questions : [];
  const choiceFields = (fields || []).filter((row) => callerFacing(row) && CHOICE_CONTROLS.has(controlToken(row.control)));
  const dateFields = (fields || []).filter((row) => callerFacing(row) && DATE_CONTROLS.has(controlToken(row.control)));
  const overlayChoice = (overlay?.roles || []).some((role) => CHOICE_CONTROLS.has(controlToken(role)));
  const overlayDate = (overlay?.inputs || []).some((item) => DATE_CONTROLS.has(controlToken(item)) || item === "datetime-local" || item === "month");
  const choiceQs = [];
  const dateQs = [];
  for (const question of list) {
    if (Object.prototype.hasOwnProperty.call(question || {}, "dataSource") && question.dataSource != null) {
      errors.push({
        code: "not_invocable",
        file: "SKILL.md",
        field: question.id,
        hint: "不要 dataSource：调用方只接受同源 dataSource，业务站不同源。活选项 options 留空，调用时先跑读命令再填。",
      });
    }
    const type = controlToken(question?.inputType);
    if (String(question?.inputType || "").trim() && !DANO_INPUTS.has(type)) {
      errors.push({
        code: "not_invocable",
        file: "SKILL.md",
        field: question.id,
        hint: "inputType 只许 text、textarea、date、select、radio、treeSelect、checkbox。多行明细用 textarea 一行一项、列用「|」，命令行用可重复行参数。",
      });
    }
    if (jsonWordedQuestion(question)) {
      errors.push({
        code: "not_invocable",
        file: "SKILL.md",
        field: question.id,
        hint: JSON_QUESTION_HINT,
      });
    }
    const row = fieldForQuestion(question, fields);
    if (recordedSampleDefault(question?.default, haystack)) {
      errors.push({
        code: "not_invocable",
        file: "SKILL.md",
        field: question.id,
        hint: "default 不要写本场 listed 里的业务 id，用空格占位。",
      });
    }
    if (CHOICE_INPUTS.has(type)) {
      choiceQs.push(question);
      const options = Array.isArray(question.options) ? question.options : [];
      if (liveChoiceQuestion(row, question)) {
        if (options.length) {
          errors.push({
            code: "not_invocable",
            file: "SKILL.md",
            field: question.id,
            hint: "活选项 options 留空，调用时先跑读命令再写入问句，不要把本场 id 写进手册。",
          });
        }
        continue;
      }
      if (!options.length) {
        errors.push({
          code: "not_invocable",
          file: "SKILL.md",
          field: question.id,
          hint: "封闭枚举在 questions[] 里要写 options，不要改成自由文本",
        });
        continue;
      }
      for (const opt of options) {
        const id = String(opt?.id ?? "").trim();
        const label = String(opt?.label ?? "").trim();
        if (!id || !label) {
          errors.push({
            code: "not_invocable",
            file: "SKILL.md",
            field: question.id,
            hint: "options 每项要有 id 和 label",
          });
        }
      }
      if (options.some((opt) => recordedSampleDefault(opt?.id, haystack))) {
        errors.push({
          code: "not_invocable",
          file: "SKILL.md",
          field: question.id,
          hint: "活选项 options 留空，调用时先跑读命令再写入问句，不要把本场 id 写进手册。",
        });
      }
    }
    if (DATE_INPUTS.has(type)) {
      dateQs.push(question);
      if (!String(question.dateFormat || "").trim()) {
        errors.push({
          code: "not_invocable",
          file: "SKILL.md",
          field: question.id,
          hint: "date 必须有 dateFormat，调用方才能按页面日期控件收集",
        });
      }
    }
  }
  if ((choiceFields.length || overlayChoice) && list.length && !choiceQs.length) {
    errors.push({
      code: "not_invocable",
      file: "SKILL.md",
      hint: "页面 combobox/listbox/radio 在调用方也必须是选择控件，活选项 options 留空，封闭枚举才写 options，不要写成 text",
    });
  }
  if ((dateFields.length || overlayDate) && list.length && !dateQs.length) {
    errors.push({
      code: "not_invocable",
      file: "SKILL.md",
      hint: "页面日期控件在调用方也必须是 date 并带 dateFormat，不要写成 text",
    });
  }
  return errors;
}

function writeConfirmErrors(clientText, skillMd) {
  const errors = [];
  const text = String(clientText || "");
  const parserRe = /add_parser\s*\(\s*["']([^"']+)["']/g;
  const parsers = [];
  let match;
  while ((match = parserRe.exec(text))) parsers.push({ name: match[1], index: match.index });
  const skillConfirm = new Set(
    skillCommandLines(skillMd).filter((line) => line.includes("--confirm")).map((line) => subcommand(line)).filter(Boolean),
  );
  for (let i = 0; i < parsers.length; i += 1) {
    const start = parsers[i].index;
    const end = i + 1 < parsers.length ? parsers[i + 1].index : text.length;
    const block = text.slice(start, end);
    const writes = commandWrites(`python scripts/client.py ${parsers[i].name}`, text) || skillConfirm.has(parsers[i].name);
    if (!writes || /--confirm/.test(block)) continue;
    errors.push({
      code: "not_invocable",
      file: "scripts/client.py",
      command: parsers[i].name,
      hint: "写入子命令必须有 --confirm，调用方确认后才发送",
    });
  }
  return errors;
}

export function argparseDispatchError(clientText) {
  const text = String(clientText || "");
  if (!/\.add_parser\s*\(/.test(text)) return "";
  if (!/set_defaults\s*\(\s*func\s*=/.test(text)) {
    return "每个 add_parser 必须 set_defaults(func=)，否则 Namespace.func 不存在，调用方一跑就崩";
  }
  return "";
}

function latestVerifyByCommand(verifyRows) {
  const map = new Map();
  for (const row of verifyRows || []) {
    const key = subcommand((row.argv || []).join(" ")) || (row.argv || []).join(" ");
    map.set(key, row);
  }
  return [...map.values()];
}


function idsOf(value) {
  return String(value || "").split(/[\s,]+/).map((item) => item.trim()).filter(Boolean);
}

// 结构错误归到哪份文件：写文件时只把这份文件的错误摆回去
export function structureErrorFile(error) {
  if (error?.file) return String(error.file);
  switch (error?.code) {
    case "field_source_invalid":
    case "required_kind_invalid":
    case "on_mismatch_invalid":
    case "evidence_missing":
      return "references/api.md";
    case "import_not_available":
      return "scripts/client.py";
    default:
      return "";
  }
}

const NOT_WRITE_TIME = new Set(["command_not_run", "auth_expired", "guide_not_read", "attachment_unresolved"]);

export function structureErrorsForFile(errors, rel) {
  return (Array.isArray(errors) ? errors : []).filter((error) => (
    !NOT_WRITE_TIME.has(error?.code) && structureErrorFile(error) === rel
  ));
}

export async function verifySkill(skillDirPath, recordingId, recording = {}, { dryRun = false } = {}) {
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
  for (const title of ["字段", "绑定", "未解决", "已验证读命令"]) {
    const escaped = title.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    if (!new RegExp(`(?:^|\\n)#{1,6}\\s+${escaped}\\s*(?:\\n|$)`).test(api)) {
      errors.push({
        code: "not_invocable",
        file: "references/api.md",
        heading: title,
        hint: `references/api.md 标题必须有「${title}」`,
      });
    }
  }
  const fields = [...blocks(section(api, "字段"), "page_name"), ...tableFieldRows(section(api, "字段"))];
  const bindings = blocks(section(api, "绑定"), "from");
  const unresolved = blocks(section(api, "未解决"), "field");
  errors.push(...fieldShapeErrors(fields));
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
  const hasQuestions = /"questions"\s*:\s*\[/.test(skillMd);
  const hasWriteConfirm = /--confirm/.test(skillMd);
  if ((fields.some(callerFacing) || hasWriteConfirm) && skillMd && !hasQuestions) {
    errors.push({
      code: "not_invocable",
      file: "SKILL.md",
      hint: "调用方字段必须在 SKILL.md 里放可原样复制的 title+questions[]。同一张 overlay 一张表，调用方每轮只调一次 ask_user_question。活选项 options 留空，封闭枚举才写 options，不要写成自由文本。",
    });
  }
  if (askProtocolMissing(skillMd)) {
    errors.push({
      code: "not_invocable",
      file: "SKILL.md",
      hint: `SKILL.md 要写明调用方怎么问。${ASK_PROTOCOL}`,
    });
  }
  if (hasQuestions && fields.length === 0) {
    errors.push({
      code: "not_invocable",
      file: "references/api.md",
      hint: "## 字段 收到下一个同级标题为止（其中的 ### 也算）。每条要有 page_name 和 source，用表或 - page_name= 均可，不要只写接口路径。",
    });
  }
  if (skillMd && !/^---\n[\s\S]*?\bname:\s*\S+/m.test(skillMd)) {
    errors.push({ code: "not_invocable", file: "SKILL.md", hint: "SKILL.md 必须以 --- 开头的 YAML 包住 name 和 description，再写正文。调用方靠这段启用，不要写成页面点击步骤" });
  }
  if (/disable-model-invocation:\s*true/.test(skillMd)) {
    errors.push({ code: "not_invocable", file: "SKILL.md", hint: "不要写 disable-model-invocation，否则调用方的智能体不会启用" });
  }
  const reads = readCommands(skillMd);
  const requests = [];
  const requestsForWrites = [];
  let optionHaystack = "";
  for (const row of evidence.filter((item) => item.kind === "network")) {
    const blob = await getEvidence(recordingId, row.id).catch(() => null);
    const body = blob?.body && typeof blob.body === "object" ? blob.body : blob;
    if (!body) continue;
    optionHaystack += `${body.response_body || ""} ${body.post_data || ""} ${body.query || ""}`;
    if (body.path) requestsForWrites.push({ method: body.method || "", path: body.path });
    if (body.path || body.post_data) requests.push(requestIndexRow({ ...body, id: row.id }));
  }
  for (const row of evidence.filter((item) => item.kind === "verify")) {
    const blob = await getEvidence(recordingId, row.id).catch(() => null);
    optionHaystack += `${blob?.stdout || blob?.body?.stdout || ""}`;
  }
  const overlay = { roles: [], inputs: [] };
  for (const row of evidence.filter((item) => item.kind === "action")) {
    const blob = await getEvidence(recordingId, row.id).catch(() => null);
    const body = blob?.body && typeof blob.body === "object" ? blob.body : blob;
    for (const item of body?.overlay_editable || []) {
      if (item?.role) overlay.roles.push(item.role);
      if (item?.input) overlay.inputs.push(item.input);
    }
  }
  if (hasQuestions) {
    errors.push(...questionShapeErrors(extractQuestions(skillMd), fields, optionHaystack, overlay));
  }
  const clientText = texts["scripts/client.py"] || "";
  const writeOnly = writeOnlySkill(skillMd, clientText, requestsForWrites);
  const businessReads = reads.filter((line) => !/^python3?\s+scripts\/client\.py\s+show-config\s*$/.test(line));
  if (!writeOnly && citedPaths(clientText).length && !businessReads.length) {
    errors.push({
      code: "command_not_run",
      file: "SKILL.md",
      hint: "show-config 只检查配置。目标含读时再写一条打到证据路径的读命令（行首或表格单元格），并用相同 argv 或同名子命令跑 run_skill_command。目标只要求写入时不要发明 GET。",
    });
  }
  if (!writeOnly && !reads.length && skillMd) {
    errors.push({
      code: "command_not_run",
      file: "SKILL.md",
      hint: "目标含读时，在 SKILL.md 写读命令（行首或表格单元格里的 python scripts/client.py），再用相同 argv 或同名子命令调用 run_skill_command。目标只要求写入时，写带 --confirm 的写命令即可，不要发明 GET。",
    });
  }
  if (clientText.trim()) {
    const argparseHint = argparseDispatchError(clientText);
    if (argparseHint) {
      errors.push({ code: "not_invocable", file: "scripts/client.py", hint: argparseHint });
    }
    errors.push(...writeConfirmErrors(clientText, skillMd));
    const overlayHint = authOverlayParseError(clientText);
    if (overlayHint) errors.push({ code: "not_invocable", file: "scripts/client.py", hint: overlayHint });
    const originHint = hardcodedOriginError(clientText);
    if (originHint) errors.push({ code: "not_invocable", file: "scripts/client.py", hint: originHint });
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
          ? "这一行是说明（含表格单元格）。用真实参数跑同名子命令即可，不要把尖括号原样当参数"
          : "run_skill_command 的 argv 用空格拼起来要等于这一行（含表格里抽出的命令）。没有鉴权而停止可以；其它失败要先改到能跑",
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
  let packedCredential = {};
  try {
    packedCredential = JSON.parse(texts["config/auth.local.json"] || "{}").credential || {};
  } catch {
    packedCredential = {};
  }
  if (packedCredential.url && packedCredential.method && !clientText.includes("credential")) {
    errors.push({
      code: "credential_not_used",
      file: "scripts/client.py",
      hint: "config/auth.local.json 同时有 credential.url 和 method 时，业务请求前按这份 credential 重放。只有 headers 时不要为 listed 里的刷新请求发明重放。",
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
    const key = [item.code, item.field, item.path, item.command, item.name, item.key, item.file, item.heading, item.hint].join("|");
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
  if (!dryRun && (status === "skill_ready" || status === "skill_written_needs_auth")) {
    await upsertCatalog({ ...recording, id: recordingId, skillId: recording.skillId, status, skillDir: dir });
  }
  return verify;
}

export async function finishSkill(recording) {
  const dir = skillDir(recording.skillId);
  await writeRuntimeConfig(recording, recording.skillId);
  return verifySkill(dir, recording.id, recording);
}
