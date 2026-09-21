import type { UserMemoryStatus } from "@dano/types/memory";

export type MemorySettingsChange = { enabled: boolean } | { automaticCollection: false }
  | { automaticCollection: true; collectionPolicyVersion: string };

/** Never retry a settings mutation automatically: a lost response may follow a successful save. */
export async function requestMemorySettings(url: string, signal: AbortSignal, change?: MemorySettingsChange): Promise<UserMemoryStatus> {
  const response = await fetch(url, change === undefined ? { signal, cache: "no-store" } : {
    method: "PUT", signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify(change),
  });
  if (!response.ok) throw new Error(response.status === 401 ? "MEMORY_LOGIN_REQUIRED" : "MEMORY_UNAVAILABLE");
  const raw: unknown = await response.json();
  if (!raw || typeof raw !== "object") throw new Error("MEMORY_UNAVAILABLE");
  const body = raw as Partial<UserMemoryStatus>;
  if (typeof body.enabled !== "boolean" || typeof body.automaticCollection !== "boolean"
    || typeof body.effectiveAt !== "string" || typeof body.policyVersion !== "string"
    || !Number.isSafeInteger(body.revision) || body.revision! < 0) throw new Error("MEMORY_UNAVAILABLE");
  let collection: UserMemoryStatus["collection"];
  if (body.collection !== undefined) {
    const configured = body.collection;
    if (!configured || typeof configured !== "object"
      || (configured.availablePolicyVersion !== null && typeof configured.availablePolicyVersion !== "string")) {
      throw new Error("MEMORY_UNAVAILABLE");
    }
    const consent = configured.consent;
    if (consent !== null && (!consent || typeof consent !== "object" || typeof consent.policyVersion !== "string"
      || (consent.scope !== null && typeof consent.scope !== "string") || typeof consent.effectiveAt !== "string"
      || !Number.isSafeInteger(consent.revision) || consent.revision < 1)) throw new Error("MEMORY_UNAVAILABLE");
    collection = { availablePolicyVersion: configured.availablePolicyVersion,
      consent: consent ? { policyVersion: consent.policyVersion, scope: consent.scope,
        effectiveAt: consent.effectiveAt, revision: consent.revision } : null };
  }
  return { enabled: body.enabled, automaticCollection: body.automaticCollection,
    effectiveAt: body.effectiveAt, policyVersion: body.policyVersion, revision: body.revision!,
    ...(collection ? { collection } : {}) };
}
