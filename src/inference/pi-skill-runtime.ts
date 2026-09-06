/**
 * 文件级说明：字段归属、选人候选、主能力角色的主流判断已交给
 * `.pi/skills/infer-field-contract` 与 `.pi/skills/judge-primary-capability`。
 * 本文件只做精确同名 / 独值 / 选人行 / 日期槽拼接，以及无模型时的 fallback。
 * 不发明字段，不覆盖已有 from:/computed:。
 */
import { readFile } from "node:fs/promises";
import path from "node:path";
import { z } from "zod";
import type { CapabilityContract, EvidenceEvent, FieldSource, InputFormField, NetworkEvidence, UiEvidence } from "../domain.js";
import { OpenAIReasoner } from "../llm/openai.js";
import { dateDay, recordedClock, recordedDateFormat } from "./date-format.js";
import { isExecutableRule } from "./field-derivation.js";
import { directoryLookupEntity, isLookupQueryPath, isNoiseCapability, isPageResultQuery, sameResource } from "./export-scope.js";
import { ASK_KEY, SEARCH_KEY } from "./heuristics.js";
import {
  collectUiObservations,
  findObservation,
  flattenRequestValues,
  looksDirectoryPicker,
  looksPickerField,
  owningFormEvent,
  pickerEntity,
  relatedUiEvents,
  requestValueAt,
  richTextPlain,
  sameFormShape,
  sameSynonymGroup,
  sameValue,
  semanticConcepts,
  semanticLabelScore,
  uiNameMatches,
  type UiObservation
} from "./field-resolver.js";
import { id } from "../utils.js";

const PAGE_NAME = /^(pageNo|pageSize|pageNum|page|size|current|offset|limit)$/i;
const WRITE = new Set(["create", "update", "review", "delete", "upload", "action"]);
const KEPT_RULE = /^(from:|computed:)/;

const FieldPatch = z.object({
  path: z.string(),
  label: z.string().optional(),
  source: z.enum(["caller", "fixed", "session", "generated", "computed", "binding", "system"]).optional(),
  widget: z.enum(["text", "number", "boolean", "select", "multiselect", "json", "textarea", "date"]).optional(),
  defaultRule: z.string().optional(),
  sourceDetail: z.string().optional(),
  candidateCapabilityId: z.string().optional(),
  candidateValuePath: z.string().optional(),
  candidateLabelPath: z.string().optional(),
  staticCandidates: z.array(z.object({ value: z.unknown(), label: z.string() })).optional()
});

const CapabilityJudgment = z.object({
  id: z.string(),
  operation: z.enum(["query", "create", "update", "review", "delete", "authenticate", "upload", "download", "action", "unknown"]),
  role: z.enum(["primary", "lookup", "noise"]),
  title: z.string().min(1).max(120),
  description: z.string().min(1).max(1200),
  fields: z.array(FieldPatch)
});

const CatalogJudgment = z.object({
  capabilities: z.array(CapabilityJudgment)
});

export async function loadPiSkill(rootDir: string, name: string) {
  return readFile(path.join(rootDir, ".pi", "skills", name, "SKILL.md"), "utf8");
}

function requestInput(event: NetworkEvidence) {
  const method = event.request.method.toUpperCase();
  if (["GET", "HEAD"].includes(method)) return event.request.query;
  if (event.request.body && typeof event.request.body === "object") return event.request.body;
  return event.request.query;
}

function relatedEvents(capability: CapabilityContract, events: EvidenceEvent[]) {
  const ids = new Set(capability.evidence.map(ref => ref.eventId));
  return events.filter(event => ids.has(event.id));
}

function richestNetwork(related: EvidenceEvent[]) {
  const networks = related.filter((event): event is NetworkEvidence => event.kind === "network");
  if (!networks.length) return undefined;
  const size = (event: NetworkEvidence) => flattenRequestValues(requestInput(event)).length;
  return networks.reduce((best, event) => size(event) > size(best) ? event : best);
}

function richestRequestSample(related: EvidenceEvent[]) {
  const owner = richestNetwork(related);
  return owner ? requestInput(owner) : undefined;
}

function requestNamesOf(sample: unknown) {
  return new Set(flattenRequestValues(sample).map(item => item.name));
}

function evidencePage(raw?: string) {
  if (!raw) return "";
  try {
    const parsed = new URL(raw);
    return `${parsed.origin}${parsed.pathname}${parsed.hash.split("?")[0]}`;
  } catch {
    return raw.split("?", 1)[0] || raw;
  }
}

function mergeUi(items: UiEvidence[]) {
  const seen = new Set<string>();
  const out: UiEvidence[] = [];
  for (const item of items) {
    if (seen.has(item.id)) continue;
    seen.add(item.id);
    out.push(item);
  }
  return out;
}

function mergeEvidence(capability: CapabilityContract, extras: UiEvidence[]) {
  const seen = new Set(capability.evidence.map(item => item.eventId));
  return [
    ...capability.evidence,
    ...extras.filter(item => !seen.has(item.id)).map(item => ({
      eventId: item.id,
      sessionId: item.sessionId,
      kind: "ui" as const,
      at: item.at
    }))
  ];
}

function joinUiEvents(capability: CapabilityContract, events: EvidenceEvent[]) {
  const related = relatedEvents(capability, events);
  const uiEvents = events.filter((event): event is UiEvidence => event.kind === "ui");
  const uiById = new Map(uiEvents.map(event => [event.id, event]));
  const richest = richestNetwork(related);
  const sample = richest ? requestInput(richest) : undefined;
  const nearby = richest ? relatedUiEvents(richest, uiById, sample) : [];
  const owner = richest ? owningFormEvent(richest, uiEvents, sample) : undefined;
  const names = sample ? requestNamesOf(sample) : new Set<string>();
  const slots = new Set([...names].map(rangeIndexOf).filter((item): item is number => item !== undefined));
  const at = richest ? Date.parse(richest.at) : Number.POSITIVE_INFINITY;
  const ownerPage = evidencePage(
    owner?.pageUrl
    || (richest?.correlatedUiEvidenceId ? uiById.get(richest.correlatedUiEvidenceId)?.pageUrl : undefined)
    || richest?.pageUrl
    || nearby.find(item => item.pageUrl)?.pageUrl
    || related.find((event): event is UiEvidence => event.kind === "ui" && Boolean(event.pageUrl))?.pageUrl
  );
  const ownerLabels = new Set((owner?.form || []).map(field => field.label).filter((item): item is string => Boolean(item)));
  const sameOwnerForm = (event: UiEvidence) => {
    if (!event.form?.length || !owner?.form?.length) return false;
    if (sameFormShape(owner, event)) return true;
    const eventLabels = new Set(event.form.map(field => field.label).filter((item): item is string => Boolean(item)));
    return ownerLabels.size > 0 && [...ownerLabels].every(label => eventLabels.has(label));
  };
  const ownerFamily = uiEvents.filter(event => {
    if (!richest || event.sessionId !== richest.sessionId || Date.parse(event.at) > at + 500) return false;
    const page = evidencePage(event.pageUrl);
    if (ownerPage && page && page !== ownerPage) return false;
    return sameOwnerForm(event);
  });
  const ownerFamilyLabels = new Set([
    ...ownerLabels,
    ...ownerFamily.flatMap(event => (event.form || []).map(field => field.label).filter((item): item is string => Boolean(item)))
  ]);
  const ownerFamilyNames = new Set([
    ...(owner?.form || []).map(field => field.name).filter((item): item is string => Boolean(item)),
    ...ownerFamily.flatMap(event => (event.form || []).map(field => field.name).filter((item): item is string => Boolean(item)))
  ]);
  const formMatchesRequest = (form: NonNullable<UiEvidence["form"]> | undefined) => {
    const named = (form || []).map(field => field.name).filter((item): item is string => Boolean(item && !/^(el-id-|el-[a-z]+-\d+)/i.test(item)));
    if (!named.length) return false;
    const overlap = named.filter(item => names.has(item) || [...names].some(requestName => uiNameMatches(item, requestName)));
    const extra = named.filter(item => !names.has(item) && ![...names].some(requestName => uiNameMatches(item, requestName)));
    return overlap.length > 0 && overlap.length > extra.length;
  };
  const belongsToOwner = (event: UiEvidence) => {
    if (!richest || event.sessionId !== richest.sessionId) return false;
    if (Date.parse(event.at) > at + 500) return false;
    const page = evidencePage(event.pageUrl);
    if (ownerPage && page && page !== ownerPage) return false;
    if (event.id === richest.correlatedUiEvidenceId) return true;
    if (event.form?.length && owner?.form?.length && sameOwnerForm(event)) return true;
    if (!owner?.form?.length) return true;
    return Boolean(
      event.label && ownerFamilyLabels.has(event.label)
      || event.name && ownerFamilyNames.has(event.name)
    );
  };
  const base = mergeUi([
    ...related.filter((event): event is UiEvidence => event.kind === "ui" && belongsToOwner(event)),
    ...nearby.filter(belongsToOwner),
    ...events.filter((event): event is UiEvidence => {
      if (event.kind !== "ui" || !richest || event.sessionId !== richest.sessionId) return false;
      if (Date.parse(event.at) > at + 500) return false;
      if (ownerPage && evidencePage(event.pageUrl) !== ownerPage) return false;
      if (!belongsToOwner(event)) return false;
      if (formMatchesRequest(event.form)) return true;
      return Boolean(slots.size && (event.form || []).some(field => field.rangeIndex !== undefined && slots.has(field.rangeIndex)));
    })
  ]);
  const requestLabels = new Set(
    base.flatMap(event => (event.form || [])
      .filter(field => Boolean(field.name && names.has(field.name) && field.label))
      .map(field => String(field.label)))
  );
  const extras = events.filter((event): event is UiEvidence => {
    if (event.kind !== "ui" || !richest || event.sessionId !== richest.sessionId) return false;
    if (Date.parse(event.at) > at + 500) return false;
    if (ownerPage && evidencePage(event.pageUrl) !== ownerPage) return false;
    if (!belongsToOwner(event)) return false;
    return Boolean(event.label && requestLabels.has(event.label));
  });
  return mergeUi([...base, ...extras]);
}

function compactObservations(events: EvidenceEvent[]) {
  return events.filter((event): event is UiEvidence => event.kind === "ui").slice(0, 80).map(event => ({
    name: event.name,
    label: event.label || event.text,
    value: event.value,
    type: event.inputType || event.role,
    options: (event.options || []).slice(0, 20)
  }));
}

function compactCapability(capability: CapabilityContract, events: EvidenceEvent[]) {
  const related = relatedEvents(capability, events);
  const network = related.find((event): event is NetworkEvidence => event.kind === "network");
  const sample = network ? requestInput(network) : undefined;
  return {
    id: capability.id,
    method: capability.transport.method,
    pathTemplate: capability.transport.pathTemplate,
    operation: capability.operation,
    title: capability.title,
    requestKeys: capability.inputForm.map(field => ({
      path: field.path,
      name: field.name,
      label: field.label,
      value: requestValueAt(sample, field.path),
      valueType: field.valueType,
      currentSource: field.source
    })),
    ui: compactObservations(related),
    peerIds: [] as string[]
  };
}

function literalRule(value: unknown) {
  if (value === undefined) return undefined;
  try {
    return `literal:${JSON.stringify(value)}`;
  } catch {
    return undefined;
  }
}

function keptJudgedRule(field: InputFormField) {
  return Boolean(field.defaultRule && KEPT_RULE.test(field.defaultRule));
}

function isSkillDistinctive(value: unknown) {
  if (value === undefined || value === null || value === "") return false;
  if (value === 0 || value === 1 || value === true || value === false) return false;
  if (value === "0" || value === "1") return false;
  return true;
}

function displayLabel(label?: string, fallback?: string) {
  const raw = String(label || "").trim();
  if (!raw) return fallback || "";
  const stripped = raw.replace(/^\*+/, "").replace(/^(请输入|请选择|请填写)/, "").trim();
  return stripped || fallback || raw;
}

function widgetFromObservation(field: InputFormField, observation?: UiObservation): InputFormField["widget"] {
  const type = String(observation?.type || "").toLowerCase();
  if (type === "date" || type === "datetime" || type === "daterange") return "date";
  if (type === "number") return "number";
  if (type === "checkbox" || type === "boolean") return "boolean";
  if (type === "textarea") return "textarea";
  if (type === "select-multiple" || type === "multiselect") return "multiselect";
  if (type === "select" || type === "select-one" || type === "combobox" || type === "picker") {
    return field.valueType === "array" ? "multiselect" : "select";
  }
  if (/^(text|input|search|email|tel|url|password)$/.test(type)) return "text";
  return field.widget;
}

function observationReadonly(observation?: UiObservation) {
  return observation?.disabled === true || /readonly|disabled/i.test(String(observation?.type || ""));
}

function optionsWithRecordedValue(observation: UiObservation | undefined, value: unknown) {
  const options = observation?.options?.length
    ? observation.options.map(item => ({ value: item.value, label: String(item.label || item.value) }))
    : undefined;
  if (!options?.length || value === undefined) return options;
  const selected = observation?.value;
  if (selected === undefined || selected === "") return options;
  return options.map(item => (
    sameValue(item.value, selected) || sameValue(item.label, selected)
      ? { ...item, value }
      : item
  ));
}

function richerNamedObservation(named: UiObservation, observations: UiObservation[]) {
  if ((named.value !== undefined && named.value !== "") || named.options?.length) return named;
  const richer = observations.find(item =>
    Boolean(item.label && item.label === named.label)
    && ((item.value !== undefined && item.value !== "") || Boolean(item.options?.length))
  );
  return richer ? { ...richer, name: named.name || richer.name } : named;
}

function asExactCaller(field: InputFormField, observation: UiObservation | undefined, value: unknown): InputFormField {
  if (observationReadonly(observation)) {
    return asExactSystem(field, value, "页面只读展示，由选择其它字段后自动带出。调用方不要手填");
  }
  const picker = /picker/i.test(observation?.type || field.widget || "");
  const options = !picker ? optionsWithRecordedValue(observation, value) : undefined;
  const widget = widgetFromObservation(field, observation);
  const clock = recordedClock(value);
  const dateHasTime = widget === "date" && (
    /datetime|time/.test(String(observation?.type || "").toLowerCase())
    || (typeof observation?.value === "string" && /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}/.test(observation.value.trim()))
  );
  const htmlRequest = richTextPlain(value);
  const requestFormat = htmlRequest !== undefined && sameValue(htmlRequest, observation?.value) ? "html" as const : undefined;
  return {
    ...field,
    label: displayLabel(observation?.label, field.label) || field.label,
    source: "caller",
    required: observation?.required ?? field.required,
    requiredBasis: observation?.required === true ? "ui-required" : observation?.required === false ? "not-observed" : field.requiredBasis,
    systemHandled: false,
    widget,
    defaultRule: keptJudgedRule(field) ? field.defaultRule : undefined,
    candidates: options?.length ? { type: "static", values: options } : field.candidates,
    dateFormat: widget === "date"
      ? recordedDateFormat(observation?.value, dateHasTime) || recordedDateFormat(value) || field.dateFormat
      : undefined,
    dateClock: widget === "date" ? (dateHasTime ? undefined : clock || field.dateClock) : field.dateClock,
    requestFormat: requestFormat || field.requestFormat,
    sourceDetail: requestFormat
      ? "页面由调用方输入文本，系统按录制到的请求格式编码为 HTML"
      : options?.length
      ? "页面同名控件有固定选项，由调用方选择"
      : picker
        ? "页面同名选择控件，由调用方提供"
        : "页面同名字段有输入，由调用方提供"
  };
}

function asExactSystem(field: InputFormField, value: unknown, detail: string): InputFormField {
  return {
    ...field,
    source: "system",
    systemHandled: true,
    required: false,
    defaultRule: field.defaultRule || literalRule(value),
    sourceDetail: detail
  };
}

export function fallbackRole(capability: CapabilityContract, catalog: CapabilityContract[]): NonNullable<CapabilityContract["role"]> {
  if (capability.role) return capability.role;
  if (capability.operation === "authenticate") return "noise";
  if (WRITE.has(capability.operation) || capability.operation === "download") return "primary";
  const path = capability.transport.pathTemplate || "";
  if (isLookupQueryPath(path) || directoryLookupEntity(path)) return "lookup";
  if (/\/page$/i.test(path)) return "primary";
  const hasCaller = capability.inputForm.some(field => field.source === "caller" && !PAGE_NAME.test(field.name));
  if (capability.operation === "unknown") return hasCaller ? "primary" : "lookup";
  if (capability.operation !== "query") return "lookup";
  const writes = catalog.filter(item => WRITE.has(item.operation));
  if (writes.length && !hasCaller) return "lookup";
  if (hasCaller) return "primary";
  const queries = catalog.filter(item => item.operation === "query");
  return queries.length === 1 ? "primary" : "lookup";
}

export function applyExactEvidenceJoin(capability: CapabilityContract, events: EvidenceEvent[]): CapabilityContract {
  const related = relatedEvents(capability, events);
  const sample = richestRequestSample(related);
  const observations = collectUiObservations(joinUiEvents(capability, events));
  const values = capability.inputForm.map(field => ({
    field,
    value: requestValueAt(sample, field.path)
  }));

  const uniqueDayOwner = new Map<string, string>();
  for (const item of values) {
    const day = dateDay(item.value);
    if (!day) continue;
    uniqueDayOwner.set(day, uniqueDayOwner.has(day) ? "" : item.field.path);
  }
  const uniqueValueOwner = new Map<string, string>();
  const uniqueValueHits = new Map<string, UiObservation[]>();
  for (const item of values) {
    if (!isSkillDistinctive(item.value)) continue;
    const key = JSON.stringify(item.value);
    const hits = [...new Map(
      observations
        .filter(observation =>
          observation.value !== undefined
          && observation.value !== ""
          && sameValue(observation.value, item.value)
        )
        .map(observation => [observation.label || observation.name || "", observation])
    ).values()].filter(observation => observation.label || observation.name);
    uniqueValueHits.set(item.field.path, hits);
    if (hits.length !== 1) continue;
    uniqueValueOwner.set(key, uniqueValueOwner.has(key) ? "" : item.field.path);
  }

  const inputForm = values.map(({ field, value }) => {
    if (keptJudgedRule(field)) return field;
    if (PAGE_NAME.test(field.name)) {
      return {
        ...field,
        source: "system" as const,
        systemHandled: true,
        required: false,
        defaultRule: field.defaultRule || literalRule(value) || "literal:1",
        sourceDetail: "列表分页由执行器按默认值补齐"
      };
    }
    let named = observations.find(item =>
      item.name === field.name
      && item.value !== undefined
      && item.value !== ""
    ) || observations.find(item => item.name === field.name)
      || observations.find(item =>
        uiNameMatches(item.name, field.name)
        && item.value !== undefined
        && item.value !== ""
      ) || observations.find(item => uiNameMatches(item.name, field.name));
    if (named) named = richerNamedObservation(named, observations);
    if (named && observationReadonly(named)) {
      return {
        ...field,
        label: displayLabel(named.label, field.label) || field.label,
        source: "system" as const,
        systemHandled: true,
        required: false,
        defaultRule: field.defaultRule || literalRule(value),
        sourceDetail: "页面只读展示，由选择其它字段后自动带出。调用方不要手填"
      };
    }
    if (named) return asExactCaller(field, named, value);
    const slot = rangeIndexOf(field.name);
    const day = dateDay(value);
    if (slot !== undefined) {
      const ranged = observations.filter(item => item.rangeIndex === slot);
      if (ranged.length === 1) return asExactCaller(field, ranged[0], value);
      const dateSlots = [...new Map(
        observations
          .filter(item => item.rangeIndex !== undefined || dateDay(item.value) || /date|time/i.test(item.type || ""))
          .map(item => [item.label || item.name || String(item.rangeIndex), item])
      ).values()].sort((left, right) => (left.rangeIndex ?? 99) - (right.rangeIndex ?? 99));
      const indexed = dateSlots.find(item => item.rangeIndex === slot)
        || (dateSlots.length > 1 ? dateSlots[slot] : undefined);
      if (indexed) return asExactCaller(field, indexed, value);
    }
    const observed = findObservation(field, value, observations, [], sample);
    if (observed) return asExactCaller(field, observed, value);
    const sharedBusinessValue = values.some(item =>
      item.field.path !== field.path
      && !PAGE_NAME.test(item.field.name)
      && sameValue(item.value, value)
    );
    const sameNamedCollectionLeaf = values.some(item =>
      item.field.path !== field.path
      && item.field.name === field.name
      && item.field.path.includes("[*]") !== field.path.includes("[*]")
    );
    const semanticMatches = sharedBusinessValue && !sameNamedCollectionLeaf && semanticConcepts(field).size < 2
      ? []
      : [...new Map(observations
      .filter(item => sameSynonymGroup(field, item))
      .filter(item =>
        sameValue(item.value, value)
        || Boolean(item.options?.some(option => sameValue(option.value, value) || sameValue(option.label, value)))
        || Boolean(dateDay(item.value) && dateDay(item.value) === dateDay(value))
      )
      .map(item => [item.label || item.name || "", item])).values()]
      .filter(item => item.label || item.name);
    const exactSemantic = semanticMatches.filter(item => semanticLabelScore(field, item) > 0);
    const semantic = exactSemantic.length ? exactSemantic : semanticMatches;
    if (semantic.length === 1) return asExactCaller(field, semantic[0], value);
    if (day && uniqueDayOwner.get(day) === field.path) {
      const dayHits = [...new Map(
        observations
          .filter(item => dateDay(item.value) === day)
          .map(item => [item.label || item.name || day, item])
      ).values()];
      if (dayHits.length === 1) return asExactCaller(field, dayHits[0], value);
    }
    if ((ASK_KEY.test(field.name) || SEARCH_KEY.test(field.name)) && value !== undefined && value !== null && value !== "") {
      const ask = observations.find(item => sameValue(item.value, value)) || named;
      return asExactCaller(field, ask, value);
    }
    if (field.source === "caller" && field.label !== field.name) return field;
    if (isSkillDistinctive(value)) {
      const hits = uniqueValueHits.get(field.path) || [];
      const owner = uniqueValueOwner.get(JSON.stringify(value));
      if (hits.length === 1 && owner === field.path) {
        return observationReadonly(hits[0])
          ? asExactSystem(field, value, "页面只读展示，由选择其它字段后自动带出。调用方不要手填")
          : asExactCaller(field, hits[0], value);
      }
    }
    if (field.source === "caller") return field;
    return asExactSystem(field, value, "请求中有该键，页面无同名输入；系统按录制成功请求原值补齐（系统默认）");
  });

  return { ...capability, inputForm, evidence: mergeEvidence(capability, joinUiEvents(capability, events)) };
}

function asObjectRows(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item));
}

function responseLists(body: unknown): Array<{ path: string; rows: Record<string, unknown>[] }> {
  if (!body || typeof body !== "object") return [];
  const record = body as Record<string, unknown>;
  const nested = record.data && typeof record.data === "object" && !Array.isArray(record.data)
    ? record.data as Record<string, unknown>
    : undefined;
  const candidates = [
    { path: "$.data.list", rows: asObjectRows(nested?.list) },
    { path: "$.data.rows", rows: asObjectRows(nested?.rows) },
    { path: "$.data.records", rows: asObjectRows(nested?.records) },
    { path: "$.data", rows: asObjectRows(record.data) },
    { path: "$.list", rows: asObjectRows(record.list) },
    { path: "$.rows", rows: asObjectRows(record.rows) },
    { path: "$.records", rows: asObjectRows(record.records) },
    { path: "$.result", rows: asObjectRows(record.result) }
  ];
  return candidates.filter(item => item.rows.length >= 1);
}

function rowIdentity(row: Record<string, unknown>) {
  for (const key of ["id", "value", "code", "key", "dictValue", "dictCode"]) {
    if (row[key] !== undefined && row[key] !== null && row[key] !== "") return row[key];
  }
  return undefined;
}

function rowLabelPath(rows: Record<string, unknown>[]) {
  for (const key of ["name", "label", "title", "dictLabel", "nickname", "username", "userName", "text"]) {
    if (rows.some(row => row[key] !== undefined && row[key] !== null && row[key] !== "")) {
      return key === "username" ? "username" : key;
    }
  }
  return undefined;
}

function looksExactPicker(field: InputFormField) {
  return field.widget === "select" || field.widget === "multiselect" || /picker/i.test(field.widget);
}

function usableExactCandidateSource(capability: CapabilityContract) {
  if (capability.operation !== "query" || isNoiseCapability(capability)) return false;
  const path = capability.transport.pathTemplate || "";
  if (directoryLookupEntity(path) || isLookupQueryPath(path)) return true;
  return !isPageResultQuery(capability);
}

function identityValues(value: unknown): unknown[] {
  if (value === undefined || value === null || value === "") return [];
  if (Array.isArray(value)) return value.filter(item => item !== undefined && item !== null && item !== "");
  return [value];
}

function rowDisplays(row: Record<string, unknown>) {
  const values = ["name", "label", "title", "dictLabel", "nickname", "username", "userName", "text"]
    .map(key => row[key])
    .filter(item => item !== undefined && item !== null && item !== "")
    .map(item => String(item));
  const username = row.username ?? row.userName;
  const nickname = row.nickname;
  if (username && nickname) {
    values.push(`${username} ${nickname}`, `${nickname} ${username}`);
  }
  return values;
}

function observationMatchesRow(observation: UiObservation, row: Record<string, unknown>) {
  const display = observation.value === undefined || observation.value === "" ? "" : String(observation.value).trim();
  if (!display) return false;
  return rowDisplays(row).some(item => item === display || sameValue(item, display));
}

function rangeIndexOf(name: string) {
  const match = /\[(\d+)\]$/.exec(name);
  return match ? Number(match[1]) : undefined;
}

export function exactCandidateSources(catalog: CapabilityContract[], events: EvidenceEvent[]) {
  return catalog.flatMap(capability => {
    if (!usableExactCandidateSource(capability)) return [];
    const related = relatedEvents(capability, events);
    const lists = related
      .filter((event): event is NetworkEvidence => event.kind === "network")
      .map(event => responseLists(event.response?.body))
      .find(items => items.length > 0);
    if (!lists) return [];
    return lists.flatMap(list => {
      const identityKey = ["id", "value", "code", "key", "dictValue", "dictCode"]
        .find(key => list.rows.some(row => row[key] !== undefined && row[key] !== null && row[key] !== ""));
      const labelKey = rowLabelPath(list.rows);
      if (!identityKey || !labelKey) return [];
      const dictionary = list.rows.every(row => typeof row.dictType === "string" && row.dictType);
      const grouped = new Map<string, Record<string, unknown>[]>();
      if (dictionary) {
        for (const row of list.rows) {
          const key = String(row.dictType);
          grouped.set(key, [...(grouped.get(key) || []), row]);
        }
      }
      const groups: [string | undefined, Record<string, unknown>[]][] = dictionary
        ? [...grouped.entries()]
        : [[undefined, list.rows]];
      return groups.map(([dictionaryType, rows]) => ({
        capabilityId: capability.id,
        pathTemplate: capability.transport.pathTemplate,
        valuePath: `${list.path}[*].${identityKey}`,
        labelPath: `${list.path}[*].${labelKey}`,
        rows,
        dictionaryType
      }));
    });
  });
}

export function matchExactCandidateSource(
  field: InputFormField,
  value: unknown,
  sources: ReturnType<typeof exactCandidateSources>,
  selfId?: string,
  observation?: UiObservation
) {
  if (field.source !== "caller") return undefined;
  if (field.candidates?.type === "capability") return undefined;
  if (!looksExactPicker(field) && field.candidates?.type !== "static") return undefined;
  const ids = identityValues(value);
  if (ids.length === 1) {
    let hits = sources.filter(source =>
      source.capabilityId !== selfId
      && source.rows.filter(row => sameValue(rowIdentity(row), ids[0])).length === 1
    );
    const entity = pickerEntity(field);
    if (entity) {
      const entityHits = hits.filter(source => directoryLookupEntity(source.pathTemplate) === entity);
      const simple = entityHits.filter(source => /simple-list$/i.test(source.pathTemplate));
      if (simple.length === 1) hits = simple;
      else if (entityHits.length) hits = entityHits;
    }
    if (observation) {
      const displayHits = hits.filter(source => source.rows.some(row =>
        sameValue(rowIdentity(row), ids[0]) && observationMatchesRow(observation, row)
      ));
      if (displayHits.length === 1) return displayHits[0];
    }
    if (hits.length === 1) return hits[0];
  }
  if (field.candidates?.type !== "static" || field.candidates.values.length < 2) return undefined;
  const values = field.candidates.values.map(item => item.value);
  const enumHits = sources.filter(source =>
    source.capabilityId !== selfId
    && values.every(item => source.rows.filter(row => sameValue(rowIdentity(row), item)).length === 1)
  );
  return enumHits.length === 1 ? enumHits[0] : undefined;
}

function sourceIsClosedEnum(source: ReturnType<typeof exactCandidateSources>[number]) {
  return Boolean(source.dictionaryType) || source.rows.length >= 2
    && source.rows.every(row => row.value !== undefined && row.value !== null && row.value !== "");
}

function rowKeysForObservation(
  observation: UiObservation,
  sources: ReturnType<typeof exactCandidateSources>
) {
  return sources.flatMap(source =>
    source.rows
      .filter(row => observationMatchesRow(observation, row))
      .map(row => JSON.stringify(rowIdentity(row)))
  );
}

function chooserObservations(
  capability: CapabilityContract,
  catalog: CapabilityContract[],
  events: EvidenceEvent[],
  sources: ReturnType<typeof exactCandidateSources>
) {
  const related = relatedEvents(capability, events);
  const richest = richestNetwork(related);
  const extraIds = new Set<string>();
  const sourceIds = new Set(sources.map(item => item.capabilityId));
  for (const event of events) {
    if (event.kind !== "network" || !event.correlatedUiEvidenceId) continue;
    const sourceCap = catalog.find(item => item.evidence.some(ref => ref.eventId === event.id));
    if (sourceCap && sourceIds.has(sourceCap.id)) extraIds.add(event.correlatedUiEvidenceId);
  }
  const local = joinUiEvents(capability, events);
  const localObs = collectUiObservations(local);
  const claimed = new Set(localObs.flatMap(item => rowKeysForObservation(item, sources)));
  const at = richest ? Date.parse(richest.at) : Number.POSITIVE_INFINITY;
  const extras = events.filter((event): event is UiEvidence => {
    if (event.kind !== "ui") return false;
    if (extraIds.has(event.id)) return true;
    if (!richest || event.sessionId !== richest.sessionId || Date.parse(event.at) > at + 500) return false;
    return collectUiObservations([event]).some(item => rowKeysForObservation(item, sources).length > 0);
  });
  const extraObs = collectUiObservations(extras).filter(item => {
    const keys = rowKeysForObservation(item, sources);
    return keys.length > 0 && keys.every(key => !claimed.has(key));
  });
  return [...localObs, ...extraObs];
}

export function applyExactChooserJoin(catalog: CapabilityContract[], events: EvidenceEvent[]): CapabilityContract[] {
  const sources = exactCandidateSources(catalog, events);
  return catalog.map(capability => {
    const related = relatedEvents(capability, events);
    const sample = richestRequestSample(related);
    const observations = chooserObservations(capability, catalog, events, sources);
    const localObservations = collectUiObservations(joinUiEvents(capability, events));
    return {
      ...capability,
      inputForm: capability.inputForm.map(field => {
        if (keptJudgedRule(field)) return field;
        if (field.source === "caller" && field.label !== field.name) return field;
        const ids = identityValues(requestValueAt(sample, field.path));
        if (ids.length !== 1) return field;
        let matchedSources = sources.filter(source =>
          source.capabilityId !== capability.id
          && source.rows.filter(row => sameValue(rowIdentity(row), ids[0])).length === 1
        );
        if (!isSkillDistinctive(ids[0])) {
          matchedSources = matchedSources.filter(sourceIsClosedEnum);
        }
        const hits = (isSkillDistinctive(ids[0]) ? observations : localObservations).filter(observation =>
          matchedSources.some(source =>
            source.rows.filter(row =>
              sameValue(rowIdentity(row), ids[0]) && observationMatchesRow(observation, row)
            ).length === 1
          )
        );
        const unique = [...new Map(hits.map(item => [item.label || item.name || "", item])).values()]
          .filter(item => item.label || item.name);
        if (unique.length !== 1) return field;
        const matched = matchedSources.find(source =>
          source.rows.some(row => sameValue(rowIdentity(row), ids[0]) && observationMatchesRow(unique[0]!, row))
        );
        const next = asExactCaller(field, unique[0], ids[0]);
        return matched ? {
          ...next,
          widget: next.valueType === "array" ? "multiselect" : "select",
          candidates: {
            type: "capability",
            capabilityId: matched.capabilityId,
            valuePath: matched.valuePath,
            labelPath: matched.labelPath
          },
          sourceDetail: `录制查询 ${matched.capabilityId} 的列表唯一对应了该字段的值，由调用方从该查询选择`
        } : next;
      })
    };
  });
}

export function applyExactCandidateJoin(catalog: CapabilityContract[], events: EvidenceEvent[]): CapabilityContract[] {
  const sources = exactCandidateSources(catalog, events);
  return catalog.map(capability => {
    const related = relatedEvents(capability, events);
    const sample = richestRequestSample(related);
    const observations = collectUiObservations(joinUiEvents(capability, events));
    return {
      ...capability,
      inputForm: capability.inputForm.map(field => {
        const observation = observations.find(item =>
          uiNameMatches(item.name, field.name)
          && item.value !== undefined
          && item.value !== ""
        );
        const hit = matchExactCandidateSource(field, requestValueAt(sample, field.path), sources, capability.id, observation);
        if (!hit) return field;
        return {
          ...field,
          widget: field.valueType === "array" ? "multiselect" : "select",
          candidates: {
            type: "capability",
            capabilityId: hit.capabilityId,
            valuePath: hit.valuePath,
            labelPath: hit.labelPath
          },
          sourceDetail: `录制查询 ${hit.capabilityId} 的列表唯一对应了该字段的值，由调用方从该查询选择`
        };
      })
    };
  });
}

export function applySameResourceCandidates(catalog: CapabilityContract[]): CapabilityContract[] {
  return catalog.map(capability => ({
    ...capability,
    inputForm: capability.inputForm.map(field => {
      if (field.candidates) return field;
      const matches = catalog.flatMap(source => {
        if (source.id === capability.id
          || source.transport.origin !== capability.transport.origin
          || !sameResource(source.transport.pathTemplate, capability.transport.pathTemplate)) return [];
        return source.inputForm.filter(item => item.name === field.name && Boolean(item.candidates));
      });
      const unique = [...new Map(matches.map(item => [JSON.stringify(item.candidates), item])).values()];
      if (unique.length !== 1) return field;
      return {
        ...field,
        widget: field.valueType === "array" ? "multiselect" as const : "select" as const,
        candidates: unique[0]!.candidates
      };
    })
  }));
}

function normalizeCandidateWidgets(catalog: CapabilityContract[]) {
  return catalog.map(capability => ({
    ...capability,
    inputForm: capability.inputForm.map(field => field.candidates
      ? { ...field, widget: field.valueType === "array" ? "multiselect" as const : "select" as const }
      : field)
  }));
}

export function applyDeterministicCatalogJudgment(
  capabilities: CapabilityContract[],
  events: EvidenceEvent[]
): CapabilityContract[] {
  const joined = applyExactChooserJoin(
    capabilities.map(item => applyExactEvidenceJoin(item, events)),
    events
  );
  const withRoles = joined.map(item => ({ ...item, role: item.role || fallbackRole(item, joined) }));
  return normalizeCandidateWidgets(applySameResourceCandidates(applyExactCandidateJoin(withRoles, events)));
}

function applyFieldPatch(
  field: InputFormField,
  patch: z.infer<typeof FieldPatch>,
  catalogIds: Set<string>,
  ownerCapabilityId: string
): InputFormField {
  const fromCapabilityId = /^from:([^:]+):/.exec(patch.defaultRule || "")?.[1];
  if (fromCapabilityId === ownerCapabilityId || patch.candidateCapabilityId === ownerCapabilityId) return field;
  const source = (patch.source || field.source) as FieldSource;
  let next: InputFormField = {
    ...field,
    label: patch.label || field.label,
    source,
    widget: patch.widget || field.widget,
    systemHandled: source !== "caller",
    defaultRule: patch.defaultRule ?? field.defaultRule,
    sourceDetail: patch.sourceDetail || field.sourceDetail
  };
  if (next.defaultRule && !isExecutableRule(next.defaultRule) && !next.defaultRule.startsWith("literal:")) {
    next = { ...next, defaultRule: field.defaultRule };
  }
  if (patch.candidateCapabilityId && catalogIds.has(patch.candidateCapabilityId) && patch.candidateValuePath && patch.candidateLabelPath) {
    next = {
      ...next,
      widget: next.valueType === "array" ? "multiselect" : "select",
      candidates: {
        type: "capability",
        capabilityId: patch.candidateCapabilityId,
        valuePath: patch.candidateValuePath,
        labelPath: patch.candidateLabelPath
      }
    };
  } else if (patch.staticCandidates?.length) {
    next = { ...next, widget: next.widget === "text" ? "select" : next.widget, candidates: { type: "static", values: patch.staticCandidates } };
  }
  return next;
}

function applyJudgment(
  capabilities: CapabilityContract[],
  judgment: z.infer<typeof CatalogJudgment>,
  model: string
): CapabilityContract[] {
  const byId = new Map(judgment.capabilities.map(item => [item.id, item]));
  const catalogIds = new Set(capabilities.map(item => item.id));
  return capabilities.map(capability => {
    const patch = byId.get(capability.id);
    if (!patch) return { ...capability, role: capability.role || fallbackRole(capability, capabilities) };
    const inputForm = capability.inputForm.map(field => {
      const fieldPatch = patch.fields.find(item => item.path === field.path);
      return fieldPatch ? applyFieldPatch(field, fieldPatch, catalogIds, capability.id) : field;
    });
    const bindings = [
      ...capability.bindings,
      ...inputForm.flatMap(field => {
        const parsed = /^from:([^:]+):([^|]+)/.exec(field.defaultRule || "");
        if (!parsed || parsed[1] === capability.id || !catalogIds.has(parsed[1]!)) return [];
        const key = `${parsed[1]}|${parsed[2]}|${field.path}`;
        if (capability.bindings.some(item => `${item.fromCapabilityId}|${item.fromPath}|${item.toPath}` === key)) return [];
        return [{
          id: id("bind"),
          fromCapabilityId: parsed[1]!,
          fromPath: parsed[2]!,
          toPath: field.path,
          confidence: 1,
          evidenceIds: capability.evidence.map(item => item.eventId).slice(0, 4),
          approved: true,
          approvalSource: "evidence" as const,
          approvedAt: new Date().toISOString(),
          note: field.sourceDetail
        }];
      })
    ];
    return {
      ...capability,
      title: patch.title || capability.title,
      description: patch.description || capability.description,
      operation: patch.operation,
      role: patch.role,
      sideEffect: WRITE.has(patch.operation),
      confirmation: {
        required: WRITE.has(patch.operation),
        reason: WRITE.has(patch.operation) ? "该操作会改变业务或文件数据" : undefined
      },
      inputForm,
      bindings,
      confidence: Math.max(capability.confidence, 0.8),
      generated: {
        source: "pi-skill",
        model,
        generatedAt: new Date().toISOString()
      }
    };
  });
}

export async function applyPiCatalogJudgment(
  capabilities: CapabilityContract[],
  events: EvidenceEvent[],
  reasoner: OpenAIReasoner,
  rootDir: string,
  useModel = true
): Promise<CapabilityContract[]> {
  const judged = applyDeterministicCatalogJudgment(capabilities, events);
  if (!useModel || !reasoner.available() || !judged.length) return judged;

  const [fieldSkill, primarySkill] = await Promise.all([
    loadPiSkill(rootDir, "infer-field-contract"),
    loadPiSkill(rootDir, "judge-primary-capability")
  ]);
  const payload = {
    capabilities: judged.map(item => compactCapability(item, events))
  };

  const parsed = await reasoner.parseStructured(
    [fieldSkill, primarySkill, "Return one judgment object for every input capability id.", "Do not invent field paths or capability ids."].join("\n\n"),
    JSON.stringify(payload).slice(0, 120_000),
    CatalogJudgment,
    "catalog_judgment"
  );
  if (!parsed) return judged;
  return normalizeCandidateWidgets(applyJudgment(judged, parsed, reasoner.model));
}
