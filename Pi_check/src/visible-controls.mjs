/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 *
 * 只把当前页看得见的筛选/表单/表格控件投影成事实。
 * 不判断能力、不补字段、不改名。不绑定业务页或字段名。
 * collectPageFacts 必须自包含，供 page.evaluate 原样注入。
 * snapshot 与 visible_control 共用同一套「整个控件能不能改」事实，不判断能力。
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
        display_value: String(row.display_value || ""),
        host_value: String(row.host_value || ""),
        date_format: String(row.date_format || ""),
        min: row.min ?? null,
        max: row.max ?? null,
        step: row.step ?? null,
      };
    }),
  };
}

export function collectPageFacts() {
  const WIDGET_HOST_SELECTOR = [
    ".el-select",
    ".ant-select",
    ".el-picker",
    ".ant-picker",
    ".el-date-editor",
    ".el-range-editor",
    ".el-cascader",
    ".ant-cascader",
    ".el-tree-select",
    ".el-time-picker",
    "[role='combobox']",
    ".el-radio-group",
    ".ant-radio-group",
    "[role='radiogroup']",
    ".el-segmented",
    ".ant-segmented",
    "[role='tablist']",
  ].join(", ");
  const LAYOUT_HOST_SELECTOR = [
    ".el-tabs",
    ".ant-tabs",
    ".el-tab-pane",
    ".ant-tabs-tabpane",
    ".el-tabs__content",
    ".ant-tabs-content",
    "form",
    ".el-form",
    ".ant-form",
  ].join(", ");
  const PAGINATION_SELECTOR = [
    ".el-pagination",
    ".ant-pagination",
    ".vxe-pager",
    ".ant-table-pagination",
    "nav[aria-label='pagination']",
    ".pagination",
  ].join(", ");
  const compactText = (value) => String(value || "").replace(/\s+/g, " ").trim().slice(0, 80);
  const filterRoot =
    ".search-form, .ant-pro-table-search, .el-form--inline, .filter-container, .table-search, .vxe-grid--form-wrapper, [class*='search-form'], [class*='table-search'], [class*='filter-bar'], [class*='search-bar'], [class*='filter-form'], [class*='query-form']";
  const textOf = (node) => String(node?.innerText || node?.textContent || "").replace(/\s+/g, " ").trim().slice(0, 80);
  const visible = (node) => {
    if (!node || !node.getBoundingClientRect) return false;
    const box = node.getBoundingClientRect();
    return box.width >= 2 && box.height >= 2;
  };
  const dialogRoot = (node) => node.closest?.(
    'dialog,[role="dialog"],[role="alertdialog"],[aria-modal="true"],.el-dialog,.ant-modal,.van-dialog',
  );
  const inFilter = (node) => {
    if (dialogRoot(node)) return false;
    if (node.closest?.(filterRoot)) return true;
    const form = node.closest?.("form, .el-form, .ant-form");
    if (!form || dialogRoot(form)) return false;
    if (form.classList?.contains("el-form--inline")) return true;
    let sibling = form.nextElementSibling;
    for (let index = 0; index < 3 && sibling; index += 1, sibling = sibling.nextElementSibling) {
      if (dialogRoot(sibling)) continue;
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
  const tableRoot = (node) => node.closest?.("table, .el-table, .ant-table, .vxe-table, tbody, thead");
  const tableHostOf = (node) => node.closest?.(".el-table, .ant-table, .vxe-table") || node.closest?.("table");
  const isLayoutHost = (node) => Boolean(node?.matches?.(LAYOUT_HOST_SELECTOR));
  const isChoiceGroup = (node) => Boolean(
    node?.matches?.("[role='tablist'], .el-radio-group, .ant-radio-group, [role='radiogroup'], .el-segmented, .ant-segmented"),
  );
  const isCompositeHost = (node) => {
    if (!node) return false;
    if (isLayoutHost(node)) return true;
    if (isChoiceGroup(node)) return false;
    return Boolean(node.querySelector?.(
      ".el-form-item, .ant-form-item, .form-item, table, .el-table, .ant-table, .vxe-table",
    ));
  };
  const rowCells = (row) => [...(row?.children || [])].filter((item) => {
    const tag = String(item.tagName || "");
    return /^(TD|TH)$/.test(tag)
      || item.classList?.contains("el-table__cell")
      || item.classList?.contains("ant-table-cell")
      || item.classList?.contains("vxe-body--column")
      || item.classList?.contains("vxe-header--column");
  });
  const columnLabel = (cell) => {
    const hostCell = cell?.closest?.("td, th, .el-table__cell, .ant-table-cell, .vxe-body--column") || cell;
    const row = hostCell?.closest?.("tr, .el-table__row, .ant-table-row, .vxe-body--row");
    const cells = rowCells(row);
    const index = cells.indexOf(hostCell);
    if (index < 0) return "";
    const host = tableHostOf(hostCell);
    const headRow = host?.querySelector?.(
      ".el-table__header-wrapper thead tr, .el-table__header thead tr, .ant-table-thead tr, .vxe-header--row, thead tr",
    );
    return textOf(rowCells(headRow)[index]);
  };
  const inChrome = (node) => {
    if (!node) return false;
    if (dialogRoot(node) || tableRoot(node) || inFilter(node) || inSidebar(node)) return false;
    if (node.closest?.("header, [role='banner'], .el-header, .ant-layout-header")) return true;
    if (node.closest?.("[class*='topbar'], [class*='navbar']")) return true;
    const nav = node.closest?.("nav");
    return Boolean(nav && !nav.closest?.(PAGINATION_SELECTOR));
  };
  const isBusinessLabel = (value) => {
    const text = compactText(String(value || "").replace(/^[＊*\s]+/, "").replace(/[＊*]\s*$/g, ""));
    if (!text) return false;
    return !/^\d+$/.test(text);
  };
  const regionOf = (node) => {
    if (dialogRoot(node)) return "dialog";
    if (tableRoot(node)) return "table";
    return (inFilter(node) || inSidebar(node)) ? "filter" : "form";
  };
  const firstInput = (node) => node.querySelector?.("input, textarea, select");
  const inPagination = (node) => Boolean(node.closest?.(PAGINATION_SELECTOR));
  const closestWidget = (node) => {
    if (!node?.closest) return null;
    let current = node.matches?.(WIDGET_HOST_SELECTOR) ? node : node.closest(WIDGET_HOST_SELECTOR);
    while (current && isCompositeHost(current)) {
      current = current.parentElement?.closest?.(WIDGET_HOST_SELECTOR) || null;
    }
    return current;
  };
  const hostLocked = (host) => {
    if (!host) return false;
    if (host.disabled) return true;
    if (host.getAttribute?.("disabled") != null) return true;
    if (host.getAttribute?.("aria-disabled") === "true") return true;
    const cls = host.classList;
    if (!cls) return false;
    return Boolean(
      cls.contains("is-disabled")
      || cls.contains("el-select--disabled")
      || cls.contains("ant-select-disabled")
      || cls.contains("ant-picker-disabled")
      || cls.contains("ant-input-disabled")
      || cls.contains("ant-radio-group-disabled")
    );
  };
  const widgetKind = (kind) => kind === "date" || kind === "select" || kind === "upload";
  const widgetLocked = (root, input, kind) => {
    const host = closestWidget(root) || closestWidget(input);
    if (host || widgetKind(kind)) return hostLocked(host || root);
    if (input?.disabled || root?.disabled) return true;
    if (input?.readOnly) return true;
    const wrap = input?.closest?.(".el-input, .ant-input-affix-wrapper, .ant-input") || root;
    return hostLocked(wrap);
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
    if (node.matches?.(".el-range-editor, .ant-picker-range")) return true;
    if (node.querySelector?.(":scope > .el-range-separator, :scope > .el-range-input, :scope > .ant-picker-range")) {
      return true;
    }
    if (node.matches?.(".el-date-editor, .ant-picker")) {
      return [...(node.querySelectorAll?.("input") || [])].length >= 2;
    }
    return false;
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
      node.matches?.(".el-select, .ant-select, select, [role='combobox'], .el-radio-group, .ant-radio-group, [role='radiogroup'], .el-segmented, .ant-segmented, [role='tablist']")
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
      disabled: widgetLocked(node, input, controlKind),
      range,
      options,
      display_value: String(input?.value || extras.display_value || "").slice(0, 200),
      host_value: String(input?.value || "").slice(0, 200),
      date_format: String(input?.getAttribute?.("placeholder") || extras.date_format || ""),
      min: input?.min || input?.getAttribute?.("min") || "",
      max: input?.max || input?.getAttribute?.("max") || "",
      step: input?.step || input?.getAttribute?.("step") || "",
    };
  };

  for (const item of document.querySelectorAll(".el-form-item, .ant-form-item, .form-item, form label")) {
    if (item.matches?.("label") && item.closest?.(".el-form-item, .ant-form-item, .form-item")) continue;
    if (inPagination(item)) continue;
    if (!visible(item)) continue;
    if (
      item.matches?.(".el-form-item, .ant-form-item, .form-item")
      && item.querySelector?.(".el-form-item, .ant-form-item, .form-item")
    ) continue;
    if (item.querySelector?.("table, .el-table, .ant-table, .vxe-table")) continue;
    const label = nearbyLabel(item)
      || textOf(item.querySelector(".el-form-item__label, .ant-form-item-label"))
      || textOf(item.matches?.("label") ? item : item.querySelector("label"));
    push(describe(item, { label }));
  }

  const widgetSelectors = [
    ".el-date-editor, .el-range-editor, .ant-picker, input[type='date'], input[type='datetime-local'], input[type='month']",
    ".el-select, .ant-select, select, [role='combobox']",
    ".el-upload, .ant-upload, .ant-upload-wrapper, input[type='file']",
    ".el-radio-group, .ant-radio-group, .el-segmented, .ant-segmented, [role='radiogroup'], [role='tablist']",
  ];
  for (const selector of widgetSelectors) {
    for (const node of document.querySelectorAll(selector)) {
      if (!visible(node) || inPagination(node) || isCompositeHost(node)) continue;
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
  const tableFieldSelector = [
    ".el-table input, .el-table textarea, .el-table select",
    ".el-table .el-select, .el-table .el-input-number, .el-table .el-slider, .el-table [role='combobox'], .el-table [role='slider']",
    ".ant-table input, .ant-table textarea, .ant-table select",
    ".ant-table .ant-select, .ant-table .ant-slider, .ant-table [role='combobox'], .ant-table [role='slider']",
    ".vxe-table input, .vxe-table textarea, .vxe-table select",
    ".vxe-table .vxe-input, .vxe-table .vxe-select, .vxe-table [role='combobox']",
    "table input, table textarea, table select",
  ].join(", ");
  for (const input of document.querySelectorAll(tableFieldSelector)) {
    if (!visible(input)) continue;
    if (input.matches?.("input, textarea, select")) {
      const host = input.closest(".el-select, .ant-select, .el-input-number, [role='combobox']");
      if (host && host !== input) continue;
    }
    const cell = input.closest("td, th, .el-table__cell, .ant-table-cell, .vxe-body--column");
    if (!cell) continue;
    const wrap = closestWidget(input) || input.closest(".el-date-editor, .el-select, .el-input-number, .ant-picker, .ant-select") || input;
    const label = columnLabel(cell);
    const controlKind = detectKind(wrap, label);
    const section = nearbyHeading(tableHostOf(input)) || nearbyHeading(input);
    const placeholder = String(input.getAttribute?.("placeholder") || wrap.getAttribute?.("placeholder") || "");
    const columnKey = `${label}|${controlKind}|${placeholder}|${section}`;
    if (tableColumns.has(columnKey)) continue;
    tableColumns.add(columnKey);
    const fieldInput = firstInput(wrap) || (wrap.matches?.("input, textarea, select") ? wrap : input);
    push({
      region: dialogRoot(input) ? "dialog" : "table",
      name: String(fieldInput?.name || fieldInput?.id || ""),
      label,
      placeholder,
      section,
      control_kind: controlKind,
      required_mark: false,
      readonly: widgetLocked(wrap, fieldInput, controlKind),
      disabled: Boolean(fieldInput?.disabled),
      range: controlKind === "date" && dateRange(wrap),
      options: [],
    });
  }

  for (const cell of document.querySelectorAll(".el-table td, .ant-table td, table td, .vxe-table td, .el-table__cell, .ant-table-cell, .vxe-body--column")) {
    if (!visible(cell)) continue;
    if (cell.querySelector("input, textarea, select, .el-select, .ant-select, [role='combobox']")) continue;
    const widget = cell.querySelector("[role='slider'], .el-slider, .ant-slider, .el-progress, .ant-progress, [class*='progress']");
    if (!widget || !visible(widget)) continue;
    const label = columnLabel(cell) || nearbyLabel(cell);
    const section = nearbyHeading(tableHostOf(cell)) || nearbyHeading(cell);
    const columnKey = `${label}|input||${section}`;
    if (tableColumns.has(columnKey)) continue;
    tableColumns.add(columnKey);
    push({
      region: dialogRoot(cell) ? "dialog" : "table",
      name: "",
      label,
      placeholder: "",
      section,
      control_kind: "input",
      required_mark: false,
      readonly: false,
      disabled: false,
      range: false,
      options: [],
    });
  }

  for (const node of document.querySelectorAll(
    ".el-dialog .el-checkbox, .ant-modal .el-checkbox, [role='dialog'] .el-checkbox, [role='dialog'] .ant-checkbox-wrapper, [role='dialog'] input[type='checkbox']",
  )) {
    const host = node.closest?.(".el-checkbox, .ant-checkbox-wrapper, label, tr, .el-table__row") || node;
    if (!visible(host) && !visible(node)) continue;
    const row = host.closest?.("tr, .el-table__row, .ant-table-row");
    const label = compactText(row ? textOf(row) : textOf(host)) || "勾选";
    const name = (label.split(" ").find((part) => /[\u4e00-\u9fff]{2,8}/.test(part))
      || label.split(" ").find((part) => part.length >= 2 && !/^\d+$/.test(part))
      || "勾选");
    push({
      region: "dialog",
      name: "",
      label: name,
      placeholder: "",
      section: nearbyHeading(host) || nearbyHeading(row) || "",
      control_kind: "checkbox",
      required_mark: false,
      readonly: false,
      disabled: Boolean(node.disabled),
      range: false,
      options: [],
    });
  }

  for (const node of document.querySelectorAll(
    'dialog input, dialog textarea, [role="dialog"] input, [role="dialog"] textarea, [role="alertdialog"] input, [role="alertdialog"] textarea, .el-dialog input, .el-dialog textarea, .ant-modal input, .ant-modal textarea, .van-dialog input, .van-dialog textarea',
  )) {
    if (!visible(node)) continue;
    if (node.matches?.("input[type='checkbox'], input[type='radio']")) continue;
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

  const rank = { button: 0, readonly: 1, input: 2, checkbox: 3, textarea: 3, upload: 4, date: 5, select: 6 };
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

  const snapshotControls = [];
  const snapshotActions = [];
  const snapshotOptions = [];
  const seenSnap = new Set();
  const mark = (node, ref, extras = {}) => {
    try {
      node.setAttribute("data-pi-ref", ref);
      const col = compactText(extras.columnLabel || extras.label || "");
      if (col && (extras.region === "table" || tableRoot(node))) {
        node.setAttribute("data-pi-col-label", col);
      }
    } catch {
      // 只读节点跳过
    }
  };
  const snapshotVisible = (node) => {
    if (!visible(node)) return false;
    const style = window.getComputedStyle(node);
    return style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity) !== 0;
  };
  const pushSnapshotControl = (node, extras = {}) => {
    if (!node || seenSnap.has(node) || inPagination(node) || isCompositeHost(node) || !snapshotVisible(node)) return;
    seenSnap.add(node);
    for (const inner of node.querySelectorAll?.("input, textarea, select") || []) {
      if (closestWidget(inner) === node || inner.parentElement === node) seenSnap.add(inner);
    }
    const input = firstInput(node) || (node.matches?.("input, textarea, select") ? node : null);
    const inputs = [...(node.querySelectorAll?.("input") || [])];
    const cell = node.closest?.("td, th, .el-table__cell, .ant-table-cell, .vxe-body--column");
    const label = extras.label
      || (cell ? columnLabel(cell) : "")
      || nearbyLabel(node)
      || nearbyHeading(node)
      || compactText(node.getAttribute?.("aria-label"));
    const controlKind = extras.control_kind || detectKind(node, `${label} ${extras.placeholder || ""}`);
    const range = extras.range ?? (controlKind === "date" && dateRange(node));
    const placeholder = compactText(
      extras.placeholder
      || (range
        ? inputs.map((item) => item.getAttribute?.("placeholder")).filter(Boolean).join(" → ")
        : "")
      || input?.getAttribute?.("placeholder")
      || node.getAttribute?.("placeholder"),
    );
    const locked = widgetLocked(node, input, controlKind);
    const region = extras.region || (cell ? (dialogRoot(node) ? "dialog" : "table") : regionOf(node));
    const section = compactText(extras.section || (cell ? nearbyHeading(tableHostOf(node)) : "") || nearbyHeading(node));
    const ref = `c${snapshotControls.length + 1}`;
    mark(node, ref, { label, region, columnLabel: cell ? label : "" });
    snapshotControls.push({
      ref,
      label: cleanLabel(label) || placeholder,
      name: compactText(input?.name || input?.id || node.getAttribute?.("name") || node.getAttribute?.("id")),
      control_kind: controlKind,
      placeholder,
      region,
      section,
      readonly: locked,
      disabled: locked,
      range,
      options: extras.options || optionTexts(node),
      selector: placeholder && !placeholder.includes(" → ")
        ? `placeholder=${placeholder}`
        : (label ? `label=${cleanLabel(label)}` : (placeholder ? `placeholder=${placeholder.split(" → ")[0]}` : `ref=${ref}`)),
    });
  };
  for (const host of document.querySelectorAll(WIDGET_HOST_SELECTOR)) {
    if (isCompositeHost(host)) continue;
    pushSnapshotControl(host);
  }
  for (const node of document.querySelectorAll(tableFieldSelector)) {
    if (seenSnap.has(node) || inPagination(node)) continue;
    if (node.matches?.("input, textarea, select")) {
      const host = node.closest(".el-select, .ant-select, .el-input-number, [role='combobox']");
      if (host && host !== node) continue;
    }
    const cell = node.closest("td, th, .el-table__cell, .ant-table-cell, .vxe-body--column");
    pushSnapshotControl(node, {
      label: cell ? columnLabel(cell) : "",
      region: dialogRoot(node) ? "dialog" : "table",
      section: nearbyHeading(tableHostOf(node)),
    });
  }
  for (const node of document.querySelectorAll("input, textarea, select, [contenteditable='true']")) {
    if (seenSnap.has(node) || inPagination(node)) continue;
    if (closestWidget(node) || node.closest?.(WIDGET_HOST_SELECTOR)) continue;
    if (node.matches?.("input[type='hidden']")) continue;
    if (node.matches?.("input[type='radio'], input[type='checkbox']") && !snapshotVisible(node)) continue;
    const cell = node.closest?.("td, th, .el-table__cell, .ant-table-cell, .vxe-body--column");
    pushSnapshotControl(node, {
      label: cell ? columnLabel(cell) : nearbyLabel(node),
      control_kind: detectKind(node, nearbyLabel(node) || node.getAttribute?.("placeholder") || ""),
      region: cell ? (dialogRoot(node) ? "dialog" : "table") : undefined,
    });
  }
  for (const node of document.querySelectorAll("button, [role='button'], a, a.ant-btn, .el-button, input[type='button'], input[type='submit']")) {
    if (!snapshotVisible(node) || seenSnap.has(node) || inPagination(node) || inChrome(node)) continue;
    const raw = cleanLabel(
      node.getAttribute?.("aria-label")
      || node.innerText
      || node.textContent
      || node.getAttribute?.("name")
      || textOf(node),
    );
    if (!isBusinessLabel(raw)) continue;
    const isBtn = Boolean(node.matches?.("button, [role='button'], a.ant-btn, .el-button, input[type='button'], input[type='submit']"));
    const genericPicker = /^(选择|选人|选部门|选组织)$/.test(raw.replace(/\s+/g, ""));
    if (!isBtn && !genericPicker) continue;
    const near = cleanLabel(nearbyLabel(node));
    const label = genericPicker && near ? near : raw;
    const selector = genericPicker && near
      ? `label=${near}`
      : (isBtn ? `role=button[name="${raw}"]` : `text=${raw}`);
    seenSnap.add(node);
    const ref = `a${snapshotActions.length + 1}`;
    mark(node, ref);
    snapshotActions.push({
      ref,
      label,
      kind: "button",
      region: regionOf(node),
      selector,
    });
  }
  const pickRowName = (text) => {
    const parts = compactText(text).split(" ").filter(Boolean);
    const named = parts.find((part) => /[\u4e00-\u9fff]{2,8}/.test(part) && !/未知/.test(part));
    return named || parts.find((part) => part.length >= 2 && !/^\d+$/.test(part)) || "";
  };
  const pushSelectable = (node, label, kind) => {
    const text = cleanLabel(label);
    if (!isBusinessLabel(text) || seenSnap.has(node) || inPagination(node) || inChrome(node) || snapshotActions.length >= 80) {
      return;
    }
    seenSnap.add(node);
    const ref = `a${snapshotActions.length + 1}`;
    mark(node, ref);
    snapshotActions.push({
      ref,
      label: text,
      kind,
      region: regionOf(node),
      selector: kind === "checkbox" ? `role=checkbox[name="${text}"]` : `text=${text}`,
    });
  };
  const seenTreeLabel = new Set();
  for (const node of document.querySelectorAll("[role='treeitem'], .el-tree-node__label, .ant-tree-title")) {
    if (!snapshotVisible(node) || inChrome(node) || inPagination(node)) continue;
    const text = cleanLabel(node.getAttribute?.("aria-label") || textOf(node));
    if (!isBusinessLabel(text) || seenTreeLabel.has(text)) continue;
    seenTreeLabel.add(text);
    pushSelectable(node, text, "treeitem");
  }
  for (const node of document.querySelectorAll(
    "[role='checkbox'], .el-checkbox, .ant-checkbox-wrapper, label.el-checkbox, input[type='checkbox']",
  )) {
    const host = node.closest?.(".el-checkbox, .ant-checkbox-wrapper, label, tr, .el-table__row, .ant-table-row") || node;
    if (seenSnap.has(host) || seenSnap.has(node) || inPagination(host)) continue;
    const box = host.getBoundingClientRect?.();
    if (!box || box.width < 2 || box.height < 2) continue;
    const row = host.closest?.("tr, .el-table__row, .ant-table-row");
    const name = pickRowName(textOf(row))
      || compactText(node.getAttribute?.("aria-label") || host.getAttribute?.("aria-label") || (!row ? textOf(host) : ""))
      || "勾选";
    pushSelectable(host, name, "checkbox");
    if (row) {
      const rowName = pickRowName(textOf(row));
      if (rowName) pushSelectable(row, rowName, "row");
    }
  }
  for (const cell of document.querySelectorAll(
    "[role='dialog'] td, .el-dialog td, .ant-modal td, [aria-modal='true'] td",
  )) {
    if (!snapshotVisible(cell) || seenSnap.has(cell) || inPagination(cell)) continue;
    if (cell.querySelector?.("input, textarea, button, .el-checkbox, .ant-checkbox")) continue;
    const text = compactText(cell.innerText || cell.textContent);
    if (!text || text.length > 24 || /^\d+$/.test(text)) continue;
    if (/^序号$/.test(text)) continue;
    pushSelectable(cell, text, "row");
  }
  const seenOption = new Set();
  for (const node of document.querySelectorAll("[role='option'], .el-select-dropdown__item, .ant-select-item-option")) {
    if (!snapshotVisible(node)) continue;
    const text = compactText(node.innerText || node.textContent);
    if (!text || seenOption.has(text)) continue;
    seenOption.add(text);
    snapshotOptions.push(text);
  }

  return {
    url: location.href,
    title: document.title || "",
    controls: snapshotControls,
    actions: snapshotActions,
    options: snapshotOptions,
    visible: compactRows.slice(0, 120),
  };
}

export function collectVisibleControlsInPage() {
  return collectPageFacts().visible;
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
