(() => {
  "use strict";
  const OPTION_KEYS = new Set(["options", "items", "dataSource", "valueEnum", "choices"]);
  const NESTED_KEYS = new Set(["props", "memoizedProps", "pendingProps", "setupState", "ctx", "data"]);
  const selectorFor = (element) => {
    if (element.id) return `#${CSS.escape(element.id)}`;
    const testId = element.getAttribute("data-testid");
    if (testId) return `[data-testid="${CSS.escape(testId)}"]`;
    const name = element.getAttribute("name");
    if (name) return `${element.tagName.toLowerCase()}[name="${CSS.escape(name)}"]`;
    return element.tagName.toLowerCase();
  };
  const ownData = (object, key) => {
    try {
      const descriptor = Object.getOwnPropertyDescriptor(object, key);
      return descriptor && "value" in descriptor ? descriptor.value : undefined;
    } catch {
      return undefined;
    }
  };
  const serialiseOptions = (value) => {
    if (!Array.isArray(value)) return [];
    return value.slice(0, 1000).flatMap((item) => {
      if (!item || typeof item !== "object") return [];
      const label = ownData(item, "label") ?? ownData(item, "name") ?? ownData(item, "text");
      const optionValue = ownData(item, "value") ?? ownData(item, "id") ?? ownData(item, "key");
      if (label === undefined || optionValue === undefined) return [];
      return [{ label: String(label), value: optionValue, disabled: Boolean(ownData(item, "disabled")) }];
    });
  };
  const inspectObject = (object, framework, selector, output, path = "", depth = 0, seen = new Set()) => {
    if (!object || typeof object !== "object" || depth > 3 || seen.has(object)) return;
    seen.add(object);
    for (const key of Object.getOwnPropertyNames(object).slice(0, 200)) {
      const value = ownData(object, key);
      const propertyPath = path ? `${path}.${key}` : key;
      if (OPTION_KEYS.has(key)) {
        const options = serialiseOptions(value);
        if (options.length) output.push({
          framework,
          component_name: String(ownData(object, "displayName") ?? ownData(object, "name") ?? ""),
          control_id: null,
          selector,
          property_path: propertyPath,
          options,
        });
      } else if (NESTED_KEYS.has(key)) {
        inspectObject(value, framework, selector, output, propertyPath, depth + 1, seen);
      }
    }
  };
  globalThis.__danoProbeComponents = () => {
    const output = [];
    for (const element of Array.from(document.querySelectorAll("[id],[data-testid],select,[role=combobox]")).slice(0, 1000)) {
      const selector = selectorFor(element);
      for (const key of Object.getOwnPropertyNames(element).slice(0, 100)) {
        if (key.startsWith("__reactProps$") || key.startsWith("__reactFiber$")) {
          inspectObject(ownData(element, key), "react", selector, output);
        } else if (key === "__vueParentComponent__" || key === "__vue__") {
          inspectObject(ownData(element, key), "vue", selector, output);
        } else if (key.startsWith("__ngContext__")) {
          inspectObject(ownData(element, key), "angular", selector, output);
        }
      }
    }
    return output.slice(0, 1000);
  };
})();
