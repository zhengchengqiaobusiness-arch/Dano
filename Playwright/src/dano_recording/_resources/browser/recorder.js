(() => {
  "use strict";
  if (globalThis.__danoRecorderInstalled) return;
  Object.defineProperty(globalThis, "__danoRecorderInstalled", { value: true });

  const selectorFor = (element) => {
    if (!(element instanceof Element)) return "";
    if (element.id) return `#${CSS.escape(element.id)}`;
    const testId = element.getAttribute("data-testid");
    if (testId) return `[data-testid="${CSS.escape(testId)}"]`;
    const name = element.getAttribute("name");
    if (name) return `${element.tagName.toLowerCase()}[name="${CSS.escape(name)}"]`;
    return element.tagName.toLowerCase();
  };

  const publish = (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const type = target.getAttribute("type")?.toLowerCase();
    const sensitive = type === "password" || /password|passwd|secret|token/i.test(target.getAttribute("name") ?? "");
    const payload = {
      event: event.type,
      selector: selectorFor(target),
      tag: target.tagName.toLowerCase(),
      name: target.getAttribute("name") ?? "",
      inputType: type ?? "",
      value: sensitive ? "[REDACTED]" : ("value" in target ? target.value : undefined),
      checked: "checked" in target ? Boolean(target.checked) : undefined,
      timestamp: Date.now(),
    };
    if (typeof globalThis.__danoRecordAction === "function") {
      Promise.resolve(globalThis.__danoRecordAction(payload)).catch(() => {});
    }
  };

  for (const type of ["click", "input", "change", "submit"]) {
    document.addEventListener(type, publish, { capture: true, passive: true });
  }
})();
