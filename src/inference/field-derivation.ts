import type { CapabilityContract, DataBinding, EvidenceEvent, InputFormField, NetworkEvidence } from "../domain.js";
import { id } from "../utils.js";
import { flattenRequestValues, requestValueAt, sameValue } from "./field-resolver.js";
import { normalizeUrl } from "./heuristics.js";
import { isNoiseCapability } from "./export-scope.js";

const WRITE_OPERATIONS = new Set(["create", "update", "review", "delete", "upload", "action"]);
const PAGE_NAME = /^(pageNo|pageSize|pageNum|page|size|current|offset|limit)$/i;
const PERCENT_NAME = /percent|rate|比率|税率|优惠率|折扣/i;
const ENVELOPE_LEAF = /\.(success|ok|msg|message|error|errmsg)$/i;
const UUID_VALUE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const ISO_VALUE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/;
const EXECUTABLE_RULE = /^(literal:.+|env:[A-Za-z_][A-Za-z0-9_]*|uuid|now:iso|from:[^|]+(?:\|via:[A-Za-z_][A-Za-z0-9_]*)?|computed:.+|copy:[A-Za-z_][A-Za-z0-9_]*)$/;

export function isExecutableRule(rule?: string) {
  return Boolean(rule && EXECUTABLE_RULE.test(rule));
}

export function parseFromRule(rule: string) {
  const match = /^from:([^:]+):(.+?)(?:\|via:([A-Za-z_][A-Za-z0-9_]*))?$/.exec(rule);
  if (!match) return undefined;
  return { capabilityId: match[1]!, fromPath: match[2]!, via: match[3] };
}

export function parseComputedRule(rule: string) {
  return rule.startsWith("computed:") ? rule.slice("computed:".length).trim() : undefined;
}

function near(left: number, right: number) {
  return Math.abs(left - right) <= Math.max(0.005, Math.abs(right) * 1e-6);
}

function rowIdentity(row: Record<string, unknown>) {
  return row.id ?? row.value ?? row.code;
}

function headerRecord(sample: unknown) {
  if (!sample || typeof sample !== "object" || Array.isArray(sample)) return {};
  return Object.fromEntries(
    Object.entries(sample as Record<string, unknown>).filter(([, value]) => !Array.isArray(value) && (typeof value !== "object" || value === null))
  );
}

function itemRecords(sample: unknown) {
  if (!sample || typeof sample !== "object") return [];
  const items = (sample as { items?: unknown }).items;
  return Array.isArray(items)
    ? items.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item))
    : [];
}

function numericEntries(record: Record<string, unknown>) {
  return Object.entries(record)
    .filter(([, value]) => typeof value === "number" && Number.isFinite(value))
    .map(([name, value]) => ({ name, value: value as number }));
}

function commonPrefixLength(left: string, right: string) {
  const a = left.toLowerCase();
  const b = right.toLowerCase();
  let index = 0;
  while (index < a.length && index < b.length && a[index] === b[index]) index++;
  return index;
}

function formulaScore(targetName: string, expr: string) {
  const names = expr.match(/[A-Za-z_][A-Za-z0-9_]*/g) || [];
  return Math.max(0, ...names.filter(name => name !== "sum" && name !== "items").map(name => commonPrefixLength(targetName, name)));
}

function commute(operator: "*" | "+", left: string, right: string) {
  return [left, right].sort((a, b) => a.localeCompare(b)).join(` ${operator} `);
}

function uniqueFormula(targetName: string, hits: string[]) {
  const unique = [...new Set(hits)];
  if (!unique.length) return undefined;
  if (unique.length === 1) return unique[0];
  return unique.slice().sort((left, right) => {
    const score = formulaScore(targetName, right) - formulaScore(targetName, left);
    if (score) return score;
    if (left.length !== right.length) return left.length - right.length;
    return left.localeCompare(right);
  })[0];
}

function binaryFormulas(target: number, others: Array<{ name: string; value: number }>) {
  const hits: string[] = [];
  for (const left of others) {
    for (const right of others) {
      if (left.name === right.name) continue;
      const percentRight = PERCENT_NAME.test(right.name);
      const percentLeft = PERCENT_NAME.test(left.name);
      if (!percentLeft && !percentRight && near(target, left.value * right.value)) hits.push(commute("*", left.name, right.name));
      if (near(target, left.value + right.value)) hits.push(commute("+", left.name, right.name));
      if (near(target, left.value - right.value)) hits.push(`${left.name} - ${right.name}`);
      if (!percentRight && right.value !== 0 && near(target, left.value / right.value)) hits.push(`${left.name} / ${right.name}`);
      if (percentRight && near(target, left.value * right.value / 100)) hits.push(`${left.name} * ${right.name} / 100`);
      if (percentRight && near(target, left.value * (1 - right.value / 100))) hits.push(`${left.name} * (1 - ${right.name} / 100)`);
      if (percentRight && near(target, left.value * (1 + right.value / 100))) hits.push(`${left.name} * (1 + ${right.name} / 100)`);
    }
  }
  return hits;
}

function inferredFormula(targetName: string, target: number, others: Array<{ name: string; value: number }>) {
  return uniqueFormula(targetName, binaryFormulas(target, others.filter(item => item.name !== targetName)));
}

function fieldScope(path: string) {
  return path.includes("[*]") ? "item" : "header";
}

function capabilityForEvent(event: NetworkEvidence, catalog: CapabilityContract[]) {
  const normalized = normalizeUrl(event.request.url);
  return catalog.find(item =>
    item.operation === "query"
    && item.transport.method.toUpperCase() === event.request.method.toUpperCase()
    && item.transport.pathTemplate === normalized.pathTemplate
    && !isNoiseCapability(item)
  );
}

function responseHits(body: unknown, prefix = "$", row?: Record<string, unknown>): Array<{ path: string; value: unknown; row?: Record<string, unknown> }> {
  if (body === undefined) return [];
  if (Array.isArray(body)) {
    return body.flatMap(item => {
      if (item && typeof item === "object" && !Array.isArray(item)) {
        return responseHits(item, `${prefix}[*]`, item as Record<string, unknown>);
      }
      return item === undefined ? [] : [{ path: prefix, value: item, row }];
    });
  }
  if (body && typeof body === "object") {
    return Object.entries(body as Record<string, unknown>).flatMap(([key, child]) =>
      responseHits(child, `${prefix}.${key}`, row)
    );
  }
  return [{ path: prefix, value: body, row }];
}

function requestJoins(sample: unknown) {
  return flattenRequestValues(sample)
    .filter(item => item.value !== undefined && item.value !== null && item.value !== "")
    .map(item => ({ name: item.name, path: item.path, value: item.value, scope: fieldScope(item.path) }));
}

function sameDerivedValue(left: unknown, right: unknown) {
  if (typeof left === "boolean" || typeof right === "boolean") {
    return typeof left === "boolean" && typeof right === "boolean" && left === right;
  }
  return sameValue(left, right);
}

function isEnvelopePath(path: string, fieldName: string) {
  if (path === `$.${fieldName}` || path.endsWith(`.${fieldName}`)) return false;
  return ENVELOPE_LEAF.test(path) || /(^|\.)code$/.test(path);
}

function pickVia(field: InputFormField, joins: ReturnType<typeof requestJoins>, row: Record<string, unknown> | undefined) {
  if (!row) {
    return joins.find(item => fieldScope(field.path) === item.scope) || joins[0];
  }
  const identity = rowIdentity(row);
  const matched = joins.filter(item => sameValue(item.value, identity));
  if (!matched.length) return undefined;
  return matched.find(item => fieldScope(item.path) === fieldScope(field.path)) || matched[0];
}

function lookupEvents(events: EvidenceEvent[]) {
  return events.filter((event): event is NetworkEvidence =>
    event.kind === "network"
    && event.request.method.toUpperCase() === "GET"
    && Boolean(event.response && event.response.status >= 200 && event.response.status < 400)
  );
}

function fromApiMatch(
  field: InputFormField,
  value: unknown,
  sample: unknown,
  events: EvidenceEvent[],
  catalog: CapabilityContract[]
) {
  if (value === undefined || value === null || value === "") return undefined;
  const joins = requestJoins(sample);
  const hits: Array<{ capabilityId: string; fromPath: string; via?: string; eventId: string; method: string; pathTemplate: string }> = [];
  for (const event of lookupEvents(events)) {
    const capability = capabilityForEvent(event, catalog);
    if (!capability) continue;
    for (const leaf of responseHits(event.response?.body)) {
      if (isEnvelopePath(leaf.path, field.name)) continue;
      if (!sameDerivedValue(leaf.value, value)) continue;
      const query = event.request.query || {};
      const queryJoin = joins.find(item =>
        Object.entries(query).some(([key, queryValue]) => key === item.name && sameValue(queryValue, item.value))
      );
      const via = leaf.row ? pickVia(field, joins, leaf.row) : queryJoin;
      if (leaf.row && !via) continue;
      hits.push({
        capabilityId: capability.id,
        fromPath: leaf.path,
        via: via?.name,
        eventId: event.id,
        method: capability.transport.method,
        pathTemplate: capability.transport.pathTemplate
      });
    }
  }
  const unique = [...new Map(hits.map(item => [`${item.capabilityId}|${item.fromPath}|${item.via || ""}`, item])).values()];
  if (unique.length !== 1) return undefined;
  return unique[0];
}

function emptyDefault(value: unknown) {
  if (Array.isArray(value) && value.length === 0) return "literal:[]";
  if (value === 0) return "literal:0";
  if (value === false) return "literal:false";
  return undefined;
}

function looksInvariantConstant(field: InputFormField, value: unknown) {
  if (typeof value === "string" && /^[a-z][a-z0-9_]*$/i.test(value) && /Type|Key|Code|Def/i.test(field.name)) return true;
  return false;
}

function generatedRule(field: InputFormField, value: unknown) {
  if (typeof value === "string" && UUID_VALUE.test(value)) {
    return { rule: "uuid", detail: "请求值是 UUID，执行时现场生成，不能冻成录制样本" };
  }
  if (typeof value === "string" && ISO_VALUE.test(value) && /time|date|At$|created|updated/i.test(`${field.name} ${field.label}`)) {
    return { rule: "now:iso", detail: "请求值是时间戳，执行时取当前时间，不能冻成录制样本" };
  }
  return undefined;
}

function headerAggregates(items: Record<string, unknown>[], known: Set<string>) {
  if (!items.length) return [] as Array<{ name: string; value: number }>;
  const names = new Set(items.flatMap(item => numericEntries(item).map(entry => entry.name)));
  return [...names].flatMap(name => {
    if (!known.has(name)) return [];
    const values = items.map(item => item[name]);
    if (!values.every(value => typeof value === "number" && Number.isFinite(value))) return [];
    return [{ name: `sum(items.${name})`, value: values.reduce((sum: number, value) => sum + Number(value), 0) }];
  });
}

function computedForField(field: InputFormField, sample: unknown, known: Set<string>) {
  const value = requestValueAt(sample, field.path);
  if (typeof value !== "number" || !Number.isFinite(value)) return undefined;
  const header = headerRecord(sample);
  const items = itemRecords(sample);
  if (fieldScope(field.path) === "item") {
    const formulas = [...new Set(items.map(item => {
      const others = numericEntries(item).filter(entry => known.has(entry.name));
      return inferredFormula(field.name, Number(item[field.name]), others);
    }).filter(Boolean))];
    return formulas.length === 1 ? formulas[0] : undefined;
  }
  const others = [
    ...numericEntries(header).filter(entry => known.has(entry.name)),
    ...headerAggregates(items, known)
  ];
  return inferredFormula(field.name, value, others);
}

function viaLabel(fields: InputFormField[], via?: string) {
  return fields.find(field => field.name === via)?.label || via || "关联字段";
}

function shouldKeep(field: InputFormField, capability: CapabilityContract) {
  if (field.source === "caller") return true;
  if (PAGE_NAME.test(field.name)) return true;
  if (capability.editing?.fields === "manual" && capability.editing.fieldPaths?.includes(field.path)) return true;
  if (field.source === "binding" && capability.bindings.some(binding => binding.approved && binding.approvalSource === "human" && binding.toPath === field.path)) {
    return true;
  }
  return false;
}

function asFrom(field: InputFormField, match: NonNullable<ReturnType<typeof fromApiMatch>>, fields: InputFormField[]): InputFormField {
  const via = match.via;
  const rule = via ? `from:${match.capabilityId}:${match.fromPath}|via:${via}` : `from:${match.capabilityId}:${match.fromPath}`;
  return {
    ...field,
    source: "binding",
    systemHandled: true,
    required: false,
    defaultRule: rule,
    sourceDetail: `选择「${viaLabel(fields, via)}」后，从已录制查询 ${match.method} ${match.pathTemplate} 的 ${match.fromPath} 带出，调用方不要手填`
  };
}

function asComputed(field: InputFormField, expr: string): InputFormField {
  return {
    ...field,
    source: "computed",
    systemHandled: true,
    required: false,
    defaultRule: `computed:${expr}`,
    sourceDetail: `由请求内字段自动计算：${expr}，调用方不要手填`
  };
}

function asDefault(field: InputFormField, rule: string, detail: string): InputFormField {
  return {
    ...field,
    source: "fixed",
    systemHandled: true,
    required: false,
    defaultRule: rule,
    sourceDetail: detail
  };
}

function asCallerInput(field: InputFormField): InputFormField {
  return {
    ...field,
    source: "caller",
    systemHandled: false,
    defaultRule: undefined,
    sourceDetail: `计算或带出其它字段所需的输入（${field.valueType}），由调用方提供，不要写成录制样本`
  };
}

function asCopy(field: InputFormField, source: InputFormField): InputFormField {
  return {
    ...field,
    source: "computed",
    systemHandled: true,
    required: false,
    defaultRule: `copy:${source.name}`,
    sourceDetail: `与「${source.label}」在请求中唯一同值，执行时从该字段拷贝，调用方不要手填`
  };
}

function asGenerated(field: InputFormField, rule: string, detail: string): InputFormField {
  return {
    ...field,
    source: "generated",
    systemHandled: true,
    required: false,
    defaultRule: rule,
    sourceDetail: detail
  };
}

function uniqueCopySource(field: InputFormField, fields: InputFormField[], sample: unknown) {
  const value = requestValueAt(sample, field.path);
  if (value === undefined || value === null || value === "" || value === 0 || value === 1 || value === false) return undefined;
  const sameScope = fields.filter(item => item.path !== field.path && fieldScope(item.path) === fieldScope(field.path));
  const hits = sameScope.filter(item =>
    sameDerivedValue(requestValueAt(sample, item.path), value)
    && (item.source === "caller" || Boolean(item.defaultRule))
  );
  return hits.length === 1 ? hits[0] : undefined;
}

function operandNames(expr: string) {
  return [...new Set((expr.match(/[A-Za-z_][A-Za-z0-9_]*/g) || []).filter(name => name !== "sum" && name !== "items"))];
}

function knownNames(fields: InputFormField[]) {
  return new Set(
    fields
      .filter(field => field.source === "caller" || Boolean(field.defaultRule) || PAGE_NAME.test(field.name))
      .map(field => field.name)
  );
}

function evidenceSample(capability: CapabilityContract, events: EvidenceEvent[]) {
  const ids = new Set(capability.evidence.filter(item => item.kind === "network").map(item => item.eventId));
  let best: unknown;
  let size = -1;
  for (const event of events) {
    if (event.kind !== "network" || !ids.has(event.id)) continue;
    const network = event as NetworkEvidence;
    const body = network.request.body;
    const query = network.request.query || {};
    const input = body && typeof body === "object" && !Array.isArray(body)
      ? { ...query, ...(body as Record<string, unknown>) }
      : query;
    const nextSize = JSON.stringify(input ?? {}).length;
    if (nextSize > size) {
      size = nextSize;
      best = input;
    }
  }
  return best;
}

function applyComputed(fields: InputFormField[], sample: unknown, capability: CapabilityContract) {
  let next = fields;
  let changed = true;
  while (changed) {
    changed = false;
    const known = knownNames(next);
    next = next.map(field => {
      if (shouldKeep(field, capability) || field.defaultRule) return field;
      const expr = computedForField(field, sample, known);
      if (!expr) return field;
      changed = true;
      return asComputed(field, expr);
    });
  }
  return next;
}

function unexplained(field: InputFormField): InputFormField {
  return {
    ...field,
    defaultRule: undefined,
    sourceDetail: field.sourceDetail?.includes("只读")
      ? "页面只读展示，但已录制查询里没有唯一带出路径，不能把录制样本当成固定值"
      : "请求中出现但未能唯一对应到页面输入、其它接口或计算公式，不能把录制样本当成固定值"
  };
}

export function attachDerivationRules(
  fields: InputFormField[],
  sample: unknown,
  events: EvidenceEvent[],
  catalog: CapabilityContract[],
  capability: CapabilityContract
): { fields: InputFormField[]; bindings: DataBinding[] } {
  const bindings: DataBinding[] = [];
  let next = fields.map(field => {
    if (shouldKeep(field, capability) || field.defaultRule) return field;
    const value = requestValueAt(sample, field.path);
    const from = fromApiMatch(field, value, sample, events, catalog);
    if (from) {
      bindings.push({
        id: id("bind"),
        fromCapabilityId: from.capabilityId,
        fromPath: from.fromPath,
        toPath: field.path,
        confidence: 1,
        evidenceIds: [from.eventId],
        approved: true,
        approvalSource: "evidence",
        approvedAt: new Date().toISOString(),
        note: `选择「${viaLabel(fields, from.via)}」后从录制查询响应唯一带出`
      });
      return asFrom(field, from, fields);
    }
    return field;
  });

  next = applyComputed(next, sample, capability);

  const computedOperands = new Set(
    next.filter(field => field.defaultRule?.startsWith("computed:")).flatMap(field => operandNames(parseComputedRule(field.defaultRule || "") || ""))
  );
  next = next.map(field => {
    if (field.source === "caller" || field.defaultRule || PAGE_NAME.test(field.name)) return field;
    if (!computedOperands.has(field.name)) return field;
    if (field.source === "binding") return field;
    return asCallerInput(field);
  });
  next = applyComputed(next, sample, capability);

  next = next.map(field => {
    if (shouldKeep(field, capability) || field.defaultRule) return field;
    const copied = uniqueCopySource(field, next, sample);
    return copied ? asCopy(field, copied) : field;
  });
  next = applyComputed(next, sample, capability);

  next = next.map(field => {
    if (shouldKeep(field, capability) || field.defaultRule) return field;
    const value = requestValueAt(sample, field.path);
    const generated = generatedRule(field, value);
    if (generated) return asGenerated(field, generated.rule, generated.detail);
    if (looksInvariantConstant(field, value) && value !== undefined && value !== null && value !== "") {
      return asDefault(field, `literal:${value}`, `请求中观察到的系统常量 ${value}，按该值补齐，调用方不要手填`);
    }
    const rule = emptyDefault(value);
    if (rule) return asDefault(field, rule, `系统默认空值 ${rule.slice("literal:".length)}，调用方未提供时使用，不是某次录制的业务样本`);
    return unexplained(field);
  });

  return { fields: next, bindings };
}

export function attachCatalogDerivations(capabilities: CapabilityContract[], events: EvidenceEvent[]) {
  return capabilities.map(capability => {
    if (!WRITE_OPERATIONS.has(capability.operation)) return capability;
    const sample = evidenceSample(capability, events);
    if (!sample) return capability;
    const derived = attachDerivationRules(capability.inputForm, sample, events, capabilities, capability);
    const existing = new Set(capability.bindings.map(item => `${item.fromCapabilityId}|${item.fromPath}|${item.toPath}`));
    return {
      ...capability,
      inputForm: derived.fields,
      bindings: [
        ...capability.bindings,
        ...derived.bindings.filter(item => !existing.has(`${item.fromCapabilityId}|${item.fromPath}|${item.toPath}`))
      ]
    };
  });
}
