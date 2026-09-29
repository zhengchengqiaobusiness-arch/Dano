import { browserSession } from "./session.mjs";
import { sealAction } from "./network.mjs";

export function frameLabel(url) {
  try {
    const parsed = new URL(url);
    return `${parsed.origin}${parsed.pathname}${parsed.hash}`;
  } catch {
    return String(url || "").replace(/\?[^#]*/, "");
  }
}

export function frameSortKey(frame) {
  let guid = "";
  let name = "";
  let url = "";
  try {
    guid = frame._guid || "";
  } catch {
    guid = "";
  }
  try {
    name = frame.name?.() || "";
  } catch {
    name = "";
  }
  try {
    url = frameLabel(typeof frame.url === "function" ? frame.url() : "");
  } catch {
    url = "";
  }
  const parent = frame.parentFrame?.();
  const parentId = parent?._guid || "";
  return `${guid}\n${name}\n${parentId}\n${url}`;
}

export function parseSnapshotYaml(yaml) {
  const nodes = [];
  for (const line of String(yaml || "").split(/\n/)) {
    if (!line.trim()) continue;
    const indent = line.match(/^\s*/)[0].length;
    let raw = line.trim().replace(/^-\s*/, "");
    if (raw.startsWith("'") && raw.endsWith("'")) raw = raw.slice(1, -1);
    const prop = raw.match(/^\/([A-Za-z]+):\s*(.*)$/);
    if (prop) {
      const depth = Math.floor(indent / 2);
      const owner = [...nodes].reverse().find((node) => (node.depth ?? 0) < depth);
      if (owner && prop[1].toLowerCase() === "placeholder") {
        let text = prop[2].trim();
        if ((text.startsWith('"') && text.endsWith('"')) || (text.startsWith("'") && text.endsWith("'"))) text = text.slice(1, -1);
        owner.placeholder = text;
      }
      continue;
    }
    const role = raw.match(/^([a-zA-Z]+)/);
    if (!role) continue;
    const ref = raw.match(/\[ref=([^\]]+)\]/);
    const named = raw.match(/^[a-zA-Z]+ "((?:\\.|[^"])*)"/);
    const value = (raw.match(/\]:\s*(.*)$/) || [])[1] || "";
    const states = [];
    for (const match of raw.matchAll(/\[([a-z]+)(?:=([^\]]+))?\]/gi)) {
      const key = match[1].toLowerCase();
      if (key === "ref" || key === "cursor" || key === "box") continue;
      states.push(match[2] ? `${key}=${match[2]}` : key);
    }
    nodes.push({
      role: role[1].toLowerCase(),
      name: named ? named[1].replace(/\\"/g, '"') : "",
      value: value.trim(),
      placeholder: "",
      states,
      ariaRef: ref ? ref[1] : "",
      depth: Math.floor(indent / 2),
    });
  }
  return annotatePopup(annotateRows(annotateColumns(nodes)));
}

export function annotatePopup(nodes) {
  const stack = [];
  const pageRoles = new Set(["navigation", "banner", "main", "complementary"]);
  for (const node of nodes || []) {
    const depth = Number.isInteger(node.depth) ? node.depth : 0;
    while (stack.length && stack[stack.length - 1].depth >= depth) stack.pop();
    const inPage = stack.some((item) => pageRoles.has(item.role));
    const dialog = node.role === "dialog" || node.role === "alertdialog";
    const floating = (node.role === "menu" || node.role === "listbox") && !inPage;
    const parentPopup = [...stack].reverse().find((item) => item.popup)?.popup || "";
    node.popup = dialog || floating ? (node.name || node.role) : parentPopup;
    stack.push({ depth, role: node.role, popup: node.popup || "" });
  }
  return nodes;
}

const FIELD_ROLES = new Set(["textbox", "searchbox", "combobox", "spinbutton", "slider", "checkbox", "radio", "switch"]);
const VALUE_ROLES = new Set(["textbox", "searchbox", "combobox", "spinbutton", "slider"]);

export function displayedValue(nodes, index) {
  const node = nodes[index];
  if (!node || node.role !== "combobox" || node.value) return "";
  const children = [];
  const siblings = [];
  let left = false;
  for (let i = index + 1; i < nodes.length; i += 1) {
    const item = nodes[i];
    const depth = item.depth ?? 0;
    if (depth < (node.depth ?? 0)) break;
    if (depth === (node.depth ?? 0)) {
      left = true;
      if (item.role === "generic") {
        if (item.name) siblings.push(item.name);
        continue;
      }
      break;
    }
    if (!left && depth === (node.depth ?? 0) + 1 && item.role === "generic" && item.name) children.push(item.name);
  }
  if (children.length === 1 && siblings.length === 0) return children[0];
  if (siblings.length === 1 && children.length === 0) return siblings[0];
  return "";
}

export function settledControlValue(role, snapshotValue, readValue) {
  const read = String(readValue || "");
  if ((role === "spinbutton" || role === "slider") && read) return read;
  return String(snapshotValue || "") || read;
}

export function applyDisplayedValues(nodes) {
  (nodes || []).forEach((node, index) => {
    const value = displayedValue(nodes, index);
    if (value) node.value = value;
  });
  return nodes;
}

export async function readControlValue(locator) {
  const type = await locator.getAttribute("type", { timeout: 300 }).catch(() => null);
  if (type === "password") return "";
  const ariaNow = await locator.getAttribute("aria-valuenow", { timeout: 300 }).catch(() => null);
  const ariaText = await locator.getAttribute("aria-valuetext", { timeout: 300 }).catch(() => null);
  if (ariaNow) return String(ariaNow);
  if (ariaText) return String(ariaText);
  try {
    const input = await locator.inputValue({ timeout: 300 });
    if (input) return String(input).replace(/\s+/g, " ").trim();
  } catch {
    return "";
  }
  return "";
}

export async function readFieldFacts(locator) {
  const [type, ariaRequired, required, ariaMin, ariaMax, min, max, step] = await Promise.all([
    locator.getAttribute("type", { timeout: 300 }).catch(() => null),
    locator.getAttribute("aria-required", { timeout: 300 }).catch(() => null),
    locator.getAttribute("required", { timeout: 300 }).catch(() => null),
    locator.getAttribute("aria-valuemin", { timeout: 300 }).catch(() => null),
    locator.getAttribute("aria-valuemax", { timeout: 300 }).catch(() => null),
    locator.getAttribute("min", { timeout: 300 }).catch(() => null),
    locator.getAttribute("max", { timeout: 300 }).catch(() => null),
    locator.getAttribute("step", { timeout: 300 }).catch(() => null),
  ]);
  return {
    value: type === "password" ? "" : await readControlValue(locator),
    required: ariaRequired === "true" || required != null,
    min: String(ariaMin || min || ""),
    max: String(ariaMax || max || ""),
    step: step == null ? "" : String(step),
  };
}

function linkParallelRows(nodes) {
  const groups = [];
  let current = null;
  for (const node of nodes) {
    if (node.role === "rowgroup") {
      current = { depth: node.depth, rows: [] };
      groups.push(current);
      continue;
    }
    if (!current || node.role !== "row" || node.depth !== current.depth + 1) continue;
    current.rows.push(node);
  }
  for (const group of groups) {
    if (group.rows.length < 2 || group.rows.some((row) => row.row)) continue;
    const donor = groups.filter((other) => other !== group && other.rows.length === group.rows.length && other.rows.some((row) => row.row) && groups.indexOf(other) < groups.indexOf(group)).pop();
    if (!donor) continue;
    group.rows.forEach((row, index) => {
      if (donor.rows[index]?.row) row.row = donor.rows[index].row;
    });
  }
  const stack = [];
  for (const node of nodes) {
    const depth = Number.isInteger(node.depth) ? node.depth : 0;
    while (stack.length && stack[stack.length - 1].depth >= depth) stack.pop();
    if (node.role === "row" && node.row) stack.push({ depth, row: node.row });
    const row = stack[stack.length - 1];
    if (row && !node.row && node.role !== "row" && node.role !== "rowgroup" && node.role !== "columnheader") node.row = row.row;
  }
  return nodes;
}

function annotateRows(nodes) {
  const stack = [];
  const close = (entry) => {
    if (entry?.node?.role === "row" && entry.names.length) entry.node.row = entry.names.join(" | ");
  };
  for (const node of nodes) {
    const depth = Number.isInteger(node.depth) ? node.depth : 0;
    while (stack.length && stack[stack.length - 1].depth >= depth) close(stack.pop());
    if (node.role === "row" || node.role === "rowgroup") stack.push({ depth, names: [], node });
    const row = [...stack].reverse().find((entry) => entry.node.role === "row");
    if (row && (node.role === "cell" || node.role === "gridcell") && depth === row.depth + 1) {
      const bit = node.name || node.value || "";
      if (bit) row.names.push(bit);
    }
  }
  while (stack.length) close(stack.pop());
  return linkParallelRows(nodes);
}

const CONTROL_ROLE = /(?:^|\s)(button|link|menuitem|tab|textbox|searchbox|combobox|spinbutton|slider|checkbox|radio|switch|treeitem|option|cell|gridcell)\b/;

export function includeInSnapshot(node) {
  if (!node) return false;
  if (node.ariaRef || node.ref) return true;
  return Boolean(node.name || node.placeholder || node.value);
}

export function refTail(ref) {
  return ref ? ` ref=${ref}` : "";
}

export function wrapperGeneric(nodes, index) {
  const node = nodes[index];
  if (!node || node.role !== "generic" || node.name || node.placeholder) return false;
  if (!(node.ref || node.ariaRef)) return false;
  const depth = node.depth ?? 0;
  for (let i = index + 1; i < nodes.length; i += 1) {
    const item = nodes[i];
    if ((item.depth ?? 0) <= depth) break;
    if ((item.ref || item.ariaRef) && (item.name || item.placeholder)) return true;
  }
  return false;
}

export function snapshotHasControl(text) {
  return CONTROL_ROLE.test(String(text || ""));
}

export function controlText(node, { row = false } = {}) {
  const label = node.name ? `${node.role} "${node.name}"` : node.role;
  const state = node.states?.length ? ` ${node.states.map((item) => `[${item}]`).join(" ")}` : "";
  const placeholder = node.placeholder ? ` placeholder="${node.placeholder}"` : "";
  const place = row && node.row ? ` row="${node.row}"` : "";
  const value = node.value ? `: ${node.value}` : "";
  const range = [node.min ? `min=${node.min}` : "", node.max ? `max=${node.max}` : "", node.step ? `step=${node.step}` : ""].filter(Boolean).join(" ");
  const popup = node.popup ? ` popup="${node.popup}"` : "";
  const column = node.column ? ` ${node.column}` : "";
  return `${label}${state}${placeholder}${place}${value}${range ? ` ${range}` : ""}${popup}${column}`;
}

function directRowCells(nodes, rowIndex) {
  const row = nodes[rowIndex];
  if (!row || row.role !== "row" || !Number.isInteger(row.depth)) return [];
  const cells = [];
  for (let i = rowIndex + 1; i < nodes.length; i += 1) {
    const node = nodes[i];
    if (!Number.isInteger(node.depth) || node.depth <= row.depth) break;
    if ((node.role === "cell" || node.role === "gridcell") && node.depth === row.depth + 1) cells.push(i);
  }
  return cells;
}

function extraSelectionCells(nodes, rowIndex, headerCount) {
  const cells = directRowCells(nodes, rowIndex);
  const skip = new Set();
  let budget = cells.length - headerCount;
  for (const index of cells) {
    if (budget <= 0) break;
    if (!selectionCell(nodes, index)) continue;
    skip.add(index);
    budget -= 1;
  }
  return skip;
}

function selectionCell(nodes, index) {
  const node = nodes[index];
  if (!node || (node.role !== "cell" && node.role !== "gridcell") || node.name) return false;
  const depth = node.depth || 0;
  let box = false;
  for (let i = index + 1; i < nodes.length; i += 1) {
    const child = nodes[i];
    if ((child.depth || 0) <= depth) break;
    if (child.role === "checkbox" || child.role === "radio") box = true;
    else if (child.name) return false;
  }
  return box;
}

export function annotateColumns(nodes) {
  let headers = [];
  let headerDepth = null;
  let column = 0;
  let rowDepth = null;
  let inBody = false;
  let skip = new Set();
  for (let index = 0; index < (nodes || []).length; index += 1) {
    const node = nodes[index];
    const depth = Number.isInteger(node.depth) ? node.depth : null;
    if (node.role === "columnheader") {
      const at = depth == null ? 0 : depth;
      if (headerDepth != null && at !== headerDepth) {
        if (at < headerDepth) {
          headers = [node.name || ""];
          headerDepth = at;
          inBody = false;
          column = 0;
        }
        continue;
      }
      if (inBody && headerDepth != null && at === headerDepth) {
        headers = [node.name || ""];
        inBody = false;
        column = 0;
      } else {
        headers.push(node.name || "");
      }
      headerDepth = at;
      continue;
    }
    if (node.role === "row" || node.role === "rowgroup") {
      column = 0;
      rowDepth = depth;
      skip = node.role === "row" && headers.length ? extraSelectionCells(nodes, index, headers.length) : new Set();
      if (headers.length) inBody = true;
      continue;
    }
    if ((node.role === "cell" || node.role === "gridcell") && headers.length) {
      if (depth != null && rowDepth != null && depth !== rowDepth + 1) continue;
      if (skip.has(index)) continue;
      if (column >= headers.length) continue;
      node.column = headers[column] || "";
      column += 1;
      inBody = true;
    }
  }
  return nodes;
}

function siblingNames(nodes, target) {
  if (!target) return [];
  const index = (nodes || []).indexOf(target);
  if (index < 0) return [];
  const depth = target.depth || 0;
  const names = [];
  for (let i = index - 1; i >= 0; i -= 1) {
    if ((nodes[i].depth || 0) < depth) break;
    if ((nodes[i].depth || 0) === depth && nodes[i].name) names.push(nodes[i].name);
  }
  for (let i = index + 1; i < nodes.length; i += 1) {
    if ((nodes[i].depth || 0) < depth) break;
    if ((nodes[i].depth || 0) === depth && nodes[i].name) names.push(nodes[i].name);
  }
  return names;
}

export function siblingValue(previous, next, field) {
  const same = (node) => node && node.role === field.role && node.name === field.name && (node.placeholder || "") === (field.placeholder || "") && (node.popup || "") === (field.popup || "");
  const beforeMatches = (previous || []).filter(same);
  const at = Math.max(0, beforeMatches.findIndex((node) => node.states?.includes("active")));
  const before = beforeMatches[at] || null;
  const now = (next || []).filter(same)[at];
  if (!now) return "";
  const oldNames = new Set(siblingNames(previous, before));
  return siblingNames(next, now).find((name) => !oldNames.has(name)) || "";
}

export function choiceClick(label) {
  return Boolean(label) && !/^(?:button|link|menuitem|tab|textbox|searchbox|combobox|spinbutton|slider|checkbox|radio|switch|columnheader)(?:\s|"|$)/.test(String(label));
}

function changedValue(item) {
  if (!item) return "";
  const states = (item.states || []).filter((state) => !["active", "expanded", "disabled", "required"].includes(state));
  return [states.join(" "), item.value].filter(Boolean).join(" ");
}

export function focusAfterChoice(held, previous, after) {
  const placed = (item, value) => ({ label: item.label, value, ...(item.popup ? { popup: item.popup } : {}), ...(item.row ? { row: item.row } : {}) });
  const applied = [];
  const beside = [];
  const unchanged = [];
  for (const item of held || []) {
    const kept = keptFocusValue(item, after);
    if (kept) {
      applied.push(placed(item, kept));
      continue;
    }
    if (String(item.value ?? "") !== String(item.before ?? "")) continue;
    const extra = siblingValue(previous, after, item);
    if (extra) beside.push(placed(item, extra));
    else unchanged.push(placed(item, item.value));
  }
  return {
    ...(unchanged.length ? { focused_unchanged: unchanged } : {}),
    ...(applied.length || beside.length ? { focused_value: [...applied, ...beside] } : {}),
  };
}

export function keptFocusValue(item, after) {
  if (!item) return "";
  const now = (after || []).find((node) => node.role === item.role && node.name === item.name && (node.placeholder || "") === (item.placeholder || "") && (node.popup || "") === (item.popup || ""));
  if (!now) return "";
  const kept = String(now.value || "");
  if (!kept || kept === String(item.before || "")) return "";
  return kept;
}

export function focusedUnchanged(previous, next) {
  const out = [];
  for (const old of previous || []) {
    if (!old?.states?.includes("active")) continue;
    if (!["textbox", "searchbox", "combobox"].includes(old.role)) continue;
    const now = (next || []).find((node) => node.role === old.role && node.name === old.name && (node.placeholder || "") === (old.placeholder || "") && (node.popup || "") === (old.popup || ""));
    if (!now) continue;
    if ((now.value || "") !== (old.value || "")) continue;
    out.push({ ref: now.ref, label: now.shown || old.shown || old.role, value: now.value || "" });
  }
  return out;
}

export function changedControls(previous, next) {
  if (!previous?.length) return [];
  const group = (nodes) => {
    const map = new Map();
    for (const node of nodes || []) {
      const key = [node.frameId || "", node.role || "", node.name || "", node.placeholder || "", node.column || "", node.popup || ""].join("\n");
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(node);
    }
    return map;
  };
  const before = group(previous);
  const after = group(next);
  const out = [];
  const keep = (node) => {
    if (!node) return false;
    if (node.placeholder || node.column) return true;
    if ((node.role === "generic" || node.role === "none" || node.role === "presentation") && !node.name) return false;
    return true;
  };
  const rank = (node) => {
    if (node?.role === "cell" || node?.role === "gridcell" || node?.role === "option" || node?.role === "treeitem") return 0;
    if (node?.role === "textbox" || node?.role === "button" || node?.role === "checkbox" || node?.role === "tab") return 1;
    return 2;
  };
  for (const [key, rows] of after) {
    const prev = before.get(key) || [];
    const count = Math.max(rows.length, prev.length);
    for (let index = 0; index < count; index += 1) {
      const now = rows[index];
      const old = prev[index];
      if (now && !keep(now)) continue;
      const afterValue = changedValue(now);
      const beforeValue = changedValue(old);
      const label = [now?.shown, now?.popup ? `popup="${now.popup}"` : "", now?.row ? `row="${now.row}"` : ""].filter(Boolean).join(" ");
      if (now && !old) out.push({ ref: now.ref, label, after: afterValue, rank: rank(now) });
      else if (now && old && afterValue !== beforeValue) out.push({ ref: now.ref, label, before: beforeValue, after: afterValue, rank: rank(now) });
    }
  }
  return out.sort((left, right) => left.rank - right.rank).slice(0, 80).map(({ rank: _rank, ...item }) => item);
}

function duplicateKey(node) {
  return `${node.frameId || ""}\n${node.role}\n${node.name}\n${node.popup || ""}`;
}

export function annotateDuplicatePaths(nodes) {
  const list = nodes || [];
  const counts = new Map();
  for (const node of list) {
    const key = duplicateKey(node);
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  list.forEach((node, index) => {
    if (node.row || !node.name) return;
    if ((counts.get(duplicateKey(node)) || 0) < 2) return;
    const names = [];
    let depth = node.depth || 0;
    for (let i = index - 1; i >= 0; i -= 1) {
      const item = list[i];
      if ((item.frameId || "") !== (node.frameId || "")) break;
      if ((item.popup || "") !== (node.popup || "")) break;
      if ((item.depth || 0) >= depth) continue;
      depth = item.depth || 0;
      if (item.name) names.unshift(item.name);
    }
    if (names.length) node.row = names.join(" / ");
  });
  const groups = new Map();
  for (const node of list) {
    const key = `${duplicateKey(node)}\n${node.row || ""}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(node);
  }
  for (const group of groups.values()) {
    if (group.length < 2 || !group[0].name) continue;
    group.forEach((node, index) => {
      node.row = node.row ? `${node.row} #${index + 1}` : `#${index + 1}`;
    });
  }
  return list;
}

export async function takeSnapshot(recordingId) {
  sealAction(recordingId);
  const state = browserSession(recordingId);
  if (!state) return { epoch: 0, text: "", refs: [] };
  const frames = state.page.frames();
  const main = state.page.mainFrame();
  const rest = frames.filter((frame) => frame !== main).sort((a, b) => frameSortKey(a).localeCompare(frameSortKey(b)));
  const ordered = [main, ...rest];
  state.refs.clear();
  state.snap = (state.snap || 0) + 1;
  const framesLines = [];
  const controls = [];
  const refs = [];
  for (let index = 0; index < ordered.length; index += 1) {
    const frame = ordered[index];
    const frameId = `f${index}`;
    framesLines.push({ kind: "frame", text: `frame ${frameId} ${frameLabel(frame.url())}` });
    let yaml = "";
    try {
      yaml = await frame.locator("body").ariaSnapshot({ mode: "ai" });
    } catch {
      yaml = "";
    }
    for (const node of parseSnapshotYaml(yaml)) {
      if (!includeInSnapshot(node)) continue;
      const ref = node.ariaRef ? `${frameId}:${node.ariaRef}@${state.snap}` : "";
      const shown = node.name
        ? `${node.role} "${node.name}"`
        : node.placeholder
          ? `${node.role} placeholder="${node.placeholder}"`
          : node.role;
      const withColumn = node.column ? `${shown} ${node.column}` : shown;
      if (ref) state.refs.set(ref, {
        locator: frame.locator(`aria-ref=${node.ariaRef}`),
        epoch: state.epoch,
        snap: state.snap,
        frame,
        label: withColumn,
        role: node.role,
        name: node.name || "",
        value: node.value || "",
        column: node.column || "",
        row: node.row || "",
        popup: node.popup || "",
      });
      const control = {
        frameId,
        role: node.role,
        name: node.name || "",
        value: node.value || "",
        placeholder: node.placeholder || "",
        states: node.states || [],
        column: node.column || "",
        popup: node.popup || "",
        row: node.row || "",
        depth: node.depth || 0,
        shown: withColumn,
        ref,
      };
      framesLines.push({ kind: "control", control });
      controls.push(control);
      if (ref) refs.push(ref);
    }
  }
  await Promise.all(controls.map(async (control) => {
    if (!FIELD_ROLES.has(control.role)) return;
    const hit = state.refs.get(control.ref);
    if (!hit?.locator) return;
    const facts = await readFieldFacts(hit.locator);
    if (VALUE_ROLES.has(control.role) && facts.value) control.value = settledControlValue(control.role, control.value, facts.value);
    if (facts.required && !control.states.includes("required")) control.states.push("required");
    if (facts.min) control.min = facts.min;
    if (facts.max) control.max = facts.max;
    if (facts.step) control.step = facts.step;
  }));
  annotateDuplicatePaths(controls);
  for (const control of controls) {
    const hit = state.refs.get(control.ref);
    if (hit && control.row) hit.row = control.row;
  }
  applyDisplayedValues(controls);
  const hidden = new Set();
  controls.forEach((control, index) => {
    if (wrapperGeneric(controls, index)) hidden.add(control);
  });
  if (hidden.size) {
    for (const control of hidden) {
      if (control.ref) state.refs.delete(control.ref);
    }
    for (let i = controls.length - 1; i >= 0; i -= 1) {
      if (hidden.has(controls[i])) controls.splice(i, 1);
    }
    for (let i = framesLines.length - 1; i >= 0; i -= 1) {
      if (framesLines[i].kind === "control" && hidden.has(framesLines[i].control)) framesLines.splice(i, 1);
    }
    for (let i = refs.length - 1; i >= 0; i -= 1) {
      if ([...hidden].some((control) => control.ref === refs[i])) refs.splice(i, 1);
    }
  }
  const changed = changedControls(state.controls, controls);
  state.controls = controls;
  const counts = new Map();
  for (const node of controls) {
    const key = `${node.role}\n${node.name}`;
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  const duplicated = (node) => (counts.get(`${node.role}\n${node.name}`) || 0) > 1;
  const fields = controls.filter((node) => FIELD_ROLES.has(node.role)).map((node) => `- ${controlText(node, { row: true })}${refTail(node.ref)}`);
  const textLines = [`epoch ${state.epoch} snap ${state.snap}`];
  if (fields.length) textLines.push("fields", ...fields);
  for (const line of framesLines) {
    if (line.kind === "frame") textLines.push(line.text);
    else textLines.push(`${"  ".repeat(line.control.depth)}- ${controlText(line.control, { row: duplicated(line.control) })}${refTail(line.control.ref)}`);
  }
  return { epoch: state.epoch, text: textLines.join("\n"), refs, ...(changed.length ? { changed } : {}) };
}

function labelHead(label) {
  const text = String(label || "").trim();
  const named = text.match(/^[a-zA-Z]+ "(?:\\.|[^"])*"/);
  if (named) return named[0];
  const placeholder = text.match(/^[a-zA-Z]+ placeholder="(?:\\.|[^"])*"/);
  if (placeholder) return placeholder[0];
  const role = text.match(/^[a-zA-Z]+/);
  return role ? role[0] : text;
}

function withoutEmbedded(label, extras) {
  const head = labelHead(label);
  const quoted = head.match(/^([a-zA-Z]+) "((?:\\.|[^"])*)"$/);
  if (!quoted) return head;
  let text = quoted[2];
  const list = [...new Set((extras || []).map((item) => String(item || "").trim()).filter((item) => item.length >= 6))]
    .sort((left, right) => right.length - left.length);
  for (const extra of list) {
    const suffix = ` ${extra}`;
    if (text.endsWith(suffix) && text.length > suffix.length) text = text.slice(0, -suffix.length).trim();
  }
  return `${quoted[1]} "${text}"`;
}

export function labelsMatch(expected, live, extras = []) {
  const want = String(expected || "").trim();
  const got = String(live || "").trim();
  if (!want || !got) return false;
  if (want === got) return true;
  const head = labelHead(want);
  return head === got || head === labelHead(got) || head === withoutEmbedded(got, extras);
}

export function labelFromSnapshot(yaml, expected = "") {
  const nodes = parseSnapshotYaml(yaml).filter((item) => item.role);
  if (!nodes.length) return "";
  const role = String(expected || "").split(/\s/)[0];
  const node = nodes.find((item) => item.role === role) || nodes[0];
  if (node.name) return `${node.role} "${node.name}"`;
  if (node.placeholder) return `${node.role} placeholder="${node.placeholder}"`;
  return node.role;
}

export async function currentControlLabel(locator, expected = "") {
  const yaml = await locator.ariaSnapshot({ mode: "ai" }).catch(() => "");
  return labelFromSnapshot(yaml, expected);
}

export function locatorFor(recordingId, ref) {
  const state = browserSession(recordingId);
  const raw = String(ref || "").trim();
  let key = /^e\d+$/i.test(raw) ? `f0:${raw}` : raw;
  if (/^f\d+:e\d+$/i.test(key)) key = `${key}@${state.snap}`;
  const hit = state?.refs.get(key);
  if (!hit || hit.epoch !== state.epoch || hit.snap !== state.snap) return null;
  return hit;
}
