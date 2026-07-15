(() => {
  "use strict";
  if (globalThis.__danoMutationObserverInstalled) return;
  Object.defineProperty(globalThis, "__danoMutationObserverInstalled", { value: true });
  const queue = [];
  const selectorFor = (element) => {
    if (!(element instanceof Element)) return "";
    if (element.id) return `#${CSS.escape(element.id)}`;
    const testId = element.getAttribute("data-testid");
    if (testId) return `[data-testid="${CSS.escape(testId)}"]`;
    const name = element.getAttribute("name");
    if (name) return `${element.tagName.toLowerCase()}[name="${CSS.escape(name)}"]`;
    return element.tagName.toLowerCase();
  };
  const observer = new MutationObserver((records) => {
    for (const record of records.slice(0, 500)) {
      const target = record.target instanceof Element ? record.target : record.target.parentElement;
      if (!target) continue;
      const control = target.matches("input,select,textarea,[contenteditable=true]")
        ? target
        : target.closest("input,select,textarea,[contenteditable=true]");
      const sensitive = control && (
        control.getAttribute("type")?.toLowerCase() === "password"
        || /password|passwd|secret|token/i.test(control.getAttribute("name") ?? "")
      );
      queue.push({
        mutationType: record.type,
        attributeName: record.attributeName || null,
        tag: target.tagName.toLowerCase(),
        id: target.id || null,
        role: target.getAttribute("role"),
        selector: control ? selectorFor(control) : selectorFor(target),
        controlTag: control ? control.tagName.toLowerCase() : null,
        name: control ? (control.getAttribute("name") || "") : "",
        inputType: control ? (control.getAttribute("type") || "") : "",
        value: control && !sensitive && "value" in control ? control.value : undefined,
        checked: control && "checked" in control ? Boolean(control.checked) : undefined,
        sensitive: Boolean(sensitive),
        timestamp: Date.now(),
      });
      if (queue.length > 2000) queue.shift();
    }
  });
  observer.observe(document.documentElement, { subtree: true, childList: true, attributes: true });
  globalThis.__danoDrainMutations = () => queue.splice(0, queue.length);
})();
