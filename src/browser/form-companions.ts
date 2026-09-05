export function isEmptyId(id: unknown) {
  return id === undefined || id === null || id === "" || id === 0 || id === "0";
}

export function isEmptyText(value: unknown) {
  return value === undefined || value === null || String(value).trim() === "";
}

export function fillEmptyCompanionNames(model: Record<string, unknown>) {
  if (!model || typeof model !== "object") return [];
  const changed: string[] = [];
  const keys = Object.keys(model);
  for (const key of keys) {
    if (!/(Name|Title|Label)$/.test(key)) continue;
    if (!isEmptyText(model[key])) continue;
    const stem = key.replace(/(Name|Title|Label)$/, "");
    const idKey = keys.find(item => item === `${stem}Id` || item === `${stem}ID`);
    if (!idKey || isEmptyId(model[idKey])) continue;
    const id = model[idKey];
    for (const other of keys) {
      if (other === key || !/(Name|Title|Label)$/.test(other)) continue;
      const otherStem = other.replace(/(Name|Title|Label)$/, "");
      const otherIdKey = keys.find(item => item === `${otherStem}Id` || item === `${otherStem}ID`);
      if (!otherIdKey || isEmptyText(model[other])) continue;
      if (String(model[otherIdKey]) !== String(id)) continue;
      model[key] = model[other];
      changed.push(key);
      break;
    }
  }
  return changed;
}

export function scoreLinkedRecord(model: Record<string, unknown>, record: Record<string, unknown>) {
  if (!model || !record || model === record) return 0;
  let score = 0;
  for (const key of Object.keys(model)) {
    const current = model[key];
    if (/(Id|ID)$/.test(key)) {
      if (isEmptyId(current)) continue;
      if (String(record[key]) === String(current)) {
        score += 2;
        continue;
      }
      if (String(record.id) !== String(current)) continue;
      const stem = key.replace(/(Id|ID)$/, "");
      const nameKey = `${stem}Name`;
      const modelName = model[nameKey];
      if (
        !isEmptyText(modelName)
        && (String(record[nameKey]) === String(modelName) || String(record.name) === String(modelName))
      ) {
        score += 3;
      }
      continue;
    }
    if (isEmptyText(current)) continue;
    if (record[key] !== undefined && String(record[key]) === String(current)) score += 1;
  }
  return score;
}

export function fillMissingIdentitiesFromLinkedRecords(
  model: Record<string, unknown>,
  records: Record<string, unknown>[]
) {
  if (!model || typeof model !== "object" || !Array.isArray(records) || !records.length) return [];
  const ranked = records
    .filter(record => record && typeof record === "object" && !Array.isArray(record) && record !== model)
    .map(record => ({ record, score: scoreLinkedRecord(model, record) }))
    .filter(item => item.score > 0)
    .sort((left, right) => right.score - left.score);
  if (!ranked.length) return [];
  const best = ranked[0]!.score;
  const top = ranked.filter(item => item.score === best).map(item => item.record);
  const changed: string[] = [];
  const keys = Object.keys(model);
  for (const key of keys) {
    if (!/(Name|Title|Label)$/.test(key) || !isEmptyText(model[key])) continue;
    const stem = key.replace(/(Name|Title|Label)$/, "");
    const idKey = keys.find(item => item === `${stem}Id` || item === `${stem}ID`);
    const names = [...new Set(top.map(record => record[key]).filter(value => !isEmptyText(value)).map(value => String(value)))];
    if (names.length !== 1) continue;
    const source = top.find(record => !isEmptyText(record[key]));
    if (!source) continue;
    model[key] = source[key];
    changed.push(key);
    if (idKey && isEmptyId(model[idKey])) {
      const ids = [...new Set(top.map(record => record[idKey]).filter(value => !isEmptyId(value)).map(value => String(value)))];
      if (ids.length === 1) {
        const idSource = top.find(record => !isEmptyId(record[idKey]));
        if (idSource) {
          model[idKey] = idSource[idKey];
          changed.push(idKey);
        }
      }
    }
  }
  return changed;
}

export function applyFormCompanions(model: Record<string, unknown>, records: Record<string, unknown>[] = []) {
  return [...new Set([
    ...fillEmptyCompanionNames(model),
    ...fillMissingIdentitiesFromLinkedRecords(model, records)
  ])];
}

export const PATCH_VUE_FORM_COMPANIONS = `(() => {
  const isEmptyId = ${isEmptyId.toString()};
  const isEmptyText = ${isEmptyText.toString()};
  const fillEmptyCompanionNames = ${fillEmptyCompanionNames.toString()};
  const scoreLinkedRecord = ${scoreLinkedRecord.toString()};
  const fillMissingIdentitiesFromLinkedRecords = ${fillMissingIdentitiesFromLinkedRecords.toString()};
  const applyFormCompanions = ${applyFormCompanions.toString()};
  const seen = new Set();
  const catalog = [];
  const unwrap = (value, depth) => {
    if (depth > 4 || !value || typeof value !== "object") return value;
    if (value.__v_isRef) return unwrap(value.value, depth + 1);
    return value;
  };
  const looksLikeRecord = (model) => {
    if (!model || typeof model !== "object" || Array.isArray(model)) return false;
    const keys = Object.keys(model);
    if (!keys.length || keys.length > 80) return false;
    const hasId = keys.includes("id") || keys.some(key => /(Id|ID)$/.test(key));
    const hasName = keys.includes("name") || keys.some(key => /(Name|Title|Label)$/.test(key));
    return hasId && hasName;
  };
  const collect = (value, depth) => {
    const model = unwrap(value, 0);
    if (!model || typeof model !== "object" || depth > 8 || seen.has(model)) return;
    if (typeof Node !== "undefined" && model instanceof Node) return;
    if (typeof Window !== "undefined" && model instanceof Window) return;
    seen.add(model);
    if (Array.isArray(model)) {
      for (const item of model.slice(0, 80)) collect(item, depth + 1);
      return;
    }
    if (looksLikeRecord(model)) catalog.push(model);
    for (const [key, child] of Object.entries(model)) {
      if (typeof child === "function") continue;
      if (key.startsWith("_")) continue;
      if (key.startsWith("$") && key !== "$data" && key !== "$pinia" && key !== "$store" && key !== "$state") continue;
      collect(child, depth + 1);
    }
  };
  const inspect = (bag) => {
    collect(bag, 0);
  };
  const walk = (el) => {
    const inst = el.__vueParentComponent || el.__vue__;
    if (inst) {
      inspect(inst.setupState);
      inspect(inst.devtoolsRawSetupState);
      inspect(inst.ctx);
      inspect(inst.proxy);
      inspect(inst.data);
      inspect(inst.$data);
      inspect(inst.props);
      inspect(inst.appContext && inst.appContext.config && inst.appContext.config.globalProperties
        && inst.appContext.config.globalProperties.$pinia && inst.appContext.config.globalProperties.$pinia.state
        && inst.appContext.config.globalProperties.$pinia.state.value);
      inspect(inst.$pinia && inst.$pinia.state && inst.$pinia.state.value);
      inspect(inst.$store && inst.$store.state);
    }
    for (const child of el.children || []) walk(child);
  };
  walk(document.body);
  const app = document.querySelector("#app");
  const vueApp = app && (app.__vue_app__ || (app.__vueParentComponent && app.__vueParentComponent.appContext && app.__vueParentComponent.appContext.app));
  if (vueApp && vueApp._context && vueApp._context.config && vueApp._context.config.globalProperties) {
    const pinia = vueApp._context.config.globalProperties.$pinia;
    if (pinia && pinia.state) inspect(pinia.state.value || pinia.state);
  }
  if (Array.isArray(window.__bssLinkedRecords)) {
    for (const record of window.__bssLinkedRecords) collect(record, 0);
  }
  const changed = [];
  for (const model of catalog) changed.push(...applyFormCompanions(model, catalog));
  return [...new Set(changed)];
})()`;
