import { adoptedEvidencePaths, citedPaths, observedSamples, responseFacts } from "./request-keys.mjs";

export { responseFacts };

const SOURCES = new Set(["caller", "current_user", "now", "previous_response", "other_api", "constant", "unknown"]);
const KINDS = new Set(["page", "caller_all", "server_verified", "server_unknown"]);
const LINKED_SOURCES = new Set(["current_user", "previous_response", "other_api"]);
const SLOT_HEADER = {
  page_name: "page_name",
  caller_name: "caller_name",
  request_path: "request_path",
  request_key: "request_key",
  data_type: "data_type",
  control: "control",
  source: "source",
  required_kind: "required_kind",
  format: "format",
  group: "group",
  evidence_ids: "evidence_ids",
  constant_reason: "constant_reason",
  from: "from",
  to: "to",
  locate: "locate",
  transform: "transform",
  unique: "unique",
  on_mismatch: "on_mismatch",
};
const FIELD_SLOTS = new Set([
  "page_name", "caller_name", "request_path", "request_key", "data_type", "control",
  "source", "required_kind", "format", "group", "evidence_ids", "constant_reason",
]);

export function observeRequest(row) {
  const body = row?.response_body ?? row?.body?.response_body;
  return { ...responseFacts(body), ...observedSamples(row) };
}

function blankSlot(value) {
  const text = String(value || "").trim();
  if (!text || text === "-" || text === "—") return "";
  return text;
}

function sectionOf(text, title) {
  const match = String(text || "").replace(/\r\n/g, "\n").match(new RegExp(`## ${title}\\n([\\s\\S]*?)(?=\\n## |$)`));
  return match ? match[1] : "";
}

function bulletBlocks(body, startKey) {
  const rows = [];
  let current = null;
  for (const line of String(body || "").split(/\n/)) {
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

function tableCells(line) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
}

function isTableRule(line) {
  const cells = tableCells(line);
  return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function tableRecords(body) {
  let inherited = {};
  let header = null;
  const rows = [];
  for (const line of String(body || "").split(/\n/)) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    if (/^#{1,6}\s/.test(trimmed) || trimmed.startsWith("```")) {
      inherited = {};
      header = null;
      continue;
    }
    const loose = trimmed.match(/^([a-z_]+):\s*(.*)$/);
    if (loose && FIELD_SLOTS.has(loose[1])) {
      inherited = { ...inherited, [loose[1]]: blankSlot(loose[2]) };
      header = null;
      continue;
    }
    if (!trimmed.startsWith("|")) {
      header = null;
      continue;
    }
    if (isTableRule(trimmed)) continue;
    const values = tableCells(trimmed);
    const mapped = values.map((name) => SLOT_HEADER[name] || "");
    const named = values.filter(Boolean);
    const headerLike = named.length >= 2 && mapped.filter(Boolean).length === named.length;
    if (!header || headerLike) {
      if (mapped.some(Boolean)) header = mapped;
      continue;
    }
    const item = { ...inherited };
    header.forEach((name, index) => {
      if (name) item[name] = blankSlot(values[index]);
    });
    rows.push(item);
  }
  return rows;
}

function finishField(item) {
  const row = {};
  for (const [key, value] of Object.entries(item)) row[key] = blankSlot(value);
  if (!row.caller_name && row.request_key && row.source && row.source !== "caller") row.caller_name = row.request_key;
  return row;
}

function dedupeRows(rows, keys) {
  const seen = new Set();
  const out = [];
  for (const row of rows) {
    const id = keys.map((key) => row[key] || "").join("|");
    if (seen.has(id)) continue;
    seen.add(id);
    out.push(row);
  }
  return out;
}

function pathsIn(text) {
  const rows = [];
  for (const match of String(text || "").matchAll(/\/[A-Za-z0-9][A-Za-z0-9_./-]*/g)) {
    rows.push({ path: match[0].replace(/[.,，。、；;:：]+$/g, "") });
  }
  return rows;
}

export function parseApiContract(api) {
  const text = String(api || "").replace(/\r\n/g, "\n");
  const fieldBody = sectionOf(text, "字段");
  const bindBody = sectionOf(text, "绑定");
  const openBody = sectionOf(text, "未解决");
  const fields = dedupeRows(
    [...bulletBlocks(fieldBody, "page_name"), ...tableRecords(fieldBody)].map(finishField)
      .filter((item) => item.request_key || item.caller_name || item.page_name),
    ["request_path", "request_key", "caller_name"],
  );
  const bindings = dedupeRows(
    [...bulletBlocks(bindBody, "from"), ...tableRecords(bindBody)]
      .filter((item) => item.from || item.to),
    ["from", "to", "evidence_ids"],
  );
  return {
    fields,
    bindings,
    unresolved: [...bulletBlocks(openBody, "field"), ...bulletBlocks(openBody, "path"), ...pathsIn(openBody)],
    unresolvedText: openBody,
  };
}

function splitIds(value) {
  return String(value || "").split(/[\s,，]+/).map((item) => item.trim()).filter(Boolean);
}

function mentionsToken(text, token) {
  const escaped = String(token || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  if (!escaped) return false;
  return new RegExp(`(?:^|[^A-Za-z0-9_])${escaped}(?:[^A-Za-z0-9_]|$)`).test(String(text || ""));
}

function keyParked(unresolved, unresolvedText, key, callerName) {
  if ((unresolved || []).some((item) => item.field === key || item.field === callerName || item.request_key === key)) return true;
  if (mentionsToken(unresolvedText, key)) return true;
  return Boolean(callerName) && mentionsToken(unresolvedText, callerName);
}

function bindingLinks(binding, hit, key) {
  const from = String(binding.from || "").trim();
  const to = String(binding.to || "").trim();
  if (!from || !to || from === to) return false;
  const text = `${from} ${to} ${binding.locate || ""}`;
  if (!mentionsToken(text, key)) return false;
  const fieldIds = splitIds(hit.evidence_ids);
  const bindIds = splitIds(binding.evidence_ids);
  if (fieldIds.length && bindIds.length && !bindIds.some((id) => fieldIds.includes(id))) return false;
  return true;
}

function pathOf(item) {
  return String(item?.request_path || item?.path || "").trim();
}

function samePath(left, right) {
  const a = String(left || "");
  const b = String(right || "");
  if (!a || !b) return false;
  return a === b || a.endsWith(b) || b.endsWith(a);
}

function mentionsPath(item, path) {
  return samePath(pathOf(item), path);
}

export function fieldContractErrors({ fields, unresolved, unresolvedText = "", bindings, requests, clientText, skillMd }) {
  const errors = [];
  const adopted = new Set(adoptedEvidencePaths(citedPaths(clientText), requests));
  for (const binding of bindings || []) {
    const missing = ["to", "locate", "on_mismatch", "evidence_ids"].filter((slot) => !binding[slot]);
    if (binding.on_mismatch && !["stop", "ask"].includes(binding.on_mismatch)) missing.push("on_mismatch");
    if (!missing.length) continue;
      errors.push({
        code: "binding_unaccounted",
        from: String(binding.from || ""),
        missing,
        file: "references/api.md",
        hint: "绑定要写 from、to、locate、transform、unique、on_mismatch、evidence_ids。on_mismatch 只写 stop 或 ask。值相等不能单独当作绑定。",
      });
  }
  for (const row of requests || []) {
    if (!row.path || row.issues_credential) continue;
    const cited = adopted.has(row.path);
    const parkedPath = (unresolved || []).some((item) => mentionsPath(item, row.path));
    if (!cited && row.option_list && !parkedPath) {
      errors.push({
        code: "option_unclassified",
        path: row.path,
        file: "references/api.md",
        hint: "这条 option_list 要么写成调用前现查的读命令，要么在未解决里写上 path。",
      });
    }
    if (!cited && row.url_string && !parkedPath) {
      errors.push({
        code: "url_unclassified",
        path: row.path,
        file: "references/api.md",
        hint: "这条 url_string 要么写成命令，把返回的字符串写入后续正文，要么在未解决里写上 path。",
      });
    }
    if (!cited) continue;
    for (const key of row.keys || []) {
      const hit = (fields || []).find((item) => mentionsPath(item, row.path) && (item.request_key === key || item.caller_name === key));
      const parkedKey = keyParked(unresolved, unresolvedText, key, "");
      if (!hit && parkedKey) continue;
      if (!hit) {
        errors.push({
          code: "field_unaccounted",
          path: row.path,
          key,
          file: "references/api.md",
          hint: "写一条字段。用 `- page_name:` 起一条，或用槽名做表头。要有 page_name、caller_name、request_path、request_key、source、required_kind、evidence_ids。来源没分清就在未解决写上这个键。",
        });
        continue;
      }
      const missing = [];
      if (!hit.page_name) missing.push("page_name");
      if (!hit.caller_name) missing.push("caller_name");
      if (!hit.request_path) missing.push("request_path");
      if (!hit.request_key && hit.caller_name !== key) missing.push("request_key");
      if (!SOURCES.has(hit.source)) missing.push("source");
      if (hit.source !== "unknown" && !KINDS.has(hit.required_kind)) missing.push("required_kind");
      if (!String(hit.evidence_ids || "").trim()) missing.push("evidence_ids");
      if (hit.source === "constant" && (!hit.constant_reason || /录到的就是这个/.test(hit.constant_reason))) missing.push("constant_reason");
      if (hit.source === "unknown") {
        if (!keyParked(unresolved, unresolvedText, key, hit.caller_name)) missing.push("未解决");
      }
      if (missing.length) {
        errors.push({
          code: "field_unaccounted",
          path: row.path,
          key,
          missing,
          file: "references/api.md",
          hint: `补上：${missing.join("、")}。source 只能是 caller、current_user、now、previous_response、other_api、constant、unknown。required_kind 只能是 page、caller_all、server_verified、server_unknown。`,
        });
      }
      if (hit.source === "caller" && hit.caller_name && !String(skillMd || "").includes(hit.caller_name)) {
        errors.push({
          code: "caller_not_in_handbook",
          path: row.path,
          key,
          caller_name: hit.caller_name,
          file: "SKILL.md",
          hint: "source 是 caller 时，SKILL.md 要让调用方提供这个名字。会变的参数写成 <名字>。",
        });
      }
      if (LINKED_SOURCES.has(hit.source) && !(bindings || []).some((binding) => bindingLinks(binding, hit, key))) {
        errors.push({
          code: "source_unlinked",
          path: row.path,
          key,
          file: "references/api.md",
          hint: "source 是 current_user、previous_response 或 other_api 时，要有一条绑定。from 和 to 不同，并写出这个 request_key，evidence_ids 含这条字段的证据。写不出就改成 unknown，放进未解决。",
        });
      }
    }
  }
  const named = new Map();
  const ambiguous = new Set();
  for (const hit of fields || []) {
    const key = String(hit.request_key || "");
    const requestPath = String(hit.request_path || "");
    if (!key || !requestPath) continue;
    const id = `${requestPath}\n${key}`;
    const caller = String(hit.caller_name || "");
    if (!named.has(id)) {
      named.set(id, caller);
      continue;
    }
    if (named.get(id) === caller || ambiguous.has(id)) continue;
    ambiguous.add(id);
    errors.push({
      code: "key_ambiguous",
      path: requestPath,
      key,
      file: "references/api.md",
      hint: "同一个 request_key 写了两条字段。数组里不同的行要写绑定，说明用哪个键区分；分不清就留一条，另一条放未解决。",
    });
  }
  return errors;
}
