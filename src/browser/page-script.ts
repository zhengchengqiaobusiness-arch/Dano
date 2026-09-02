export const PAGE_HELPERS = String.raw`
  const clean = (value) => String(value || "").replace(/\s+/g, " ").trim().slice(0, 12000);
  const generatedName = (value) => /^(el-id-\d+-\d+|el-[a-z]+-\d+)$/i.test(String(value || ""));
  const isVisible = (el) => {
    if (!(el instanceof Element)) return false;
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  };
  const FORM_ITEM_SEL = '.el-form-item, .ant-form-item, .arco-form-item, .n-form-item, .van-field';
  const FORM_LABEL_SEL = 'label, .el-form-item__label, .ant-form-item-label, .arco-form-item-label, .n-form-item-label, .van-field__label';
  const DIALOG_SEL = '.el-dialog, .el-drawer, .el-overlay-dialog, .ant-modal, .ant-drawer, .arco-modal, .arco-drawer';
  const PICKER_SEL = '.el-picker-panel, .el-select-dropdown, .el-cascader__dropdown, .el-picker__popper, .el-popper.el-date-picker, .ant-picker-dropdown, .ant-select-dropdown, .arco-picker-container, .arco-select-dropdown';
  const OPTION_SEL = '[role="option"], [role="menuitem"], .el-select-dropdown__item, .el-cascader-node, .el-autocomplete-suggestion__list li, .ant-select-item-option, .arco-select-option, .n-base-select-option';
  const EMPTY_VALUE = /^(请选择|请输入|请填写|请挑选|select|please select|please enter|please choose|choose|yyyy-mm-dd|年\/月\/日)/i;
  const UPLOAD_LABEL = /上传|附件|文件|图片|image|upload|attachment|file/i;

  const formItemOf = (el) => el.closest(FORM_ITEM_SEL);

  const nameOf = (el) => {
    const named = el.getAttribute("name") || el.getAttribute("data-field") || el.getAttribute("data-name") || el.getAttribute("data-prop");
    if (named) return named;
    const item = formItemOf(el);
    const prop = item?.getAttribute("prop") || item?.getAttribute("data-prop") || item?.getAttribute("name");
    if (prop) return prop;
    const id = el.getAttribute("id");
    return id && !generatedName(id) ? id : undefined;
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
    return "";
  };

  const selectorOf = (el) => {
    const placeholder = el.getAttribute("placeholder");
    if (placeholder && !EMPTY_VALUE.test(placeholder)) return "placeholder=" + placeholder;
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

  const displayValue = (el) => {
    const widget = el.closest(".el-select, .el-cascader, .el-date-editor, .ant-select, .ant-picker, .arco-select, .arco-picker, .n-select");
    if (widget) {
      const nodes = [...widget.querySelectorAll(".el-select__selected-item, .el-select__placeholder, .ant-select-selection-item, .arco-select-view-value, .n-base-select-option--selected, .el-range-input, input, textarea")];
      const texts = nodes
        .filter((node) => !node.classList.contains("el-select__input-wrapper")
          && !node.closest(".el-select__input-wrapper, .el-select__suffix, .el-select__caret"))
        .map((node) => clean(node instanceof HTMLInputElement || node instanceof HTMLTextAreaElement ? node.value : node.textContent))
        .filter((text) => text && !EMPTY_VALUE.test(text));
      if (texts.length) return texts[0];
    }
    if (el instanceof HTMLInputElement) {
      if (el.type === "checkbox" || el.type === "radio") return el.checked ? "true" : "";
      return String(el.value || "");
    }
    if (el instanceof HTMLSelectElement) {
      const selected = [...el.selectedOptions].map((item) => clean(item.textContent || item.value)).filter(Boolean);
      return selected.join(",");
    }
    if (el instanceof HTMLTextAreaElement) return el.value;
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
    if (el.closest(".is-disabled, .el-input.is-disabled, .el-select.is-disabled, .el-date-editor.is-disabled, .ant-select-disabled, .ant-input-disabled, .arco-select-disabled")) return true;
    if (el.hasAttribute("readonly")) {
      if (el.getAttribute("role") === "combobox") return false;
      if (el.closest(".el-select, .el-cascader, .el-date-editor, .el-date-picker, .ant-select, .ant-picker, .arco-select, .arco-picker")) return false;
    }
    return false;
  };

  const widgetKind = (item, el, label) => {
    const type = (el.getAttribute("type") || el.tagName).toLowerCase();
    if (isUploadWidget(el, label) || type === "file") return "upload";
    const host = item || el.parentElement;
    const blob = [type, host?.className || "", el.getAttribute("placeholder") || "", label, el.closest(".el-date-editor, .el-select")?.className || ""].join(" ");
    if (type === "checkbox" || el.getAttribute("role") === "checkbox" || el.getAttribute("role") === "switch") return "checkbox";
    if (type === "radio" || el.getAttribute("role") === "radio") return "radio";
    if (/date|time|picker/i.test(blob) || el.closest(".el-date-editor, .el-date-picker, .ant-picker, .arco-picker") || host?.querySelector?.(".el-date-editor, .el-date-picker, .ant-picker, .arco-picker")) return "date";
    if (el instanceof HTMLSelectElement || el.getAttribute("role") === "combobox" || el.closest(".el-select, .ant-select, .arco-select") || host?.querySelector?.(".el-select, .ant-select, .arco-select, [role=combobox], select")) return "select";
    if (isDisabledWidget(el)) return "readonly";
    if (type === "number" || el.getAttribute("inputmode") === "decimal" || el.getAttribute("inputmode") === "numeric") return "number";
    if (el.tagName === "TEXTAREA") return "textarea";
    return "text";
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

  const collectVisibleOptions = (root) => {
    const dropdowns = [...document.querySelectorAll(".el-select-dropdown, .el-cascader__dropdown, .el-autocomplete-suggestion, .ant-select-dropdown, .arco-select-dropdown, [role='listbox']")].filter(isVisible);
    const searchRoot = dropdowns.at(-1) || root || document;
    return [...searchRoot.querySelectorAll(OPTION_SEL)]
      .filter((el) => isVisible(el)
        && !el.closest("nav, aside, .el-menu, .ant-menu, .el-pagination, .ant-pagination")
        && !el.classList.contains("is-disabled")
        && !el.getAttribute("disabled")
        && !el.classList.contains("ant-select-item-option-disabled"))
      .slice(0, 200)
      .map((el) => clean(el.textContent))
      .filter(Boolean);
  };

  const activeScope = () => {
    const dialogs = [...document.querySelectorAll(DIALOG_SEL)].filter((el) => isVisible(el) && !el.closest(PICKER_SEL));
    return dialogs.at(-1) || document.body;
  };

  const scopeName = (root) => root && root !== document.body && root.closest(DIALOG_SEL) ? "dialog" : "page";

  const fieldFromControl = (el, item) => {
    if (!(el instanceof HTMLElement) || !isVisible(el)) return null;
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (/hidden|submit|button|reset|image/.test(type)) return null;
    const label = labelOf(el) || clean(item?.querySelector?.(FORM_LABEL_SEL)?.textContent || "");
    if (!label) return null;
    const kind = widgetKind(item, el, label);
    const value = displayValue(el);
    const filled = !isEmptyValue(value);
    return {
      label,
      name: nameOf(el) || item?.getAttribute?.("prop") || item?.getAttribute?.("data-prop") || undefined,
      selector: selectorOf(el),
      kind,
      filled,
      skip: kind === "upload",
      disabled: kind === "readonly" || isDisabledWidget(el),
      required: Boolean(item?.classList?.contains("is-required") || el.hasAttribute("required") || el.getAttribute("aria-required") === "true" || el.closest(".is-required")),
      value,
      scope: scopeName(el)
    };
  };

  const collectFormFields = (root) => {
    const fields = [];
    const seen = new Set();
    const add = (field) => {
      if (!field) return;
      const key = field.scope + "|" + field.label + "|" + (field.name || field.selector);
      if (seen.has(key)) return;
      seen.add(key);
      fields.push(field);
    };
    for (const item of root.querySelectorAll(FORM_ITEM_SEL)) {
      const el = item.querySelector("input, textarea, select, [role=combobox], [contenteditable=true]");
      add(fieldFromControl(el, item));
    }
    for (const cell of root.querySelectorAll("tbody td, .el-table__body td, .el-table__body .el-table__cell, .ant-table-tbody .ant-table-cell")) {
      const el = cell.querySelector("input, textarea, select, [role=combobox], [contenteditable=true]");
      if (!el || formItemOf(el)) continue;
      add(fieldFromControl(el, cell));
    }
    for (const el of root.querySelectorAll("input, textarea, select, [role=combobox], [contenteditable=true]")) {
      if (formItemOf(el) || el.closest("td, .el-table__cell, .ant-table-cell")) continue;
      add(fieldFromControl(el, el.closest("label") || el.parentElement));
    }
    return fields;
  };

  const collectControls = (root) => [...root.querySelectorAll(
    'a,button,input,select,textarea,[contenteditable="true"],[role="button"],[role="combobox"],[role="option"],[role="link"],[role="checkbox"],[role="switch"],[role="radio"],[role="tab"],[role="menuitem"]'
  )].filter(isVisible).slice(0, 250).map((el) => ({
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
    text: clean(el.textContent || el.value || "").slice(0, 300)
  }));

  const formSnapshot = (container) => {
    if (!(container instanceof HTMLElement)) return undefined;
    return collectFormFields(container).map((field) => ({
      name: field.name,
      label: field.label,
      type: field.kind,
      value: field.value,
      required: field.required,
      options: undefined
    }));
  };

  const buildSnapshot = () => {
    const scope = activeScope();
    const formFields = collectFormFields(scope);
    const todoFields = formFields.filter((field) => !field.skip && !field.disabled && !field.filled);
    return {
      title: document.title,
      url: location.href,
      text: clean(document.body.innerText),
      scope: scopeName(scope),
      controls: collectControls(document),
      formFields,
      todoFields,
      todoCount: todoFields.length
    };
  };
`;

export const SNAPSHOT_IN_PAGE = new Function(`${PAGE_HELPERS}\nreturn buildSnapshot();`) as () => unknown;

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
      options: optionsOf(control),
      visibleOptions: collectVisibleOptions(document),
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
