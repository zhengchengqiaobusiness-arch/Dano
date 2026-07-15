import type { RecordingSessionInput, RecordingSessionResponse } from "../api/recordingClient";

export type RecordingConnectionStatus =
  | "idle"
  | "restored"
  | "creating"
  | "connecting"
  | "connected"
  | "reconnecting"
  | "error";

export interface PiUsage {
  input: number;
  output: number;
  cacheRead: number;
  cacheWrite: number;
  total: number;
}

export interface PiSessionStatus {
  sessionId?: string;
  status?: string;
  turn?: number;
  toolCalls?: number;
  retries?: number;
  compactions?: number;
  usage?: PiUsage;
  lastError?: string;
}

export interface PersistedRecordingSession {
  version: 1;
  recordingId: string;
  resumeToken: string;
  revision: number;
  input?: RecordingSessionInput;
}

export interface RecordingSessionState {
  status: RecordingConnectionStatus;
  recordingId?: string;
  resumeToken?: string;
  websocketTicket?: string;
  revision: number;
  snapshot?: Record<string, unknown> | null;
  input?: RecordingSessionInput;
  pi: PiSessionStatus;
  error?: string;
}

export const initialRecordingSessionState: RecordingSessionState = {
  status: "idle",
  revision: 0,
  pi: {},
};

export type RecordingSessionAction =
  | { type: "restore"; session: PersistedRecordingSession }
  | { type: "creating" }
  | { type: "session_ready"; session: RecordingSessionResponse; input?: RecordingSessionInput; reconnecting?: boolean }
  | { type: "connection"; status: RecordingConnectionStatus; error?: string }
  | { type: "server_event"; event: Record<string, unknown> }
  | { type: "clear" };

function numberValue(...values: unknown[]): number | undefined {
  for (const value of values) {
    if (value !== null && value !== undefined && value !== "") {
      const parsed = Number(value);
      if (Number.isFinite(parsed)) return parsed;
    }
  }
  return undefined;
}

export function eventRevision(event: Record<string, unknown>): number | undefined {
  const report = event.report as Record<string, unknown> | undefined;
  const fullSpec = (event.full_spec || event.flow_spec || report?.full_spec || report?.flow_spec) as Record<string, unknown> | undefined;
  const meta = fullSpec?.meta as Record<string, unknown> | undefined;
  const snapshot = event.snapshot as Record<string, unknown> | undefined;
  return numberValue(
    event.current_revision,
    event.actual_revision,
    event.latest_revision,
    event.revision,
    report?.current_revision,
    report?.revision,
    snapshot?.current_revision,
    meta?.current_revision,
    meta?.revision,
  );
}

export function nextRecordingRevision(current: number, event: Record<string, unknown>): number {
  const observed = eventRevision(event);
  return observed === undefined ? current : Math.max(current, observed);
}

export function isStaleRecordingEvent(current: number, event: Record<string, unknown>): boolean {
  const observed = eventRevision(event);
  return observed !== undefined && observed < current;
}

export function decorateRecordingMutation<T extends Record<string, unknown>>(
  message: T,
  revision: number,
  operationId: string,
): T & { expected_revision: number; operation_id: string } {
  return {
    ...message,
    expected_revision: Number(message.expected_revision ?? revision),
    operation_id: String(message.operation_id || operationId),
  };
}

function mergePiStatus(current: PiSessionStatus, event: Record<string, unknown>): PiSessionStatus {
  const eventType = String(event.type || "");
  const nestedEvent = event.event as Record<string, unknown> | undefined;
  const fullSpec = (event.full_spec || event.flow_spec) as Record<string, unknown> | undefined;
  const meta = fullSpec?.meta as Record<string, unknown> | undefined;
  const raw = (
    event.pi_session
    || event.pi_status
    || event.pi
    || meta?.recording_pi
    || meta?.recording_pi_loop
    || (eventType === "pi_event" ? nestedEvent : null)
    || (eventType.startsWith("pi_") ? event : null)
  ) as Record<string, unknown> | null;
  if (!raw) return current;

  const rawUsage = (raw.usage || event.usage || nestedEvent?.usage) as Record<string, unknown> | undefined;
  const previousUsage = current.usage;
  const input = numberValue(rawUsage?.input, rawUsage?.input_tokens, previousUsage?.input) || 0;
  const output = numberValue(rawUsage?.output, rawUsage?.output_tokens, previousUsage?.output) || 0;
  const cacheRead = numberValue(rawUsage?.cacheRead, rawUsage?.cache_read, rawUsage?.cache_read_tokens, previousUsage?.cacheRead) || 0;
  const cacheWrite = numberValue(rawUsage?.cacheWrite, rawUsage?.cache_write, rawUsage?.cache_write_tokens, previousUsage?.cacheWrite) || 0;
  const total = numberValue(rawUsage?.total, rawUsage?.total_tokens, previousUsage?.total) || input + output;
  const toolCallsValue = Array.isArray(raw.tool_calls) ? raw.tool_calls.length : raw.tool_calls;
  const status = String(raw.status || raw.session_status || current.status || "") || undefined;
  const hasExplicitError = Object.prototype.hasOwnProperty.call(raw, "last_error")
    || Object.prototype.hasOwnProperty.call(raw, "error");
  const healthyStatus = ["connected", "running", "idle", "ok", "completed"].includes(status || "");

  return {
    sessionId: String(raw.session_id || raw.id || current.sessionId || "") || undefined,
    status,
    turn: numberValue(raw.current_turn, raw.turn, current.turn),
    toolCalls: numberValue(raw.tool_call_count, raw.tool_calls_count, toolCallsValue, current.toolCalls),
    retries: numberValue(raw.retry_count, raw.retries, current.retries),
    compactions: numberValue(raw.compaction_count, raw.compactions, current.compactions),
    usage: rawUsage || previousUsage ? { input, output, cacheRead, cacheWrite, total } : undefined,
    lastError: hasExplicitError
      ? (String(raw.last_error || raw.error || "") || undefined)
      : (healthyStatus ? undefined : current.lastError),
  };
}

export function recordingReducer(
  state: RecordingSessionState,
  action: RecordingSessionAction,
): RecordingSessionState {
  switch (action.type) {
    case "restore":
      return {
        ...state,
        status: "restored",
        recordingId: action.session.recordingId,
        resumeToken: action.session.resumeToken,
        revision: action.session.revision,
        input: action.session.input,
        error: undefined,
      };
    case "creating":
      return { ...state, status: "creating", error: undefined };
    case "session_ready": {
      const snapshot = action.session.snapshot as Record<string, unknown> | null | undefined;
      const sessionPi = action.session.pi_status;
      const staleResume = !!action.reconnecting && action.session.current_revision < state.revision;
      const snapshotPiEvent: Record<string, unknown> = snapshot || sessionPi ? {
        type: "pi_snapshot",
        pi: sessionPi || snapshot?.pi_session || snapshot?.pi_status || snapshot?.pi,
        flow_spec: snapshot?.flow_spec || snapshot?.full_spec,
      } : {};
      return {
        ...state,
        status: action.reconnecting ? "reconnecting" : "connecting",
        recordingId: action.session.recording_id,
        resumeToken: action.session.resume_token,
        websocketTicket: action.session.websocket_ticket,
        revision: action.reconnecting
          ? Math.max(state.revision, action.session.current_revision)
          : action.session.current_revision,
        snapshot: staleResume ? state.snapshot : (action.session.snapshot ?? state.snapshot),
        input: action.input || state.input,
        pi: snapshot || sessionPi ? mergePiStatus(state.pi, snapshotPiEvent) : state.pi,
        error: undefined,
      };
    }
    case "connection":
      return { ...state, status: action.status, error: action.error };
    case "server_event": {
      const stale = isStaleRecordingEvent(state.revision, action.event);
      return {
        ...state,
        revision: nextRecordingRevision(state.revision, action.event),
        snapshot: stale
          ? state.snapshot
          : ((action.event.snapshot as Record<string, unknown> | undefined) ?? state.snapshot),
        pi: mergePiStatus(state.pi, action.event),
      };
    }
    case "clear":
      return initialRecordingSessionState;
    default:
      return state;
  }
}
