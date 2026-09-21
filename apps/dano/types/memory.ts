export interface UserMemoryStatus {
  enabled: boolean;
  automaticCollection: boolean;
  effectiveAt: string;
  policyVersion: string;
  revision: number;
  collection?: {
    availablePolicyVersion: string | null;
    consent: { policyVersion: string; scope: string | null; effectiveAt: string; revision: number } | null;
  };
}

/** Local delivery receipt. Remote identifiers and pending payloads stay private. */
export interface UserMemoryOperation {
  id: string;
  phase: "queued" | "session_unknown" | "session_created" | "message_unknown"
    | "message_delivered" | "commit_unknown" | "processing" | "ready" | "failed"
    | "blocked_by_pause" | "blocked";
  createdAt: string;
  updatedAt: string;
  source: { sessionId: string; entryId: string; branchId: string };
}

export interface UserMemoryOperationPage {
  items: UserMemoryOperation[];
  nextCursor: string | null;
}

export interface UserMemoryContent {
  operationId: string;
  index: number;
  total: number;
  text: string;
}
