/**
 * 把 page.evaluate(collectPageFacts) 的结果打成带 ref 的快照。
 * 可改事实在 visible-controls.collectPageFacts，这里不另算 readonly。
 */

import { snapshotSelector } from "./browser-actions.mjs";

function asRecord(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

export function assignSnapshotRefs(raw = {}) {
  const source = asRecord(raw);
  const frames = Array.isArray(source.frames) && source.frames.length
    ? source.frames
    : [{
      url: source.url || "",
      controls: Array.isArray(source.controls) ? source.controls : [],
      actions: Array.isArray(source.actions) ? source.actions : [],
    }];
  const controls = [];
  const actions = [];
  let controlNo = 0;
  let actionNo = 0;
  for (const frame of frames) {
    const row = asRecord(frame);
    for (const item of Array.isArray(row.controls) ? row.controls : []) {
      controlNo += 1;
      const control = {
        ...asRecord(item),
        ref: `c${controlNo}`,
        frame_url: String(row.url || ""),
      };
      control.selector = snapshotSelector(control, "control");
      controls.push(control);
    }
    for (const item of Array.isArray(row.actions) ? row.actions : []) {
      actionNo += 1;
      const action = {
        ...asRecord(item),
        ref: `a${actionNo}`,
        frame_url: String(row.url || ""),
      };
      action.selector = snapshotSelector(action, "action");
      actions.push(action);
    }
  }
  return {
    available: true,
    url: String(source.url || frames[0]?.url || ""),
    title: String(source.title || ""),
    controls,
    actions,
  };
}

