import type { CapabilityContract } from "../domain.js";
import { isPaginationField } from "./field-resolver.js";

const NOISE_PATH = /\/im\/|notify-message|unread-count|online-status|get-permission-info|captcha|tenant\/get-by-website|tenant\/get-id-by-name|\/user\/get-current$|\/auth\/login|\/auth\/logout/i;

export function isNoiseCapability(capability: CapabilityContract) {
  if (capability.operation === "authenticate") return true;
  return NOISE_PATH.test(capability.transport.pathTemplate || capability.transport.urlTemplate);
}

export function hasBusinessCallerField(capability: CapabilityContract) {
  return capability.inputForm.some(field => field.source === "caller" && !isPaginationField(field.name));
}

const WRITE_OPERATIONS = new Set(["create", "update", "review", "delete", "upload"]);
const LOOKUP_QUERY = /simple-list|dict-data|\/enum(?:\/|$)|get-count|get-current/i;
const PICKER_PAGE = /\/(?:user|dept|department|role|post|tenant|dict)\/(?:page|list)$/i;
const PAGE_QUERY = /\/page$/i;
const LIST_QUERY = /\/(?:list|search|query)$/i;

export function resourcePrefix(pathTemplate: string) {
  const parts = (pathTemplate || "").split("/").filter(Boolean);
  if (parts.length < 2) return "";
  return `/${parts.slice(0, -1).join("/")}`;
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

export function isPageResultQuery(capability: CapabilityContract) {
  if (capability.operation !== "query") return false;
  if (isNoiseCapability(capability)) return false;
  const path = capability.transport.pathTemplate || capability.transport.urlTemplate || "";
  if (isLookupQueryPath(path)) return false;
  if (PAGE_QUERY.test(path) || LIST_QUERY.test(path) || LIST_SHAPED.test(lastSegment(path))) return true;
  const paging = capability.inputForm.some(field => isPaginationField(field.name));
  const collection = schemaHasRecordArray(capability.outputSchema);
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

function sameResource(left: string, right: string) {
  const prefix = resourcePrefix(left);
  return Boolean(prefix) && prefix === resourcePrefix(right);
}

export function isPrimaryCapability(capability: CapabilityContract, catalog: CapabilityContract[] = []) {
  if (isNoiseCapability(capability)) return false;
  if (isWriteCapability(capability)) return true;
  if (capability.operation !== "query") return false;
  const path = capability.transport.pathTemplate || capability.transport.urlTemplate || "";
  if (isLookupQueryPath(path)) return false;
  const writes = catalog.filter(isWriteCapability);
  if (writes.length) {
    if (writes.some(item => sameResource(item.transport.pathTemplate, path))) return true;
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
    if (PICKER_PAGE.test(path) && (otherLists.length || pageQueries(catalog).some(item => !PICKER_PAGE.test(item.transport.pathTemplate || "")))) {
      return false;
    }
    if (hasBusinessCallerField(capability)) return true;
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
  const primary = verified.filter(capability => isPrimaryCapability(capability, capabilities) && matchesExportFilter(capability, match));
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
  if (selected.length) return selected;
  if (match.length) return [];
  return verified.filter(capability => !isNoiseCapability(capability));
}

export function summarizeCatalog(capabilities: CapabilityContract[]) {
  const primary = capabilities.filter(capability => isPrimaryCapability(capability, capabilities));
  const lookups = capabilities.filter(capability =>
    !primary.includes(capability) && isCandidateSourceCapability(capability, capabilities)
  );
  const noise = capabilities.filter(isNoiseCapability);
  return { primary, lookups, noise };
}
