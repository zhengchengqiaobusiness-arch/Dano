export function fillEmptyCompanionNames(model: Record<string, unknown>) {
  if (!model || typeof model !== "object") return [];
  const changed: string[] = [];
  const keys = Object.keys(model);
  for (const key of keys) {
    if (!/(Name|Title|Label)$/.test(key)) continue;
    const current = model[key];
    if (current !== undefined && current !== null && String(current).trim() !== "") continue;
    const stem = key.replace(/(Name|Title|Label)$/, "");
    const idKey = keys.find(item => item === `${stem}Id` || item === `${stem}ID`);
    if (!idKey) continue;
    const id = model[idKey];
    if (id === undefined || id === null || id === "" || id === 0 || id === "0") continue;
    for (const other of keys) {
      if (other === key || !/(Name|Title|Label)$/.test(other)) continue;
      const otherStem = other.replace(/(Name|Title|Label)$/, "");
      const otherIdKey = keys.find(item => item === `${otherStem}Id` || item === `${otherStem}ID`);
      if (!otherIdKey) continue;
      const otherName = model[other];
      if (String(model[otherIdKey]) === String(id) && otherName !== undefined && otherName !== null && String(otherName).trim() !== "") {
        model[key] = otherName;
        changed.push(key);
        break;
      }
    }
  }
  return changed;
}

export const PATCH_VUE_FORM_COMPANIONS = `(() => {
  const fillEmptyCompanionNames = ${fillEmptyCompanionNames.toString()};
  const seen = new Set();
  const changed = [];
  const unwrap = (value) => {
    if (!value || typeof value !== "object") return value;
    if (value.__v_isRef || value.__v_isRef === true) return value.value;
    return value;
  };
  const consider = (value) => {
    const model = unwrap(value);
    if (!model || typeof model !== "object" || Array.isArray(model) || seen.has(model)) return;
    seen.add(model);
    const keys = Object.keys(model);
    if (!keys.length || keys.length > 120) return;
    if (!keys.some(key => /(Id|ID)$/.test(key)) || !keys.some(key => /Name$/.test(key))) return;
    changed.push(...fillEmptyCompanionNames(model));
  };
  const inspect = (bag) => {
    const raw = unwrap(bag);
    if (!raw || typeof raw !== "object") return;
    consider(raw);
    for (const value of Object.values(raw)) consider(value);
  };
  const walk = (el) => {
    const inst = el.__vueParentComponent || el.__vue__;
    if (inst) {
      inspect(inst.setupState);
      inspect(inst.ctx);
      inspect(inst.proxy);
      inspect(inst.data);
      inspect(inst.$data);
    }
    for (const child of el.children || []) walk(child);
  };
  walk(document.body);
  return [...new Set(changed)];
})()`;
