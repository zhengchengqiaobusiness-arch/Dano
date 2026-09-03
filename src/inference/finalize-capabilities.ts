import type { CapabilityContract, EvidenceEvent } from "../domain.js";
import { attachCandidateSources } from "./candidate-sources.js";
import { attachDictEnums } from "./dict-enums.js";
import { validateCapability } from "../validation/validator.js";
import { attachCatalogDerivations } from "./field-derivation.js";

export function sealWriteCapabilities(capabilities: CapabilityContract[], events: EvidenceEvent[]) {
  return attachCatalogDerivations(capabilities, events);
}

export function finalizeCapabilities(capabilities: CapabilityContract[], events: EvidenceEvent[]) {
  const first = capabilities.map(capability => validateCapability(capability, events, capabilities));
  const sourced = attachDictEnums(attachCandidateSources(first, events), events);
  const sealed = sealWriteCapabilities(sourced, events);
  return sealed.map(capability => validateCapability(capability, events, sealed));
}
