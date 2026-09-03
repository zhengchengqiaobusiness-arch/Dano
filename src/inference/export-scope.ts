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

const LIST_QUERY = /\/page$|\/list$|\/search$|\/query$|simple-list/i;

export function isPrimaryCapability(capability: CapabilityContract) {
  if (isNoiseCapability(capability)) return false;
  if (["create", "update", "review", "delete", "upload"].includes(capability.operation)) return true;
  if (capability.operation !== "query") return false;
  const path = capability.transport.pathTemplate || capability.transport.urlTemplate || "";
  if (/\/page$/i.test(path)) return true;
  if (!hasBusinessCallerField(capability)) return false;
  return LIST_QUERY.test(path);
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
  const primary = verified.filter(capability => isPrimaryCapability(capability) && matchesExportFilter(capability, match));
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
  const primary = capabilities.filter(capability => isPrimaryCapability(capability));
  const lookups = capabilities.filter(capability =>
    !primary.includes(capability) && isCandidateSourceCapability(capability, capabilities)
  );
  const noise = capabilities.filter(isNoiseCapability);
  return { primary, lookups, noise };
}
