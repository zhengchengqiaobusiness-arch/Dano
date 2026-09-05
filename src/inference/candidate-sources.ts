import type { CapabilityContract, CandidateRule, EvidenceEvent, InputFormField, UiEvidence } from "../domain.js";
import { looksDirectoryPicker, looksPickerField, pickerEntity, recordedLists } from "./field-resolver.js";

function pathPickerEntity(path: string) {
  if (/\/(?:dept|department)(?:\/|$)/i.test(path)) return "dept";
  if (/\/role(?:\/|$)/i.test(path)) return "role";
  if (/\/post(?:\/|$)/i.test(path)) return "post";
  if (/\/user(?:\/|$)/i.test(path)) return "user";
  return undefined;
}

function schemaNode(schema: any, jsonPath: string): any {
  const parts = jsonPath.replace(/^\$\.?/, "").split(".").filter(Boolean);
  let current = schema;
  for (const raw of parts) {
    const wildcard = raw.endsWith("[*]");
    const part = wildcard ? raw.slice(0, -3) : raw;
    if (part) current = current?.properties?.[part];
    if (!current) return undefined;
    if (wildcard) current = current.items;
  }
  return current;
}

function listPaths(schema: CapabilityContract["outputSchema"]): { valuePath: string; labelPath: string } | undefined {
  const arrays = ["$.data[*]", "$.data.list[*]", "$.data.rows[*]", "$.data.records[*]", "$.list[*]", "$.rows[*]"];
  const valueKeys = ["id", "value", "code"];
  const labelKeys = ["name", "label", "nickname", "username", "title", "xtmc", "yymc", "bmmc", "ssbmmc", "yyxtmc", "mc", "csmc"];
  for (const prefix of arrays) {
    const item = schemaNode(schema, prefix);
    const properties = item?.properties || {};
    const valueKey = valueKeys.find(key => properties[key]);
    const labelKey = labelKeys.find(key => properties[key]);
    if (valueKey && labelKey) {
      return { valuePath: `${prefix}.${valueKey}`, labelPath: `${prefix}.${labelKey}` };
    }
  }
  return undefined;
}

function fieldStem(name: string) {
  return name.replace(/Ids$/i, "").replace(/Id$/i, "").toLowerCase();
}

function pathHasStem(path: string, stem: string) {
  if (stem.length < 3) return false;
  const normalized = path.toLowerCase();
  return normalized.includes(`/${stem}/`)
    || normalized.includes(`/${stem}s/`)
    || normalized.endsWith(`/${stem}`)
    || normalized.includes(`/${stem}-`);
}

function triggerLabels(capability: CapabilityContract, events: EvidenceEvent[]) {
  const byId = new Map(events.map(event => [event.id, event]));
  const labels = new Set<string>();
  for (const ref of capability.evidence) {
    if (ref.kind !== "network") continue;
    const network = byId.get(ref.eventId);
    if (network?.kind !== "network" || !network.correlatedUiEvidenceId) continue;
    const ui = byId.get(network.correlatedUiEvidenceId) as UiEvidence | undefined;
    if (ui?.label) labels.add(ui.label);
  }
  return labels;
}

function pickUnique<T>(items: T[]) {
  return items.length === 1 ? items[0] : undefined;
}

function pickDirectoryLookup(items: CapabilityContract[]) {
  if (!items.length) return undefined;
  if (items.length === 1) return items[0];
  const simple = items.filter(item => /simple-list$/i.test(item.transport.pathTemplate || ""));
  return simple.length === 1 ? simple[0] : undefined;
}

export function isLookupListPath(pathTemplate: string) {
  const path = pathTemplate || "";
  return /\/(?:user|dept|department|role|post|tenant|dict)\/(?:page|list|simple-list)$/i.test(path)
    || /simple-list|dict-data|\/enum(?:\/|$)/i.test(path);
}

function triggeredByField(field: InputFormField, capability: CapabilityContract, events: EvidenceEvent[]) {
  return triggerLabels(capability, events).has(field.label);
}

function usableCandidateSource(
  field: InputFormField,
  source: CapabilityContract,
  events: EvidenceEvent[]
) {
  if (triggeredByField(field, source, events)) return true;
  return isLookupListPath(source.transport.pathTemplate || "");
}

function displayNamesOf(capability: CapabilityContract, events: EvidenceEvent[]) {
  const ids = new Set(capability.evidence.filter(item => item.kind === "network").map(item => item.eventId));
  return new Set(
    recordedLists(events.filter(event => ids.has(event.id)))
      .flatMap(list => list.rows.flatMap(row => {
        const names: string[] = [];
        for (const key of ["name", "label", "title", "nickname", "username", "userName", "xtmc", "yymc", "bmmc", "ssbmmc", "yyxtmc", "mc", "csmc"]) {
          const value = row[key];
          if (value !== undefined && value !== null && value !== "") names.push(String(value));
        }
        const username = row.username ?? row.userName;
        const nickname = row.nickname;
        if (username && nickname) {
          names.push(`${username} ${nickname}`, `${nickname} ${username}`);
        }
        return names;
      }))
      .filter(Boolean)
  );
}

function selectedDisplays(field: InputFormField, events: EvidenceEvent[]) {
  const values = new Set<string>();
  for (const event of events) {
    if (event.kind !== "ui") continue;
    if (event.label === field.label && event.value !== undefined && event.value !== "") {
      values.add(String(event.value));
    }
    for (const item of event.form || []) {
      if (item.label === field.label && item.value !== undefined && item.value !== "") {
        values.add(String(item.value));
      }
    }
  }
  return [...values];
}

function lookupFor(field: InputFormField, catalog: CapabilityContract[], events: EvidenceEvent[] = []) {
  const lists = catalog.filter(item =>
    item.operation === "query"
    && Boolean(listPaths(item.outputSchema))
  );
  const lookups = lists.filter(item => isLookupListPath(item.transport.pathTemplate || ""));
  const byTrigger = events.length
    ? lists.filter(item => {
      if (!triggeredByField(field, item, events)) return false;
      if ((looksDirectoryPicker(field) || looksPickerField(field))
        && !isLookupListPath(item.transport.pathTemplate || "")) {
        return false;
      }
      return true;
    })
    : [];
  const byPath = lookups.filter(item => pathHasStem(item.transport.pathTemplate, fieldStem(field.name)));
  const optionLabels = field.candidates?.type === "static"
    ? field.candidates.values.map(item => String(item.label || "")).filter(Boolean)
    : [];
  const byOptions = optionLabels.length >= 2 && events.length
    ? lookups.filter(item => {
      const names = displayNamesOf(item, events);
      const hit = optionLabels.filter(label => names.has(label)).length;
      return hit >= Math.max(2, optionLabels.length - 1) && names.size <= optionLabels.length * 2 + 4;
    })
    : [];
  const displays = events.length ? selectedDisplays(field, events) : [];
  const bySelected = displays.length
    ? lookups.filter(item => {
      const names = displayNamesOf(item, events);
      return displays.every(label => names.has(label)) && names.size <= Math.max(40, displays.length * 8);
    })
    : [];
  const closedEnum = field.candidates?.type === "static"
    && field.candidates.values.length >= 2
    && field.candidates.values.length <= 20
    && !looksPickerField(field);
  const byPicker = looksPickerField(field) || looksDirectoryPicker(field)
    ? lookups.filter(item => {
      if (!/\/(?:user|dept|department|role|post)\/(?:page|list|simple-list)$/i.test(item.transport.pathTemplate || "")) return false;
      const fieldEntity = pickerEntity(field);
      const listEntity = pathPickerEntity(item.transport.pathTemplate || "");
      return Boolean(fieldEntity && listEntity && fieldEntity === listEntity);
    })
    : [];
  if (closedEnum) return pickUnique(byTrigger) || pickUnique(byPath) || pickDirectoryLookup(byPath);
  return pickUnique(byTrigger)
    || pickUnique(byPath)
    || pickDirectoryLookup(byPath)
    || pickUnique(byOptions)
    || pickUnique(bySelected)
    || pickDirectoryLookup(byPicker)
    || pickUnique(byPicker);
}

export function queryCandidateForField(field: InputFormField, catalog: CapabilityContract[], events: EvidenceEvent[] = []) {
  if (field.source !== "caller") return undefined;
  if (field.candidates?.type === "capability") return undefined;
  if (!looksPickerField(field)
    && !looksDirectoryPicker(field)
    && field.widget !== "select"
    && field.widget !== "multiselect"
    && field.candidates?.type !== "static") return undefined;
  return lookupFor(field, catalog, events);
}

export function attachCandidateSources(catalog: CapabilityContract[], events: EvidenceEvent[] = []): CapabilityContract[] {
  return catalog.map(capability => ({
    ...capability,
    inputForm: capability.inputForm.map(field => {
      if (field.source !== "caller") return field;
      if (field.widget === "number" || field.widget === "date" || field.widget === "textarea") return field;
      if (/天数|数量|金额|单价|税率|库存/.test(field.label || "") && !looksPickerField(field)) return field;
      // A normal text box is not a picker merely because a recorded list
      // happens to contain the same sample text. Candidate APIs require real
      // picker/enum UI evidence.
      if (!looksPickerField(field)
        && !looksDirectoryPicker(field)
        && field.widget !== "select"
        && field.widget !== "multiselect"
        && field.candidates?.type !== "static") return field;
      const source = lookupFor(field, catalog.filter(item => item.id !== capability.id), events);
      const paths = source ? listPaths(source.outputSchema) : undefined;
      if (!source || !paths) {
        if (field.candidates?.type === "capability") {
          const existing = catalog.find(item => item.id === field.candidates!.capabilityId);
          if (existing && usableCandidateSource(field, existing, events)) return field;
          const { candidates: _candidates, ...rest } = field;
          return {
            ...rest,
            sourceDetail: /已录制查询接口|data\.list/.test(rest.sourceDetail || "")
              ? "页面有筛选控件；有值时按页面字段名传递，空则省略，与未选时不传该键一致"
              : rest.sourceDetail
          };
        }
        return field;
      }
      const candidates: CandidateRule = {
        type: "capability",
        capabilityId: source.id,
        valuePath: paths.valuePath,
        labelPath: paths.labelPath
      };
      return {
        ...field,
        widget: field.valueType === "array" ? "multiselect" : "select",
        candidates,
        sourceDetail: `调用方从已录制查询接口选择，不要写死录制样本。接口 ${source.transport.method} ${source.transport.pathTemplate}，值 ${paths.valuePath}，显示 ${paths.labelPath}`
      };
    })
  }));
}

export function describeFieldHandling(field: InputFormField) {
  if (field.candidates?.type === "capability") {
    return `接口候选：先调 ${field.candidates.capabilityId}，调用方选显示名，提交 ${field.candidates.valuePath}`;
  }
  if (field.candidates?.type === "static") {
    return `页面固定枚举，调用方直接选择：${field.candidates.values.map(item => `${item.label}=${String(item.value)}`).join("；")}`;
  }
  if (field.source === "caller" && field.defaultRule?.startsWith("computed:")) {
    return `调用方按页面类型提供（${field.valueType}）。未提供时按 ${field.defaultRule.slice("computed:".length)} 计算，可以改`;
  }
  if (field.source === "caller") return `调用方按页面原始格式输入（${field.valueType}），不要改成其它类型`;
  if (field.sourceDetail && (field.defaultRule?.startsWith("from:") || field.defaultRule?.startsWith("computed:") || field.defaultRule?.startsWith("copy:") || field.source === "binding")) {
    return field.sourceDetail;
  }
  if (/^(pageNo|pageSize|pageNum|page|size|current|offset|limit)$/i.test(field.name)) {
    return `后台自动处理：${field.sourceDetail}`;
  }
  if (field.defaultRule?.startsWith("literal:")) {
    return field.sourceDetail || `系统自动补齐 ${field.defaultRule.slice("literal:".length)}，调用方不必提供`;
  }
  if (field.source === "computed") return field.sourceDetail || "由请求内其它字段自动计算，调用方不要手填";
  if (field.source === "generated") return `后台自动生成，调用方不要手填`;
  if (field.source === "fixed") return `系统补齐默认值 ${field.defaultRule || ""}，调用方可覆盖`;
  if (field.source === "session") return `会话环境自动提供 ${field.defaultRule || ""}`;
  return field.sourceDetail ? `后台自动处理：${field.sourceDetail}` : "后台自动处理";
}
