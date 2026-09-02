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

export function isPrimaryCapability(capability: CapabilityContract) {
  if (isNoiseCapability(capability)) return false;
  if (["create", "update", "review", "delete", "upload"].includes(capability.operation)) return true;
  return capability.operation === "query" && hasBusinessCallerField(capability);
}

export function isCandidateSourceCapability(capability: CapabilityContract, catalog: CapabilityContract[]) {
  return catalog.some(item =>
    item.inputForm.some(field => field.candidates?.type === "capability" && field.candidates.capabilityId === capability.id)
  );
}

export function exportableCapabilities(capabilities: CapabilityContract[]) {
  const verified = capabilities.filter(capability => capability.validation.status === "verified");
  const primary = verified.filter(isPrimaryCapability);
  const needed = new Set(primary.map(capability => capability.id));
  for (const capability of primary) {
    for (const field of capability.inputForm) {
      if (field.candidates?.type === "capability") needed.add(field.candidates.capabilityId);
    }
    for (const binding of capability.bindings.filter(item => item.approved)) needed.add(binding.fromCapabilityId);
  }
  const selected = verified.filter(capability => needed.has(capability.id) && !isNoiseCapability(capability));
  if (selected.length) return selected;
  return verified.filter(capability => !isNoiseCapability(capability));
}
