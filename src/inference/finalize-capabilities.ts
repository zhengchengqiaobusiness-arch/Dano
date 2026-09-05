import type { CapabilityContract, EvidenceEvent } from "../domain.js";
import { catalogTransportKey } from "../catalog/normalize.js";
import { attachCandidateSources } from "./candidate-sources.js";
import { attachDictEnums } from "./dict-enums.js";
import { validateCapability } from "../validation/validator.js";
import { attachCatalogDerivations } from "./field-derivation.js";
import { exportableCapabilities, isCandidateSourceCapability, isPrimaryCapability } from "./export-scope.js";

function stripUnavailableSources(capability: CapabilityContract, availableIds: Set<string>): CapabilityContract {
  let changed = false;
  const inputForm = capability.inputForm.map(field => {
    let next = field;
    if (next.candidates?.type === "capability" && !availableIds.has(next.candidates.capabilityId)) {
      const { candidates: _candidates, ...rest } = next;
      next = rest;
      changed = true;
    }
    const from = /^from:([^:]+):/.exec(next.defaultRule || "");
    if (from?.[1] && !availableIds.has(from[1])) {
      next = {
        ...next,
        defaultRule: undefined,
        source: next.source === "binding" ? "system" : next.source,
        systemHandled: next.source === "binding" ? true : next.systemHandled
      };
      changed = true;
    }
    return next;
  });
  const bindings = capability.bindings.filter(binding =>
    availableIds.has(binding.fromCapabilityId) || binding.approvalSource === "human"
  );
  if (bindings.length !== capability.bindings.length) changed = true;
  if (!changed) return capability;
  return { ...capability, inputForm, bindings };
}

function hasScopedNetworkEvidence(capability: CapabilityContract, events: EvidenceEvent[]) {
  const ids = new Set(events.map(event => event.id));
  return capability.evidence.some(ref => ids.has(ref.eventId));
}

export function sealWriteCapabilities(capabilities: CapabilityContract[], events: EvidenceEvent[]) {
  return attachCatalogDerivations(capabilities, events);
}

export function finalizeCapabilities(capabilities: CapabilityContract[], events: EvidenceEvent[]) {
  const first = capabilities.map(capability =>
    capability.validation.status === "verified"
      ? capability
      : validateCapability(capability, events, capabilities)
  );
  const sourced = attachDictEnums(attachCandidateSources(first, events), events);
  const sealed = sealWriteCapabilities(sourced, events);
  return sealed.map(capability => {
    if (capability.validation.status === "verified" && capability.inputForm === first.find(item => item.id === capability.id)?.inputForm) {
      return capability;
    }
    return validateCapability(capability, events, sealed);
  });
}

export function sessionExportReady(catalog: CapabilityContract[]) {
  if (!exportableCapabilities(catalog).length) return false;
  return catalog.every(capability =>
    capability.validation.status === "verified"
    || (!isPrimaryCapability(capability, catalog) && !isCandidateSourceCapability(capability, catalog))
  );
}

export function finalizeSessionSlice(
  slice: CapabilityContract[],
  events: EvidenceEvent[],
  existing: CapabilityContract[] = []
) {
  const availableIds = new Set(slice.map(capability => capability.id));
  let stripped = false;
  const cleaned = slice.map(capability => {
    const next = stripUnavailableSources(capability, availableIds);
    if (next !== capability) stripped = true;
    return next;
  });
  const ready = stripped ? cleaned : slice;
  if (sessionExportReady(ready) && existing.length) return ready;
  const finalized = finalizeCapabilities(ready, events);
  if (!existing.length) return finalized;
  const existingByKey = new Map(existing.map(capability => [catalogTransportKey(capability), capability]));
  return finalized.map(capability => {
    const old = existingByKey.get(catalogTransportKey(capability));
    if (!old || old.validation.status !== "verified" || capability.validation.status === "verified") return capability;
    if (hasScopedNetworkEvidence(old, events) || hasScopedNetworkEvidence(capability, events)) return capability;
    return old;
  });
}
