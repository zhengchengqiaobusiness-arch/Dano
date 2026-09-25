import { browserSession } from "./session.mjs";

function frameKey(frame) {
  const parent = frame.parentFrame();
  const parentId = parent?._guid || parent?.url() || "";
  return `${parentId}\n${frame.url()}`;
}

function parseNodes(yaml) {
  const nodes = [];
  for (const line of String(yaml || "").split(/\n/)) {
    let raw = line.replace(/^\s*-\s*/, "").trim();
    if (raw.startsWith("'") && raw.endsWith("'")) raw = raw.slice(1, -1);
    const ref = raw.match(/\[ref=([^\]]+)\]/);
    const role = raw.match(/^([a-zA-Z]+)/);
    if (!ref || !role) continue;
    const named = raw.match(/^[a-zA-Z]+ "((?:\\.|[^"])*)"/);
    const trailing = raw.match(/\]:\s*(.+)$/);
    nodes.push({
      role: role[1].toLowerCase(),
      name: named ? named[1].replace(/\\"/g, '"') : (trailing ? trailing[1].trim() : ""),
      ariaRef: ref[1],
    });
  }
  return annotateColumns(nodes);
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

export async function takeSnapshot(recordingId) {
  const state = browserSession(recordingId);
  if (!state) return { epoch: 0, text: "", refs: [] };
  const frames = state.page.frames();
  const main = state.page.mainFrame();
  const rest = frames.filter((frame) => frame !== main).sort((a, b) => frameKey(a).localeCompare(frameKey(b)));
  const ordered = [main, ...rest];
  state.refs.clear();
  const lines = [`epoch ${state.epoch}`];
  const refs = [];
  for (let index = 0; index < ordered.length; index += 1) {
    const frame = ordered[index];
    const frameId = `f${index}`;
    lines.push(`frame ${frameId} ${frame.url()}`);
    let yaml = "";
    try {
      yaml = await frame.locator("body").ariaSnapshot({ mode: "ai" });
    } catch {
      yaml = "";
    }
    for (const node of parseNodes(yaml)) {
      const ref = `${frameId}:${node.ariaRef}`;
      const label = node.name ? `${node.role} "${node.name}"` : node.role;
      const column = node.column ? ` ${node.column}` : "";
      const shown = `${label}${column}`;
      state.refs.set(ref, {
        locator: frame.locator(`aria-ref=${node.ariaRef}`),
        epoch: state.epoch,
        frame,
        label: shown,
      });
      lines.push(`- ${shown} ref=${ref}`);
      refs.push(ref);
    }
  }
  return { epoch: state.epoch, text: lines.join("\n"), refs };
}

export function locatorFor(recordingId, ref) {
  const state = browserSession(recordingId);
  const raw = String(ref || "").trim();
  const key = /^e\d+$/i.test(raw) ? `f0:${raw}` : raw;
  const hit = state?.refs.get(key);
  if (!hit || hit.epoch !== state.epoch) return null;
  return hit;
}
