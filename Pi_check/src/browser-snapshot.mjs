/**
 * 把当前页/弹层/frame 收成带 ref 和语义 selector 的快照。不分类、不判断能力。
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

export function collectInteractiveSnapshot() {
  const compact = (value) => String(value || "").replace(/\s+/g, " ").trim().slice(0, 80);
  const visible = (node) => {
    if (!node || node.nodeType !== 1) return false;
    const style = window.getComputedStyle(node);
    if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity) === 0) {
      return false;
    }
    const box = node.getBoundingClientRect();
    return box.width > 2 && box.height > 2;
  };
  const labelOf = (node) => {
    const item = node.closest?.(".el-form-item, .ant-form-item, label") || node;
    const labelNode = item.querySelector?.(".el-form-item__label, .ant-form-item-label, label");
    return compact(
      node.getAttribute?.("aria-label")
      || node.getAttribute?.("placeholder")
      || labelNode?.innerText
      || node.innerText
      || node.getAttribute?.("name")
      || node.getAttribute?.("id"),
    );
  };
  const controls = [];
  const actions = [];
  const seen = new Set();
  const mark = (node, ref) => {
    try {
      node.setAttribute("data-pi-ref", ref);
    } catch {
      // 只读节点跳过
    }
  };
  const hosts = document.querySelectorAll(".el-select, .ant-select, .el-picker, .ant-picker, [role='combobox']");
  for (const host of hosts) {
    if (!visible(host) || seen.has(host)) continue;
    for (const inner of host.querySelectorAll("input, textarea, select")) seen.add(inner);
    seen.add(host);
  }
  const inputs = document.querySelectorAll("input, textarea, select, [contenteditable='true'], .el-select, .ant-select, .el-picker, .ant-picker, .el-tree, .ant-tree, [role='combobox']");
  let controlNo = 0;
  for (const node of inputs) {
    if (!visible(node) || seen.has(node) && !node.matches?.(".el-select, .ant-select, .el-picker, .ant-picker, [role='combobox']")) continue;
    if (node.matches?.("input, textarea") && node.closest?.(".el-select, .ant-select, .el-picker, .ant-picker, [role='combobox']")) continue;
    seen.add(node);
    controlNo += 1;
    const ref = `c${controlNo}`;
    mark(node, ref);
    const inner = node.querySelector?.("input, textarea, select") || node;
    const placeholder = compact(inner.getAttribute?.("placeholder") || node.getAttribute?.("placeholder"));
    const label = labelOf(inner) || labelOf(node);
    const row = {
      ref,
      label,
      name: compact(inner.getAttribute?.("name") || inner.getAttribute?.("id") || node.getAttribute?.("id")),
      control_kind: compact(node.getAttribute?.("role") || node.tagName || "input"),
      placeholder,
      readonly: Boolean(inner.readOnly || inner.getAttribute?.("aria-readonly") === "true"),
      disabled: Boolean(inner.disabled),
    };
    row.selector = placeholder
      ? `placeholder=${placeholder}`
      : (label ? `label=${label}` : `ref=${ref}`);
    controls.push(row);
  }
  const buttons = document.querySelectorAll("button, [role='button'], a.ant-btn, .el-button, input[type='button'], input[type='submit']");
  let actionNo = 0;
  for (const node of buttons) {
    if (!visible(node) || seen.has(node)) continue;
    seen.add(node);
    actionNo += 1;
    const ref = `a${actionNo}`;
    mark(node, ref);
    const label = labelOf(node);
    actions.push({
      ref,
      label,
      kind: "button",
      selector: label ? `role=button[name="${label}"]` : `ref=${ref}`,
    });
  }
  const options = [];
  const seenOption = new Set();
  for (const node of document.querySelectorAll("[role='option'], .el-select-dropdown__item, .ant-select-item-option")) {
    if (!visible(node)) continue;
    const text = compact(node.innerText || node.textContent);
    if (!text || seenOption.has(text)) continue;
    seenOption.add(text);
    options.push(text);
  }
  return {
    url: location.href,
    title: document.title || "",
    controls,
    actions,
    options,
  };
}
