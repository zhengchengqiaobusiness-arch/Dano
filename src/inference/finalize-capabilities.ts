import type { CapabilityContract, EvidenceEvent } from "../domain.js";
import { attachCandidateSources } from "./candidate-sources.js";
import { attachDictEnums } from "./dict-enums.js";
import { validateCapability } from "../validation/validator.js";

export function finalizeCapabilities(capabilities: CapabilityContract[], events: EvidenceEvent[]) {
  const first = capabilities.map(capability => validateCapability(capability, events, capabilities));
  const sourced = attachDictEnums(attachCandidateSources(first, events), events);
  return sourced.map(capability => validateCapability(capability, events, sourced));
}
