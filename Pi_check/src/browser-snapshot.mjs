/**
 * 把当前页/弹层/frame 收成带 ref 的快照。不分类、不判断能力。
 */

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
      controls.push({
        ...asRecord(item),
        ref: `c${controlNo}`,
        frame_url: String(row.url || ""),
      });
    }
    for (const item of Array.isArray(row.actions) ? row.actions : []) {
      actionNo += 1;
      actions.push({
        ...asRecord(item),
        ref: `a${actionNo}`,
        frame_url: String(row.url || ""),
      });
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
  const inputs = document.querySelectorAll("input, textarea, select, [contenteditable='true'], .el-select, .ant-select, .el-picker, .ant-picker, .el-tree, .ant-tree");
  let controlNo = 0;
  for (const node of inputs) {
    if (!visible(node) || seen.has(node)) continue;
    seen.add(node);
    controlNo += 1;
    const ref = `c${controlNo}`;
    mark(node, ref);
    controls.push({
      ref,
      label: labelOf(node),
      name: compact(node.getAttribute?.("name") || node.getAttribute?.("id")),
      control_kind: compact(node.tagName || node.getAttribute?.("role") || "input"),
      placeholder: compact(node.getAttribute?.("placeholder")),
      readonly: Boolean(node.readOnly || node.getAttribute?.("aria-readonly") === "true"),
      disabled: Boolean(node.disabled),
    });
  }
  const buttons = document.querySelectorAll("button, [role='button'], a.ant-btn, .el-button, input[type='button'], input[type='submit']");
  let actionNo = 0;
  for (const node of buttons) {
    if (!visible(node) || seen.has(node)) continue;
    seen.add(node);
    actionNo += 1;
    const ref = `a${actionNo}`;
    mark(node, ref);
    actions.push({
      ref,
      label: labelOf(node),
      kind: "button",
    });
  }
  return {
    url: location.href,
    title: document.title || "",
    controls,
    actions,
  };
}
