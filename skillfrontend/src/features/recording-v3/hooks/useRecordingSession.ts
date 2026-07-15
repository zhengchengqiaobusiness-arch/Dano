import { useCallback, useMemo, useReducer, useRef } from "react";
import {
  createRecordingSession,
  recordingWebSocketUrl,
  resumeRecordingSession,
} from "../api/recordingClient";
import type { RecordingSessionInput, RecordingSessionResponse } from "../api/recordingClient";
import {
  decorateRecordingMutation,
  initialRecordingSessionState,
  isStaleRecordingEvent,
  nextRecordingRevision,
  recordingReducer,
} from "../state/recordingReducer";
import type {
  PersistedRecordingSession,
  RecordingSessionAction,
  RecordingSessionState,
} from "../state/recordingReducer";

export interface RecordingSocketConnection {
  response: RecordingSessionResponse;
  websocketUrl: string;
}

export interface RecordingSessionController {
  state: RecordingSessionState;
  create(input: RecordingSessionInput): Promise<RecordingSocketConnection>;
  resume(): Promise<RecordingSocketConnection>;
  observeServerEvent(event: Record<string, unknown>): boolean;
  markConnected(): void;
  markReconnecting(error?: string): void;
  markError(error: string): void;
  decorateMutation<T extends Record<string, unknown>>(message: T): T & { expected_revision: number; operation_id: string };
  clear(): void;
}

function storageKey(tenant: string, subsystem: string): string {
  return `dano.recording-v3.session:${encodeURIComponent(tenant)}:${encodeURIComponent(subsystem)}`;
}

function loadPersisted(key: string): PersistedRecordingSession | null {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<PersistedRecordingSession>;
    if (value.version !== 1 || !value.recordingId || !value.resumeToken) return null;
    return {
      version: 1,
      recordingId: value.recordingId,
      resumeToken: value.resumeToken,
      revision: Number(value.revision || 0),
      input: value.input,
    };
  } catch {
    return null;
  }
}

function savePersisted(key: string, value: PersistedRecordingSession): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Storage can be unavailable in hardened/private browser contexts. The
    // in-memory credentials still keep the active socket resumable.
  }
}

function removePersisted(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch {
    // Clearing the in-memory controller remains authoritative for this page.
  }
}

function initialState(key: string): RecordingSessionState {
  const persisted = loadPersisted(key);
  return persisted
    ? recordingReducer(initialRecordingSessionState, { type: "restore", session: persisted })
    : initialRecordingSessionState;
}

function operationId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `recording-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function useRecordingSession(tenant: string, subsystem: string): RecordingSessionController {
  const key = useMemo(() => storageKey(tenant, subsystem), [tenant, subsystem]);
  const [state, dispatch] = useReducer(recordingReducer, key, initialState);
  const credentialsRef = useRef<{ recordingId?: string; resumeToken?: string }>({
    recordingId: state.recordingId,
    resumeToken: state.resumeToken,
  });
  const revisionRef = useRef(state.revision);
  const inputRef = useRef(state.input);
  const lifecycleRef = useRef(0);
  const resumeInFlightRef = useRef<Promise<RecordingSocketConnection> | null>(null);
  credentialsRef.current = { recordingId: state.recordingId, resumeToken: state.resumeToken };
  inputRef.current = state.input;
  if (state.revision > revisionRef.current) revisionRef.current = state.revision;

  const persist = useCallback((
    session: RecordingSessionResponse,
    input?: RecordingSessionInput,
    resetRevision = false,
  ) => {
    const revision = resetRevision
      ? session.current_revision
      : Math.max(revisionRef.current, session.current_revision);
    const value: PersistedRecordingSession = {
      version: 1,
      recordingId: session.recording_id,
      resumeToken: session.resume_token,
      revision,
      input: input || inputRef.current,
    };
    savePersisted(key, value);
    credentialsRef.current = { recordingId: value.recordingId, resumeToken: value.resumeToken };
    revisionRef.current = value.revision;
    inputRef.current = value.input;
  }, [key]);

  const dispatchSession = useCallback((action: RecordingSessionAction) => dispatch(action), []);

  const create = useCallback(async (input: RecordingSessionInput): Promise<RecordingSocketConnection> => {
    const lifecycle = ++lifecycleRef.current;
    resumeInFlightRef.current = null;
    dispatchSession({ type: "creating" });
    const response = await createRecordingSession(input);
    if (lifecycle !== lifecycleRef.current) throw new Error("录制会话已被新的操作替代");
    persist(response, input, true);
    dispatchSession({ type: "session_ready", session: response, input });
    return { response, websocketUrl: recordingWebSocketUrl(response.recording_id, response.websocket_ticket) };
  }, [dispatchSession, persist]);

  const resume = useCallback((): Promise<RecordingSocketConnection> => {
    if (resumeInFlightRef.current) return resumeInFlightRef.current;
    const lifecycle = lifecycleRef.current;
    const task = (async () => {
      const { recordingId, resumeToken } = credentialsRef.current;
      if (!recordingId || !resumeToken) throw new Error("没有可恢复的录制会话");
      dispatchSession({ type: "connection", status: "reconnecting" });
      const response = await resumeRecordingSession(recordingId, resumeToken);
      if (lifecycle !== lifecycleRef.current) throw new Error("录制会话已被新的操作替代");
      persist(response);
      dispatchSession({ type: "session_ready", session: response, reconnecting: true });
      return { response, websocketUrl: recordingWebSocketUrl(response.recording_id, response.websocket_ticket) };
    })();
    resumeInFlightRef.current = task;
    const clearPending = () => {
      if (resumeInFlightRef.current === task) resumeInFlightRef.current = null;
    };
    task.then(clearPending, clearPending);
    return task;
  }, [dispatchSession, persist]);

  const observeServerEvent = useCallback((event: Record<string, unknown>) => {
    const stale = isStaleRecordingEvent(revisionRef.current, event);
    const revision = nextRecordingRevision(revisionRef.current, event);
    if (revision > revisionRef.current) {
      revisionRef.current = revision;
      const { recordingId, resumeToken } = credentialsRef.current;
      if (recordingId && resumeToken) {
        savePersisted(key, {
          version: 1,
          recordingId,
          resumeToken,
          revision,
          input: inputRef.current,
        } satisfies PersistedRecordingSession);
      }
    }
    dispatchSession({ type: "server_event", event });
    return !stale;
  }, [dispatchSession, key]);

  const markConnected = useCallback(() => dispatchSession({ type: "connection", status: "connected" }), [dispatchSession]);
  const markReconnecting = useCallback((error?: string) => dispatchSession({ type: "connection", status: "reconnecting", error }), [dispatchSession]);
  const markError = useCallback((error: string) => dispatchSession({ type: "connection", status: "error", error }), [dispatchSession]);
  const decorateMutation = useCallback(<T extends Record<string, unknown>>(message: T) =>
    decorateRecordingMutation(
      message,
      revisionRef.current ?? 0,
      String(message.operation_id || operationId()),
    ), []);
  const clear = useCallback(() => {
    lifecycleRef.current += 1;
    resumeInFlightRef.current = null;
    removePersisted(key);
    credentialsRef.current = {};
    revisionRef.current = 0;
    inputRef.current = undefined;
    dispatchSession({ type: "clear" });
  }, [dispatchSession, key]);

  return { state, create, resume, observeServerEvent, markConnected, markReconnecting, markError, decorateMutation, clear };
}
