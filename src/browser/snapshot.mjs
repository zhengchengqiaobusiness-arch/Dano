import { browserSession } from "./session.mjs";
import { readGoal } from "../evidence/store.mjs";

const OVERLAY_ROOT = /^(dialog|alertdialog|listbox|menu|tooltip)\b/i;
const OVERLAY_MODAL = /^(dialog|alertdialog)\b/i;
const CHROME_LANDMARK = /^(navigation|banner|contentinfo|complementary)$/i;
const PAGE_CHROME = /^(navigation|banner|contentinfo)$/i;
const VALUE_ROLES = /^(textbox|searchbox|combobox|spinbutton|checkbox|radio|slider|listbox)$/i;
const NOT_VALUE_ROLE = /^(menuitem|menu|navigation|banner|complementary|link|heading|listitem|option|tab|treeitem|columnheader|row)$/i;
const ROW_ROLE = /^row\b/i;
const CELL_ROLE = /^(cell|gridcell|button|link|textbox)\b/i;

function frameKey(frame) {
  const parent = frame.parentFrame();
  const parentId = parent?._guid || parent?.url() || "";
  return `${parentId}\n${frame.url()}`;
}

function indentOf(line) {
  const match = String(line || "").match(/^(\s*)/);
  return match ? match[1].length : 0;
}

function unwrapLine(line) {
  let raw = String(line || "").replace(/^\s*-\s*/, "").trim();
  if (raw.startsWith("'") && raw.endsWith("'")) raw = raw.slice(1, -1);
  return raw;
}

function parseNodes(yaml) {
  const nodes = [];
  for (const line of String(yaml || "").split(/\n/)) {
    const raw = unwrapLine(line);
    const ref = raw.match(/\[ref=([^\]]+)\]/);
    const role = raw.match(/^([a-zA-Z]+)/);
    if (!ref || !role) continue;
    const named = raw.match(/^[a-zA-Z]+ "((?:\\.|[^"])*)"/);
    const col = raw.match(/\scol "((?:\\.|[^"])*)"/);
    const trailing = raw.match(/\]:\s*(.+)$/);
    nodes.push({
      role: role[1].toLowerCase(),
      name: named ? named[1].replace(/\\"/g, '"') : (trailing ? trailing[1].trim() : ""),
      column: col ? col[1].replace(/\\"/g, '"') : "",
      ariaRef: ref[1],
      indent: indentOf(line),
      line: String(line || ""),
    });
  }
  return annotateColumns(nodes);
}

export function goalExactLines(nodes, goalText, overlayRefs = []) {
  const goal = String(goalText || "");
  const inGoal = (value) => String(value || "").length >= 2 && goal.includes(value);
  const hits = (nodes || []).filter((node) => inGoal(node.name) || inGoal(node.column));
  const covered = new Set(hits.map((node) => node.column).filter(Boolean));
  const overlay = new Set((overlayRefs || []).map((ref) => String(ref || "")));
  return hits
    .filter((node) => !(node.role === "columnheader" && covered.has(node.name)))
    .sort((a, b) => Number(overlay.has(b.ref || b.ariaRef)) - Number(overlay.has(a.ref || a.ariaRef)));
}

export function sameColumnRefs(snapshotText, headerLabel) {
  const label = String(headerLabel || "");
  if (!label.startsWith("columnheader")) return [];
  const name = (label.match(/"([^"]+)"/) || [])[1] || "";
  if (name.length < 2) return [];
  const refs = [];
  for (const line of String(snapshotText || "").split("\n")) {
    if (/columnheader/i.test(line) || !line.includes(name)) continue;
    const ref = publishedRef(line);
    if (ref) refs.push(ref);
  }
  return refs.slice(0, 8);
}

export function publishedRef(line) {
  const raw = String(line || "");
  const bracket = raw.match(/\[ref=([^\]]+)\]/);
  if (bracket) return bracket[1];
  const bare = raw.match(/\bref=(f\d+:e\d+@\d+)/i);
  return bare ? bare[1] : "";
}

export function annotateColumns(nodes) {
  const headers = [];
  let column = 0;
  let readingHeaders = true;
  let cellIndent = -1;
  let cellColumn = "";
  for (const node of nodes) {
    const indent = Number(node.indent || 0);
    if (cellIndent >= 0 && indent <= cellIndent) {
      cellIndent = -1;
      cellColumn = "";
    }
    if (node.role === "columnheader") {
      if (!readingHeaders) {
        headers.length = 0;
        column = 0;
        readingHeaders = true;
      }
      headers.push(node.name || "");
      continue;
    }
    if (node.role === "row" || node.role === "rowgroup") {
      column = 0;
      cellIndent = -1;
      cellColumn = "";
      if (headers.length) readingHeaders = false;
      continue;
    }
    if ((node.role === "cell" || node.role === "gridcell") && headers.length) {
      readingHeaders = false;
      node.column = headers[column] || node.column || "";
      cellIndent = indent;
      cellColumn = node.column;
      column += 1;
      if (column >= headers.length) column = 0;
      continue;
    }
    if (cellColumn && indent > cellIndent) node.column = cellColumn;
  }
  return nodes;
}

export function overlayExactBlock(snapshotText) {
  const text = String(snapshotText || "");
  const start = text.search(/(?:^|\n)overlay_exact\n/);
  if (start < 0) return "";
  const from = text.slice(start);
  const end = from.search(/\n(?:goal_exact|row_facts|frame )\b/);
  return (end < 0 ? from : from.slice(0, end)).split("\n").slice(0, 160).join("\n");
}

export function overlayHasRef(snapshotText, ref) {
  const raw = String(ref || "").trim().replace(/^\[ref=/, "").replace(/\]$/, "");
  if (!raw) return false;
  const block = overlayExactBlock(snapshotText);
  return block.includes(`[ref=${raw}]`) || block.includes(raw);
}

export function stripSnapshotDecorators(yaml) {
  return String(yaml || "").replace(/\s*\[cursor=[^\]]+\]/g, "");
}

function ancestorRoles(lines, index) {
  const roles = [];
  let depth = indentOf(lines[index]);
  for (let i = index - 1; i >= 0 && depth > 0; i -= 1) {
    const ind = indentOf(lines[i]);
    if (ind >= depth) continue;
    depth = ind;
    const role = (unwrapLine(lines[i]).match(/^([a-zA-Z]+)/) || [])[1];
    if (role) roles.push(role.toLowerCase());
  }
  return roles;
}

function takeOverlaySubtree(lines, start) {
  const out = [lines[start]];
  const base = indentOf(lines[start]);
  let i = start + 1;
  while (i < lines.length && indentOf(lines[i]) > base) {
    if (/\[ref=/.test(lines[i])) out.push(lines[i]);
    i += 1;
  }
  return { lines: out, next: i };
}

function valueControlLines(lines) {
  const out = [];
  for (let i = 0; i < lines.length; i += 1) {
    if (!/\[ref=/.test(lines[i])) continue;
    const role = ((unwrapLine(lines[i]).match(/^([a-zA-Z]+)/) || [])[1] || "");
    if (!VALUE_ROLES.test(role)) continue;
    if (ancestorRoles(lines, i).some((item) => PAGE_CHROME.test(item))) continue;
    out.push(lines[i]);
    if (out.length >= 160) break;
  }
  return out;
}

export function overlayExactLines(yaml) {
  const lines = String(yaml || "").split("\n");
  const modals = [];
  const popups = [];
  let i = 0;
  while (i < lines.length) {
    const raw = unwrapLine(lines[i]);
    if (OVERLAY_ROOT.test(raw) && /\[ref=/.test(lines[i])) {
      const role = ((raw.match(/^([a-zA-Z]+)/) || [])[1] || "").toLowerCase();
      const chrome = ancestorRoles(lines, i).some((item) => CHROME_LANDMARK.test(item));
      const taken = takeOverlaySubtree(lines, i);
      if (role === "menu" && chrome) {
        i = taken.next;
        continue;
      }
      if (OVERLAY_MODAL.test(role)) modals.push(...taken.lines);
      else popups.push(...taken.lines);
      i = taken.next;
      continue;
    }
    i += 1;
  }
  const overlay = (modals.length ? [...modals, ...popups] : popups).slice(0, 160);
  return overlay.length ? overlay : valueControlLines(lines);
}

function rowFactsFromYaml(yaml) {
  const lines = String(yaml || "").split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const raw = unwrapLine(line);
    if (ROW_ROLE.test(raw) && /\[ref=/.test(line)) {
      const base = indentOf(line);
      const cells = [];
      let j = i + 1;
      while (j < lines.length && indentOf(lines[j]) > base) {
        const cell = unwrapLine(lines[j]);
        if (CELL_ROLE.test(cell) && /\[ref=/.test(lines[j])) cells.push(lines[j].trim());
        j += 1;
      }
      if (cells.length) out.push(`- ${raw} :: ${cells.join(" | ")}`);
      i = j;
      continue;
    }
    i += 1;
  }
  return out.slice(0, 40);
}

export function yamlFingerprint(text) {
  return String(text || "")
    .replace(/^epoch \d+ snap \d+\n/, "")
    .replace(/@\d+/g, "@");
}

export function nearestNamedAncestor(yaml, index) {
  const lines = Array.isArray(yaml) ? yaml : String(yaml || "").split("\n");
  let depth = indentOf(lines[index]);
  for (let i = index - 1; i >= 0 && depth > 0; i -= 1) {
    const ind = indentOf(lines[i]);
    if (ind >= depth) continue;
    depth = ind;
    const named = unwrapLine(lines[i]).match(/^[a-zA-Z]+ "((?:\\.|[^"])*)"/);
    if (named) return named[1].replace(/\\"/g, '"');
  }
  return "";
}

function rewriteYaml(yaml, frame, frameId, snap, epoch, refs) {
  const source = stripSnapshotDecorators(yaml).split("\n");
  const lines = [];
  source.forEach((line, index) => {
    const next = line.replace(/\[ref=([^\]]+)\]/g, (_, aria) => {
      const ref = `${frameId}:${aria}@${snap}`;
      const raw = unwrapLine(line);
      const named = raw.match(/^[a-zA-Z]+ "((?:\\.|[^"])*)"/);
      const role = (raw.match(/^([a-zA-Z]+)/) || [])[1] || "";
      const parent = nearestNamedAncestor(source, index);
      const text = named ? named[1].replace(/\\"/g, '"') : "";
      const label = text && parent
        ? `${role} "${text}" in "${parent}"`
        : text
          ? `${role} "${text}"`
          : (parent ? `${role} in "${parent}"` : role);
      refs.set(ref, {
        locator: frame.locator(`aria-ref=${aria}`),
        epoch,
        snap,
        frame,
        label,
      });
      return `[ref=${ref}]`;
    });
    lines.push(next);
  });
  const rewritten = lines.join("\n");
  applyColumnLabels(rewritten, refs);
  return withColumnMarks(rewritten);
}

function applyColumnLabels(yaml, refs) {
  for (const node of parseNodes(yaml)) {
    if (!node.column) continue;
    const hit = refs.get(node.ariaRef);
    if (!hit) continue;
    if (String(hit.label || "").includes(node.column)) continue;
    hit.label = hit.label ? `${hit.label} col "${node.column}"` : `col "${node.column}"`;
  }
}

export function withColumnMarks(yaml) {
  const source = String(yaml || "").split("\n");
  const columns = new Map();
  for (const node of parseNodes(source.join("\n"))) {
    if (node.column && node.ariaRef) columns.set(node.ariaRef, node.column);
  }
  return source.map((line) => {
    const ref = publishedRef(line);
    const column = columns.get(ref);
    if (!column) return line;
    const mark = `col "${String(column).replace(/"/g, '\\"')}"`;
    if (line.includes(mark)) return line;
    return line.replace(/\[ref=/, `${mark} [ref=`);
  }).join("\n");
}

async function ariaYaml(frame, budgetMs) {
  const wait = Math.max(400, Number(budgetMs) || 8000);
  try {
    return await Promise.race([
      frame.locator("body").ariaSnapshot({ mode: "ai", timeout: wait }),
      new Promise((resolve) => setTimeout(() => resolve(""), wait)),
    ]);
  } catch {
    return "";
  }
}

export async function takeSnapshot(recordingId, { budgetMs = 8000 } = {}) {
  const state = browserSession(recordingId);
  if (!state) return { ok: false, error: "no_browser", epoch: 0, text: "", refs: [] };
  const frames = state.page.frames();
  const main = state.page.mainFrame();
  const rest = frames.filter((frame) => frame !== main).sort((a, b) => frameKey(a).localeCompare(frameKey(b)));
  const ordered = [main, ...rest];
  state.refs.clear();
  let goalText = "";
  try {
    const goal = await readGoal(recordingId);
    goalText = `${goal.goal_text || ""}`;
  } catch {
    goalText = "";
  }
  state.snap = (state.snap || 0) + 1;
  const lines = [`epoch ${state.epoch} snap ${state.snap}`];
  const exactNodes = [];
  const overlay = [];
  const rows = [];
  const refs = [];
  const perFrame = Math.max(800, Math.floor((Number(budgetMs) || 8000) / Math.max(1, ordered.length)));
  for (let index = 0; index < ordered.length; index += 1) {
    const frame = ordered[index];
    const frameId = `f${index}`;
    lines.push(`frame ${frameId} ${frame.url()}`);
    const yaml = await ariaYaml(frame, perFrame);
    const rewritten = rewriteYaml(yaml, frame, frameId, state.snap, state.epoch, state.refs);
    overlay.push(...overlayExactLines(rewritten));
    rows.push(...rowFactsFromYaml(rewritten));
    for (const node of parseNodes(rewritten)) {
      const shown = node.column ? `${node.role}${node.name ? ` "${node.name}"` : ""} col "${node.column}"` : (node.name ? `${node.role} "${node.name}"` : node.role);
      exactNodes.push({
        role: node.role,
        name: node.name || "",
        column: node.column || "",
        shown,
        ref: node.ariaRef,
      });
      refs.push(node.ariaRef);
    }
    if (rewritten) lines.push(rewritten);
  }
  const overlayRefs = overlay.map((line) => publishedRef(line)).filter(Boolean);
  const hits = goalExactLines(exactNodes, goalText, overlayRefs);
  state.seenGoalNames = Array.isArray(state.seenGoalNames) ? state.seenGoalNames : [];
  for (const node of hits) {
    for (const raw of [node.name, node.column]) {
      const name = String(raw || "").trim();
      if (name.length < 2 || /^\d+(\.\d+)?%?$/.test(name)) continue;
      if (!state.seenGoalNames.includes(name)) state.seenGoalNames.push(name);
    }
  }
  const exact = hits.map((node) => `- ${node.shown} [ref=${node.ref}]`);
  const lookup = [];
  if (exact.length) lookup.push("goal_exact", ...exact);
  if (overlay.length) lookup.push("overlay_exact", ...overlay.map((line) => line.trimStart()));
  if (rows.length) lookup.push("row_facts", ...rows);
  if (lookup.length) lines.splice(1, 0, ...lookup);
  const snapshot = { epoch: state.epoch, text: lines.join("\n"), refs };
  state.lastPublished = snapshot;
  return snapshot;
}

const UNFILLED_PROMPT = /^(请选|请输入|请填写|请填入|placeholder)/i;

export function controlLooksEmpty(value, placeholder = "", ...names) {
  const v = String(value ?? "").trim();
  if (!v) return true;
  const mark = String(placeholder || "").trim();
  if (mark && v === mark) return true;
  if (UNFILLED_PROMPT.test(v)) return true;
  return names.some((name) => {
    const label = String(name || "").trim();
    return Boolean(label) && v === label && UNFILLED_PROMPT.test(label);
  });
}

export function markRequired(ariaRequired, htmlRequired, ...labels) {
  if (htmlRequired) return true;
  const aria = String(ariaRequired ?? "").trim().toLowerCase();
  if (aria === "true") return true;
  if (aria === "false") return false;
  return labels.some((label) => /\*/.test(String(label || "")));
}

export function markReadonly(ariaReadonly, htmlReadonly, ariaDisabled) {
  if (htmlReadonly) return true;
  const aria = String(ariaReadonly ?? "").trim().toLowerCase();
  if (aria === "true") return true;
  return String(ariaDisabled ?? "").trim().toLowerCase() === "true";
}

export function overlayAlignRows(fields) {
  return (Array.isArray(fields) ? fields : []).map((row) => ({
    ref: row.ref || "",
    role: row.role || "",
    name: row.name || "",
    column: row.column || "",
    value: row.value ?? "",
    input: row.input || "",
    empty: Boolean(row.empty),
    required: Boolean(row.required),
    readonly: Boolean(row.readonly),
  }));
}

export function overlayItemLabel(item) {
  if (item && typeof item === "object") {
    const name = String(item.name || "").trim();
    const role = String(item.role || "").trim();
    const ref = String(item.ref || "").trim();
    const head = name && role && name !== role ? `${role} ${name}` : (name || role);
    return [head, ref].filter(Boolean).join(" ");
  }
  return String(item || "");
}

export function overlayItemsLine(items, limit = 0) {
  const labels = (Array.isArray(items) ? items : []).map(overlayItemLabel).filter(Boolean);
  return (limit > 0 ? labels.slice(0, limit) : labels).join("|");
}

export function overlayFactLists(overlay_fields) {
  const rows = overlayAlignRows(overlay_fields);
  const editable = rows.filter((row) => !row.readonly);
  return {
    overlay_fields: rows,
    overlay_editable: editable,
    empty_overlay: rows.filter((row) => row.empty).map((row) => row.name || row.role),
    required_empty_overlay: rows.filter((row) => row.empty && row.required).map((row) => ({
      name: row.name || row.role || "",
      role: row.role || "",
      ref: row.ref || "",
    })),
  };
}

async function readControlFact(locator, meta) {
  try {
    return await locator.evaluate((node, info) => {
      const el = node instanceof Element ? node : null;
      if (!el) return { skip: true };
      if (!el.getClientRects().length) return { skip: true };
      const nested = el.matches("input,select,textarea") ? el : el.querySelector("input,select,textarea");
      const native = el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT"
        || el.getAttribute("contenteditable") === "true"
        || Boolean(nested && (nested.tagName === "INPUT" || nested.tagName === "TEXTAREA" || nested.tagName === "SELECT"));
      const haspopup = el.getAttribute("aria-haspopup");
      const expanded = el.getAttribute("aria-expanded");
      if (!info.valueRole && !native && haspopup == null && expanded == null && !info.genericNamed) return { skip: true };
      // 选择类包裹层里的过滤框是同一个控件的一部分，只报包裹层这一行
      const wrapper = el.parentElement ? el.parentElement.closest('[role="combobox"],[role="listbox"]') : null;
      if (wrapper && wrapper !== el) return { skip: true };
      let input = "";
      if (nested instanceof HTMLSelectElement) input = "select";
      else if (nested instanceof HTMLTextAreaElement) input = "textarea";
      else if (nested instanceof HTMLInputElement) input = nested.type || "text";
      const placeholder = el.getAttribute("placeholder")
        || el.getAttribute("aria-placeholder")
        || (nested && nested.getAttribute("placeholder"))
        || "";
      let value = "";
      if (el.getAttribute("aria-valuenow") != null) value = String(el.getAttribute("aria-valuenow"));
      else if (el.getAttribute("aria-checked") != null) value = String(el.getAttribute("aria-checked"));
      else if (nested && "value" in nested) {
        value = String(nested.value ?? "");
        // 选择类控件常把选中项画在包裹层或兄弟节点上；role 在 input 自己身上时 value 仍是空过滤框
        if (!value.trim() && (info.choiceRole || haspopup != null || expanded != null)) {
          let host = el;
          if (el.matches("input,select,textarea")) {
            host = el.closest('[role="combobox"],[role="listbox"]');
            if (!host || host === el) host = el.parentElement || el;
          }
          const text = String((host || el).innerText || "").trim();
          const ph = String(placeholder || "");
          const parts = text.split(/\s+/).map((part) => part.trim()).filter(Boolean);
          value = parts.find((part) => part && part !== ph && !/^(请选|请输入|请填写|请填入)/.test(part)) || "";
        }
      } else value = String(el.innerText || "").trim();
      return {
        skip: false,
        value,
        placeholder,
        input,
        ariaRequired: el.getAttribute("aria-required"),
        htmlRequired: Boolean(el.required),
        ariaReadonly: el.getAttribute("aria-readonly"),
        htmlReadonly: Boolean(el.readOnly),
        ariaDisabled: el.getAttribute("aria-disabled"),
      };
    }, meta);
  } catch {
    return { skip: true };
  }
}

export async function overlayControlFacts(recordingId) {
  const state = browserSession(recordingId);
  const published = String(state?.lastPublished?.text || "");
  const block = overlayExactBlock(published).split("\n").filter((line) => line && !/^overlay_exact\s*$/.test(line.trim()));
  const jobs = [];
  for (const line of block) {
    const raw = unwrapLine(line);
    const role = (raw.match(/^([a-zA-Z]+)/) || [])[1] || "";
    const ref = publishedRef(line);
    if (!ref || NOT_VALUE_ROLE.test(role)) continue;
    const named = raw.match(/^[a-zA-Z]+ "((?:\\.|[^"])*)"/);
    const col = raw.match(/\scol "((?:\\.|[^"])*)"/);
    const label = named ? named[1].replace(/\\"/g, '"') : "";
    const column = col ? col[1].replace(/\\"/g, '"') : "";
    const hit = locatorFor(recordingId, ref);
    if (!hit) continue;
    jobs.push({
      ref,
      role: role.toLowerCase(),
      label,
      column,
      name: hit.label || label || role,
      locator: hit.locator,
    });
    if (jobs.length >= 160) break;
  }
  const rows = [];
  for (let i = 0; i < jobs.length; i += 24) {
    const part = await Promise.all(jobs.slice(i, i + 24).map(async (job) => {
      const raw = await readControlFact(job.locator, {
        valueRole: VALUE_ROLES.test(job.role),
        choiceRole: /^(combobox|listbox)$/.test(job.role),
        genericNamed: job.role === "generic" && Boolean(job.label),
      });
      if (!raw || raw.skip) return null;
      return {
        ref: job.ref,
        role: job.role,
        name: job.name,
        value: raw.value,
        ...(raw.placeholder ? { placeholder: raw.placeholder } : {}),
        ...(job.column ? { column: job.column } : {}),
        ...(raw.input ? { input: raw.input } : {}),
        empty: controlLooksEmpty(raw.value, raw.placeholder, job.label, job.column),
        required: markRequired(raw.ariaRequired, raw.htmlRequired, job.label, job.column, job.name),
        readonly: markReadonly(raw.ariaReadonly, raw.htmlReadonly, raw.ariaDisabled),
      };
    }));
    for (const row of part) if (row) rows.push(row);
  }
  return rows.slice(0, 160);
}

export function locatorFor(recordingId, ref) {
  const state = browserSession(recordingId);
  let raw = String(ref || "").trim().replace(/^\[ref=/, "").replace(/\]$/, "");
  let key = /^e\d+$/i.test(raw) ? `f0:${raw}` : raw;
  if (/^f\d+:e\d+$/i.test(key)) key = `${key}@${state.snap}`;
  const hit = state?.refs.get(key);
  if (!hit || hit.epoch !== state.epoch || hit.snap !== state.snap) return null;
  return hit;
}
