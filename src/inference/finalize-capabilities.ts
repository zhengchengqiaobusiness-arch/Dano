import type { CapabilityContract, EvidenceEvent } from "../domain.js";
import { catalogTransportKey } from "../catalog/normalize.js";
import { attachCandidateSources } from "./candidate-sources.js";
import { attachDictEnums } from "./dict-enums.js";
import { validateCapability } from "../validation/validator.js";
import { attachCatalogDerivations } from "./field-derivation.js";

function hasScopedNetworkEvidence(capability: CapabilityContract, events: EvidenceEvent[]) {
  const ids = new Set(events.map(event => event.id));
  return capability.evidence.some(ref => ids.has(ref.eventId));
}

export function sealWriteCapabilities(capabilities: CapabilityContract[], events: EvidenceEvent[]) {
  return attachCatalogDerivations(capabilities, events);
}

export function finalizeCapabilities(capabilities: CapabilityContract[], events: EvidenceEvent[]) {
  const first = capabilities.map(capability => validateCapability(capability, events, capabilities));
  const sourced = attachDictEnums(attachCandidateSources(first, events), events);
  const sealed = sealWriteCapabilities(sourced, events);
  return sealed.map(capability => validateCapability(capability, events, sealed));
}

export function finalizeSessionSlice(
  slice: CapabilityContract[],
  events: EvidenceEvent[],
  existing: CapabilityContract[] = []
) {
  const finalized = finalizeCapabilities(slice, events);
  if (!existing.length) return finalized;
  const existingByKey = new Map(existing.map(capability => [catalogTransportKey(capability), capability]));
  return finalized.map(capability => {
    const old = existingByKey.get(catalogTransportKey(capability));
    if (!old || old.validation.status !== "verified" || capability.validation.status === "verified") return capability;
    if (hasScopedNetworkEvidence(old, events) || hasScopedNetworkEvidence(capability, events)) return capability;
    return old;
  });
}
