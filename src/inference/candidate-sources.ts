import type { CapabilityContract, CandidateRule, EvidenceEvent, InputFormField, UiEvidence } from "../domain.js";
import { recordedLists } from "./field-resolver.js";

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
  const arrays = ["$.data[*]", "$.data.list[*]", "$.list[*]"];
  const valueKeys = ["id", "value", "code"];
  const labelKeys = ["name", "label", "nickname", "title"];
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

function displayNamesOf(capability: CapabilityContract, events: EvidenceEvent[]) {
  const ids = new Set(capability.evidence.filter(item => item.kind === "network").map(item => item.eventId));
  return new Set(
    recordedLists(events.filter(event => ids.has(event.id)))
      .flatMap(list => list.rows.map(row => row.name ?? row.label ?? row.title ?? row.nickname))
      .map(value => value === undefined || value === null || value === "" ? "" : String(value))
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
    && item.validation.status === "verified"
    && Boolean(listPaths(item.outputSchema))
  );
  const byTrigger = events.length
    ? lists.filter(item => triggerLabels(item, events).has(field.label))
    : [];
  const byPath = lists.filter(item => pathHasStem(item.transport.pathTemplate, fieldStem(field.name)));
  const optionLabels = field.candidates?.type === "static"
    ? field.candidates.values.map(item => String(item.label || "")).filter(Boolean)
    : [];
  const byOptions = optionLabels.length >= 2 && events.length
    ? lists.filter(item => {
      const names = displayNamesOf(item, events);
      const hit = optionLabels.filter(label => names.has(label)).length;
      return hit >= Math.max(2, optionLabels.length - 1) && names.size <= optionLabels.length * 2 + 4;
    })
    : [];
  const displays = events.length ? selectedDisplays(field, events) : [];
  const bySelected = displays.length
    ? lists.filter(item => {
      const names = displayNamesOf(item, events);
      return displays.every(label => names.has(label)) && names.size <= Math.max(40, displays.length * 8);
    })
    : [];
  const closedEnum = field.candidates?.type === "static"
    && field.candidates.values.length >= 2
    && field.candidates.values.length <= 20;
  if (closedEnum) return pickUnique(byTrigger) || pickUnique(byPath);
  return pickUnique(byTrigger) || pickUnique(byPath) || pickUnique(byOptions) || pickUnique(bySelected);
}

export function attachCandidateSources(catalog: CapabilityContract[], events: EvidenceEvent[] = []): CapabilityContract[] {
  return catalog.map(capability => ({
    ...capability,
    inputForm: capability.inputForm.map(field => {
      if (field.source !== "caller") return field;
      if (field.widget === "number" || field.widget === "date" || field.widget === "textarea") return field;
      if (/天数|数量|金额|单价|税率|库存/.test(field.label || "")) return field;
      const source = lookupFor(field, catalog.filter(item => item.id !== capability.id), events);
      const paths = source ? listPaths(source.outputSchema) : undefined;
      if (!source || !paths) return field;
      const candidates: CandidateRule = {
        type: "capability",
        capabilityId: source.id,
        valuePath: paths.valuePath,
        labelPath: paths.labelPath
      };
      return {
        ...field,
        widget: "select",
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
  if (field.source === "caller") return `调用方按页面原始格式输入（${field.valueType}），不要改成其它类型`;
  if (field.sourceDetail && (field.defaultRule?.startsWith("from:") || field.defaultRule?.startsWith("computed:") || field.defaultRule?.startsWith("copy:") || field.source === "binding")) {
    return field.sourceDetail;
  }
  if (/^(pageNo|pageSize|pageNum|page|size|current|offset|limit)$/i.test(field.name)) {
    return `后台自动处理：${field.sourceDetail}`;
  }
  if (field.defaultRule?.startsWith("literal:")) {
    return `系统默认值 ${field.defaultRule.slice("literal:".length)}，调用方未提供时使用，不是某次录制的业务样本`;
  }
  if (field.source === "computed") return field.sourceDetail || "由请求内其它字段自动计算，调用方不要手填";
  if (field.source === "generated") return `后台自动生成，调用方不要手填`;
  if (field.source === "fixed") return `系统补齐默认值 ${field.defaultRule || ""}，调用方可覆盖`;
  if (field.source === "session") return `会话环境自动提供 ${field.defaultRule || ""}`;
  return field.sourceDetail ? `后台自动处理：${field.sourceDetail}` : "后台自动处理";
}
