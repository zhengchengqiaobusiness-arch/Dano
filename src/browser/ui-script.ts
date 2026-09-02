export const UI_RECORDER_SCRIPT = String.raw`
(() => {
  if (window.__BSS_RECORDER_INSTALLED__) return;
  window.__BSS_RECORDER_INSTALLED__ = true;

  const text = (value) => String(value || "").replace(/\s+/g, " ").trim().slice(0, 500);
  const sensitive = (el) => {
    const input = el instanceof HTMLInputElement ? el : null;
    const key = [el.getAttribute("name"), el.getAttribute("id"), el.getAttribute("autocomplete"), input?.type]
      .filter(Boolean).join(" ");
    return /password|passwd|pwd|secret|token|credential|current-password|new-password/i.test(key);
  };
  const valueOf = (el) => {
    if (sensitive(el)) return "[REDACTED]";
    if (el instanceof HTMLInputElement) {
      if (el.type === "checkbox" || el.type === "radio") return el.checked;
      return el.value;
    }
    if (el instanceof HTMLSelectElement) return el.multiple ? [...el.selectedOptions].map(o => o.value) : el.value;
    if (el instanceof HTMLTextAreaElement) return el.value;
    if (el.isContentEditable) return text(el.textContent);
    return undefined;
  };
  const generatedName = (value) => /^(el-id-\d+-\d+|el-[a-z]+-\d+)$/i.test(String(value || ""));
  const nameOf = (el) => {
    const named = el.getAttribute("name") || el.getAttribute("data-field") || el.getAttribute("data-name");
    if (named) return named;
    const formItem = el.closest('.el-form-item,.ant-form-item,.arco-form-item,[class*="form-item"]');
    const prop = formItem?.getAttribute("prop") || formItem?.getAttribute("data-prop");
    if (prop) return prop;
    const id = el.getAttribute("id");
    return id && !generatedName(id) ? id : undefined;
  };
  const labelOf = (el) => {
    if (el.labels?.length) return text([...el.labels].map(l => l.textContent).join(" "));
    const aria = el.getAttribute("aria-label");
    if (aria) return text(aria);
    const labelled = el.getAttribute("aria-labelledby");
    if (labelled) return text(labelled.split(/\s+/).map(id => document.getElementById(id)?.textContent || "").join(" "));
    const parentLabel = el.closest("label");
    if (parentLabel) return text(parentLabel.textContent);
    const formItem = el.closest('.el-form-item,.ant-form-item,.arco-form-item,[class*="form-item"],[class*="formItem"]');
    const itemLabel = formItem?.querySelector('label,.el-form-item__label,.ant-form-item-label,[class*="label"]');
    return text(itemLabel?.textContent || el.getAttribute("placeholder") || el.getAttribute("title") || "");
  };
  const selectorOf = (el) => {
    const placeholder = el.getAttribute("placeholder");
    if (placeholder) return "placeholder=" + placeholder;
    if (el.id && !generatedName(el.id)) return "#" + CSS.escape(el.id);
    const label = labelOf(el);
    if (label && label.length <= 40) return "label=" + label;
    const role = el.getAttribute("role") || (el.matches("button,.el-button") ? "button" : "");
    const roleName = text(el.getAttribute("aria-label") || el.textContent || "");
    if (role && roleName && roleName.length <= 40) return "role=" + role + '[name="' + roleName + '"]';
    const testid = el.getAttribute("data-testid");
    if (testid) return '[data-testid="' + CSS.escape(testid) + '"]';
    const name = nameOf(el);
    if (name) return el.tagName.toLowerCase() + '[name="' + CSS.escape(name) + '"]';
    const parts = [];
    let node = el;
    for (let i = 0; node && node.nodeType === 1 && i < 4; i++, node = node.parentElement) {
      let part = node.tagName.toLowerCase();
      if (node.classList.length) part += "." + [...node.classList].slice(0, 2).map(c => CSS.escape(c)).join(".");
      parts.unshift(part);
    }
    return parts.join(" > ");
  };
  const optionsOf = (el) => {
    if (el instanceof HTMLSelectElement) {
      return [...el.options].slice(0, 300).map(o => ({ value: o.value, label: text(o.textContent) }));
    }
    const listId = el.getAttribute("list");
    if (listId) {
      const list = document.getElementById(listId);
      if (list instanceof HTMLDataListElement) {
        return [...list.options].slice(0, 300).map(o => ({ value: o.value, label: text(o.label || o.value) }));
      }
    }
    return undefined;
  };
  const visibleRoleOptions = () => [...document.querySelectorAll('[role="option"]')]
    .filter(el => {
      const s = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      return s.visibility !== "hidden" && s.display !== "none" && r.width > 0 && r.height > 0;
    })
    .slice(0, 200)
    .map(el => text(el.textContent))
    .filter(Boolean);

  const formSnapshot = (container) => {
    if (!(container instanceof HTMLElement)) return undefined;
    const controls = container instanceof HTMLFormElement
      ? [...container.elements]
      : [...container.querySelectorAll('input,select,textarea,[contenteditable="true"],[role="combobox"],[role="checkbox"],[role="switch"],[data-field]')];
    return controls.slice(0, 200).map(el => {
      if (!(el instanceof HTMLElement)) return null;
      return {
        name: nameOf(el),
        label: labelOf(el) || undefined,
        type: el.getAttribute("type") || el.tagName.toLowerCase(),
        value: valueOf(el),
        required: el.hasAttribute("required") || el.getAttribute("aria-required") === "true" || el.closest('.is-required,[class*="required"]') !== null,
        options: optionsOf(el)
      };
    }).filter(Boolean);
  };

  const send = (eventType, rawTarget) => {
    const el = rawTarget instanceof HTMLElement ? rawTarget : rawTarget?.parentElement;
    if (!(el instanceof HTMLElement)) return;
    const control = el.matches('input,select,textarea,button,[contenteditable="true"],[role="button"],[role="combobox"],[role="checkbox"],[role="switch"],[role="radio"],[role="option"]')
      ? el
      : el.closest('input,select,textarea,button,[contenteditable="true"],[role="button"],[role="combobox"],[role="checkbox"],[role="switch"],[role="radio"],[role="option"],a') || el;

    const formContainer = control.closest('form,[role="form"],.el-form,.ant-form,.arco-form,[data-form]');

    const payload = {
      eventType,
      pageUrl: location.href,
      selector: selectorOf(control),
      tag: control.tagName.toLowerCase(),
      role: control.getAttribute("role") || undefined,
      text: text(control.textContent || control.getAttribute("value") || ""),
      label: labelOf(control) || undefined,
      name: nameOf(control),
      inputType: control.getAttribute("type") || undefined,
      value: valueOf(control),
      options: optionsOf(control),
      visibleOptions: visibleRoleOptions(),
      form: formSnapshot(formContainer)
    };
    Promise.resolve(window.__bssRecordUi?.(payload)).catch(() => {});
  };

  const inputTimers = new WeakMap();
  document.addEventListener("click", e => send("click", e.composedPath?.()[0] || e.target), true);
  document.addEventListener("input", e => {
    const target = e.composedPath?.()[0] || e.target;
    if (!(target instanceof HTMLElement)) return;
    const previous = inputTimers.get(target);
    if (previous) clearTimeout(previous);
    inputTimers.set(target, setTimeout(() => send("input", target), 250));
  }, true);
  document.addEventListener("change", e => send("change", e.composedPath?.()[0] || e.target), true);
  document.addEventListener("submit", e => send("submit", e.composedPath?.()[0] || e.target), true);
})();
`;
