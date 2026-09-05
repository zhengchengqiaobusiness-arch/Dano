/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 *
 * 只把当前页看得见的筛选/表单/表格控件投影成事实。
 * 不判断能力、不补字段、不改名。不绑定业务页或字段名。
 * collectVisibleControlsInPage 必须自包含，供 page.evaluate 原样注入。
 */

function compact(value) {
  return String(value || "").replace(/\s+/g, " ").trim().slice(0, 80);
}

export function projectVisibleControlSnapshot(event) {
  const payload = event?.payload && typeof event.payload === "object" ? event.payload : {};
  const controls = Array.isArray(payload.controls) ? payload.controls : [];
  return {
    seq: Number(event?.seq) || 0,
    kind: "visible_control",
    url: String(payload.url || ""),
    reason: String(payload.reason || ""),
    count: controls.length,
    controls: controls.map((item) => {
      const row = item && typeof item === "object" ? item : {};
      return {
        region: String(row.region || ""),
        name: String(row.name || ""),
        label: String(row.label || ""),
        placeholder: String(row.placeholder || ""),
        control_kind: String(row.control_kind || ""),
        required_mark: Boolean(row.required_mark),
        readonly: Boolean(row.readonly),
        disabled: Boolean(row.disabled),
      };
    }),
  };
}

export function collectVisibleControlsInPage() {
  const filterRoot =
    ".search-form, .ant-pro-table-search, .el-form--inline, .filter-container, .table-search, .vxe-grid--form-wrapper, [class*='search-form'], [class*='table-search'], [class*='filter-bar'], [class*='search-bar'], [class*='filter-form'], [class*='query-form']";
  const expandRe = /^(展开|展开筛选|高级搜索|高级|更多筛选|Expand|Advanced)$/i;
  for (const root of document.querySelectorAll(filterRoot)) {
    for (const btn of root.querySelectorAll("button, a, .el-button, .ant-btn, span, [role='button']")) {
      const text = String(btn.innerText || btn.textContent || "").replace(/\s+/g, "");
      if (!expandRe.test(text) && !/展开筛选|高级搜索/.test(text)) continue;
      try {
        btn.click();
      } catch {
        // ignore
      }
    }
  }

  const textOf = (node) => String(node?.innerText || node?.textContent || "").replace(/\s+/g, " ").trim().slice(0, 80);
  const visible = (node) => {
    if (!node || !node.getBoundingClientRect) return false;
    const box = node.getBoundingClientRect();
    return box.width >= 2 && box.height >= 2;
  };
  const inFilter = (node) => {
    if (node.closest?.(filterRoot)) return true;
    const form = node.closest?.("form, .el-form, .ant-form");
    if (!form) return false;
    if (form.classList?.contains("el-form--inline")) return true;
    let sibling = form.nextElementSibling;
    for (let index = 0; index < 3 && sibling; index += 1, sibling = sibling.nextElementSibling) {
      if (
        sibling.matches?.("table, .el-table, .ant-table, .vxe-table")
        || sibling.querySelector?.("table, .el-table, .ant-table, .vxe-table")
      ) {
        return true;
      }
    }
    return false;
  };
  const regionOf = (node) => {
    if (node.closest?.("table, .el-table, .ant-table, .vxe-table, tbody, thead")) return "table";
    return inFilter(node) ? "filter" : "form";
  };
  const firstInput = (node) => node.querySelector?.("input, textarea, select");
  const widgetLocked = (root, input, kind) => {
    const disabled = Boolean(
      input?.disabled
      || root.matches?.(".is-disabled, [disabled], .el-input.is-disabled, .el-select.is-disabled, .ant-select-disabled, .ant-picker-disabled")
      || root.querySelector?.(".is-disabled, [disabled], .el-input.is-disabled, .el-select.is-disabled, .ant-select-disabled, .ant-picker-disabled"),
    );
    if (kind === "date" || kind === "select" || kind === "upload") return disabled;
    return Boolean(input?.readOnly || disabled);
  };
  const nearbyLabel = (node) => {
    const item = node.closest?.(".el-form-item, .ant-form-item, .form-item");
    const fromItem = textOf(item?.querySelector(".el-form-item__label, .ant-form-item-label, label, .form-label"));
    if (fromItem) return fromItem;
    const labelled = node.getAttribute?.("aria-label") || firstInput(node)?.getAttribute?.("aria-label");
    if (labelled) return String(labelled).replace(/\s+/g, " ").trim().slice(0, 80);
    const forId = firstInput(node)?.id;
    if (forId) {
      const escaped = typeof CSS !== "undefined" && CSS.escape ? CSS.escape(forId) : forId.replace(/"/g, "");
      const byFor = document.querySelector(`label[for="${escaped}"]`);
      if (byFor) return textOf(byFor);
    }
    const prev = node.previousElementSibling;
    if (prev && /label|title|text|caption/i.test(`${prev.className || ""} ${prev.tagName || ""}`)) {
      return textOf(prev);
    }
    return "";
  };
  const markRequired = (item, label) => (
    Boolean(
      item?.classList?.contains("is-required")
      || item?.querySelector?.(".el-form-item__label.is-required, .ant-form-item-required, .required"),
    )
    || /\*/.test(label)
  );
  const detectKind = (node, label = "") => {
    if (
      node.matches?.("input[type='file'], .el-upload, .ant-upload, .ant-upload-wrapper")
      || node.querySelector?.("input[type='file'], .el-upload, .ant-upload")
      || /上传|附件|选择文件|Upload|Attach|Browse/i.test(label)
    ) return "upload";
    if (
      node.matches?.(".el-date-editor, .el-range-editor, .ant-picker, input[type='date'], input[type='datetime-local'], input[type='month']")
      || node.querySelector?.(".el-date-editor, .el-range-editor, .ant-picker, input[type='date'], input[type='datetime-local'], input[type='month']")
    ) return "date";
    if (
      node.matches?.(".el-select, .ant-select, select, [role='combobox'], .el-radio-group, .ant-radio-group, [role='radiogroup']")
      || node.querySelector?.(".el-select, .ant-select, select, [role='combobox'], .el-radio-group, .ant-radio-group, [role='radiogroup']")
    ) return "select";
    if (node.matches?.("textarea") || node.querySelector?.("textarea")) return "textarea";
    return "input";
  };

  const seen = new Set();
  const out = [];
  const push = (row) => {
    const key = [row.region, row.name, row.label, row.placeholder, row.control_kind].join("|");
    if (seen.has(key)) return;
    if (!row.label && !row.name && !row.placeholder) return;
    seen.add(key);
    out.push(row);
  };

  for (const item of document.querySelectorAll(".el-form-item, .ant-form-item, .form-item, form label")) {
    if (item.matches?.("label") && item.closest?.(".el-form-item, .ant-form-item, .form-item")) continue;
    if (!visible(item)) continue;
    const label = nearbyLabel(item)
      || textOf(item.querySelector(".el-form-item__label, .ant-form-item-label"))
      || textOf(item.matches?.("label") ? item : item.querySelector("label"));
    const input = firstInput(item);
    const controlKind = detectKind(item, label);
    const readonly = widgetLocked(item, input, controlKind);
    push({
      region: regionOf(item),
      name: String(input?.name || input?.id || ""),
      label,
      placeholder: String(input?.placeholder || ""),
      control_kind: controlKind,
      required_mark: markRequired(item, label),
      readonly,
      disabled: Boolean(input?.disabled),
    });
  }

  const widgetSelectors = [
    ".el-date-editor, .el-range-editor, .ant-picker, input[type='date'], input[type='datetime-local'], input[type='month']",
    ".el-select, .ant-select, select, [role='combobox']",
    ".el-upload, .ant-upload, .ant-upload-wrapper, input[type='file']",
    ".el-radio-group, .ant-radio-group, .el-segmented, [role='tablist'], .el-tabs, .ant-tabs",
  ];
  for (const selector of widgetSelectors) {
    for (const node of document.querySelectorAll(selector)) {
      if (!visible(node)) continue;
      if (node.closest?.(".el-form-item, .ant-form-item, .form-item")) continue;
      const input = firstInput(node) || (node.matches?.("input, textarea, select") ? node : null);
      const label = nearbyLabel(node) || String(node.getAttribute?.("aria-label") || "").replace(/\s+/g, " ").trim().slice(0, 80);
      const placeholder = String(input?.placeholder || node.getAttribute?.("placeholder") || "");
      const controlKind = detectKind(node, `${label} ${placeholder}`);
      const readonly = widgetLocked(node, input, controlKind);
      push({
        region: regionOf(node),
        name: String(input?.name || input?.id || node.getAttribute?.("name") || ""),
        label,
        placeholder,
        control_kind: controlKind,
        required_mark: markRequired(node.closest?.(".el-form-item, .ant-form-item") || node, label),
        readonly,
        disabled: Boolean(input?.disabled),
      });
    }
  }

  for (const btn of document.querySelectorAll("button, .el-button, .ant-btn, a, [role='button']")) {
    if (!visible(btn)) continue;
    const label = textOf(btn);
    if (!/上传|选择文件|Upload|Attach|Browse/i.test(label)) continue;
    const wrap = btn.closest?.(".el-upload, .ant-upload, .el-form-item, .ant-form-item") || btn;
    push({
      region: regionOf(btn),
      name: "",
      label: nearbyLabel(wrap) || label,
      placeholder: "",
      control_kind: "upload",
      required_mark: markRequired(wrap, nearbyLabel(wrap) || label),
      readonly: widgetLocked(wrap, firstInput(wrap), "upload"),
      disabled: Boolean(btn.disabled),
    });
  }

  for (const input of document.querySelectorAll(".el-table input, .el-table textarea, .ant-table input, .ant-table textarea, table input, table textarea")) {
    if (!visible(input)) continue;
    const cell = input.closest("td, th");
    const row = cell?.parentElement;
    const index = row ? [...row.children].indexOf(cell) : -1;
    const table = input.closest("table, .el-table, .ant-table");
    const head = index >= 0
      ? table?.querySelector?.(`thead th:nth-child(${index + 1}), thead td:nth-child(${index + 1})`)
      : null;
    const wrap = input.closest(".el-date-editor, .el-select, .ant-picker, .ant-select") || input;
    const controlKind = detectKind(wrap, textOf(head));
    push({
      region: "table",
      name: String(input.name || input.id || ""),
      label: textOf(head),
      placeholder: String(input.placeholder || ""),
      control_kind: controlKind,
      required_mark: false,
      readonly: widgetLocked(wrap, input, controlKind),
      disabled: Boolean(input.disabled),
    });
  }

  return out.slice(0, 120);
}

export function summarizeVisibleControls(controls) {
  return (Array.isArray(controls) ? controls : [])
    .map((item) => compact(item?.label || item?.placeholder || item?.name))
    .filter(Boolean)
    .slice(0, 24)
    .join("、");
}
