import type { CapabilityContract, EvidenceEvent } from "../domain.js";
import { ASK_KEY } from "./heuristics.js";
import { isPaginationField, pickerEntity } from "./field-resolver.js";

const NOISE_PATH = /\/im\/|notify-message|unread-count|online-status|get-permission-info|captcha|tenant\/get-by-website|tenant\/get-id-by-name|\/user\/get-current$|\/auth\/login|\/auth\/logout|process-instance|process-definition/i;

export function isNoiseCapability(capability: CapabilityContract) {
  if (capability.operation === "authenticate") return true;
  return NOISE_PATH.test(capability.transport.pathTemplate || capability.transport.urlTemplate);
}

export function hasBusinessCallerField(capability: CapabilityContract) {
  return capability.inputForm.some(field => field.source === "caller" && !isPaginationField(field.name));
}

const WRITE_OPERATIONS = new Set(["create", "update", "review", "delete", "upload", "action"]);
const LOOKUP_QUERY = /simple-list|dict-data|\/enum(?:\/|$)|get-count|get-current|getappid|get-app-id|getAppId|save_[\w-]*chat/i;
const PICKER_PAGE = /\/(?:user|dept|department|role|post|tenant|dict)\/(?:page|list)$/i;
const PAGE_QUERY = /\/page$/i;
const LIST_QUERY = /\/(?:list|search|query)$/i;
const DETAIL_QUERY = /\/(?:get|detail|info)(?:[-_/].*)?$/i;

const OPERATION_SEGMENT = /^(page|list|search|query|find|create|update|delete|save|submit|export|import|detail|info|count|get|add|edit|remove|complete|enable|disable|statistics|statistic|stats|summary|overview|analyse|analyze)(?:[-_].+)?$/i;
const SUMMARY_QUERY = /\/(?:statistics|statistic|stats|summary|overview|analyse|analyze|dashboard)$/i;
const OPERATION_PREFIX = /^(get|create|update|delete|save|submit|query|list|find|add|edit|remove|enable|disable|complete)/i;
const OPERATION_SUFFIX = /(?:List|Page|Search|Query|Find|Detail|Info|Count|Process|ById)$/i;
const DIRECTORY_LOOKUP = /\/(?:user|dept|department|role|post)\/(?:page|list|simple-list)$/i;
const PAGE_ROLE_LABELS: Record<string, string> = {
  page: "列表",
  list: "列表",
  search: "列表",
  query: "列表",
  statistics: "统计",
  statistic: "统计",
  stats: "统计",
  summary: "统计",
  overview: "统计",
  analyse: "统计",
  analyze: "统计",
  dashboard: "统计",
  get: "详情",
  detail: "详情",
  info: "详情",
  export: "导出",
  import: "导入"
};

function pathPickerEntity(path: string) {
  if (/\/(?:dept|department)(?:\/|$)/i.test(path)) return "dept";
  if (/\/role(?:\/|$)/i.test(path)) return "role";
  if (/\/post(?:\/|$)/i.test(path)) return "post";
  if (/\/user(?:\/|$)/i.test(path)) return "user";
  return undefined;
}

export function directoryLookupEntity(pathTemplate: string) {
  const path = pathTemplate || "";
  return DIRECTORY_LOOKUP.test(path) ? pathPickerEntity(path) : undefined;
}

export function pageRoleLabel(pathTemplate: string, pageUrl = "") {
  const path = pathTemplate || "";
  const haystack = `${path} ${pageUrl}`;
  if (SUMMARY_QUERY.test(path) || /statistic|stats|summary|overview|dashboard|analyse|analyze/.test(haystack)) {
    return "统计";
  }
  if (DETAIL_QUERY.test(path)) return "详情";
  if (PAGE_QUERY.test(path) || LIST_QUERY.test(path) || /(?:list|search|query)(?:[-_/]|$)/i.test(pageUrl)) {
    return "列表";
  }
  return PAGE_ROLE_LABELS[lastSegment(path).toLowerCase()];
}

export function resourcePrefix(pathTemplate: string) {
  const parts = (pathTemplate || "").split("/").filter(Boolean);
  if (parts.length < 2) return "";
  return `/${parts.slice(0, -1).join("/")}`;
}

function isOperationSegment(segment: string) {
  return OPERATION_SEGMENT.test(segment) || OPERATION_PREFIX.test(segment) || OPERATION_SUFFIX.test(segment);
}

function resourceStem(pathTemplate: string) {
  const parts = (pathTemplate || "").split("/").filter(Boolean);
  const last = parts[parts.length - 1] || "";
  const parent = parts[parts.length - 2] || "";
  const stem = last.replace(OPERATION_PREFIX, "").replace(OPERATION_SUFFIX, "").replace(/^[-_]+|[-_]+$/g, "");
  const prefix = resourcePrefix(pathTemplate).toLowerCase();
  if (stem.length < 3) return prefix;
  if (parent.toLowerCase() === stem.toLowerCase()) return prefix;
  return `${prefix}/${stem}`.toLowerCase();
}

export function sameResource(left: string, right: string) {
  const prefix = resourcePrefix(left);
  if (prefix && prefix === resourcePrefix(right)) {
    const lastLeft = lastSegment(left);
    const lastRight = lastSegment(right);
    if (isOperationSegment(lastLeft) || isOperationSegment(lastRight)) return true;
    return lastLeft.toLowerCase() === lastRight.toLowerCase();
  }
  const stem = resourceStem(left);
  return Boolean(stem) && stem === resourceStem(right);
}

const GENERIC_SEGMENT = /^(api|admin-api|app|appgateway|gateway|open|inner|public|erp|oa|system|bpm|admin|backend|service|services|v\d+(?:\.\d+)*)$/i;

export function resourceTokens(pathTemplate: string) {
  return (pathTemplate || "").split("/").filter(Boolean)
    .filter(part => !GENERIC_SEGMENT.test(part) && !isOperationSegment(part));
}

export function relatedResource(left: string, right: string) {
  if (sameResource(left, right)) return true;
  const leftTokens = resourceTokens(left);
  const rightTokens = resourceTokens(right);
  const leftHead = leftTokens[0]?.toLowerCase();
  const rightHead = rightTokens[0]?.toLowerCase();
  return Boolean(leftHead && leftHead.length >= 3 && leftHead === rightHead);
}

export function isBroughtOutLookup(capability: CapabilityContract) {
  if (capability.operation !== "query") return false;
  if (isNoiseCapability(capability)) return false;
  if (isPageResultQuery(capability) || isAskQuery(capability)) return false;
  return true;
}

function transportKey(capability: CapabilityContract) {
  return `${capability.transport.method}|${capability.transport.pathTemplate}`;
}

export function relatedLookupCapabilities(catalog: CapabilityContract[], scoped: CapabilityContract[]) {
  const have = new Set(scoped.map(transportKey));
  const writes = scoped.filter(item => WRITE_OPERATIONS.has(item.operation) && !isNoiseCapability(item));
  const fromWrites = writes.length
    ? catalog.filter(item => {
      if (have.has(transportKey(item))) return false;
      if (!isBroughtOutLookup(item)) return false;
      return writes.some(write => relatedResource(write.transport.pathTemplate, item.transport.pathTemplate));
    })
    : [];
  const needed = new Set(
    scoped.flatMap(capability =>
      capability.inputForm
        .filter(field => field.source === "caller")
        .map(field => pickerEntity(field))
        .filter((entity): entity is NonNullable<ReturnType<typeof pickerEntity>> => Boolean(entity))
    )
  );
  const origins = new Set(scoped.map(item => item.transport.origin).filter(Boolean));
  const fromPickers = needed.size
    ? catalog.filter(item => {
      if (have.has(transportKey(item))) return false;
      if (item.operation !== "query" || isNoiseCapability(item)) return false;
      if (item.transport.origin && origins.size && !origins.has(item.transport.origin)) return false;
      const path = item.transport.pathTemplate || "";
      const entity = pathPickerEntity(path);
      return Boolean(entity && needed.has(entity) && DIRECTORY_LOOKUP.test(path));
    })
    : [];
  const seen = new Set<string>();
  return [...fromWrites, ...fromPickers].filter(item => {
    const key = transportKey(item);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function isLookupQueryPath(pathTemplate: string) {
  return LOOKUP_QUERY.test(pathTemplate || "");
}

function lastSegment(pathTemplate: string) {
  return (pathTemplate || "").split("/").filter(Boolean).pop() || "";
}

function schemaHasRecordArray(schema: any): boolean {
  if (!schema) return false;
  const type = Array.isArray(schema.type) ? schema.type.find((item: string) => item !== "null") : schema.type;
  if (type === "array") {
    const items = schema.items;
    if (!items) return true;
    const itemType = Array.isArray(items.type) ? items.type.find((item: string) => item !== "null") : items.type;
    return itemType === "object" || Boolean(items.properties);
  }
  if (schema.properties) return Object.values(schema.properties).some(child => schemaHasRecordArray(child));
  return false;
}

const LIST_SHAPED = /(?:list|page|search|query|find)$/i;

export function isAskFieldName(name?: string) {
  return Boolean(name && ASK_KEY.test(name));
}

export function isAskQuery(capability: CapabilityContract) {
  if (capability.operation !== "query") return false;
  if (isNoiseCapability(capability)) return false;
  const path = capability.transport.pathTemplate || capability.transport.urlTemplate || "";
  if (/\/im\//i.test(path) || isLookupQueryPath(path)) return false;
  if (capability.inputForm.some(field => isAskFieldName(field.name))) return true;
  return /(?:chat|ask|qa)$/i.test(lastSegment(path)) && hasBusinessCallerField(capability);
}

export function isPageResultQuery(capability: CapabilityContract) {
  if (capability.operation !== "query") return false;
  if (isNoiseCapability(capability)) return false;
  const path = capability.transport.pathTemplate || capability.transport.urlTemplate || "";
  if (isLookupQueryPath(path)) return false;
  if (isAskQuery(capability)) return true;
  const last = lastSegment(path);
  if (/^save/i.test(last) && !schemaHasRecordArray(capability.outputSchema)) return false;
  if (PAGE_QUERY.test(path) || LIST_QUERY.test(path)) return true;
  const paging = capability.inputForm.some(field => isPaginationField(field.name));
  const collection = schemaHasRecordArray(capability.outputSchema);
  const hasBusinessFilter = capability.inputForm.some(field => !isPaginationField(field.name));
  if (SUMMARY_QUERY.test(path) && (hasBusinessFilter || collection || paging || hasBusinessCallerField(capability))) return true;
  if (LIST_SHAPED.test(last)) return collection || paging;
  return collection && (paging || hasBusinessCallerField(capability));
}

function isWriteCapability(capability: CapabilityContract) {
  return WRITE_OPERATIONS.has(capability.operation) && !isNoiseCapability(capability);
}

function pageQueries(catalog: CapabilityContract[]) {
  return catalog.filter(item =>
    item.operation === "query"
    && !isNoiseCapability(item)
    && !isLookupQueryPath(item.transport.pathTemplate || "")
    && PAGE_QUERY.test(item.transport.pathTemplate || "")
  );
}

export function isBusinessEvidenceEvent(event: EvidenceEvent) {
  if (event.kind === "network") {
    const url = event.request?.url || "";
    if (NOISE_PATH.test(url)) return false;
    const type = event.request?.resourceType || "";
    if (type && !/xhr|fetch|websocket/i.test(type)) return false;
    return true;
  }
  return Boolean(event.form?.some(field => field.name || field.label));
}

export function sessionBusinessPageKeys(events: EvidenceEvent[], startUrl?: string) {
  const pages = new Set<string>();
  for (const event of events) {
    const page = evidencePageKey(event.pageUrl);
    if (page && isBusinessEvidenceEvent(event)) pages.add(page);
  }
  if (!pages.size) {
    const last = [...events].reverse().find(event => event.pageUrl);
    if (last?.pageUrl) pages.add(evidencePageKey(last.pageUrl));
    else if (startUrl) pages.add(evidencePageKey(startUrl));
  }
  return [...pages];
}

export function reviewSessionIds(
  sessions: Array<{ id: string; startUrl?: string; pageKeys?: string[] }>,
  currentId: string,
  _sessionEvents: EvidenceEvent[] = []
) {
  void sessions;
  return new Set<string>(currentId ? [currentId] : []);
}

export function isPrimaryCapability(capability: CapabilityContract, catalog: CapabilityContract[] = []) {
  if (isNoiseCapability(capability)) return false;
  if (capability.operation === "download") return true;
  if (isWriteCapability(capability)) return true;
  if (capability.operation !== "query") return false;
  const path = capability.transport.pathTemplate || capability.transport.urlTemplate || "";
  if (isLookupQueryPath(path)) return false;
  const writes = catalog.filter(isWriteCapability);
  const asks = catalog.filter(item => isAskQuery(item) && !isLookupQueryPath(item.transport.pathTemplate || ""));
  if (asks.length) {
    if (writes.some(item => sameResource(item.transport.pathTemplate, path))) return true;
    return asks.includes(capability) && (hasBusinessCallerField(capability) || asks.length === 1);
  }
  if (writes.length) {
    if (writes.some(item => sameResource(item.transport.pathTemplate, path))) {
      // A detail reload immediately after save is supporting evidence for the
      // write result, not another user-facing "query" ability. A standalone
      // detail recording can still be primary when no same-resource write is
      // present in the reviewed slice.
      if (DETAIL_QUERY.test(path)) return false;
      return true;
    }
    const shared = catalog.filter(item =>
      item.operation === "query"
      && !isLookupQueryPath(item.transport.pathTemplate || "")
      && writes.some(write => sameResource(write.transport.pathTemplate, item.transport.pathTemplate))
    );
    if (shared.length) return false;
  }
  if (isPageResultQuery(capability)) {
    const otherLists = catalog.filter(item =>
      item !== capability
      && isPageResultQuery(item)
      && !PICKER_PAGE.test(item.transport.pathTemplate || "")
    );
    if (PICKER_PAGE.test(path) && (writes.length || otherLists.length || pageQueries(catalog).some(item => !PICKER_PAGE.test(item.transport.pathTemplate || "")))) {
      return false;
    }
    if (hasBusinessCallerField(capability)) return true;
    if (SUMMARY_QUERY.test(path) && capability.inputForm.some(field => !isPaginationField(field.name))) return true;
    const pageResults = catalog.filter(item => isPageResultQuery(item) && !PICKER_PAGE.test(item.transport.pathTemplate || ""));
    if (pageResults.length === 1) return true;
  }
  if (writes.length) {
    const businessQueries = catalog.filter(item =>
      item.operation === "query" && !isNoiseCapability(item) && !isLookupQueryPath(item.transport.pathTemplate || "")
    );
    return businessQueries.length === 1 || hasBusinessCallerField(capability);
  }
  if (hasBusinessCallerField(capability) && (LIST_QUERY.test(path) || isPageResultQuery(capability))) return true;
  if (PICKER_PAGE.test(path)) return false;
  const businessQueries = catalog.filter(item =>
    item.operation === "query" && !isNoiseCapability(item) && !isLookupQueryPath(item.transport.pathTemplate || "")
  );
  return businessQueries.length === 1;
}

export function isCandidateSourceCapability(capability: CapabilityContract, catalog: CapabilityContract[]) {
  return catalog.some(item =>
    item.inputForm.some(field =>
      (field.candidates?.type === "capability" && field.candidates.capabilityId === capability.id)
      || Boolean(field.defaultRule?.startsWith(`from:${capability.id}:`))
    )
    || item.bindings.some(binding => binding.approved && binding.fromCapabilityId === capability.id)
  );
}

export function matchesExportFilter(capability: CapabilityContract, match: string[] = []) {
  if (!match.length) return true;
  const haystack = `${capability.id} ${capability.title} ${capability.transport.pathTemplate} ${capability.transport.urlTemplate}`;
  return match.some(item => item && haystack.includes(item));
}

export function exportableCapabilities(capabilities: CapabilityContract[], match: string[] = []) {
  const verified = capabilities.filter(capability => capability.validation.status === "verified");
  let primary = verified.filter(capability => isPrimaryCapability(capability, capabilities) && matchesExportFilter(capability, match));
  const writes = primary.filter(isWriteCapability);
  if (writes.length) {
    primary = primary.filter(capability =>
      isWriteCapability(capability)
      || writes.some(write => sameResource(write.transport.pathTemplate, capability.transport.pathTemplate))
    );
  }
  const needed = new Set(primary.map(capability => capability.id));
  for (const capability of primary) {
    for (const field of capability.inputForm) {
      if (field.candidates?.type === "capability") needed.add(field.candidates.capabilityId);
      const from = field.defaultRule?.startsWith("from:") ? field.defaultRule.slice("from:".length).split(":")[0] : undefined;
      if (from) needed.add(from);
    }
    for (const binding of capability.bindings.filter(item => item.approved)) needed.add(binding.fromCapabilityId);
  }
  const selected = verified.filter(capability => needed.has(capability.id) && !isNoiseCapability(capability));
  return selected;
}

export function summarizeCatalog(capabilities: CapabilityContract[]) {
  const primary = capabilities.filter(capability => isPrimaryCapability(capability, capabilities));
  const lookups = capabilities.filter(capability =>
    !primary.includes(capability) && isCandidateSourceCapability(capability, capabilities)
  );
  const noise = capabilities.filter(isNoiseCapability);
  return { primary, lookups, noise };
}

export function evidencePageKey(url?: string) {
  if (!url) return "";
  try {
    const parsed = new URL(url);
    return `${parsed.origin}${parsed.pathname}${parsed.hash.split("?")[0]}`;
  } catch {
    return url;
  }
}

export function capabilitiesForSession(
  catalog: CapabilityContract[],
  _allEvents: EvidenceEvent[],
  sessionEvents: EvidenceEvent[]
) {
  if (!sessionEvents.length) return [];
  const eventIds = new Set(sessionEvents.map(event => event.id));
  const recordingIds = new Set(sessionEvents.map(event => event.sessionId).filter(Boolean));
  return catalog.filter(capability => {
    if (isNoiseCapability(capability)) return false;
    return capability.evidence.some(ref => eventIds.has(ref.eventId) || recordingIds.has(ref.sessionId));
  });
}

function referencedCapabilityIds(capabilities: CapabilityContract[]) {
  const ids = new Set<string>();
  for (const capability of capabilities) {
    ids.add(capability.id);
    for (const field of capability.inputForm) {
      const from = /^from:([^:]+):/.exec(field.defaultRule || "");
      if (from?.[1]) ids.add(from[1]);
      if (field.candidates?.type === "capability") ids.add(field.candidates.capabilityId);
    }
    for (const binding of capability.bindings) ids.add(binding.fromCapabilityId);
  }
  return ids;
}

function hasSessionEvidence(capability: CapabilityContract, sessionEvents: EvidenceEvent[]) {
  const eventIds = new Set(sessionEvents.map(event => event.id));
  const recordingIds = new Set(sessionEvents.map(event => event.sessionId).filter(Boolean));
  return capability.evidence.some(ref => eventIds.has(ref.eventId) || recordingIds.has(ref.sessionId));
}

export function lookupUsableInSession(capability: CapabilityContract, sessionEvents: EvidenceEvent[]) {
  return capability.validation.status === "verified" || hasSessionEvidence(capability, sessionEvents);
}

export function usableRelatedLookups(
  catalog: CapabilityContract[],
  scoped: CapabilityContract[],
  sessionEvents: EvidenceEvent[]
) {
  return relatedLookupCapabilities(catalog, scoped)
    .filter(item => lookupUsableInSession(item, sessionEvents));
}

function lookupRecordedOnSessionPages(
  capability: CapabilityContract,
  allEvents: EvidenceEvent[],
  sessionEvents: EvidenceEvent[]
) {
  const sessionPages = new Set(sessionEvents.map(event => evidencePageKey(event.pageUrl)).filter(Boolean));
  if (!sessionPages.size) return false;
  const eventIds = new Set(capability.evidence.map(ref => ref.eventId));
  return allEvents.some(event => eventIds.has(event.id) && sessionPages.has(evidencePageKey(event.pageUrl)));
}

function sameResourceVerifiedPrimaries(
  catalog: CapabilityContract[],
  scoped: CapabilityContract[]
) {
  if (!scoped.length) return [];
  return catalog.filter(capability => {
    if (scoped.some(item => item.id === capability.id)) return false;
    if (capability.validation.status !== "verified") return false;
    if (isNoiseCapability(capability)) return false;
    if (!isPrimaryCapability(capability, catalog)) return false;
    if (WRITE_OPERATIONS.has(capability.operation)) return false;
    return scoped.some(item => sameResource(item.transport.pathTemplate, capability.transport.pathTemplate));
  });
}

export function sessionCatalogSlice(
  catalog: CapabilityContract[],
  allEvents: EvidenceEvent[],
  sessionEvents: EvidenceEvent[]
) {
  const scoped = capabilitiesForSession(catalog, allEvents, sessionEvents);
  const related = usableRelatedLookups(catalog, scoped, sessionEvents);
  const keptPrimaries = sameResourceVerifiedPrimaries(catalog, scoped);
  const needed = referencedCapabilityIds(scoped);
  for (const item of related) needed.add(item.id);
  for (const item of keptPrimaries) needed.add(item.id);
  const slice = catalog.filter(capability => {
    if (scoped.some(item => item.id === capability.id)) return true;
    if (keptPrimaries.some(item => item.id === capability.id)) return true;
    if (!needed.has(capability.id)) return false;
    return lookupUsableInSession(capability, sessionEvents)
      || lookupRecordedOnSessionPages(capability, allEvents, sessionEvents);
  });
  return slice.length ? slice : scoped;
}
