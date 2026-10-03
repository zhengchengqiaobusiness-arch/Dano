import { browserSession } from "./session.mjs";
import { readGoal } from "../evidence/store.mjs";

function unwrapQuoted(text) {
  const value = String(text || "").trim();
  if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
    return value.slice(1, -1);
  }
  return value;
}

function lineName(raw) {
  const named = raw.match(/^[a-zA-Z]+ "((?:\\.|[^"])*)"/);
  if (named) return named[1].replace(/\\"/g, '"');
  const trailing = raw.match(/\]:\s*(.+)$/);
  if (trailing) return unwrapQuoted(trailing[1].trim());
  const loose = raw.match(/^[a-zA-Z]+:\s*(.+)$/);
  if (!loose) return "";
  return unwrapQuoted(loose[1].replace(/\s*\[ref=[^\]]+\].*$/, "").trim());
}

export function parseNodes(yaml) {
  const nodes = [];
  const stack = [];
  for (const line of String(yaml || "").split(/\n/)) {
    if (!line.trim()) continue;
    const depth = Math.floor((line.match(/^\s*/)[0] || "").length / 2);
    let raw = line.trim().replace(/^-\s*/, "");
    if (raw.startsWith("'") && raw.endsWith("'")) raw = raw.slice(1, -1).trim();
    if (raw.startsWith("- ")) raw = raw.slice(2).trim();
    while (stack.length && stack[stack.length - 1].depth >= depth) stack.pop();
    const parent = stack.length ? stack[stack.length - 1].node : null;
    const prop = raw.match(/^\/([A-Za-z]+):\s*(.*)$/);
    if (prop) {
      if (parent && prop[1].toLowerCase() === "placeholder") {
        const text = unwrapQuoted(prop[2]);
        parent.placeholder = text;
        if (!parent.name) parent.name = text;
      }
      continue;
    }
    const role = raw.match(/^([a-zA-Z]+)/);
    if (!role) continue;
    const ref = raw.match(/\[ref=([^\]]+)\]/);
    const name = lineName(raw);
    if (!ref) {
      if (name && parent && !parent.name) parent.name = name;
      continue;
    }
    const node = { role: role[1].toLowerCase(), name, ariaRef: ref[1] };
    if (parent) {
      node.parent = parent;
      node.inCell = parent.role === "cell" || parent.role === "gridcell" || Boolean(parent.inCell);
    }
    nodes.push(node);
    stack.push({ depth, node });
  }
  return annotateColumns(nodes);
}

export function goalExactLines(nodes, goalText) {
  const goal = String(goalText || "");
  const inGoal = (value) => String(value || "").length >= 2 && goal.includes(value);
  const hits = (nodes || []).filter((node) => inGoal(node.name) || inGoal(node.column));
  const covered = new Set(hits.map((node) => node.column).filter(Boolean));
  const namedColumn = new Set(
    hits.filter((node) => node.name && node.column && node.role !== "generic").map((node) => node.column),
  );
  return hits.filter((node) => {
    if (node.role === "columnheader" && covered.has(node.name)) return false;
    if ((node.role === "cell" || node.role === "gridcell") && !node.name && namedColumn.has(node.column)) return false;
    const parentRole = node.parentRole || node.parent?.role;
    const inCell = node.inCell || parentRole === "cell" || parentRole === "gridcell";
    if (node.role === "generic" && inCell) return false;
    return true;
  });
}

export function lineRef(text) {
  const value = String(text || "");
  const bracket = value.match(/\[ref=([^\]]+)\]/);
  if (bracket) return bracket[1];
  const plain = value.match(/\bref=(f\d+:e\d+@\d+|e\d+)/i);
  return plain ? plain[1] : "";
}

export function sameColumnRefs(snapshotText, headerLabel) {
  const label = String(headerLabel || "");
  if (!label.startsWith("columnheader")) return [];
  const name = (label.match(/"([^"]+)"/) || [])[1] || "";
  if (name.length < 2) return [];
  const refs = [];
  for (const line of String(snapshotText || "").split("\n")) {
    if (/\bcolumnheader\b/i.test(line)) continue;
    const hasColumn = line.includes(`[column=${name}]`) || line.includes(` ${name} ref=`) || line.includes(` ${name} [ref=`);
    if (!hasColumn) continue;
    const ref = lineRef(line);
    if (ref) refs.push(ref);
  }
  return refs.slice(0, 8);
}

export function publishSnapshotYaml(yaml, frameId, frameIndex, snap) {
  const parsed = parseNodes(yaml);
  const byAria = new Map();
  for (const node of parsed) {
    if (node.ariaRef && !byAria.has(node.ariaRef)) byAria.set(node.ariaRef, node);
  }
  const nodes = [];
  const lines = [];
  for (const line of String(yaml || "").split("\n")) {
    const rawRef = (line.match(/\[ref=([^\]]+)\]/) || [])[1];
    if (!rawRef) {
      lines.push(line);
      continue;
    }
    const ref = publishedRef(frameId, rawRef, snap);
    const engine = engineAriaRef(frameIndex, rawRef);
    if (!ref || !engine) {
      lines.push(line);
      continue;
    }
    const node = byAria.get(rawRef) || { role: "", name: "", column: "" };
    let next = line.replace(`[ref=${rawRef}]`, `[ref=${ref}]`);
    if (node.column && !next.includes("[column=")) next += ` [column=${node.column}]`;
    lines.push(next);
    const label = node.name ? `${node.role} "${String(node.name).replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"` : (node.role || "");
    const shown = `${label}${node.column ? ` ${node.column}` : ""}`.trim();
    nodes.push({
      ref,
      engine,
      ariaRef: rawRef,
      role: node.role || "",
      name: node.name || "",
      column: node.column || "",
      parentRole: node.parentRole || "",
      inCell: Boolean(node.inCell),
      shown,
    });
  }
  return { text: lines.join("\n"), nodes };
}

export function annotateColumns(nodes) {
  const headers = [];
  let column = 0;
  let readingHeaders = true;
  let lastCell = null;
  for (const node of nodes) {
    if (node.role === "columnheader") {
      if (!readingHeaders) {
        headers.length = 0;
        column = 0;
        readingHeaders = true;
      }
      headers.push(node.name || "");
      lastCell = null;
      continue;
    }
    if (node.role === "row" || node.role === "rowgroup") {
      column = 0;
      lastCell = null;
      if (headers.length) readingHeaders = false;
      continue;
    }
    if ((node.role === "cell" || node.role === "gridcell") && headers.length) {
      readingHeaders = false;
      node.column = headers[column] || "";
      lastCell = node;
      column += 1;
      if (column >= headers.length) column = 0;
      continue;
    }
    if (lastCell?.column && node.name && (node.parent === lastCell || node.parent === lastCell.parent)) {
      node.column = lastCell.column;
    }
  }
  for (const node of nodes) {
    if (node.column) continue;
    let walk = node.parent;
    while (walk) {
      if (walk.column) {
        node.column = walk.column;
        break;
      }
      walk = walk.parent;
    }
  }
  for (const node of nodes) {
    node.parentRole = node.parent?.role || node.parentRole || "";
    delete node.parent;
  }
  return nodes;
}

function ownAriaRef(ariaRef) {
  const text = String(ariaRef || "");
  if (/^e\d+$/i.test(text)) return text;
  const nested = text.match(/^f\d+(e\d+)$/i);
  return nested ? nested[1] : "";
}

export function publishedRef(frameId, ariaRef, snap) {
  const nested = String(ariaRef || "").match(/^f(\d+)(e\d+)$/i);
  if (nested) return `f${nested[1]}:${nested[2]}@${snap}`;
  if (/^e\d+$/i.test(String(ariaRef || ""))) return `${frameId}:${ariaRef}@${snap}`;
  return "";
}

function engineAriaRef(frameIndex, ariaRef) {
  if (/^f\d+e\d+$/i.test(String(ariaRef || ""))) return String(ariaRef);
  const aria = ownAriaRef(ariaRef);
  if (!aria) return "";
  return frameIndex === 0 ? aria : `f${frameIndex}${aria}`;
}

function orderedFrames(page) {
  const main = page.mainFrame();
  return [main, ...page.frames().filter((frame) => frame !== main)];
}

function controlCount(yaml) {
  let count = 0;
  for (const line of String(yaml || "").split("\n")) {
    let raw = line.trim().replace(/^-\s*/, "");
    if (raw.startsWith("'") && raw.endsWith("'")) raw = raw.slice(1, -1).trim();
    if (raw.startsWith("- ")) raw = raw.slice(2).trim();
    if (raw.startsWith("/")) continue;
    const role = (raw.match(/^([a-zA-Z]+)/) || [])[1];
    if (!role) continue;
    if (/^(generic|document|none|webview|presentation|group|region|main|article)$/i.test(role)) continue;
    count += 1;
  }
  return count;
}

function yamlKey(rows) {
  return rows.map((row) => `${row.index}\n${String(row.yaml || "").replace(/\s*\[(active|focused)\]/g, "")}`).join("\n---\n");
}

function capturedControls(rows) {
  return rows.reduce((sum, row) => sum + controlCount(row.yaml), 0);
}

function treeThin(rows) {
  const bytes = rows.reduce((sum, row) => sum + String(row.yaml || "").length, 0);
  if (bytes >= 800) return false;
  return capturedControls(rows) === 0;
}

async function captureFrameYaml(ordered) {
  const rows = [];
  for (let index = 0; index < ordered.length; index += 1) {
    const frame = ordered[index];
    let yaml = "";
    try {
      yaml = await frame.locator("body").ariaSnapshot({ mode: "ai" });
    } catch {
      yaml = "";
    }
    rows.push({ index, yaml });
  }
  return rows;
}

export async function yamlFingerprint(recordingId) {
  const state = browserSession(recordingId);
  if (!state?.page) return "";
  return yamlKey(await captureFrameYaml(orderedFrames(state.page)));
}

export async function takeSnapshot(recordingId, { changedFrom, minObserve = 0 } = {}) {
  const state = browserSession(recordingId);
  if (!state) return { epoch: 0, text: "", refs: [] };
  const settleMs = 800;
  const pollMs = 200;
  const started = Date.now();
  let ordered = orderedFrames(state.page);
  let captured = await captureFrameYaml(ordered);
  let lastKey = yamlKey(captured);
  let lastChange = started;
  const changeDeadline = changedFrom ? started + 4000 : started;
  const observeUntil = started + Math.max(0, Number(minObserve) || 0);
  const deadline = started + (treeThin(captured) ? 20000 : Math.max(4000, (Number(minObserve) || 0) + settleMs));
  while (true) {
    const now = Date.now();
    const thin = treeThin(captured);
    const awaitingChange = Boolean(changedFrom) && yamlKey(captured) === changedFrom && now < changeDeadline;
    const awaitingObserve = now < observeUntil;
    if (!awaitingChange && !awaitingObserve && !thin && now - lastChange >= settleMs) break;
    if (!awaitingChange && !awaitingObserve && now >= deadline) break;
    await new Promise((resolve) => setTimeout(resolve, pollMs));
    ordered = orderedFrames(state.page);
    captured = await captureFrameYaml(ordered);
    const key = yamlKey(captured);
    if (key !== lastKey) {
      lastKey = key;
      lastChange = Date.now();
    }
  }
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
  const refs = [];
  const register = (item, yamlIndex) => {
    if (!item.ref || !item.engine || state.refs.has(item.ref)) return false;
    const nested = String(item.ariaRef || "").match(/^f(\d+)/i);
    const ownerIndex = nested ? Number(nested[1]) : yamlIndex;
    const owner = ordered[ownerIndex] || ordered[yamlIndex];
    state.refs.set(item.ref, {
      locator: state.page.locator(`aria-ref=${item.engine}`),
      epoch: state.epoch,
      snap: state.snap,
      frame: owner,
      label: item.shown || item.ref,
    });
    exactNodes.push({
      role: item.role,
      name: item.name,
      column: item.column,
      parentRole: item.parentRole || "",
      inCell: Boolean(item.inCell),
      shown: item.shown || item.ref,
      ref: item.ref,
    });
    refs.push(item.ref);
    return true;
  };
  for (const row of captured) {
    const frameId = `f${row.index}`;
    const published = publishSnapshotYaml(row.yaml, frameId, row.index, state.snap);
    let added = 0;
    for (const item of published.nodes) {
      if (register(item, row.index)) added += 1;
    }
    if (!String(published.text || "").trim()) continue;
    if (row.index > 0 && added === 0) continue;
    const frame = ordered[row.index];
    lines.push(`frame ${frameId} ${frame ? frame.url() : ""}`);
    lines.push(published.text);
  }
  const exact = goalExactLines(exactNodes, goalText).map((node) => `- ${node.shown} ref=${node.ref}`);
  if (exact.length) lines.splice(1, 0, "goal_exact", ...exact);
  return { epoch: state.epoch, text: lines.join("\n"), refs };
}

export function locatorFor(recordingId, ref) {
  const state = browserSession(recordingId);
  const token = lineRef(ref) || String(ref || "").trim();
  let key = /^e\d+$/i.test(token) ? `f0:${token}` : token;
  if (/^f\d+:e\d+$/i.test(key)) key = `${key}@${state.snap}`;
  const hit = state?.refs.get(key);
  if (!hit || hit.epoch !== state.epoch || hit.snap !== state.snap) return null;
  return hit;
}
