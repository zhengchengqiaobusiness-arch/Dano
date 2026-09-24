import { browserSession } from "./session.mjs";

const INTERACTIVE = new Set([
  "button", "link", "textbox", "checkbox", "radio", "combobox", "searchbox",
  "slider", "spinbutton", "tab", "menuitem", "option", "switch", "treeitem",
]);

function frameKey(frame) {
  const parent = frame.parentFrame();
  const parentId = parent?._guid || parent?.url() || "";
  return `${parentId}\n${frame.url()}`;
}

function parseNodes(yaml) {
  const nodes = [];
  const context = [];
  for (const line of String(yaml || "").split(/\n/)) {
    const heading = line.match(/^\s*- (heading|region)(?: "([^"]*)")?/);
    if (heading) {
      context.push(`${heading[1]} ${heading[2] || ""}`.trim());
      if (context.length > 4) context.shift();
    }
    const match = line.match(/^\s*- ([a-zA-Z]+)(?: "([^"]*)")?/);
    if (!match) continue;
    const role = match[1].toLowerCase();
    if (!INTERACTIVE.has(role)) continue;
    nodes.push({ role, name: match[2] || "", context: context.slice(-2).join(" / ") });
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
      yaml = await frame.locator("body").ariaSnapshot();
    } catch {
      yaml = "";
    }
    const counts = new Map();
    let local = 0;
    for (const node of parseNodes(yaml)) {
      local += 1;
      const key = `${node.role}\n${node.name}`;
      const nth = counts.get(key) || 0;
      counts.set(key, nth + 1);
      const ref = `${frameId}:e${local}`;
      const locator = node.name
        ? frame.getByRole(node.role, { name: node.name, exact: true }).nth(nth)
        : frame.getByRole(node.role).nth(nth);
      state.refs.set(ref, { locator, epoch: state.epoch, frame });
      const label = node.name ? `${node.role} "${node.name}"` : node.role;
      const where = node.context ? ` ${node.context}` : "";
      lines.push(`- ${label} ref=${ref}${where}`);
      refs.push(ref);
    }
  }
  return { epoch: state.epoch, text: lines.join("\n"), refs };
}

export function locatorFor(recordingId, ref) {
  const state = browserSession(recordingId);
  const hit = state?.refs.get(ref);
  if (!hit || hit.epoch !== state.epoch) return null;
  return hit;
}
