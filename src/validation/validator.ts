import type { CapabilityContract, EvidenceEvent, NetworkEvidence, UiEvidence } from "../domain.js";
import { getByPath } from "../utils.js";

export function validateCapability(cap: CapabilityContract, events: EvidenceEvent[]): CapabilityContract {
  const byId = new Map(events.map(e => [e.id, e]));
  const checks: CapabilityContract["validation"]["checks"] = [];

  const networkRefs = cap.evidence
    .map(ref => byId.get(ref.eventId))
    .filter((e): e is NetworkEvidence => e?.kind === "network");

  const uiRefs = cap.evidence
    .map(ref => byId.get(ref.eventId))
    .filter((e): e is UiEvidence => e?.kind === "ui");

  const hasNetwork = networkRefs.length > 0;
  checks.push({
    name: "recorded-network-evidence",
    ok: hasNetwork,
    detail: hasNetwork ? `${networkRefs.length} recorded network event(s)` : "No recorded network evidence"
  });

  const successful = networkRefs.some(e =>
    Boolean(e.response && e.response.status >= 200 && e.response.status < 400)
  );
  checks.push({
    name: "successful-response",
    ok: successful,
    detail: successful ? "At least one recorded response is 2xx/3xx" : "No successful recorded response"
  });

  const transportMatches = networkRefs.every(e =>
    e.request.method.toUpperCase() === cap.transport.method.toUpperCase()
  );
  checks.push({
    name: "transport-consistency",
    ok: hasNetwork && transportMatches,
    detail: transportMatches ? "Recorded methods match contract" : "Recorded method mismatch"
  });

  const completionAssertionsBacked = (cap.completion.assertions || []).every(assertion =>
    networkRefs.some(event => {
      if (!event.response || event.response.status < 200 || event.response.status >= 400) return false;
      const actual = getByPath(event.response.body, assertion.path);
      if (assertion.kind === "exists") return actual !== undefined;
      if (assertion.kind === "nonempty") return actual !== undefined && actual !== null && actual !== "" && (!Array.isArray(actual) || actual.length > 0);
      return Object.is(actual, assertion.value);
    })
  );
  checks.push({
    name: "completion-assertions-backed-by-evidence",
    ok: completionAssertionsBacked,
    detail: completionAssertionsBacked
      ? `${cap.completion.assertions?.length || 0} completion assertion(s) backed by recorded success evidence`
      : "A completion assertion is not supported by recorded successful evidence"
  });

  const knownOperation = cap.operation !== "unknown";
  checks.push({
    name: "known-operation",
    ok: knownOperation,
    detail: knownOperation ? cap.operation : "Operation is still unknown"
  });

  if (cap.sideEffect) {
    const correlatedUi = uiRefs.length > 0 || networkRefs.some(e => Boolean(e.correlatedUiEvidenceId));
    checks.push({
      name: "write-ui-correlation",
      ok: correlatedUi,
      detail: correlatedUi
        ? "Write operation has correlated real UI evidence"
        : "Write operation lacks correlated UI evidence"
    });
  }

  const allOk = checks.every(c => c.ok);

  return {
    ...cap,
    validation: {
      status: allOk ? "verified" : "candidate",
      checks,
      verifiedAt: allOk ? new Date().toISOString() : undefined
    }
  };
}
