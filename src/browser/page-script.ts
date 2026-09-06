/**
 * 文件级说明：控件怎么填、是下拉还是选人，主流判断已交给 `.pi/skills/operate-form-controls`。
 * 本文件只向页面注入快照/取证原语。组件库 class 清单的旧全文见 `page-script.ts.bak`。
 */
export const PAGE_HELPERS = String.raw`
  const clean = (value) => String(value || "").replace(/\s+/g, " ").trim().slice(0, 12000);
  const generatedName = (value) => /^(el-id-\d+|el-[a-z]+-\d+|reka-v-[a-z0-9-]+|input-\d+|select-\d+|aria-id|:r[0-9a-z]+$)/i.test(String(value || ""));
  const isVisible = (el) => {
    if (!(el instanceof Element)) return false;
    if (el.hidden || el.getAttribute("aria-hidden") === "true") return false;
    if (el.closest("[hidden]")) return false;
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  };
  const shadowRootsOf = () => {
    if (window.__bssShadowScan) return window.__bssShadowRoots || [];
    const roots = [];
    try {
      const all = document.getElementsByTagName("*");
      for (let i = 0; i < all.length; i++) {
        if (all[i].shadowRoot) roots.push(all[i].shadowRoot);
      }
    } catch { /* ignore */ }
    window.__bssShadowRoots = roots;
    if (roots.length || document.readyState === "complete") window.__bssShadowScan = 1;
    return roots;
  };
  const queryDeep = (root, selector) => {
    const found = [];
    const take = (node) => {
      if (!node || !node.querySelectorAll || found.length > 800) return;
      try { found.push(...node.querySelectorAll(selector)); } catch { /* ignore */ }
    };
    take(root);
    if (root === document || root === document.body || root === document.documentElement) {
      for (const shadow of shadowRootsOf()) take(shadow);
    } else {
      try {
        for (const kid of root.querySelectorAll("*")) if (kid.shadowRoot) take(kid.shadowRoot);
      } catch { /* ignore */ }
    }
    return found.slice(0, 800);
  };
  const FORM_ITEM_SEL = '.el-form-item, .ant-form-item, .arco-form-item, .n-form-item, .van-field, [data-slot="form-item"], [class*="form-item"]:not([class*="form-item__"]):not([class*="form-item-"])';
  const FORM_LABEL_SEL = 'label, .el-form-item__label, .ant-form-item-label, .arco-form-item-label, .n-form-item-label, .van-field__label, [data-slot="form-label"]';
  const DIALOG_SEL = '[role="dialog"], [role="alertdialog"], .el-dialog, .el-drawer, .el-overlay-dialog, .ant-modal, .ant-drawer, .arco-modal, .arco-drawer';
  const PICKER_SEL = '.el-picker-panel, .el-select-dropdown, .el-cascader__dropdown, .el-picker__popper, .el-popper.el-date-picker, .el-date-range-picker, .el-time-panel, .ant-picker-dropdown, .ant-select-dropdown, .ant-tree-select-dropdown, .arco-picker-container, .arco-select-dropdown, .arco-select-popup, .arco-tree-select-popup, .arco-cascader-popup, .arco-trigger-popup, [class*="picker-panel"], [class*="picker-dropdown"], [class*="select-popup"], [class*="trigger-popup"], [data-reka-popper-content-wrapper], [data-radix-popper-content-wrapper], [data-state="open"][data-slot="popover-content"], [data-state="open"][data-slot="select-content"], [data-state="open"][data-slot="combobox-content"]';
  const CHOOSER_TITLE = /选择(用户|人员|员工|审批|部门|项目|角色|岗位|成员|产品|供应商|商品|客户|物料|仓库|账户|用品|物品|办公)|选人|选部门|(用户|人员|产品|供应商|用品|物品)选择|^(请选择|选择|挑选|选取)/;
  const OPTION_SEL = '[role="option"], [role="menuitem"], [role="treeitem"], .el-select-dropdown__item, .el-cascader-node, .el-tree-node__content, .el-autocomplete-suggestion__list li, .ant-select-item-option, .ant-select-tree-title, .ant-tree-title, .ant-cascader-menu-item, .arco-select-option, .arco-tree-node-title, .arco-cascader-option, .n-base-select-option';
  const EMPTY_VALUE = /^(请选择|请输入|请填写|请挑选|select|please select|please enter|please choose|choose|yyyy-mm-dd|年\/月\/日)/i;
  const PROMPT_ONLY = /^(请选择|请输入|请填写|请挑选|select|please select|please enter|please choose|choose)[.…]?$/i;
  const DATE_PLACEHOLDER = /yyyy-mm-dd|年\/月\/日/i;
  const UPLOAD_LABEL = /上传|附件|图片|image|upload|attachment/i;
  const PLUS_ONLY = /^(＋|\+|添加|选择)$/;
  const ACTION_ONLY = /^(新增|新增一行|添加一行|加一行|添加明细|新增明细|创建|导入|导出|删除|搜索|查询|重置|提交|确定|取消|关闭|保存|返回)$/;
  // A generic toolbar class is frequently used by business tables for their
  // create/import/export buttons. Treating every toolbar as application chrome
  // hides real operations from the capability inventory. Only exclude structural
  // navigation, pagination, and toolbars whose semantics are unambiguously global
  // or editor-specific.
  const CHROME_SEL = "nav, header, .el-menu, .ant-menu, .el-pagination, .ant-pagination, .arco-pagination, [class*='pagination'], [class*='header-bar'], [role='toolbar'][aria-label*='导航'], [role='toolbar'][aria-label*='navigation' i], [data-w-e-toolbar], [data-menu-key], [class*='w-e-bar'], [class*='editor-menu']";
  const SLOT_HOST_SEL = "[class*='process-node'], [class*='workflow-node'], [class*='user-select'], [class*='assignee'], [class*='approver'], [class*='approval-node'], [class*='flow-node'], [class*='activity'], .el-timeline-item, [class*='timeline-item'], [id*='activity-task']";
  const WIDE_SEL = DIALOG_SEL + ", form, [role='form'], body, main, header, nav, aside, footer, .el-overlay, .ant-modal-wrap, [class*='overlay']";
  const FIELD_GROUP_SEL = FORM_ITEM_SEL + ", label, dt, dd, li, [class*='form-field'], [class*='field-item'], [class*='form-row'], [class*='field-row']";
  const FIELD_CONTROL_SEL = "input, textarea, select, [role='combobox'], [role='textbox'], [contenteditable='true'], button, [role='button'], [aria-haspopup], .arco-select-view, [class*='arco-select-view']:not(input):not(textarea):not([class*='input']):not([class*='value']):not([class*='suffix']):not([class*='arrow']), .el-select__wrapper, .ant-select-selector";

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
    const cell = el.closest("td, th, .el-table__cell, .ant-table-cell, .vxe-body--column, .vxe-header--column");
    const row = cell?.closest("tr, .el-table__row, .ant-table-row, .vxe-body--row, .vxe-header--row");
    if (!cell || !row) return "";
    const cells = [...row.children].filter((node) => node.matches("td, th, .el-table__cell, .ant-table-cell, .vxe-body--column, .vxe-header--column"));
    const index = cells.indexOf(cell);
    const host = el.closest(".el-table, .ant-table, .arco-table, .vxe-table") || el.closest("table");
    const headerRow = host?.querySelector(".el-table__header tr, .el-table__header-wrapper tr, .ant-table-thead tr, .arco-table-tr, thead tr, .vxe-header--row");
    const headers = headerRow
      ? [...headerRow.children].filter((node) => node.matches("th, td, .el-table__cell, .ant-table-cell, .arco-table-th, .vxe-header--column"))
      : [...(host?.querySelectorAll("th, .el-table__header .el-table__cell, .ant-table-thead th, .arco-table-th, .vxe-header--column") || [])];
    return index >= 0 ? clean(headers[index]?.textContent || "") : "";
  };

  const stripControls = (node) => {
    const clone = node.cloneNode(true);
    clone.querySelectorAll(FIELD_CONTROL_SEL + ", .el-select, .ant-select, .arco-select, .arco-input-wrapper, .arco-textarea-wrapper, .arco-select-view, svg, i, .arco-form-item-tooltip").forEach((item) => item.remove());
    return clean(clone.textContent).replace(/[:：*]\s*$/g, "");
  };

  const formItemLabel = (item) => {
    if (!(item instanceof Element)) return "";
    const official = item.querySelector(".el-form-item__label, .ant-form-item-label > label, .ant-form-item-label, .arco-form-item-label, .n-form-item-label, .van-field__label");
    if (official) return stripControls(official);
    const any = item.querySelector(FORM_LABEL_SEL);
    return any ? stripControls(any) : "";
  };

  const labelOf = (el) => {
    const item = formItemOf(el);
    const official = formItemLabel(item);
    if (official) return official;
    if (el.labels?.length) {
      const text = clean([...el.labels].map((lab) => stripControls(lab)).join(" "));
      const value = displayValue(el);
      if (text && text !== value && !/^(字段|输入|文本|内容)$/.test(text)) return text;
    }
    const aria = el.getAttribute("aria-label");
    if (aria && !EMPTY_VALUE.test(aria) && !generatedName(aria) && aria !== displayValue(el)) return clean(aria);
    const labelled = el.getAttribute("aria-labelledby");
    if (labelled) {
      const named = clean(labelled.split(/\s+/).map((id) => document.getElementById(id)?.textContent || "").join(" "));
      if (named && named !== displayValue(el)) return named;
    }
    const parentLabel = el.closest("label");
    if (parentLabel) {
      const text = stripControls(parentLabel);
      if (text && text !== displayValue(el)) return text;
    }
    const header = tableHeaderOf(el);
    if (header) return header;
    const placeholder = el.getAttribute("placeholder") || "";
    if (placeholder && !EMPTY_VALUE.test(placeholder) && !PROMPT_ONLY.test(placeholder)) return clean(placeholder);
    const nearby = nearbyLabel(el);
    if (nearby && nearby !== displayValue(el) && !isForeignDisplayValue(nearby, el)) return nearby;
    return "";
  };

  const isForeignDisplayValue = (text, el) => {
    if (!text || text.length < 2) return false;
    return queryDeep(document.body, FIELD_CONTROL_SEL).some((other) => {
      if (other === el || other.contains(el) || el.contains(other)) return false;
      return displayValue(other) === text;
    });
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
    if (el.matches("a[href]") || actionRole === "link" || actionRole === "menuitem") {
      const name = clean(el.getAttribute("aria-label") || el.textContent || "");
      const role = actionRole || "link";
      if (name && name.length <= 80 && !generatedName(name) && !PLUS_ONLY.test(name)) {
        return "role=" + role + "[name=\"" + name + "\"]";
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
      if (/input-wrapper|suffix|caret|selection-item|selected-item|placeholder|(?:^|\s)el-select__selection(?:\s|$)/i.test(cls)) continue;
      if (/(?:selection-search|search-input|select-view-input|select__input)/i.test(cls)) continue;
      if (role === "combobox" && !node.matches("input, textarea")) return node;
      if (/^(select-trigger|combobox-trigger)$/.test(node.getAttribute("data-slot") || "")) return node;
      if (/(?:^|\s)(el-select__wrapper|ant-select-selector|arco-select-view)/.test(cls)) return node;
      if (/(?:^|\s)(el-select|ant-select|arco-select|n-select|el-cascader|el-date-editor|ant-picker|arco-picker)(?:\s|$)/.test(cls)) return node;
      if (/(?:^|\s|_|-)(select|picker|cascader|date-editor)(?:\s|$|_)/i.test(cls) && !/dropdown|panel|item|option/i.test(cls)) return node;
    }
    return null;
  };

  const isValueSlot = (node) => node.matches("[class*='selection-item'], [class*='selected-item'], [class*='selected'], [class*='placeholder'], [class*='tag'], [data-slot$='-value'], [data-slot='select-value'], [data-slot='combobox-value']");
  const chooserSurface = (host) => {
    if (!(host instanceof Element)) return false;
    const slot = host.getAttribute("data-slot") || "";
    const cls = String(host.className || "");
    if (host.getAttribute("role") === "combobox") return true;
    if (/^(select-trigger|combobox-trigger)$/.test(slot)) return true;
    return /(?:^|\s)(el-select__wrapper|ant-select-selector|arco-select-view|el-select|ant-select|ant-tree-select|arco-select|n-select)(?:\s|$)/.test(cls);
  };
  const slotText = (node) => {
    const shown = clean(node.textContent) || clean(node.getAttribute("title"));
    return shown && !EMPTY_VALUE.test(shown) && !PLUS_ONLY.test(shown) ? shown : "";
  };
  const skipDisplayNode = (node) => {
    if (!(node instanceof Element)) return true;
    if (isValueSlot(node)) return false;
    return Boolean(node.closest("[class*='input-wrapper'], [class*='selection-search'], [class*='search-input'], [class*='select-view-input'], [class*='suffix'], [class*='caret'], [class*='arrow'], .anticon, .el-icon, .arco-icon"));
  };
  const visibleChooserText = (host) => {
    const walk = (node) => {
      if (!(node instanceof Element)) return [];
      const style = getComputedStyle(node);
      if (style.display === "none" || style.visibility === "hidden") return [];
      if (node.matches("input, textarea, svg, i, [class*='suffix'], [class*='arrow'], [class*='caret'], .anticon, .el-icon, .arco-icon")) return [];
      if (node.matches("[class*='placeholder'], [class*='selection-search'], [class*='search-input'], [class*='select-view-input']") && !isValueSlot(node)) return [];
      const kids = [...node.children];
      if (kids.length) {
        const nested = kids.flatMap(walk);
        if (nested.length) return nested;
      }
      const shown = slotText(node);
      return shown ? [shown] : [];
    };
    return walk(host).find(Boolean) || "";
  };
  const hostDisplay = (host) => {
    if (!(host instanceof Element)) return "";
    const slotted = [...host.querySelectorAll("[class*='selected'], [class*='selection-item'], [class*='placeholder'], [class*='tag'], [class*='value'], [data-slot$='-value'], [data-slot='select-value'], [data-slot='combobox-value']")]
      .filter((node) => !skipDisplayNode(node))
      .map(slotText)
      .find(Boolean);
    if (slotted) return slotted;
    if (chooserSurface(host)) return visibleChooserText(host);
    return "";
  };

  const isChooserFilter = (el) => {
    if (!(el instanceof HTMLInputElement)) return false;
    if (/select__input|selection-search|search-input|filter|select-view-input/i.test(String(el.className || ""))) return true;
    if (el.getAttribute("role") === "combobox") return false;
    return Boolean(el.getAttribute("aria-autocomplete") && chooserHostOf(el));
  };

  const displayValue = (el) => {
    if (el instanceof HTMLInputElement && (el.type === "checkbox" || el.type === "radio")) return el.checked ? "true" : "";
    if (el instanceof HTMLSelectElement) {
      return [...el.selectedOptions].map((item) => clean(item.textContent || item.value)).filter(Boolean).join(",");
    }
    if ((el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) && !isChooserFilter(el)) {
      const own = clean(el.value);
      if (own && !EMPTY_VALUE.test(own)) return own;
    }
    const hosts = [chooserHostOf(el), el.parentElement, el.closest("[data-slot='form-item'], [class*='form-item']"), el];
    for (const host of hosts) {
      const shown = hostDisplay(host);
      if (shown) return shown;
    }
    if (isChooserFilter(el)) return "";
    if (el.getAttribute("role") === "combobox") {
      const own = clean(el instanceof HTMLInputElement ? el.value : el.textContent);
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
    const selectPlaceholder = /^(请选择|请挑选|please select|please choose)/i.test(clean(placeholder));
    const blob = [type, placeholder, label, popup, role].join(" ");
    if (isUploadWidget(el, label) || type === "file") return "upload";
    if (type === "checkbox" || role === "checkbox" || role === "switch") return "checkbox";
    if (type === "radio" || role === "radio") return "radio";
    if (/^(date|datetime-local|time|month|week)$/.test(type) || /日期|时间|(^|[^a-z])date|time/i.test(blob)) return "date";
    if (el.closest(".el-date-editor, .el-date-picker, .ant-picker, .arco-picker")) return "date";
    if (/dialog|tree/i.test(popup)) return "picker";
    if (el.closest(".el-select, .ant-select, .arco-select, .n-select, .el-cascader") || /(?:^|\s)(el-select|ant-select|arco-select|n-select)/.test(String(el.className || ""))) return "select";
    if (el instanceof HTMLSelectElement || role === "combobox" || /listbox|menu/i.test(popup) || el.closest("[role='combobox']")) return "select";
    if (selectPlaceholder && !isChooserFilter(el)) return "picker";
    if (el.hasAttribute("readonly") && !isDisabledWidget(el) && (EMPTY_VALUE.test(placeholder) || /请选择|please select/i.test(blob))) return "picker";
    if (isDisabledWidget(el)) return "readonly";
    if (
      type === "number"
      || role === "spinbutton"
      || el.getAttribute("inputmode") === "decimal"
      || el.getAttribute("inputmode") === "numeric"
      || el.closest(".el-input-number, .ant-input-number, .arco-input-number, .n-input-number")
    ) return "number";
    if (el.tagName === "TEXTAREA" || role === "textbox" || el.isContentEditable) return "textarea";
    return "text";
  };

  const evidenceType = (item, el, kind) => {
    if (kind !== "date") return kind;
    const input = el instanceof HTMLInputElement ? el : el.querySelector?.("input");
    const dateHost = el.closest?.(".el-date-editor, .el-date-picker, .ant-picker, .arco-picker, [class*='date-editor'], [class*='picker-range']")
      || item?.querySelector?.(".el-date-editor, .el-date-picker, .ant-picker, .arco-picker, [class*='date-editor'], [class*='picker-range']");
    const type = clean(input?.getAttribute?.("type") || "").toLowerCase();
    const blob = clean([dateHost?.className, item?.className, el.className, input?.placeholder].join(" ")).toLowerCase();
    return /^(datetime-local|time)$/.test(type) || /datetimerange|--datetime|--time(?:\s|$)|hh?:mm|时分|时间选择/.test(blob)
      ? "datetime"
      : "date";
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

  const isChoiceControl = (el) => {
    if (!(el instanceof Element)) return false;
    if (el instanceof HTMLSelectElement) return true;
    const role = el.getAttribute("role") || "";
    if (/combobox|listbox/.test(role)) return true;
    if (/listbox|menu|dialog/i.test(el.getAttribute("aria-haspopup") || "")) return true;
    if (/^(请选择|请挑选|please select|please choose)/i.test(clean(el.getAttribute("placeholder") || "")) && !isChooserFilter(el)) return true;
    return Boolean(el.closest(".el-select, .ant-select, .arco-select, .n-select, .el-cascader"));
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
    const dropdowns = [...document.querySelectorAll(".el-select-dropdown, .el-cascader__dropdown, .el-autocomplete-suggestion, .ant-select-dropdown, .ant-tree-select-dropdown, .arco-select-dropdown, .arco-select-popup, .arco-tree-select-popup, .arco-cascader-popup, .arco-trigger-popup, [class*='select-popup'], [class*='tree-select-popup'], [class*='cascader-popup'], [class*='trigger-popup'], [role='listbox'], [data-reka-popper-content-wrapper], [data-radix-popper-content-wrapper], [data-state='open'][data-slot='popover-content'], [data-state='open'][data-slot='select-content'], [data-state='open'][data-slot='combobox-content']")].filter(isVisible);
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

  const isPickerHost = (el) => Boolean(el.matches?.(PICKER_SEL) || el.closest(PICKER_SEL));

  const isChooserDialog = (el) => {
    if (!(el instanceof Element) || isPickerHost(el)) return false;
    const title = clean((el.querySelector(".el-dialog__title, .el-dialog__header, .ant-modal-title, .arco-modal-title, .el-drawer__title, [class*='dialog__title'], [class*='dialog-header'], [class*='modal-title']") || {}).textContent || "");
    const formItems = el.querySelectorAll(".el-form-item, .ant-form-item, .arco-form-item").length;
    const rows = el.querySelectorAll("tbody tr, .el-table__body .el-table__row, .ant-table-tbody .ant-table-row, .vxe-body--row, .el-tree-node").length;
    const confirm = [...el.querySelectorAll("button, [role='button']")].some((btn) => /^(确\s*定|确\s*认|选\s*择|ok|confirm)$/i.test(String(btn.textContent || "").replace(/\s+/g, "")));
    if (CHOOSER_TITLE.test(title)) return true;
    const tree = el.querySelectorAll(".el-tree, [role='tree']").length;
    if (tree >= 1 && confirm && formItems <= 8) return true;
    return Boolean(rows >= 1 && formItems <= 3 && confirm);
  };

  const scopeHasFields = (el) => Boolean(
    el.querySelector(".el-form-item, .ant-form-item, .arco-form-item, input, textarea, select, [role='combobox'], .el-timeline-item, [class*='timeline-item'], [id*='activity-task']")
  );

  const activeScope = () => {
    const dialogs = [...document.querySelectorAll(DIALOG_SEL)].filter((el) => isVisible(el) && !isPickerHost(el) && !isChooserDialog(el));
    for (let i = dialogs.length - 1; i >= 0; i -= 1) {
      if (scopeHasFields(dialogs[i])) return dialogs[i];
    }
    return document.body;
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
    const named = host.querySelector("h1, h2, h3, h4, [class*='title'], [class*='name'], [class*='head'], [class*='label']");
    if (named) return named;
    if (host.matches(".el-timeline-item, [class*='timeline-item'], [id*='activity-task']") || host.querySelector(".el-timeline-item, [id*='activity-task']")) {
      return host.querySelector(".font-bold, [class*='font-bold']");
    }
    return null;
  };

  const headingTextOf = (host) => clean(headingOf(host)?.textContent || "").replace(/[:：*]\s*$/g, "");

  const assigneeName = (host) => {
    if (!(host instanceof Element)) return "";
    const title = headingTextOf(host);
    const wrap = host.querySelector("[class*='flex-wrap'], [class*='items-center']") || host;
    const clone = wrap.cloneNode(true);
    clone.querySelectorAll("button, [class*='avatar'], [class*='plus'], [class*='icon'], img, svg, .font-bold, [class*='font-bold'], h1, h2, h3, h4, [class*='title'], [class*='name'], [class*='head'], [class*='label']").forEach((node) => node.remove());
    const text = clean(clone.textContent);
    if (text && text !== title && !PLUS_ONLY.test(text) && !isEmptyValue(text) && text.length <= 40) return text;
    return "";
  };

  const personChip = (host) => {
    if (!(host instanceof Element)) return "";
    const title = headingTextOf(host);
    const heading = headingOf(host);
    const named = [...host.querySelectorAll("[class*='tag'], [class*='user'], [class*='nickname'], [class*='selected'], span, strong, div")]
      .filter((node) => node !== heading && !(heading && heading.contains(node)) && !node.matches("button, [role='button'], [class*='avatar'], [class*='plus'], [class*='icon']"))
      .map((node) => clean(node.textContent))
      .find((text) => text && text !== title && !text.includes(title) && !PLUS_ONLY.test(text) && !isEmptyValue(text) && text.length <= 40);
    return named || assigneeName(host);
  };

  const isEmptyWell = (el) => {
    if (!(el instanceof HTMLElement) || !isVisible(el) || isWide(el)) return false;
    if (el.matches("input, textarea, select, a, [role='tab']")) return false;
    if (el.closest(".el-timeline-item__dot, [class*='timeline-item__dot']")) return false;
    const box = el.getBoundingClientRect();
    if (box.width < 8 || box.height < 8 || box.width > 96 || box.height > 96) return false;
    const text = clean(el.textContent || el.getAttribute("aria-label") || "");
    if (ACTION_ONLY.test(text.replace(/\s+/g, ""))) return false;
    if (text && !PLUS_ONLY.test(text.replace(/\s+/g, "")) && !isEmptyValue(text) && text.length > 2) return false;
    const host = slotHost(el);
    if (host && personChip(host)) return false;
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
    if (el.matches("input, textarea, select, [role=combobox], [role=textbox], [contenteditable=true]")) return false;
    if (chooserHostOf(el) || el.matches(".el-select__wrapper, .ant-select-selector, .arco-select-view, [class*='arco-select-view']") || el.closest(".el-select, .ant-select, .arco-select, .n-select, .el-cascader")) return false;
    if (el.closest(PICKER_SEL + ", " + CHROME_SEL)) return false;
    if (el.closest(".el-timeline-item__dot, [class*='timeline-item__dot']")) return false;
    if (el.closest("tbody, .el-table__body, .ant-table-tbody, thead, .el-table__header")) return false;
    if (isUploadWidget(el, labelOf(el))) return false;
    const host = slotHost(el);
    if (host && personChip(host)) return false;
    const text = clean(el.textContent || el.getAttribute("aria-label") || "");
    if (ACTION_ONLY.test(text.replace(/\s+/g, ""))) return false;
    if (/dialog/i.test(el.getAttribute("aria-haspopup") || "") && (isPlusControl(el) || isEmptyValue(text) || PLUS_ONLY.test(text.replace(/\s+/g, "")))) return true;
    if (isPlusControl(el) && !el.closest("thead, .el-table__header, [class*='toolbar']")) return true;
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
    if (el.matches("button, [role='button']") && !isPickerSlot(el) && !/dialog|listbox|menu|tree/i.test(el.getAttribute("aria-haspopup") || "") && el.getAttribute("role") !== "combobox") return false;
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
    const kitRootOf = (el) => el.closest?.(".el-select, .ant-select, .arco-select, .n-select, .el-cascader, .el-date-editor, .ant-picker, .arco-picker") || chooserHostOf(el);
    const rank = (el) => {
      if (isChooserFilter(el)) return 2;
      if (el.matches?.("input, textarea, select, [role='combobox']")) return 0;
      return 1;
    };
    const ranked = [...els].sort((left, right) => rank(left) - rank(right));
    for (const el of ranked) {
      if (!el || seen.has(el)) continue;
      const host = chooserHostOf(el);
      const kit = kitRootOf(el);
      if ((host && seen.has(host)) || (kit && seen.has(kit))) continue;
      seen.add(el);
      if (host) seen.add(host);
      if (kit) seen.add(kit);
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
    const officialHits = [];
    for (const item of queryDeep(root, FORM_ITEM_SEL)) {
      if (!isVisible(item)) continue;
      const official = formItemLabel(item);
      if (official !== want && official !== base) continue;
      officialHits.push(...itemControls(item).filter((el) => !isChooserFilter(el)));
    }
    const officialPicked = pickFrom(uniqueControls(officialHits));
    if (officialPicked) return officialPicked;
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
    // An empty icon/dropdown is not evidence of a business field. Global
    // navigation avatars and rich-text toolbar buttons are common examples.
    // Only emit picker fields when the page supplies a semantic label.
    return labelOf(el) || nearbyLabel(el) || "";
  };

  const fieldFromPicker = (el) => {
    const label = slotLabel(el);
    if (!label) return null;
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

  const groupLabels = (controls, fallbacks) => {
    if (controls.length <= 1) return fallbacks.map((label, index) => label || identityPlaceholder(controls[index]) || "");
    const placeholders = controls.map((el) => identityPlaceholder(el));
    const usable = placeholders.filter(Boolean);
    if (usable.length === controls.length && new Set(usable).size === controls.length) return placeholders;
    const bases = controls.map((el, index) => fallbacks[index] || placeholders[index] || "");
    const counts = new Map();
    for (const base of bases) counts.set(base, (counts.get(base) || 0) + 1);
    return bases.map((base, index) => {
      if (placeholders[index] && placeholders[index] !== base) return placeholders[index];
      if (counts.get(base) > 1 && base) return clean(base) + "-" + (index + 1);
      return base;
    });
  };

  const itemControls = (item) => {
    const range = rangeInputsOf(item);
    if (range.length >= 2) return range;
    const promoted = [...item.querySelectorAll(FIELD_CONTROL_SEL)].flatMap((el) => {
      if (isChooserFilter(el)) {
        const host = chooserHostOf(el);
        return host ? [host] : [];
      }
      return [el];
    });
    const fields = uniqueControls(promoted.filter((el) => !el.closest(PICKER_SEL) && isFieldControl(el) && !isChooserFilter(el)));
    const slots = fields.length ? [] : [...item.querySelectorAll("button, [role=button], [class*='add-user'], [class*='user-select'], [class*='plus'], [class*='avatar']")].filter(isPickerSlot);
    return [...fields, ...slots];
  };

  const fieldFromControl = (el, item) => {
    if (!(el instanceof HTMLElement)) return null;
    if (isChooserFilter(el)) {
      const host = chooserHostOf(el);
      return host && host !== el ? fieldFromControl(host, item) : null;
    }
    if (!isVisible(el) && !(chooserHostOf(el) && isVisible(chooserHostOf(el)))) return null;
    const chooser = el.closest(DIALOG_SEL);
    if (chooser && isChooserDialog(chooser)) return null;
    if (el.closest(PICKER_SEL + ", " + CHROME_SEL + ", thead, .el-table__header, .el-table__header-wrapper, .ant-table-thead, .vxe-header--row")) return null;
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (/hidden|submit|reset|image/.test(type)) return null;
    if (type === "button" && el.getAttribute("role") !== "combobox" && !/dialog|listbox|menu|tree/i.test(el.getAttribute("aria-haspopup") || "")) return null;
    const official = formItemLabel(item) || labelOf(el) || nearbyLabel(el) || tableHeaderOf(el) || nameOf(el) || "";
    const label = distinctLabel(el, official, 0, 1);
    if (!label) return null;
    const value = displayValue(el);
    if (/共\s*\d+\s*条(?:记录)?|每\s*页|条\s*\/\s*页/i.test(label + " " + value)) return null;
    const kind = widgetKind(item, el, label);
    const required = Boolean(item?.classList?.contains("is-required") || el.hasAttribute("required") || el.getAttribute("aria-required") === "true" || el.closest(".is-required"));
    const numericZero = kind === "number" && /^(0+|0*\.0+)$/.test(clean(value));
    const syntheticChoice = (kind === "select" || kind === "picker") && /^样例(?:-|$)/.test(clean(value));
    const filled = !isEmptyValue(value) && !syntheticChoice && !(required && numericZero);
    const errorNode = item?.querySelector?.(".el-form-item__error, .ant-form-item-explain-error, .arco-form-item-message, [data-slot='form-message']");
    const error = errorNode && isVisible(errorNode) ? clean(errorNode.textContent || "") : "";
    const invalid = Boolean(error);
    return {
      label,
      name: nameOf(el),
      selector: selectorOf(el),
      kind,
      type: evidenceType(item, el, kind),
      filled,
      skip: kind === "upload",
      disabled: kind === "readonly" || isDisabledWidget(el),
      required,
      invalid,
      error: error || undefined,
      value,
      options: optionsOf(el),
      scope: scopeName(el)
    };
  };

  const placeholderUses = (scope) => {
    const counts = new Map();
    for (const el of queryDeep(scope, "input, textarea")) {
      const ph = identityPlaceholder(el);
      if (!ph) continue;
      counts.set(ph, (counts.get(ph) || 0) + 1);
    }
    return counts;
  };

  const fieldSelector = (el, label, identity, phCounts) => {
    const header = tableHeaderOf(el);
    if (header && el.closest("td, th, .el-table__cell, .ant-table-cell, .vxe-body--column, .vxe-cell")) return "column=" + header;
    if (identity && (phCounts.get(identity) || 0) === 1) return "placeholder=" + identity;
    if (label) return "label=" + label;
    return selectorOf(el);
  };

  const collectFormFields = (root) => {
    const fields = [];
    const seen = new Set();
    const seenEls = new Set();
    const phCounts = placeholderUses(root);
    const add = (field) => {
      if (!field) return;
      const radioKey = field.kind === "radio" && field.name ? field.scope + "|radio|" + field.name : "";
      const pickerKey = field.kind === "picker" ? field.scope + "|picker|" + field.label : "";
      const key = radioKey || pickerKey || (field.scope + "|" + field.label + "|" + field.kind + "|" + (field.name || field.selector) + "|" + String(field.rangeIndex ?? field.groupIndex ?? ""));
      if (seen.has(key)) return;
      seen.add(key);
      fields.push(field);
    };
    for (const item of queryDeep(root, FORM_ITEM_SEL)) {
      if (item.closest(PICKER_SEL + ", .el-pagination, .ant-pagination, .arco-pagination, [class*='pagination']")) continue;
      const range = rangeInputsOf(item);
      const controls = itemControls(item);
      const prop = range.length >= 2 ? nameOf(range[0]) : undefined;
      const drafted = controls.map((el) => isPickerSlot(el) ? null : fieldFromControl(el, item));
      const fieldEntries = controls.map((el, index) => ({ el, field: drafted[index] })).filter((entry) => entry.field);
      const semanticLabels = groupLabels(fieldEntries.map((entry) => entry.el), fieldEntries.map((entry) => entry.field.label || ""));
      const labelByControl = new Map(fieldEntries.map((entry, index) => [entry.el, semanticLabels[index]]));
      controls.forEach((el, index) => {
        // Component libraries often nest a helper form-item inside the owning
        // form-item (date ranges are a common example). The same physical
        // control must remain one field even when several wrappers match.
        if (seenEls.has(el)) return;
        if (isPickerSlot(el)) {
          seenEls.add(el);
          add(fieldFromPicker(el));
          return;
        }
        const field = drafted[index];
        if (!field) return;
        seenEls.add(el);
        const label = labelByControl.get(el) || field.label;
        const identity = identityPlaceholder(el);
        add({
          ...field,
          name: prop ? prop + "[" + index + "]" : field.name,
          label,
          selector: fieldSelector(el, label, identity, phCounts),
          rangeIndex: range.length >= 2 ? index : field.rangeIndex,
          groupIndex: controls.length > 1 ? index : field.rangeIndex
        });
      });
    }
    for (const cell of queryDeep(root, "tbody td, .el-table__body td, .el-table__body .el-table__cell, .ant-table-tbody .ant-table-cell, .vxe-body--column, .vxe-body--row .vxe-cell")) {
      const el = cell.querySelector("input, textarea, select, [role=combobox], [contenteditable=true]");
      if (!el || formItemOf(el) || seenEls.has(el)) continue;
      seenEls.add(el);
      const field = fieldFromControl(el, cell);
      if (!field) continue;
      add({ ...field, selector: fieldSelector(el, field.label, identityPlaceholder(el), phCounts) });
    }
    for (const el of queryDeep(root, "input, textarea, select, [role=combobox], [contenteditable=true], [role=textbox]")) {
      if (formItemOf(el) || isChooserFilter(el) || el.closest("td, th, thead, .el-table__header, .el-table__header-wrapper, .el-table__cell, .ant-table-cell, .ant-table-thead, .vxe-body--column, .vxe-header--column") || seenEls.has(el)) continue;
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
      if (!isVisible(host) || !compactStep(host) || formItemOf(host) || host.closest(PICKER_SEL + ", " + CHROME_SEL + ", tbody, .el-table__body, .ant-table-tbody")) continue;
      const well = emptyWellOf(host);
      if (well) add(fieldFromPicker(well));
      const chip = personChip(host);
      const title = headingTextOf(host);
      const assigneeHost = host.matches(".el-timeline-item, [class*='timeline-item'], [id*='activity-task'], [class*='process-node'], [class*='workflow-node'], [class*='approval-node'], [class*='user-select']");
      if (assigneeHost && chip && title && title.length <= 16 && !/不限|天数|类型/.test(title)) {
        add({
          label: title,
          name: nameOf(host),
          selector: "label=" + title,
          kind: "picker",
          filled: true,
          skip: false,
          disabled: false,
          required: false,
          invalid: false,
          value: chip,
          scope: scopeName(host)
        });
      }
    }
    for (const el of queryDeep(root, "[class*='user-tag'], [class*='el-tag'], [class*='selected-tag']")) {
      if (!isVisible(el) || !el.closest("[class*='process'], [class*='workflow'], [class*='timeline'], [id*='activity-task'], [class*='user-select']")) continue;
      const value = clean(el.textContent);
      if (!value || PLUS_ONLY.test(value) || EMPTY_VALUE.test(value)) continue;
      add({ ...fieldFromPicker(el), filled: true, required: false, value, kind: "picker" });
    }
    return fields.filter((field, index, all) => {
      const base = String(field.label || "").replace(/-\d+$/, "");
      const semantic = (value) => clean(value).replace(/^(请选择|请输入|请填写|please\s+(?:select|choose|enter))\s*/i, "");
      if (field.kind === "picker" && all.some((other, otherIndex) =>
        otherIndex !== index && other.kind !== "picker" && semantic(other.label) === semantic(field.label)
      )) return false;
      return !all.some((other, otherIndex) =>
        otherIndex !== index
        && other.value
        && (other.value === field.label || other.value === base)
      );
    });
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
    scope: scopeName(el),
    chrome: Boolean(el.closest(CHROME_SEL))
  }));

  const collectNavigation = () => {
    const items = [];
    const seen = new Set();
    for (const el of queryDeep(document.body, "a[href], [role='menuitem']")) {
      if (el.hidden || el.closest("[hidden]")) continue;
      const menu = el.closest("nav, aside, [role='menu'], .el-menu, .ant-menu, .arco-menu, [class*='sidebar'], [class*='side-menu'], [class*='sider']");
      if (!menu && el.getAttribute("role") !== "menuitem") continue;
      const raw = String(el.getAttribute("href") || "").trim();
      if (!raw || /^(?:javascript:|mailto:|tel:|#?$)/i.test(raw)) continue;
      let url;
      try { url = new URL(raw, location.href); } catch { continue; }
      if (url.origin !== location.origin) continue;
      const label = clean(el.getAttribute("aria-label") || el.textContent || "");
      if (!label || label.length > 100 || seen.has(url.href)) continue;
      seen.add(url.href);
      const current = url.href === location.href
        || Boolean(url.hash && url.hash === location.hash);
      items.push({ label, selector: selectorOf(el), url: url.href, current });
      if (items.length >= 500) break;
    }
    return items;
  };

  const collectPageHeading = () => {
    const crumbs = queryDeep(document.body, ".el-breadcrumb__inner, .el-breadcrumb__item, .ant-breadcrumb-link, .arco-breadcrumb-item, [class*='breadcrumb'] li, [class*='breadcrumb-item']")
      .filter(isVisible)
      .map((el) => clean(el.textContent || ""))
      .filter((text) => text && text.length <= 40 && !/^[\/\>]+$/.test(text));
    if (crumbs.length) return crumbs[crumbs.length - 1];
    const header = queryDeep(document.body, "main h1, .el-page-header__content, .ant-page-header-heading-title, [class*='page-header'] h1, [class*='page-title']")
      .filter(isVisible)
      .map((el) => clean(el.textContent || ""))
      .find((text) => text && text.length >= 2 && text.length <= 40);
    if (header) return header;
    const currentNav = collectNavigation().find((item) => item.current && item.label);
    if (currentNav?.label) return currentNav.label;
    return clean(String(document.title || "").replace(/\s*[-|–—].*$/, "")) || clean(document.title);
  };

  const formSnapshot = (container) => {
    if (!(container instanceof HTMLElement)) return undefined;
    return collectFormFields(container).map((field) => ({
      name: field.name,
      label: field.label,
      type: field.kind,
      value: field.value,
      required: field.required,
      disabled: field.disabled,
      invalid: field.invalid,
      options: field.options,
      rangeIndex: field.rangeIndex
    }));
  };

  const collectErrors = () => [...new Set([...document.querySelectorAll(
    ".el-form-item__error, .ant-form-item-explain-error, .arco-form-item-message, .n-form-item-feedback, .el-message--error, .el-notification--error, .ant-message-error, .ant-notification-notice-error, [class*='form-item__error'], [class*='form-item-error'], [class*='explain-error']"
  )].filter(isVisible).map((el) => clean(el.textContent)).filter(Boolean))].slice(0, 20);

  const pageText = (scope, formFields) => {
    const labels = (formFields || []).map((field) => field.label).filter(Boolean);
    const buttons = [];
    for (const el of (scope || document.body).querySelectorAll("button, [role='button'], [type='submit']")) {
      if (!isVisible(el) || isPickerHost(el) || el.closest(PICKER_SEL)) continue;
      const text = clean(el.textContent);
      if (text && text.length <= 24 && !buttons.includes(text)) buttons.push(text);
      if (buttons.length >= 12) break;
    }
    return clean([document.title, ...labels, ...buttons].join("\n")).slice(0, 4000);
  };

  const buildSnapshot = () => {
    const scope = activeScope();
    const formFields = collectFormFields(scope);
    const todoFields = formFields.filter((field) => !field.skip && !field.disabled && !field.filled);
    return {
      title: document.title,
      pageHeading: collectPageHeading(),
      url: location.href,
      text: pageText(scope, formFields),
      scope: scopeName(scope),
      controls: collectControls(scope),
      navigationInventory: collectNavigation(),
      formFields,
      todoFields,
      todoCount: todoFields.length,
      errors: collectErrors()
    };
  };
`;

export const SNAPSHOT_IN_PAGE = new Function(`${PAGE_HELPERS}\nreturn buildSnapshot();`) as () => unknown;

export const SNAPSHOT_FIELDS_IN_PAGE = new Function(`${PAGE_HELPERS}
  const scope = activeScope();
  const formFields = collectFormFields(scope);
  return {
    scope: scopeName(scope),
    formFields,
    todoFields: formFields.filter((field) => !field.skip && !field.disabled && !field.filled),
    errors: collectErrors()
  };
`) as () => unknown;

export const MARK_LABELED_CONTROL = new Function(
  "root",
  "payload",
  `${PAGE_HELPERS}
return markLabeledControl(root, payload.name, payload.mark);`
) as (root: Element, payload: { name: string; mark: string }) => boolean;

export const UI_RECORDER_SCRIPT = `(() => {
  if (window.__BSS_RECORDER_INSTALLED__) return;
  window.__BSS_RECORDER_INSTALLED__ = true;
  window.__bssLinkedRecords = window.__bssLinkedRecords || [];
  const rememberLinkedRecords = (data) => {
    const add = (obj) => {
      if (!obj || typeof obj !== "object" || Array.isArray(obj)) return;
      const keys = Object.keys(obj);
      if (keys.length < 2 || keys.length > 80) return;
      if (!(keys.includes("id") || keys.some(key => /(Id|ID)$/.test(key)))) return;
      if (!(keys.includes("name") || keys.some(key => /(Name|Title|Label)$/.test(key)))) return;
      window.__bssLinkedRecords.push(obj);
      if (window.__bssLinkedRecords.length > 400) window.__bssLinkedRecords.splice(0, 200);
    };
    const walk = (value, depth) => {
      if (!value || typeof value !== "object" || depth > 4) return;
      if (Array.isArray(value)) {
        for (const item of value.slice(0, 80)) walk(item, depth + 1);
        return;
      }
      add(value);
      if (value.data) walk(value.data, depth + 1);
      if (value.list) walk(value.list, depth + 1);
      if (value.rows) walk(value.rows, depth + 1);
      if (value.records) walk(value.records, depth + 1);
    };
    walk(data, 0);
  };
  const rawFetch = window.fetch;
  if (typeof rawFetch === "function") {
    window.fetch = async function(...args) {
      const response = await rawFetch.apply(this, args);
      try {
        const copy = response.clone();
        const type = String(copy.headers.get("content-type") || "");
        if (type.includes("json")) rememberLinkedRecords(await copy.json());
      } catch { /* ignore */ }
      return response;
    };
  }
  const rawOpen = XMLHttpRequest.prototype.open;
  const rawSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(method, url, ...rest) {
    this.__bssUrl = url;
    return rawOpen.call(this, method, url, ...rest);
  };
  XMLHttpRequest.prototype.send = function(...args) {
    this.addEventListener("load", () => {
      try {
        const type = String(this.getResponseHeader("content-type") || "");
        if (type.includes("json") && this.responseText) rememberLinkedRecords(JSON.parse(this.responseText));
      } catch { /* ignore */ }
    });
    return rawSend.apply(this, args);
  };
  ${PAGE_HELPERS}

  const send = (eventType, rawTarget) => {
    const el = rawTarget instanceof HTMLElement ? rawTarget : rawTarget?.parentElement;
    if (!(el instanceof HTMLElement)) return;
    const tableBox = el.closest(".vxe-cell--checkbox, .vxe-checkbox--icon, [class*='vxe-table-icon-checkbox'], .ant-checkbox, .el-checkbox");
    const control = tableBox
      ? (tableBox.closest(".vxe-cell--checkbox, .ant-checkbox, .el-checkbox") || tableBox)
      : el.matches('input,select,textarea,button,[contenteditable="true"],[role="button"],[role="combobox"],[role="checkbox"],[role="switch"],[role="radio"],[role="option"],.arco-select-view,[class*="arco-select-view"],.el-select__wrapper,.ant-select-selector')
      ? el
      : el.closest('input,select,textarea,button,[contenteditable="true"],[role="button"],[role="combobox"],[role="checkbox"],[role="switch"],[role="radio"],[role="option"],.arco-select-view,[class*="arco-select-view"],.el-select__wrapper,.ant-select-selector,a') || el;
    const formContainer = control.closest('form, [role="form"], .el-form, .ant-form, .arco-form, [data-form], [role="dialog"], [role="alertdialog"], .el-dialog, .el-drawer, .el-overlay-dialog, .ant-modal, .ant-drawer, .arco-modal, .arco-drawer') || activeScope();
    const actionLabel = control.matches('button,a,[role="button"],[role="link"],input[type="button"],input[type="submit"]')
      ? clean(control.getAttribute("aria-label") || control.textContent || control.getAttribute("value") || "")
      : "";
    const row = control.closest(".vxe-body--row, .ant-table-tbody tr.ant-table-row, .el-table__body .el-table__row, tbody tr");
    const rowLabel = tableBox && row ? clean(row.innerText).slice(0, 40) : "";
    const payload = {
      eventType,
      pageUrl: location.href,
      selector: rowLabel ? "label=" + rowLabel : selectorOf(control),
      tag: control.tagName.toLowerCase(),
      role: control.getAttribute("role") || undefined,
      text: clean(control.textContent || control.getAttribute("value") || ""),
      label: actionLabel || labelOf(control) || rowLabel || undefined,
      name: nameOf(control),
      inputType: control.getAttribute("type") || undefined,
      value: (() => {
        const key = [control.getAttribute("name"), control.getAttribute("id"), control.getAttribute("autocomplete"), control.getAttribute("type")].filter(Boolean).join(" ");
        if (/password|passwd|pwd|secret|token|credential|current-password|new-password/i.test(key)) return "[REDACTED]";
        return displayValue(control);
      })(),
      options: eventType === "change" || eventType === "submit"
        ? (optionsOf(control) || (isChoiceControl(control) ? collectOptionRecords() : undefined))
        : optionsOf(control),
      visibleOptions: (eventType === "change" || eventType === "submit") && isChoiceControl(control) ? collectVisibleOptions(document) : [],
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
