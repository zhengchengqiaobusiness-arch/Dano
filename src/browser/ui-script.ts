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
    return undefined;
  };
  const labelOf = (el) => {
    if (el.labels?.length) return text([...el.labels].map(l => l.textContent).join(" "));
    const aria = el.getAttribute("aria-label");
    if (aria) return text(aria);
    const labelled = el.getAttribute("aria-labelledby");
    if (labelled) return text(labelled.split(/\s+/).map(id => document.getElementById(id)?.textContent || "").join(" "));
    const parentLabel = el.closest("label");
    return text(parentLabel?.textContent || "");
  };
  const selectorOf = (el) => {
    if (el.id) return "#" + CSS.escape(el.id);
    const name = el.getAttribute("name");
    if (name) return el.tagName.toLowerCase() + '[name="' + CSS.escape(name) + '"]';
    const testid = el.getAttribute("data-testid");
    if (testid) return '[data-testid="' + CSS.escape(testid) + '"]';
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

  const formSnapshot = (form) => {
    if (!(form instanceof HTMLFormElement)) return undefined;
    return [...form.elements].slice(0, 200).map(el => {
      if (!(el instanceof HTMLElement)) return null;
      return {
        name: el.getAttribute("name") || undefined,
        label: labelOf(el) || undefined,
        type: el.getAttribute("type") || el.tagName.toLowerCase(),
        value: valueOf(el),
        required: el.hasAttribute("required"),
        options: optionsOf(el)
      };
    }).filter(Boolean);
  };

  const send = (eventType, rawTarget) => {
    const el = rawTarget instanceof HTMLElement ? rawTarget : rawTarget?.parentElement;
    if (!(el instanceof HTMLElement)) return;
    const control = el.matches('input,select,textarea,button,[role="button"],[role="combobox"]')
      ? el
      : el.closest('input,select,textarea,button,[role="button"],[role="combobox"],a') || el;

    const payload = {
      eventType,
      pageUrl: location.href,
      selector: selectorOf(control),
      tag: control.tagName.toLowerCase(),
      role: control.getAttribute("role") || undefined,
      text: text(control.textContent || control.getAttribute("value") || ""),
      label: labelOf(control) || undefined,
      name: control.getAttribute("name") || undefined,
      inputType: control.getAttribute("type") || undefined,
      value: valueOf(control),
      options: optionsOf(control),
      visibleOptions: visibleRoleOptions(),
      form: formSnapshot(control.closest("form"))
    };
    Promise.resolve(window.__bssRecordUi?.(payload)).catch(() => {});
  };

  const inputTimers = new WeakMap();
  document.addEventListener("click", e => send("click", e.target), true);
  document.addEventListener("input", e => {
    const target = e.target;
    if (!(target instanceof HTMLElement)) return;
    const previous = inputTimers.get(target);
    if (previous) clearTimeout(previous);
    inputTimers.set(target, setTimeout(() => send("input", target), 250));
  }, true);
  document.addEventListener("change", e => send("change", e.target), true);
  document.addEventListener("submit", e => send("submit", e.target), true);
})();
`;
