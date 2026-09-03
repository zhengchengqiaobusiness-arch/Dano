import type { InputFormField, NetworkEvidence, UiEvidence } from "../domain.js";

const GENERATED_NAME = /^(el-id-\d+|el-[a-z]+-\d+|input-\d+|select-\d+|aria-id|:r[0-9a-z]+$)/i;
const PAGE_NAME = /^(pageNo|pageSize|pageNum|page|size|current|offset|limit)$/i;

export function isGeneratedFieldName(name?: string) {
  return Boolean(name && GENERATED_NAME.test(name));
}

export function isPaginationField(name?: string) {
  return Boolean(name && PAGE_NAME.test(name));
}

export function uiNameMatches(uiName: string | undefined, requestName: string) {
  if (!uiName || isGeneratedFieldName(uiName)) return false;
  if (uiName === requestName) return true;
  return requestName.startsWith(`${uiName}[`);
}

export function realFieldName(name?: string) {
  return name && !isGeneratedFieldName(name) ? name : undefined;
}

export interface UiObservation {
  name?: string;
  label?: string;
  value?: unknown;
  type?: string;
  required?: boolean;
  options?: Array<{ value: unknown; label: string }>;
  rangeIndex?: number;
}

export interface RecordedList {
  rows: Array<Record<string, unknown>>;
}

function optionsOf(event: UiEvidence, field?: NonNullable<UiEvidence["form"]>[number]) {
  const raw = field
    ? field.options
    : event.options?.length
      ? event.options
      : event.visibleOptions?.map(label => ({ value: label, label }));
  return raw?.filter(item => String(item.label || item.value || "").trim()).slice(0, 200);
}

function emptyPromptLabel(text?: string) {
  const stripped = String(text || "").replace(/^(请选择|请输入|请填写|please select|please enter|please choose|select)\s*/i, "");
  return stripped && stripped !== text ? stripped : undefined;
}

function eventLabel(event: UiEvidence) {
  return event.label || emptyPromptLabel(event.text);
}

export function flattenRequestValues(value: unknown, prefix = "$"): Array<{ path: string; name: string; value: unknown }> {
  if (value === null || value === undefined) return [];
  if (Array.isArray(value)) {
    const first = value.find(item => item !== null && item !== undefined);
    return flattenRequestValues(first, `${prefix}[*]`);
  }
  if (typeof value !== "object") {
    const name = prefix.split(".").pop()?.replace(/\[\*\]$/, "") || prefix;
    return [{ path: prefix, name, value }];
  }
  return Object.entries(value as Record<string, unknown>).flatMap(([key, child]) =>
    flattenRequestValues(child, `${prefix}.${key}`)
  );
}

function padDatePart(value: number) {
  return String(value).padStart(2, "0");
}

export function sameValue(left: unknown, right: unknown) {
  if (left === undefined || left === null || right === undefined || right === null || right === "") return false;
  if (Object.is(left, right)) return true;
  if (typeof left === "boolean" || typeof right === "boolean") return Boolean(left) === Boolean(right);
  const leftDay = dateDay(left);
  const rightDay = dateDay(right);
  if (leftDay && rightDay && leftDay === rightDay) {
    const leftClock = recordedClock(left) || clockFromEpoch(left);
    const rightClock = recordedClock(right) || clockFromEpoch(right);
    if (leftClock && rightClock) return leftClock === rightClock;
  }
  if (typeof left === "number" || typeof right === "number") return Number(left) === Number(right);
  return String(left) === String(right);
}

function isDistinctiveValue(value: unknown) {
  if (value === undefined || value === null || value === "") return false;
  if (typeof value === "boolean") return true;
  if (typeof value === "number") return false;
  const text = String(value).trim();
  if (/^-?\d+(\.\d+)?$/.test(text)) return false;
  return text.length > 0;
}

function looksDateControl(item: Pick<UiObservation, "type" | "label" | "name"> | Pick<InputFormField, "name" | "label" | "widget">) {
  const widget = "widget" in item ? item.widget : undefined;
  const type = "type" in item ? item.type : undefined;
  return /date|time|picker|时间|日期/i.test(`${type || ""} ${widget || ""} ${item.name || ""} ${item.label || ""}`);
}

function looksReadonly(item: Pick<UiObservation, "type">) {
  return /readonly|disabled/i.test(item.type || "");
}

function clockFromEpoch(value: unknown) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 10_000_000_000) return undefined;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return undefined;
  return `${padDatePart(date.getHours())}:${padDatePart(date.getMinutes())}:${padDatePart(date.getSeconds())}`;
}

function dateDay(value: unknown) {
  const match = String(value ?? "").match(/^(\d{4}-\d{2}-\d{2})/);
  if (match) return match[1];
  if (typeof value === "number" && Number.isFinite(value) && value > 10_000_000_000) {
    const date = new Date(value);
    if (!Number.isNaN(date.getTime())) {
      return `${date.getFullYear()}-${padDatePart(date.getMonth() + 1)}-${padDatePart(date.getDate())}`;
    }
  }
  return undefined;
}

export function collectUiObservations(events: UiEvidence[]): UiObservation[] {
  const items: UiObservation[] = [];
  for (const event of events) {
    const eventOptions = optionsOf(event);
    const label = eventLabel(event);
    const controlType = eventOptions?.length || event.role === "combobox" ? "select" : (event.inputType || event.role);
    if (event.name && !isGeneratedFieldName(event.name)) {
      items.push({ name: event.name, label, value: event.value, type: controlType, options: eventOptions });
    } else if (label) {
      items.push({ name: undefined, label, value: event.value, type: controlType, options: eventOptions });
    } else if (eventOptions?.length) {
      const matches = (event.form || []).filter(field =>
        field.label && eventOptions.some(item => String(item.label || item.value).includes(field.label!))
      );
      if (matches.length === 1) {
        items.push({
          name: realFieldName(matches[0]!.name),
          label: matches[0]!.label,
          value: matches[0]!.value,
          type: matches[0]!.type,
          required: matches[0]!.required,
          options: eventOptions,
          rangeIndex: matches[0]!.rangeIndex
        });
      }
    }
    for (const field of event.form || []) {
      items.push({
        name: realFieldName(field.name),
        label: field.label,
        value: field.value,
        type: field.type,
        required: field.required,
        options: optionsOf(event, field),
        rangeIndex: field.rangeIndex
      });
    }
  }
  return items;
}

function mergeObservations(items: UiObservation[]) {
  if (!items.length) return undefined;
  const named = items.find(item => item.name);
  return items.reduce((best, item) => ({
    name: best.name || item.name,
    label: named?.label || best.label || item.label,
    value: best.value !== undefined && best.value !== "" ? best.value : item.value,
    type: best.type || item.type,
    required: best.required === true || item.required === true,
    options: (item.options?.length || 0) > (best.options?.length || 0) ? item.options : best.options
  }));
}

function rowIdentity(row: Record<string, unknown>) {
  return row.id ?? row.value ?? row.code;
}

function rowDisplay(row: Record<string, unknown>) {
  const value = row.name ?? row.label ?? row.title ?? row.nickname;
  return value === undefined || value === null || value === "" ? undefined : value;
}

function listLabelFor(row: Record<string, unknown>, requestValue: unknown) {
  if (!sameValue(rowIdentity(row), requestValue)) return undefined;
  return rowDisplay(row);
}

function isJoinableList(rows: Record<string, unknown>[]) {
  const displayById = new Map<string, string>();
  for (const row of rows) {
    const id = rowIdentity(row);
    const display = rowDisplay(row);
    if (id === undefined || id === null || id === "" || display === undefined) continue;
    const key = String(id);
    const previous = displayById.get(key);
    if (previous !== undefined && previous !== String(display)) return false;
    displayById.set(key, String(display));
  }
  return displayById.size > 0;
}

function listSignature(list: RecordedList) {
  return list.rows
    .map(row => `${String(rowIdentity(row) ?? "")}:${String(rowDisplay(row) ?? "")}`)
    .sort()
    .join("|");
}

function uniqueLists(lists: RecordedList[]) {
  return [...new Map(lists.map(list => [listSignature(list), list])).values()];
}

function namesOf(list: RecordedList) {
  return new Set(
    list.rows
      .map(rowDisplay)
      .filter((name): name is string => name !== undefined)
      .map(name => String(name))
  );
}

function listsMatchingIdentity(lists: RecordedList[], display: string, requestValue: unknown) {
  if (requestValue === undefined || requestValue === null || requestValue === "") return [];
  return lists.filter(list => list.rows.some(row =>
    sameValue(rowIdentity(row), requestValue) && sameValue(rowDisplay(row), display)
  ));
}

function listForObservation(item: UiObservation, lists: RecordedList[], requestValue?: unknown) {
  const optionLabels = (item.options || []).map(option => String(option.label || "")).filter(Boolean);
  const display = item.value === undefined || item.value === "" ? undefined : String(item.value);
  const named = uniqueLists(lists);
  if (optionLabels.length >= 2) {
    const byOptions = named.filter(list => optionLabels.every(label => namesOf(list).has(label)));
    if (byOptions.length === 1) return byOptions[0];
    if (display && byOptions.length > 1) {
      const byValue = listsMatchingIdentity(byOptions, display, requestValue);
      if (byValue.length) return byValue[0];
    }
  }
  if (display) {
    const byDisplay = named.filter(list => namesOf(list).has(display));
    if (byDisplay.length === 1) return byDisplay[0];
    const byValue = listsMatchingIdentity(byDisplay, display, requestValue);
    if (byValue.length) return byValue[0];
  }
  return undefined;
}

function splitTypedRows(rows: Record<string, unknown>[]) {
  if (!rows.length || rows.some(row => row.dictType === undefined || row.dictType === null || row.dictType === "")) {
    return isJoinableList(rows) ? [rows] : [];
  }
  const groups = new Map<string, Record<string, unknown>[]>();
  for (const row of rows) {
    const key = String(row.dictType);
    const list = groups.get(key) || [];
    list.push(row);
    groups.set(key, list);
  }
  return [...groups.values()].filter(isJoinableList);
}

export function recordedLists(events: Array<{ response?: { body?: unknown } }>): RecordedList[] {
  const lists: RecordedList[] = [];
  for (const event of events) {
    const body = event.response?.body;
    if (!body || typeof body !== "object") continue;
    const data = (body as { data?: unknown }).data;
    const raw = Array.isArray(data)
      ? data
      : Array.isArray((data as { list?: unknown } | undefined)?.list)
        ? (data as { list: unknown[] }).list
        : Array.isArray(body) ? body : [];
    const rows = raw.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item));
    for (const group of splitTypedRows(rows)) lists.push({ rows: group });
  }
  return lists;
}

function requestValueIsShared(sample: unknown, fieldPath: string, value: unknown) {
  if (value === undefined || value === null || value === "") return false;
  return flattenRequestValues(sample).some(item =>
    item.path !== fieldPath
    && !PAGE_NAME.test(item.name)
    && sameValue(item.value, value)
  );
}

export function findObservation(
  field: InputFormField,
  requestValue: unknown,
  observations: UiObservation[],
  lists: RecordedList[] = [],
  sample?: unknown
) {
  const byName = observations.filter(item => uiNameMatches(item.name, field.name));
  const seedLabel = byName.find(item => item.label)?.label || field.label;
  const related = observations.filter(item =>
    uiNameMatches(item.name, field.name)
    || Boolean(item.label) && (item.label === seedLabel || item.label === field.label || item.label === field.name)
  );
  const direct = mergeObservations(related);
  if (direct) return expandObservation(direct, observations);

  if (isDistinctiveValue(requestValue)) {
    const exact = observations.filter(item => sameValue(item.value, requestValue));
    if (exact.length === 1) return expandObservation(exact[0], observations);
    const labels = new Set(exact.map(item => item.label).filter(Boolean));
    if (exact.length > 1 && labels.size === 1) return expandObservation(exact[0], observations);
  }

  if (requestValue !== undefined && requestValue !== null && requestValue !== "" && !requestValueIsShared(sample, field.path, requestValue)) {
    const hits = observations.filter(item => {
      const list = listForObservation(item, lists, requestValue);
      return Boolean(list && list.rows.some(row =>
        sameValue(rowIdentity(row), requestValue) && sameValue(rowDisplay(row), item.value)
      ));
    });
    const unique = [...new Map(hits.map(item => [item.label || item.name || "", item])).values()];
    if (unique.length === 1) return expandObservation(unique[0], observations);
  }

  const day = dateDay(requestValue);
  if (day) {
    const byDay = observations.filter(item => dateDay(item.value) === day);
    if (byDay.length === 1) return expandObservation(byDay[0], observations);
    const dayLabels = new Set(byDay.map(item => item.label).filter(Boolean));
    if (byDay.length > 1 && dayLabels.size === 1) return expandObservation(byDay[0], observations);
  }

  const dateFields = observations.filter(looksDateControl);
  const dateLabels = new Set(dateFields.map(item => item.label).filter(Boolean));
  if (dateLabels.size === 1 && looksDateControl(field)) return expandObservation(dateFields[0], observations);
  return undefined;
}

export function fieldHasUiEvidence(field: InputFormField, events: UiEvidence[]) {
  return Boolean(findObservation(field, undefined, collectUiObservations(events)));
}

export function staticCandidatesHaveUiEvidence(field: InputFormField, events: UiEvidence[]) {
  if (field.candidates?.type !== "static") return true;
  const labels = new Set(field.candidates.values.map(item => String(item.label)));
  return collectUiObservations(events).some(item => {
    const same = uiNameMatches(item.name, field.name)
      || Boolean(item.label) && (item.label === field.label || item.label === field.name);
    if (!same) return false;
    if (item.options?.some(option => labels.has(String(option.label)))) return true;
    return item.value !== undefined && item.value !== "" && labels.has(String(item.value));
  });
}

export function isEditableBusinessField(field: Pick<InputFormField, "name" | "label">, events: UiEvidence[] = []) {
  if (!events.length) return false;
  return Boolean(findObservation(field as InputFormField, undefined, collectUiObservations(events)));
}

function literalRule(value: unknown) {
  if (value === undefined || value === null || value === "") return undefined;
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return `literal:${String(value)}`;
  }
  return undefined;
}

function recordedClock(value: unknown) {
  const match = String(value ?? "").match(/^\d{4}-\d{2}-\d{2}[ T](\d{2}:\d{2}:\d{2})/);
  return match?.[1];
}

function formatHint(field: InputFormField, requestValue?: unknown) {
  if (/time|date|start|end/i.test(`${field.name} ${field.label}`)) {
    const clock = recordedClock(requestValue);
    if (typeof requestValue === "number") {
      return "，页面按 YYYY-MM-DD 填写，执行器转成当天 00:00 的毫秒时间戳";
    }
    if (clock) {
      return `，页面按 YYYY-MM-DD 填写，请求使用 YYYY-MM-DD ${clock}`;
    }
    return "，保持页面原始日期格式";
  }
  if (field.valueType === "number" || field.valueType === "integer") return "，保持页面数字格式";
  return "，保持页面原始输入格式";
}

function finalizeUnhandled(field: InputFormField): InputFormField {
  return {
    ...field,
    source: "system",
    systemHandled: true,
    required: false,
    defaultRule: undefined,
    sourceDetail: "请求中出现但未能唯一对应到页面控件。不要使用录制样本填这个字段"
  };
}

function widgetFromObservation(field: InputFormField, matched?: UiObservation): InputFormField["widget"] {
  const type = `${matched?.type || ""}`.toLowerCase();
  if (/textarea/.test(type)) return "textarea";
  if (/date|time|picker/.test(type)) return "date";
  if (/select|combobox/.test(type) || matched?.options?.length) return "select";
  if (/number/.test(type)) return "number";
  if (/checkbox|switch|boolean/.test(type)) return "boolean";
  return field.widget;
}

function observedLabel(field: InputFormField, matched?: UiObservation) {
  if (matched?.label && matched.label !== field.name) return matched.label;
  if (field.label && field.label !== field.name) return field.label;
  return field.name;
}

function staticFromList(list: RecordedList | undefined) {
  if (!list || list.rows.length < 2 || list.rows.length > 40) return undefined;
  const values = list.rows
    .map(row => {
      const label = rowDisplay(row);
      const value = rowIdentity(row);
      if (label === undefined || value === undefined || value === null || value === "") return undefined;
      return { value, label: String(label) };
    })
    .filter((item): item is { value: unknown; label: string } => Boolean(item));
  return values.length >= 2 ? values : undefined;
}

function asCaller(
  field: InputFormField,
  matched: UiObservation | undefined,
  requestValue: unknown,
  observations: UiObservation[] = [],
  lists: RecordedList[] = []
): InputFormField {
  const selected = matched?.value
    || observations.find(item => item.label === matched?.label && item.value !== undefined && item.value !== "")?.value;
  const looksText = /text|textarea|search|date|number|tel|email/i.test(`${matched?.type || ""}`)
    && !/select|combobox/i.test(`${matched?.type || ""}`);
  const useStatic = Boolean(matched?.options?.length) && !looksText && !looksDateControl(matched || field);
  const options = looksDateControl(matched || field)
    ? undefined
    : useStatic
      ? matched!.options!.map(item =>
        selected !== undefined && String(item.label) === String(selected) && requestValue !== undefined && requestValue !== selected
          ? { value: requestValue, label: String(item.label) }
          : { value: item.value, label: String(item.label || item.value) }
      )
      : staticFromList(matched ? listForObservation(matched, lists, requestValue) : undefined);
  const clock = recordedClock(requestValue);
  return {
    ...field,
    label: observedLabel(field, matched),
    source: "caller",
    required: matched?.required === true,
    requiredBasis: matched?.required === true ? "ui-required" : "not-observed",
    systemHandled: false,
    widget: looksDateControl(matched || field) ? "date" : options?.length ? "select" : widgetFromObservation(field, matched),
    candidates: options?.length ? { type: "static", values: options } : field.candidates,
    dateClock: looksDateControl(matched || field) && clock ? clock : field.dateClock,
    sourceDetail: options?.length
      ? "页面固定枚举，调用方直接选择，不要写成录制时的固定样本"
      : `调用方按页面原始格式提供（${field.valueType}）${formatHint(field, requestValue)}，不要改成录制样本`
  };
}

function asReadonly(field: InputFormField, matched: UiObservation | undefined): InputFormField {
  return {
    ...field,
    label: observedLabel(field, matched),
    source: "system",
    systemHandled: true,
    required: false,
    defaultRule: undefined,
    sourceDetail: "页面只读展示，由选择其它字段后自动带出。调用方不要手填"
  };
}

export function resolveFieldOwnership(
  field: InputFormField,
  requestValue: unknown,
  observations: UiObservation[],
  lists: RecordedList[] = [],
  sample?: unknown
): InputFormField {
  if (PAGE_NAME.test(field.name)) {
    return {
      ...field,
      source: "system",
      systemHandled: true,
      required: false,
      defaultRule: literalRule(requestValue) || field.defaultRule || "literal:1",
      sourceDetail: "列表分页由执行器按默认值补齐，调用方可覆盖，不要当成业务主键"
    };
  }
  const matched = findObservation(field, requestValue, observations, lists, sample);
  if (matched && looksReadonly(matched)) return asReadonly(field, matched);
  if (matched) return asCaller(field, matched, requestValue, observations, lists);
  return finalizeUnhandled(field);
}

function expandObservation(hit: UiObservation | undefined, observations: UiObservation[]) {
  if (!hit?.label) return hit;
  return mergeObservations(observations.filter(item => item.label === hit.label)) || hit;
}

function uniqueByLabel(items: UiObservation[]) {
  const merged = new Map<string, UiObservation>();
  for (const item of items) {
    if (!item.label) continue;
    const previous = merged.get(item.label);
    merged.set(item.label, previous ? mergeObservations([previous, item])! : item);
  }
  return [...merged.values()];
}

function observationMatchesValue(item: UiObservation, value: unknown, lists: RecordedList[]) {
  if (sameValue(item.value, value)) return true;
  const day = dateDay(value);
  if (day && dateDay(item.value) === day) return true;
  const list = listForObservation(item, lists, value);
  return Boolean(list && list.rows.some(row =>
    sameValue(rowIdentity(row), value)
    && (item.value === undefined || item.value === "" || sameValue(rowDisplay(row), item.value))
  ));
}

function isReadonlyBound(field: InputFormField) {
  return field.source === "system" && /只读展示/.test(field.sourceDetail || "");
}

function leftoverCallerLabels(fields: InputFormField[]) {
  return new Set(fields.filter(field => field.source === "caller").map(field => field.label));
}

function leftoverEditable(fields: InputFormField[], observations: UiObservation[]) {
  const callerLabels = leftoverCallerLabels(fields);
  return uniqueByLabel(observations.filter(item =>
    item.label && !callerLabels.has(item.label) && !looksReadonly(item)
  ));
}

function bindObservation(
  field: InputFormField,
  matched: UiObservation,
  requestValue: unknown,
  observations: UiObservation[],
  lists: RecordedList[]
) {
  const full = expandObservation(matched, observations) || matched;
  return looksReadonly(full)
    ? asReadonly(field, full)
    : asCaller(field, full, requestValue, observations, lists);
}

function nameIndex(name: string) {
  const match = name.match(/\[(\d+)\]$/);
  return match ? Number(match[1]) : undefined;
}

function isDateSlot(field: Pick<UiObservation, "type" | "label" | "name">) {
  if (/date|time|picker/i.test(field.type || "")) return true;
  if (/select|combobox|number|readonly|checkbox|textarea/i.test(field.type || "")) return false;
  return /时间|日期/.test(`${field.label || ""} ${field.name || ""}`);
}

function adjacentDateSlots(owner?: UiEvidence) {
  if (!owner?.form?.length) return [];
  const slots: UiObservation[][] = [];
  let current: UiObservation[] = [];
  for (const field of owner.form) {
    if (field.label && isDateSlot(field)) {
      current.push({
        name: field.name,
        label: field.label,
        value: field.value,
        type: field.type,
        required: field.required,
        options: field.options,
        rangeIndex: field.rangeIndex
      });
    } else if (current.length) {
      if (current.length >= 2) slots.push(current);
      current = [];
    }
  }
  if (current.length >= 2) slots.push(current);
  return slots;
}

function bindIndexedDateRange(
  fields: InputFormField[],
  owner: UiEvidence | undefined,
  observations: UiObservation[],
  sample: unknown,
  lists: RecordedList[],
  includeCaller = false
) {
  const eligible = fields.filter(field =>
    (includeCaller ? field.source === "caller" && field.label === field.name : field.source !== "caller")
    && !PAGE_NAME.test(field.name)
    && !isReadonlyBound(field)
    && looksDateControl(field)
    && nameIndex(field.name) !== undefined
  );
  if (eligible.length < 2) return fields;
  const byStem = new Map<string, InputFormField[]>();
  for (const field of eligible) {
    const stem = field.name.replace(/\[\d+\]$/, "");
    byStem.set(stem, [...(byStem.get(stem) || []), field]);
  }
  const bound = new Map<string, InputFormField>();
  for (const group of byStem.values()) {
    if (group.length < 2) continue;
    const sorted = [...group].sort((left, right) => (nameIndex(left.name) ?? 0) - (nameIndex(right.name) ?? 0));
    const byRange = observations.filter(item =>
      item.rangeIndex !== undefined && item.label && isDateSlot(item) && !looksReadonly(item)
    );
    const rangeSlots = new Map<number, UiObservation>();
    for (const item of byRange) {
      if (!rangeSlots.has(item.rangeIndex!)) rangeSlots.set(item.rangeIndex!, expandObservation(item, observations) || item);
    }
    const slots = rangeSlots.size === sorted.length
      ? sorted.map(field => rangeSlots.get(nameIndex(field.name)!))
      : adjacentDateSlots(owner).find(item => item.length === sorted.length);
    if (!slots || slots.some(item => !item)) continue;
    sorted.forEach((field, index) => {
      bound.set(field.path, asCaller(field, slots[index]!, requestValueAt(sample, field.path), observations, lists));
    });
  }
  if (!bound.size) return fields;
  return fields.map(field => bound.get(field.path) || field);
}

export function bindLeftoverFields(
  fields: InputFormField[],
  observations: UiObservation[],
  sample: unknown,
  lists: RecordedList[] = [],
  owner?: UiEvidence
): InputFormField[] {
  const leftoverObs = leftoverEditable(fields, observations);
  const leftoverFields = fields.filter(field => field.source !== "caller" && !PAGE_NAME.test(field.name) && !isReadonlyBound(field));
  if (leftoverObs.length === 1 && leftoverFields.length === 1) {
    const field = leftoverFields[0]!;
    return fields.map(item =>
      item.path === field.path
        ? asCaller(item, leftoverObs[0], requestValueAt(sample, item.path), observations, lists)
        : item
    );
  }
  const dateObs = leftoverObs.filter(looksDateControl);
  const dateFields = leftoverFields.filter(looksDateControl);
  const dateLabels = new Set(dateObs.map(item => item.label));
  if (dateObs.length && dateFields.length && dateLabels.size === 1) {
    return fields.map(item =>
      dateFields.some(field => field.path === item.path)
        ? asCaller(item, dateObs[0], requestValueAt(sample, item.path), observations, lists)
        : item
    );
  }
  return bindIndexedDateRange(fields, owner, observations, sample, lists);
}

export function assignUniqueRemaining(
  fields: InputFormField[],
  observations: UiObservation[],
  sample: unknown,
  lists: RecordedList[] = []
): InputFormField[] {
  const callerLabels = leftoverCallerLabels(fields);
  const leftoverObs = uniqueByLabel(observations.filter(item => item.label && !callerLabels.has(item.label)));
  const leftoverFields = fields.filter(field => field.source !== "caller" && !PAGE_NAME.test(field.name) && !isReadonlyBound(field));
  const bound = new Map<string, InputFormField>();
  for (const field of leftoverFields) {
    const value = requestValueAt(sample, field.path);
    if (value === undefined || value === null || value === "") continue;
    const sameValueFields = leftoverFields.filter(item => sameValue(requestValueAt(sample, item.path), value));
    if (sameValueFields.length !== 1) continue;
    const matches = leftoverObs.filter(item =>
      !bound.has(item.label || "") && observationMatchesValue(item, value, lists)
    );
    const unique = [...new Map(matches.map(item => [item.label || "", item])).values()];
    if (unique.length !== 1) continue;
    bound.set(unique[0]!.label || field.path, bindObservation(field, unique[0]!, value, observations, lists));
  }
  if (!bound.size) return fields;
  const byPath = new Map([...bound.values()].map(field => [field.path, field]));
  return fields.map(field => byPath.get(field.path) || field);
}

export function assignUniqueFromSamples(
  fields: InputFormField[],
  observations: UiObservation[],
  samples: unknown[],
  lists: RecordedList[] = []
) {
  return samples.reduce((current, sample) => assignUniqueRemaining(current, observations, sample, lists), fields);
}

function uniqueAssignment<TField, TObs>(
  fields: TField[],
  observations: TObs[],
  compatible: (field: TField, observation: TObs) => boolean
) {
  const matches: number[][] = [];
  const walk = (index: number, used: Set<number>, current: number[]) => {
    if (index === fields.length) {
      matches.push([...current]);
      return;
    }
    for (let obsIndex = 0; obsIndex < observations.length; obsIndex++) {
      if (used.has(obsIndex) || !compatible(fields[index]!, observations[obsIndex]!)) continue;
      used.add(obsIndex);
      current.push(obsIndex);
      walk(index + 1, used, current);
      current.pop();
      used.delete(obsIndex);
      if (matches.length > 1) return;
    }
  };
  walk(0, new Set(), []);
  return matches.length === 1 ? matches[0] : undefined;
}

export function bindByUniqueMatching(
  fields: InputFormField[],
  observations: UiObservation[],
  sample: unknown,
  lists: RecordedList[] = []
): InputFormField[] {
  const leftoverObs = leftoverEditable(fields, observations);
  const leftoverFields = fields.filter(field => field.source !== "caller" && !PAGE_NAME.test(field.name) && !isReadonlyBound(field));
  if (leftoverFields.length < 2 || leftoverObs.length < leftoverFields.length) return fields;
  const assignment = uniqueAssignment(leftoverFields, leftoverObs, (field, item) => {
    const value = requestValueAt(sample, field.path);
    if (value === undefined || value === null || value === "") return false;
    if (observationMatchesValue(item, value, lists)) return true;
    const list = listForObservation(item, lists, value);
    return Boolean(list && list.rows.some(row => sameValue(rowIdentity(row), value)));
  });
  if (!assignment) return fields;
  const byPath = new Map(leftoverFields.map((field, index) => [
    field.path,
    bindObservation(field, leftoverObs[assignment[index]!]!, requestValueAt(sample, field.path), observations, lists)
  ]));
  return fields.map(field => byPath.get(field.path) || field);
}

function unlabeledCallers(fields: InputFormField[]) {
  return fields.filter(field => field.source === "caller" && field.label === field.name && !PAGE_NAME.test(field.name));
}

function bindUnlabeledCallers(
  fields: InputFormField[],
  observations: UiObservation[],
  sample: unknown,
  lists: RecordedList[]
) {
  const unlabeled = unlabeledCallers(fields);
  const leftoverObs = leftoverEditable(fields, observations);
  if (leftoverObs.length === 1 && unlabeled.length === 1) {
    return fields.map(field =>
      field.path === unlabeled[0]!.path
        ? asCaller(field, leftoverObs[0], requestValueAt(sample, field.path), observations, lists)
        : field
    );
  }
  const dateObs = leftoverObs.filter(looksDateControl);
  const dateFields = unlabeled.filter(looksDateControl);
  const dateLabels = new Set(dateObs.map(item => item.label));
  if (dateObs.length && dateFields.length && dateLabels.size === 1) {
    return fields.map(field =>
      dateFields.some(item => item.path === field.path)
        ? asCaller(field, dateObs[0], requestValueAt(sample, field.path), observations, lists)
        : field
    );
  }
  return fields;
}

function leftoverForField(
  field: InputFormField,
  leftover: UiObservation[],
  sample: unknown,
  lists: RecordedList[]
) {
  if ((field.widget === "select" || field.widget === "text") && field.valueType !== "number" && field.valueType !== "integer") {
    const selects = leftover.filter(item => /select|combobox/.test(item.type || ""));
    if (selects.length) return selects;
  }
  const value = requestValueAt(sample, field.path);
  const matching = leftover.filter(item => observationMatchesValue(item, value, lists));
  return matching.length ? matching : leftover;
}

function attachUnresolvedHints(
  fields: InputFormField[],
  observations: UiObservation[],
  sample: unknown,
  lists: RecordedList[]
) {
  const leftover = leftoverEditable(fields, observations);
  if (!leftover.length) return fields;
  return fields.map(field => {
    if (field.source !== "caller" || field.label !== field.name || PAGE_NAME.test(field.name)) return field;
    if (/页面未唯一对应/.test(field.sourceDetail || "")) return field;
    const hints = leftoverForField(field, leftover, sample, lists);
    if (!hints.length) return field;
    const names = hints.map(item => `${item.label}${item.required ? "（必填）" : ""}`).join("、");
    return {
      ...field,
      sourceDetail: `${field.sourceDetail}。页面未唯一对应：${names}`
    };
  });
}

function enrichFromObservations(
  field: InputFormField,
  observations: UiObservation[],
  sample: unknown,
  lists: RecordedList[]
) {
  if (field.source !== "caller" || field.label === field.name) return field;
  const matched = expandObservation({ label: field.label }, observations);
  if (!matched) return field;
  const next = asCaller(field, matched, requestValueAt(sample, field.path), observations, lists);
  return {
    ...next,
    candidates: field.candidates?.type === "capability" ? field.candidates : next.candidates || field.candidates,
    sourceDetail: field.candidates?.type === "capability" ? field.sourceDetail : next.sourceDetail
  };
}

export function finalizeCallerFields(
  fields: InputFormField[],
  observations: UiObservation[],
  sample: unknown,
  lists: RecordedList[] = [],
  owner?: UiEvidence
): InputFormField[] {
  const relabeled = bindUnlabeledCallers(
    bindIndexedDateRange(fields, owner, observations, sample, lists, true),
    observations,
    sample,
    lists
  );
  return attachUnresolvedHints(relabeled, observations, sample, lists).map(field =>
    enrichFromObservations(field, observations, sample, lists)
  );
}

export function promoteUnboundFillable(
  fields: InputFormField[],
  observations: UiObservation[],
  sample: unknown
): InputFormField[] {
  const leftoverObs = leftoverEditable(fields, observations);
  if (!leftoverObs.length) return fields;
  const eligible = fields.filter(field =>
    field.source !== "caller" && !PAGE_NAME.test(field.name) && !isReadonlyBound(field)
  );
  const matches = eligible.map(field => {
    const value = requestValueAt(sample, field.path);
    const matching = leftoverObs.filter(item =>
      sameValue(item.value, value) || Boolean(dateDay(value) && dateDay(item.value) === dateDay(value))
    );
    return { field, matching };
  });
  const labelUses = new Map<string, number>();
  for (const item of matches) {
    if (item.matching.length !== 1 || looksReadonly(item.matching[0])) continue;
    const label = item.matching[0]!.label || "";
    if (!label) continue;
    labelUses.set(label, (labelUses.get(label) || 0) + 1);
  }
  return fields.map(field => {
    const hit = matches.find(item => item.field.path === field.path);
    if (!hit || hit.matching.length !== 1) return field;
    const observation = hit.matching[0]!;
    if (!observation.label || looksReadonly(observation) || (labelUses.get(observation.label) || 0) !== 1) return field;
    return asCaller(field, observation, requestValueAt(sample, field.path), observations, []);
  });
}

export function requestValueAt(sample: unknown, path: string) {
  return flattenRequestValues(sample).find(item => item.path === path)?.value;
}

function formNames(form: NonNullable<UiEvidence["form"]>) {
  return form.map(field => realFieldName(field.name)).filter((name): name is string => Boolean(name));
}

function formLabels(form: NonNullable<UiEvidence["form"]>) {
  return form.map(field => field.label).filter((label): label is string => Boolean(label));
}

function nameOverlap(form: NonNullable<UiEvidence["form"]>, requestNames: string[]) {
  return formNames(form).filter(name => requestNames.some(requestName => uiNameMatches(name, requestName))).length;
}

function valueOverlap(form: NonNullable<UiEvidence["form"]>, sample: unknown) {
  const values = flattenRequestValues(sample);
  return form.filter(field =>
    isDistinctiveValue(field.value)
    && values.some(item => isDistinctiveValue(item.value) && sameValue(item.value, field.value))
  ).length;
}

export function owningFormEvent(
  event: NetworkEvidence,
  uiEvents: UiEvidence[],
  sample: unknown
): UiEvidence | undefined {
  const requestNames = flattenRequestValues(sample).map(item => item.name);
  const correlated = event.correlatedUiEvidenceId
    ? uiEvents.find(item => item.id === event.correlatedUiEvidenceId)
    : undefined;
  if (correlated?.form?.length) return correlated;
  const at = Date.parse(event.at);
  const candidates = uiEvents
    .filter(item => item.sessionId === event.sessionId && Date.parse(item.at) <= at && Boolean(item.form?.length))
    .sort((left, right) => Date.parse(right.at) - Date.parse(left.at));

  let best: UiEvidence | undefined;
  let bestScore = -1;
  for (const item of candidates) {
    const named = formNames(item.form!);
    const names = nameOverlap(item.form!, requestNames);
    const values = valueOverlap(item.form!, sample);
    if (names === 0 && values === 0 && item.id !== correlated?.id) continue;
    const union = named.length + requestNames.length - names;
    const jaccard = union > 0 ? names / union : 0;
    const recency = 1 / (1 + (at - Date.parse(item.at)) / 60_000);
    const score = jaccard * 10 + values * 2 + recency * 0.1;
    if (score > bestScore) {
      bestScore = score;
      best = item;
    }
  }
  return best || (correlated?.form?.length ? correlated : candidates[0]);
}

function belongsToOwningForm(
  item: UiEvidence,
  owner: UiEvidence | undefined,
  ownerLabels: Set<string>,
  ownerNames: Set<string>,
  correlatedId?: string
) {
  if (owner && item.id === owner.id) return true;
  if (correlatedId && item.id === correlatedId) return true;
  if (item.form?.length && owner?.form?.length) {
    const labels = [...new Set(formLabels(item.form))];
    const extra = labels.filter(label => !ownerLabels.has(label)).length;
    const overlap = labels.filter(label => ownerLabels.has(label)).length;
    return labels.length > 0 && extra === 0 && overlap > 0;
  }
  const asked = emptyPromptLabel(item.text);
  return Boolean(
    (item.label && ownerLabels.has(item.label))
    || (item.name && ownerNames.has(item.name))
    || (asked && ownerLabels.has(asked))
  );
}

export function sameFormShape(left?: UiEvidence, right?: UiEvidence) {
  if (!left?.form?.length || !right?.form?.length) return false;
  const labels = new Set(formLabels(left.form));
  const other = formLabels(right.form);
  const overlap = other.filter(label => labels.has(label)).length;
  const union = new Set([...labels, ...other]).size;
  return union > 0 && overlap / union >= 0.6;
}

export function relatedUiEvents(
  event: NetworkEvidence,
  uiById: Map<string, UiEvidence>,
  sample: unknown
): UiEvidence[] {
  const uiEvents = [...uiById.values()];
  const owner = owningFormEvent(event, uiEvents, sample);
  const ownerLabels = new Set(owner?.form ? formLabels(owner.form) : []);
  const ownerNames = new Set(owner?.form ? formNames(owner.form) : []);
  const at = Date.parse(event.at);
  return uiEvents
    .filter(item => {
      if (item.sessionId !== event.sessionId) return false;
      if (Date.parse(item.at) > at + 500) return false;
      return belongsToOwningForm(item, owner, ownerLabels, ownerNames, event.correlatedUiEvidenceId);
    })
    .sort((left, right) => Date.parse(right.at) - Date.parse(left.at));
}
