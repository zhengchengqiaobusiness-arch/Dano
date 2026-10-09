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
    const trailing = raw.match(/\]:\s*(.+)$/);
    nodes.push({
      role: role[1].toLowerCase(),
      name: named ? named[1].replace(/\\"/g, '"') : (trailing ? trailing[1].trim() : ""),
      ariaRef: ref[1],
      line: String(line || ""),
    });
  }
  return annotateColumns(nodes);
}

export function goalExactLines(nodes, goalText) {
  const goal = String(goalText || "");
  const inGoal = (value) => String(value || "").length >= 2 && goal.includes(value);
  const hits = (nodes || []).filter((node) => inGoal(node.name) || inGoal(node.column));
  const covered = new Set(hits.map((node) => node.column).filter(Boolean));
  return hits.filter((node) => !(node.role === "columnheader" && covered.has(node.name)));
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
  for (const node of nodes) {
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
      if (headers.length) readingHeaders = false;
      continue;
    }
    if ((node.role === "cell" || node.role === "gridcell") && headers.length) {
      readingHeaders = false;
      node.column = headers[column] || "";
      column += 1;
      if (column >= headers.length) column = 0;
    }
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
      const parent = named ? "" : ancestorName(source, index);
      const label = named ? `${role} "${named[1].replace(/\\"/g, '"')}"` : (parent ? `${role} in "${parent}"` : role);
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
  return lines.join("\n");
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
      const shown = node.column ? `${node.role}${node.name ? ` "${node.name}"` : ""} ${node.column}` : (node.name ? `${node.role} "${node.name}"` : node.role);
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
  const exact = goalExactLines(exactNodes, goalText).map((node) => `- ${node.shown} [ref=${node.ref}]`);
  const lookup = [];
  if (exact.length) lookup.push("goal_exact", ...exact);
  if (overlay.length) lookup.push("overlay_exact", ...overlay.map((line) => line.trimStart()));
  if (rows.length) lookup.push("row_facts", ...rows);
  if (lookup.length) lines.splice(1, 0, ...lookup);
  const snapshot = { epoch: state.epoch, text: lines.join("\n"), refs };
  state.lastPublished = snapshot;
  return snapshot;
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
