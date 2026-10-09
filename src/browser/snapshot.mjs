import { browserSession } from "./session.mjs";
import { readGoal } from "../evidence/store.mjs";

const OVERLAY_ROLE = /^(dialog|alertdialog|listbox|menu|tooltip)\b/i;
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

export function overlayHasRef(snapshotText, ref) {
  const raw = String(ref || "").trim().replace(/^\[ref=/, "").replace(/\]$/, "");
  if (!raw) return false;
  const text = String(snapshotText || "");
  const start = text.search(/(?:^|\n)overlay_exact\n/);
  if (start < 0) return false;
  const from = text.slice(start);
  const end = from.search(/\n(?:goal_exact|row_facts|frame )\b/);
  const block = end < 0 ? from : from.slice(0, end);
  return block.includes(`[ref=${raw}]`) || block.includes(raw);
}

export function overlayExactLines(yaml) {
  const lines = String(yaml || "").split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const raw = unwrapLine(lines[i]);
    if (OVERLAY_ROLE.test(raw) && /\[ref=/.test(lines[i])) {
      const base = indentOf(lines[i]);
      out.push(lines[i]);
      i += 1;
      while (i < lines.length && indentOf(lines[i]) > base) {
        if (/\[ref=/.test(lines[i])) out.push(lines[i]);
        i += 1;
      }
      continue;
    }
    i += 1;
  }
  return out.slice(0, 80);
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

function ancestorName(lines, index) {
  const base = indentOf(lines[index]);
  for (let i = index - 1; i >= 0; i -= 1) {
    if (indentOf(lines[i]) >= base) continue;
    const named = unwrapLine(lines[i]).match(/^[a-zA-Z]+ "((?:\\.|[^"])*)"/);
    return named ? named[1].replace(/\\"/g, '"') : "";
  }
  return "";
}

function rewriteYaml(yaml, frame, frameId, snap, epoch, refs) {
  const source = String(yaml || "").split("\n");
  const lines = [];
  source.forEach((line, index) => {
    const next = line.replace(/\[ref=([^\]]+)\]/g, (_, aria) => {
      const ref = `${frameId}:${aria}@${snap}`;
      const raw = unwrapLine(line);
      const named = raw.match(/^[a-zA-Z]+ "((?:\\.|[^"])*)"/);
      const role = (raw.match(/^([a-zA-Z]+)/) || [])[1] || "";
      const parent = ancestorName(source, index);
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
  if (!state) return { epoch: 0, text: "", refs: [] };
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
  const exact = goalExactLines(exactNodes, goalText, overlayRefs).map((node) => `- ${node.shown} [ref=${node.ref}]`);
  const lookup = [];
  if (exact.length) lookup.push("goal_exact", ...exact);
  if (overlay.length) lookup.push("overlay_exact", ...overlay.map((line) => line.trimStart()));
  if (rows.length) lookup.push("row_facts", ...rows);
  if (lookup.length) lines.splice(1, 0, ...lookup);
  const snapshot = { epoch: state.epoch, text: lines.join("\n"), refs };
  state.lastPublished = snapshot;
  return snapshot;
}

const VALUE_ROLES = /^(textbox|searchbox|combobox|spinbutton|checkbox|radio|slider|listbox)$/i;

export function controlLooksEmpty(value, placeholder = "", ...names) {
  const v = String(value ?? "").trim();
  if (!v) return true;
  const mark = String(placeholder || "").trim();
  if (mark && v === mark) return true;
  return names.some((name) => {
    const label = String(name || "").trim();
    return Boolean(label) && v === label;
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

export function overlayFactLists(overlay_fields) {
  const rows = Array.isArray(overlay_fields) ? overlay_fields : [];
  return {
    overlay_fields: rows,
    empty_overlay: rows.filter((row) => row.empty).map((row) => row.name || row.role),
    required_empty_overlay: rows.filter((row) => row.empty && row.required).map((row) => row.name || row.role),
  };
}

async function placeholderOf(locator) {
  const named = await locator.getAttribute("placeholder").catch(() => null);
  if (named) return String(named);
  const aria = await locator.getAttribute("aria-placeholder").catch(() => null);
  return aria ? String(aria) : "";
}

async function overlayValueControl(locator, role, named) {
  if (VALUE_ROLES.test(role)) return true;
  const haspopup = await locator.getAttribute("aria-haspopup").catch(() => null);
  const expanded = await locator.getAttribute("aria-expanded").catch(() => null);
  if (haspopup != null || expanded != null) return true;
  return /^generic$/i.test(role) && Boolean(named);
}

async function readControlValue(locator) {
  const placeholder = await placeholderOf(locator);
  const aria = await locator.getAttribute("aria-valuenow").catch(() => null);
  if (aria != null) return { value: String(aria), placeholder };
  const checked = await locator.getAttribute("aria-checked").catch(() => null);
  if (checked != null) return { value: checked, placeholder };
  try {
    return { value: String(await locator.inputValue()), placeholder };
  } catch {
    const attr = await locator.getAttribute("value").catch(() => null);
    if (attr != null) return { value: String(attr), placeholder };
    return { value: String(await locator.innerText().catch(() => "")).trim(), placeholder };
  }
}

export async function overlayControlFacts(recordingId) {
  const state = browserSession(recordingId);
  const published = String(state?.lastPublished?.text || "");
  const start = published.search(/(?:^|\n)overlay_exact\n/);
  if (start < 0) return [];
  const from = published.slice(start);
  const end = from.search(/\n(?:goal_exact|row_facts|frame )\b/);
  const block = (end < 0 ? from : from.slice(0, end)).split("\n").slice(1);
  const facts = [];
  for (const line of block) {
    const raw = unwrapLine(line);
    const role = (raw.match(/^([a-zA-Z]+)/) || [])[1] || "";
    const ref = publishedRef(line);
    if (!ref) continue;
    const named = raw.match(/^[a-zA-Z]+ "((?:\\.|[^"])*)"/);
    const col = raw.match(/\scol "((?:\\.|[^"])*)"/);
    const label = named ? named[1].replace(/\\"/g, '"') : "";
    const column = col ? col[1].replace(/\\"/g, '"') : "";
    const hit = locatorFor(recordingId, ref);
    if (!hit) continue;
    if (!(await overlayValueControl(hit.locator, role, label))) continue;
    const read = await readControlValue(hit.locator);
    const ariaRequired = await hit.locator.getAttribute("aria-required").catch(() => null);
    const htmlRequired = await hit.locator.evaluate((el) => Boolean(el.required)).catch(() => false);
    const ariaReadonly = await hit.locator.getAttribute("aria-readonly").catch(() => null);
    const htmlReadonly = await hit.locator.evaluate((el) => Boolean(el.readOnly)).catch(() => false);
    const ariaDisabled = await hit.locator.getAttribute("aria-disabled").catch(() => null);
    facts.push({
      ref,
      role: role.toLowerCase(),
      name: hit.label || label || role,
      value: read.value,
      ...(read.placeholder ? { placeholder: read.placeholder } : {}),
      ...(column ? { column } : {}),
      empty: controlLooksEmpty(read.value, read.placeholder, label, column),
      required: markRequired(ariaRequired, htmlRequired, label, column, hit.label),
      readonly: markReadonly(ariaReadonly, htmlReadonly, ariaDisabled),
    });
    if (facts.length >= 40) break;
  }
  return facts;
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
