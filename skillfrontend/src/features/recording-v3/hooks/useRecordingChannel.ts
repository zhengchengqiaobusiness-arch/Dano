import { useEffect, useRef } from "react";
import type { RecordingSessionController } from "./useRecordingSession";
import type { RecordingSessionInput } from "../api/recordingClient";
import { isRetryableRecordingSessionError } from "../api/recordingClient";
import {
  drainFlowSyncCallbacks,
  replayUnknownMutation,
  retryAfterRevisionConflict,
} from "../state/mutationReplay";
import {
  browserOperationScheduler,
  OperationWatchdog,
} from "../state/operationWatchdog";

export const PUBLISH_RECOVERY_TIMEOUT_MS = 10 * 60 * 1000;

const REVISIONED_COMMANDS = new Set([
  "finalize",
  "reanalyze",
  "orchestrate_flow",
  "auto_fix_flow",
  "step_naming",
  "business_description",
  "llm_recommendations",
  "publish_request",
]);

export interface RecordingChannelCallbacks {
  onEvent(event: Record<string, any>, staleRevision: boolean): void;
  onSnapshot(snapshot?: Record<string, unknown> | null): void;
  onResumeInput(input?: RecordingSessionInput): void;
  onResumeReady(): void;
  onOpen(): void;
  onDisconnected(willReconnect: boolean): void;
  onConnectionError(detail: string, terminal: boolean): void;
  onOperationTimeout(operation: "publish", detail: string): void;
}

export interface RecordingChannel {
  send(message: Record<string, any>): boolean;
  sendPublish(message: Record<string, any>): boolean;
  acceptsPublishEvent(event: Record<string, unknown>): boolean;
  completePublish(): void;
  keepPublishPending(delay?: number): void;
  start(input: RecordingSessionInput, initialMessage: Record<string, unknown>): Promise<void>;
  stop(): void;
  isStarting(): boolean;
  hasPendingFlowMutations(): boolean;
  hasQueuedFlowMutations(): boolean;
  clearFlowMutations(): void;
  finishFlowMutation(operationId?: string): void;
  failFlowMutation(operationId?: string): void;
  retryFlowMutation(operationId?: string): void;
  runAfterFlowSync(callback: () => void): void;
  hasPendingPublish(): boolean;
}

function fallbackOperationId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `flow-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/**
 * Owns the V3 socket/session lifecycle and the revisioned command lane.
 * Rendering and legacy workbench state intentionally stay in PageRecorder.
 */
export function useRecordingChannel(
  controller: RecordingSessionController | undefined,
  callbacks: RecordingChannelCallbacks,
): RecordingChannel {
  const controllerRef = useRef(controller);
  const callbacksRef = useRef(callbacks);
  controllerRef.current = controller;
  callbacksRef.current = callbacks;

  const wsRef = useRef<WebSocket | null>(null);
  const socketAliveRef = useRef(false);
  const intentionalCloseRef = useRef(false);
  const shouldReconnectRef = useRef(false);
  const reconnectTimerRef = useRef<number | null>(null);
  const reconnectAttemptRef = useRef(0);
  const autoResumeAttemptedRef = useRef(false);
  const startingRef = useRef(false);
  const sessionEpochRef = useRef(0);

  const flowQueueRef = useRef<Array<Record<string, any>>>([]);
  const flowInFlightRef = useRef<Record<string, any> | null>(null);
  const afterFlowSyncRef = useRef<Array<() => void>>([]);

  const pendingPublishRef = useRef<Record<string, any> | null>(null);
  const publishRecoveryTimerRef = useRef<number | null>(null);
  const publishWatchdogRef = useRef<OperationWatchdog<"publish", number> | null>(null);
  if (!publishWatchdogRef.current) {
    publishWatchdogRef.current = new OperationWatchdog(
      browserOperationScheduler,
      () => expirePublish(),
    );
  }

  function sendRaw(message: unknown): boolean {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      try {
        ws.send(JSON.stringify(message));
        return true;
      } catch {
        ws.close();
      }
    }
    if (socketAliveRef.current && shouldReconnectRef.current && !intentionalCloseRef.current) {
      socketAliveRef.current = false;
      callbacksRef.current.onConnectionError("录制连接已断开，正在恢复会话", false);
    }
    return false;
  }

  function flushFlowQueue(): void {
    if (flowInFlightRef.current || !flowQueueRef.current.length) return;
    const queued = flowQueueRef.current.shift()!;
    const activeController = controllerRef.current;
    const next = activeController ? activeController.decorateMutation(queued) : queued;
    flowInFlightRef.current = next;
    if (sendRaw(next)) return;
    flowInFlightRef.current = null;
    if (activeController && shouldReconnectRef.current) {
      flowQueueRef.current.unshift(replayUnknownMutation(next));
    } else {
      flowQueueRef.current = [];
      afterFlowSyncRef.current = [];
    }
  }

  function enqueueFlowMutation(message: Record<string, any>): boolean {
    const ws = wsRef.current;
    const socketOpen = !!ws && ws.readyState === WebSocket.OPEN;
    if (!socketOpen && (!controllerRef.current || !shouldReconnectRef.current)) return sendRaw(message);
    const operationId = message.operation_id || controllerRef.current?.decorateMutation(message).operation_id || fallbackOperationId();
    flowQueueRef.current.push({ ...message, operation_id: operationId });
    if (socketOpen) flushFlowQueue();
    return true;
  }

  function send(message: Record<string, any>): boolean {
    if (message.type === "flow_update" || message.type === "flow_replace") return enqueueFlowMutation(message);
    const activeController = controllerRef.current;
    const outbound = activeController && REVISIONED_COMMANDS.has(String(message.type || ""))
      ? activeController.decorateMutation(message)
      : message;
    return sendRaw(outbound);
  }

  function finishFlowMutation(operationId?: string): void {
    const active = flowInFlightRef.current;
    if (!active) return;
    if (operationId && active.operation_id && operationId !== active.operation_id) return;
    flowInFlightRef.current = null;
    flushFlowQueue();
    if (!flowInFlightRef.current && !flowQueueRef.current.length && afterFlowSyncRef.current.length) {
      drainFlowSyncCallbacks(afterFlowSyncRef.current);
    }
  }

  function failFlowMutation(operationId?: string): void {
    const active = flowInFlightRef.current;
    if (operationId && active?.operation_id && operationId !== active.operation_id) return;
    flowInFlightRef.current = null;
    flowQueueRef.current = [];
    afterFlowSyncRef.current = [];
  }

  function retryFlowMutation(operationId?: string): void {
    const active = flowInFlightRef.current;
    if (!active) return;
    if (operationId && active.operation_id && operationId !== active.operation_id) return;
    const retry = retryAfterRevisionConflict(active);
    flowInFlightRef.current = null;
    flowQueueRef.current.unshift(retry);
    flushFlowQueue();
  }

  function runAfterFlowSync(callback: () => void): void {
    if (!flowInFlightRef.current && !flowQueueRef.current.length) {
      callback();
      return;
    }
    afterFlowSyncRef.current.push(callback);
  }

  function clearPublish(): void {
    if (publishRecoveryTimerRef.current != null) window.clearTimeout(publishRecoveryTimerRef.current);
    publishRecoveryTimerRef.current = null;
    publishWatchdogRef.current?.clear("publish");
    pendingPublishRef.current = null;
  }

  function expirePublish(): void {
    if (!pendingPublishRef.current) return;
    clearPublish();
    callbacksRef.current.onOperationTimeout(
      "publish",
      "发布结果超过10分钟仍未确认，已停止自动重放；服务端已提交的结果仍可通过刷新会话恢复",
    );
  }

  function keepPublishPending(delay = 1200): void {
    if (publishRecoveryTimerRef.current != null || !pendingPublishRef.current || !shouldReconnectRef.current) return;
    publishRecoveryTimerRef.current = window.setTimeout(() => {
      publishRecoveryTimerRef.current = null;
      const pending = pendingPublishRef.current;
      if (!pending || !shouldReconnectRef.current || intentionalCloseRef.current) return;
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      if (sendRaw(pending)) keepPublishPending(2500);
    }, delay);
  }

  function acceptsPublishEvent(event: Record<string, unknown>): boolean {
    const pending = pendingPublishRef.current;
    if (!pending) return true;
    const pendingId = String(pending.operation_id || "");
    const eventId = String(event.operation_id || "");
    return !pendingId || !eventId || pendingId === eventId;
  }

  function sendPublish(message: Record<string, any>): boolean {
    if (pendingPublishRef.current) return false;
    const activeController = controllerRef.current;
    const outbound = activeController ? activeController.decorateMutation(message) : message;
    pendingPublishRef.current = outbound;
    publishWatchdogRef.current?.arm("publish", PUBLISH_RECOVERY_TIMEOUT_MS);
    if (send(outbound)) return true;
    clearPublish();
    return false;
  }

  function bindSocket(ws: WebSocket, initialMessage?: Record<string, unknown>): void {
    wsRef.current = ws;
    ws.onopen = () => {
      reconnectAttemptRef.current = 0;
      socketAliveRef.current = true;
      controllerRef.current?.markConnected();
      callbacksRef.current.onOpen();
      if (initialMessage) sendRaw(initialMessage);
      else sendRaw({ type: "refresh_flow_spec" });
      sendRaw({ type: "analysis_status" });
      flushFlowQueue();
      if (!initialMessage && pendingPublishRef.current) keepPublishPending();
    };
    ws.onmessage = (event) => {
      let message: Record<string, any>;
      try {
        message = JSON.parse(event.data);
      } catch {
        return;
      }
      const currentRevision = controllerRef.current?.observeServerEvent(message) ?? true;
      callbacksRef.current.onEvent(message, !currentRevision);
    };
    ws.onerror = () => {
      if (wsRef.current !== ws || intentionalCloseRef.current || !shouldReconnectRef.current) return;
      callbacksRef.current.onConnectionError("WebSocket 连接失败，正在恢复会话", false);
    };
    ws.onclose = () => {
      if (wsRef.current !== ws) return;
      socketAliveRef.current = false;
      const willReconnect = !intentionalCloseRef.current && shouldReconnectRef.current;
      const interrupted = flowInFlightRef.current;
      flowInFlightRef.current = null;
      if (willReconnect && interrupted) {
        flowQueueRef.current.unshift(replayUnknownMutation(interrupted));
      }
      if (!willReconnect) {
        flowQueueRef.current = [];
        afterFlowSyncRef.current = [];
        clearPublish();
      }
      callbacksRef.current.onDisconnected(willReconnect);
      if (!willReconnect) return;
      controllerRef.current?.markReconnecting("录制连接已断开");
      scheduleResume();
    };
  }

  function handleResumeFailure(error: unknown): boolean {
    const detail = error instanceof Error ? error.message : "恢复录制会话失败";
    if (isRetryableRecordingSessionError(error)) {
      controllerRef.current?.markReconnecting(detail);
      callbacksRef.current.onConnectionError(`${detail}，正在重试`, false);
      return false;
    }
    shouldReconnectRef.current = false;
    clearPublish();
    controllerRef.current?.clear();
    callbacksRef.current.onConnectionError(detail, true);
    return true;
  }

  async function resumePersisted(): Promise<void> {
    const activeController = controllerRef.current;
    if (!activeController || startingRef.current) return;
    startingRef.current = true;
    const epoch = ++sessionEpochRef.current;
    shouldReconnectRef.current = true;
    intentionalCloseRef.current = false;
    callbacksRef.current.onResumeInput(activeController.state.input);
    try {
      const connection = await activeController.resume();
      if (epoch !== sessionEpochRef.current || !shouldReconnectRef.current) return;
      const knownRevision = controllerRef.current?.state.revision ?? 0;
      if (connection.response.current_revision >= knownRevision) {
        callbacksRef.current.onSnapshot(connection.response.snapshot);
      }
      callbacksRef.current.onResumeReady();
      bindSocket(new WebSocket(connection.websocketUrl));
    } catch (error) {
      if (epoch !== sessionEpochRef.current || !shouldReconnectRef.current) return;
      if (!handleResumeFailure(error)) scheduleResume();
    } finally {
      startingRef.current = false;
    }
  }

  function scheduleResume(): void {
    if (!controllerRef.current || !shouldReconnectRef.current || reconnectTimerRef.current != null) return;
    const attempt = ++reconnectAttemptRef.current;
    const delay = Math.min(250 * (2 ** Math.min(attempt - 1, 6)), 15000);
    reconnectTimerRef.current = window.setTimeout(() => {
      reconnectTimerRef.current = null;
      if (!shouldReconnectRef.current || intentionalCloseRef.current) return;
      void resumePersisted();
    }, delay);
  }

  async function start(input: RecordingSessionInput, initialMessage: Record<string, unknown>): Promise<void> {
    const activeController = controllerRef.current;
    if (!activeController) throw new Error("V3 录制会话不可用");
    if (startingRef.current) return;
    startingRef.current = true;
    const epoch = ++sessionEpochRef.current;
    if (reconnectTimerRef.current != null) window.clearTimeout(reconnectTimerRef.current);
    reconnectTimerRef.current = null;
    clearPublish();
    flowInFlightRef.current = null;
    flowQueueRef.current = [];
    afterFlowSyncRef.current = [];
    const previous = wsRef.current;
    wsRef.current = null;
    previous?.close();
    intentionalCloseRef.current = false;
    shouldReconnectRef.current = true;
    autoResumeAttemptedRef.current = true;
    socketAliveRef.current = false;
    try {
      const connection = await activeController.create(input);
      if (epoch !== sessionEpochRef.current || !shouldReconnectRef.current) return;
      callbacksRef.current.onSnapshot(connection.response.snapshot);
      bindSocket(new WebSocket(connection.websocketUrl), initialMessage);
    } catch (error) {
      if (epoch === sessionEpochRef.current) {
        shouldReconnectRef.current = false;
        socketAliveRef.current = false;
        activeController.markError(error instanceof Error ? error.message : "创建录制会话失败");
      }
      throw error;
    } finally {
      startingRef.current = false;
    }
  }

  function stop(): void {
    sessionEpochRef.current += 1;
    shouldReconnectRef.current = false;
    intentionalCloseRef.current = true;
    startingRef.current = false;
    if (reconnectTimerRef.current != null) window.clearTimeout(reconnectTimerRef.current);
    reconnectTimerRef.current = null;
    clearPublish();
    sendRaw({ type: "stop" });
    const ws = wsRef.current;
    wsRef.current = null;
    ws?.close();
    flowInFlightRef.current = null;
    flowQueueRef.current = [];
    afterFlowSyncRef.current = [];
    controllerRef.current?.clear();
  }

  useEffect(() => {
    if (
      !controller?.state.recordingId
      || (controller.state.status !== "restored" && controller.state.status !== "reconnecting")
      || autoResumeAttemptedRef.current
    ) return;
    autoResumeAttemptedRef.current = true;
    void resumePersisted();
  // resumePersisted reads the latest controller/callbacks through refs.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [controller?.state.recordingId, controller?.state.status]);

  useEffect(() => () => {
    intentionalCloseRef.current = true;
    shouldReconnectRef.current = false;
    autoResumeAttemptedRef.current = false;
    startingRef.current = false;
    sessionEpochRef.current += 1;
    if (reconnectTimerRef.current != null) window.clearTimeout(reconnectTimerRef.current);
    reconnectTimerRef.current = null;
    clearPublish();
    const ws = wsRef.current;
    wsRef.current = null;
    ws?.close();
  }, []);

  return {
    send,
    sendPublish,
    acceptsPublishEvent,
    completePublish: clearPublish,
    keepPublishPending,
    start,
    stop,
    isStarting: () => startingRef.current,
    hasPendingFlowMutations: () => !!flowInFlightRef.current || flowQueueRef.current.length > 0,
    hasQueuedFlowMutations: () => flowQueueRef.current.length > 0,
    clearFlowMutations: () => failFlowMutation(),
    finishFlowMutation,
    failFlowMutation,
    retryFlowMutation,
    runAfterFlowSync,
    hasPendingPublish: () => pendingPublishRef.current !== null,
  };
}
