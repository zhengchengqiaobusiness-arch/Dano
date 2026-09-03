export const PAGE_HELPERS = String.raw`
  const clean = (value) => String(value || "").replace(/\s+/g, " ").trim().slice(0, 12000);
  const generatedName = (value) => /^(el-id-\d+|el-[a-z]+-\d+|input-\d+|select-\d+|aria-id|:r[0-9a-z]+$)/i.test(String(value || ""));
  const isVisible = (el) => {
    if (!(el instanceof Element)) return false;
    if (el.closest("[hidden], [aria-hidden='true']")) return false;
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  };
  const queryDeep = (root, selector) => {
    const found = [];
    const visit = (node) => {
      if (!node || found.length > 800) return;
      try { found.push(...node.querySelectorAll(selector)); } catch { /* ignore */ }
      const all = node.querySelectorAll ? node.querySelectorAll("*") : [];
      for (const kid of all) if (kid.shadowRoot) visit(kid.shadowRoot);
    };
    visit(root);
    return found;
  };
  const FORM_ITEM_SEL = '.el-form-item, .ant-form-item, .arco-form-item, .n-form-item, .van-field, [class*="form-item"]';
  const FORM_LABEL_SEL = 'label, .el-form-item__label, .ant-form-item-label, .arco-form-item-label, .n-form-item-label, .van-field__label';
  const DIALOG_SEL = '[role="dialog"], [role="alertdialog"], .el-dialog, .el-drawer, .el-overlay-dialog, .ant-modal, .ant-drawer, .arco-modal, .arco-drawer';
  const PICKER_SEL = '.el-picker-panel, .el-select-dropdown, .el-cascader__dropdown, .el-picker__popper, .el-popper.el-date-picker, .ant-picker-dropdown, .ant-select-dropdown, .arco-picker-container, .arco-select-dropdown, [class*="picker-panel"], [class*="picker-dropdown"]';
  const OPTION_SEL = '[role="option"], [role="menuitem"], .el-select-dropdown__item, .el-cascader-node, .el-autocomplete-suggestion__list li, .ant-select-item-option, .arco-select-option, .n-base-select-option';
  const EMPTY_VALUE = /^(请选择|请输入|请填写|请挑选|select|please select|please enter|please choose|choose|yyyy-mm-dd|年\/月\/日)/i;
  const PROMPT_ONLY = /^(请选择|请输入|请填写|请挑选|select|please select|please enter|please choose|choose)[.…]?$/i;
  const DATE_PLACEHOLDER = /yyyy-mm-dd|年\/月\/日/i;
  const UPLOAD_LABEL = /上传|附件|文件|图片|image|upload|attachment|file/i;
  const PLUS_ONLY = /^(＋|\+|添加|选择)$/;
  const ACTION_ONLY = /^(新增|添加一行|添加明细|创建|导入|导出|删除|搜索|查询|重置|提交|确定|取消|关闭|保存|返回)$/;
  const CHROME_SEL = "nav, header, .el-menu, .ant-menu, .el-pagination, .ant-pagination, [class*='toolbar'], [class*='header-bar']";
  const SLOT_HOST_SEL = "[class*='process-node'], [class*='workflow-node'], [class*='user-select'], [class*='assignee'], [class*='approver'], [class*='card'], [class*='node'], [class*='step'], [class*='activity']";
  const WIDE_SEL = DIALOG_SEL + ", form, [role='form'], body, main, header, nav, aside, footer, .el-overlay, .ant-modal-wrap, [class*='overlay']";
  const FIELD_GROUP_SEL = FORM_ITEM_SEL + ", label, dt, dd, li, [class*='form-field'], [class*='field-item'], [class*='form-row'], [class*='field-row']";
  const FIELD_CONTROL_SEL = "input, textarea, select, [role='combobox'], [role='textbox'], [contenteditable='true'], button, [role='button'], [aria-haspopup]";

  const isWide = (el) => {
    if (!(el instanceof Element)) return true;
    if (el === document.body || el === document.documentElement) return true;
    if (el.matches(WIDE_SEL)) return true;
    const cls = String(el.className || "");
    return /(?:^|\s)(el-form|ant-form|arco-form|n-form)(?:\s|$)/.test(cls);
  };

  const formItemOf = (el) => el.closest(FORM_ITEM_SEL);
  const FIELD_NAME_ATTRS = ["name", "data-field", "data-name", "data-key", "data-model"];

  const nameOf = (el) => {
    let node = el;
    for (let i = 0; i < 8 && node && node.nodeType === 1; i++, node = node.parentElement) {
      if (i > 0 && node.matches && node.matches("form, [role='form'], [role='dialog'], " + DIALOG_SEL)) break;
      for (const attr of FIELD_NAME_ATTRS) {
        const value = node.getAttribute(attr);
        if (value && !generatedName(value)) return value;
      }
      if (i === 0) {
        const id = node.getAttribute("id");
        if (id && !generatedName(id)) return id;
      }
    }
    return undefined;
  };

  const tableHeaderOf = (el) => {
    const cell = el.closest("td, th, .el-table__cell, .ant-table-cell");
    const row = cell?.closest("tr, .el-table__row, .ant-table-row");
    if (!cell || !row) return "";
    const cells = [...row.children].filter((node) => node.matches("td, th, .el-table__cell, .ant-table-cell"));
    const index = cells.indexOf(cell);
    const host = el.closest(".el-table, .ant-table") || el.closest("table");
    const headerRow = host?.querySelector(".el-table__header tr, .el-table__header-wrapper tr, .ant-table-thead tr, thead tr");
    const headers = headerRow
      ? [...headerRow.children].filter((node) => node.matches("th, td, .el-table__cell, .ant-table-cell"))
      : [...(host?.querySelectorAll("th, .el-table__header .el-table__cell, .ant-table-thead th") || [])];
    return index >= 0 ? clean(headers[index]?.textContent || "") : "";
  };

  const labelOf = (el) => {
    if (el.labels?.length) return clean([...el.labels].map((item) => item.textContent).join(" "));
    const aria = el.getAttribute("aria-label");
    if (aria && !EMPTY_VALUE.test(aria) && !generatedName(aria)) return clean(aria);
    const labelled = el.getAttribute("aria-labelledby");
    if (labelled) {
      const named = clean(labelled.split(/\s+/).map((id) => document.getElementById(id)?.textContent || "").join(" "));
      if (named) return named;
    }
    const parentLabel = el.closest("label");
    if (parentLabel) {
      const clone = parentLabel.cloneNode(true);
      clone.querySelectorAll("input,select,textarea,.el-select,.ant-select").forEach((node) => node.remove());
      const text = clean(clone.textContent);
      if (text) return text;
    }
    const item = formItemOf(el);
    const itemLabel = item?.querySelector(FORM_LABEL_SEL);
    if (itemLabel?.textContent) return clean(itemLabel.textContent);
    const header = tableHeaderOf(el);
    if (header) return header;
    const placeholder = el.getAttribute("placeholder") || "";
    if (placeholder && !EMPTY_VALUE.test(placeholder)) return clean(placeholder);
    const nearby = nearbyLabel(el);
    if (nearby) return nearby;
    return "";
  };

  const nearbyLabel = (el) => {
    const take = (node) => {
      if (!(node instanceof Element) || node.contains(el) || el.contains(node)) return "";
      if (node.matches("input, textarea, select, button, [role='combobox'], [role='button']")) return "";
      if (node.matches("h1, nav, header")) return "";
      const clone = node.cloneNode(true);
      clone.querySelectorAll("input, textarea, select, button, [role='combobox']").forEach((item) => item.remove());
      const text = clean(clone.textContent);
      return text && text.length <= 40 && !EMPTY_VALUE.test(text) && !PLUS_ONLY.test(text) && !ACTION_ONLY.test(text) ? text : "";
    };
    let sib = el.previousElementSibling;
    for (let i = 0; i < 5 && sib; i++, sib = sib.previousElementSibling) {
      const text = take(sib);
      if (text) return text;
    }
    const dt = el.closest("dd")?.previousElementSibling;
    if (dt && dt.tagName === "DT") {
      const text = take(dt);
      if (text) return text;
    }
    let host = el.parentElement;
    for (let i = 0; i < 4 && host && !isWide(host); i++, host = host.parentElement) {
      const er = el.getBoundingClientRect();
      for (const child of host.children) {
        if (child === el || child.contains(el)) continue;
        const r = child.getBoundingClientRect();
        const left = r.right <= er.left + 12 && Math.abs((r.top + r.bottom) / 2 - (er.top + er.bottom) / 2) < 40;
        const above = r.bottom <= er.top + 12 && Math.abs(r.left - er.left) < 140;
        if (!(left || above)) continue;
        const text = take(child);
        if (text) return text;
      }
    }
    return "";
  };

  const identityPlaceholder = (el) => {
    const ph = clean(el.getAttribute("placeholder") || "");
    if (!ph || PROMPT_ONLY.test(ph) || DATE_PLACEHOLDER.test(ph)) return "";
    return ph;
  };

  const selectorOf = (el) => {
    const actionRole = el.getAttribute("role") || "";
    if (el.matches("button, [type='submit'], [type='button']") || actionRole === "button" || actionRole === "tab") {
      const name = clean(el.getAttribute("aria-label") || el.textContent || "");
      if (name && name.length <= 40 && !generatedName(name) && !PLUS_ONLY.test(name)) {
        return "role=" + (actionRole || "button") + "[name=\"" + name + "\"]";
      }
    }
    const placeholder = identityPlaceholder(el) || "";
    if (placeholder) return "placeholder=" + placeholder;
    if (el.id && !generatedName(el.id)) return "#" + CSS.escape(el.id);
    const label = labelOf(el);
    if (label && label.length <= 40) return "label=" + label;
    if (placeholder) return "placeholder=" + placeholder;
    const role = el.getAttribute("role") || (el.matches("button, .el-button, .ant-btn") ? "button" : "");
    const roleName = clean(el.getAttribute("aria-label") || el.textContent || "");
    if (role && roleName && roleName.length <= 40 && !generatedName(roleName)) return "role=" + role + '[name="' + roleName + '"]';
    const testid = el.getAttribute("data-testid");
    if (testid) return '[data-testid="' + CSS.escape(testid) + '"]';
    const name = nameOf(el);
    if (name) return el.tagName.toLowerCase() + '[name="' + CSS.escape(name) + '"]';
    const parts = [];
    let node = el;
    for (let i = 0; node && node.nodeType === 1 && i < 4; i++, node = node.parentElement) {
      let part = node.tagName.toLowerCase();
      const classes = [...node.classList].slice(0, 2);
      if (classes.length) part += "." + classes.map((item) => CSS.escape(item)).join(".");
      parts.unshift(part);
    }
    return parts.join(" > ");
  };

  const chooserHostOf = (el) => {
    let node = el.matches && el.matches("input, textarea") ? el.parentElement : el;
    for (let i = 0; i < 6 && node && node !== document.body; i += 1, node = node.parentElement) {
      if (isWide(node) || node.matches(FORM_ITEM_SEL)) break;
      const cls = String(node.className || "");
      const role = node.getAttribute("role") || "";
      if (/input-wrapper|suffix|caret|search|selection-item/i.test(cls)) continue;
      if (role === "combobox" && !node.matches("input, textarea")) return node;
      if (/(?:^|\s)(el-select|ant-select|arco-select|n-select|el-cascader|el-date-editor|ant-picker|arco-picker)(?:\s|$)/.test(cls)) return node;
      if (/(?:^|\s|_|-)(select|picker|cascader|date-editor)(?:\s|$|_)/i.test(cls) && !/dropdown|panel|item|option/i.test(cls)) return node;
    }
    return null;
  };

  const hostDisplay = (host) => {
    if (!(host instanceof Element)) return "";
    const skip = (node) => node.closest("[class*='input-wrapper'], [class*='search'], [class*='suffix'], [class*='caret']");
    return [...host.querySelectorAll("[class*='selected'], [class*='selection-item'], [class*='placeholder'], [class*='tag'], [class*='value']")]
      .filter((node) => !skip(node))
      .map((node) => clean(node.textContent))
      .find((text) => text && !EMPTY_VALUE.test(text) && !PLUS_ONLY.test(text)) || "";
  };

  const isChooserFilter = (el) => {
    if (!(el instanceof HTMLInputElement)) return false;
    if (/select__input|selection-search|search-input|filter/i.test(String(el.className || ""))) return true;
    return Boolean(el.getAttribute("aria-autocomplete") && chooserHostOf(el));
  };

  const displayValue = (el) => {
    if (el instanceof HTMLInputElement && (el.type === "checkbox" || el.type === "radio")) return el.checked ? "true" : "";
    if (el instanceof HTMLSelectElement) {
      return [...el.selectedOptions].map((item) => clean(item.textContent || item.value)).filter(Boolean).join(",");
    }
    const host = chooserHostOf(el);
    const shown = hostDisplay(host);
    if (shown) return shown;
    if (isChooserFilter(el)) return "";
    if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) {
      const own = clean(el.value);
      if (own && !EMPTY_VALUE.test(own)) return own;
    }
    if (el.isContentEditable) return clean(el.textContent);
    return "";
  };

  const isEmptyValue = (value) => {
    const text = clean(value);
    return !text || EMPTY_VALUE.test(text);
  };

  const isUploadWidget = (el, label) => {
    if (el instanceof HTMLInputElement && el.type === "file") return true;
    if (el.closest(".el-upload, .el-upload-dragger, .ant-upload, .arco-upload, .n-upload")) return true;
    return UPLOAD_LABEL.test(label || "");
  };

  const isDisabledWidget = (el) => {
    if (el.getAttribute("aria-disabled") === "true" || el.hasAttribute("disabled")) return true;
    if (el.closest("[aria-disabled='true'], .is-disabled")) return true;
    if (el.hasAttribute("readonly")) {
      if (el.getAttribute("role") === "combobox" || el.getAttribute("aria-haspopup") || el.closest("[role='combobox'], [aria-haspopup]")) return false;
    }
    return false;
  };

  const widgetKind = (item, el, label) => {
    const type = (el.getAttribute("type") || el.tagName).toLowerCase();
    const role = el.getAttribute("role") || "";
    const popup = el.getAttribute("aria-haspopup") || item?.getAttribute?.("aria-haspopup") || "";
    const placeholder = el.getAttribute("placeholder") || "";
    const blob = [type, placeholder, label, popup, role].join(" ");
    if (isUploadWidget(el, label) || type === "file") return "upload";
    if (type === "checkbox" || role === "checkbox" || role === "switch") return "checkbox";
    if (type === "radio" || role === "radio") return "radio";
    if (/^(date|datetime-local|time|month|week)$/.test(type) || /日期|时间|(^|[^a-z])date|time/i.test(blob)) return "date";
    if (/dialog/i.test(popup)) return "picker";
    if (el instanceof HTMLSelectElement || role === "combobox" || /listbox|menu/i.test(popup) || el.closest("[role='combobox']")) return "select";
    if (el.hasAttribute("readonly") && !isDisabledWidget(el) && (EMPTY_VALUE.test(placeholder) || /请选择|please select/i.test(blob))) return "picker";
    if (isDisabledWidget(el)) return "readonly";
    if (type === "number" || el.getAttribute("inputmode") === "decimal" || el.getAttribute("inputmode") === "numeric") return "number";
    if (el.tagName === "TEXTAREA" || role === "textbox" || el.isContentEditable) return "textarea";
    return "text";
  };

  const optionRecord = (el) => {
    const label = clean(el.textContent);
    if (!label) return null;
    const attr = el.getAttribute("data-value") || el.getAttribute("value") || el.getAttribute("data-id");
    const raw = el instanceof HTMLOptionElement && el.value !== ""
      ? el.value
      : (attr !== null && attr !== "" ? attr : undefined);
    return { value: raw !== undefined && raw !== null && raw !== "" ? raw : label, label };
  };

  const optionsOf = (el) => {
    if (el instanceof HTMLSelectElement) {
      return [...el.options].slice(0, 300).map((item) => ({ value: item.value, label: clean(item.textContent) }));
    }
    const listId = el.getAttribute("list");
    if (listId) {
      const list = document.getElementById(listId);
      if (list instanceof HTMLDataListElement) {
        return [...list.options].slice(0, 300).map((item) => ({ value: item.value, label: clean(item.label || item.value) }));
      }
    }
    return undefined;
  };

  const collectOptionRecords = (root) => {
    const dropdowns = [...document.querySelectorAll(".el-select-dropdown, .el-cascader__dropdown, .el-autocomplete-suggestion, .ant-select-dropdown, .arco-select-dropdown, [role='listbox']")].filter(isVisible);
    const searchRoot = dropdowns.at(-1) || root || document;
    return [...searchRoot.querySelectorAll(OPTION_SEL)]
      .filter((el) => isVisible(el)
        && !el.closest("nav, aside, .el-menu, .ant-menu, .el-pagination, .ant-pagination")
        && !el.classList.contains("is-disabled")
        && !el.getAttribute("disabled")
        && !el.classList.contains("ant-select-item-option-disabled"))
      .slice(0, 200)
      .map(optionRecord)
      .filter(Boolean);
  };

  const collectVisibleOptions = (root) => collectOptionRecords(root).map((item) => item.label);

  const rangeInputsOf = (item) => {
    if (!(item instanceof Element)) return [];
    const rangeHost = item.querySelector(".el-date-editor--daterange, .el-date-editor--datetimerange, .el-range-editor, .ant-picker-range, .arco-picker-range, [class*='picker-range'], [class*='daterange']")
      || ((item.classList.contains("el-date-editor") || /picker-range|daterange|date-editor/i.test(item.className)) && item.querySelectorAll(".el-range-input, input").length >= 2 ? item : null);
    const inputs = [...(rangeHost || item).querySelectorAll("input")].filter(isVisible);
    if (inputs.length >= 2 && widgetKind(item, inputs[0], labelOf(inputs[0])) === "date") return inputs.slice(0, 2);
    return [];
  };

  const isPickerHost = (el) => Boolean(el.matches?.(PICKER_SEL) || el.closest(PICKER_SEL) || /picker-panel|picker-dropdown|datepicker/i.test(el.className || ""));

  const activeScope = () => {
    const dialogs = [...document.querySelectorAll(DIALOG_SEL)].filter((el) => isVisible(el) && !isPickerHost(el));
    return dialogs.at(-1) || document.body;
  };

  const scopeName = (root) => root && root !== document.body && root.closest(DIALOG_SEL) ? "dialog" : "page";

  const compactBox = (node) => {
    if (!(node instanceof Element) || isWide(node)) return false;
    const rect = node.getBoundingClientRect();
    return rect.height > 0 && rect.height <= 240 && rect.width > 0 && rect.width <= 960;
  };

  const compactStep = (node) => {
    if (!(node instanceof Element) || isWide(node)) return false;
    const rect = node.getBoundingClientRect();
    return rect.height > 8 && rect.height <= 280 && rect.width > 8;
  };

  const slotHost = (el) => {
    let node = el.parentElement;
    let compact = el.parentElement;
    for (let i = 0; i < 6 && node && !isWide(node); i += 1, node = node.parentElement) {
      if (compactBox(node) || compactStep(node)) compact = node;
      if (node.matches(SLOT_HOST_SEL) && compactStep(node)) return node;
    }
    return compact || el.parentElement;
  };

  const isPlusControl = (el) => {
    const text = clean(el.getAttribute("aria-label") || el.textContent || "");
    const compact = text.replace(/\s+/g, "");
    if (PLUS_ONLY.test(compact) || /^选择.{0,6}$/.test(compact)) return true;
    const looksAdd = /plus|add/i.test(String(el.className || "")) || el.querySelector("[class*='plus'], [class*='add']");
    return Boolean(looksAdd) && compact.length <= 2;
  };

  const headingOf = (host) => {
    if (!(host instanceof Element)) return null;
    return host.querySelector("h1, h2, h3, h4, [class*='title'], [class*='name'], [class*='head'], [class*='label']");
  };

  const headingTextOf = (host) => clean(headingOf(host)?.textContent || "").replace(/[:：*]\s*$/g, "");

  const personChip = (host) => {
    if (!(host instanceof Element)) return "";
    const title = headingTextOf(host);
    const heading = headingOf(host);
    return [...host.querySelectorAll("[class*='tag'], [class*='user'], [class*='nickname'], [class*='selected'], span, strong")]
      .filter((node) => node !== heading && !(heading && heading.contains(node)) && !node.matches("button, [role='button'], [class*='avatar'], [class*='plus'], [class*='icon']"))
      .map((node) => clean(node.textContent))
      .find((text) => text && text !== title && !PLUS_ONLY.test(text) && !isEmptyValue(text) && text.length <= 40) || "";
  };

  const isEmptyWell = (el) => {
    if (!(el instanceof HTMLElement) || !isVisible(el) || isWide(el)) return false;
    if (el.matches("input, textarea, select, a, [role='tab']")) return false;
    const box = el.getBoundingClientRect();
    if (box.width < 8 || box.height < 8 || box.width > 96 || box.height > 96) return false;
    const text = clean(el.textContent || el.getAttribute("aria-label") || "");
    if (ACTION_ONLY.test(text.replace(/\s+/g, ""))) return false;
    if (text && !PLUS_ONLY.test(text.replace(/\s+/g, "")) && !isEmptyValue(text) && text.length > 2) return false;
    return true;
  };

  const emptyWellOf = (host) => {
    if (!(host instanceof Element) || personChip(host)) return null;
    const title = headingTextOf(host);
    if (!title || title.length > 40) return null;
    const heading = headingOf(host);
    const candidates = [...host.querySelectorAll("button, [role='button'], [aria-haspopup], [class*='avatar'], [class*='plus'], [class*='add-user'], [class*='add-btn'], [class*='icon']")]
      .filter((el) => isVisible(el) && el !== heading && !(heading && heading.contains(el)));
    return candidates.find((el) => isPlusControl(el) || isEmptyWell(el)) || null;
  };

  const isPickerSlot = (el) => {
    if (!(el instanceof HTMLElement) || !isVisible(el)) return false;
    if (el.matches("input, textarea, select, [role=combobox]")) return false;
    if (el.closest(PICKER_SEL + ", " + CHROME_SEL)) return false;
    if (isUploadWidget(el, labelOf(el))) return false;
    const text = clean(el.textContent || el.getAttribute("aria-label") || "");
    if (ACTION_ONLY.test(text.replace(/\s+/g, ""))) return false;
    if (/dialog/i.test(el.getAttribute("aria-haspopup") || "") && (isPlusControl(el) || isEmptyValue(text) || PLUS_ONLY.test(text.replace(/\s+/g, "")))) return true;
    if (isPlusControl(el) && !el.closest("thead, .el-table__header, [class*='toolbar']")) return true;
    const host = slotHost(el);
    const empty = !text || isEmptyValue(text) || PLUS_ONLY.test(text.replace(/\s+/g, ""));
    if (host && compactStep(host) && !personChip(host) && (isPlusControl(el) || isEmptyWell(el) || empty)) {
      if (!el.closest("thead, .el-table__header, [class*='toolbar'], nav, header")) return true;
    }
    return false;
  };

  const isFieldControl = (el) => {
    if (!(el instanceof Element) || el.closest(PICKER_SEL + ", " + CHROME_SEL)) return false;
    if (!isVisible(el)) {
      const host = chooserHostOf(el);
      if (!(host && isVisible(host))) return false;
    }
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (/hidden|submit|reset|image/.test(type)) return false;
    if (el.matches("button, [role='button']") && !isPickerSlot(el) && !/dialog|listbox|menu/i.test(el.getAttribute("aria-haspopup") || "") && el.getAttribute("role") !== "combobox") return false;
    return true;
  };

  const compactFieldGroup = (node) => {
    if (!(node instanceof Element) || isWide(node)) return false;
    const rect = node.getBoundingClientRect();
    if (rect.height > 240 || rect.width > 960) return false;
    const controls = [...node.querySelectorAll(FIELD_CONTROL_SEL)].filter(isFieldControl);
    return controls.length >= 1 && controls.length <= 6;
  };

  const fieldGroupOf = (labelEl) => {
    let node = labelEl;
    while (node && node.nodeType === 1) {
      if (isWide(node) && node !== labelEl) return labelEl.matches("label, dt") ? labelEl : null;
      const emptyLabel = node === labelEl && node.matches("label, dt") && !node.querySelector(FIELD_CONTROL_SEL);
      if (!emptyLabel && (node.matches(FIELD_GROUP_SEL) || (node !== labelEl && compactFieldGroup(node)))) return node;
      node = node.parentElement;
    }
    return null;
  };

  const labelTextOf = (el) => {
    const clone = el.cloneNode(true);
    clone.querySelectorAll(FIELD_CONTROL_SEL).forEach((item) => item.remove());
    return clean(clone.textContent).replace(/[:：*]\s*$/g, "");
  };

  const uniqueControls = (els) => {
    const out = [];
    const seen = new Set();
    const ranked = [...els].sort((left, right) => Number(isChooserFilter(left)) - Number(isChooserFilter(right)));
    for (const el of ranked) {
      if (!el || seen.has(el)) continue;
      const host = chooserHostOf(el);
      if (host && seen.has(host)) continue;
      seen.add(el);
      if (host) seen.add(host);
      out.push(el);
    }
    return out;
  };

  const controlsInGroup = (group) => uniqueControls(
    [...queryDeep(group, FIELD_CONTROL_SEL)].filter(isFieldControl)
  );

  const controlForLabel = (root, name) => {
    const want = clean(name);
    if (!want || !root) return null;
    const numbered = want.match(/^(.+)-(\d+)$/);
    const base = numbered ? clean(numbered[1]) : want;
    const index = numbered ? Number(numbered[2]) - 1 : 0;
    const escape = (value) => String(value).replace(/[.*+?^$()|[\]\\]/g, function (ch) { return "\\" + ch; });
    const promptName = new RegExp("^(请选择|请输入|请填写|please select|please enter|please choose|select)?\\s*" + escape(want) + "$", "i");
    const pickFrom = (controls) => {
      if (!controls.length) return null;
      if (numbered) return controls[index] || null;
      return controls.length === 1 ? controls[0] : null;
    };
    const afterLabel = (lab) => {
      const id = lab.getAttribute && lab.getAttribute("for");
      const doc = lab.getRootNode ? lab.getRootNode() : document;
      if (id && doc.getElementById && !numbered) {
        const target = doc.getElementById(id);
        if (target && isFieldControl(target)) return target;
      }
      const group = fieldGroupOf(lab);
      if (group && group !== lab) {
        const controls = controlsInGroup(group);
        const after = controls.filter((item) => lab === item || lab.contains(item) || (lab.compareDocumentPosition(item) & Node.DOCUMENT_POSITION_FOLLOWING));
        const picked = pickFrom(after.length ? after : controls);
        if (picked) return picked;
      }
      const siblingControls = [];
      let sib = lab.nextElementSibling;
      while (sib) {
        if (isWide(sib) || sib.matches(FORM_LABEL_SEL)) break;
        if (isFieldControl(sib)) siblingControls.push(sib);
        siblingControls.push(...controlsInGroup(sib));
        if (!numbered && siblingControls.length) break;
        sib = sib.nextElementSibling;
      }
      return pickFrom(uniqueControls(siblingControls));
    };
    const exactLabs = [];
    const seenLab = new Set();
    for (const lab of queryDeep(root, "label, " + FORM_LABEL_SEL)) {
      if (labelTextOf(lab) !== want || seenLab.has(lab) || !isVisible(lab)) continue;
      seenLab.add(lab);
      exactLabs.push(lab);
    }
    if (numbered || exactLabs.length <= 1) {
      for (const lab of exactLabs) {
        const picked = afterLabel(lab);
        if (picked) return picked;
      }
      const candidates = queryDeep(root, FORM_LABEL_SEL + ", span, p, div, dt, legend, h2, h3, h4, [class*='label'], [class*='title'], [class*='name'], [class*='head']")
        .filter((el) => isVisible(el) && !el.matches("h1, nav, header") && (labelTextOf(el) === want || labelTextOf(el) === base) && labelTextOf(el).length <= 40);
      for (const lab of candidates) {
        const picked = afterLabel(lab);
        if (picked) return picked;
      }
    }
    for (const el of queryDeep(root, FIELD_CONTROL_SEL)) {
      if (!isFieldControl(el) || isChooserFilter(el)) continue;
      if (clean(el.getAttribute("aria-label")) === want) return el;
      const placeholder = clean(el.getAttribute("placeholder") || "");
      if (placeholder === want || identityPlaceholder(el) === want || promptName.test(placeholder)) return el;
    }
    for (const el of queryDeep(root, "button, [role='button'], [aria-haspopup], [class*='plus'], [class*='add-user'], [class*='avatar']")) {
      if (!isPickerSlot(el)) continue;
      const label = slotLabel(el);
      if (label === want || label === base) return el;
    }
    for (const host of queryDeep(root, SLOT_HOST_SEL)) {
      if (!isVisible(host) || !compactStep(host)) continue;
      const title = headingTextOf(host);
      if (title !== want && title !== base) continue;
      const well = emptyWellOf(host);
      if (well) return well;
    }
    return null;
  };

  const markLabeledControl = (root, name, mark) => {
    const el = controlForLabel(root, name);
    if (!el) return false;
    const host = chooserHostOf(el);
    const target = isVisible(el) ? el : (host && isVisible(host) ? host : el);
    target.setAttribute("data-bss-locate", mark);
    return true;
  };

  const selectedName = (el) => {
    const host = slotHost(el);
    const title = headingTextOf(host);
    const chip = personChip(host);
    if (chip && chip !== title) return chip;
    const own = clean(el.textContent || "");
    if (own && own !== title && !PLUS_ONLY.test(own) && !isEmptyValue(own) && own.length > 1) return own;
    return "";
  };

  const slotLabel = (el) => {
    const host = slotHost(el);
    const heading = host?.querySelector("h1, h2, h3, h4, [class*='title'], [class*='name'], [class*='head'], [class*='label']");
    const headingText = clean(heading?.textContent || "");
    if (headingText && headingText.length <= 40 && headingText !== clean(el.textContent || "")) return headingText;
    const aria = clean(el.getAttribute("aria-label") || "");
    if (aria && !PLUS_ONLY.test(aria) && !EMPTY_VALUE.test(aria)) return aria;
    return labelOf(el) || nearbyLabel(el) || "待选择";
  };

  const fieldFromPicker = (el) => {
    const label = slotLabel(el);
    const value = selectedName(el);
    return {
      label,
      name: nameOf(el),
      selector: "label=" + label,
      kind: "picker",
      filled: Boolean(value),
      skip: false,
      disabled: isDisabledWidget(el),
      required: !value,
      invalid: false,
      value,
      scope: scopeName(el)
    };
  };

  const distinctLabel = (el, shared, index, total) => {
    const ph = identityPlaceholder(el);
    if (total > 1 && ph && ph !== shared) return ph;
    if (total > 1 && shared) return clean(shared) + "-" + (index + 1);
    return shared || ph || "";
  };

  const itemControls = (item) => {
    const range = rangeInputsOf(item);
    if (range.length >= 2) return range;
    const fields = uniqueControls([...item.querySelectorAll("input, textarea, select, [role=combobox], [contenteditable=true]")]
      .filter((el) => !el.closest(PICKER_SEL) && isFieldControl(el)));
    const slots = [...item.querySelectorAll("button, [role=button], [class*='add-user'], [class*='user-select'], [class*='plus'], [class*='avatar']")].filter(isPickerSlot);
    return [...fields, ...slots];
  };

  const fieldFromControl = (el, item) => {
    if (!(el instanceof HTMLElement)) return null;
    if (!isVisible(el) && !(chooserHostOf(el) && isVisible(chooserHostOf(el)))) return null;
    if (el.closest(PICKER_SEL + ", .el-pagination, .ant-pagination, .arco-pagination")) return null;
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (/hidden|submit|button|reset|image/.test(type)) return null;
    const label = distinctLabel(el, labelOf(el) || clean(item?.querySelector?.(FORM_LABEL_SEL)?.textContent || "") || nearbyLabel(el) || nameOf(el) || "字段", 0, 1);
    const kind = widgetKind(item, el, label);
    const value = displayValue(el);
    const required = Boolean(item?.classList?.contains("is-required") || el.hasAttribute("required") || el.getAttribute("aria-required") === "true" || el.closest(".is-required"));
    const numericZero = kind === "number" && /^(0+|0*\.0+)$/.test(clean(value));
    const filled = !isEmptyValue(value) && !(required && numericZero);
    const invalid = Boolean(item?.classList?.contains("is-error") || item?.querySelector?.(".el-form-item__error, .ant-form-item-explain-error, .arco-form-item-message"));
    return {
      label,
      name: nameOf(el),
      selector: selectorOf(el),
      kind,
      filled,
      skip: kind === "upload",
      disabled: kind === "readonly" || isDisabledWidget(el),
      required,
      invalid,
      value,
      options: optionsOf(el),
      scope: scopeName(el)
    };
  };

  const collectFormFields = (root) => {
    const fields = [];
    const seen = new Set();
    const add = (field) => {
      if (!field) return;
      const radioKey = field.kind === "radio" && field.name ? field.scope + "|radio|" + field.name : "";
      const key = radioKey || (field.scope + "|" + field.label + "|" + (field.name || field.selector) + "|" + String(field.rangeIndex ?? ""));
      if (seen.has(key)) return;
      seen.add(key);
      fields.push(field);
    };
    for (const item of queryDeep(root, FORM_ITEM_SEL)) {
      if (item.closest(PICKER_SEL + ", .el-pagination, .ant-pagination, .arco-pagination")) continue;
      const range = rangeInputsOf(item);
      const controls = itemControls(item);
      const prop = range.length >= 2 ? nameOf(range[0]) : undefined;
      controls.forEach((el, index) => {
        if (isPickerSlot(el)) {
          add(fieldFromPicker(el));
          return;
        }
        const field = fieldFromControl(el, item);
        if (!field) return;
        const label = distinctLabel(el, field.label, index, controls.length);
        const identity = identityPlaceholder(el);
        add({
          ...field,
          name: prop ? prop + "[" + index + "]" : field.name,
          label,
          selector: identity ? "placeholder=" + identity : (controls.length > 1 ? "label=" + label : field.selector),
          rangeIndex: range.length >= 2 ? index : field.rangeIndex,
          groupIndex: controls.length > 1 ? index : field.rangeIndex
        });
      });
    }
    for (const cell of queryDeep(root, "tbody td, .el-table__body td, .el-table__body .el-table__cell, .ant-table-tbody .ant-table-cell")) {
      const el = cell.querySelector("input, textarea, select, [role=combobox], [contenteditable=true]");
      if (!el || formItemOf(el)) continue;
      add(fieldFromControl(el, cell));
    }
    const seenEls = new Set();
    for (const el of queryDeep(root, "input, textarea, select, [role=combobox], [contenteditable=true], [role=textbox]")) {
      if (formItemOf(el) || isChooserFilter(el) || el.closest("td, .el-table__cell, .ant-table-cell") || seenEls.has(el)) continue;
      const host = el.closest("label") || el.parentElement;
      const range = rangeInputsOf(host);
      if (range.length >= 2) {
        range.forEach((input, index) => {
          seenEls.add(input);
          const field = fieldFromControl(input, host);
          if (!field) return;
          add({
            ...field,
            name: field.name && !/\[\d+\]$/.test(field.name) ? field.name + "[" + index + "]" : field.name,
            label: labelOf(input) || field.label,
            rangeIndex: index
          });
        });
        continue;
      }
      seenEls.add(el);
      add(fieldFromControl(el, host));
    }
    for (const el of queryDeep(root, "button, [role=button], [aria-haspopup], [class*='plus'], [class*='add-user'], [class*='add-btn'], [class*='user-select'], [class*='avatar']")) {
      if (formItemOf(el) || !isPickerSlot(el)) continue;
      add(fieldFromPicker(el));
    }
    for (const host of queryDeep(root, SLOT_HOST_SEL)) {
      if (!isVisible(host) || !compactStep(host) || formItemOf(host) || host.closest(PICKER_SEL + ", " + CHROME_SEL)) continue;
      const well = emptyWellOf(host);
      if (well) add(fieldFromPicker(well));
    }
    for (const el of queryDeep(root, "[class*='user-tag'], [class*='el-tag'], [class*='selected-tag']")) {
      if (!isVisible(el) || !el.closest("[class*='process'], [class*='workflow'], [class*='node'], [class*='card']")) continue;
      const value = clean(el.textContent);
      if (!value || PLUS_ONLY.test(value) || EMPTY_VALUE.test(value)) continue;
      add({ ...fieldFromPicker(el), filled: true, required: false, value, kind: "picker" });
    }
    return fields;
  };

  const collectControls = (root) => queryDeep(root,
    'a,button,input,select,textarea,[contenteditable="true"],[role="button"],[role="combobox"],[role="option"],[role="link"],[role="checkbox"],[role="switch"],[role="radio"],[role="tab"],[role="menuitem"]'
  ).filter(isVisible).slice(0, 250).map((el) => ({
    selector: selectorOf(el),
    tag: el.tagName.toLowerCase(),
    role: el.getAttribute("role") || undefined,
    label: labelOf(el) || el.getAttribute("aria-label") || undefined,
    name: nameOf(el),
    type: el.getAttribute("type") || undefined,
    placeholder: el.getAttribute("placeholder") || undefined,
    required: el.hasAttribute("required") || el.getAttribute("aria-required") === "true",
    disabled: isDisabledWidget(el),
    value: el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement ? String(el.value || "") : undefined,
    filled: !isEmptyValue(displayValue(el)),
    text: clean(el.textContent || el.value || "").slice(0, 300),
    scope: scopeName(el)
  }));

  const formSnapshot = (container) => {
    if (!(container instanceof HTMLElement)) return undefined;
    return collectFormFields(container).map((field) => ({
      name: field.name,
      label: field.label,
      type: field.kind,
      value: field.value,
      required: field.required,
      invalid: field.invalid,
      options: field.options,
      rangeIndex: field.rangeIndex
    }));
  };

  const collectErrors = () => [...new Set([...document.querySelectorAll(
    ".el-form-item__error, .ant-form-item-explain-error, .arco-form-item-message, .n-form-item-feedback, .el-message--error, .el-notification--error, .ant-message-error, .ant-notification-notice-error, [class*='form-item__error'], [class*='form-item-error'], [class*='explain-error']"
  )].filter(isVisible).map((el) => clean(el.textContent)).filter(Boolean))].slice(0, 20);

  const buildSnapshot = () => {
    const scope = activeScope();
    const formFields = collectFormFields(scope);
    const todoFields = formFields.filter((field) => !field.skip && !field.disabled && !field.filled);
    return {
      title: document.title,
      url: location.href,
      text: clean(document.body.innerText),
      scope: scopeName(scope),
      controls: collectControls(scope),
      formFields,
      todoFields,
      todoCount: todoFields.length,
      errors: collectErrors()
    };
  };
`;

export const SNAPSHOT_IN_PAGE = new Function(`${PAGE_HELPERS}\nreturn buildSnapshot();`) as () => unknown;

export const MARK_LABELED_CONTROL = new Function(
  "root",
  "payload",
  `${PAGE_HELPERS}
return markLabeledControl(root, payload.name, payload.mark);`
) as (root: Element, payload: { name: string; mark: string }) => boolean;

export const UI_RECORDER_SCRIPT = `(() => {
  if (window.__BSS_RECORDER_INSTALLED__) return;
  window.__BSS_RECORDER_INSTALLED__ = true;
  ${PAGE_HELPERS}

  const send = (eventType, rawTarget) => {
    const el = rawTarget instanceof HTMLElement ? rawTarget : rawTarget?.parentElement;
    if (!(el instanceof HTMLElement)) return;
    const control = el.matches('input,select,textarea,button,[contenteditable="true"],[role="button"],[role="combobox"],[role="checkbox"],[role="switch"],[role="radio"],[role="option"]')
      ? el
      : el.closest('input,select,textarea,button,[contenteditable="true"],[role="button"],[role="combobox"],[role="checkbox"],[role="switch"],[role="radio"],[role="option"],a') || el;
    const formContainer = control.closest('form, [role="form"], .el-form, .ant-form, .arco-form, [data-form], [role="dialog"], .el-dialog, .el-overlay-dialog, .ant-modal, .arco-modal');
    const payload = {
      eventType,
      pageUrl: location.href,
      selector: selectorOf(control),
      tag: control.tagName.toLowerCase(),
      role: control.getAttribute("role") || undefined,
      text: clean(control.textContent || control.getAttribute("value") || ""),
      label: labelOf(control) || undefined,
      name: nameOf(control),
      inputType: control.getAttribute("type") || undefined,
      value: (() => {
        const key = [control.getAttribute("name"), control.getAttribute("id"), control.getAttribute("autocomplete"), control.getAttribute("type")].filter(Boolean).join(" ");
        if (/password|passwd|pwd|secret|token|credential|current-password|new-password/i.test(key)) return "[REDACTED]";
        return displayValue(control);
      })(),
      options: optionsOf(control) || collectOptionRecords(),
      visibleOptions: collectVisibleOptions(document),
      scope: scopeName(formContainer || control),
      form: formSnapshot(formContainer)
    };
    Promise.resolve(window.__bssRecordUi?.(payload)).catch(() => {});
  };

  const inputTimers = new WeakMap();
  window.__bssFlushUi = (eventType, target) => send(eventType || "input", target || document.activeElement);
  document.addEventListener("click", (event) => send("click", event.composedPath?.()[0] || event.target), true);
  document.addEventListener("input", (event) => {
    const target = event.composedPath?.()[0] || event.target;
    if (!(target instanceof HTMLElement)) return;
    const previous = inputTimers.get(target);
    if (previous) clearTimeout(previous);
    inputTimers.set(target, setTimeout(() => send("input", target), 80));
  }, true);
  document.addEventListener("change", (event) => send("change", event.composedPath?.()[0] || event.target), true);
  document.addEventListener("submit", (event) => send("submit", event.composedPath?.()[0] || event.target), true);
})();`;
