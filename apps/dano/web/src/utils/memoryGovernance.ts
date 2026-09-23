export interface GovernanceReceipt { jobId: string; status: "pending" | "complete" | "superseded"; errorCode?: string }
export interface ExportedMemory { uri: string; content: string;
  sources: Array<{ kind: "explicit" | "automatic"; status: "current" | "revoked";
    sessionId: string; entryId: string; createdAt: string }>;
  revisions: Array<{ kind: string; revision: number; createdAt: string; completedAt?: string }> }
export interface ExportPage { items: ExportedMemory[]; nextCursor?: string }
export type GovernanceReview = { stage: "classify"; candidates: Array<{ operationId: string; phase: string; candidateText: string }> }
  | { stage: "merged"; candidates: Array<{ operationId: string; candidateText: string; documentText: string }> };

async function json(url: string, signal: AbortSignal, body?: unknown): Promise<unknown> {
  const response = await fetch(url, body === undefined ? { signal, cache: "no-store" } : {
    method: "POST", signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(response.status === 401 ? "MEMORY_LOGIN_REQUIRED" : "MEMORY_GOVERNANCE_UNAVAILABLE");
  return response.json();
}

function receipt(value: unknown): GovernanceReceipt {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("MEMORY_GOVERNANCE_UNAVAILABLE");
  const data = value as Partial<GovernanceReceipt>;
  if (typeof data.jobId !== "string" || !/^[a-f0-9-]{36}$/.test(data.jobId)
    || (data.status !== "pending" && data.status !== "complete" && data.status !== "superseded")
    || (data.errorCode !== undefined && typeof data.errorCode !== "string")) throw new Error("MEMORY_GOVERNANCE_UNAVAILABLE");
  return data as GovernanceReceipt;
}

export async function pendingGovernance(url: string, signal: AbortSignal): Promise<GovernanceReceipt | null> {
  const result = await json(url, signal) as { pending?: unknown };
  return result?.pending == null ? null : receipt(result.pending);
}

export async function startGovernance(url: string, signal: AbortSignal,
  action: { action: "correct"; memoryUri: string; selectedText: string; replacementText: string }
    | { action: "forget"; memoryUri: string; selectedText: string }
    | { action: "clear"; confirmed: true }): Promise<GovernanceReceipt> {
  return receipt(await json(url, signal, action));
}

export async function governanceStatus(url: string, jobId: string, signal: AbortSignal): Promise<GovernanceReceipt> {
  return receipt(await json(`${url}/${encodeURIComponent(jobId)}`, signal));
}

export async function governanceReview(url: string, jobId: string, signal: AbortSignal): Promise<GovernanceReview> {
  const raw = await json(`${url}/${encodeURIComponent(jobId)}/review`, signal);
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new Error("MEMORY_GOVERNANCE_UNAVAILABLE");
  const review = raw as GovernanceReview;
  if ((review.stage !== "classify" && review.stage !== "merged") || !Array.isArray(review.candidates)
    || review.candidates.some(candidate => !candidate || typeof candidate.operationId !== "string"
      || typeof candidate.candidateText !== "string"
      || review.stage === "merged" && typeof (candidate as { documentText?: unknown }).documentText !== "string")) {
    throw new Error("MEMORY_GOVERNANCE_UNAVAILABLE");
  }
  return review;
}

export async function submitGovernanceReview(url: string, jobId: string, signal: AbortSignal,
  decision: { operationId: string; decision: "target" | "unrelated" }
    | { operationId: string; exactText: string }): Promise<GovernanceReceipt> {
  return receipt(await json(`${url}/${encodeURIComponent(jobId)}/review`, signal, decision));
}

export async function exportMemoryPage(url: string, signal: AbortSignal, cursor?: string): Promise<ExportPage> {
  const target = new URL(url, window.location.origin);
  target.searchParams.set("limit", "10");
  if (cursor) target.searchParams.set("cursor", cursor);
  const raw = await json(target.toString(), signal);
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new Error("MEMORY_EXPORT_UNAVAILABLE");
  const page = raw as ExportPage;
  if (!Array.isArray(page.items) || page.items.some(item => !item || typeof item.uri !== "string"
    || typeof item.content !== "string" || !Array.isArray(item.sources) || !Array.isArray(item.revisions)
    || item.sources.some(source => !source || (source.kind !== "explicit" && source.kind !== "automatic")
      || (source.status !== "current" && source.status !== "revoked")
      || typeof source.sessionId !== "string" || typeof source.entryId !== "string"
      || typeof source.createdAt !== "string" || !Number.isFinite(Date.parse(source.createdAt))))
    || (page.nextCursor !== undefined && typeof page.nextCursor !== "string")) throw new Error("MEMORY_EXPORT_UNAVAILABLE");
  return page;
}
