import type { CapabilityContract, EvidenceEvent } from "../domain.js";
import { attachCandidateSources } from "./candidate-sources.js";
import { validateCapability } from "../validation/validator.js";

export function finalizeCapabilities(capabilities: CapabilityContract[], events: EvidenceEvent[]) {
  const first = capabilities.map(capability => validateCapability(capability, events, capabilities));
  const sourced = attachCandidateSources(first);
  return sourced.map(capability => validateCapability(capability, events, sourced));
}
