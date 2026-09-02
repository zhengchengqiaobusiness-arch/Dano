import type { CapabilityContract } from "../domain.js";

const BACKGROUND_PATH = /\/im\/|notify-message|unread-count|online-status|get-permission-info|captcha|tenant\/get-by-website|tenant\/get-id-by-name|\/user\/get-current$/i;

export function isUserFacingCapability(capability: CapabilityContract) {
  if (["create", "update", "review", "delete", "upload"].includes(capability.operation)) return true;
  return capability.evidence.some(item => item.kind === "ui");
}

export function isBackgroundCapability(capability: CapabilityContract) {
  if (isUserFacingCapability(capability)) return false;
  return BACKGROUND_PATH.test(capability.transport.pathTemplate || capability.transport.urlTemplate);
}

export function exportableCapabilities(capabilities: CapabilityContract[]) {
  const verified = capabilities.filter(capability => capability.validation.status === "verified");
  const primary = verified.filter(isUserFacingCapability);
  const needed = new Set(primary.map(capability => capability.id));
  for (const capability of primary) {
    for (const binding of capability.bindings.filter(item => item.approved)) needed.add(binding.fromCapabilityId);
  }
  const selected = verified.filter(capability => needed.has(capability.id));
  if (selected.length) return selected;
  return verified.filter(capability => !isBackgroundCapability(capability));
}
