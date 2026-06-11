import type {
  ClientMessage,
  RpcBridgeEvent,
  RpcCommand,
  RpcAgentEndEvent,
  RpcAgentStartEvent,
  RpcImageContent,
  RpcResponse,
  RpcSessionState,
  RpcSessionStats,
  RpcSlashCommand,
  RpcThinkingLevel,
  RpcTranscriptDeltaEvent,
  RpcTreeEntry,
  RpcTranscriptStartEvent,
  RpcWorkspaceEntry,
  RpcWorkspaceFile,
  RpcExtensionUIRequest,
  RpcExtensionUIResponse,
  RpcGitBranch,
  RpcGitRepoState,
  RpcQueuedMessage,
  RpcQueueUpdateEvent,
  RpcTranscriptMessage,
  RpcTranscriptPage,
  RpcTranscriptSnapshotEvent,
  RpcTranscriptUpsertEvent,
  RpcSessionStatsEvent,
  RpcWorkspaceSummary,
  ServerMessage,
} from "@pi-web/bridge/types";
import {
  normalizeRpcModel,
  upsertModel,
  type RpcModelInfo,
} from "../utils/models";
import {
  normalizeTranscript,
  transcriptConfigState,
  type PendingTranscriptSessionEvent,
} from "../utils/transcript";

type TranscriptConfigSnapshot = ReturnType<typeof transcriptConfigState>;

const normalizeTranscriptEntries = normalizeTranscript as (
  messages: readonly unknown[],
) => TranscriptEntry[];
const readTranscriptConfigState = transcriptConfigState as (
  messages: readonly unknown[],
) => TranscriptConfigSnapshot;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type ConnectionStatus = "connecting" | "connected" | "disconnected";

export type TranscriptEntry = RpcTranscriptMessage;
export type TranscriptDelta = RpcTranscriptDeltaEvent;
export type TranscriptStream = RpcTranscriptStartEvent;
export type TreeEntry = RpcTreeEntry;

type DialogExtensionUIRequest = Extract<
  RpcExtensionUIRequest,
  { method: "select" | "confirm" | "input" | "editor" }
>;

type PendingDisplayTranscriptDelta = {
  payload: Omit<TranscriptDelta, "delta">;
  pendingText: string;
  pendingUnits: number;
  queuedAt: number;
  started: boolean;
};

export interface SessionEntry {
  id: string;
  name: string;
  path: string;
  isRunning?: boolean;
  timestamp?: string;
  updatedAt?: string;
  workspaceId?: string;
  workspaceName?: string;
  workspacePath?: string;
}

export type WorkspaceSummary = RpcWorkspaceSummary;

function readFiniteNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function normalizeSessionStats(value: unknown): RpcSessionStats | null {
  if (!value || typeof value !== "object") return null;
  const data = value as Partial<RpcSessionStats>;
  return {
    tokens:
      typeof data.tokens === "number" && Number.isFinite(data.tokens)
        ? data.tokens
        : null,
    contextWindow: readFiniteNumber(data.contextWindow),
    percent:
      typeof data.percent === "number" && Number.isFinite(data.percent)
        ? data.percent
        : null,
    messageCount: readFiniteNumber(data.messageCount),
    cost: readFiniteNumber(data.cost),
    inputTokens: readFiniteNumber(data.inputTokens),
    outputTokens: readFiniteNumber(data.outputTokens),
    cacheReadTokens: readFiniteNumber(data.cacheReadTokens),
    cacheWriteTokens: readFiniteNumber(data.cacheWriteTokens),
  };
}

function normalizeGitBranch(value: unknown): RpcGitBranch | null {
  if (!value || typeof value !== "object") return null;
  const data = value as Partial<RpcGitBranch>;
  if (typeof data.name !== "string" || typeof data.shortName !== "string") {
    return null;
  }
  if (data.kind !== "local" && data.kind !== "remote") {
    return null;
  }

  return {
    name: data.name,
    shortName: data.shortName,
    kind: data.kind,
    remoteName:
      typeof data.remoteName === "string" ? data.remoteName : undefined,
    isCurrent: data.isCurrent === true,
  };
}

function normalizeGitRepoState(value: unknown): RpcGitRepoState | null {
  if (!value || typeof value !== "object") return null;
  const data = value as Partial<RpcGitRepoState>;
  if (typeof data.repoRoot !== "string" || typeof data.headLabel !== "string") {
    return null;
  }

  const branches = Array.isArray(data.branches)
    ? data.branches
        .map(branch => normalizeGitBranch(branch))
        .filter((branch): branch is RpcGitBranch => branch !== null)
    : [];

  return {
    repoRoot: data.repoRoot,
    headLabel: data.headLabel,
    currentBranch:
      typeof data.currentBranch === "string" ? data.currentBranch : undefined,
    detached: data.detached === true,
    isDirty: data.isDirty === true,
    branches,
  };
}

function normalizeQueuedMessage(value: unknown): RpcQueuedMessage | null {
  if (!value || typeof value !== "object") return null;
  const data = value as Partial<RpcQueuedMessage>;
  if (typeof data.text !== "string") {
    return null;
  }

  const images = Array.isArray(data.images)
    ? data.images.filter(
        (image): image is RpcImageContent =>
          Boolean(image) &&
          image.type === "image" &&
          typeof image.data === "string" &&
          typeof image.mimeType === "string",
      )
    : [];

  return {
    text: data.text,
    images,
    timestamp:
      typeof data.timestamp === "number" && Number.isFinite(data.timestamp)
        ? data.timestamp
        : Date.now(),
    queueType: data.queueType === "steering" ? "steering" : "followUp",
  };
}

function normalizeThinkingLevel(value: unknown): RpcThinkingLevel | null {
  switch (value) {
    case "normal":
    case "medium":
      return "medium";
    case "off":
    case "minimal":
    case "low":
    case "high":
    case "xhigh":
      return value;
    default:
      return null;
  }
}

function summarizeErrorMessage(message: string, fallback: string): string {
  const line = message
    .split(/\r?\n/)
    .map(part => part.trim())
    .find(Boolean);
  if (!line) return fallback;
  return line.length > 220 ? `${line.slice(0, 217)}...` : line;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const WORKSPACE_ENTRIES_REFRESH_MS = 10_000;
const MAX_RECONNECT_DELAY = 30_000;
const SESSION_ROUTE_PARAM = "session";

// ---------------------------------------------------------------------------
// Bridge state (module-level singletons)
// ---------------------------------------------------------------------------

let ws: WebSocket | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let reconnectDelay = 1000;
let disposed = false;
let requestIdCounter = 0;
let pendingTranscriptConfigEventCounter = 0;
let workspaceEntriesRequest: Promise<RpcWorkspaceEntry[]> | null = null;
let workspaceEntriesRequestId: string | null = null;
let workspaceEntriesRequestContextKey: string | null = null;
let workspaceEntriesLoadedContextKey: string | null = null;
let workspaceEntriesLoadedAt = 0;
let gitRepoStateRequest: Promise<RpcGitRepoState | null> | null = null;
let displayTranscriptDeltaTimer: ReturnType<typeof setTimeout> | null = null;
let displayTranscriptDeltaTimerDueAt = 0;
let displayTranscriptDeltaLastFlushAt = 0;
let displayTranscriptDeltaIngressRateEma = 0;
let displayTranscriptDeltaIngressJitterEma = 0;
let displayTranscriptDeltaLastIngressAt = 0;
let displayTranscriptDeltaLastIngressGapMs = 0;
const pendingDisplayTranscriptDeltas = new Map<
  string,
  PendingDisplayTranscriptDelta
>();

const DISPLAY_TRANSCRIPT_DELTA_FRAME_MS = 16;
const DISPLAY_TRANSCRIPT_DELTA_MIN_START_BUFFER_MS = 24;
const DISPLAY_TRANSCRIPT_DELTA_MAX_START_BUFFER_MS = 96;
const DISPLAY_TRANSCRIPT_DELTA_MIN_START_BUFFER_UNITS = 12;
const DISPLAY_TRANSCRIPT_DELTA_MAX_START_BUFFER_UNITS = 48;
const DISPLAY_TRANSCRIPT_DELTA_START_BUFFER_JITTER_FACTOR = 1.5;
const DISPLAY_TRANSCRIPT_DELTA_INGRESS_RATE_ALPHA = 0.22;
const DISPLAY_TRANSCRIPT_DELTA_INGRESS_JITTER_ALPHA = 0.2;
const DISPLAY_TRANSCRIPT_DELTA_BASE_UNITS_PER_SECOND = 160;
const DISPLAY_TRANSCRIPT_DELTA_MAX_UNITS_PER_SECOND = 720;
const DISPLAY_TRANSCRIPT_DELTA_TARGET_LATENCY_MS = 320;
const DISPLAY_TRANSCRIPT_DELTA_FOLLOW_FACTOR = 0.9;
const DISPLAY_TRANSCRIPT_DELTA_DRAIN_FACTOR = 1.15;

const pendingRequests = new Map<
  string,
  {
    resolve: (response: RpcResponse) => void;
    reject: (error: Error) => void;
    timer: ReturnType<typeof setTimeout>;
  }
>();

// ---------------------------------------------------------------------------
// Reactive state (module-level, initialized once)
// ---------------------------------------------------------------------------

let _connectionStatus = $state<ConnectionStatus>("disconnected");
let _rawTranscript = $state<TranscriptEntry[]>([]);
let _transcriptSessionPath = $state<string | null>(null);
let _transcriptHasOlder = $state(false);
let _transcriptOldestCursor = $state<string | null>(null);
let _transcriptNewestCursor = $state<string | null>(null);
let _transcriptInitialLoading = $state(true);
let _transcriptPageLoading = $state(false);
let _transcript = $state<TranscriptEntry[]>([]);
let _transcriptDeltas = $state.raw<TranscriptDelta[]>([]);
let _transcriptStreams = $state.raw<TranscriptStream[]>([]);
let _sessionState = $state<RpcSessionState | null>(null);
let _pendingTranscriptConfigEvent = $state<
  (PendingTranscriptSessionEvent & { sessionPath: string | null }) | null
>(null);
let _workspaces = $state<WorkspaceSummary[]>([]);
let _workspaceSessions = $state<Record<string, SessionEntry[]>>({});
let _workspaceSessionLoaded = $state<Record<string, boolean>>({});
let _workspaceSessionLoading = $state<Record<string, boolean>>({});
let _treeEntries = $state<TreeEntry[]>([]);
let _activeTreeSessionPath = $state<string | null>(null);
let _liveSessionPath = $state<string | null>(null);
let _runningSessionPaths = $state<string[]>([]);
let _workspaceSessionCursors = $state<Record<string, string | null>>({});
let _commands = $state<RpcSlashCommand[]>([]);
let _workspaceEntries = $state<RpcWorkspaceEntry[]>([]);
let _workspaceEntriesLoaded = $state(false);
let _workspaceEntriesLoading = $state(false);
let _availableModels = $state<RpcModelInfo[]>([]);
let _currentModel = $state<RpcModelInfo | null>(null);
let _currentThinkingLevel = $state<RpcThinkingLevel | null>(null);
let _isStreaming = $state(false);
let _compactingRequestCount = $state(0);
let _remoteCompactionActive = $state(false);
let _queuedUserMessages = $state<RpcQueuedMessage[]>([]);
let _sessionStats = $state<RpcSessionStats | null>(null);
let _gitRepoState = $state<RpcGitRepoState | null>(null);
let _gitRepoLoading = $state(false);
let _gitBranchSwitching = $state(false);
let _reconnectCount = $state(0);
let _lastDisconnectReason = $state("");
let _connectionError = $state("");
let _pendingExtensionRequest = $state<DialogExtensionUIRequest | null>(null);
let _notifications = $state<
  Array<{ message: string; notifyType?: string; id: string }>
>([]);
let _statusEntries = $state<Record<string, string>>({});
let _widgetEntries = $state<
  Record<string, { lines: string[]; placement?: string }>
>({});
let _prefillText = $state<string | null>(null);

// ---------------------------------------------------------------------------
// Derived state
// ---------------------------------------------------------------------------

let connectionStatus = $derived(_connectionStatus);
let transcript = $derived(_transcript);
let transcriptDeltas = $derived(_transcriptDeltas);
let transcriptStreams = $derived(_transcriptStreams);
let transcriptHasOlder = $derived(_transcriptHasOlder);
let transcriptInitialLoading = $derived(_transcriptInitialLoading);
let transcriptPageLoading = $derived(_transcriptPageLoading);
let sessionState = $derived(_sessionState);
let workspaces = $derived(_workspaces);
let workspaceSessions = $derived(_workspaceSessions);
let workspaceSessionLoaded = $derived(_workspaceSessionLoaded);
let workspaceSessionLoading = $derived(_workspaceSessionLoading);
let sessions = $derived.by(() => Object.values(_workspaceSessions).flat());
let treeEntries = $derived(_treeEntries);
let activeTreeSessionPath = $derived(_activeTreeSessionPath);
let liveSessionPath = $derived(_liveSessionPath);
let runningSessionPaths = $derived(_runningSessionPaths);
let workspaceSessionCursors = $derived(_workspaceSessionCursors);
let commands = $derived(_commands);
let workspaceEntries = $derived(_workspaceEntries);
let workspaceEntriesLoading = $derived(_workspaceEntriesLoading);
let availableModels = $derived(_availableModels);
let currentModel = $derived(_currentModel);
let currentThinkingLevel = $derived(_currentThinkingLevel);
let isStreaming = $derived(_isStreaming);
let isCompacting = $derived(
  _compactingRequestCount > 0 || _remoteCompactionActive,
);
let queuedUserMessages = $derived(_queuedUserMessages);
let sessionStats = $derived(_sessionStats);
let gitRepoState = $derived(_gitRepoState);
let gitRepoLoading = $derived(_gitRepoLoading);
let gitBranchSwitching = $derived(_gitBranchSwitching);
let reconnectCount = $derived(_reconnectCount);
let lastDisconnectReason = $derived(_lastDisconnectReason);
let connectionError = $derived(_connectionError);
let pendingExtensionRequest = $derived(_pendingExtensionRequest);
let notifications = $derived(_notifications);
let statusEntries = $derived(_statusEntries);
let widgetEntries = $derived(_widgetEntries);
let prefillText = $derived(_prefillText);
let pendingMessageCount = $derived(_sessionState?.pendingMessageCount ?? 0);
let isReconnecting = $derived(
  _connectionStatus === "disconnected" && !disposed && !_connectionError,
);
let activeSessionPath = $derived(
  _activeTreeSessionPath ?? _sessionState?.sessionFile ?? null,
);
let hasSessionOutline = $derived(
  Boolean(_activeTreeSessionPath) ||
    _transcript.length > 0 ||
    _treeEntries.length > 0,
);

let visiblePendingTranscriptConfigEvent = $derived.by(
  (): PendingTranscriptSessionEvent | null => {
    const pending = _pendingTranscriptConfigEvent;
    if (!pending) return null;
    return pending.sessionPath === _transcriptSessionPath
      ? {
          key: pending.key,
          model: pending.model,
          thinkingLevel: pending.thinkingLevel,
          insertAfterMessageKey: pending.insertAfterMessageKey,
        }
      : null;
  },
);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function currentRawTranscriptEntries(): TranscriptEntry[] {
  return _rawTranscript as unknown as TranscriptEntry[];
}

function syncTranscript() {
  _transcript = normalizeTranscriptEntries(currentRawTranscriptEntries());
}

function resetReconnectDelay() {
  reconnectDelay = 1000;
}

function scheduleReconnect() {
  if (_connectionError) return;
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(() => {
    if (!disposed) connect();
  }, reconnectDelay);
  reconnectDelay = Math.min(reconnectDelay * 2, MAX_RECONNECT_DELAY);
}

function updateCurrentModel(value: unknown) {
  _currentModel = normalizeRpcModel(value);
  if (_currentModel) {
    _availableModels = upsertModel(_availableModels, _currentModel);
  }
}

function updateAvailableModels(values: readonly unknown[]) {
  _availableModels = values
    .map(value => normalizeRpcModel(value))
    .filter((model): model is RpcModelInfo => model !== null);

  if (_currentModel) {
    _availableModels = upsertModel(_availableModels, _currentModel);
  }
}

function getDisplayedSessionPath(): string | null {
  return _activeTreeSessionPath ?? _sessionState?.sessionFile ?? null;
}

function getDisplayedWorkspacePath(): string | null {
  const swp = _sessionState?.workspacePath?.trim();
  if (swp) return swp;

  const dsp = getDisplayedSessionPath();
  if (!dsp) return null;

  const ms = sessions.find(s => s.path === dsp);
  return ms?.workspacePath?.trim() ?? ms?.workspaceId?.trim() ?? null;
}

function getWorkspaceEntriesContextKey(): string | null {
  return getDisplayedWorkspacePath() ?? getDisplayedSessionPath();
}

function invalidateWorkspaceEntries() {
  _workspaceEntriesLoaded = false;
  _workspaceEntriesLoading = false;
  _workspaceEntries = [];
  workspaceEntriesRequest = null;
  workspaceEntriesRequestId = null;
  workspaceEntriesRequestContextKey = null;
  workspaceEntriesLoadedContextKey = null;
  workspaceEntriesLoadedAt = 0;
}

function readSessionRoutePath(): string | null {
  const search = globalThis.location?.search;
  if (typeof search !== "string") return null;
  const value = new URLSearchParams(search).get(SESSION_ROUTE_PARAM);
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

function buildSessionRouteUrl(sessionPath: string | null): string | null {
  const currentLocation = globalThis.location;
  if (!currentLocation) return null;

  const params = new URLSearchParams(currentLocation.search ?? "");
  if (sessionPath) {
    params.set(SESSION_ROUTE_PARAM, sessionPath);
  } else {
    params.delete(SESSION_ROUTE_PARAM);
  }

  const pathname = currentLocation.pathname ?? "/";
  const search = params.toString();
  const hash = currentLocation.hash ?? "";
  return `${pathname}${search ? `?${search}` : ""}${hash}`;
}

function writeSessionRoutePath(
  sessionPath: string | null,
  mode: "push" | "replace" = "replace",
) {
  const historyApi = globalThis.history;
  const nextUrl = buildSessionRouteUrl(sessionPath);
  if (!historyApi || !nextUrl) return;
  if (readSessionRoutePath() === sessionPath) return;

  if (mode === "push" && typeof historyApi.pushState === "function") {
    historyApi.pushState(null, "", nextUrl);
    return;
  }

  if (typeof historyApi.replaceState === "function") {
    historyApi.replaceState(null, "", nextUrl);
  }
}

async function restoreLiveSessionState() {
  _activeTreeSessionPath = null;
  _treeEntries = [];
  _sessionStats = null;

  await Promise.all([
    sendCommand({ type: "get_messages", direction: "latest", limit: 40 }),
    sendCommand({ type: "get_state" }),
  ]);
}

async function applySessionRouteFromLocation() {
  const routeSessionPath = readSessionRoutePath();
  const currentSessionPath = getDisplayedSessionPath();
  if (routeSessionPath === currentSessionPath) return;

  try {
    if (!routeSessionPath) {
      await restoreLiveSessionState();
      return;
    }

    const response = await sendCommand({
      type: "switch_session",
      sessionPath: routeSessionPath,
    });
    if (!response.success) {
      pushNotification(
        summarizeErrorMessage(
          response.error ?? "Failed to open session from URL",
          "Failed to open session from URL",
        ),
        "error",
      );
      writeSessionRoutePath(null, "replace");
    }
  } catch {
    // Leave unchanged
  }
}

function handleSessionRoutePopState() {
  if (_connectionStatus !== "connected") return;
  void applySessionRouteFromLocation();
}

function startSessionRouteSync() {
  // Route sync is done manually at state change points (switchSession, newSession, etc.),
  // not via $effect to avoid infinite loops with history.replaceState → popstate.
  globalThis.addEventListener?.("popstate", handleSessionRoutePopState);
}

function stopSessionRouteSync() {
  globalThis.removeEventListener?.("popstate", handleSessionRoutePopState);
}

function shouldApplyQueueUpdate(sessionPath: string | null): boolean {
  const dsp = getDisplayedSessionPath();
  if (!sessionPath) return dsp === null;
  if (!dsp) return true;
  return dsp === sessionPath;
}

function applyQueuedMessages(
  steering: readonly RpcQueuedMessage[],
  followUp: readonly RpcQueuedMessage[],
  options?: { sessionPath?: string | null },
) {
  const sp = options?.sessionPath ?? getDisplayedSessionPath();
  if (!shouldApplyQueueUpdate(sp)) return;

  _queuedUserMessages = [...steering, ...followUp];
  if (_sessionState) {
    _sessionState = {
      ..._sessionState,
      pendingMessageCount: steering.length + followUp.length,
    };
  }
}

function clearQueuedSteeringMessages(options?: {
  sessionPath?: string | null;
}) {
  const sp = options?.sessionPath ?? getDisplayedSessionPath();
  if (!shouldApplyQueueUpdate(sp)) return;

  const nextQueuedMessages = _queuedUserMessages.filter(
    message => message.queueType !== "steering",
  );
  if (nextQueuedMessages.length === _queuedUserMessages.length) return;

  _queuedUserMessages = nextQueuedMessages;
  if (_sessionState) {
    _sessionState = {
      ..._sessionState,
      pendingMessageCount: nextQueuedMessages.length,
    };
  }
}

function resetGitRepoState() {
  _gitRepoState = null;
  _gitRepoLoading = false;
  _gitBranchSwitching = false;
  gitRepoStateRequest = null;
}

function workspaceDisplayName(workspacePath: string): string {
  const parts = workspacePath.split(/[\\/]/).filter(Boolean);
  return parts.at(-1) ?? workspacePath;
}

function workspaceUpdatedAtValue(updatedAt?: string): number {
  const parsed = Date.parse(updatedAt ?? "");
  return Number.isFinite(parsed) ? parsed : Number.NEGATIVE_INFINITY;
}

function compareWorkspaceSummaries(
  left: WorkspaceSummary,
  right: WorkspaceSummary,
): number {
  const updatedAtDelta =
    workspaceUpdatedAtValue(right.updatedAt) -
    workspaceUpdatedAtValue(left.updatedAt);
  if (updatedAtDelta !== 0) return updatedAtDelta;

  const nameDelta = left.name.localeCompare(right.name);
  if (nameDelta !== 0) return nameDelta;
  return left.path.localeCompare(right.path);
}

function normalizeWorkspaceSummary(value: unknown): WorkspaceSummary | null {
  if (!value || typeof value !== "object") return null;
  const data = value as Partial<WorkspaceSummary>;
  if (typeof data.path !== "string") return null;

  const path = data.path.trim();
  if (!path) return null;

  return {
    id: typeof data.id === "string" && data.id.trim() ? data.id : path,
    name:
      typeof data.name === "string" && data.name.trim()
        ? data.name
        : workspaceDisplayName(path),
    path,
    updatedAt:
      typeof data.updatedAt === "string" && data.updatedAt.trim()
        ? data.updatedAt
        : undefined,
  };
}

function ensureWorkspaceSummary(
  workspacePath?: string | null,
  workspaceName?: string | null,
  updatedAt?: string | null,
) {
  const path = workspacePath?.trim();
  if (!path) return;
  const existing = _workspaces.find(workspace => workspace.path === path);
  const nextUpdatedAt =
    workspaceUpdatedAtValue(updatedAt ?? undefined) >=
    workspaceUpdatedAtValue(existing?.updatedAt)
      ? (updatedAt ?? undefined)
      : existing?.updatedAt;
  const nextWorkspace: WorkspaceSummary = {
    id: existing?.id ?? path,
    name: workspaceName?.trim() || existing?.name || workspaceDisplayName(path),
    path,
    updatedAt: nextUpdatedAt,
  };

  _workspaces = existing
    ? _workspaces
        .map(workspace => (workspace.path === path ? nextWorkspace : workspace))
        .sort(compareWorkspaceSummaries)
    : [..._workspaces, nextWorkspace].sort(compareWorkspaceSummaries);
}

function setWorkspaceSessions(
  workspacePath: string,
  entries: SessionEntry[],
  mergeMode: "replace" | "append" = "replace",
) {
  _workspaceSessions = {
    ..._workspaceSessions,
    [workspacePath]:
      mergeMode === "append"
        ? mergeSessionEntries(_workspaceSessions[workspacePath] ?? [], entries)
        : [...entries].sort(compareSessionEntries),
  };
}

function setWorkspaceSessionLoading(workspacePath: string, loading: boolean) {
  _workspaceSessionLoading = {
    ..._workspaceSessionLoading,
    [workspacePath]: loading,
  };
}

function markWorkspaceSessionLoaded(workspacePath: string) {
  _workspaceSessionLoaded = {
    ..._workspaceSessionLoaded,
    [workspacePath]: true,
  };
}

function removeSessionFromWorkspaceSessions(sessionPath: string) {
  const nextSessions: Record<string, SessionEntry[]> = {};
  for (const [workspacePath, entries] of Object.entries(_workspaceSessions)) {
    nextSessions[workspacePath] = entries.filter(
      entry => entry.path !== sessionPath,
    );
  }
  _workspaceSessions = nextSessions;

  const nextRunning = new Set(_runningSessionPaths);
  nextRunning.delete(sessionPath);
  _runningSessionPaths = [...nextRunning];
}

function setSessionRunning(sessionPath: string | null, isRunning: boolean) {
  if (!sessionPath) return;

  const next = new Set(_runningSessionPaths);
  if (isRunning) {
    next.add(sessionPath);
  } else {
    next.delete(sessionPath);
  }
  _runningSessionPaths = [...next];

  const nextWorkspaceSessions: Record<string, SessionEntry[]> = {};
  for (const [workspacePath, entries] of Object.entries(_workspaceSessions)) {
    nextWorkspaceSessions[workspacePath] = entries.map(session =>
      session.path === sessionPath ? { ...session, isRunning } : session,
    );
  }
  _workspaceSessions = nextWorkspaceSessions;
}

function syncRunningSessionsFromEntries(entries: readonly SessionEntry[]) {
  const nextRunning = new Set(_runningSessionPaths);
  for (const entry of entries) {
    nextRunning.delete(entry.path);
    if (entry.isRunning) nextRunning.add(entry.path);
  }
  _runningSessionPaths = [...nextRunning];
}

function compareSessionEntries(
  left: SessionEntry,
  right: SessionEntry,
): number {
  const leftTime = Date.parse(left.updatedAt ?? left.timestamp ?? "");
  const rightTime = Date.parse(right.updatedAt ?? right.timestamp ?? "");
  const delta =
    (Number.isFinite(rightTime) ? rightTime : Number.NEGATIVE_INFINITY) -
    (Number.isFinite(leftTime) ? leftTime : Number.NEGATIVE_INFINITY);
  return delta || right.path.localeCompare(left.path);
}

function mergeSessionEntries(
  current: readonly SessionEntry[],
  incoming: readonly SessionEntry[],
): SessionEntry[] {
  const nextByPath = new Map(current.map(s => [s.path, s]));
  for (const s of incoming) {
    nextByPath.set(s.path, s);
  }
  return [...nextByPath.values()].sort(compareSessionEntries);
}

function clearPendingTranscriptConfigEvent() {
  _pendingTranscriptConfigEvent = null;
}

function pendingTranscriptAnchorKey(): string | null {
  const msg = _transcript.at(-1);
  return msg?.transcriptKey ?? msg?.id ?? null;
}

function transcriptHasMessageKey(messageKey: string): boolean {
  return _transcript.some(
    (msg, idx) =>
      (msg.transcriptKey ?? msg.id ?? `message:${idx}`) === messageKey,
  );
}

function reanchorMissingPendingTranscriptConfigEvent() {
  const pending = _pendingTranscriptConfigEvent;
  if (!pending || pending.sessionPath !== _transcriptSessionPath) return;

  const ak = pending.insertAfterMessageKey;
  if (typeof ak !== "string" || !ak.trim()) return;
  if (transcriptHasMessageKey(ak)) return;

  _pendingTranscriptConfigEvent = {
    ...pending,
    insertAfterMessageKey: pendingTranscriptAnchorKey(),
  };
}

function samePendingTranscriptModel(
  a: PendingTranscriptSessionEvent["model"] | undefined,
  b: PendingTranscriptSessionEvent["model"] | undefined,
): boolean {
  if (!a || !b) return false;
  return a.id === b.id && a.provider === b.provider;
}

function reconcilePendingTranscriptConfigEvent() {
  const pending = _pendingTranscriptConfigEvent;
  if (!pending || pending.sessionPath !== _transcriptSessionPath) return;

  const cs = readTranscriptConfigState(_rawTranscript);
  const next = { ...pending };
  if (samePendingTranscriptModel(next.model, cs.model)) {
    next.model = undefined;
  }
  if (next.thinkingLevel && next.thinkingLevel === cs.thinkingLevel) {
    next.thinkingLevel = undefined;
  }

  _pendingTranscriptConfigEvent =
    next.model || next.thinkingLevel ? next : null;
}

function updatePendingTranscriptConfigEvent(change: {
  model?: RpcModelInfo | null;
  thinkingLevel?: RpcThinkingLevel | null;
}) {
  reconcilePendingTranscriptConfigEvent();

  const sp = _transcriptSessionPath;
  const existing = _pendingTranscriptConfigEvent;
  const existingForSession =
    existing && existing.sessionPath === sp ? existing : null;
  const nextKey = existingForSession
    ? existingForSession.key
    : `pending-session-event:${++pendingTranscriptConfigEventCounter}`;
  const next: PendingTranscriptSessionEvent & { sessionPath: string | null } = {
    key: nextKey,
    sessionPath: sp,
    model: existingForSession?.model,
    thinkingLevel: existingForSession?.thinkingLevel,
    insertAfterMessageKey: existingForSession
      ? existingForSession.insertAfterMessageKey
      : pendingTranscriptAnchorKey(),
  };

  if ("model" in change) {
    next.model = change.model
      ? { provider: change.model.provider, id: change.model.id }
      : undefined;
  }
  if ("thinkingLevel" in change) {
    next.thinkingLevel = change.thinkingLevel ?? undefined;
  }

  _pendingTranscriptConfigEvent =
    next.model || next.thinkingLevel ? next : null;
}

function sendEnvelope(msg: ClientMessage) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(msg));
  }
}

function createRequestId(): string {
  const ca = globalThis.crypto;
  if (ca?.randomUUID) return ca.randomUUID();

  if (ca?.getRandomValues) {
    const bytes = new Uint8Array(16);
    ca.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, b => b.toString(16).padStart(2, "0"));
    return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10).join("")}`;
  }

  requestIdCounter += 1;
  return `req_${Date.now().toString(36)}_${requestIdCounter}_${Math.random().toString(36).slice(2)}`;
}

function pushNotification(message: string, notifyType?: string) {
  _notifications = [
    ..._notifications,
    { message, notifyType, id: `local-notify:${createRequestId()}` },
  ];
}

export function dismissNotification(id: string) {
  _notifications = _notifications.filter(n => n.id !== id);
}

// ---------------------------------------------------------------------------
// RPC helpers
// ---------------------------------------------------------------------------

async function sendCommand(
  payload: RpcCommand,
  options?: { timeoutMs?: number },
): Promise<RpcResponse> {
  return new Promise((resolve, reject) => {
    const id = payload.id ?? createRequestId();
    const cmd = { ...payload, id };
    const timer = setTimeout(() => {
      pendingRequests.delete(id);
      reject(new Error(`RPC timeout: ${cmd.type}`));
    }, options?.timeoutMs ?? 15_000);
    pendingRequests.set(id, { resolve, reject, timer });
    sendEnvelope({ type: "command", payload: cmd });
  });
}

function normalizeTranscriptEntry(
  entry: TranscriptEntry | RpcTranscriptMessage,
  fallbackKey: string,
): TranscriptEntry {
  return {
    ...entry,
    transcriptKey:
      typeof entry.transcriptKey === "string" && entry.transcriptKey
        ? entry.transcriptKey
        : typeof entry.id === "string" && entry.id
          ? entry.id
          : fallbackKey,
  };
}

function pendingDisplayTranscriptDeltaKey(
  payload: Pick<
    TranscriptDelta,
    | "sessionPath"
    | "transcriptKey"
    | "messageId"
    | "role"
    | "blockType"
    | "contentIndex"
  >,
): string {
  return [
    payload.sessionPath ?? "",
    payload.transcriptKey,
    payload.messageId ?? "",
    payload.role,
    payload.blockType,
    payload.contentIndex,
  ].join("\u0000");
}

function deltaMatchesMessage(
  delta: Pick<TranscriptDelta, "transcriptKey" | "messageId">,
  message: Pick<TranscriptEntry, "transcriptKey" | "id">,
): boolean {
  if (message.transcriptKey && delta.transcriptKey === message.transcriptKey) {
    return true;
  }
  return Boolean(message.id && delta.messageId === message.id);
}

function clearDisplayTranscriptDeltaTimer() {
  if (!displayTranscriptDeltaTimer) return;
  clearTimeout(displayTranscriptDeltaTimer);
  displayTranscriptDeltaTimer = null;
  displayTranscriptDeltaTimerDueAt = 0;
}

function clampNumber(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function resetDisplayTranscriptDeltaIngressState() {
  displayTranscriptDeltaIngressRateEma = 0;
  displayTranscriptDeltaIngressJitterEma = 0;
  displayTranscriptDeltaLastIngressAt = 0;
  displayTranscriptDeltaLastIngressGapMs = 0;
}

function displayTranscriptDeltaUnits(text: string): number {
  let units = 0;
  for (let index = 0; index < text.length; index += 1) {
    units += text[index]!.charCodeAt(0) < 256 ? 1 : 2;
  }
  return units;
}

function recordDisplayTranscriptDeltaIngress(units: number, now: number) {
  if (units <= 0) return;
  if (!displayTranscriptDeltaLastIngressAt) {
    displayTranscriptDeltaLastIngressAt = now;
    displayTranscriptDeltaIngressRateEma = Math.max(
      displayTranscriptDeltaIngressRateEma,
      DISPLAY_TRANSCRIPT_DELTA_BASE_UNITS_PER_SECOND,
    );
    displayTranscriptDeltaIngressJitterEma = Math.max(
      displayTranscriptDeltaIngressJitterEma,
      DISPLAY_TRANSCRIPT_DELTA_FRAME_MS,
    );
    return;
  }

  const gapMs = Math.max(1, now - displayTranscriptDeltaLastIngressAt);
  const instantaneousRate = (units * 1000) / gapMs;
  displayTranscriptDeltaIngressRateEma = displayTranscriptDeltaIngressRateEma
    ? displayTranscriptDeltaIngressRateEma +
      (instantaneousRate - displayTranscriptDeltaIngressRateEma) *
        DISPLAY_TRANSCRIPT_DELTA_INGRESS_RATE_ALPHA
    : instantaneousRate;

  const jitterSample = displayTranscriptDeltaLastIngressGapMs
    ? Math.abs(gapMs - displayTranscriptDeltaLastIngressGapMs)
    : gapMs;
  displayTranscriptDeltaIngressJitterEma =
    displayTranscriptDeltaIngressJitterEma
      ? displayTranscriptDeltaIngressJitterEma +
        (jitterSample - displayTranscriptDeltaIngressJitterEma) *
          DISPLAY_TRANSCRIPT_DELTA_INGRESS_JITTER_ALPHA
      : jitterSample;

  displayTranscriptDeltaLastIngressAt = now;
  displayTranscriptDeltaLastIngressGapMs = gapMs;
}

function takeDisplayTranscriptDeltaChunk(
  text: string,
  targetUnits: number,
): string {
  if (!text || targetUnits <= 0) return "";

  let unitCount = 0;
  let boundaryIndex = 0;
  let boundaryUnits = 0;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index]!;
    unitCount += char.charCodeAt(0) < 256 ? 1 : 2;
    if (char === "\n" || /[.!?。！？]/.test(char)) {
      boundaryIndex = index + 1;
      boundaryUnits = unitCount;
    }
    if (unitCount >= targetUnits) {
      if (
        boundaryIndex > 0 &&
        boundaryUnits >= Math.max(2, Math.floor(targetUnits * 0.6))
      ) {
        return text.slice(0, boundaryIndex);
      }
      return text.slice(0, index + 1);
    }
  }

  return text;
}

function totalPendingDisplayTranscriptDeltaUnits(): number {
  let totalUnits = 0;
  for (const pending of pendingDisplayTranscriptDeltas.values()) {
    totalUnits += pending.pendingUnits;
  }
  return totalUnits;
}

function oldestPendingDisplayTranscriptDeltaAgeMs(now: number): number {
  let oldestQueuedAt = Number.POSITIVE_INFINITY;
  for (const pending of pendingDisplayTranscriptDeltas.values()) {
    oldestQueuedAt = Math.min(oldestQueuedAt, pending.queuedAt);
  }
  if (!Number.isFinite(oldestQueuedAt)) return 0;
  return Math.max(0, now - oldestQueuedAt);
}

function displayTranscriptDeltaStartBufferMs(): number {
  const adaptiveMs =
    displayTranscriptDeltaIngressJitterEma > 0
      ? displayTranscriptDeltaIngressJitterEma *
        DISPLAY_TRANSCRIPT_DELTA_START_BUFFER_JITTER_FACTOR
      : DISPLAY_TRANSCRIPT_DELTA_MIN_START_BUFFER_MS;
  return clampNumber(
    Math.round(adaptiveMs),
    DISPLAY_TRANSCRIPT_DELTA_MIN_START_BUFFER_MS,
    DISPLAY_TRANSCRIPT_DELTA_MAX_START_BUFFER_MS,
  );
}

function displayTranscriptDeltaStartBufferUnits(startBufferMs: number): number {
  const expectedUnits = Math.round(
    (displayTranscriptDeltaRate(totalPendingDisplayTranscriptDeltaUnits()) *
      startBufferMs) /
      1000,
  );
  return clampNumber(
    expectedUnits,
    DISPLAY_TRANSCRIPT_DELTA_MIN_START_BUFFER_UNITS,
    DISPLAY_TRANSCRIPT_DELTA_MAX_START_BUFFER_UNITS,
  );
}

function displayTranscriptDeltaRate(units: number): number {
  const backlogRate = Math.ceil(
    (units * 1000) / DISPLAY_TRANSCRIPT_DELTA_TARGET_LATENCY_MS,
  );
  const ingressRate = Math.ceil(
    displayTranscriptDeltaIngressRateEma *
      DISPLAY_TRANSCRIPT_DELTA_FOLLOW_FACTOR,
  );
  const floorRate = _isStreaming
    ? DISPLAY_TRANSCRIPT_DELTA_BASE_UNITS_PER_SECOND
    : Math.ceil(
        DISPLAY_TRANSCRIPT_DELTA_BASE_UNITS_PER_SECOND *
          DISPLAY_TRANSCRIPT_DELTA_DRAIN_FACTOR,
      );
  const desiredRate = Math.max(floorRate, backlogRate, ingressRate);
  const boostedRate = _isStreaming
    ? desiredRate
    : Math.ceil(desiredRate * DISPLAY_TRANSCRIPT_DELTA_DRAIN_FACTOR);
  return clampNumber(
    boostedRate,
    DISPLAY_TRANSCRIPT_DELTA_BASE_UNITS_PER_SECOND,
    DISPLAY_TRANSCRIPT_DELTA_MAX_UNITS_PER_SECOND,
  );
}

function scheduleDisplayTranscriptDeltaFlush(
  delayMs: number = DISPLAY_TRANSCRIPT_DELTA_FRAME_MS,
) {
  if (pendingDisplayTranscriptDeltas.size === 0) return;
  const normalizedDelay = Math.max(0, Math.round(delayMs));
  const nextDueAt = Date.now() + normalizedDelay;
  if (
    displayTranscriptDeltaTimer &&
    displayTranscriptDeltaTimerDueAt <= nextDueAt
  ) {
    return;
  }
  clearDisplayTranscriptDeltaTimer();
  displayTranscriptDeltaTimerDueAt = nextDueAt;
  displayTranscriptDeltaTimer = setTimeout(() => {
    displayTranscriptDeltaTimer = null;
    displayTranscriptDeltaTimerDueAt = 0;
    flushDisplayTranscriptDeltasFrame();
  }, normalizedDelay);
}

function appendTranscriptDeltas(deltas: readonly TranscriptDelta[]) {
  if (deltas.length === 0) return;
  _transcriptDeltas = [..._transcriptDeltas, ...deltas];
  reconcilePendingTranscriptConfigEvent();
}

function flushDisplayTranscriptDeltasFrame() {
  clearDisplayTranscriptDeltaTimer();
  if (pendingDisplayTranscriptDeltas.size === 0) {
    displayTranscriptDeltaLastFlushAt = 0;
    return;
  }

  const now = Date.now();
  const totalUnits = totalPendingDisplayTranscriptDeltaUnits();
  const allPendingUnstarted = [
    ...pendingDisplayTranscriptDeltas.values(),
  ].every(pending => !pending.started);

  // Hold the first paint briefly when ingress is jittery so the display can emit steadily.
  if (_isStreaming && allPendingUnstarted) {
    const ageMs = oldestPendingDisplayTranscriptDeltaAgeMs(now);
    const startBufferMs = displayTranscriptDeltaStartBufferMs();
    const startBufferUnits =
      displayTranscriptDeltaStartBufferUnits(startBufferMs);
    if (totalUnits < startBufferUnits && ageMs < startBufferMs) {
      scheduleDisplayTranscriptDeltaFlush(startBufferMs - ageMs);
      return;
    }
  }

  const elapsedMs = displayTranscriptDeltaLastFlushAt
    ? Math.max(
        DISPLAY_TRANSCRIPT_DELTA_FRAME_MS,
        now - displayTranscriptDeltaLastFlushAt,
      )
    : DISPLAY_TRANSCRIPT_DELTA_FRAME_MS;
  displayTranscriptDeltaLastFlushAt = now;

  let remainingUnits = Math.max(
    1,
    Math.round((displayTranscriptDeltaRate(totalUnits) * elapsedMs) / 1000),
  );
  const nextDeltas: TranscriptDelta[] = [];

  for (const [key, pending] of pendingDisplayTranscriptDeltas) {
    if (remainingUnits <= 0) break;

    const chunk = takeDisplayTranscriptDeltaChunk(
      pending.pendingText,
      remainingUnits,
    );
    if (!chunk) {
      pendingDisplayTranscriptDeltas.delete(key);
      continue;
    }

    const chunkUnits = displayTranscriptDeltaUnits(chunk);
    nextDeltas.push({ ...pending.payload, delta: chunk });
    pending.pendingText = pending.pendingText.slice(chunk.length);
    pending.pendingUnits = Math.max(0, pending.pendingUnits - chunkUnits);
    pending.started = true;
    remainingUnits -= chunkUnits;
    if (!pending.pendingText) {
      pendingDisplayTranscriptDeltas.delete(key);
    }
  }

  if (nextDeltas.length > 0) {
    appendTranscriptDeltas(nextDeltas);
  }

  if (pendingDisplayTranscriptDeltas.size === 0) {
    displayTranscriptDeltaLastFlushAt = 0;
    if (!_isStreaming) resetDisplayTranscriptDeltaIngressState();
    return;
  }
  scheduleDisplayTranscriptDeltaFlush();
}

function flushAllDisplayTranscriptDeltas() {
  clearDisplayTranscriptDeltaTimer();
  if (pendingDisplayTranscriptDeltas.size === 0) {
    displayTranscriptDeltaLastFlushAt = 0;
    return;
  }

  const nextDeltas = [...pendingDisplayTranscriptDeltas.values()]
    .map(pending =>
      pending.pendingText
        ? { ...pending.payload, delta: pending.pendingText }
        : null,
    )
    .filter((delta): delta is TranscriptDelta => delta !== null);

  pendingDisplayTranscriptDeltas.clear();
  displayTranscriptDeltaLastFlushAt = 0;
  appendTranscriptDeltas(nextDeltas);
}

function clearDisplayTranscriptDeltasForMessage(
  message: Pick<TranscriptEntry, "transcriptKey" | "id">,
) {
  for (const [key, pending] of pendingDisplayTranscriptDeltas) {
    if (deltaMatchesMessage(pending.payload, message)) {
      pendingDisplayTranscriptDeltas.delete(key);
    }
  }
  if (pendingDisplayTranscriptDeltas.size === 0) {
    displayTranscriptDeltaLastFlushAt = 0;
    clearDisplayTranscriptDeltaTimer();
  }
}

function clearDisplayTranscriptDeltas() {
  pendingDisplayTranscriptDeltas.clear();
  displayTranscriptDeltaLastFlushAt = 0;
  clearDisplayTranscriptDeltaTimer();
  resetDisplayTranscriptDeltaIngressState();
}

function clearTranscriptDeltasForMessage(
  message: Pick<TranscriptEntry, "transcriptKey" | "id">,
) {
  _transcriptDeltas = _transcriptDeltas.filter(
    delta => !deltaMatchesMessage(delta, message),
  );
  clearDisplayTranscriptDeltasForMessage(message);
}

function clearTranscriptStreamsForMessage(
  message: Pick<TranscriptEntry, "transcriptKey" | "id">,
) {
  _transcriptStreams = _transcriptStreams.filter(stream => {
    if (
      message.transcriptKey &&
      stream.message.transcriptKey === message.transcriptKey
    ) {
      return false;
    }
    if (message.id && stream.message.id === message.id) return false;
    return true;
  });
}

function clearTranscriptDeltas() {
  _transcriptDeltas = [];
  clearDisplayTranscriptDeltas();
}

function clearTranscriptStreams() {
  _transcriptStreams = [];
}

function replaceTranscript(
  entries: readonly (TranscriptEntry | RpcTranscriptMessage)[],
  sessionPath: string | null = _transcriptSessionPath,
) {
  const prevSp = _transcriptSessionPath;
  _rawTranscript = entries.map((entry, idx) =>
    normalizeTranscriptEntry(entry, `snapshot:${idx}`),
  ) as TranscriptEntry[];
  clearTranscriptDeltas();
  clearTranscriptStreams();
  syncTranscript();
  if (prevSp !== sessionPath || _rawTranscript.length === 0) {
    clearPendingTranscriptConfigEvent();
  }
  if (prevSp !== sessionPath) {
    _queuedUserMessages = [];
  }
  _transcriptSessionPath = sessionPath;
  reanchorMissingPendingTranscriptConfigEvent();
  reconcilePendingTranscriptConfigEvent();
}

function applyTranscriptPage(
  page: RpcTranscriptPage,
  mode: "replace" | "prepend" = "replace",
) {
  const prevSp = _transcriptSessionPath;
  const normalized = page.messages.map((entry, idx) =>
    normalizeTranscriptEntry(entry, `snapshot:${idx}`),
  ) as TranscriptEntry[];

  if (mode === "prepend") {
    const existingKeys = new Set<string | undefined>();
    for (const entry of currentRawTranscriptEntries()) {
      existingKeys.add(entry.transcriptKey);
    }
    const merged = normalized.filter(
      entry => !existingKeys.has(entry.transcriptKey),
    );
    _rawTranscript = [
      ...merged,
      ...currentRawTranscriptEntries(),
    ] as TranscriptEntry[];
  } else {
    _rawTranscript = normalized;
  }
  clearTranscriptDeltas();
  clearTranscriptStreams();
  syncTranscript();

  const nsp = page.sessionPath ?? null;
  if (prevSp !== nsp) {
    clearPendingTranscriptConfigEvent();
    _queuedUserMessages = [];
  }
  _transcriptSessionPath = nsp;
  _transcriptHasOlder = page.hasOlder;
  _transcriptOldestCursor = page.oldestCursor ?? null;
  _transcriptNewestCursor = page.newestCursor ?? null;
  _transcriptInitialLoading = false;
  _transcriptPageLoading = false;
  if (mode === "replace") reanchorMissingPendingTranscriptConfigEvent();
  reconcilePendingTranscriptConfigEvent();
}

function shouldReplaceSessionTranscript(sessionPath: string | null): boolean {
  return _rawTranscript.length === 0 || _transcriptSessionPath !== sessionPath;
}

function applySessionTranscriptPage(page: RpcTranscriptPage) {
  if (
    page.messages.length === 0 &&
    !shouldReplaceSessionTranscript(page.sessionPath ?? null) &&
    currentRawTranscriptEntries().some(
      e =>
        typeof e.transcriptKey === "string" &&
        e.transcriptKey.startsWith("live:"),
    )
  ) {
    _transcriptHasOlder = page.hasOlder;
    _transcriptOldestCursor = page.oldestCursor ?? null;
    _transcriptNewestCursor = page.newestCursor ?? null;
    _transcriptInitialLoading = false;
    _transcriptPageLoading = false;
    return;
  }

  applyTranscriptPage(page, "replace");
}

function applyTreeEntriesUpdate(
  entries: readonly TreeEntry[],
  sessionPath: string | null,
  options?: { force?: boolean },
) {
  if (
    !options?.force &&
    _activeTreeSessionPath &&
    _activeTreeSessionPath !== sessionPath
  ) {
    return;
  }
  _treeEntries = [...entries];
  _activeTreeSessionPath = sessionPath;
}

function applySessionSnapshotResponse(
  data:
    | {
        transcript: RpcTranscriptPage;
        treeEntries?: TreeEntry[];
        sessionId?: string;
        sessionName?: string;
        sessionPath?: string;
        workspacePath?: string;
      }
    | undefined,
  options?: { refreshState?: boolean },
): boolean {
  if (!data?.transcript) return false;

  const prevSp = getDisplayedSessionPath();
  const prevWp = getWorkspaceEntriesContextKey();

  applySessionTranscriptPage(data.transcript);
  if (data.sessionPath) _liveSessionPath = data.sessionPath;
  if (Array.isArray(data.treeEntries)) {
    applyTreeEntriesUpdate(data.treeEntries, data.sessionPath ?? null, {
      force: true,
    });
  } else if (data.sessionPath) {
    _activeTreeSessionPath = data.sessionPath;
  }
  if (data.workspacePath) {
    ensureWorkspaceSummary(data.workspacePath);
  }
  if (data.sessionId) {
    _sessionState = {
      ..._sessionState,
      sessionId: data.sessionId,
      sessionName: data.sessionName,
      sessionFile: data.sessionPath ?? _sessionState?.sessionFile,
      workspacePath: data.workspacePath ?? _sessionState?.workspacePath,
    } as RpcSessionState;
  }
  if (prevSp !== getDisplayedSessionPath()) {
    resetGitRepoState();
    _isStreaming = false;
  }
  if (prevWp !== getWorkspaceEntriesContextKey()) invalidateWorkspaceEntries();
  if (options?.refreshState) sendCommand({ type: "get_state" }).catch(() => {});
  return true;
}

async function loadOlderTranscriptPage() {
  if (
    _transcriptPageLoading ||
    !_transcriptHasOlder ||
    !_transcriptOldestCursor
  )
    return;
  _transcriptPageLoading = true;
  try {
    const resp = await sendCommand({
      type: "get_messages",
      direction: "older",
      cursor: _transcriptOldestCursor,
      limit: 40,
    });
    if (!resp.success) _transcriptPageLoading = false;
  } catch {
    _transcriptPageLoading = false;
  }
}

function upsertTranscriptMessage(
  entry: TranscriptEntry | RpcTranscriptMessage,
  sessionPath: string | null = _transcriptSessionPath,
) {
  const normalized = normalizeTranscriptEntry(
    entry,
    `live:${_rawTranscript.length}`,
  );
  if (shouldReplaceSessionTranscript(sessionPath)) {
    _transcriptSessionPath = sessionPath;
  }
  let idx = -1;
  for (const [ci, cur] of currentRawTranscriptEntries().entries()) {
    if (cur.transcriptKey === normalized.transcriptKey) {
      idx = ci;
      break;
    }
  }

  if (idx >= 0) {
    const updated = currentRawTranscriptEntries().slice();
    updated[idx] = { ...updated[idx], ...normalized };
    _rawTranscript = updated as TranscriptEntry[];
    clearTranscriptDeltasForMessage(normalized);
    clearTranscriptStreamsForMessage(normalized);
    syncTranscript();
    reconcilePendingTranscriptConfigEvent();
    return;
  }

  const nt = currentRawTranscriptEntries().slice();
  nt.push(normalized);
  _rawTranscript = nt as TranscriptEntry[];
  clearTranscriptDeltasForMessage(normalized);
  clearTranscriptStreamsForMessage(normalized);
  syncTranscript();
  reconcilePendingTranscriptConfigEvent();
}

function applyTranscriptStart(payload: RpcTranscriptStartEvent) {
  const sessionPath = payload.sessionPath ?? null;
  if (shouldReplaceSessionTranscript(sessionPath)) {
    _transcriptSessionPath = sessionPath;
  }

  const normalized = normalizeTranscriptEntry(
    payload.message,
    `live:${_transcriptStreams.length}`,
  );
  const nextStream = {
    ...payload,
    message: normalized,
  };
  const idx = _transcriptStreams.findIndex(stream => {
    if (stream.message.transcriptKey === normalized.transcriptKey) return true;
    return Boolean(normalized.id && stream.message.id === normalized.id);
  });
  if (idx >= 0) {
    const nextStreams = _transcriptStreams.slice();
    nextStreams[idx] = nextStream;
    _transcriptStreams = nextStreams;
  } else {
    _transcriptStreams = [..._transcriptStreams, nextStream];
  }
  reconcilePendingTranscriptConfigEvent();
}

function applyTranscriptDelta(payload: RpcTranscriptDeltaEvent) {
  const sessionPath = payload.sessionPath ?? null;
  if (shouldReplaceSessionTranscript(sessionPath)) {
    clearDisplayTranscriptDeltas();
    _transcriptSessionPath = sessionPath;
  }

  if (payload.blockType !== "text") {
    flushAllDisplayTranscriptDeltas();
    appendTranscriptDeltas([payload]);
    return;
  }

  const deltaKey = pendingDisplayTranscriptDeltaKey(payload);
  if (
    pendingDisplayTranscriptDeltas.size > 0 &&
    !pendingDisplayTranscriptDeltas.has(deltaKey)
  ) {
    flushAllDisplayTranscriptDeltas();
  }

  const now = Date.now();
  const deltaUnits = displayTranscriptDeltaUnits(payload.delta);
  recordDisplayTranscriptDeltaIngress(deltaUnits, now);

  const existing = pendingDisplayTranscriptDeltas.get(deltaKey);
  if (existing) {
    existing.pendingText += payload.delta;
    existing.pendingUnits += deltaUnits;
  } else {
    pendingDisplayTranscriptDeltas.set(deltaKey, {
      payload: { ...payload },
      pendingText: payload.delta,
      pendingUnits: deltaUnits,
      queuedAt: now,
      started: false,
    });
  }
  scheduleDisplayTranscriptDeltaFlush();
}

function appendCompactErrorMessage(message: string) {
  const detail = message.trim();
  const em = detail ? `Compaction failed: ${detail}` : "Compaction failed";
  upsertTranscriptMessage({
    transcriptKey: `local:compact-error:${Date.now()}:${requestIdCounter}`,
    role: "assistant",
    stopReason: "error",
    errorMessage: em,
    timestamp: new Date().toISOString(),
  });
}

function setCompactionState(compacting: boolean) {
  _remoteCompactionActive = compacting;
  if (!_sessionState) return;
  _sessionState = { ..._sessionState, isCompacting: compacting };
}

function sendPrompt(
  message: string,
  images?: RpcImageContent[],
  streamingBehavior: "steer" | "followUp" = "followUp",
) {
  if (_isStreaming) {
    _queuedUserMessages = [
      ..._queuedUserMessages,
      {
        text: message,
        images: images ?? [],
        timestamp: Date.now(),
        queueType: streamingBehavior === "steer" ? "steering" : "followUp",
      },
    ];
  }
  sendEnvelope({
    type: "command",
    payload: { type: "prompt", message, images, streamingBehavior },
  });
}

async function dequeueQueuedMessage(
  idx: number,
): Promise<RpcQueuedMessage | null> {
  if (!Number.isInteger(idx) || idx < 0) return null;

  const queuedMessage = _queuedUserMessages[idx];
  if (!queuedMessage || queuedMessage.queueType === "steering") {
    return null;
  }

  const followUpIndex = _queuedUserMessages
    .slice(0, idx)
    .filter(message => message.queueType !== "steering").length;

  try {
    const resp = await sendCommand({
      type: "dequeue_follow_up_message",
      index: followUpIndex,
    });
    if (!resp.success) {
      pushNotification(
        summarizeErrorMessage(
          resp.error ?? "Failed to update queued messages",
          "Failed to update queued messages",
        ),
        "error",
      );
      return null;
    }

    const removed = normalizeQueuedMessage(
      (resp.data as { removed?: RpcQueuedMessage } | undefined)?.removed,
    );
    if (removed) {
      _queuedUserMessages = _queuedUserMessages.filter((_, qi) => qi !== idx);
      if (_sessionState) {
        _sessionState = {
          ..._sessionState,
          pendingMessageCount: Math.max(
            0,
            _sessionState.pendingMessageCount - 1,
          ),
        };
      }
    }
    return removed;
  } catch (error) {
    pushNotification(
      summarizeErrorMessage(
        error instanceof Error
          ? error.message
          : "Failed to update queued messages",
        "Failed to update queued messages",
      ),
      "error",
    );
    return null;
  }
}

export async function cancelQueuedMessage(index: number): Promise<boolean> {
  return (await dequeueQueuedMessage(index)) !== null;
}

export async function editQueuedMessage(
  index: number,
): Promise<{ text: string; images: RpcImageContent[] } | null> {
  const item = await dequeueQueuedMessage(index);
  if (!item) return null;
  return { text: item.text, images: item.images };
}

export async function fetchWorkspaceEntries(
  force: boolean = false,
): Promise<RpcWorkspaceEntry[]> {
  const wp = getDisplayedWorkspacePath();
  const ck = getWorkspaceEntriesContextKey();
  const contextChanged = workspaceEntriesLoadedContextKey !== ck;
  const isStale =
    workspaceEntriesLoadedAt > 0 &&
    Date.now() - workspaceEntriesLoadedAt >= WORKSPACE_ENTRIES_REFRESH_MS;
  const shouldRefresh =
    force || !_workspaceEntriesLoaded || contextChanged || isStale;

  if (!shouldRefresh) return _workspaceEntries;

  if (
    workspaceEntriesRequest &&
    workspaceEntriesRequestContextKey === ck &&
    !force
  ) {
    return workspaceEntriesRequest;
  }

  if (_connectionStatus !== "connected") return _workspaceEntries;

  if (contextChanged) {
    _workspaceEntriesLoaded = false;
    _workspaceEntries = [];
  }

  const rid = createRequestId();
  _workspaceEntriesLoading = true;
  workspaceEntriesRequestId = rid;
  workspaceEntriesRequestContextKey = ck;
  workspaceEntriesRequest = sendCommand({
    id: rid,
    type: "list_workspace_entries",
    force: force || contextChanged || isStale,
    ...(wp ? { workspacePath: wp } : {}),
  })
    .then(() => _workspaceEntries)
    .catch(() => _workspaceEntries)
    .finally(() => {
      if (workspaceEntriesRequestId !== rid) return;
      _workspaceEntriesLoading = false;
      workspaceEntriesRequest = null;
      workspaceEntriesRequestId = null;
      workspaceEntriesRequestContextKey = null;
    });

  return workspaceEntriesRequest;
}

export async function readWorkspaceFile(
  path: string,
): Promise<RpcWorkspaceFile> {
  const wp = getDisplayedWorkspacePath();
  const resp = await sendCommand({
    type: "read_workspace_file",
    path,
    ...(wp ? { workspacePath: wp } : {}),
  });
  if (!resp.success)
    throw new Error(resp.error ?? "Failed to read workspace file");
  const data = resp.data;
  if (!data || typeof data !== "object")
    throw new Error("Failed to parse workspace file contents");
  return data as RpcWorkspaceFile;
}

export async function loadGitRepoState(
  force: boolean = false,
): Promise<RpcGitRepoState | null> {
  if (_gitRepoState && !force) return _gitRepoState;
  if (gitRepoStateRequest && !force) return gitRepoStateRequest;
  if (_connectionStatus !== "connected") return _gitRepoState;

  _gitRepoLoading = true;
  gitRepoStateRequest = sendCommand({ type: "list_git_branches" })
    .then(resp => {
      if (!resp.success) {
        pushNotification(
          summarizeErrorMessage(
            resp.error ?? "Failed to load git branches",
            "Failed to load git branches",
          ),
          "error",
        );
        return _gitRepoState;
      }
      const state = normalizeGitRepoState(resp.data);
      _gitRepoState = state;
      if (!state) pushNotification("Failed to parse git branch data", "error");
      return state;
    })
    .catch(error => {
      pushNotification(
        summarizeErrorMessage(
          error instanceof Error
            ? error.message
            : "Failed to load git branches",
          "Failed to load git branches",
        ),
        "error",
      );
      return _gitRepoState;
    })
    .finally(() => {
      _gitRepoLoading = false;
      gitRepoStateRequest = null;
    });

  return gitRepoStateRequest;
}

function applyGitRepoMutation(state: RpcGitRepoState | null) {
  _gitRepoState = state;
  if (state && _sessionState) {
    _sessionState = { ..._sessionState, gitBranch: state.headLabel };
  }
  invalidateWorkspaceEntries();
  void fetchWorkspaceEntries(true).catch(() => {});
}

export async function switchGitBranch(
  branchName: string,
): Promise<RpcGitRepoState | null> {
  if (!branchName.trim() || _connectionStatus !== "connected") return null;
  _gitBranchSwitching = true;

  try {
    const resp = await sendCommand({ type: "switch_git_branch", branchName });
    if (!resp.success) {
      pushNotification(
        summarizeErrorMessage(
          resp.error ?? "Failed to switch git branch",
          "Failed to switch git branch",
        ),
        "error",
      );
      return null;
    }
    const state = normalizeGitRepoState(resp.data);
    if (!state) {
      pushNotification("Failed to parse git branch data", "error");
      return null;
    }
    applyGitRepoMutation(state);
    return state;
  } catch (error) {
    pushNotification(
      summarizeErrorMessage(
        error instanceof Error ? error.message : "Failed to switch git branch",
        "Failed to switch git branch",
      ),
      "error",
    );
    return null;
  } finally {
    _gitBranchSwitching = false;
  }
}

export async function createGitBranch(
  branchName: string,
): Promise<RpcGitRepoState | null> {
  if (!branchName.trim() || _connectionStatus !== "connected") return null;
  _gitBranchSwitching = true;

  try {
    const resp = await sendCommand({ type: "create_git_branch", branchName });
    if (!resp.success) {
      pushNotification(
        summarizeErrorMessage(
          resp.error ?? "Failed to create git branch",
          "Failed to create git branch",
        ),
        "error",
      );
      return null;
    }
    const state = normalizeGitRepoState(resp.data);
    if (!state) {
      pushNotification("Failed to parse git branch data", "error");
      return null;
    }
    applyGitRepoMutation(state);
    return state;
  } catch (error) {
    pushNotification(
      summarizeErrorMessage(
        error instanceof Error ? error.message : "Failed to create git branch",
        "Failed to create git branch",
      ),
      "error",
    );
    return null;
  } finally {
    _gitBranchSwitching = false;
  }
}

export async function abortGeneration() {
  if (!_isStreaming) return null;

  const response = await sendCommand({ type: "abort" });
  if (response.success) {
    clearQueuedSteeringMessages();
  }
  return response;
}

export async function loadWorkspaceSessions(options: {
  workspacePath: string;
  cursor?: string | null;
  limit?: number;
  query?: string;
  merge?: "replace" | "append";
}): Promise<RpcResponse> {
  setWorkspaceSessionLoading(options.workspacePath, true);
  try {
    const resp = await sendCommand({
      type: "list_sessions",
      workspacePath: options.workspacePath,
      cursor: options.cursor ?? undefined,
      limit: options.limit ?? 50,
      query: options.query,
      includeActive: true,
      merge: options.merge ?? "append",
    });
    if (!resp.success) {
      setWorkspaceSessionLoading(options.workspacePath, false);
    }
    return resp;
  } catch (error) {
    setWorkspaceSessionLoading(options.workspacePath, false);
    throw error;
  }
}

export async function refreshWorkspaces(): Promise<RpcResponse> {
  return sendCommand({
    type: "list_workspaces",
  });
}

export async function switchSession(sessionPath: string): Promise<RpcResponse> {
  const resp = await sendCommand({ type: "switch_session", sessionPath });
  if (resp.success) {
    const data = resp.data as { sessionPath?: string } | undefined;
    writeSessionRoutePath(data?.sessionPath ?? sessionPath, "push");
  }
  return resp;
}

export async function newSession(workspacePath: string): Promise<RpcResponse> {
  const resp = await sendCommand({ type: "new_session", workspacePath });
  if (resp.success) {
    const data = resp.data as { sessionPath?: string } | undefined;
    writeSessionRoutePath(data?.sessionPath ?? null, "push");
  }
  return resp;
}

export function registerWorkspace(
  workspacePath?: string,
): Promise<RpcResponse> {
  return sendCommand(
    { type: "register_workspace", workspacePath },
    { timeoutMs: 300_000 },
  );
}

export async function compactSession(customInstructions?: string) {
  _compactingRequestCount += 1;

  try {
    const resp = await sendCommand(
      { type: "compact", customInstructions },
      { timeoutMs: 120_000 },
    );
    if (!resp.success) {
      appendCompactErrorMessage(resp.error ?? "Unknown compaction error");
    }
    return resp;
  } catch (error) {
    appendCompactErrorMessage(
      error instanceof Error ? error.message : String(error),
    );
    throw error;
  } finally {
    _compactingRequestCount = Math.max(0, _compactingRequestCount - 1);
  }
}

export async function setThinkingLevel(level: RpcThinkingLevel) {
  const resp = await sendCommand({ type: "set_thinking_level", level });
  if (resp.success) {
    _currentThinkingLevel = normalizeThinkingLevel(level);
    updatePendingTranscriptConfigEvent({
      thinkingLevel: _currentThinkingLevel,
    });
  }
  return resp;
}

export async function setAutoCompactionEnabled(enabled: boolean) {
  const resp = await sendCommand({ type: "set_auto_compaction", enabled });
  if (resp.success && _sessionState) {
    _sessionState = { ..._sessionState, autoCompactionEnabled: enabled };
  }
  return resp;
}

export async function deleteSession(sessionPath: string): Promise<RpcResponse> {
  const resp = await sendCommand({ type: "delete_session", sessionPath });
  if (resp.success) removeSessionFromWorkspaceSessions(sessionPath);
  return resp;
}

export function respondToUIRequest(payload: RpcExtensionUIResponse) {
  _pendingExtensionRequest = null;
  sendEnvelope({ type: "extension_ui_response", payload });
}

// ---------------------------------------------------------------------------
// Message handling
// ---------------------------------------------------------------------------

function handleServerMessage(raw: MessageEvent) {
  let envelope: ServerMessage;
  try {
    envelope = JSON.parse(raw.data as string) as ServerMessage;
  } catch {
    return;
  }

  if (envelope.type === "response") {
    handleResponse(envelope.payload);
  } else if (envelope.type === "event") {
    handleEvent(envelope.payload);
  } else if (envelope.type === "extension_ui_request") {
    handleExtensionUIRequest(envelope.payload as RpcExtensionUIRequest);
  }
}

function handleResponse(payload: RpcResponse) {
  if (payload.id) {
    const pending = pendingRequests.get(payload.id);
    if (pending) {
      clearTimeout(pending.timer);
      pendingRequests.delete(payload.id);
      pending.resolve(payload);
    }
  }

  if (payload.success) {
    switch (payload.command) {
      case "get_messages": {
        const data = payload.data as
          | (RpcTranscriptPage & { direction: "latest" | "older" })
          | undefined;
        if (data) {
          applyTranscriptPage(
            data,
            data.direction === "older" ? "prepend" : "replace",
          );
        }
        break;
      }
      case "get_state": {
        const data = payload.data as RpcSessionState | undefined;
        if (data) {
          const prevSp = getDisplayedSessionPath();
          const prevWp = getWorkspaceEntriesContextKey();
          _liveSessionPath = data.sessionFile ?? null;
          const isBrowsingDifferent = Boolean(
            _activeTreeSessionPath &&
            data.sessionFile &&
            _activeTreeSessionPath !== data.sessionFile,
          );
          _sessionState = isBrowsingDifferent
            ? {
                ...data,
                sessionId: _sessionState?.sessionId ?? data.sessionId,
                sessionName: _sessionState?.sessionName ?? data.sessionName,
                sessionFile: _activeTreeSessionPath ?? data.sessionFile,
                workspacePath:
                  _sessionState?.workspacePath ?? data.workspacePath,
              }
            : data;
          if (data.workspacePath) {
            ensureWorkspaceSummary(data.workspacePath);
          }
          updateCurrentModel(data.model);
          _currentThinkingLevel = normalizeThinkingLevel(data.thinkingLevel);
          setSessionRunning(data.sessionFile ?? null, data.isStreaming);
          _isStreaming = data.isStreaming;
          setCompactionState(data.isCompacting);
          if (!_activeTreeSessionPath && data.sessionFile) {
            _activeTreeSessionPath = data.sessionFile;
          }
          if (prevSp !== getDisplayedSessionPath()) resetGitRepoState();
          if (prevWp !== getWorkspaceEntriesContextKey())
            invalidateWorkspaceEntries();
        }
        break;
      }
      case "list_workspaces": {
        const data = payload.data as
          | { workspaces?: WorkspaceSummary[] }
          | undefined;
        const nextWorkspaces = Array.isArray(data?.workspaces)
          ? data.workspaces
              .map(workspace => normalizeWorkspaceSummary(workspace))
              .filter(
                (workspace): workspace is WorkspaceSummary =>
                  workspace !== null,
              )
          : [];
        for (const workspace of nextWorkspaces) {
          ensureWorkspaceSummary(
            workspace.path,
            workspace.name,
            workspace.updatedAt,
          );
        }
        _workspaces = [...nextWorkspaces].sort(compareWorkspaceSummaries);
        if (_sessionState?.workspacePath) {
          ensureWorkspaceSummary(_sessionState.workspacePath);
        }
        break;
      }
      case "list_sessions": {
        const data = payload.data as
          | {
              sessions?: SessionEntry[];
              workspacePath?: string;
              nextCursor?: string;
              merge?: "replace" | "append";
            }
          | undefined;
        if (Array.isArray(data?.sessions) && data.workspacePath) {
          ensureWorkspaceSummary(data.workspacePath);
          setWorkspaceSessions(
            data.workspacePath,
            data.sessions,
            data.merge ?? "replace",
          );
          syncRunningSessionsFromEntries(data.sessions);
          markWorkspaceSessionLoaded(data.workspacePath);
          setWorkspaceSessionLoading(data.workspacePath, false);
          _workspaceSessionCursors = {
            ..._workspaceSessionCursors,
            [data.workspacePath]: data.nextCursor ?? null,
          };
        }
        break;
      }
      case "switch_session": {
        applySessionSnapshotResponse(
          payload.data as Parameters<typeof applySessionSnapshotResponse>[0],
          { refreshState: true },
        );
        break;
      }
      case "list_tree_entries": {
        const data = payload.data as
          | { entries: TreeEntry[]; sessionPath?: string }
          | undefined;
        if (data)
          applyTreeEntriesUpdate(data.entries, data.sessionPath ?? null);
        break;
      }
      case "new_session": {
        const data = payload.data as
          | Parameters<typeof applySessionSnapshotResponse>[0]
          | undefined;
        if (!applySessionSnapshotResponse(data)) {
          replaceTranscript([], null);
          _transcriptHasOlder = false;
          _transcriptOldestCursor = null;
          _transcriptNewestCursor = null;
          _transcriptInitialLoading = false;
          _treeEntries = [];
          _sessionState = null;
          _isStreaming = false;
        }
        setCompactionState(false);
        const workspacePath =
          data?.workspacePath ?? _sessionState?.workspacePath;
        void refreshWorkspaces().catch(() => {});
        if (workspacePath) {
          void loadWorkspaceSessions({
            workspacePath,
            limit: 5,
            merge: "replace",
          }).catch(() => {});
        }
        break;
      }
      case "compact": {
        sendCommand({ type: "get_state" }).catch(() => {});
        break;
      }
      case "get_commands": {
        const data = payload.data as
          | { commands: RpcSlashCommand[] }
          | undefined;
        if (data) _commands = data.commands;
        break;
      }
      case "list_workspace_entries": {
        if (payload.id && workspaceEntriesRequestId !== payload.id) break;
        const data = payload.data as
          | { entries?: RpcWorkspaceEntry[] }
          | undefined;
        _workspaceEntries = Array.isArray(data?.entries) ? data.entries : [];
        _workspaceEntriesLoaded = true;
        workspaceEntriesLoadedContextKey =
          workspaceEntriesRequestContextKey ?? getWorkspaceEntriesContextKey();
        workspaceEntriesLoadedAt = Date.now();
        _workspaceEntriesLoading = false;
        break;
      }
      case "list_git_branches": {
        const state = normalizeGitRepoState(payload.data);
        _gitRepoState = state;
        if (!state)
          pushNotification("Failed to parse git branch data", "error");
        break;
      }
      case "switch_git_branch": {
        applyGitRepoMutation(normalizeGitRepoState(payload.data));
        break;
      }
      case "create_git_branch": {
        applyGitRepoMutation(normalizeGitRepoState(payload.data));
        break;
      }
      case "set_model": {
        updateCurrentModel(payload.data);
        updatePendingTranscriptConfigEvent({ model: _currentModel });
        break;
      }
      case "get_available_models": {
        const data = payload.data;
        if (data) updateAvailableModels(data.models);
        break;
      }
      case "select_tree_entry": {
        applySessionSnapshotResponse(
          payload.data as Parameters<typeof applySessionSnapshotResponse>[0],
          { refreshState: true },
        );
        break;
      }
      case "navigate_tree": {
        sendCommand({ type: "get_state" }).catch(() => {});
        sendCommand({
          type: "list_tree_entries",
          sessionPath: _activeTreeSessionPath ?? _sessionState?.sessionFile,
        }).catch(() => {});
        break;
      }
    }
  }
}

function handleEvent(payload: RpcBridgeEvent) {
  switch (payload.type) {
    case "transcript_snapshot": {
      const data = payload as RpcTranscriptSnapshotEvent;
      if (Array.isArray(data.messages)) applyTranscriptPage(data, "replace");
      break;
    }
    case "transcript_start": {
      const data = payload as RpcTranscriptStartEvent;
      if (data.message) applyTranscriptStart(data);
      if (Array.isArray(data.treeEntries)) {
        applyTreeEntriesUpdate(data.treeEntries, data.sessionPath ?? null);
      }
      break;
    }
    case "transcript_upsert": {
      const data = payload as RpcTranscriptUpsertEvent;
      if (data.message)
        upsertTranscriptMessage(data.message, data.sessionPath ?? null);
      if (Array.isArray(data.treeEntries)) {
        applyTreeEntriesUpdate(data.treeEntries, data.sessionPath ?? null);
      }
      break;
    }
    case "transcript_delta": {
      const data = payload as RpcTranscriptDeltaEvent;
      applyTranscriptDelta(data);
      break;
    }
    case "session_stats": {
      const data = payload as RpcSessionStatsEvent;
      if (
        !_activeTreeSessionPath ||
        !data.sessionPath ||
        _activeTreeSessionPath === data.sessionPath
      ) {
        const stats = normalizeSessionStats(data.stats);
        if (stats) _sessionStats = stats;
      }
      break;
    }
    case "queue_update": {
      const data = payload as RpcQueueUpdateEvent;
      const steering = Array.isArray(data.steering)
        ? data.steering
            .map(m => normalizeQueuedMessage(m))
            .filter((m): m is RpcQueuedMessage => m !== null)
        : [];
      const followUp = Array.isArray(data.followUp)
        ? data.followUp
            .map(m => normalizeQueuedMessage(m))
            .filter((m): m is RpcQueuedMessage => m !== null)
        : [];
      applyQueuedMessages(steering, followUp, {
        sessionPath: data.sessionPath ?? null,
      });
      break;
    }
    case "agent_start": {
      const data = payload as RpcAgentStartEvent;
      const sp = data.sessionPath ?? _liveSessionPath ?? null;
      setSessionRunning(sp, true);
      if (!sp || sp === getDisplayedSessionPath()) _isStreaming = true;
      break;
    }
    case "agent_end": {
      const data = payload as RpcAgentEndEvent;
      const sp = data.sessionPath ?? _liveSessionPath ?? null;
      setSessionRunning(sp, false);
      if (!sp || sp === getDisplayedSessionPath()) {
        _isStreaming = false;
        sendCommand({ type: "get_state" }).catch(() => {});
      }
      break;
    }
    case "model_select": {
      const model = normalizeRpcModel(payload.model ?? payload);
      if (model) {
        _currentModel = model;
        _availableModels = upsertModel(_availableModels, model);
      }
      break;
    }
    case "compaction_start": {
      setCompactionState(true);
      break;
    }
    case "compaction_end": {
      setCompactionState(false);
      if (
        payload.reason !== "manual" &&
        !payload.aborted &&
        typeof payload.errorMessage === "string" &&
        payload.errorMessage.trim()
      ) {
        appendCompactErrorMessage(payload.errorMessage);
      }
      break;
    }
  }
}

function handleExtensionUIRequest(payload: RpcExtensionUIRequest) {
  switch (payload.method) {
    case "select":
    case "confirm":
    case "input":
    case "editor":
      _pendingExtensionRequest = payload;
      break;
    case "notify":
      _notifications = [
        ..._notifications,
        {
          message: payload.message,
          notifyType: payload.notifyType,
          id: payload.id,
        },
      ];
      break;
    case "setTitle":
      document.title = payload.title;
      break;
    case "set_editor_text":
      _prefillText = payload.text;
      break;
    case "setStatus":
      _statusEntries = {
        ..._statusEntries,
        [payload.statusKey]: payload.statusText ?? "",
      };
      break;
    case "setWidget":
      if (payload.widgetLines) {
        _widgetEntries = {
          ..._widgetEntries,
          [payload.widgetKey]: {
            lines: payload.widgetLines,
            placement: payload.widgetPlacement,
          },
        };
      } else {
        const { [payload.widgetKey]: _, ...rest } = _widgetEntries;
        _widgetEntries = rest;
      }
      break;
  }
}

// ---------------------------------------------------------------------------
// Connect / disconnect
// ---------------------------------------------------------------------------

async function fetchInitialState() {
  _transcriptInitialLoading = true;
  const routeSessionPath = readSessionRoutePath();

  try {
    const bootstrap = [
      sendCommand({
        type: "list_workspaces",
      }),
      sendCommand({ type: "get_available_models" }),
      sendCommand({ type: "get_commands" }),
    ];

    if (routeSessionPath) {
      const resp = await sendCommand({
        type: "switch_session",
        sessionPath: routeSessionPath,
      });
      await Promise.all(bootstrap);
      if (!resp.success) {
        pushNotification(
          summarizeErrorMessage(
            resp.error ?? "Failed to restore session from URL",
            "Failed to restore session from URL",
          ),
          "error",
        );
        writeSessionRoutePath(null, "replace");
        await restoreLiveSessionState();
      }
      return;
    }

    await Promise.all([restoreLiveSessionState(), ...bootstrap]);
  } catch {
    _transcriptInitialLoading = false;
  }
}

function connect() {
  if (disposed) return;

  _connectionError = "";
  _connectionStatus = "connecting";
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${location.host}/ws`;
  ws = new WebSocket(wsUrl);

  ws.addEventListener("open", () => {
    _connectionStatus = "connected";
    _connectionError = "";
    _lastDisconnectReason = "";
    resetReconnectDelay();
    fetchInitialState();
  });

  ws.addEventListener("close", (event?: CloseEvent) => {
    _connectionStatus = "disconnected";
    _remoteCompactionActive = false;
    _reconnectCount++;
    _runningSessionPaths = [];
    _workspaceSessionLoading = {};
    resetGitRepoState();
    _workspaceSessions = Object.fromEntries(
      Object.entries(_workspaceSessions).map(([workspacePath, entries]) => [
        workspacePath,
        entries.map(entry => ({ ...entry, isRunning: false })),
      ]),
    );
    _lastDisconnectReason = event?.reason
      ? `Connection lost: ${event.reason}`
      : "Connection lost";
    _pendingExtensionRequest = null;
    _notifications = [];
    for (const [id, pending] of pendingRequests) {
      clearTimeout(pending.timer);
      pending.reject(new Error("WebSocket closed"));
      pendingRequests.delete(id);
    }
    scheduleReconnect();
  });

  ws.addEventListener("error", () => {
    _connectionStatus = "disconnected";
    scheduleReconnect();
  });

  ws.addEventListener("message", handleServerMessage);
}

function disconnect() {
  disposed = true;
  stopSessionRouteSync();
  clearDisplayTranscriptDeltas();
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  if (ws) {
    ws.close();
    ws = null;
  }
}

// ---------------------------------------------------------------------------
// Auto-connect
// ---------------------------------------------------------------------------

if (!ws && !disposed) {
  startSessionRouteSync();
  connect();
} else if (!disposed) {
  startSessionRouteSync();
}

export function initBridge() {
  return {
    get connectionStatus() {
      return connectionStatus;
    },
    get transcript() {
      return transcript;
    },
    get transcriptDeltas() {
      return transcriptDeltas;
    },
    get transcriptStreams() {
      return transcriptStreams;
    },
    get transcriptHasOlder() {
      return transcriptHasOlder;
    },
    get transcriptInitialLoading() {
      return transcriptInitialLoading;
    },
    get transcriptPageLoading() {
      return transcriptPageLoading;
    },
    get pendingTranscriptConfigEvent() {
      return visiblePendingTranscriptConfigEvent;
    },
    get sessionState() {
      return sessionState;
    },
    get workspaces() {
      return workspaces;
    },
    get workspaceSessions() {
      return workspaceSessions;
    },
    get workspaceSessionLoaded() {
      return workspaceSessionLoaded;
    },
    get workspaceSessionLoading() {
      return workspaceSessionLoading;
    },
    get sessions() {
      return sessions;
    },
    get treeEntries() {
      return treeEntries;
    },
    get activeTreeSessionPath() {
      return activeTreeSessionPath;
    },
    get liveSessionPath() {
      return liveSessionPath;
    },
    get runningSessionPaths() {
      return runningSessionPaths;
    },
    get workspaceSessionCursors() {
      return workspaceSessionCursors;
    },
    get commands() {
      return commands;
    },
    get workspaceEntries() {
      return workspaceEntries;
    },
    get workspaceEntriesLoading() {
      return workspaceEntriesLoading;
    },
    get availableModels() {
      return availableModels;
    },
    get currentModel() {
      return currentModel;
    },
    get currentThinkingLevel() {
      return currentThinkingLevel;
    },
    get isStreaming() {
      return isStreaming;
    },
    get isCompacting() {
      return isCompacting;
    },
    get sessionStats() {
      return sessionStats;
    },
    get gitRepoState() {
      return gitRepoState;
    },
    get gitRepoLoading() {
      return gitRepoLoading;
    },
    get gitBranchSwitching() {
      return gitBranchSwitching;
    },
    get pendingMessageCount() {
      return pendingMessageCount;
    },
    get queuedUserMessages() {
      return queuedUserMessages;
    },
    get isReconnecting() {
      return isReconnecting;
    },
    get reconnectCount() {
      return reconnectCount;
    },
    get lastDisconnectReason() {
      return lastDisconnectReason;
    },
    get connectionError() {
      return connectionError;
    },
    get pendingExtensionRequest() {
      return pendingExtensionRequest;
    },
    get notifications() {
      return notifications;
    },
    get statusEntries() {
      return statusEntries;
    },
    get widgetEntries() {
      return widgetEntries;
    },
    get prefillText() {
      return prefillText;
    },
    get activeSessionPath() {
      return activeSessionPath;
    },
    get hasSessionOutline() {
      return hasSessionOutline;
    },
    sendCommand,
    sendPrompt,
    loadOlderTranscriptPage,
    fetchWorkspaceEntries,
    readWorkspaceFile,
    loadWorkspaceSessions,
    refreshWorkspaces,
    loadGitRepoState,
    switchGitBranch,
    createGitBranch,
    switchSession,
    newSession,
    registerWorkspace,
    abortGeneration,
    compactSession,
    setThinkingLevel,
    setAutoCompactionEnabled,
    deleteSession,
    cancelQueuedMessage,
    editQueuedMessage,
    respondToUIRequest,
    dismissNotification,
    disconnect,
  };
}
