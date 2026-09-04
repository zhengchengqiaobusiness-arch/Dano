import type { CapabilityContract, DataBinding, EvidenceEvent, InputFormField, NetworkEvidence } from "../domain.js";
import { id } from "../utils.js";
import { flattenRequestValues, nameTokens, requestValueAt, sameSynonymGroup, sameValue } from "./field-resolver.js";
import { normalizeUrl } from "./heuristics.js";
import { isNoiseCapability, isPageResultQuery, isPrimaryCapability, relatedResource } from "./export-scope.js";

const WRITE_OPERATIONS = new Set(["create", "update", "review", "delete", "upload", "action"]);
const PAGE_NAME = /^(pageNo|pageSize|pageNum|page|size|current|offset|limit)$/i;
const PERCENT_NAME = /percent|rate|比率|税率|优惠率|折扣/i;
const QUANTITY_NAME = /count|qty|quantity|price|amount|total|percent|rate|tax|discount|deposit|day|days|hour|stock|sum|数量|金额|单价|税率|优惠|订金|天数|库存|合计|余额/i;
const IDENTIFIER_NAME = /(?:Id|Ids|Key|Type|Status|Code)$|(?:^|[^A-Za-z])(id|type|status|code|key|userId|accountId|supplierId|creator|deptId)(?:$|[^A-Za-z])/i;
const DURATION_NAME = /(?:^|[^a-z])(day|days|hour|hours|duration)(?:$|[^a-z])|天数|小时|时长/i;
const ENVELOPE_LEAF = /\.(success|ok|msg|message|error|errmsg)$/i;
const UUID_VALUE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const ISO_VALUE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/;
const EXECUTABLE_RULE = /^(literal:.+|env:[A-Za-z_][A-Za-z0-9_]*|uuid|now:iso|from:[^|]+(?:\|via:[A-Za-z_][A-Za-z0-9_]*)?|computed:.+|copy:[A-Za-z_][A-Za-z0-9_]*)$/;
const EPOCH_MS_MIN = 1e11;
const EPOCH_MS_MAX = 2e13;

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
  for (const key of ["id", "value", "code", "key"]) {
    const value = row[key];
    if (value !== undefined && value !== null && value !== "") return value;
  }
  return undefined;
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

export function isIdentifierOperandName(name: string) {
  return IDENTIFIER_NAME.test(name) && !QUANTITY_NAME.test(name);
}

export function isEpochMs(value: number) {
  return Number.isFinite(value) && value > EPOCH_MS_MIN && value < EPOCH_MS_MAX;
}

function isChoiceOperand(field?: InputFormField) {
  return Boolean(field && (field.candidates || field.widget === "select" || field.widget === "multiselect"));
}

function usableArithmeticOperand(entry: { name: string; value: number }, fields: InputFormField[] = []) {
  if (!Number.isFinite(entry.value) || isEpochMs(entry.value)) return false;
  if (isIdentifierOperandName(entry.name)) return false;
  return !isChoiceOperand(fields.find(field => field.name === entry.name));
}

function relatedFormula(targetName: string, ...names: string[]) {
  return names.some(name => formulaScore(targetName, name) >= 3 || QUANTITY_NAME.test(name) && QUANTITY_NAME.test(targetName));
}

function uniqueFormula(targetName: string, hits: string[]) {
  const unique = [...new Set(hits)];
  if (!unique.length) return undefined;
  if (unique.length === 1) return unique[0];
  const ranked = unique.slice().sort((left, right) => {
    const score = formulaScore(targetName, right) - formulaScore(targetName, left);
    if (score) return score;
    if (left.length !== right.length) return left.length - right.length;
    return left.localeCompare(right);
  });
  const topScore = formulaScore(targetName, ranked[0]!);
  const top = ranked.filter(item => formulaScore(targetName, item) === topScore);
  if (topScore === 0) return undefined;
  return top.sort((left, right) => left.length - right.length || left.localeCompare(right))[0];
}

function binaryFormulas(targetName: string, target: number, others: Array<{ name: string; value: number }>) {
  const hits: string[] = [];
  for (const left of others) {
    for (const right of others) {
      if (left.name === right.name) continue;
      const percentRight = PERCENT_NAME.test(right.name);
      const percentLeft = PERCENT_NAME.test(left.name);
      if (!percentLeft && !percentRight && near(target, left.value * right.value)) hits.push(commute("*", left.name, right.name));
      if (near(target, left.value + right.value)) hits.push(commute("+", left.name, right.name));
      if (near(target, left.value - right.value)) hits.push(`${left.name} - ${right.name}`);
      if (!percentRight && right.value !== 0 && near(target, left.value / right.value) && Math.abs(target) > 0.005) {
        if (relatedFormula(targetName, left.name, right.name) || /rate|ratio|percent|平均/.test(targetName)) {
          hits.push(`${left.name} / ${right.name}`);
        }
      }
      if (percentRight && near(target, left.value * right.value / 100)) hits.push(`${left.name} * ${right.name} / 100`);
      if (percentRight && near(target, left.value * (1 - right.value / 100))) hits.push(`${left.name} * (1 - ${right.name} / 100)`);
      if (percentRight && near(target, left.value * (1 + right.value / 100))) hits.push(`${left.name} * (1 + ${right.name} / 100)`);
    }
  }
  return hits;
}

function splitCamel(name: string) {
  return String(name || "").replace(/([a-z])([A-Z])/g, "$1 $2");
}

function actualFamily(name: string) {
  return /actual|实际/i.test(name);
}

function looksDurationName(name: string) {
  return DURATION_NAME.test(name) || DURATION_NAME.test(splitCamel(name));
}

function timeRole(name: string): "start" | "end" | "other" {
  const text = splitCamel(name);
  if (/(^|[^a-z])(start|begin|from)([^a-z]|$)|开始/i.test(text)) return "start";
  if (/(^|[^a-z])(end|to|until|expire)([^a-z]|$)|结束|截止/i.test(text)) return "end";
  return "other";
}

function durationPairScore(targetName: string, startName: string, endName: string) {
  let score = 0;
  if (actualFamily(targetName) === actualFamily(startName) && actualFamily(startName) === actualFamily(endName)) score += 5;
  if (timeRole(startName) === "start" && timeRole(endName) === "end") score += 3;
  return score;
}

function durationFormulas(targetName: string, target: number, others: Array<{ name: string; value: number }>) {
  if (!looksDurationName(targetName)) return [];
  const times = others.filter(item => isEpochMs(item.value) || /time|date|At$/i.test(item.name));
  const targetActual = actualFamily(targetName);
  const sameFamily = times.filter(item => actualFamily(item.name) === targetActual);
  const pool = sameFamily.length >= 2 ? sameFamily : [];
  const hits: Array<{ expr: string; score: number }> = [];
  for (const start of pool) {
    for (const end of pool) {
      if (start.name === end.name || end.value <= start.value) continue;
      if (actualFamily(start.name) !== actualFamily(end.name)) continue;
      if (timeRole(start.name) === "end" || timeRole(end.name) === "start") continue;
      const days = (end.value - start.value) / 86400000;
      const score = durationPairScore(targetName, start.name, end.name);
      if (near(target, days) || near(target, Math.ceil(days - 1e-9)) || near(target, Math.max(0, Math.round(days)))) {
        hits.push({ expr: `(${end.name} - ${start.name}) / 86400000`, score });
      }
      if (/hour|小时/.test(targetName) && near(target, (end.value - start.value) / 3600000)) {
        hits.push({ expr: `(${end.name} - ${start.name}) / 3600000`, score });
      }
    }
  }
  if (!hits.length) return [];
  const best = Math.max(...hits.map(item => item.score));
  const top = [...new Set(hits.filter(item => item.score === best).map(item => item.expr))];
  return top.length === 1 ? top : [];
}

function inferredFormula(targetName: string, target: number, others: Array<{ name: string; value: number }>) {
  const usable = others.filter(item => item.name !== targetName);
  return uniqueFormula(targetName, [
    ...binaryFormulas(targetName, target, usable.filter(item => !isEpochMs(item.value) && !isIdentifierOperandName(item.name))),
    ...durationFormulas(targetName, target, usable)
  ]);
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

function lastPathName(path: string) {
  return (path.split(".").pop() || "").replace(/\[\*\]/g, "");
}

function schemaLeafPaths(schema: CapabilityContract["outputSchema"], prefix = "$"): string[] {
  if (!schema) return [];
  const type = Array.isArray(schema.type) ? schema.type.find(item => item !== "null") : schema.type;
  if (type === "object" && schema.properties) {
    return Object.entries(schema.properties).flatMap(([key, child]) =>
      schemaLeafPaths(child as CapabilityContract["outputSchema"], `${prefix}.${key}`)
    );
  }
  if (type === "array") {
    return schemaLeafPaths((schema.items || {}) as CapabilityContract["outputSchema"], `${prefix}[*]`);
  }
  return type && type !== "object" ? [prefix] : [];
}

const GENERIC_LEAF = /^(data|result|value|item|items|record|records|rows|list|content)$/i;

function namesRelated(field: InputFormField, leaf: string, pathText: string) {
  if (sameSynonymGroup(field, { name: leaf, label: leaf })) return true;
  return sameSynonymGroup({ name: field.name, label: field.name }, { name: leaf, label: pathText });
}

function lookupAffinityScore(
  field: InputFormField,
  leafPath: string,
  capability: CapabilityContract,
  write?: CapabilityContract
) {
  const leaf = lastPathName(leafPath);
  const fieldText = `${field.name} ${field.label}`;
  const pathText = capability.transport.pathTemplate || "";
  const exact = leaf.toLowerCase() === field.name.toLowerCase();
  const synonym = !exact && namesRelated(field, leaf, pathText);
  const nameScore = exact ? 8 : synonym ? 5 : 0;
  const fieldToks = new Set(nameTokens(fieldText));
  const overlap = nameTokens(`${leaf} ${pathText}`).filter(token =>
    fieldToks.has(token) || [...fieldToks].some(item => item.includes(token) || token.includes(item))
  );
  const tokenScore = Math.min(6, new Set(overlap).size * 2);
  const related = Boolean(write && relatedResource(write.transport.pathTemplate, pathText));
  if (!nameScore && (!tokenScore || !related)) return 0;
  return nameScore
    + tokenScore
    + (related ? 3 : 0)
    + (leafPath.includes("[*]") ? 0 : 2)
    - (GENERIC_LEAF.test(leaf) ? 4 : 0);
}

function pickUniqueHit(hits: LookupHit[]) {
  const unique = [...new Map(hits.map(item => [`${item.capabilityId}|${item.fromPath}|${item.via || ""}`, item])).values()]
    .sort((left, right) => right.score - left.score || left.fromPath.localeCompare(right.fromPath));
  if (!unique.length || unique[0]!.score < 6) return undefined;
  if (unique.length > 1 && unique[0]!.score - unique[1]!.score < 2) return undefined;
  return unique[0];
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

function isJoinFieldName(name: string) {
  return IDENTIFIER_NAME.test(name) && !QUANTITY_NAME.test(name);
}

function pickVia(field: InputFormField, joins: ReturnType<typeof requestJoins>, row: Record<string, unknown> | undefined) {
  const usable = joins.filter(item => item.name !== field.name && isJoinFieldName(item.name));
  if (!row) {
    return usable.find(item => fieldScope(field.path) === item.scope) || usable[0];
  }
  const identity = rowIdentity(row);
  const matched = usable.filter(item => sameValue(item.value, identity));
  if (!matched.length) return undefined;
  return matched.find(item => fieldScope(item.path) === fieldScope(field.path)) || matched[0];
}

function requestContainsFieldValue(event: NetworkEvidence, fieldName: string, value: unknown) {
  const body = event.request.body;
  const params = {
    ...(event.request.query || {}),
    ...(body && typeof body === "object" && !Array.isArray(body) ? body as Record<string, unknown> : {})
  };
  return Object.entries(params).some(([key, item]) => key === fieldName && sameValue(item, value));
}

function isDistinctiveJoinValue(value: unknown) {
  if (typeof value === "string") return value.trim().length >= 8;
  if (typeof value === "number" && Number.isFinite(value)) return Math.abs(value) >= 1_000_000;
  return false;
}

function requestSelectsValue(event: NetworkEvidence, fieldName: string, value: unknown) {
  if (requestContainsFieldValue(event, fieldName, value)) return true;
  // Ambiguous values like 0/1 must share the field name; long ids may appear under a join key.
  if (!isDistinctiveJoinValue(value)) return false;
  const body = event.request.body;
  const params = {
    ...(event.request.query || {}),
    ...(body && typeof body === "object" && !Array.isArray(body) ? body as Record<string, unknown> : {})
  };
  return Object.values(params).some(item => sameValue(item, value));
}

type IndexedLeaf = { path: string; value: unknown; row?: Record<string, unknown> };

type LookupIndexEntry = {
  event?: NetworkEvidence;
  evidenceIds: string[];
  capability: CapabilityContract;
  leaves: IndexedLeaf[];
  leavesByValue: Map<string, IndexedLeaf[]>;
  isPageQuery: boolean;
  isPrimary: boolean;
};

type LookupHit = {
  capabilityId: string;
  fromPath: string;
  via?: string;
  eventId: string;
  method: string;
  pathTemplate: string;
  score: number;
};

function primitiveValueKey(value: unknown) {
  const type = typeof value;
  if (type === "string" || type === "number" || type === "boolean") return `${type}:${String(value)}`;
  return undefined;
}

function dayValueKey(value: unknown) {
  if (typeof value === "number" && value >= EPOCH_MS_MIN && value <= EPOCH_MS_MAX) {
    return `day:${new Date(value).toISOString().slice(0, 10)}`;
  }
  if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}/.test(value)) return `day:${value.slice(0, 10)}`;
  return undefined;
}

function lookupValueKeys(value: unknown) {
  const keys = new Set<string>();
  const primitive = primitiveValueKey(value);
  if (primitive) keys.add(primitive);
  const day = dayValueKey(value);
  if (day) keys.add(day);
  if (typeof value === "number" && Number.isFinite(value)) keys.add(`string:${value}`);
  if (typeof value === "string" && value !== "" && Number.isFinite(Number(value))) keys.add(`number:${Number(value)}`);
  if (value === true) {
    keys.add("number:1");
    keys.add("string:true");
  }
  if (value === false) {
    keys.add("number:0");
    keys.add("string:false");
  }
  return [...keys];
}

function addLeafKey(index: Map<string, IndexedLeaf[]>, key: string | undefined, leaf: IndexedLeaf) {
  if (!key) return;
  const bucket = index.get(key);
  if (bucket) bucket.push(leaf);
  else index.set(key, [leaf]);
}

function matchingLeaves(entry: LookupIndexEntry, value: unknown) {
  const keys = lookupValueKeys(value);
  if (!keys.length) return entry.leaves;
  const seen = new Set<IndexedLeaf>();
  const matched: IndexedLeaf[] = [];
  for (const key of keys) {
    for (const leaf of entry.leavesByValue.get(key) || []) {
      if (seen.has(leaf)) continue;
      seen.add(leaf);
      matched.push(leaf);
    }
  }
  return matched;
}

function buildLookupIndex(events: EvidenceEvent[], catalog: CapabilityContract[]) {
  const primaryCache = new Map<string, boolean>();
  const pageQueryCache = new Map<string, boolean>();
  const isPrimary = (capability: CapabilityContract) => {
    const cached = primaryCache.get(capability.id);
    if (cached !== undefined) return cached;
    const value = isPrimaryCapability(capability, catalog);
    primaryCache.set(capability.id, value);
    return value;
  };
  const isPageQuery = (capability: CapabilityContract) => {
    const cached = pageQueryCache.get(capability.id);
    if (cached !== undefined) return cached;
    const value = isPageResultQuery(capability);
    pageQueryCache.set(capability.id, value);
    return value;
  };
  const index: LookupIndexEntry[] = [];
  for (const event of events) {
    if (event.kind !== "network" || !event.response || event.response.status < 200 || event.response.status >= 400) continue;
    const capability = capabilityForEvent(event, catalog);
    if (!capability) continue;
    const leaves = responseHits(event.response.body);
    const leavesByValue = new Map<string, IndexedLeaf[]>();
    for (const leaf of leaves) {
      addLeafKey(leavesByValue, primitiveValueKey(leaf.value), leaf);
      addLeafKey(leavesByValue, dayValueKey(leaf.value), leaf);
    }
    index.push({
      event,
      evidenceIds: [event.id],
      capability,
      leaves,
      leavesByValue,
      isPageQuery: isPageQuery(capability),
      isPrimary: isPrimary(capability)
    });
  }
  const indexed = new Set(index.map(entry => entry.capability.id));
  for (const capability of catalog) {
    if (capability.operation !== "query" || isNoiseCapability(capability) || indexed.has(capability.id)) continue;
    if (isPageQuery(capability) && isPrimary(capability)) continue;
    const paths = schemaLeafPaths(capability.outputSchema);
    if (!paths.length) continue;
    const evidenceIds = capability.evidence.filter(item => item.kind === "network").map(item => item.eventId);
    index.push({
      evidenceIds,
      capability,
      leaves: paths.map(path => ({ path, value: undefined })),
      leavesByValue: new Map(),
      isPageQuery: isPageQuery(capability),
      isPrimary: isPrimary(capability)
    });
    indexed.add(capability.id);
  }
  return index;
}

function fromApiMatch(
  field: InputFormField,
  value: unknown,
  sample: unknown,
  index: LookupIndexEntry[],
  write?: CapabilityContract,
  mode: "write" | "query" = "write"
) {
  if (value === undefined || value === null || value === "") return undefined;
  const joins = requestJoins(sample);
  const valueHits: LookupHit[] = [];
  for (const entry of index) {
    const { event, capability } = entry;
    if (write && capability.id === write.id) continue;
    for (const leaf of matchingLeaves(entry, value)) {
      if (isEnvelopePath(leaf.path, field.name)) continue;
      if (leaf.value !== undefined && !sameDerivedValue(leaf.value, value)) continue;
      if (leaf.value === undefined) continue;
      // The page's own result list often repeats the field we just wrote.
      // Only keep it when that value was also a same-named filter on the list request.
      if (entry.isPageQuery && entry.isPrimary && (!event || !requestSelectsValue(event, field.name, value))) continue;
      if (entry.isPageQuery && entry.isPrimary && lastPathName(leaf.path).toLowerCase() === field.name.toLowerCase() && (!event || !requestSelectsValue(event, field.name, value))) continue;
      const query = event?.request.query || {};
      const queryJoin = joins.find(item =>
        Object.entries(query).some(([key, queryValue]) => key === item.name && sameValue(queryValue, item.value))
      );
      const via = leaf.row ? pickVia(field, joins, leaf.row) : queryJoin;
      if (leaf.row && !via) {
        const siblings = entry.leaves.filter(item => item.path === leaf.path);
        if (!siblings.length || siblings.some(item => !sameDerivedValue(item.value, value))) continue;
      }
      const score = lookupAffinityScore(field, leaf.path, capability, write) + (via ? 2 : 0) + 4;
      valueHits.push({
        capabilityId: capability.id,
        fromPath: leaf.path,
        via: via?.name,
        eventId: event?.id || entry.evidenceIds[0] || capability.id,
        method: capability.transport.method,
        pathTemplate: capability.transport.pathTemplate,
        score
      });
    }
  }
  const unique = [...new Map(valueHits.map(item => [`${item.capabilityId}|${item.fromPath}|${item.via || ""}`, item])).values()];
  const viaHits = unique.filter(item => item.via);
  const chosen = viaHits.length ? viaHits : unique;
  if (chosen.length === 1) return chosen[0];
  if (chosen.length > 1 || mode !== "write") return undefined;

  const affinityHits: LookupHit[] = [];
  for (const entry of index) {
    if (write && entry.capability.id === write.id) continue;
    if (entry.isPageQuery && entry.isPrimary) continue;
    for (const leaf of entry.leaves) {
      if (isEnvelopePath(leaf.path, field.name)) continue;
      const score = lookupAffinityScore(field, leaf.path, entry.capability, write);
      if (score < 6) continue;
      const query = entry.event?.request.query || {};
      const queryJoin = joins.find(item =>
        Object.entries(query).some(([key, queryValue]) => key === item.name && sameValue(queryValue, item.value))
      );
      affinityHits.push({
        capabilityId: entry.capability.id,
        fromPath: leaf.path,
        via: queryJoin?.name,
        eventId: entry.event?.id || entry.evidenceIds[0] || entry.capability.id,
        method: entry.capability.transport.method,
        pathTemplate: entry.capability.transport.pathTemplate,
        score: score + (queryJoin ? 2 : 0)
      });
    }
  }
  return pickUniqueHit([...valueHits, ...affinityHits]);
}

function emptyDefault(field: InputFormField, value: unknown) {
  if (/balance|stock|库存|余额/i.test(`${field.name} ${field.label}`)) return undefined;
  if (field.sourceDetail?.includes("只读")) return undefined;
  if (Array.isArray(value) && value.length === 0) return "literal:[]";
  if (value === "") return "literal:\"\"";
  if (value === 0) return "literal:0";
  if (value === false) return "literal:false";
  return undefined;
}

function looksInvariantConstant(field: InputFormField, value: unknown) {
  if (typeof value !== "string" || /^[a-f0-9]{16,}$/i.test(value)) return false;
  return /^[a-z][a-z0-9_]*$/i.test(value) && /Type|Key|Code|Def/i.test(field.name);
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

function computedForField(field: InputFormField, sample: unknown, known: Set<string>, fields: InputFormField[]) {
  const value = requestValueAt(sample, field.path);
  if (typeof value !== "number" || !Number.isFinite(value)) return undefined;
  const header = headerRecord(sample);
  const items = itemRecords(sample);
  if (fieldScope(field.path) === "item") {
    const formulas = [...new Set(items.map(item => {
      const others = numericEntries(item).filter(entry => known.has(entry.name) && usableArithmeticOperand(entry, fields));
      return inferredFormula(field.name, Number(item[field.name]), others);
    }).filter(Boolean))];
    return formulas.length === 1 ? formulas[0] : undefined;
  }
  const others = [
    ...numericEntries(header).filter(entry => known.has(entry.name) && (usableArithmeticOperand(entry, fields) || isEpochMs(entry.value))),
    ...headerAggregates(items, known)
  ];
  return inferredFormula(field.name, value, others);
}

function viaLabel(fields: InputFormField[], via?: string) {
  return fields.find(field => field.name === via)?.label || via || "关联字段";
}

export function isAssembledObjectField(field: InputFormField, fields: InputFormField[]) {
  return field.valueType === "object" && fields.some(other =>
    other.path !== field.path && (other.path.startsWith(`${field.path}.`) || other.path.startsWith(`${field.path}[`))
  );
}

function shouldKeep(field: InputFormField, capability: CapabilityContract, fields: InputFormField[] = capability.inputForm) {
  if (field.source === "caller") return true;
  if (PAGE_NAME.test(field.name)) return true;
  if (isAssembledObjectField(field, fields)) return true;
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

function asAssembled(field: InputFormField, children: InputFormField[]): InputFormField {
  const names = children.map(item => item.label || item.name).join("、");
  return {
    ...field,
    source: "computed",
    systemHandled: true,
    required: false,
    defaultRule: undefined,
    sourceDetail: `由子字段「${names}」按路径拼接成对象，调用方不要手填。最终请求键与录制成功请求一致，不增不删`
  };
}

function asCallerOverride(field: InputFormField, expr: string): InputFormField {
  return {
    ...field,
    source: "caller",
    systemHandled: false,
    defaultRule: `computed:${expr}`,
    sourceDetail: `页面按 ${expr} 自动计算，调用方可改。未提供时按公式计算，不要冻成录制样本`
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

export function operandNames(expr: string) {
  return [...new Set((expr.match(/[A-Za-z_][A-Za-z0-9_]*/g) || []).filter(name => name !== "sum" && name !== "items"))];
}

export function unsoundComputedOperands(expr: string, fields: InputFormField[]) {
  return operandNames(expr).filter(name => {
    const field = fields.find(item => item.name === name);
    if (isIdentifierOperandName(name) && field?.widget !== "date") return true;
    if (isChoiceOperand(field) && field?.widget !== "date") return true;
    return false;
  });
}

function knownNames(fields: InputFormField[]) {
  return new Set(
    fields
      .filter(field => field.source === "caller" || Boolean(field.defaultRule) || PAGE_NAME.test(field.name))
      .map(field => field.name)
  );
}

export function evidenceSample(capability: CapabilityContract, events: EvidenceEvent[]) {
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
      if (shouldKeep(field, capability, next) || field.defaultRule) return field;
      const expr = computedForField(field, sample, known, next);
      if (!expr) return field;
      changed = true;
      return asComputed(field, expr);
    });
  }
  return next;
}

function attachCallerOverrides(fields: InputFormField[], sample: unknown) {
  const known = knownNames(fields);
  return fields.map(field => {
    if (field.source !== "caller" || field.defaultRule) return field;
    if (field.candidates || field.widget === "select" || field.widget === "multiselect") return field;
    const expr = computedForField(field, sample, known, fields);
    if (!expr || !looksDurationName(`${field.name} ${field.label}`)) return field;
    return asCallerOverride(field, expr);
  });
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

function asUnresolved(field: InputFormField): InputFormField {
  if (field.sourceDetail?.includes("只读")) return unexplained(field);
  if (/price|单价|售价/i.test(`${field.name} ${field.label}`)) {
    return {
      ...asCallerInput(field),
      sourceDetail: "可改单价未能唯一对应带出或公式，由调用方提供，不要冻成录制样本"
    };
  }
  return unexplained(field);
}

export function attachDerivationRules(
  fields: InputFormField[],
  sample: unknown,
  events: EvidenceEvent[],
  catalog: CapabilityContract[],
  capability: CapabilityContract,
  mode: "write" | "query" = "write",
  index?: LookupIndexEntry[]
): { fields: InputFormField[]; bindings: DataBinding[] } {
  const lookupIndex = index || buildLookupIndex(events, catalog);
  const bindings: DataBinding[] = [];
  let next = fields.map(field => {
    if (isAssembledObjectField(field, fields)) {
      return asAssembled(field, fields.filter(item => item.path.startsWith(`${field.path}.`) || item.path.startsWith(`${field.path}[`)));
    }
    if (shouldKeep(field, capability, fields)) return field;
    if (field.defaultRule && (mode === "write" || !field.defaultRule.startsWith("literal:"))) return field;
    const value = requestValueAt(sample, field.path);
    const from = fromApiMatch(field, value, sample, lookupIndex, capability, mode);
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

  if (mode === "write") {
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
    next = attachCallerOverrides(next, sample);
  }

  next = next.map(field => {
    if (shouldKeep(field, capability, next) || field.defaultRule) return field;
    const copied = uniqueCopySource(field, next, sample);
    return copied ? asCopy(field, copied) : field;
  });
  if (mode === "write") next = applyComputed(next, sample, capability);

  next = next.map(field => {
    if (shouldKeep(field, capability, next) || field.defaultRule) return field;
    const value = requestValueAt(sample, field.path);
    const generated = generatedRule(field, value);
    if (generated) return asGenerated(field, generated.rule, generated.detail);
    if (mode === "query") return field;
    if (looksInvariantConstant(field, value) && value !== undefined && value !== null && value !== "") {
      return asDefault(field, `literal:${value}`, `请求中观察到的系统常量 ${value}，按该值补齐，调用方不要手填`);
    }
    const rule = emptyDefault(field, value);
    if (rule) return asDefault(field, rule, `系统默认空值 ${rule.slice("literal:".length)}，调用方未提供时使用，不是某次录制的业务样本`);
    return asUnresolved(field);
  });

  return { fields: next, bindings };
}

export function attachCatalogDerivations(capabilities: CapabilityContract[], events: EvidenceEvent[]) {
  const pending = capabilities.filter(capability =>
    capability.validation?.status !== "verified"
    && (WRITE_OPERATIONS.has(capability.operation) || capability.operation === "query")
  );
  if (!pending.length) return capabilities;
  const index = buildLookupIndex(events, capabilities);
  return capabilities.map(capability => {
    if (capability.validation?.status === "verified") return capability;
    if (!WRITE_OPERATIONS.has(capability.operation) && capability.operation !== "query") return capability;
    const sample = evidenceSample(capability, events);
    if (!sample) return capability;
    const derived = attachDerivationRules(
      capability.inputForm,
      sample,
      events,
      capabilities,
      capability,
      WRITE_OPERATIONS.has(capability.operation) ? "write" : "query",
      index
    );
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
