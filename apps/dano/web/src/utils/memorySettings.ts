import type { UserMemoryStatus } from "@dano/types/memory";

/** Never retry a settings mutation automatically: a lost response may follow a successful save. */
export async function requestMemorySettings(url: string, signal: AbortSignal, enabled?: boolean): Promise<UserMemoryStatus> {
  const response = await fetch(url, enabled === undefined ? { signal, cache: "no-store" } : {
    method: "PUT", signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled }),
  });
  if (!response.ok) throw new Error(response.status === 401 ? "MEMORY_LOGIN_REQUIRED" : "MEMORY_UNAVAILABLE");
  const raw: unknown = await response.json();
  if (!raw || typeof raw !== "object") throw new Error("MEMORY_UNAVAILABLE");
  const body = raw as Partial<UserMemoryStatus>;
  if (typeof body.enabled !== "boolean" || typeof body.automaticCollection !== "boolean"
    || typeof body.effectiveAt !== "string" || typeof body.policyVersion !== "string"
    || !Number.isSafeInteger(body.revision) || body.revision! < 0) throw new Error("MEMORY_UNAVAILABLE");
  return { enabled: body.enabled, automaticCollection: body.automaticCollection,
    effectiveAt: body.effectiveAt, policyVersion: body.policyVersion, revision: body.revision! };
}
