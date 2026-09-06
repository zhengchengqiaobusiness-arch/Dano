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

function displayLabel(value) {
  return compact(String(value || "").replace(/^[＊*\s]+/, "").replace(/[＊*]\s*$/g, ""));
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
      const options = Array.isArray(row.options)
        ? row.options.map((option) => compact(option)).filter(Boolean).slice(0, 24)
        : [];
      return {
        region: String(row.region || ""),
        name: String(row.name || ""),
        label: displayLabel(row.label),
        placeholder: String(row.placeholder || ""),
        section: String(row.section || ""),
        control_kind: String(row.control_kind || ""),
        required_mark: Boolean(row.required_mark),
        readonly: Boolean(row.readonly),
        disabled: Boolean(row.disabled),
        range: Boolean(row.range),
        options,
      };
    }),
  };
}

export function collectVisibleControlsInPage() {
  const compactText = (value) => String(value || "").replace(/\s+/g, " ").trim().slice(0, 80);
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
  const inSidebar = (node) => Boolean(
    node.closest?.("aside, .el-aside, .ant-layout-sider, [class*='sidebar'], [class*='sider']"),
  );
  const dialogRoot = (node) => node.closest?.(
    'dialog,[role="dialog"],[role="alertdialog"],[aria-modal="true"],.el-dialog,.ant-modal,.van-dialog',
  );
  const tableRoot = (node) => node.closest?.("table, .el-table, .ant-table, .vxe-table, tbody, thead");
  const regionOf = (node) => {
    if (dialogRoot(node)) return "dialog";
    if (tableRoot(node)) return "table";
    return (inFilter(node) || inSidebar(node)) ? "filter" : "form";
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
  const nearbyHeading = (node) => {
    let current = node;
    for (let depth = 0; depth < 4 && current; depth += 1) {
      let prev = current.previousElementSibling;
      while (prev) {
        const text = textOf(prev);
        if (
          text
          && text.length <= 24
          && !prev.matches?.("button, .el-button, .ant-btn, a, [role='button']")
          && !prev.querySelector?.("input, textarea, select, [role='tree'], [role='tablist']")
        ) {
          return text;
        }
        prev = prev.previousElementSibling;
      }
      current = current.parentElement;
    }
    return "";
  };
  const optionTexts = (node) => {
    const out = [];
    const seen = new Set();
    const add = (text) => {
      const value = compactText(text);
      if (!value || seen.has(value) || value.length > 24) return;
      seen.add(value);
      out.push(value);
    };
    for (const item of node.querySelectorAll?.(
      "[role='treeitem'], [role='tab'], [role='radio'], .el-radio-button, .el-radio, .el-segmented__item, .ant-segmented-item, .ant-radio-wrapper, .el-tabs__item",
    ) || []) {
      add(item.getAttribute?.("aria-label") || textOf(item));
    }
    return out.slice(0, 24);
  };
  const dateRange = (node) => {
    const inputs = [...(node.querySelectorAll?.("input") || [])];
    return Boolean(
      node.matches?.(".el-range-editor, .ant-picker-range")
      || node.querySelector?.(".el-range-separator, .ant-picker-range, .el-range-input")
      || inputs.length >= 2,
    );
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
      node.matches?.("[role='tree'], .el-tree, .ant-tree, .el-tree-select")
      || node.querySelector?.("[role='tree'], [role='treeitem']")
    ) return "select";
    if (
      node.matches?.(".el-select, .ant-select, select, [role='combobox'], .el-radio-group, .ant-radio-group, [role='radiogroup'], .el-segmented, .ant-segmented, [role='tablist'], .el-tabs, .ant-tabs")
      || node.querySelector?.(".el-select, .ant-select, select, [role='combobox'], .el-radio-group, .ant-radio-group, [role='radiogroup']")
    ) return "select";
    if (node.matches?.("textarea") || node.querySelector?.("textarea")) return "textarea";
    return "input";
  };

  const seen = new Set();
  const out = [];
  const cleanLabel = (value) => compactText(String(value || "").replace(/^[＊*\s]+/, "").replace(/[＊*]\s*$/g, ""));
  const push = (row) => {
    const options = Array.isArray(row.options) ? row.options.map((item) => compactText(item)).filter(Boolean).slice(0, 24) : [];
    const rawLabel = compactText(row.label) || (options.length ? options.slice(0, 4).join(" / ") : "");
    const label = cleanLabel(rawLabel);
    if (!label && !row.name && !row.placeholder) return;
    const next = {
      ...row,
      label,
      options,
      section: compactText(row.section),
      required_mark: Boolean(row.required_mark || /[＊*]/.test(rawLabel)),
    };
    const key = [next.region, next.name, next.label, next.placeholder, next.section, next.control_kind, next.range ? "range" : ""].join("|");
    if (seen.has(key)) return;
    seen.add(key);
    out.push(next);
  };
  const describe = (node, extras = {}) => {
    const input = firstInput(node) || (node.matches?.("input, textarea, select") ? node : null);
    const inputs = [...(node.querySelectorAll?.("input") || [])];
    const options = extras.options || optionTexts(node);
    const label = extras.label || nearbyLabel(node) || nearbyHeading(node) || String(node.getAttribute?.("aria-label") || "");
    const controlKind = extras.control_kind || detectKind(node, `${label} ${extras.placeholder || ""}`);
    const range = controlKind === "date" && dateRange(node);
    const placeholder = range
      ? inputs.map((item) => item.placeholder).filter(Boolean).join(" → ") || String(input?.placeholder || extras.placeholder || "")
      : String(input?.placeholder || node.getAttribute?.("placeholder") || extras.placeholder || "");
    return {
      region: extras.region || regionOf(node),
      name: String(input?.name || input?.id || node.getAttribute?.("name") || extras.name || ""),
      label,
      placeholder,
      section: compactText(extras.section || ""),
      control_kind: controlKind,
      required_mark: markRequired(node.closest?.(".el-form-item, .ant-form-item, .form-item") || node, label),
      readonly: widgetLocked(node, input, controlKind),
      disabled: Boolean(input?.disabled),
      range,
      options,
    };
  };

  for (const item of document.querySelectorAll(".el-form-item, .ant-form-item, .form-item, form label")) {
    if (item.matches?.("label") && item.closest?.(".el-form-item, .ant-form-item, .form-item")) continue;
    if (!visible(item)) continue;
    const label = nearbyLabel(item)
      || textOf(item.querySelector(".el-form-item__label, .ant-form-item-label"))
      || textOf(item.matches?.("label") ? item : item.querySelector("label"));
    push(describe(item, { label }));
  }

  const widgetSelectors = [
    ".el-date-editor, .el-range-editor, .ant-picker, input[type='date'], input[type='datetime-local'], input[type='month']",
    ".el-select, .ant-select, select, [role='combobox']",
    ".el-upload, .ant-upload, .ant-upload-wrapper, input[type='file']",
    ".el-radio-group, .ant-radio-group, .el-segmented, .ant-segmented, [role='radiogroup'], [role='tablist'], .el-tabs, .ant-tabs",
  ];
  for (const selector of widgetSelectors) {
    for (const node of document.querySelectorAll(selector)) {
      if (!visible(node)) continue;
      if (node.closest?.(".el-form-item, .ant-form-item, .form-item")) continue;
      push(describe(node));
    }
  }

  for (const node of document.querySelectorAll("[role='tree'], .el-tree, .ant-tree")) {
    if (!visible(node)) continue;
    if (node.closest?.(".el-select-dropdown, .ant-select-dropdown, .el-tree-select, .ant-select")) continue;
    const host = node.closest?.("aside, .el-aside, .ant-layout-sider, [class*='sidebar'], [class*='sider']") || node.parentElement;
    const search = host?.querySelector?.("input[placeholder]");
    const hostTitle = textOf(host?.querySelector?.(":scope > .title, :scope > h1, :scope > h2, :scope > h3, :scope > h4"));
    const options = optionTexts(node);
    push(describe(node, {
      label: nearbyLabel(node) || nearbyHeading(node) || hostTitle || String(search?.placeholder || ""),
      placeholder: String(search?.placeholder || ""),
      control_kind: "select",
      region: inSidebar(node) || inFilter(node) ? "filter" : regionOf(node),
      options,
    }));
  }

  for (const btn of document.querySelectorAll("button, .el-button, .ant-btn, a, [role='button']")) {
    if (!visible(btn)) continue;
    const label = textOf(btn);
    if (!/上传|选择文件|Upload|Attach|Browse/i.test(label)) continue;
    const wrap = btn.closest?.(".el-upload, .ant-upload, .el-form-item, .ant-form-item") || btn;
    push({
      region: regionOf(btn),
      name: "",
      label: nearbyLabel(wrap) || nearbyHeading(wrap) || label,
      placeholder: "",
      section: nearbyHeading(wrap) || nearbyHeading(btn),
      control_kind: "upload",
      required_mark: markRequired(wrap, nearbyLabel(wrap) || label),
      readonly: widgetLocked(wrap, firstInput(wrap), "upload"),
      disabled: Boolean(btn.disabled),
      range: false,
      options: [],
    });
  }

  const tableColumns = new Set();
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
    const label = textOf(head);
    const controlKind = detectKind(wrap, label);
    const section = nearbyHeading(table) || nearbyHeading(input);
    const columnKey = `${label}|${controlKind}|${String(input.placeholder || "")}|${section}`;
    if (tableColumns.has(columnKey)) continue;
    tableColumns.add(columnKey);
    push({
      region: dialogRoot(input) ? "dialog" : "table",
      name: String(input.name || input.id || ""),
      label,
      placeholder: String(input.placeholder || ""),
      section,
      control_kind: controlKind,
      required_mark: false,
      readonly: widgetLocked(wrap, input, controlKind),
      disabled: Boolean(input.disabled),
      range: controlKind === "date" && dateRange(wrap),
      options: [],
    });
  }

  for (const cell of document.querySelectorAll(".el-table td, .ant-table td, table td, .vxe-table td")) {
    if (!visible(cell)) continue;
    if (cell.querySelector("input, textarea, select")) continue;
    const widget = cell.querySelector("[role='slider'], .el-slider, .ant-slider, .el-progress, .ant-progress, [class*='progress']");
    if (!widget || !visible(widget)) continue;
    const row = cell.parentElement;
    const index = row ? [...row.children].indexOf(cell) : -1;
    const table = cell.closest("table, .el-table, .ant-table, .vxe-table");
    const head = index >= 0
      ? table?.querySelector?.(`thead th:nth-child(${index + 1}), thead td:nth-child(${index + 1})`)
      : null;
    push({
      region: dialogRoot(cell) ? "dialog" : "table",
      name: "",
      label: textOf(head) || nearbyLabel(cell),
      placeholder: "",
      section: nearbyHeading(table) || nearbyHeading(cell),
      control_kind: "input",
      required_mark: false,
      readonly: false,
      disabled: false,
      range: false,
      options: [],
    });
  }

  for (const node of document.querySelectorAll(
    'dialog input, dialog textarea, [role="dialog"] input, [role="dialog"] textarea, [role="alertdialog"] input, [role="alertdialog"] textarea, .el-dialog input, .el-dialog textarea, .ant-modal input, .ant-modal textarea, .van-dialog input, .van-dialog textarea',
  )) {
    if (!visible(node)) continue;
    if (node.closest?.(".el-form-item, .ant-form-item, .form-item")) continue;
    const wrap = node.closest(".el-date-editor, .el-select, .ant-picker, .ant-select") || node;
    push(describe(wrap, {
      region: "dialog",
      label: nearbyLabel(node) || nearbyHeading(node) || String(node.getAttribute?.("aria-label") || ""),
      placeholder: String(node.placeholder || ""),
    }));
  }

  const addRowPacked = /^(添加|新增|增加).{0,12}(行|项|明细|记录)$/i;
  const addRowEnglish = /\bAdd (row|item|line)\b/i;
  for (const btn of document.querySelectorAll("button, .el-button, .ant-btn, a, [role='button']")) {
    if (!visible(btn)) continue;
    const rawLabel = textOf(btn);
    if (!addRowPacked.test(rawLabel.replace(/\s+/g, "")) && !addRowEnglish.test(rawLabel)) continue;
    const nearTable = tableRoot(btn)
      || btn.closest?.(".el-card, .ant-card, section, .panel")?.querySelector?.("table, .el-table, .ant-table, .vxe-table")
      || btn.parentElement?.querySelector?.("table, .el-table, .ant-table, .vxe-table");
    push({
      region: dialogRoot(btn) ? "dialog" : (nearTable ? "table" : regionOf(btn)),
      name: "",
      label: textOf(btn),
      placeholder: "",
      section: nearbyHeading(btn) || nearbyHeading(nearTable),
      control_kind: "button",
      required_mark: false,
      readonly: Boolean(btn.disabled),
      disabled: Boolean(btn.disabled),
      range: false,
      options: [],
    });
  }

  const rank = { button: 0, readonly: 1, input: 2, textarea: 3, upload: 4, date: 5, select: 6 };
  const merged = [];
  const groups = new Map();
  for (const row of out) {
    if (row.control_kind === "button") {
      merged.push(row);
      continue;
    }
    const key = [row.region, row.section, row.label || row.placeholder || row.name, row.range ? "range" : "field"].join("|");
    const prev = groups.get(key);
    if (!prev) {
      groups.set(key, { ...row });
      continue;
    }
    const keep = (rank[row.control_kind] || 0) >= (rank[prev.control_kind] || 0) ? { ...row } : { ...prev };
    keep.readonly = Boolean(prev.readonly || row.readonly);
    keep.disabled = Boolean(prev.disabled || row.disabled);
    if (keep.readonly || keep.disabled) keep.readonly = true;
    keep.required_mark = Boolean(prev.required_mark || row.required_mark);
    const richer = (row.options || []).length > (prev.options || []).length ? row : prev;
    keep.options = richer.options || keep.options;
    keep.placeholder = keep.placeholder || row.placeholder || prev.placeholder;
    keep.name = keep.name || row.name || prev.name;
    groups.set(key, keep);
  }
  merged.push(...groups.values());
  const named = new Set(merged.filter((row) => row.label && row.name).map((row) => `${row.region}|${row.name}|${row.control_kind}`));
  const compactRows = merged.filter((row) => {
    if (row.label || row.control_kind === "button") return true;
    if (!row.name) return Boolean(row.placeholder);
    return !named.has(`${row.region}|${row.name}|${row.control_kind}`);
  });
  return compactRows.slice(0, 120);
}

export function summarizeVisibleControls(controls) {
  return (Array.isArray(controls) ? controls : [])
    .map((item) => {
      const label = compact(item?.label || item?.placeholder || item?.name);
      if (!label) return "";
      const kind = String(item?.control_kind || "");
      const options = (Array.isArray(item?.options) ? item.options : [])
        .map((option) => compact(option))
        .filter(Boolean)
        .slice(0, 4);
      if (kind === "select" && options.length) return `${label}(${kind}:${options.join("/")})`;
      if (kind && kind !== "input") return `${label}(${kind})`;
      return label;
    })
    .filter(Boolean)
    .slice(0, 24)
    .join("、");
}
