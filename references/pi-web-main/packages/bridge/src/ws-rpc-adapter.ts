import { spawnSync } from "node:child_process";
import * as crypto from "node:crypto";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import {
  SessionManager,
  type AgentEndEvent as PiAgentEndEvent,
  type AgentSession,
  type AgentSessionEvent,
  type ExtensionUIContext,
  type SessionEntry,
} from "@earendil-works/pi-coding-agent";
import type { WebSocket } from "ws";
import type { BridgeEventBus } from "./bridge-event-bus.js";
import type {
  BridgeSessionActions,
  BridgeSessionEvents,
  BridgeSessionState,
} from "./live-session.js";
import { DetachedSessionRegistry } from "./session-registry.js";
import type {
  BridgeConfig,
  BridgeEvent,
  ClientMessage,
  RpcAgentEndEvent,
  RpcAgentMessage,
  RpcAgentStartEvent,
  RpcBridgeEvent,
  RpcCommand,
  RpcCompactionEndEvent,
  RpcCompactionStartEvent,
  RpcExtensionUIRequest,
  RpcExtensionUIResponse,
  RpcGitBranch,
  RpcGitRepoState,
  RpcImageContent,
  RpcModel,
  RpcModelSelectEvent,
  RpcQueuedMessage,
  RpcQueueUpdateEvent,
  RpcResponse,
  RpcSessionState,
  RpcSessionStats,
  RpcSessionStatsEvent,
  RpcSlashCommand,
  RpcThinkingLevel,
  RpcTranscriptContentBlock,
  RpcTranscriptMessage,
  RpcTranscriptDeltaEvent,
  RpcTranscriptPage,
  RpcTranscriptSnapshotEvent,
  RpcTranscriptStartEvent,
  RpcTranscriptUpsertEvent,
  RpcTreeEntry,
  RpcWorkspaceEntry,
  RpcWorkspaceFile,
  RpcWorkspaceSummary,
  RpcTreeTrackColumn,
  ServerMessage,
  WsClient,
} from "./types.js";
import { detectWorkspaceEnvironments } from "./workspace-environment.js";

/** Model shape mirrored from Pi — used in shaping helpers below. */
type PiModel = {
  id: string;
  provider: string;
  name?: string;
  api?: string;
  reasoning?: boolean;
  contextWindow?: number;
  maxTokens?: number;
};
type UserMessageContent =
  | string
  | Array<
      | { type: "text"; text: string }
      | { type: "image"; data: string; mimeType: string }
    >;
type UserMessageBlock = Exclude<UserMessageContent, string>[number];
type PiTextContent = Extract<UserMessageBlock, { type: "text" }>;
type PiAgentMessage = NonNullable<PiAgentEndEvent["messages"]>[number];
type PiAgentUserMessage = Extract<PiAgentMessage, { role: "user" }>;
type PiAgentAssistantMessage = Extract<PiAgentMessage, { role: "assistant" }>;
type PiAgentToolResultMessage = Extract<PiAgentMessage, { role: "toolResult" }>;
type PiAgentUserContentBlock = Exclude<
  PiAgentUserMessage["content"],
  string
>[number];
type PiAgentAssistantContentBlock = PiAgentAssistantMessage["content"][number];
type PiAgentToolResultContentBlock =
  PiAgentToolResultMessage["content"][number];
type PiAgentTextOrImageContentBlock =
  | PiAgentUserContentBlock
  | PiAgentToolResultContentBlock;
type RpcAgentUserContentBlock = Exclude<
  Extract<RpcAgentMessage, { role: "user" }>["content"],
  string
>[number];
type RpcAgentAssistantContentBlock = Extract<
  RpcAgentMessage,
  { role: "assistant" }
>["content"][number];
type RpcAgentToolResultContentBlock = Extract<
  RpcAgentMessage,
  { role: "toolResult" }
>["content"][number];
type RpcAgentTextOrImageContentBlock =
  | RpcAgentUserContentBlock
  | RpcAgentToolResultContentBlock;
type PiModelSelectEventLike = {
  model: PiModel;
  previousModel?: PiModel;
  source: RpcModelSelectEvent["source"];
};

/**
 * Context passed to the adapter — three focused interfaces
 * abstracting over the live Pi session.
 */
export interface WsRpcAdapterContext {
  events: BridgeSessionEvents;
  state: BridgeSessionState;
  actions: BridgeSessionActions;
}

/**
 * Pending extension UI request
 */
interface PendingUIRequest {
  resolve: (value: RpcExtensionUIResponse) => void;
  reject: (error: Error) => void;
  timeoutId?: ReturnType<typeof setTimeout>;
  method: string;
}

interface PendingTranscriptDeltaBatch {
  payload: RpcTranscriptDeltaEvent;
  timeoutId: ReturnType<typeof setTimeout>;
}

interface TranscriptSyncState {
  sessionPath: string | null;
  nextEphemeralId: number;
  messageIdToKey: Map<string, string>;
  openKeysByRole: Map<string, string[]>;
  lastMessagesByKey: Map<string, RpcTranscriptMessage>;
  closedKeys: Set<string>;
}

interface SessionSummary {
  sessionManager: SessionManager;
  sessionPath: string;
  transcript: RpcTranscriptPage;
  treeEntries: RpcTreeEntry[];
  sessionId: string;
  sessionName: string;
  workspacePath?: string;
}

interface WorkspaceSessionEntry {
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

interface WorkspaceMetadata {
  workspaceId: string;
  workspaceName: string;
  workspacePath: string;
}

interface RegisteredWorkspace {
  workspacePath: string;
  sessionDir: string;
}

interface SessionListCursor {
  updatedAt?: string;
  path: string;
}

interface SessionListManager {
  getHeader: () => { id: string; timestamp: string; cwd?: string } | null;
  getCwd: () => string | undefined;
  getSessionFile: () => string | undefined;
  getSessionName: () => string | undefined;
  getEntries: () => unknown[];
  getSessionId: () => string;
}

/* ============================================================================
 * Event and payload shaping
 * ========================================================================== */

function toRpcAgentStartEvent(sessionPath?: string | null): RpcAgentStartEvent {
  return {
    type: "agent_start",
    sessionPath: sessionPath ?? undefined,
  };
}

function toRpcAgentEndEvent(
  event: { messages?: unknown[] },
  sessionPath?: string | null,
): RpcAgentEndEvent {
  if (!Array.isArray(event.messages)) {
    return {
      type: "agent_end",
      sessionPath: sessionPath ?? undefined,
    };
  }

  return {
    type: "agent_end",
    sessionPath: sessionPath ?? undefined,
    messages: event.messages.flatMap(message => {
      const shaped = toRpcAgentMessage(message as PiAgentMessage);
      return shaped ? [shaped] : [];
    }),
  };
}

function toRpcAgentMessage(message: PiAgentMessage): RpcAgentMessage | null {
  switch (message.role) {
    case "user":
      return {
        role: "user",
        content:
          typeof message.content === "string"
            ? message.content
            : message.content.map(toRpcAgentTextOrImageContentBlock),
        timestamp: message.timestamp,
      };
    case "assistant":
      return {
        role: "assistant",
        content: message.content.map(toRpcAgentAssistantContentBlock),
        api: message.api,
        provider: message.provider,
        model: message.model,
        responseId: message.responseId,
        usage: {
          input: message.usage.input,
          output: message.usage.output,
          cacheRead: message.usage.cacheRead,
          cacheWrite: message.usage.cacheWrite,
          totalTokens: message.usage.totalTokens,
          cost: {
            input: message.usage.cost.input,
            output: message.usage.cost.output,
            cacheRead: message.usage.cost.cacheRead,
            cacheWrite: message.usage.cost.cacheWrite,
            total: message.usage.cost.total,
          },
        },
        stopReason: message.stopReason,
        errorMessage: message.errorMessage,
        timestamp: message.timestamp,
      };
    case "toolResult":
      return {
        role: "toolResult",
        toolCallId: message.toolCallId,
        toolName: message.toolName,
        content: message.content.map(toRpcAgentTextOrImageContentBlock),
        details: message.details,
        isError: message.isError,
        timestamp: message.timestamp,
      };
    default:
      return null;
  }
}

function toRpcAgentTextOrImageContentBlock(
  block: PiAgentTextOrImageContentBlock,
): RpcAgentTextOrImageContentBlock {
  switch (block.type) {
    case "text":
      return {
        type: "text",
        text: block.text,
        textSignature: block.textSignature,
      };
    case "image":
      return {
        type: "image",
        data: block.data,
        mimeType: block.mimeType,
      };
  }
}

function toRpcAgentAssistantContentBlock(
  block: PiAgentAssistantContentBlock,
): RpcAgentAssistantContentBlock {
  switch (block.type) {
    case "text":
      return {
        type: "text",
        text: block.text,
        textSignature: block.textSignature,
      };
    case "thinking":
      return {
        type: "thinking",
        thinking: block.thinking,
        thinkingSignature: block.thinkingSignature,
        redacted: block.redacted,
      };
    case "toolCall":
      return {
        type: "toolCall",
        id: block.id,
        name: block.name,
        arguments: block.arguments,
        thoughtSignature: block.thoughtSignature,
      };
  }
}

function toRpcModel(model: PiModel): RpcModel {
  return {
    id: model.id,
    provider: model.provider,
    name: model.name,
    api: model.api,
    reasoning: model.reasoning,
    contextWindow: model.contextWindow,
    maxTokens: model.maxTokens,
  };
}

function isPiModel(value: unknown): value is PiModel {
  if (!value || typeof value !== "object") return false;
  const typedValue = value as { id?: unknown; provider?: unknown };
  return (
    typeof typedValue.id === "string" && typeof typedValue.provider === "string"
  );
}

function isModelSelectSource(
  value: unknown,
): value is RpcModelSelectEvent["source"] {
  return value === "set" || value === "cycle" || value === "restore";
}

function isPiModelSelectEventLike(
  value: unknown,
): value is PiModelSelectEventLike {
  if (!value || typeof value !== "object") return false;
  const typedValue = value as {
    model?: unknown;
    previousModel?: unknown;
    source?: unknown;
  };
  return (
    isPiModel(typedValue.model) &&
    (typedValue.previousModel === undefined ||
      isPiModel(typedValue.previousModel)) &&
    isModelSelectSource(typedValue.source)
  );
}

function toRpcModelSelectEvent(event: {
  model: PiModel;
  previousModel?: PiModel;
  source: string;
}): RpcModelSelectEvent {
  return {
    type: "model_select",
    model: toRpcModel(event.model),
    previousModel: event.previousModel
      ? toRpcModel(event.previousModel)
      : undefined,
    source: event.source as RpcModelSelectEvent["source"],
  };
}

function toRpcCompactionStartEvent(
  event: Extract<AgentSessionEvent, { type: "compaction_start" }>,
): RpcCompactionStartEvent {
  return {
    type: "compaction_start",
    reason: event.reason,
  };
}

function toRpcCompactionEndEvent(
  event: Extract<AgentSessionEvent, { type: "compaction_end" }>,
): RpcCompactionEndEvent {
  return {
    type: "compaction_end",
    reason: event.reason,
    result: event.result
      ? {
          summary: event.result.summary,
          firstKeptEntryId: event.result.firstKeptEntryId,
          tokensBefore: event.result.tokensBefore,
          details: event.result.details,
        }
      : null,
    aborted: event.aborted,
    willRetry: event.willRetry,
    errorMessage: event.errorMessage,
  };
}

interface SessionTreeNodeLike {
  entry: SessionEntry;
  children: SessionTreeNodeLike[];
  label?: string;
}

interface VisibleTreeNodeLike {
  entry: SessionEntry;
  children: VisibleTreeNodeLike[];
  label?: string;
  containsActiveLeaf: boolean;
}

interface TreeRowGutter {
  position: number;
  show: boolean;
}

interface TreeEntryPresentation {
  role: Exclude<RpcTreeEntry["role"], undefined>;
  labelTag?: string;
  previewText: string;
  searchText: string;
  isSettingsEntry: boolean;
  isLabeled: boolean;
  isToolOnlyAssistant: boolean;
}

const TREE_HARD_HIDDEN_ENTRY_TYPES = new Set(["label"]);
const TREE_SETTINGS_ENTRY_TYPES = new Set([
  "custom",
  "model_change",
  "thinking_level_change",
  "session_info",
]);

function openSessionManager(sessionPath: string): SessionManager {
  return SessionManager.open(sessionPath, path.dirname(sessionPath));
}

function sessionTimestampSortValue(timestamp?: string): number {
  const parsed =
    typeof timestamp === "string" && timestamp.trim().length > 0
      ? Date.parse(timestamp)
      : Number.NaN;
  return Number.isFinite(parsed) ? parsed : Number.NEGATIVE_INFINITY;
}

function compareSessionsByRecency(
  left: { timestamp?: string; updatedAt?: string; path: string },
  right: { timestamp?: string; updatedAt?: string; path: string },
): number {
  const timestampDelta =
    sessionTimestampSortValue(right.updatedAt ?? right.timestamp) -
    sessionTimestampSortValue(left.updatedAt ?? left.timestamp);
  if (timestampDelta !== 0) return timestampDelta;

  return right.path.localeCompare(left.path);
}

function normalizeOptionalWorkspaceRoot(
  workspacePath?: string | null,
): string | undefined {
  const trimmed = workspacePath?.trim();
  if (!trimmed) return undefined;

  const normalized = path.normalize(trimmed);
  const root = path.parse(normalized).root;
  if (normalized === root) {
    return normalized;
  }

  return normalized.replace(/[\\/]+$/, "");
}

function workspaceDisplayName(workspacePath: string): string {
  const baseName = path.basename(workspacePath);
  return baseName || workspacePath || "Unknown workspace";
}

function workspaceMetadata(
  workspacePath: string | undefined,
  sessionPath: string,
): WorkspaceMetadata {
  const fallbackPath = path.dirname(sessionPath);
  const normalizedWorkspacePath =
    normalizeOptionalWorkspaceRoot(workspacePath) ??
    normalizeOptionalWorkspaceRoot(fallbackPath) ??
    fallbackPath;

  return {
    workspaceId: normalizedWorkspacePath,
    workspaceName: workspaceDisplayName(normalizedWorkspacePath),
    workspacePath: normalizedWorkspacePath,
  };
}

function normalizeSessionTimestamp(timestamp?: string): string | undefined {
  const value = sessionTimestampSortValue(timestamp);
  return Number.isFinite(value) ? new Date(value).toISOString() : timestamp;
}

function listSessionFilesInDir(sessionDir: string): string[] {
  try {
    return fs
      .readdirSync(sessionDir)
      .filter(file => file.endsWith(".jsonl"))
      .map(file => path.join(sessionDir, file));
  } catch {
    return [];
  }
}

function getSessionsRoot(): string {
  return (
    process.env.PI_WEB_SESSIONS_ROOT ??
    path.join(os.homedir(), ".pi", "agent", "sessions")
  );
}

function workspaceSessionDirName(workspacePath: string): string {
  return `--${workspacePath.replace(/^[/\\]/, "").replace(/[/\\:]/g, "-")}--`;
}

function workspaceSessionDirPath(workspacePath: string): string {
  return path.join(getSessionsRoot(), workspaceSessionDirName(workspacePath));
}

function resolveWorkspaceSessionDirPath(workspacePath: string): string {
  const normalizedWorkspacePath = normalizeOptionalWorkspaceRoot(workspacePath);
  const preferredPath = workspaceSessionDirPath(
    normalizedWorkspacePath ?? workspacePath,
  );
  if (fs.existsSync(preferredPath)) {
    return preferredPath;
  }

  if (!normalizedWorkspacePath) {
    return preferredPath;
  }

  for (const workspace of listRegisteredWorkspaces()) {
    if (
      normalizeOptionalWorkspaceRoot(workspace.workspacePath) ===
      normalizedWorkspacePath
    ) {
      return workspace.sessionDir;
    }
  }

  return preferredPath;
}

function isExistingDirectory(directoryPath: string): boolean {
  try {
    return fs.statSync(directoryPath).isDirectory();
  } catch {
    return false;
  }
}

function resolveExistingWorkspacePathFromEncoded(
  encoded: string,
): string | null {
  const tokens = encoded.split("-").filter(Boolean);
  if (tokens.length === 0) return null;

  const cache = new Map<string, string | null>();

  const search = (basePath: string, tokenIndex: number): string | null => {
    const cacheKey = `${basePath}\0${tokenIndex}`;
    const cached = cache.get(cacheKey);
    if (cached !== undefined) return cached;

    if (tokenIndex >= tokens.length) {
      const resolved = isExistingDirectory(basePath) ? basePath : null;
      cache.set(cacheKey, resolved);
      return resolved;
    }

    for (let length = 1; tokenIndex + length <= tokens.length; length += 1) {
      const segment = tokens.slice(tokenIndex, tokenIndex + length).join("-");
      const candidate = path.join(basePath, segment);
      if (!isExistingDirectory(candidate)) continue;

      const resolved = search(candidate, tokenIndex + length);
      if (resolved) {
        cache.set(cacheKey, resolved);
        return resolved;
      }
    }

    cache.set(cacheKey, null);
    return null;
  };

  return search(path.sep, 0);
}

function decodeWorkspaceSessionDirName(sessionDirName: string): string | null {
  if (!sessionDirName.startsWith("--") || !sessionDirName.endsWith("--")) {
    return null;
  }

  const encoded = sessionDirName.slice(2, -2);
  if (!encoded) return null;

  const directPath = path.sep + encoded.replace(/-/g, path.sep);
  if (isExistingDirectory(directPath)) {
    return directPath;
  }

  return resolveExistingWorkspacePathFromEncoded(encoded) ?? directPath;
}

function ensureRegisteredWorkspace(workspacePath: string): {
  metadata: WorkspaceMetadata;
  created: boolean;
} {
  const normalizedWorkspacePath = normalizeOptionalWorkspaceRoot(workspacePath);
  if (!normalizedWorkspacePath) {
    throw new Error("Workspace path is required");
  }

  const sessionDir = resolveWorkspaceSessionDirPath(normalizedWorkspacePath);
  const created = !fs.existsSync(sessionDir);

  fs.mkdirSync(sessionDir, { recursive: true });
  fs.rmSync(path.join(sessionDir, ".pi-workspace.json"), { force: true });

  return {
    metadata: workspaceMetadata(normalizedWorkspacePath, sessionDir),
    created,
  };
}

function listRegisteredWorkspaces(): RegisteredWorkspace[] {
  const sessionsRoot = getSessionsRoot();
  if (!fs.existsSync(sessionsRoot)) return [];

  const workspaces: RegisteredWorkspace[] = [];
  for (const entry of fs.readdirSync(sessionsRoot, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const workspacePath = normalizeOptionalWorkspaceRoot(
      decodeWorkspaceSessionDirName(entry.name),
    );
    if (!workspacePath) continue;
    workspaces.push({
      workspacePath,
      sessionDir: path.join(sessionsRoot, entry.name),
    });
  }
  return workspaces;
}

function workspaceSummary(
  workspacePath: string,
  updatedAt?: string,
): RpcWorkspaceSummary {
  const workspace = workspaceMetadata(
    workspacePath,
    workspaceSessionDirPath(workspacePath),
  );
  return {
    id: workspace.workspaceId,
    name: workspace.workspaceName,
    path: workspace.workspacePath,
    updatedAt: normalizeSessionTimestamp(updatedAt),
  };
}

function compareWorkspaceSummaries(
  left: RpcWorkspaceSummary,
  right: RpcWorkspaceSummary,
): number {
  const updatedAtDelta =
    sessionTimestampSortValue(right.updatedAt) -
    sessionTimestampSortValue(left.updatedAt);
  if (updatedAtDelta !== 0) return updatedAtDelta;

  const nameDelta = left.name.localeCompare(right.name);
  if (nameDelta !== 0) return nameDelta;
  return left.path.localeCompare(right.path);
}

function readWorkspaceUpdatedAt(workspacePath: string): string | undefined {
  let latestHeaderTimestamp: string | undefined;
  let latestMtime = Number.NEGATIVE_INFINITY;

  for (const sessionPath of listWorkspaceSessionFiles(workspacePath)) {
    const header = readSessionFileHeader(sessionPath);
    if (header?.timestamp) {
      const normalizedTimestamp = normalizeSessionTimestamp(header.timestamp);
      if (
        sessionTimestampSortValue(normalizedTimestamp) >
        sessionTimestampSortValue(latestHeaderTimestamp)
      ) {
        latestHeaderTimestamp = normalizedTimestamp;
      }
    }

    try {
      latestMtime = Math.max(latestMtime, fs.statSync(sessionPath).mtimeMs);
    } catch {
      // Ignore vanished files while scanning.
    }
  }

  return (
    latestHeaderTimestamp ??
    (Number.isFinite(latestMtime)
      ? new Date(latestMtime).toISOString()
      : undefined)
  );
}

function appendWorkspaceSummary(
  workspaces: Map<string, RpcWorkspaceSummary>,
  workspacePath?: string,
  updatedAt?: string,
) {
  const normalizedWorkspacePath = normalizeOptionalWorkspaceRoot(workspacePath);
  if (!normalizedWorkspacePath) return;

  const existing = workspaces.get(normalizedWorkspacePath);
  const nextUpdatedAt =
    sessionTimestampSortValue(updatedAt) >=
    sessionTimestampSortValue(existing?.updatedAt)
      ? updatedAt
      : existing?.updatedAt;
  workspaces.set(
    normalizedWorkspacePath,
    workspaceSummary(normalizedWorkspacePath, nextUpdatedAt),
  );
}

function pickWorkspaceDirectoryFromNativeDialog(): string | null {
  if (process.platform === "darwin") {
    const result = spawnSync(
      "osascript",
      [
        "-e",
        'set selectedFolder to choose folder with prompt "Choose a workspace"',
        "-e",
        "POSIX path of selectedFolder",
      ],
      { encoding: "utf8" },
    );
    if (result.status === 0) {
      const selectedPath = result.stdout.trim();
      return selectedPath || null;
    }
    return null;
  }

  if (process.platform === "win32") {
    const result = spawnSync(
      "powershell.exe",
      [
        "-NoProfile",
        "-Command",
        [
          "Add-Type -AssemblyName System.Windows.Forms",
          "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog",
          '$dialog.Description = "Choose a workspace"',
          "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {",
          "  Write-Output $dialog.SelectedPath",
          "}",
        ].join("; "),
      ],
      { encoding: "utf8" },
    );
    if (result.status === 0) {
      const selectedPath = result.stdout.trim();
      return selectedPath || null;
    }
    return null;
  }

  const zenity = spawnSync(
    "zenity",
    ["--file-selection", "--directory", "--title=Choose a workspace"],
    { encoding: "utf8" },
  );
  if (zenity.status === 0) {
    const selectedPath = zenity.stdout.trim();
    return selectedPath || null;
  }

  const kdialog = spawnSync(
    "kdialog",
    ["--getexistingdirectory", os.homedir(), "--title", "Choose a workspace"],
    { encoding: "utf8" },
  );
  if (kdialog.status === 0) {
    const selectedPath = kdialog.stdout.trim();
    return selectedPath || null;
  }

  return null;
}

function listWorkspaceSessionFiles(workspacePath: string): string[] {
  return listSessionFilesInDir(resolveWorkspaceSessionDirPath(workspacePath));
}

function readFileChunk(
  filePath: string,
  start: number,
  length: number,
): string | null {
  let fd: number | null = null;
  try {
    fd = fs.openSync(filePath, "r");
    const stat = fs.fstatSync(fd);
    const safeStart = Math.max(0, Math.min(start, stat.size));
    const safeLength = Math.max(0, Math.min(length, stat.size - safeStart));
    const buffer = Buffer.alloc(safeLength);
    fs.readSync(fd, buffer, 0, safeLength, safeStart);
    return buffer.toString("utf8");
  } catch {
    return null;
  } finally {
    if (fd !== null) fs.closeSync(fd);
  }
}

function readSessionFilePrefix(filePath: string, maxBytes = 64 * 1024): string {
  return readFileChunk(filePath, 0, maxBytes) ?? "";
}

function parseJsonLine(line: string): Record<string, unknown> | null {
  try {
    const value = JSON.parse(line) as unknown;
    return value && typeof value === "object"
      ? (value as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

function parseJsonStringLiteral(value: string): string | undefined {
  try {
    const parsed = JSON.parse(value) as unknown;
    return typeof parsed === "string" ? parsed : undefined;
  } catch {
    return undefined;
  }
}

function extractUserMessageTextFromRawLine(line: string): string | undefined {
  if (
    !/"type"\s*:\s*"message"/.test(line) ||
    !/"role"\s*:\s*"user"/.test(line)
  ) {
    return undefined;
  }

  const contentArrayTextMatch = line.match(
    /"message"\s*:\s*\{[\s\S]*?"role"\s*:\s*"user"[\s\S]*?"content"\s*:\s*\[[\s\S]*?\{\s*"type"\s*:\s*"text"\s*,\s*"text"\s*:\s*("(?:\\.|[^"\\])*")/,
  );
  const contentStringMatch = line.match(
    /"message"\s*:\s*\{[\s\S]*?"role"\s*:\s*"user"[\s\S]*?"content"\s*:\s*("(?:\\.|[^"\\])*")/,
  );
  const textFieldMatch = line.match(
    /"message"\s*:\s*\{[\s\S]*?"role"\s*:\s*"user"[\s\S]*?"text"\s*:\s*("(?:\\.|[^"\\])*")/,
  );

  return [
    contentArrayTextMatch?.[1],
    contentStringMatch?.[1],
    textFieldMatch?.[1],
  ]
    .map(value => (value ? parseJsonStringLiteral(value) : undefined))
    .find((value): value is string => typeof value === "string");
}

function findFirstUserMessageText(chunk: string): string | undefined {
  for (const line of chunk.split("\n")) {
    const entry = parseJsonLine(line);
    if (entry?.type === "message") {
      const message = entry.message as { role?: unknown; content?: unknown };
      if (message?.role !== "user") continue;
      const text = collapseWhitespace(extractMessageText(message));
      if (text) return text;
      continue;
    }

    const fallbackText = extractUserMessageTextFromRawLine(line);
    if (fallbackText) return collapseWhitespace(fallbackText);
  }
  return undefined;
}

function encodeSessionCursor(session: WorkspaceSessionEntry): string {
  const cursor: SessionListCursor = {
    updatedAt: session.updatedAt ?? session.timestamp,
    path: session.path,
  };
  return Buffer.from(JSON.stringify(cursor), "utf8").toString("base64url");
}

function decodeSessionCursor(value?: string): SessionListCursor | null {
  if (!value) return null;
  try {
    const parsed = JSON.parse(
      Buffer.from(value, "base64url").toString("utf8"),
    ) as Partial<SessionListCursor>;
    return typeof parsed.path === "string"
      ? { updatedAt: parsed.updatedAt, path: parsed.path }
      : null;
  } catch {
    return null;
  }
}

function isAfterSessionCursor(
  session: WorkspaceSessionEntry,
  cursor: SessionListCursor | null,
): boolean {
  if (!cursor) return true;
  return (
    compareSessionsByRecency(session, {
      path: cursor.path,
      updatedAt: cursor.updatedAt,
      timestamp: cursor.updatedAt,
    }) > 0
  );
}

function sessionMatchesListQuery(
  session: WorkspaceSessionEntry,
  query?: string,
): boolean {
  const q = query?.trim().toLowerCase();
  if (!q) return true;
  return [
    session.name,
    session.path,
    session.workspaceName,
    session.workspacePath,
  ]
    .filter(Boolean)
    .some(value => value!.toLowerCase().includes(q));
}

const DEFAULT_PENDING_SESSION_NAME = "New session";

function readSessionFileHeader(sessionPath: string): {
  id: string;
  timestamp?: string;
  cwd?: string;
} | null {
  const prefix = readSessionFilePrefix(sessionPath);
  const firstLine = prefix.split("\n", 1)[0];
  const header = parseJsonLine(firstLine) as {
    id?: unknown;
    timestamp?: unknown;
    cwd?: unknown;
    type?: unknown;
  } | null;
  if (header?.type !== "session" || typeof header.id !== "string") {
    return null;
  }

  return {
    id: header.id,
    timestamp:
      typeof header.timestamp === "string" ? header.timestamp : undefined,
    cwd: typeof header.cwd === "string" ? header.cwd : undefined,
  };
}

function readWorkspaceSessionSummary(
  sessionPath: string,
  running: boolean,
): WorkspaceSessionEntry | null {
  const prefix = readSessionFilePrefix(sessionPath);
  const header = readSessionFileHeader(sessionPath);
  if (!header) {
    return null;
  }

  const timestamp = normalizeSessionTimestamp(header.timestamp);
  const workspace = workspaceMetadata(header.cwd, sessionPath);
  const firstUserMessage = findFirstUserMessageText(prefix);

  return {
    id: header.id,
    name: firstUserMessage ?? DEFAULT_PENDING_SESSION_NAME,
    path: sessionPath,
    isRunning: running,
    timestamp,
    updatedAt: timestamp,
    ...workspace,
  };
}

function normalizeWorkspacePath(filePath: string): string {
  return filePath.split(path.sep).join("/");
}

function collectWorkspaceEntries(
  filePaths: readonly string[],
): RpcWorkspaceEntry[] {
  const files = new Set<string>();
  const directories = new Set<string>();

  for (const rawFilePath of filePaths) {
    const filePath = normalizeWorkspacePath(rawFilePath.trim());
    if (!filePath) continue;

    files.add(filePath);

    let currentDir = path.posix.dirname(filePath);
    while (currentDir && currentDir !== ".") {
      if (directories.has(currentDir)) break;
      directories.add(currentDir);
      currentDir = path.posix.dirname(currentDir);
    }
  }

  return [
    ...Array.from(directories)
      .sort((a, b) => a.localeCompare(b))
      .map(entryPath => ({ path: entryPath, kind: "directory" as const })),
    ...Array.from(files)
      .sort((a, b) => a.localeCompare(b))
      .map(entryPath => ({ path: entryPath, kind: "file" as const })),
  ];
}

function listWorkspaceFilesWithRipgrep(cwd: string): string[] | null {
  const args = ["--files", "--hidden", "--follow", "-g", "!.git"];
  const rootIgnoreFile = path.join(cwd, ".gitignore");
  if (fs.existsSync(rootIgnoreFile)) {
    args.push("--ignore-file", rootIgnoreFile);
  }

  const result = spawnSync("rg", args, {
    cwd,
    encoding: "utf8",
    maxBuffer: 16 * 1024 * 1024,
    windowsHide: true,
  });

  if (result.error || result.status !== 0) {
    return null;
  }

  return result.stdout.split(/\r?\n/).filter(Boolean);
}

function listWorkspaceFilesFallback(cwd: string): string[] {
  const files: string[] = [];
  const stack = [""];
  const rootIgnoreFile = path.join(cwd, ".gitignore");
  const ignoredPatterns = fs.existsSync(rootIgnoreFile)
    ? new Set(
        fs
          .readFileSync(rootIgnoreFile, "utf8")
          .split(/\r?\n/)
          .map(line => line.trim())
          .filter(line => line.length > 0 && !line.startsWith("#")),
      )
    : new Set<string>();

  while (stack.length > 0) {
    const currentRelativeDir = stack.pop();
    if (currentRelativeDir === undefined) continue;

    const absoluteDir = currentRelativeDir
      ? path.join(cwd, currentRelativeDir)
      : cwd;

    let entries: fs.Dirent[] = [];
    try {
      entries = fs.readdirSync(absoluteDir, { withFileTypes: true });
    } catch {
      continue;
    }

    for (const entry of entries) {
      if (entry.name === ".git") {
        continue;
      }

      const relativePath = currentRelativeDir
        ? path.join(currentRelativeDir, entry.name)
        : entry.name;
      const absolutePath = path.join(absoluteDir, entry.name);
      const normalizedRelativePath = normalizeWorkspacePath(relativePath);
      if (
        ignoredPatterns.has(entry.name) ||
        ignoredPatterns.has(normalizedRelativePath)
      ) {
        continue;
      }

      if (entry.isDirectory()) {
        stack.push(relativePath);
        continue;
      }

      if (entry.isFile()) {
        files.push(relativePath);
        continue;
      }

      if (entry.isSymbolicLink()) {
        try {
          const stats = fs.statSync(absolutePath);
          if (stats.isDirectory()) {
            stack.push(relativePath);
          } else if (stats.isFile()) {
            files.push(relativePath);
          }
        } catch {
          // Ignore broken links while building the workspace index.
        }
      }
    }
  }

  return files;
}

function listWorkspaceEntries(cwd: string): RpcWorkspaceEntry[] {
  const filePaths =
    listWorkspaceFilesWithRipgrep(cwd) ?? listWorkspaceFilesFallback(cwd);
  return collectWorkspaceEntries(filePaths);
}

const MAX_WORKSPACE_FILE_BYTES = 256 * 1024;

function isPathInsideRoot(rootPath: string, candidatePath: string): boolean {
  if (candidatePath === rootPath) {
    return true;
  }
  const rootPrefix = rootPath.endsWith(path.sep)
    ? rootPath
    : `${rootPath}${path.sep}`;
  return candidatePath.startsWith(rootPrefix);
}

function resolveWorkspaceFile(
  cwd: string,
  requestedPath: string,
): { resolvedPath: string; displayPath: string } | { error: string } {
  const trimmedPath = requestedPath.trim();
  if (!trimmedPath) {
    return { error: "File path cannot be empty" };
  }

  const workspaceRoot = fs.realpathSync.native(cwd);
  const absolutePath = path.isAbsolute(trimmedPath)
    ? path.resolve(trimmedPath)
    : path.resolve(workspaceRoot, trimmedPath);
  if (!fs.existsSync(absolutePath)) {
    return { error: `File not found: ${trimmedPath}` };
  }

  const resolvedPath = fs.realpathSync.native(absolutePath);
  if (!isPathInsideRoot(workspaceRoot, resolvedPath)) {
    return { error: "File must be inside the current workspace" };
  }

  let stats: fs.Stats;
  try {
    stats = fs.statSync(resolvedPath);
  } catch {
    return { error: `Failed to stat file: ${trimmedPath}` };
  }

  if (!stats.isFile()) {
    return { error: `Not a file: ${trimmedPath}` };
  }

  return {
    resolvedPath,
    displayPath: normalizeWorkspacePath(
      path.relative(workspaceRoot, resolvedPath),
    ),
  };
}

function readWorkspaceFile(
  cwd: string,
  requestedPath: string,
): RpcWorkspaceFile | { error: string } {
  let resolved:
    | { resolvedPath: string; displayPath: string }
    | { error: string };
  try {
    resolved = resolveWorkspaceFile(cwd, requestedPath);
  } catch {
    return { error: "Failed to resolve workspace file" };
  }
  if ("error" in resolved) {
    return resolved;
  }

  let contentBuffer: Buffer;
  try {
    contentBuffer = fs.readFileSync(resolved.resolvedPath);
  } catch {
    return { error: `Failed to read file: ${requestedPath}` };
  }

  if (contentBuffer.includes(0)) {
    return { error: "Binary file preview is not supported" };
  }

  const truncated = contentBuffer.length > MAX_WORKSPACE_FILE_BYTES;
  const previewBuffer = truncated
    ? contentBuffer.subarray(0, MAX_WORKSPACE_FILE_BYTES)
    : contentBuffer;
  const content = previewBuffer.toString("utf8");

  return {
    path: resolved.displayPath,
    absolutePath: resolved.resolvedPath,
    content,
    truncated,
    totalBytes: contentBuffer.length,
    lineCount: content.split(/\r?\n/).length,
  };
}

function runGitCommand(
  cwd: string,
  args: string[],
  timeout = 2000,
): ReturnType<typeof spawnSync> {
  return spawnSync("git", args, {
    cwd,
    encoding: "utf8",
    timeout,
    windowsHide: true,
  });
}

function readSpawnText(value: string | Uint8Array | null | undefined): string {
  if (typeof value === "string") {
    return value;
  }
  if (!value) {
    return "";
  }
  return Buffer.from(value).toString("utf8");
}

function getCurrentGitBranch(
  cwd: string | null | undefined,
): string | undefined {
  return readGitRepoState(cwd)?.headLabel;
}

function readGitRepoState(
  cwd: string | null | undefined,
): RpcGitRepoState | null {
  if (!cwd) return null;

  const repoRootResult = runGitCommand(cwd, ["rev-parse", "--show-toplevel"]);
  if (repoRootResult.error || repoRootResult.status !== 0) {
    return null;
  }

  const repoRoot = readSpawnText(repoRootResult.stdout).trim();
  if (!repoRoot) {
    return null;
  }

  const currentBranchResult = runGitCommand(repoRoot, [
    "symbolic-ref",
    "--quiet",
    "--short",
    "HEAD",
  ]);
  const currentBranch =
    currentBranchResult.error || currentBranchResult.status !== 0
      ? undefined
      : readSpawnText(currentBranchResult.stdout).trim() || undefined;

  const headShaResult = runGitCommand(repoRoot, [
    "rev-parse",
    "--short",
    "HEAD",
  ]);
  const headSha =
    headShaResult.error || headShaResult.status !== 0
      ? undefined
      : readSpawnText(headShaResult.stdout).trim() || undefined;

  const branchesResult = runGitCommand(repoRoot, [
    "for-each-ref",
    "--format=%(refname)\t%(refname:short)\t%(HEAD)",
    "refs/heads",
    "refs/remotes",
  ]);
  if (branchesResult.error || branchesResult.status !== 0) {
    return null;
  }

  const branches: RpcGitBranch[] = readSpawnText(branchesResult.stdout)
    .split(/\r?\n/)
    .map((line: string) => line.trim())
    .filter(Boolean)
    .flatMap((line: string): RpcGitBranch[] => {
      const [refName = "", shortName = "", headMarker = ""] = line.split("\t");
      if (!refName || !shortName) return [];
      if (refName.startsWith("refs/remotes/") && shortName.endsWith("/HEAD")) {
        return [];
      }

      if (refName.startsWith("refs/heads/")) {
        return [
          {
            name: shortName,
            shortName,
            kind: "local",
            isCurrent: headMarker === "*",
          },
        ];
      }

      if (refName.startsWith("refs/remotes/")) {
        const [remoteName, ...rest] = shortName.split("/");
        const remoteShortName = rest.join("/");
        return [
          {
            name: shortName,
            shortName: remoteShortName || shortName,
            kind: "remote",
            remoteName,
            isCurrent: headMarker === "*",
          },
        ];
      }

      return [];
    })
    .sort((left: RpcGitBranch, right: RpcGitBranch) => {
      if (left.isCurrent !== right.isCurrent) {
        return left.isCurrent ? -1 : 1;
      }
      if (left.kind !== right.kind) {
        return left.kind === "local" ? -1 : 1;
      }
      return left.name.localeCompare(right.name);
    });

  const dirtyResult = runGitCommand(repoRoot, ["status", "--porcelain"]);
  const isDirty =
    !dirtyResult.error && dirtyResult.status === 0
      ? readSpawnText(dirtyResult.stdout).trim().length > 0
      : false;

  return {
    repoRoot,
    headLabel: currentBranch ?? (headSha ? `detached@${headSha}` : "detached"),
    currentBranch,
    detached: !currentBranch,
    isDirty,
    branches,
  };
}

function isTreeSettingsEntry(type: string): boolean {
  return TREE_SETTINGS_ENTRY_TYPES.has(type);
}

function getTreeEntryRole(
  entry:
    | SessionEntry
    | ({ role?: string; type?: string } & Record<string, unknown>),
): Exclude<RpcTreeEntry["role"], undefined> {
  const entryType = typeof entry.type === "string" ? entry.type : undefined;

  if (entryType === "message") {
    const messageRole =
      typeof (entry as { message?: { role?: string } }).message?.role ===
      "string"
        ? (entry as { message: { role: string } }).message.role
        : typeof (entry as { role?: string }).role === "string"
          ? (entry as { role: string }).role
          : undefined;

    if (messageRole === "user") return "user";
    if (messageRole === "assistant") return "assistant";
    if (messageRole === "toolResult" || messageRole === "bashExecution") {
      return "tool";
    }
    return "other";
  }

  if (
    entryType === "custom" ||
    entryType === "model_change" ||
    entryType === "thinking_level_change" ||
    entryType === "session_info" ||
    entryType === "compaction" ||
    entryType === "branch_summary"
  ) {
    return "meta";
  }

  return "other";
}

function isToolOnlyAssistantEntry(
  entry:
    | SessionEntry
    | ({ role?: string; content?: unknown; text?: string } & Record<
        string,
        unknown
      >),
): boolean {
  if (entry.type !== "message") return false;

  const message = (
    entry as SessionEntry & {
      message?: {
        role?: string;
        content?: unknown;
        text?: string;
        stopReason?: string;
        errorMessage?: string;
      };
    }
  ).message;

  if (!message || message.role !== "assistant") return false;
  if (collapseWhitespace(extractMessageText(message))) return false;
  if (message.stopReason === "aborted") return false;
  return !message.errorMessage;
}

function buildTreePreviewText(
  entry:
    | SessionEntry
    | ({ role?: string; content?: unknown; text?: string } & Record<
        string,
        unknown
      >),
): string {
  if ((entry as { type?: string }).type === "message" && "message" in entry) {
    const message = (
      entry as SessionEntry & {
        message: {
          role?: string;
          content?: unknown;
          text?: string;
          stopReason?: string;
          errorMessage?: string;
          toolName?: string;
          command?: string;
        };
      }
    ).message;
    const content = collapseWhitespace(extractMessageText(message));

    switch (message.role) {
      case "user":
        return content || "user";
      case "assistant":
        if (content) return content;
        if (message.stopReason === "aborted") return "(aborted)";
        if (message.errorMessage) {
          return collapseWhitespace(message.errorMessage);
        }
        return "(no content)";
      case "toolResult":
        return message.toolName
          ? `[tool: ${message.toolName}]`
          : "[tool result]";
      case "bashExecution":
        return message.command
          ? `[bash]: ${collapseWhitespace(message.command)}`
          : "[bash]";
      default:
        return describeMessage(message);
    }
  }

  if (typeof (entry as { role?: string }).role === "string") {
    const role = (entry as { role: string }).role;
    const content = collapseWhitespace(
      extractMessageText(entry as { content?: unknown; text?: string }),
    );
    if (role === "user") return content || "user";
    if (role === "assistant") return content || "assistant";
    return content ? `${role}: ${content}` : `[${role}]`;
  }

  return describeSessionEntry(entry as SessionEntry);
}

function buildTreeSearchText(
  entryLabel: string,
  previewText: string,
  entryType: string,
  role: Exclude<RpcTreeEntry["role"], undefined>,
  labelTag?: string,
): string {
  return [labelTag, previewText, entryLabel, entryType, role]
    .filter(
      (value): value is string =>
        typeof value === "string" && value.trim().length > 0,
    )
    .join(" ");
}

function buildTreeEntryPresentation(
  entry:
    | SessionEntry
    | ({ role?: string; content?: unknown; text?: string } & Record<
        string,
        unknown
      >),
  entryLabel: string,
  labelTag?: string,
): TreeEntryPresentation {
  const entryType =
    typeof (entry as { type?: string }).type === "string"
      ? (entry as { type: string }).type
      : typeof (entry as { role?: string }).role === "string"
        ? (entry as { role: string }).role
        : "unknown";
  const role = getTreeEntryRole(entry);
  const previewText = buildTreePreviewText(entry);

  return {
    role,
    labelTag,
    previewText,
    searchText: buildTreeSearchText(
      entryLabel,
      previewText,
      entryType,
      role,
      labelTag,
    ),
    isSettingsEntry: isTreeSettingsEntry(entryType),
    isLabeled: Boolean(labelTag),
    isToolOnlyAssistant: isToolOnlyAssistantEntry(entry),
  };
}

function buildVisibleTree(
  nodes: readonly SessionTreeNodeLike[],
  activeLeafId: string | null,
): VisibleTreeNodeLike[] {
  const visibleNodes: VisibleTreeNodeLike[] = [];

  for (const node of nodes) {
    const visibleChildren = buildVisibleTree(node.children, activeLeafId);
    const containsActiveLeaf =
      node.entry.id === activeLeafId ||
      visibleChildren.some(child => child.containsActiveLeaf);
    const hidden = TREE_HARD_HIDDEN_ENTRY_TYPES.has(node.entry.type);

    if (hidden) {
      visibleNodes.push(...visibleChildren);
      continue;
    }

    visibleNodes.push({
      entry: node.entry,
      children: visibleChildren,
      label: node.label,
      containsActiveLeaf,
    });
  }

  return visibleNodes;
}

function flattenVisibleTree(
  nodes: readonly VisibleTreeNodeLike[],
): RpcTreeEntry[] {
  const entries: RpcTreeEntry[] = [];
  const multipleRoots = nodes.length > 1;
  const orderedRoots = orderTreeChildren(nodes);
  const stack: Array<{
    node: VisibleTreeNodeLike;
    indent: number;
    justBranched: boolean;
    showConnector: boolean;
    isLast: boolean;
    gutters: TreeRowGutter[];
    isVirtualRootChild: boolean;
    parentId: string | null;
  }> = [];

  for (let index = orderedRoots.length - 1; index >= 0; index--) {
    stack.push({
      node: orderedRoots[index],
      indent: multipleRoots ? 1 : 0,
      justBranched: multipleRoots,
      showConnector: multipleRoots,
      isLast: index === orderedRoots.length - 1,
      gutters: [],
      isVirtualRootChild: multipleRoots,
      parentId: null,
    });
  }

  while (stack.length > 0) {
    const current = stack.pop();
    if (!current) continue;
    const {
      node,
      indent,
      justBranched,
      showConnector,
      isLast,
      gutters,
      isVirtualRootChild,
      parentId,
    } = current;
    const displayIndent = multipleRoots ? Math.max(0, indent - 1) : indent;
    const connectorDisplayed = showConnector && !isVirtualRootChild;
    const connectorPosition = connectorDisplayed
      ? Math.max(0, displayIndent - 1)
      : -1;
    const children = orderTreeChildren(node.children);
    const hasActiveChild = children.some(child => child.containsActiveLeaf);

    const entryLabel = formatTreeEntryLabel(node);
    const presentation = buildTreeEntryPresentation(
      node.entry,
      entryLabel,
      node.label,
    );

    entries.push({
      id: node.entry.id,
      parentId,
      label: entryLabel,
      type: node.entry.type,
      timestamp: node.entry.timestamp,
      depth: displayIndent,
      trackColumns: buildTrackColumns(
        displayIndent,
        connectorPosition,
        isLast,
        gutters,
      ),
      isActive: node.containsActiveLeaf && !hasActiveChild,
      isOnActivePath: node.containsActiveLeaf,
      role: presentation.role,
      labelTag: presentation.labelTag,
      previewText: presentation.previewText,
      searchText: presentation.searchText,
      isSettingsEntry: presentation.isSettingsEntry,
      isLabeled: presentation.isLabeled,
      isToolOnlyAssistant: presentation.isToolOnlyAssistant,
    });

    const multipleChildren = children.length > 1;
    const childIndent = multipleChildren
      ? indent + 1
      : justBranched && indent > 0
        ? indent + 1
        : indent;
    const childGutters = connectorDisplayed
      ? [...gutters, { position: connectorPosition, show: !isLast }]
      : gutters;

    for (let index = children.length - 1; index >= 0; index--) {
      stack.push({
        node: children[index],
        indent: childIndent,
        justBranched: multipleChildren,
        showConnector: multipleChildren,
        isLast: index === children.length - 1,
        gutters: childGutters,
        isVirtualRootChild: false,
        parentId: node.entry.id,
      });
    }
  }

  return entries;
}

function buildTrackColumns(
  displayIndent: number,
  connectorPosition: number,
  isLast: boolean,
  gutters: readonly TreeRowGutter[],
): RpcTreeTrackColumn[] {
  const columns: RpcTreeTrackColumn[] = [];

  for (let level = 0; level < displayIndent; level++) {
    const gutter = gutters.find(item => item.position === level);
    if (gutter) {
      columns.push(gutter.show ? "line" : "blank");
      continue;
    }
    if (connectorPosition === level) {
      columns.push(isLast ? "branch-last" : "branch");
      continue;
    }
    columns.push("blank");
  }

  return columns;
}

function orderTreeChildren(
  children: readonly VisibleTreeNodeLike[],
): VisibleTreeNodeLike[] {
  const activeChildren = children.filter(child => child.containsActiveLeaf);
  const inactiveChildren = children.filter(child => !child.containsActiveLeaf);
  return [...activeChildren, ...inactiveChildren];
}

function buildTreeEntriesFromSession(
  sessionManager: Pick<SessionManager, "getLeafId" | "getTree">,
): RpcTreeEntry[] {
  const activeLeafId = sessionManager.getLeafId();
  const visibleTree = buildVisibleTree(
    sessionManager.getTree() as SessionTreeNodeLike[],
    activeLeafId,
  );
  return flattenVisibleTree(orderTreeChildren(visibleTree));
}

function buildTreeEntriesFromBranch(
  branch: readonly unknown[],
): RpcTreeEntry[] {
  const visibleEntries = branch.filter(entry => {
    const typedEntry = entry as { type?: string; id?: string };
    if (!typedEntry.id) return false;
    if (typedEntry.type && TREE_HARD_HIDDEN_ENTRY_TYPES.has(typedEntry.type)) {
      return false;
    }
    return true;
  });

  return visibleEntries.map((entry, index) => {
    const typedEntry = entry as
      | SessionEntry
      | ({ role?: string; content?: unknown; text?: string } & Record<
          string,
          unknown
        >);
    const type =
      typeof typedEntry.type === "string"
        ? typedEntry.type
        : ((typedEntry as { role?: string }).role ?? "unknown");
    const entryLabel = formatFallbackTreeEntryLabel(typedEntry);
    const presentation = buildTreeEntryPresentation(typedEntry, entryLabel);

    return {
      id: String((typedEntry as { id: string }).id),
      parentId:
        index === 0
          ? null
          : String((visibleEntries[index - 1] as { id?: string }).id ?? ""),
      label: entryLabel,
      type,
      timestamp:
        typeof (typedEntry as { timestamp?: string }).timestamp === "string"
          ? (typedEntry as { timestamp: string }).timestamp
          : undefined,
      depth: 0,
      trackColumns: [],
      isActive: index === visibleEntries.length - 1,
      isOnActivePath: true,
      role: presentation.role,
      labelTag: presentation.labelTag,
      previewText: presentation.previewText,
      searchText: presentation.searchText,
      isSettingsEntry: presentation.isSettingsEntry,
      isLabeled: presentation.isLabeled,
      isToolOnlyAssistant: presentation.isToolOnlyAssistant,
    };
  });
}

function buildTreeEntriesForSessionPath(sessionPath: string): RpcTreeEntry[] {
  const sessionManager = openSessionManager(sessionPath);
  return buildTreeEntriesFromSession(sessionManager);
}

function projectTreeEntriesWithTranscriptMessage(
  entries: readonly RpcTreeEntry[],
  message: RpcTranscriptMessage,
): RpcTreeEntry[] {
  const projectedEntryId = message.id ?? message.transcriptKey;
  if (!projectedEntryId) return [...entries];

  const [projectedEntry] = buildTreeEntriesFromBranch([
    {
      id: projectedEntryId,
      role: message.role,
      content: message.content,
      text: message.text,
      timestamp: message.timestamp,
      type: message.role,
    },
  ]);
  if (!projectedEntry) return [...entries];

  const nextEntries = entries.map(entry => ({
    ...entry,
    isActive: false,
  }));
  const existingIndex = nextEntries.findIndex(
    entry =>
      entry.id === projectedEntryId || entry.id === message.transcriptKey,
  );

  if (existingIndex >= 0) {
    const existingEntry = nextEntries[existingIndex];
    nextEntries[existingIndex] = {
      ...existingEntry,
      ...projectedEntry,
      id: projectedEntryId,
      parentId: existingEntry.parentId ?? projectedEntry.parentId ?? null,
      depth: existingEntry.depth ?? projectedEntry.depth,
      trackColumns: existingEntry.trackColumns ?? projectedEntry.trackColumns,
      isActive: true,
      isOnActivePath: true,
    };
    return nextEntries;
  }

  const activeParent = [...nextEntries]
    .reverse()
    .find(entry => entry.isOnActivePath);
  nextEntries.push({
    ...projectedEntry,
    id: projectedEntryId,
    parentId: activeParent?.id ?? null,
    isActive: true,
    isOnActivePath: true,
  });
  return nextEntries;
}

function transcriptMessageFromBranchEntry(
  entry: unknown,
  fallbackKey: string,
): RpcTranscriptMessage | null {
  if (!entry || typeof entry !== "object") return null;

  const typedEntry = entry as {
    type?: string;
    id?: unknown;
    role?: unknown;
    timestamp?: unknown;
    message?: unknown;
  };

  if (
    typedEntry.type === "message" &&
    typedEntry.message &&
    typeof typedEntry.message === "object"
  ) {
    const message = typedEntry.message as Record<string, unknown>;
    const id = typeof typedEntry.id === "string" ? typedEntry.id : undefined;
    const role = typeof message.role === "string" ? message.role : null;
    if (!role) return null;
    return {
      ...message,
      transcriptKey: id ?? fallbackKey,
      id,
      role,
      timestamp:
        typeof typedEntry.timestamp === "string"
          ? typedEntry.timestamp
          : undefined,
    };
  }

  if (typedEntry.type) {
    return transcriptMessageFromSessionEntry(
      typedEntry as SessionEntry,
      fallbackKey,
    );
  }

  if (typeof typedEntry.role === "string") {
    const flatMessage = typedEntry as Record<string, unknown>;
    const id = typeof typedEntry.id === "string" ? typedEntry.id : undefined;
    return {
      ...flatMessage,
      transcriptKey: id ?? fallbackKey,
      id,
      role: typedEntry.role,
      timestamp:
        typeof typedEntry.timestamp === "string"
          ? typedEntry.timestamp
          : undefined,
    };
  }

  return null;
}

function transcriptMessageFromSessionEntry(
  entry: SessionEntry,
  fallbackKey: string,
): RpcTranscriptMessage | null {
  const id = typeof entry.id === "string" ? entry.id : undefined;
  const timestamp =
    typeof entry.timestamp === "string" ? entry.timestamp : undefined;

  switch (entry.type) {
    case "compaction":
      return {
        transcriptKey: id ?? fallbackKey,
        id,
        role: "system",
        timestamp,
        content: [
          {
            type: "compaction",
            summary: entry.summary,
            tokensBefore: entry.tokensBefore,
            firstKeptEntryId: entry.firstKeptEntryId,
          },
        ],
      };
    case "branch_summary":
      return {
        transcriptKey: id ?? fallbackKey,
        id,
        role: "system",
        timestamp,
        content: [
          {
            type: "branch_summary",
            summary: entry.summary,
            fromId: entry.fromId,
          },
        ],
      };
    case "model_change":
      return {
        transcriptKey: id ?? fallbackKey,
        id,
        role: "system",
        timestamp,
        content: [
          {
            type: "model_change",
            provider: entry.provider,
            modelId: entry.modelId,
          },
        ],
      };
    case "thinking_level_change":
      return {
        transcriptKey: id ?? fallbackKey,
        id,
        role: "system",
        timestamp,
        content: [
          {
            type: "thinking_level_change",
            thinkingLevel: entry.thinkingLevel,
          },
        ],
      };
    case "session_info":
      return null;
    default:
      return null;
  }
}

function flattenMessagesForTranscript(
  branch: readonly unknown[],
): RpcTranscriptMessage[] {
  const messages: RpcTranscriptMessage[] = [];

  for (let index = 0; index < branch.length; index += 1) {
    const message = transcriptMessageFromBranchEntry(
      branch[index],
      `snapshot:${index}`,
    );
    if (message) {
      messages.push(message);
    }
  }

  return filterBootstrapTranscriptMessages(messages);
}

function trimAssistantContentToToolCall(
  content: RpcTranscriptMessage["content"],
  toolCallId: string,
): {
  content: RpcTranscriptMessage["content"];
  found: boolean;
} {
  if (!Array.isArray(content)) {
    return { content, found: false };
  }

  const trimmed: typeof content = [];
  for (const block of content) {
    trimmed.push(block);
    if (
      typeof block === "object" &&
      block !== null &&
      block.type === "toolCall" &&
      block.id === toolCallId
    ) {
      return { content: trimmed, found: true };
    }
  }

  return { content, found: false };
}

function buildExactSelectionTranscriptMessages(
  branch: readonly unknown[],
  targetEntryId: string,
): RpcTranscriptMessage[] {
  const messages = flattenMessagesForTranscript(branch);
  const targetIndex = messages.findIndex(
    message => message.id === targetEntryId,
  );
  if (targetIndex === -1) {
    return messages;
  }

  const targetMessage = messages[targetIndex];
  if (
    targetMessage.role !== "toolResult" ||
    typeof targetMessage.toolCallId !== "string" ||
    !targetMessage.toolCallId
  ) {
    return messages;
  }

  for (let index = targetIndex - 1; index >= 0; index -= 1) {
    const candidate = messages[index];
    if (candidate.role !== "assistant") {
      continue;
    }

    const trimmed = trimAssistantContentToToolCall(
      candidate.content,
      targetMessage.toolCallId,
    );
    if (!trimmed.found) {
      return messages;
    }

    const nextMessages = [...messages];
    nextMessages[index] = {
      ...candidate,
      content: trimmed.content,
    };
    return nextMessages;
  }

  return messages;
}

function filterBootstrapTranscriptMessages(
  messages: readonly RpcTranscriptMessage[],
): RpcTranscriptMessage[] {
  if (messages.length === 0) return [];
  if (messages.some(message => !isBootstrapTranscriptMessage(message))) {
    return [...messages];
  }
  return [];
}

function isBootstrapTranscriptMessage(message: RpcTranscriptMessage): boolean {
  if (message.role !== "system" || !Array.isArray(message.content)) {
    return false;
  }
  if (message.content.length !== 1) return false;

  const [block] = message.content;
  if (typeof block !== "object" || block === null) return false;
  const type = (block as { type?: unknown }).type;
  return type === "model_change" || type === "thinking_level_change";
}

function normalizeTranscriptPageLimit(limit: unknown): number {
  if (typeof limit !== "number" || !Number.isFinite(limit)) return 40;
  return Math.min(100, Math.max(1, Math.trunc(limit)));
}

function encodeTranscriptCursor(index: number): string {
  return `i:${index}`;
}

function decodeTranscriptCursor(cursor: unknown): number | null {
  if (typeof cursor !== "string") return null;
  const matched = /^i:(\d+)$/.exec(cursor);
  if (!matched) return null;
  const index = Number.parseInt(matched[1], 10);
  return Number.isInteger(index) ? index : null;
}

function buildTranscriptPage(
  messages: readonly RpcTranscriptMessage[],
  sessionPath: string | null,
  options?: {
    direction?: "latest" | "older";
    cursor?: string;
    limit?: number;
  },
): RpcTranscriptPage {
  const limit = normalizeTranscriptPageLimit(options?.limit);
  const total = messages.length;

  if (total === 0) {
    return {
      sessionPath: sessionPath ?? undefined,
      messages: [],
      hasOlder: false,
      hasNewer: false,
    };
  }

  const direction = options?.direction ?? "latest";
  let start = Math.max(0, total - limit);
  let end = total;

  if (direction === "older") {
    const cursorIndex = decodeTranscriptCursor(options?.cursor);
    const upperBound =
      cursorIndex == null
        ? Math.max(0, total - 1)
        : Math.min(total - 1, cursorIndex);
    end = Math.max(0, upperBound);
    start = Math.max(0, end - limit);
  }

  const pageMessages = messages.slice(start, end);
  const hasOlder = start > 0;
  const hasNewer = end < total;

  return {
    sessionPath: sessionPath ?? undefined,
    messages: pageMessages,
    oldestCursor:
      pageMessages.length > 0 ? encodeTranscriptCursor(start) : undefined,
    newestCursor:
      pageMessages.length > 0 ? encodeTranscriptCursor(end - 1) : undefined,
    hasOlder,
    hasNewer,
  };
}

function extractEventMessage(event: object): Record<string, unknown> | null {
  if (!event || typeof event !== "object") return null;

  const typedEvent = event as { message?: unknown; role?: unknown };
  if (typedEvent.message && typeof typedEvent.message === "object") {
    return typedEvent.message as Record<string, unknown>;
  }

  if (typeof typedEvent.role === "string") {
    return event as Record<string, unknown>;
  }

  return null;
}

function toolCallDeltaMetadata(
  item: string | RpcTranscriptContentBlock | undefined,
): Pick<RpcTranscriptDeltaEvent, "toolCallId" | "toolName"> {
  if (!item || typeof item !== "object" || item.type !== "toolCall") {
    return {};
  }

  return {
    toolCallId: typeof item.id === "string" ? item.id : undefined,
    toolName: typeof item.name === "string" ? item.name : undefined,
  };
}

function extractAssistantMessageDeltaEvent(
  event: object,
  message: RpcTranscriptMessage,
): {
  blockType: RpcTranscriptDeltaEvent["blockType"];
  contentIndex: number;
  delta: string;
  toolCallId?: string;
  toolName?: string;
} | null {
  if (!event || typeof event !== "object") return null;

  const typedEvent = event as { assistantMessageEvent?: unknown };
  const assistantMessageEvent = typedEvent.assistantMessageEvent;
  if (!assistantMessageEvent || typeof assistantMessageEvent !== "object") {
    return null;
  }

  const data = assistantMessageEvent as {
    type?: unknown;
    contentIndex?: unknown;
    delta?: unknown;
  };
  if (
    typeof data.contentIndex !== "number" ||
    !Number.isInteger(data.contentIndex) ||
    data.contentIndex < 0 ||
    typeof data.delta !== "string"
  ) {
    return null;
  }

  switch (data.type) {
    case "text_delta":
      return {
        blockType: "text",
        contentIndex: data.contentIndex,
        delta: data.delta,
      };
    case "thinking_delta":
      return {
        blockType: "thinking",
        contentIndex: data.contentIndex,
        delta: data.delta,
      };
    case "toolcall_delta":
      return {
        blockType: "toolCall",
        contentIndex: data.contentIndex,
        delta: data.delta,
        ...toolCallDeltaMetadata(
          transcriptContentItems(message)[data.contentIndex],
        ),
      };
    default:
      return null;
  }
}

function transcriptContentItems(
  message: RpcTranscriptMessage,
): readonly (string | RpcTranscriptContentBlock)[] {
  if (Array.isArray(message.content)) return message.content;
  if (typeof message.content === "string") return [message.content];
  if (typeof message.text === "string") return [message.text];
  return [];
}

function stringFromToolArguments(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === undefined || value === null) return "";
  try {
    return JSON.stringify(value);
  } catch {
    return "";
  }
}

function streamTextForContentItem(
  item: string | RpcTranscriptContentBlock | undefined,
): { blockType: RpcTranscriptDeltaEvent["blockType"]; text: string } | null {
  if (typeof item === "string") return { blockType: "text", text: item };
  if (!item || typeof item !== "object") return null;

  switch (item.type) {
    case "text":
      return { blockType: "text", text: item.text };
    case "thinking":
      return { blockType: "thinking", text: item.thinking };
    case "toolCall":
      return {
        blockType: "toolCall",
        text: stringFromToolArguments(item.arguments),
      };
    default:
      return null;
  }
}

function synthesizeTranscriptDelta(
  previous: RpcTranscriptMessage | undefined,
  next: RpcTranscriptMessage,
): Omit<
  RpcTranscriptDeltaEvent,
  "type" | "sessionPath" | "transcriptKey" | "messageId" | "role"
> | null {
  const previousItems = previous ? transcriptContentItems(previous) : [];
  const nextItems = transcriptContentItems(next);

  for (let index = 0; index < nextItems.length; index += 1) {
    const nextText = streamTextForContentItem(nextItems[index]);
    if (!nextText) continue;

    const previousText = streamTextForContentItem(previousItems[index]);
    const previousValue =
      previousText?.blockType === nextText.blockType ? previousText.text : "";
    if (!nextText.text.startsWith(previousValue)) continue;

    const delta = nextText.text.slice(previousValue.length);
    if (!delta) continue;

    return {
      blockType: nextText.blockType,
      contentIndex: index,
      delta,
      ...toolCallDeltaMetadata(nextItems[index]),
    };
  }

  return null;
}

function streamStartMessage(
  message: RpcTranscriptMessage,
): RpcTranscriptMessage {
  if (message.role !== "assistant") return message;
  return {
    ...message,
    content: [],
    text: undefined,
  };
}

function findLatestModelInfo(branch: readonly SessionEntry[]): RpcModel | null {
  for (let index = branch.length - 1; index >= 0; index -= 1) {
    const entry = branch[index];
    if (entry?.type === "model_change") {
      return { provider: entry.provider, id: entry.modelId };
    }
  }

  return null;
}

function normalizeThinkingLevel(value: string): RpcThinkingLevel {
  switch (value) {
    case "off":
    case "minimal":
    case "low":
    case "medium":
    case "high":
    case "xhigh":
      return value;
    default:
      return "off";
  }
}

function buildStateFromStoredSession(
  sessionManager: SessionManager,
  fallbackCwd?: string,
): RpcSessionState {
  const branch = sessionManager.getBranch();
  const context = sessionManager.buildSessionContext();
  const model = findLatestModelInfo(branch);
  const workspacePath =
    normalizeOptionalWorkspaceRoot(sessionManager.getCwd()) ??
    normalizeOptionalWorkspaceRoot(fallbackCwd) ??
    fallbackCwd;
  const workspaceEnvironments = detectWorkspaceEnvironments(workspacePath);

  return {
    model: model ?? undefined,
    thinkingLevel: normalizeThinkingLevel(context.thinkingLevel),
    isStreaming: false,
    isCompacting: false,
    steeringMode: "all",
    followUpMode: "all",
    sessionFile: sessionManager.getSessionFile(),
    sessionId: sessionManager.getSessionId(),
    sessionName: sessionDisplayName(
      sessionManager,
      sessionManager.getSessionFile(),
    ),
    workspacePath,
    ...(workspaceEnvironments ? { workspaceEnvironments } : {}),
    gitBranch: getCurrentGitBranch(workspacePath),
    autoCompactionEnabled: false,
    messageCount: sessionManager.getEntries()?.length ?? 0,
    pendingMessageCount: 0,
  };
}

function formatTreeEntryLabel(node: SessionTreeNodeLike): string {
  const labelPrefix = node.label ? `[${node.label}] ` : "";
  return `${labelPrefix}${describeSessionEntry(node.entry)}`.trim();
}

function summarizeTokenUsage(
  branch: unknown[],
  entries?: unknown[] | undefined,
): {
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  messageCount: number;
  totalCost: number;
  lastAssistantUsage: {
    input?: number;
    output?: number;
    cacheRead?: number;
    cacheWrite?: number;
  } | null;
  lastModel: { provider?: string; modelId?: string } | null;
} {
  let inputTokens = 0;
  let outputTokens = 0;
  let cacheReadTokens = 0;
  let cacheWriteTokens = 0;
  let totalCost = 0;
  let lastAssistantUsage: {
    input?: number;
    output?: number;
    cacheRead?: number;
    cacheWrite?: number;
  } | null = null;
  let lastModel: { provider?: string; modelId?: string } | null = null;

  for (const entry of branch) {
    const e = entry as {
      type?: string;
      provider?: string;
      modelId?: string;
      message?: {
        role?: string;
        usage?: {
          cost?: { total?: number };
          input?: number;
          output?: number;
          cacheRead?: number;
          cacheWrite?: number;
        };
      };
    };

    if (e.type === "model_change") {
      lastModel = { provider: e.provider, modelId: e.modelId };
    }

    if (e.type === "message" && e.message?.role === "assistant") {
      const usage = e.message.usage;
      if (!usage) continue;
      inputTokens += usage.input ?? 0;
      outputTokens += usage.output ?? 0;
      cacheReadTokens += usage.cacheRead ?? 0;
      cacheWriteTokens += usage.cacheWrite ?? 0;
      totalCost += usage.cost?.total ?? 0;
      if ((usage.input ?? 0) > 0) {
        lastAssistantUsage = usage;
      }
    }
  }

  return {
    inputTokens,
    outputTokens,
    cacheReadTokens,
    cacheWriteTokens,
    messageCount: entries?.length ?? 0,
    totalCost,
    lastAssistantUsage,
    lastModel,
  };
}

function sessionDisplayName(
  sessionManager: {
    getSessionName: () => string | undefined;
    getEntries: () => unknown[];
    getSessionId: () => string;
  },
  _sessionPath?: string,
): string {
  const firstUserEntry = sessionManager.getEntries().find(entry => {
    if (
      typeof entry === "object" &&
      entry !== null &&
      "type" in entry &&
      entry.type === "message" &&
      "message" in entry
    ) {
      const message = entry.message as { role?: unknown };
      return message.role === "user";
    }

    if (typeof entry === "object" && entry !== null && "role" in entry) {
      return entry.role === "user";
    }

    return false;
  });

  if (firstUserEntry && typeof firstUserEntry === "object") {
    const message =
      "type" in firstUserEntry &&
      firstUserEntry.type === "message" &&
      "message" in firstUserEntry
        ? (firstUserEntry.message as { content?: unknown; text?: string })
        : (firstUserEntry as { content?: unknown; text?: string });
    const text = collapseWhitespace(extractMessageText(message));
    if (text) return text;
  }

  return DEFAULT_PENDING_SESSION_NAME;
}

function formatFallbackTreeEntryLabel(
  entry:
    | SessionEntry
    | ({ role?: string; content?: unknown; text?: string } & Record<
        string,
        unknown
      >),
): string {
  if ((entry as { type?: string }).type === "message" && "message" in entry) {
    return describeSessionEntry(entry as SessionEntry);
  }

  const role =
    typeof (entry as { role?: string }).role === "string"
      ? (entry as { role: string }).role
      : undefined;
  if (role) {
    const content = collapseWhitespace(
      extractMessageText(entry as { content?: unknown; text?: string }),
    );
    return content ? `${role}: ${content}` : role;
  }

  return describeSessionEntry(entry as SessionEntry);
}

function describeSessionEntry(entry: SessionEntry): string {
  switch (entry.type) {
    case "message":
      return describeMessage(
        entry.message as {
          role?: string;
          content?: unknown;
          text?: string;
          stopReason?: string;
          errorMessage?: string;
          toolName?: string;
          command?: string;
        },
      );
    case "custom_message": {
      const customText = Array.isArray(entry.content)
        ? entry.content
            .filter(
              item =>
                typeof item === "object" &&
                item !== null &&
                (item as { type?: string }).type === "text",
            )
            .map(item => (item as { text?: string }).text ?? "")
            .join(" ")
        : typeof entry.content === "string"
          ? entry.content
          : "";
      const content = collapseWhitespace(customText);
      return content
        ? `[${entry.customType}]: ${content}`
        : `[${entry.customType}]`;
    }
    case "compaction":
      return `[compaction: ${Math.round(entry.tokensBefore / 1000)}k tokens]`;
    case "branch_summary":
      return `[branch summary]: ${collapseWhitespace(entry.summary)}`;
    case "model_change":
      return `[model: ${entry.modelId}]`;
    case "thinking_level_change":
      return `[thinking: ${entry.thinkingLevel}]`;
    case "session_info":
      return entry.name ? `[title: ${entry.name}]` : "[title]";
    case "custom":
      return `[custom: ${entry.customType}]`;
    case "label":
      return entry.label ? `[label: ${entry.label}]` : "[label]";
    default:
      return (entry as { type: string }).type;
  }
}

function normalizeRpcImages(images: unknown): RpcImageContent[] | undefined {
  if (!Array.isArray(images)) return undefined;

  const normalized = images.flatMap((image): RpcImageContent[] => {
    if (typeof image !== "object" || image === null) return [];

    const data = (image as { data?: unknown }).data;
    const mimeType = (image as { mimeType?: unknown }).mimeType;
    if (
      typeof data !== "string" ||
      typeof mimeType !== "string" ||
      !mimeType.startsWith("image/")
    ) {
      return [];
    }

    return [{ type: "image", data, mimeType }];
  });

  return normalized.length > 0 ? normalized : undefined;
}

function buildUserMessageContent(
  message: string,
  images?: RpcImageContent[],
): UserMessageContent {
  if (!images?.length) return message;

  const content: UserMessageBlock[] = [];
  if (message) {
    content.push({ type: "text", text: message } as PiTextContent);
  }
  content.push(...images);
  return content;
}

function queuedMessageTimestamp(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value)
    ? value
    : Date.now();
}

function extractMessageImages(message: {
  content?: unknown;
}): RpcImageContent[] {
  if (!Array.isArray(message.content)) return [];

  return message.content.flatMap(item => {
    if (typeof item !== "object" || item === null) return [];

    const typedItem = item as {
      type?: unknown;
      data?: unknown;
      mimeType?: unknown;
    };
    if (
      typedItem.type !== "image" ||
      typeof typedItem.data !== "string" ||
      typeof typedItem.mimeType !== "string"
    ) {
      return [];
    }

    return [
      {
        type: "image" as const,
        data: typedItem.data,
        mimeType: typedItem.mimeType,
      },
    ];
  });
}

function toRpcQueuedMessage(
  message: {
    content?: unknown;
    text?: string;
    timestamp?: unknown;
  },
  queueType: "steering" | "followUp",
): RpcQueuedMessage {
  return {
    text: extractMessageText(message),
    images: extractMessageImages(message),
    timestamp: queuedMessageTimestamp(message.timestamp),
    queueType,
  };
}

function queuedAgentMessages(
  session: AgentSession,
  queueName: "steeringQueue" | "followUpQueue",
): unknown[] {
  const agent = session.agent as unknown as
    | {
        steeringQueue?: { messages?: unknown[] };
        followUpQueue?: { messages?: unknown[] };
      }
    | undefined;
  const queue = agent?.[queueName];
  return Array.isArray(queue?.messages) ? queue.messages : [];
}

function trackedQueuedMessages(
  session: AgentSession,
  queueName: "_steeringMessages" | "_followUpMessages",
): string[] {
  const sessionWithQueue = session as unknown as
    | {
        _steeringMessages?: string[];
        _followUpMessages?: string[];
      }
    | undefined;
  const queue = sessionWithQueue?.[queueName];
  return Array.isArray(queue) ? [...queue] : [];
}

function buildTrackedQueuedMessages(
  session: AgentSession,
  queueName: "steeringQueue" | "followUpQueue",
  trackedQueueName: "_steeringMessages" | "_followUpMessages",
  queueType: "steering" | "followUp",
): RpcQueuedMessage[] {
  const queued = queuedAgentMessages(session, queueName);
  const tracked = trackedQueuedMessages(session, trackedQueueName);
  const messages = tracked.map((text, index) => {
    const queuedMessage = queued[index] as
      | {
          content?: unknown;
          text?: string;
          timestamp?: unknown;
        }
      | undefined;

    if (queuedMessage && extractMessageText(queuedMessage) === text) {
      return toRpcQueuedMessage(queuedMessage, queueType);
    }

    return {
      text,
      images: [],
      timestamp: Date.now(),
      queueType,
    };
  });

  for (let index = tracked.length; index < queued.length; index += 1) {
    messages.push(
      toRpcQueuedMessage(
        queued[index] as {
          content?: unknown;
          text?: string;
          timestamp?: unknown;
        },
        queueType,
      ),
    );
  }

  return messages;
}

function buildQueueUpdateEvent(
  session: AgentSession,
  sessionPath: string | null,
): RpcQueueUpdateEvent {
  return {
    type: "queue_update",
    sessionPath: sessionPath ?? undefined,
    steering: buildTrackedQueuedMessages(
      session,
      "steeringQueue",
      "_steeringMessages",
      "steering",
    ),
    followUp: buildTrackedQueuedMessages(
      session,
      "followUpQueue",
      "_followUpMessages",
      "followUp",
    ),
  };
}

function clearSteeringQueue(session: AgentSession): void {
  const followUpQueue = [...queuedAgentMessages(session, "followUpQueue")];
  const followUpMessages = trackedQueuedMessages(session, "_followUpMessages");
  const agent = session.agent as unknown as {
    clearSteeringQueue?: () => void;
    followUpQueue?: { messages?: unknown[] };
  };
  const sessionWithQueue = session as unknown as {
    clearQueue?: () => { steering: string[]; followUp: string[] };
    _steeringMessages?: string[];
    _followUpMessages?: string[];
    _emitQueueUpdate?: () => void;
  };

  if (typeof sessionWithQueue.clearQueue === "function") {
    sessionWithQueue.clearQueue();

    const restoredFollowUpQueue = agent.followUpQueue?.messages;
    if (Array.isArray(restoredFollowUpQueue)) {
      restoredFollowUpQueue.length = 0;
      restoredFollowUpQueue.push(...followUpQueue);
    }

    if (Array.isArray(sessionWithQueue._followUpMessages)) {
      sessionWithQueue._followUpMessages.length = 0;
      sessionWithQueue._followUpMessages.push(...followUpMessages);
    }

    sessionWithQueue._emitQueueUpdate?.();
    return;
  }

  agent.clearSteeringQueue?.();
  if (Array.isArray(sessionWithQueue._steeringMessages)) {
    sessionWithQueue._steeringMessages.length = 0;
  }

  sessionWithQueue._emitQueueUpdate?.();
}

function dequeueFollowUpMessage(
  session: AgentSession,
  index: number,
): RpcQueuedMessage {
  if (!Number.isInteger(index) || index < 0) {
    throw new Error("Queued message index must be a non-negative integer");
  }

  const followUpQueue = queuedAgentMessages(session, "followUpQueue");
  if (index >= followUpQueue.length) {
    throw new Error(`Queued message not found at index ${index}`);
  }

  const sessionWithQueue = session as unknown as {
    _followUpMessages?: string[];
    _emitQueueUpdate?: () => void;
  };
  const trackedMessages = sessionWithQueue._followUpMessages;
  if (!Array.isArray(trackedMessages)) {
    throw new Error("Detached follow-up queue is unavailable");
  }

  const [removed] = followUpQueue.splice(index, 1);
  trackedMessages.splice(index, 1);
  sessionWithQueue._emitQueueUpdate?.();

  return toRpcQueuedMessage(
    removed as {
      content?: unknown;
      text?: string;
      timestamp?: unknown;
    },
    "followUp",
  );
}

function describeMessage(message: {
  role?: string;
  content?: unknown;
  text?: string;
  stopReason?: string;
  errorMessage?: string;
  toolName?: string;
  command?: string;
}): string {
  const role = message.role ?? "message";
  const content = collapseWhitespace(extractMessageText(message));

  switch (role) {
    case "user":
      return content ? `user: ${content}` : "user";
    case "assistant":
      if (content) return `assistant: ${content}`;
      if (message.stopReason === "aborted") return "assistant: (aborted)";
      if (message.errorMessage)
        return `assistant: ${collapseWhitespace(message.errorMessage)}`;
      return "assistant: (no content)";
    case "toolResult":
      return message.toolName ? `[tool: ${message.toolName}]` : "[tool result]";
    case "bashExecution":
      return message.command
        ? `[bash]: ${collapseWhitespace(message.command)}`
        : "[bash]";
    default:
      return content ? `${role}: ${content}` : `[${role}]`;
  }
}

function extractMessageText(message: {
  content?: unknown;
  text?: string;
}): string {
  if (typeof message.content === "string") return message.content;
  if (typeof message.text === "string") return message.text;
  if (!Array.isArray(message.content)) return "";

  return message.content
    .map(block => {
      if (typeof block === "string") return block;
      if (typeof block !== "object" || block === null) return "";
      const typedBlock = block as {
        type?: string;
        text?: string;
        thinking?: string;
      };
      if (typedBlock.type === "text" || typedBlock.type === "toolResult") {
        return typeof typedBlock.text === "string" ? typedBlock.text : "";
      }
      if (typedBlock.type === "thinking") {
        return typeof typedBlock.thinking === "string"
          ? typedBlock.thinking
          : "";
      }
      return "";
    })
    .filter(Boolean)
    .join(" ");
}

function collapseWhitespace(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

/* ============================================================================
 * Session runtime
 * ========================================================================== */

class SessionRuntime {
  private selectedSessionPath: string | null = null;
  private unsubscribeSelectedSession: (() => void) | undefined;

  constructor(
    private readonly context: WsRpcAdapterContext,
    private readonly clientId: string,
    private readonly registry: DetachedSessionRegistry,
    private readonly createExtensionUIContext: () => ExtensionUIContext,
    private readonly onDetachedSessionEvent: (event: AgentSessionEvent) => void,
  ) {}

  hasDetachedSelection(): boolean {
    return this.selectedSessionPath !== null;
  }

  getDetachedSession(): AgentSession | null {
    if (!this.selectedSessionPath) return null;
    return this.registry.getActiveSession(this.selectedSessionPath);
  }

  getCachedSessionManager(sessionPath: string): SessionManager | null {
    return this.registry.getCachedSessionManager(sessionPath);
  }

  getCachedSessionManagers(): SessionManager[] {
    return this.registry.getCachedSessionManagers();
  }

  isSessionRunning(sessionPath: string): boolean {
    const liveSessionPath = this.context.state.sessionManager.getSessionFile();
    if (liveSessionPath && sessionPath === liveSessionPath) {
      return !this.context.state.isIdle();
    }
    return this.registry.isSessionRunning(sessionPath);
  }

  currentDetachedSessionPath(): string | null {
    return this.selectedSessionPath;
  }

  currentTranscriptSessionPath(): string | null {
    return (
      this.selectedSessionPath ??
      this.context.state.sessionManager.getSessionFile() ??
      null
    );
  }

  currentGitCwd(): string {
    if (this.selectedSessionPath) {
      const activeSession = this.registry.getActiveSession(
        this.selectedSessionPath,
      );
      const activeCwd = activeSession?.sessionManager.getCwd();
      if (activeCwd) {
        return activeCwd;
      }

      const storedSession = this.registry.getCachedSessionManager(
        this.selectedSessionPath,
      );
      const storedCwd = storedSession?.getCwd();
      if (storedCwd) {
        return storedCwd;
      }
    }

    return this.context.state.cwd;
  }

  shouldHandleLiveSessionEvents(): boolean {
    return !this.selectedSessionPath || this.isViewingLiveSession();
  }

  buildCurrentTranscriptMessages(): RpcTranscriptMessage[] {
    if (this.isViewingLiveSession()) {
      return flattenMessagesForTranscript(
        this.context.state.sessionManager.getBranch(),
      );
    }

    if (this.selectedSessionPath) {
      return flattenMessagesForTranscript(
        this.registry
          .openSession(this.selectedSessionPath)
          .getSessionManager()
          .getBranch(),
      );
    }

    return flattenMessagesForTranscript(
      this.context.state.sessionManager.getBranch(),
    );
  }

  buildCurrentTranscriptPage(options?: {
    direction?: "latest" | "older";
    cursor?: string;
    limit?: number;
  }): RpcTranscriptPage {
    return buildTranscriptPage(
      this.buildCurrentTranscriptMessages(),
      this.currentTranscriptSessionPath(),
      options,
    );
  }

  buildTreeEntriesForSessionPath(sessionPath: string | null): RpcTreeEntry[] {
    const activeSession = sessionPath
      ? this.registry.getActiveSession(sessionPath)
      : null;
    if (activeSession) {
      return buildTreeEntriesFromSession(activeSession.sessionManager);
    }

    const liveSessionPath = this.context.state.sessionManager.getSessionFile();
    if (!sessionPath || sessionPath === liveSessionPath) {
      return buildTreeEntriesFromSession(this.context.state.sessionManager);
    }

    const cachedSession = this.registry.getCachedSessionManager(sessionPath);
    if (cachedSession) {
      return buildTreeEntriesFromSession(cachedSession);
    }

    if (fs.existsSync(sessionPath)) {
      return buildTreeEntriesForSessionPath(sessionPath);
    }

    return [];
  }

  buildActiveState(): RpcSessionState {
    if (this.selectedSessionPath) {
      const activeSession = this.registry.getActiveSession(
        this.selectedSessionPath,
      );
      if (activeSession) {
        const workspacePath =
          activeSession.sessionManager.getCwd() ?? this.context.state.cwd;
        const workspaceEnvironments =
          detectWorkspaceEnvironments(workspacePath);
        return {
          model:
            activeSession.model ??
            findLatestModelInfo(activeSession.sessionManager.getBranch()) ??
            undefined,
          thinkingLevel: activeSession.thinkingLevel,
          isStreaming: activeSession.isStreaming,
          isCompacting: activeSession.isCompacting,
          steeringMode: activeSession.steeringMode,
          followUpMode: activeSession.followUpMode,
          sessionFile: activeSession.sessionFile,
          sessionId: activeSession.sessionId,
          sessionName: sessionDisplayName(
            activeSession.sessionManager,
            activeSession.sessionFile,
          ),
          workspacePath,
          ...(workspaceEnvironments ? { workspaceEnvironments } : {}),
          gitBranch: getCurrentGitBranch(workspacePath),
          autoCompactionEnabled: activeSession.autoCompactionEnabled,
          messageCount: activeSession.sessionManager.getEntries()?.length ?? 0,
          pendingMessageCount: activeSession.pendingMessageCount,
        };
      }

      if (!this.isViewingLiveSession()) {
        return buildStateFromStoredSession(
          this.registry
            .openSession(this.selectedSessionPath)
            .getSessionManager(),
          this.context.state.cwd,
        );
      }
    }

    const sessionFile = this.context.state.sessionManager.getSessionFile();
    const workspaceEnvironments = detectWorkspaceEnvironments(
      this.context.state.cwd,
    );
    return {
      model: this.context.state.getCurrentModel(),
      thinkingLevel: normalizeThinkingLevel(
        this.context.state.getThinkingLevel(),
      ),
      isStreaming: !this.context.state.isIdle(),
      isCompacting: false,
      steeringMode: "all",
      followUpMode: "all",
      sessionFile,
      sessionId: this.context.state.sessionManager.getSessionId(),
      sessionName: sessionDisplayName(
        {
          getSessionName: () => undefined,
          getEntries: () =>
            this.context.state.sessionManager.getEntries() ?? [],
          getSessionId: () => this.context.state.sessionManager.getSessionId(),
        },
        sessionFile,
      ),
      workspacePath: normalizeOptionalWorkspaceRoot(this.context.state.cwd),
      ...(workspaceEnvironments ? { workspaceEnvironments } : {}),
      gitBranch: getCurrentGitBranch(this.context.state.cwd),
      autoCompactionEnabled: false,
      messageCount: this.context.state.sessionManager.getEntries()?.length ?? 0,
      pendingMessageCount: this.context.state.hasPendingMessages() ? 1 : 0,
    };
  }

  async createDetachedSession(
    options: {
      workspacePath?: string;
      transcriptLimit?: number;
    } = {},
  ): Promise<SessionSummary> {
    const currentSessionFile =
      this.currentDetachedSessionPath() ??
      this.context.state.sessionManager.getSessionFile();
    const currentSessionManager = this.selectedSessionPath
      ? this.registry.getCachedSessionManager(this.selectedSessionPath)
      : null;
    const targetCwd =
      normalizeOptionalWorkspaceRoot(options.workspacePath) ||
      normalizeOptionalWorkspaceRoot(currentSessionManager?.getCwd()) ||
      normalizeOptionalWorkspaceRoot(
        this.context.state.sessionManager.getCwd(),
      ) ||
      normalizeOptionalWorkspaceRoot(this.context.state.cwd) ||
      this.context.state.cwd;
    const sessionDir = options.workspacePath
      ? resolveWorkspaceSessionDirPath(targetCwd)
      : currentSessionFile
        ? path.dirname(currentSessionFile)
        : undefined;

    const handle = this.registry.createSession({ cwd: targetCwd, sessionDir });
    await this.selectSessionPath(handle.sessionPath);

    return this.buildSessionSummary(
      handle.getSessionManager(),
      handle.sessionPath,
      options.transcriptLimit,
    );
  }

  async switchToStoredSession(
    sessionPath: string,
    transcriptLimit?: number,
  ): Promise<SessionSummary> {
    const handle = this.registry.openSession(sessionPath);
    await this.selectSessionPath(sessionPath);
    return this.buildSessionSummary(
      handle.getSessionManager(),
      sessionPath,
      transcriptLimit,
    );
  }

  async ensureDetachedSession(_options?: {
    skipInitialSnapshot?: boolean;
  }): Promise<AgentSession> {
    if (!this.selectedSessionPath) {
      throw new Error("Selected session file not found");
    }

    await this.registry.bindViewer(this.selectedSessionPath, {
      clientId: this.clientId,
      uiContext: this.createExtensionUIContext(),
    });

    return this.registry.ensureSession(this.selectedSessionPath);
  }

  async ensureDetachedSessionFromLive(options?: {
    skipInitialSnapshot?: boolean;
  }): Promise<AgentSession> {
    const liveSessionPath = this.context.state.sessionManager.getSessionFile();
    if (!liveSessionPath || !fs.existsSync(liveSessionPath)) {
      throw new Error("No session file available");
    }

    await this.selectSessionPath(liveSessionPath);
    return this.ensureDetachedSession(options);
  }

  clearSelection(): void {
    if (this.unsubscribeSelectedSession) {
      this.unsubscribeSelectedSession();
      this.unsubscribeSelectedSession = undefined;
    }

    const selectedSessionPath = this.selectedSessionPath;
    this.selectedSessionPath = null;
    if (selectedSessionPath) {
      void this.registry.releaseViewer(selectedSessionPath, this.clientId);
    }
  }

  dispose(): void {
    this.clearSelection();
  }

  private buildSessionSummary(
    sessionManager: SessionManager,
    sessionPath: string,
    transcriptLimit?: number,
  ): SessionSummary {
    return {
      sessionManager,
      sessionPath,
      transcript: buildTranscriptPage(
        flattenMessagesForTranscript(sessionManager.getBranch()),
        sessionPath,
        { limit: transcriptLimit },
      ),
      treeEntries: buildTreeEntriesFromSession(sessionManager),
      sessionId: sessionManager.getSessionId(),
      sessionName: sessionDisplayName(sessionManager, sessionPath),
      workspacePath: normalizeOptionalWorkspaceRoot(sessionManager.getCwd()),
    };
  }

  private isViewingLiveSession(): boolean {
    const liveSessionPath = this.context.state.sessionManager.getSessionFile();
    return Boolean(
      this.selectedSessionPath &&
      liveSessionPath &&
      this.selectedSessionPath === liveSessionPath &&
      !this.registry.isSessionActive(this.selectedSessionPath),
    );
  }

  private async selectSessionPath(sessionPath: string): Promise<void> {
    if (this.selectedSessionPath === sessionPath) {
      await this.registry.bindViewer(sessionPath, {
        clientId: this.clientId,
        uiContext: this.createExtensionUIContext(),
      });
      if (!this.unsubscribeSelectedSession) {
        this.unsubscribeSelectedSession = this.registry
          .openSession(sessionPath)
          .subscribe(event => {
            this.onDetachedSessionEvent(event);
          });
      }
      return;
    }

    if (this.unsubscribeSelectedSession) {
      this.unsubscribeSelectedSession();
      this.unsubscribeSelectedSession = undefined;
    }

    if (this.selectedSessionPath) {
      await this.registry.releaseViewer(
        this.selectedSessionPath,
        this.clientId,
      );
    }

    this.selectedSessionPath = sessionPath;
    this.unsubscribeSelectedSession = this.registry
      .openSession(sessionPath)
      .subscribe(event => {
        this.onDetachedSessionEvent(event);
      });

    await this.registry.bindViewer(sessionPath, {
      clientId: this.clientId,
      uiContext: this.createExtensionUIContext(),
    });
  }
}

/* ============================================================================
 * Transcript projector
 * ========================================================================== */

class TranscriptProjector {
  private state: TranscriptSyncState = {
    sessionPath: null,
    nextEphemeralId: 0,
    messageIdToKey: new Map(),
    openKeysByRole: new Map(),
    lastMessagesByKey: new Map(),
    closedKeys: new Set(),
  };

  syncPage(page: {
    messages: readonly RpcTranscriptMessage[];
    sessionPath?: string | null;
  }): void {
    const previousClosedKeys = this.state.closedKeys;
    this.state = {
      sessionPath: page.sessionPath ?? null,
      nextEphemeralId: 0,
      messageIdToKey: new Map(),
      openKeysByRole: new Map(),
      lastMessagesByKey: new Map(),
      closedKeys: new Set(),
    };

    for (const message of page.messages) {
      const key = message.transcriptKey ?? message.id;
      if (!key) continue;
      if (message.id) {
        this.state.messageIdToKey.set(message.id, key);
      }
      if (previousClosedKeys.has(key)) {
        this.state.closedKeys.add(key);
      }
      this.state.lastMessagesByKey.set(key, message);
    }
  }

  buildSnapshotEvent(page: RpcTranscriptPage): RpcTranscriptSnapshotEvent {
    this.syncPage(page);
    return {
      type: "transcript_snapshot",
      ...page,
    };
  }

  projectLifecycleEvent(
    eventType: "message_start" | "message_update" | "message_end",
    event: object,
    sessionPath: string | null,
  ): RpcTranscriptStartEvent | RpcTranscriptUpsertEvent | null {
    const message = extractEventMessage(event);
    if (!message) return null;

    if (this.state.sessionPath !== sessionPath) {
      this.syncPage({ messages: [], sessionPath });
    }

    const transcriptKey = this.resolveTranscriptKey(eventType, message);
    if (!transcriptKey) return null;

    const transcriptMessage = this.toTranscriptMessage(message, transcriptKey);
    if (!transcriptMessage) return null;
    const payloadMessage =
      eventType === "message_start"
        ? streamStartMessage(transcriptMessage)
        : transcriptMessage;
    this.rememberTranscriptMessage(payloadMessage);

    return {
      type:
        eventType === "message_start"
          ? "transcript_start"
          : "transcript_upsert",
      sessionPath: sessionPath ?? undefined,
      message: payloadMessage,
    };
  }

  projectDeltaEvent(
    event: object,
    sessionPath: string | null,
  ): RpcTranscriptDeltaEvent | null {
    const message = extractEventMessage(event);
    if (!message) return null;

    if (this.state.sessionPath !== sessionPath) {
      this.syncPage({ messages: [], sessionPath });
    }

    const transcriptKey = this.resolveTranscriptKey("message_update", message);
    if (!transcriptKey) return null;
    if (this.state.closedKeys.has(transcriptKey)) {
      return null;
    }

    const role = typeof message.role === "string" ? message.role : null;
    if (!role) return null;

    const transcriptMessage = this.toTranscriptMessage(message, transcriptKey);
    if (!transcriptMessage) return null;

    const deltaEvent =
      extractAssistantMessageDeltaEvent(event, transcriptMessage) ??
      synthesizeTranscriptDelta(
        this.state.lastMessagesByKey.get(transcriptKey),
        transcriptMessage,
      );
    this.rememberTranscriptMessage(transcriptMessage);
    if (!deltaEvent) return null;

    return {
      type: "transcript_delta",
      sessionPath: sessionPath ?? undefined,
      transcriptKey,
      messageId: typeof message.id === "string" ? message.id : undefined,
      role,
      ...deltaEvent,
    };
  }

  private rememberTranscriptMessage(message: RpcTranscriptMessage): void {
    const key = message.transcriptKey ?? message.id;
    if (!key) return;
    this.state.lastMessagesByKey.set(key, message);
    if (message.id) {
      this.state.messageIdToKey.set(message.id, key);
    }
  }

  private nextTranscriptKey(): string {
    this.state.nextEphemeralId += 1;
    return `live:${this.state.nextEphemeralId}`;
  }

  private roleOpenKeys(role: string): string[] {
    const existing = this.state.openKeysByRole.get(role);
    if (existing) return existing;
    const created: string[] = [];
    this.state.openKeysByRole.set(role, created);
    return created;
  }

  private markRoleKeyOpen(role: string, key: string): void {
    this.state.closedKeys.delete(key);
    const keys = this.roleOpenKeys(role);
    if (!keys.includes(key)) {
      keys.push(key);
    }
  }

  private markRoleKeyClosed(role: string, key: string): void {
    this.state.closedKeys.add(key);
    const keys = this.state.openKeysByRole.get(role);
    if (!keys) return;
    const next = keys.filter(candidate => candidate !== key);
    if (next.length > 0) {
      this.state.openKeysByRole.set(role, next);
      return;
    }
    this.state.openKeysByRole.delete(role);
  }

  private findOpenRoleKey(role: string): string | null {
    const keys = this.state.openKeysByRole.get(role);
    if (!keys || keys.length === 0) return null;
    return keys[keys.length - 1] ?? null;
  }

  private resolveTranscriptKey(
    eventType: "message_start" | "message_update" | "message_end",
    message: Record<string, unknown>,
  ): string | null {
    const role = typeof message.role === "string" ? message.role : null;
    if (!role) return null;

    const messageId = typeof message.id === "string" ? message.id : null;
    if (eventType === "message_start") {
      const key =
        (messageId && this.state.messageIdToKey.get(messageId)) ??
        this.nextTranscriptKey();
      if (messageId) {
        this.state.messageIdToKey.set(messageId, key);
      }
      this.markRoleKeyOpen(role, key);
      return key;
    }

    const knownKey =
      (messageId && this.state.messageIdToKey.get(messageId)) ??
      this.findOpenRoleKey(role);
    const key = knownKey ?? this.nextTranscriptKey();

    if (messageId) {
      this.state.messageIdToKey.set(messageId, key);
    }
    if (!knownKey) {
      this.markRoleKeyOpen(role, key);
    }
    if (eventType === "message_end") {
      this.markRoleKeyClosed(role, key);
    }
    return key;
  }

  private toTranscriptMessage(
    message: Record<string, unknown>,
    transcriptKey: string,
  ): RpcTranscriptMessage | null {
    const role = typeof message.role === "string" ? message.role : null;
    if (!role) return null;

    return {
      transcriptKey,
      ...message,
      id: typeof message.id === "string" ? message.id : undefined,
      role,
      timestamp:
        typeof message.timestamp === "string" ? message.timestamp : undefined,
    };
  }
}

/* ============================================================================
 * Extension UI bridge
 * ========================================================================== */

const TRANSCRIPT_DELTA_BATCH_MS = 200;
const TRANSCRIPT_DELTA_MAX_CHARS = 32;

class ExtensionUIBridge {
  private pendingRequests = new Map<string, PendingUIRequest>();

  constructor(
    private readonly clientId: string,
    private readonly config: BridgeConfig,
    private readonly send: (message: ServerMessage) => void,
  ) {}

  handleResponse(response: RpcExtensionUIResponse): void {
    const pending = this.pendingRequests.get(response.id);
    if (!pending) {
      console.warn(
        `WsRpcAdapter[${this.clientId}]: Received UI response for unknown request: ${response.id}`,
      );
      return;
    }

    if (pending.timeoutId) {
      clearTimeout(pending.timeoutId);
    }
    this.pendingRequests.delete(response.id);

    console.log(
      `WsRpcAdapter[${this.clientId}]: UI request ${response.id} (${pending.method}) resolved`,
    );

    pending.resolve(response);
  }

  createContext(): ExtensionUIContext {
    const createDialogPromise = <T>(
      request: Record<string, unknown>,
      defaultValue: T,
      parseResponse: (response: RpcExtensionUIResponse) => T,
    ): Promise<T> => {
      const id = crypto.randomUUID();

      return new Promise((resolve, reject) => {
        let timeoutId: ReturnType<typeof setTimeout> | undefined;

        const cleanup = () => {
          if (timeoutId) clearTimeout(timeoutId);
          this.pendingRequests.delete(id);
        };

        timeoutId = setTimeout(() => {
          console.log(
            `WsRpcAdapter[${this.clientId}]: UI request ${id} (${request.method}) timed out`,
          );
          cleanup();
          resolve(defaultValue);
        }, this.config.uiRequestTimeout);

        this.pendingRequests.set(id, {
          resolve: (value: RpcExtensionUIResponse) => {
            cleanup();
            resolve(parseResponse(value));
          },
          reject,
          timeoutId,
          method: request.method as string,
        });

        this.sendRequest({
          type: "extension_ui_request",
          id,
          ...request,
        } as RpcExtensionUIRequest);
      });
    };

    const setEditorText = (text: string) => {
      this.sendRequest({
        type: "extension_ui_request",
        id: crypto.randomUUID(),
        method: "set_editor_text",
        text,
      } as RpcExtensionUIRequest);
    };

    return {
      select: (
        title: string,
        options: string[],
        opts?: { timeout?: number; signal?: AbortSignal },
      ) =>
        createDialogPromise(
          { method: "select", title, options, timeout: opts?.timeout },
          undefined as string | undefined,
          r =>
            "cancelled" in r && r.cancelled
              ? undefined
              : "value" in r
                ? r.value
                : undefined,
        ),

      confirm: (
        title: string,
        message: string,
        opts?: { timeout?: number; signal?: AbortSignal },
      ) =>
        createDialogPromise(
          { method: "confirm", title, message, timeout: opts?.timeout },
          false,
          r =>
            "cancelled" in r && r.cancelled
              ? false
              : "confirmed" in r
                ? r.confirmed
                : false,
        ),

      input: (
        title: string,
        placeholder?: string,
        opts?: { timeout?: number; signal?: AbortSignal },
      ) =>
        createDialogPromise(
          { method: "input", title, placeholder, timeout: opts?.timeout },
          undefined as string | undefined,
          r =>
            "cancelled" in r && r.cancelled
              ? undefined
              : "value" in r
                ? r.value
                : undefined,
        ),

      editor: (title: string, prefill?: string) =>
        createDialogPromise(
          { method: "editor", title, prefill },
          undefined as string | undefined,
          r =>
            "cancelled" in r && r.cancelled
              ? undefined
              : "value" in r
                ? r.value
                : undefined,
        ),

      notify: (message: string, notifyType?: "info" | "warning" | "error") => {
        this.sendRequest({
          type: "extension_ui_request",
          id: crypto.randomUUID(),
          method: "notify",
          message,
          notifyType,
        } as RpcExtensionUIRequest);
      },

      setStatus: (key: string, statusText: string | undefined) => {
        this.sendRequest({
          type: "extension_ui_request",
          id: crypto.randomUUID(),
          method: "setStatus",
          statusKey: key,
          statusText,
        } as RpcExtensionUIRequest);
      },

      setWidget: (key, content, options) => {
        if (typeof content === "function") {
          return;
        }

        this.sendRequest({
          type: "extension_ui_request",
          id: crypto.randomUUID(),
          method: "setWidget",
          widgetKey: key,
          widgetLines: content,
          widgetPlacement: options?.placement,
        } as RpcExtensionUIRequest);
      },

      setTitle: (title: string) => {
        this.sendRequest({
          type: "extension_ui_request",
          id: crypto.randomUUID(),
          method: "setTitle",
          title,
        } as RpcExtensionUIRequest);
      },

      setEditorText,

      getEditorText: () => "", // Synchronous - not supported
      onTerminalInput: () => () => {}, // Not supported
      setWorkingMessage: () => {}, // Not supported
      setHiddenThinkingLabel: () => {}, // Not supported
      setFooter: () => {}, // Not supported
      setHeader: () => {}, // Not supported
      custom: async <T>() => undefined as T, // Not supported
      pasteToEditor: (text: string) => {
        setEditorText(text);
      },
      setEditorComponent: () => {}, // Not supported
      theme: {} as ExtensionUIContext["theme"], // Not available
      getAllThemes: () => [],
      getTheme: () => undefined,
      setTheme: () => ({ success: false, error: "Not supported" }),
      getToolsExpanded: () => false,
      setToolsExpanded: () => {},
      setWorkingVisible: () => {},
      setWorkingIndicator: () => {},
      addAutocompleteProvider: () => {},
      getEditorComponent: () => undefined,
    };
  }

  dispose(): void {
    for (const [id, pending] of this.pendingRequests) {
      if (pending.timeoutId) {
        clearTimeout(pending.timeoutId);
      }
      console.log(
        `WsRpcAdapter[${this.clientId}]: Resolving UI request ${id} (${pending.method}) on disconnect`,
      );
      pending.resolve({
        type: "extension_ui_response",
        id,
        cancelled: true,
      } as RpcExtensionUIResponse);
    }
    this.pendingRequests.clear();
  }

  private sendRequest(request: RpcExtensionUIRequest): void {
    console.log(
      `WsRpcAdapter[${this.clientId}]: Sending UI request ${request.id} (${request.method})`,
    );
    this.send({
      type: "extension_ui_request",
      payload: request,
    });
  }
}

/* ============================================================================
 * Session stats pusher
 * ========================================================================== */

class SessionStatsPusher {
  private inFlight = false;
  private pendingPath: string | null | undefined = undefined;
  private disposed = false;

  constructor(
    private readonly buildStats: (
      targetPath?: string | null,
    ) => Promise<RpcSessionStats>,
    private readonly send: (payload: RpcSessionStatsEvent) => void,
  ) {}

  queue(sessionPath: string | null): void {
    this.pendingPath = sessionPath;
    if (this.inFlight || this.disposed) return;
    void this.flush();
  }

  dispose(): void {
    this.disposed = true;
    this.pendingPath = undefined;
  }

  private async flush(): Promise<void> {
    const sessionPath = this.pendingPath;
    if (sessionPath === undefined || this.disposed) return;

    this.pendingPath = undefined;
    this.inFlight = true;

    try {
      const stats = await this.buildStats(sessionPath);
      if (this.disposed) return;

      this.send({
        type: "session_stats",
        sessionPath: sessionPath ?? undefined,
        stats,
      });
    } catch {
      // Ignore transient push failures. Explicit stats RPC reads still work.
    } finally {
      this.inFlight = false;
      if (this.pendingPath !== undefined && !this.disposed) {
        void this.flush();
      }
    }
  }
}

/* ============================================================================
 * WS-RPC adapter
 * ========================================================================== */

export class WsRpcAdapter {
  private client: WsClient;
  private ws: WebSocket;
  private context: WsRpcAdapterContext;
  private config: BridgeConfig;
  private eventBus: BridgeEventBus;
  private emitEvent: (event: BridgeEvent) => void;

  private readonly sessionRuntime: SessionRuntime;
  private readonly transcriptProjector = new TranscriptProjector();
  private readonly uiBridge: ExtensionUIBridge;
  private readonly sessionStatsPusher: SessionStatsPusher;
  private readonly detachedSessionRegistry: DetachedSessionRegistry;
  private pendingTranscriptDeltaBatch: PendingTranscriptDeltaBatch | null =
    null;

  // Detached-session registry subscription.
  private unsubscribeRegistryEvents: (() => void) | undefined;

  // Track if adapter is disposed.
  private disposed = false;

  private workspaceEntriesCache: {
    cwd: string;
    entries: RpcWorkspaceEntry[];
  } | null = null;

  constructor(
    client: WsClient,
    ws: WebSocket,
    context: WsRpcAdapterContext,
    config: BridgeConfig,
    eventBus: BridgeEventBus,
    emitEvent: (event: BridgeEvent) => void,
    sessionRegistry?: DetachedSessionRegistry,
  ) {
    this.client = client;
    this.ws = ws;
    this.context = context;
    this.config = config;
    this.eventBus = eventBus;
    this.emitEvent = emitEvent;
    this.detachedSessionRegistry =
      sessionRegistry ?? new DetachedSessionRegistry(context.state.cwd);
    this.uiBridge = new ExtensionUIBridge(client.id, config, message => {
      this.sendResponse(message);
    });
    this.sessionRuntime = new SessionRuntime(
      context,
      client.id,
      this.detachedSessionRegistry,
      () => this.uiBridge.createContext(),
      event => {
        this.handleSelectedSessionEvent(event);
      },
    );
    this.sessionStatsPusher = new SessionStatsPusher(
      sessionPath => this.buildSessionStats(sessionPath),
      payload => {
        this.sendEvent(payload);
      },
    );

    this.setupWebSocket();
    this.subscribeToEvents();
    this.subscribeToDetachedSessionEvents(this.detachedSessionRegistry);
    this.sendInitialTranscriptSnapshot();
    this.sessionStatsPusher.queue(
      this.sessionRuntime.currentTranscriptSessionPath(),
    );
  }

  /* ------------------------------------------------------------------------
   * WebSocket lifecycle and live event fan-out
   * ---------------------------------------------------------------------- */

  private setupWebSocket(): void {
    this.ws.on("message", data => {
      if (this.disposed) return;
      this.handleMessage(data.toString());
    });

    this.ws.on("close", () => {
      this.dispose();
    });

    this.ws.on("error", err => {
      console.error(`WsRpcAdapter[${this.client.id}]: WebSocket error:`, err);
      this.emitEvent({
        type: "command_error",
        client: this.client,
        commandType: "websocket",
        error: err.message,
      });
    });
  }

  /**
   * Subscribe to Pi events and route them directly to the active browser view.
   */
  subscribeToEvents(): void {
    void this.eventBus;

    this.context.events.subscribe(event => {
      switch (event.type) {
        case "agent_start":
          this.sendEvent(
            toRpcAgentStartEvent(
              this.context.state.sessionManager.getSessionFile(),
            ),
          );
          return;

        case "agent_end": {
          const liveSessionPath =
            this.context.state.sessionManager.getSessionFile();
          this.sendEvent(toRpcAgentEndEvent(event, liveSessionPath));
          if (!this.sessionRuntime.shouldHandleLiveSessionEvents()) return;
          this.sessionStatsPusher.queue(
            this.sessionRuntime.currentTranscriptSessionPath(),
          );
          return;
        }

        case "session_compact":
          if (!this.sessionRuntime.shouldHandleLiveSessionEvents()) return;
          this.sendTranscriptSnapshot(
            this.sessionRuntime.buildCurrentTranscriptPage(),
          );
          this.sessionStatsPusher.queue(
            this.sessionRuntime.currentTranscriptSessionPath(),
          );
          return;

        case "message_start":
        case "message_update":
        case "message_end":
          if (!this.sessionRuntime.shouldHandleLiveSessionEvents()) return;
          this.handleTranscriptLifecycleEvent(
            event.type,
            event,
            this.sessionRuntime.currentTranscriptSessionPath(),
          );
          return;

        case "model_select":
          if (!this.sessionRuntime.shouldHandleLiveSessionEvents()) return;
          this.sendEvent(toRpcModelSelectEvent(event));
          this.sessionStatsPusher.queue(
            this.sessionRuntime.currentTranscriptSessionPath(),
          );
          return;
      }
    });
  }

  private subscribeToDetachedSessionEvents(
    sessionRegistry: DetachedSessionRegistry | null,
  ): void {
    if (!sessionRegistry) return;

    this.unsubscribeRegistryEvents = sessionRegistry.subscribe(
      ({ sessionPath, event }) => {
        if (this.disposed) return;

        switch (event.type) {
          case "agent_start":
            this.sendEvent(toRpcAgentStartEvent(sessionPath));
            return;
          case "agent_end":
            this.sendEvent(toRpcAgentEndEvent(event, sessionPath));
            if (
              this.sessionRuntime.currentDetachedSessionPath() === sessionPath
            ) {
              this.sessionStatsPusher.queue(sessionPath);
            }
            return;
          default:
            return;
        }
      },
    );
  }

  private sendEvent(payload: RpcBridgeEvent): void {
    if (payload.type === "transcript_delta") {
      this.queueTranscriptDelta(payload);
      return;
    }

    this.flushPendingTranscriptDeltaBatch();
    this.sendEventNow(payload);
  }

  private sendEventNow(payload: RpcBridgeEvent): void {
    this.sendResponse({
      type: "event",
      payload,
    });
  }

  private queueTranscriptDelta(payload: RpcTranscriptDeltaEvent): void {
    const pending = this.pendingTranscriptDeltaBatch;
    if (pending && this.canBatchTranscriptDelta(pending.payload, payload)) {
      pending.payload = {
        ...pending.payload,
        delta: `${pending.payload.delta}${payload.delta}`,
        toolCallId: pending.payload.toolCallId ?? payload.toolCallId,
        toolName: pending.payload.toolName ?? payload.toolName,
      };
      if (this.shouldFlushTranscriptDeltaBatch(pending.payload)) {
        this.flushPendingTranscriptDeltaBatch();
      }
      return;
    }

    this.flushPendingTranscriptDeltaBatch();
    const timeoutId = setTimeout(() => {
      const current = this.pendingTranscriptDeltaBatch;
      if (!current || current.timeoutId !== timeoutId) return;
      this.pendingTranscriptDeltaBatch = null;
      this.sendEventNow(current.payload);
    }, TRANSCRIPT_DELTA_BATCH_MS);
    this.pendingTranscriptDeltaBatch = { payload, timeoutId };
    if (this.shouldFlushTranscriptDeltaBatch(payload)) {
      this.flushPendingTranscriptDeltaBatch();
    }
  }

  private shouldFlushTranscriptDeltaBatch(
    payload: RpcTranscriptDeltaEvent,
  ): boolean {
    return (
      payload.delta.length >= TRANSCRIPT_DELTA_MAX_CHARS ||
      payload.delta.includes("\n") ||
      /[.!?。！？]\s*$/.test(payload.delta)
    );
  }

  private flushPendingTranscriptDeltaBatch(): void {
    const pending = this.pendingTranscriptDeltaBatch;
    if (!pending) return;
    this.pendingTranscriptDeltaBatch = null;
    clearTimeout(pending.timeoutId);
    this.sendEventNow(pending.payload);
  }

  private canBatchTranscriptDelta(
    left: RpcTranscriptDeltaEvent,
    right: RpcTranscriptDeltaEvent,
  ): boolean {
    return (
      (left.sessionPath ?? null) === (right.sessionPath ?? null) &&
      left.transcriptKey === right.transcriptKey &&
      left.role === right.role &&
      left.blockType === right.blockType &&
      left.contentIndex === right.contentIndex &&
      (left.messageId ?? null) === (right.messageId ?? null)
    );
  }

  /* ------------------------------------------------------------------------
   * Transcript synchronization
   * ---------------------------------------------------------------------- */

  private sendTranscriptSnapshot(page: RpcTranscriptPage): void {
    this.sendEvent(this.transcriptProjector.buildSnapshotEvent(page));
  }

  private sendInitialTranscriptSnapshot(): void {
    this.sendTranscriptSnapshot(
      this.sessionRuntime.buildCurrentTranscriptPage(),
    );
  }

  private handleTranscriptLifecycleEvent(
    eventType: "message_start" | "message_update" | "message_end",
    event: object,
    sessionPath: string | null,
  ): void {
    if (eventType === "message_update") {
      const deltaPayload = this.transcriptProjector.projectDeltaEvent(
        event,
        sessionPath,
      );
      if (deltaPayload) {
        this.sendEvent(deltaPayload);
        return;
      }
      return;
    }

    const payload = this.transcriptProjector.projectLifecycleEvent(
      eventType,
      event,
      sessionPath,
    );
    if (!payload) return;

    let treeEntries =
      this.sessionRuntime.buildTreeEntriesForSessionPath(sessionPath);
    if (
      !treeEntries.some(
        entry =>
          entry.id === payload.message.id ||
          entry.id === payload.message.transcriptKey,
      )
    ) {
      treeEntries = projectTreeEntriesWithTranscriptMessage(
        treeEntries,
        payload.message,
      );
    }

    payload.treeEntries = treeEntries;
    this.sendEvent(payload);
    if (eventType !== "message_start") {
      this.sessionStatsPusher.queue(sessionPath);
    }
  }

  /* ------------------------------------------------------------------------
   * Detached session event handling
   * ---------------------------------------------------------------------- */

  private sendSelectedSessionQueueUpdate(): void {
    const sessionPath = this.sessionRuntime.currentTranscriptSessionPath();
    const detachedSession = this.sessionRuntime.getDetachedSession();
    if (!detachedSession) return;

    this.sendEvent(buildQueueUpdateEvent(detachedSession, sessionPath));
  }

  private handleSelectedSessionEvent(event: AgentSessionEvent): void {
    const sessionPath = this.sessionRuntime.currentTranscriptSessionPath();
    const detachedSession = this.sessionRuntime.getDetachedSession();
    const eventType: string = event.type;

    if (eventType === "session_compact") {
      this.sendTranscriptSnapshot(
        this.sessionRuntime.buildCurrentTranscriptPage(),
      );
      this.sessionStatsPusher.queue(sessionPath);
      return;
    }

    if (eventType === "model_select") {
      if (!isPiModelSelectEventLike(event)) return;
      this.sendEvent(toRpcModelSelectEvent(event));
      this.sessionStatsPusher.queue(sessionPath);
      return;
    }

    switch (event.type) {
      case "message_start":
      case "message_update":
      case "message_end": {
        this.handleTranscriptLifecycleEvent(event.type, event, sessionPath);
        return;
      }
      case "agent_start":
        return;
      case "agent_end":
        if (detachedSession) {
          // AgentSession persists message_end entries before agent_end fires, so
          // this snapshot includes the real session entry IDs for the finished turn.
          this.sendTranscriptSnapshot(
            buildTranscriptPage(
              flattenMessagesForTranscript(
                detachedSession.sessionManager.getBranch(),
              ),
              detachedSession.sessionFile ?? null,
            ),
          );
        }
        this.sessionStatsPusher.queue(sessionPath);
        return;
      case "queue_update":
        if (detachedSession) {
          this.sendEvent(buildQueueUpdateEvent(detachedSession, sessionPath));
        }
        return;
      case "compaction_start":
        this.sendEvent(toRpcCompactionStartEvent(event));
        return;
      case "compaction_end":
        this.sendTranscriptSnapshot(
          this.sessionRuntime.buildCurrentTranscriptPage(),
        );
        this.sendEvent(toRpcCompactionEndEvent(event));
        this.sessionStatsPusher.queue(sessionPath);
        return;
      default:
        return;
    }
  }

  /* ------------------------------------------------------------------------
   * Session stats
   * ---------------------------------------------------------------------- */

  private toRpcSessionStats(stats: {
    contextUsage?: {
      tokens: number | null;
      contextWindow: number;
      percent: number | null;
    };
    totalMessages: number;
    cost: number;
    tokens: {
      input: number;
      output: number;
      cacheRead: number;
      cacheWrite: number;
    };
  }): RpcSessionStats {
    return {
      tokens: stats.contextUsage?.tokens ?? null,
      contextWindow: stats.contextUsage?.contextWindow ?? 0,
      percent: stats.contextUsage?.percent ?? null,
      messageCount: stats.totalMessages,
      cost: stats.cost,
      inputTokens: stats.tokens.input,
      outputTokens: stats.tokens.output,
      cacheReadTokens: stats.tokens.cacheRead,
      cacheWriteTokens: stats.tokens.cacheWrite,
    };
  }

  private async lookupContextWindow(
    provider: string | undefined,
    modelId: string | undefined,
  ): Promise<number> {
    if (!provider || !modelId) return 0;

    try {
      const models = this.context.state.getAvailableModels();
      const matched = models.find(
        model => model.provider === provider && model.id === modelId,
      );
      return (
        (matched as { contextWindow?: number } | undefined)?.contextWindow ?? 0
      );
    } catch {
      return 0;
    }
  }

  private async buildSessionStats(
    targetPath?: string | null,
  ): Promise<RpcSessionStats> {
    const selectedSessionPath =
      this.sessionRuntime.currentDetachedSessionPath();
    const detachedSession = this.sessionRuntime.getDetachedSession();
    const liveSessionPath = this.context.state.sessionManager.getSessionFile();
    const resolvedTargetPath =
      targetPath === undefined
        ? (selectedSessionPath ?? liveSessionPath ?? null)
        : targetPath;
    const cachedSessionManager = resolvedTargetPath
      ? this.sessionRuntime.getCachedSessionManager(resolvedTargetPath)
      : null;

    if (
      detachedSession &&
      (!resolvedTargetPath ||
        resolvedTargetPath === detachedSession.sessionFile ||
        resolvedTargetPath === selectedSessionPath)
    ) {
      return this.toRpcSessionStats(detachedSession.getSessionStats());
    }

    if (resolvedTargetPath && resolvedTargetPath !== liveSessionPath) {
      try {
        const storedSessionManager = cachedSessionManager
          ? cachedSessionManager
          : fs.existsSync(resolvedTargetPath)
            ? openSessionManager(resolvedTargetPath)
            : null;

        if (storedSessionManager) {
          const branch = storedSessionManager.getBranch();
          const summary = summarizeTokenUsage(
            branch,
            storedSessionManager.getEntries(),
          );

          let tokens: number | null = null;
          let contextWindow = 0;
          let percent: number | null = null;
          if (summary.lastAssistantUsage) {
            const usage = summary.lastAssistantUsage;
            tokens =
              (usage.input ?? 0) +
              (usage.cacheRead ?? 0) +
              (usage.cacheWrite ?? 0);
          }
          if (tokens != null) {
            contextWindow = await this.lookupContextWindow(
              summary.lastModel?.provider,
              summary.lastModel?.modelId,
            );
            if (contextWindow > 0) {
              percent = Math.round((tokens / contextWindow) * 100 * 10) / 10;
            }
          }

          return {
            tokens,
            contextWindow,
            percent,
            messageCount: summary.messageCount,
            cost: summary.totalCost,
            inputTokens: summary.inputTokens,
            outputTokens: summary.outputTokens,
            cacheReadTokens: summary.cacheReadTokens,
            cacheWriteTokens: summary.cacheWriteTokens,
          };
        }
      } catch {
        // Fall through to live session stats.
      }
    }

    const usage = this.context.state.getContextUsage();
    const branch = this.context.state.sessionManager.getBranch();
    const summary = summarizeTokenUsage(
      branch,
      this.context.state.sessionManager.getEntries(),
    );

    let tokens: number | null = usage?.tokens ?? null;
    let contextWindow = usage?.contextWindow ?? 0;
    let percent: number | null = usage?.percent ?? null;

    if (tokens == null && summary.lastAssistantUsage) {
      tokens =
        (summary.lastAssistantUsage.input ?? 0) +
        (summary.lastAssistantUsage.cacheRead ?? 0) +
        (summary.lastAssistantUsage.cacheWrite ?? 0);
    }
    if (contextWindow === 0 && tokens != null) {
      contextWindow = await this.lookupContextWindow(
        summary.lastModel?.provider,
        summary.lastModel?.modelId,
      );
    }
    if (percent == null && tokens != null && contextWindow > 0) {
      percent = Math.round((tokens / contextWindow) * 100 * 10) / 10;
    }

    return {
      tokens,
      contextWindow,
      percent,
      messageCount: summary.messageCount,
      cost: summary.totalCost,
      inputTokens: summary.inputTokens,
      outputTokens: summary.outputTokens,
      cacheReadTokens: summary.cacheReadTokens,
      cacheWriteTokens: summary.cacheWriteTokens,
    };
  }

  private buildSelectTreeEntryResponse(
    correlationId: string,
    sessionManager: SessionManager,
    sessionPath: string,
    targetEntryId: string,
  ): RpcResponse {
    const transcript = buildTranscriptPage(
      buildExactSelectionTranscriptMessages(
        sessionManager.getBranch(),
        targetEntryId,
      ),
      sessionPath,
    );
    this.transcriptProjector.syncPage(transcript);

    return {
      id: correlationId,
      type: "response",
      command: "select_tree_entry",
      success: true,
      data: {
        transcript,
        treeEntries: buildTreeEntriesFromSession(sessionManager),
        sessionId: sessionManager.getSessionId(),
        sessionName: sessionDisplayName(sessionManager, sessionPath),
        sessionPath,
        workspacePath: normalizeOptionalWorkspaceRoot(sessionManager.getCwd()),
        cancelled: false,
      },
    };
  }

  /* ------------------------------------------------------------------------
   * RPC command dispatch
   * ---------------------------------------------------------------------- */

  private handleMessage(data: string): void {
    let message: ClientMessage;
    try {
      message = JSON.parse(data) as ClientMessage;
    } catch (err) {
      this.sendResponse({
        type: "response",
        payload: {
          type: "response",
          command: "parse",
          success: false,
          error: `Failed to parse message: ${err instanceof Error ? err.message : String(err)}`,
        },
      });
      return;
    }

    if (message.type === "command") {
      void this.handleCommand(message.payload);
    } else if (message.type === "extension_ui_response") {
      this.handleUIResponse(message.payload);
    } else {
      this.sendResponse({
        type: "response",
        payload: {
          type: "response",
          command: "unknown",
          success: false,
          error: `Unknown message type`,
        },
      });
    }
  }

  /**
   * Handle RPC command dispatch
   */
  private async handleCommand(command: RpcCommand): Promise<void> {
    const correlationId = command.id ?? crypto.randomUUID();

    this.emitEvent({
      type: "command_received",
      client: this.client,
      commandType: command.type,
      correlationId,
    });

    try {
      const response = await this.dispatchCommand(command, correlationId);
      this.sendResponse({ type: "response", payload: response });

      if (
        response.success &&
        (command.type === "get_state" ||
          command.type === "switch_session" ||
          command.type === "new_session" ||
          command.type === "select_tree_entry")
      ) {
        this.sessionStatsPusher.queue(
          this.sessionRuntime.currentTranscriptSessionPath(),
        );
        this.sendSelectedSessionQueueUpdate();
      }
    } catch (err) {
      const error = err instanceof Error ? err.message : String(err);
      console.error(
        `WsRpcAdapter[${this.client.id}]: Command error (${command.type}):`,
        error,
      );

      this.emitEvent({
        type: "command_error",
        client: this.client,
        commandType: command.type,
        correlationId,
        error,
      });

      this.sendResponse({
        type: "response",
        payload: {
          id: correlationId,
          type: "response",
          command: command.type,
          success: false,
          error,
        },
      });
    }
  }

  /**
   * Dispatch command to Pi extension API
   */
  private async dispatchCommand(
    command: RpcCommand,
    correlationId: string,
  ): Promise<RpcResponse> {
    switch (command.type) {
      /* ====================================================================
       * Prompting and turn control
       * Shared state: SessionRuntime detached session, else live Pi runtime.
       * ================================================================== */

      case "prompt": {
        const images = normalizeRpcImages(command.images);

        // Auto-create a detached session when no session is selected.
        // Without this, the fallback calls this.context.actions.sendUserMessage() which
        // drives the live Pi runtime and causes a TUI switch away from
        // the bridge's custom terminal view.
        let autoCreated: SessionSummary | null = null;
        if (!this.sessionRuntime.hasDetachedSelection()) {
          autoCreated = await this.sessionRuntime.createDetachedSession();
        }

        const session = await this.sessionRuntime.ensureDetachedSession();
        const promptOptions = session.isStreaming
          ? {
              source: "rpc" as const,
              images,
              streamingBehavior: command.streamingBehavior ?? "steer",
            }
          : { source: "rpc" as const, images };

        setTimeout(() => {
          void session.prompt(command.message, promptOptions).catch(error => {
            const message =
              error instanceof Error ? error.message : String(error);
            console.error(
              `WsRpcAdapter[${this.client.id}]: Detached prompt failed:`,
              message,
            );
            this.emitEvent({
              type: "command_error",
              client: this.client,
              commandType: "prompt",
              correlationId,
              error: message,
            });
          });
        }, 0);

        if (autoCreated) {
          this.transcriptProjector.syncPage(autoCreated.transcript);
          return {
            id: correlationId,
            type: "response",
            command: "new_session",
            success: true,
            data: {
              transcript: autoCreated.transcript,
              treeEntries: autoCreated.treeEntries,
              sessionId: autoCreated.sessionId,
              sessionName: autoCreated.sessionName,
              sessionPath: autoCreated.sessionPath,
              cancelled: false,
            },
          };
        }

        return {
          id: correlationId,
          type: "response",
          command: "prompt",
          success: true,
        };
      }

      case "steer": {
        const images = normalizeRpcImages(command.images);
        if (this.sessionRuntime.hasDetachedSelection()) {
          const session = await this.sessionRuntime.ensureDetachedSession();
          void session.steer(command.message, images).catch(error => {
            console.error(
              `WsRpcAdapter[${this.client.id}]: Detached steer failed:`,
              error,
            );
          });
        } else {
          this.context.actions.sendUserMessage(
            buildUserMessageContent(command.message, images),
            {
              deliverAs: "steer",
            },
          );
        }
        return {
          id: correlationId,
          type: "response",
          command: "steer",
          success: true,
        };
      }

      case "follow_up": {
        const images = normalizeRpcImages(command.images);
        if (this.sessionRuntime.hasDetachedSelection()) {
          const session = await this.sessionRuntime.ensureDetachedSession();
          void session.followUp(command.message, images).catch(error => {
            console.error(
              `WsRpcAdapter[${this.client.id}]: Detached follow_up failed:`,
              error,
            );
          });
        } else {
          this.context.actions.sendUserMessage(
            buildUserMessageContent(command.message, images),
            {
              deliverAs: "followUp",
            },
          );
        }
        return {
          id: correlationId,
          type: "response",
          command: "follow_up",
          success: true,
        };
      }

      case "abort": {
        if (this.sessionRuntime.hasDetachedSelection()) {
          const session = await this.sessionRuntime.ensureDetachedSession();
          clearSteeringQueue(session);
          await session.abort();
        } else {
          this.context.actions.abort();
        }
        return {
          id: correlationId,
          type: "response",
          command: "abort",
          success: true,
        };
      }

      /* ====================================================================
       * Session state and model settings
       * Shared state: SessionRuntime plus live ctx and pi settings APIs.
       * ================================================================== */

      case "get_state": {
        return {
          id: correlationId,
          type: "response",
          command: "get_state",
          success: true,
          data: this.sessionRuntime.buildActiveState(),
        };
      }

      /* --------------------------------------------------------------------
       * Model selection and availability
       * ------------------------------------------------------------------ */

      case "set_model": {
        const detachedSession = this.sessionRuntime.getDetachedSession();
        const models = detachedSession
          ? detachedSession.modelRegistry.getAvailable()
          : this.context.state.getAvailableModels();
        const model = models.find(
          m => m.provider === command.provider && m.id === command.modelId,
        );
        if (!model) {
          return {
            id: correlationId,
            type: "response",
            command: "set_model",
            success: false,
            error: `Model not found: ${command.provider}/${command.modelId}`,
          };
        }
        if (this.sessionRuntime.hasDetachedSelection()) {
          const session = await this.sessionRuntime.ensureDetachedSession();
          await session.setModel(
            model as unknown as Parameters<typeof session.setModel>[0],
          );
        } else {
          await this.context.actions.setModel(model);
        }
        return {
          id: correlationId,
          type: "response",
          command: "set_model",
          success: true,
          data: model,
        };
      }

      case "get_available_models": {
        const detachedSession = this.sessionRuntime.getDetachedSession();
        const models = detachedSession
          ? detachedSession.modelRegistry.getAvailable()
          : this.context.state.getAvailableModels();
        return {
          id: correlationId,
          type: "response",
          command: "get_available_models",
          success: true,
          data: { models },
        };
      }

      case "cycle_model": {
        // Not directly supported via extension API
        return {
          id: correlationId,
          type: "response",
          command: "cycle_model",
          success: false,
          error: "cycle_model not supported via bridge",
        };
      }

      /* --------------------------------------------------------------------
       * Thinking level and queue controls
       * ------------------------------------------------------------------ */

      case "set_thinking_level": {
        if (this.sessionRuntime.hasDetachedSelection()) {
          const session = await this.sessionRuntime.ensureDetachedSession();
          session.setThinkingLevel(command.level);
        } else {
          this.context.actions.setThinkingLevel(command.level);
        }
        return {
          id: correlationId,
          type: "response",
          command: "set_thinking_level",
          success: true,
        };
      }

      case "cycle_thinking_level": {
        // Not directly supported via extension API
        return {
          id: correlationId,
          type: "response",
          command: "cycle_thinking_level",
          success: false,
          error: "cycle_thinking_level not supported via bridge",
        };
      }

      /* --------------------------------------------------------------------
       * Detached-session queue controls
       * ------------------------------------------------------------------ */

      case "set_steering_mode": {
        if (this.sessionRuntime.hasDetachedSelection()) {
          const session = await this.sessionRuntime.ensureDetachedSession();
          session.setSteeringMode(command.mode);
          return {
            id: correlationId,
            type: "response",
            command: "set_steering_mode",
            success: true,
          };
        }
        return {
          id: correlationId,
          type: "response",
          command: "set_steering_mode",
          success: false,
          error: "set_steering_mode not supported via bridge",
        };
      }

      case "set_follow_up_mode": {
        if (this.sessionRuntime.hasDetachedSelection()) {
          const session = await this.sessionRuntime.ensureDetachedSession();
          session.setFollowUpMode(command.mode);
          return {
            id: correlationId,
            type: "response",
            command: "set_follow_up_mode",
            success: true,
          };
        }
        return {
          id: correlationId,
          type: "response",
          command: "set_follow_up_mode",
          success: false,
          error: "set_follow_up_mode not supported via bridge",
        };
      }

      case "dequeue_follow_up_message": {
        if (!this.sessionRuntime.hasDetachedSelection()) {
          return {
            id: correlationId,
            type: "response",
            command: "dequeue_follow_up_message",
            success: false,
            error:
              "Queued follow-up editing requires an active detached session",
          };
        }

        const session = await this.sessionRuntime.ensureDetachedSession();
        try {
          const removed = dequeueFollowUpMessage(session, command.index);
          return {
            id: correlationId,
            type: "response",
            command: "dequeue_follow_up_message",
            success: true,
            data: { removed },
          };
        } catch (error) {
          return {
            id: correlationId,
            type: "response",
            command: "dequeue_follow_up_message",
            success: false,
            error: error instanceof Error ? error.message : String(error),
          };
        }
      }

      /* --------------------------------------------------------------------
       * Compaction and retry behavior
       * ------------------------------------------------------------------ */

      case "compact": {
        if (this.sessionRuntime.hasDetachedSelection()) {
          try {
            const session = await this.sessionRuntime.ensureDetachedSession();
            const result = await session.compact(command.customInstructions);
            return {
              id: correlationId,
              type: "response",
              command: "compact",
              success: true,
              data: result,
            };
          } catch (error) {
            return {
              id: correlationId,
              type: "response",
              command: "compact",
              success: false,
              error: error instanceof Error ? error.message : String(error),
            };
          }
        }

        return {
          id: correlationId,
          type: "response",
          command: "compact",
          success: false,
          error: "Compaction requires an active session",
        };
      }

      case "set_auto_compaction": {
        if (this.sessionRuntime.hasDetachedSelection()) {
          const session = await this.sessionRuntime.ensureDetachedSession();
          session.setAutoCompactionEnabled(command.enabled);
          return {
            id: correlationId,
            type: "response",
            command: "set_auto_compaction",
            success: true,
          };
        }
        return {
          id: correlationId,
          type: "response",
          command: "set_auto_compaction",
          success: false,
          error: "set_auto_compaction not supported via bridge",
        };
      }

      /* --------------------------------------------------------------------
       * Retry controls
       * ------------------------------------------------------------------ */

      case "set_auto_retry":
      case "abort_retry": {
        if (this.sessionRuntime.hasDetachedSelection()) {
          const session = await this.sessionRuntime.ensureDetachedSession();
          if (command.type === "set_auto_retry") {
            session.setAutoRetryEnabled(command.enabled);
          } else {
            session.abortRetry();
          }
          return {
            id: correlationId,
            type: "response",
            command: command.type,
            success: true,
          };
        }
        return {
          id: correlationId,
          type: "response",
          command: command.type,
          success: false,
          error: `${command.type} not supported via bridge`,
        };
      }

      /* --------------------------------------------------------------------
       * Explicitly unsupported execution commands
       * ------------------------------------------------------------------ */

      case "bash":
      case "abort_bash": {
        return {
          id: correlationId,
          type: "response",
          command: command.type,
          success: false,
          error: `${command.type} not supported via bridge for security`,
        };
      }

      /* ====================================================================
       * Session lifecycle and tree navigation
       * Shared state: SessionRuntime selection plus stored session files.
       * ================================================================== */

      case "new_session": {
        const workspacePath = normalizeOptionalWorkspaceRoot(
          command.workspacePath,
        );
        if (workspacePath) {
          try {
            if (!fs.statSync(workspacePath).isDirectory()) {
              return {
                id: correlationId,
                type: "response",
                command: "new_session",
                success: false,
                error: "Workspace path is not a directory",
              };
            }
          } catch {
            return {
              id: correlationId,
              type: "response",
              command: "new_session",
              success: false,
              error: "Workspace path not found",
            };
          }

          ensureRegisteredWorkspace(workspacePath);
        }

        const created = await this.sessionRuntime.createDetachedSession({
          workspacePath,
          transcriptLimit: command.limit,
        });
        this.transcriptProjector.syncPage(created.transcript);
        return {
          id: correlationId,
          type: "response",
          command: "new_session",
          success: true,
          data: {
            transcript: created.transcript,
            treeEntries: created.treeEntries,
            sessionId: created.sessionId,
            sessionName: created.sessionName,
            sessionPath: created.sessionPath,
            workspacePath: created.workspacePath,
            cancelled: false,
          },
        };
      }

      case "register_workspace": {
        const selectedWorkspacePath =
          normalizeOptionalWorkspaceRoot(command.workspacePath) ||
          pickWorkspaceDirectoryFromNativeDialog();
        if (!selectedWorkspacePath) {
          return {
            id: correlationId,
            type: "response",
            command: "register_workspace",
            success: true,
            data: {
              workspaceId: "",
              workspaceName: "",
              workspacePath: "",
              created: false,
              cancelled: true,
            },
          };
        }

        try {
          if (!fs.statSync(selectedWorkspacePath).isDirectory()) {
            return {
              id: correlationId,
              type: "response",
              command: "register_workspace",
              success: false,
              error: "Workspace path is not a directory",
            };
          }
        } catch {
          return {
            id: correlationId,
            type: "response",
            command: "register_workspace",
            success: false,
            error: "Workspace path not found",
          };
        }

        const registered = ensureRegisteredWorkspace(selectedWorkspacePath);
        return {
          id: correlationId,
          type: "response",
          command: "register_workspace",
          success: true,
          data: {
            ...registered.metadata,
            created: registered.created,
            cancelled: false,
          },
        };
      }

      case "switch_session": {
        try {
          const sessionPath = command.sessionPath as string;
          const cachedSessionManager = sessionPath
            ? this.sessionRuntime.getCachedSessionManager(sessionPath)
            : null;
          if (
            !sessionPath ||
            (!cachedSessionManager && !fs.existsSync(sessionPath))
          ) {
            return {
              id: correlationId,
              type: "response",
              command: "switch_session",
              success: false,
              error: "Session file not found",
            };
          }

          const selected = await this.sessionRuntime.switchToStoredSession(
            sessionPath,
            command.limit,
          );
          this.transcriptProjector.syncPage(selected.transcript);
          return {
            id: correlationId,
            type: "response",
            command: "switch_session",
            success: true,
            data: {
              transcript: selected.transcript,
              treeEntries: selected.treeEntries,
              sessionId: selected.sessionId,
              sessionName: selected.sessionName,
              sessionPath: selected.sessionPath,
              workspacePath: selected.workspacePath,
              cancelled: false,
            },
          };
        } catch (err) {
          return {
            id: correlationId,
            type: "response",
            command: "switch_session",
            success: false,
            error: err instanceof Error ? err.message : String(err),
          };
        }
      }

      case "set_session_name": {
        const name = command.name.trim();
        if (!name) {
          return {
            id: correlationId,
            type: "response",
            command: "set_session_name",
            success: false,
            error: "Session name cannot be empty",
          };
        }
        this.context.actions.setSessionName(name);
        return {
          id: correlationId,
          type: "response",
          command: "set_session_name",
          success: true,
        };
      }

      case "delete_session": {
        const sessionPath = command.sessionPath as string;
        if (!sessionPath || !fs.existsSync(sessionPath)) {
          return {
            id: correlationId,
            type: "response",
            command: "delete_session",
            success: false,
            error: "Session file not found",
          };
        }

        if (this.sessionRuntime.isSessionRunning(sessionPath)) {
          return {
            id: correlationId,
            type: "response",
            command: "delete_session",
            success: false,
            error: "Cannot delete a running session",
          };
        }

        if (this.sessionRuntime.currentDetachedSessionPath() === sessionPath) {
          this.sessionRuntime.clearSelection();
          this.sendTranscriptSnapshot({
            sessionPath: undefined,
            messages: [],
            hasOlder: false,
            hasNewer: false,
          });
          this.sessionStatsPusher.queue(null);
        }

        this.detachedSessionRegistry.removeSession(sessionPath);
        fs.unlinkSync(sessionPath);

        return {
          id: correlationId,
          type: "response",
          command: "delete_session",
          success: true,
        };
      }

      case "fork": {
        const currentSessionFile =
          this.sessionRuntime.currentTranscriptSessionPath() ??
          this.context.state.sessionManager.getSessionFile();
        if (!currentSessionFile || !fs.existsSync(currentSessionFile)) {
          return {
            id: correlationId,
            type: "response",
            command: "fork",
            success: false,
            error: "No session file available to fork from",
          };
        }

        const sourceSm = openSessionManager(currentSessionFile);
        const newSessionPath = sourceSm.createBranchedSession(command.entryId);
        if (!newSessionPath) {
          return {
            id: correlationId,
            type: "response",
            command: "fork",
            success: false,
            error: "Failed to create forked session",
          };
        }

        const selected =
          await this.sessionRuntime.switchToStoredSession(newSessionPath);
        const forkedSm = selected.sessionManager;
        const entry = forkedSm.getEntry(command.entryId);
        const text =
          entry && "message" in entry
            ? ((entry.message as { content?: string }).content ?? "")
            : "";

        return {
          id: correlationId,
          type: "response",
          command: "fork",
          success: true,
          data: { text, cancelled: false },
        };
      }

      case "get_fork_messages": {
        // Not directly available via extension API
        return {
          id: correlationId,
          type: "response",
          command: "get_fork_messages",
          success: false,
          error: "get_fork_messages not supported via bridge",
        };
      }

      case "get_last_assistant_text": {
        // Not directly available via extension API
        return {
          id: correlationId,
          type: "response",
          command: "get_last_assistant_text",
          success: false,
          error: "get_last_assistant_text not supported via bridge",
        };
      }

      case "export_html": {
        return {
          id: correlationId,
          type: "response",
          command: "export_html",
          success: false,
          error: "export_html not supported via bridge",
        };
      }

      /* --------------------------------------------------------------------
       * Transcript reads and slash commands
       * ------------------------------------------------------------------ */

      case "get_messages": {
        const direction = command.direction === "older" ? "older" : "latest";
        const page = this.sessionRuntime.buildCurrentTranscriptPage({
          direction,
          cursor: command.cursor,
          limit: command.limit,
        });
        if (direction === "latest") {
          this.transcriptProjector.syncPage(page);
        }
        return {
          id: correlationId,
          type: "response",
          command: "get_messages",
          success: true,
          data: { ...page, direction },
        };
      }

      /* --------------------------------------------------------------------
       * Slash command discovery
       * ------------------------------------------------------------------ */

      case "get_commands": {
        const commands = this.context.actions.getCommands();
        const rpcCommands: RpcSlashCommand[] = commands.map(cmd => ({
          name: cmd.name,
          description: cmd.description,
          source: "extension" as const,
        }));
        return {
          id: correlationId,
          type: "response",
          command: "get_commands",
          success: true,
          data: { commands: rpcCommands },
        };
      }

      /* --------------------------------------------------------------------
       * Tree selection and navigation
       * ------------------------------------------------------------------ */

      case "select_tree_entry": {
        let session: AgentSession;

        try {
          session = this.sessionRuntime.hasDetachedSelection()
            ? await this.sessionRuntime.ensureDetachedSession({
                skipInitialSnapshot: true,
              })
            : await this.sessionRuntime.ensureDetachedSessionFromLive({
                skipInitialSnapshot: true,
              });
        } catch (error) {
          return {
            id: correlationId,
            type: "response" as const,
            command: "select_tree_entry" as const,
            success: false as const,
            error: error instanceof Error ? error.message : String(error),
          };
        }

        const targetEntry = session.sessionManager.getEntry(command.entryId);
        if (!targetEntry) {
          return {
            id: correlationId,
            type: "response" as const,
            command: "select_tree_entry" as const,
            success: false as const,
            error: "Tree entry not found",
          };
        }

        session.sessionManager.branch(command.entryId);
        const sessionPath =
          session.sessionFile ??
          session.sessionManager.getSessionFile() ??
          this.sessionRuntime.currentDetachedSessionPath();
        if (!sessionPath) {
          return {
            id: correlationId,
            type: "response" as const,
            command: "select_tree_entry" as const,
            success: false as const,
            error: "No session file available",
          };
        }

        return this.buildSelectTreeEntryResponse(
          correlationId,
          session.sessionManager,
          sessionPath,
          command.entryId,
        );
      }

      /* --------------------------------------------------------------------
       * Tree navigation actions
       * ------------------------------------------------------------------ */

      case "navigate_tree": {
        let session: AgentSession;

        try {
          session = this.sessionRuntime.hasDetachedSelection()
            ? await this.sessionRuntime.ensureDetachedSession({
                skipInitialSnapshot: true,
              })
            : await this.sessionRuntime.ensureDetachedSessionFromLive({
                skipInitialSnapshot: true,
              });
        } catch (error) {
          return {
            id: correlationId,
            type: "response" as const,
            command: "navigate_tree" as const,
            success: false as const,
            error: error instanceof Error ? error.message : String(error),
          };
        }

        const result = await session.navigateTree(command.entryId, {
          summarize: command.summarize,
          customInstructions: command.customInstructions,
          replaceInstructions: command.replaceInstructions,
          label: command.label,
        });

        this.sendTranscriptSnapshot(
          buildTranscriptPage(
            flattenMessagesForTranscript(session.sessionManager.getBranch()),
            session.sessionFile ?? null,
          ),
        );
        this.sessionStatsPusher.queue(session.sessionFile ?? null);

        return {
          id: correlationId,
          type: "response" as const,
          command: "navigate_tree" as const,
          success: true as const,
          data: result,
        };
      }

      /* ====================================================================
       * Discovery and sidebar data
       * Shared state: current session path, pending session, workspace cache.
       * ================================================================== */

      case "list_workspaces": {
        try {
          const workspaces = new Map<string, RpcWorkspaceSummary>();
          const appendSessionManagerWorkspace = (
            sessionManager: SessionListManager,
            fallbackWorkspacePath?: string,
          ) => {
            const header = sessionManager.getHeader();
            appendWorkspaceSummary(
              workspaces,
              sessionManager.getCwd() || header?.cwd || fallbackWorkspacePath,
              header?.timestamp,
            );
          };

          for (const registeredWorkspace of listRegisteredWorkspaces()) {
            appendWorkspaceSummary(
              workspaces,
              registeredWorkspace.workspacePath,
              readWorkspaceUpdatedAt(registeredWorkspace.workspacePath),
            );
          }

          appendSessionManagerWorkspace(
            this.context.state.sessionManager,
            this.context.state.cwd,
          );
          for (const sessionManager of this.sessionRuntime.getCachedSessionManagers()) {
            appendSessionManagerWorkspace(
              sessionManager,
              this.context.state.cwd,
            );
          }

          return {
            id: correlationId,
            type: "response" as const,
            command: "list_workspaces" as const,
            success: true as const,
            data: {
              workspaces: [...workspaces.values()].sort(
                compareWorkspaceSummaries,
              ),
            },
          };
        } catch {
          return {
            id: correlationId,
            type: "response" as const,
            command: "list_workspaces" as const,
            success: true as const,
            data: {
              workspaces: [] as RpcWorkspaceSummary[],
            },
          };
        }
      }

      case "list_sessions": {
        const workspacePath = normalizeOptionalWorkspaceRoot(
          command.workspacePath,
        );
        const merge = command.merge;

        if (!workspacePath) {
          return {
            id: correlationId,
            type: "response" as const,
            command: "list_sessions" as const,
            success: false as const,
            error: "workspacePath is required",
          };
        }

        try {
          const sessions: WorkspaceSessionEntry[] = [];
          const seenSessionPaths = new Set<string>();
          const liveSessionFile =
            this.context.state.sessionManager.getSessionFile();
          const cursor = decodeSessionCursor(command.cursor);
          const limit =
            typeof command.limit === "number" && command.limit > 0
              ? Math.floor(command.limit)
              : undefined;

          const appendSession = (session: WorkspaceSessionEntry | null) => {
            if (!session || seenSessionPaths.has(session.path)) return;

            const sessionWorkspacePath = normalizeOptionalWorkspaceRoot(
              session.workspacePath ?? session.workspaceId,
            );
            if (sessionWorkspacePath !== workspacePath) return;

            seenSessionPaths.add(session.path);
            sessions.push(session);
          };

          const appendSessionManager = (
            sessionManager: SessionListManager,
            sessionPath?: string,
            fallbackWorkspacePath?: string,
          ) => {
            if (!sessionPath || !fs.existsSync(sessionPath)) return;
            const header = sessionManager.getHeader();
            if (!header) return;

            const workspace = workspaceMetadata(
              sessionManager.getCwd() || header.cwd || fallbackWorkspacePath,
              sessionPath,
            );
            appendSession({
              id: header.id,
              name: sessionDisplayName(sessionManager, sessionPath),
              path: sessionPath,
              isRunning:
                sessionPath === liveSessionFile
                  ? !this.context.state.isIdle()
                  : this.sessionRuntime.isSessionRunning(sessionPath),
              timestamp: normalizeSessionTimestamp(header.timestamp),
              updatedAt: normalizeSessionTimestamp(header.timestamp),
              ...workspace,
            });
          };

          for (const sessionPath of listWorkspaceSessionFiles(workspacePath)) {
            appendSession(
              readWorkspaceSessionSummary(
                sessionPath,
                sessionPath === liveSessionFile
                  ? !this.context.state.isIdle()
                  : this.sessionRuntime.isSessionRunning(sessionPath),
              ),
            );
          }

          if (command.includeActive !== false) {
            appendSessionManager(
              this.context.state.sessionManager,
              liveSessionFile,
              this.context.state.cwd,
            );
            for (const sessionManager of this.sessionRuntime.getCachedSessionManagers()) {
              appendSessionManager(
                sessionManager,
                sessionManager.getSessionFile(),
                this.context.state.cwd,
              );
            }
          }

          const filteredSessions = sessions
            .filter(session => isAfterSessionCursor(session, cursor))
            .filter(session => sessionMatchesListQuery(session, command.query))
            .sort(compareSessionsByRecency);
          const limitedSessions = limit
            ? filteredSessions.slice(0, limit)
            : filteredSessions;
          const activeSessionPath =
            this.sessionRuntime.currentTranscriptSessionPath() ??
            liveSessionFile;
          const pageSessions = [...limitedSessions];
          if (command.includeActive !== false && activeSessionPath) {
            const activeSession = filteredSessions.find(
              session => session.path === activeSessionPath,
            );
            if (
              activeSession &&
              !pageSessions.some(session => session.path === activeSession.path)
            ) {
              pageSessions.push(activeSession);
            }
          }
          const nextCursor =
            limit && filteredSessions.length > limitedSessions.length
              ? encodeSessionCursor(limitedSessions[limitedSessions.length - 1])
              : undefined;

          return {
            id: correlationId,
            type: "response" as const,
            command: "list_sessions" as const,
            success: true as const,
            data: {
              sessions: pageSessions,
              workspacePath,
              nextCursor,
              merge,
            },
          };
        } catch {
          return {
            id: correlationId,
            type: "response" as const,
            command: "list_sessions" as const,
            success: true as const,
            data: {
              sessions: [] as WorkspaceSessionEntry[],
              workspacePath,
              merge,
            },
          };
        }
      }

      case "list_tree_entries": {
        try {
          const requestedSessionPath = command.sessionPath;
          const liveSessionPath =
            this.sessionRuntime.currentTranscriptSessionPath() ??
            this.context.state.sessionManager.getSessionFile();
          const sessionPath = requestedSessionPath ?? liveSessionPath;
          const entries =
            sessionPath && fs.existsSync(sessionPath)
              ? buildTreeEntriesForSessionPath(sessionPath)
              : buildTreeEntriesFromBranch(
                  this.context.state.sessionManager.getBranch(),
                );
          return {
            id: correlationId,
            type: "response" as const,
            command: "list_tree_entries" as const,
            success: true as const,
            data: { entries, sessionPath: sessionPath ?? undefined },
          };
        } catch {
          return {
            id: correlationId,
            type: "response" as const,
            command: "list_tree_entries" as const,
            success: true as const,
            data: {
              entries: [] as RpcTreeEntry[],
              sessionPath: command.sessionPath,
            },
          };
        }
      }

      case "list_workspace_entries": {
        const cwd =
          normalizeOptionalWorkspaceRoot(command.workspacePath) ||
          this.sessionRuntime.currentGitCwd();
        if (
          command.force ||
          !this.workspaceEntriesCache ||
          this.workspaceEntriesCache.cwd !== cwd
        ) {
          this.workspaceEntriesCache = {
            cwd,
            entries: listWorkspaceEntries(cwd),
          };
        }

        return {
          id: correlationId,
          type: "response" as const,
          command: "list_workspace_entries" as const,
          success: true as const,
          data: { entries: this.workspaceEntriesCache.entries },
        };
      }

      case "read_workspace_file": {
        const result = readWorkspaceFile(
          normalizeOptionalWorkspaceRoot(command.workspacePath) ||
            this.sessionRuntime.currentGitCwd(),
          command.path,
        );
        if ("error" in result) {
          return {
            id: correlationId,
            type: "response" as const,
            command: "read_workspace_file" as const,
            success: false as const,
            error: result.error,
          };
        }

        return {
          id: correlationId,
          type: "response" as const,
          command: "read_workspace_file" as const,
          success: true as const,
          data: result,
        };
      }

      case "list_git_branches": {
        const repoState = readGitRepoState(this.sessionRuntime.currentGitCwd());
        if (!repoState) {
          return {
            id: correlationId,
            type: "response" as const,
            command: "list_git_branches" as const,
            success: false as const,
            error: "No git repository found for the active session",
          };
        }

        return {
          id: correlationId,
          type: "response" as const,
          command: "list_git_branches" as const,
          success: true as const,
          data: repoState,
        };
      }

      case "switch_git_branch": {
        const branchName = command.branchName.trim();
        if (!branchName) {
          return {
            id: correlationId,
            type: "response" as const,
            command: "switch_git_branch" as const,
            success: false as const,
            error: "Branch name cannot be empty",
          };
        }

        const activeState = this.sessionRuntime.buildActiveState();
        if (activeState.isStreaming || activeState.isCompacting) {
          return {
            id: correlationId,
            type: "response" as const,
            command: "switch_git_branch" as const,
            success: false as const,
            error: "Cannot switch branches while the active session is busy",
          };
        }

        const repoState = readGitRepoState(this.sessionRuntime.currentGitCwd());
        if (!repoState) {
          return {
            id: correlationId,
            type: "response" as const,
            command: "switch_git_branch" as const,
            success: false as const,
            error: "No git repository found for the active session",
          };
        }

        if (repoState.currentBranch === branchName) {
          return {
            id: correlationId,
            type: "response" as const,
            command: "switch_git_branch" as const,
            success: true as const,
            data: repoState,
          };
        }

        const targetBranch = repoState.branches.find(
          branch => branch.name === branchName,
        );
        if (!targetBranch) {
          return {
            id: correlationId,
            type: "response" as const,
            command: "switch_git_branch" as const,
            success: false as const,
            error: `Branch not found: ${branchName}`,
          };
        }

        const localBranch = repoState.branches.find(
          branch =>
            branch.kind === "local" &&
            branch.shortName === targetBranch.shortName,
        );
        const switchArgs =
          targetBranch.kind === "local"
            ? ["switch", targetBranch.name]
            : localBranch
              ? ["switch", localBranch.name]
              : ["switch", "--track", targetBranch.name];
        const switchResult = runGitCommand(
          repoState.repoRoot,
          switchArgs,
          10000,
        );
        if (switchResult.error || switchResult.status !== 0) {
          const failureOutput = [switchResult.stderr, switchResult.stdout]
            .map(value => readSpawnText(value).trim())
            .filter(Boolean)
            .join("\n");
          return {
            id: correlationId,
            type: "response" as const,
            command: "switch_git_branch" as const,
            success: false as const,
            error: failureOutput || `Failed to switch to ${branchName}`,
          };
        }

        this.workspaceEntriesCache = null;
        const nextRepoState = readGitRepoState(repoState.repoRoot);
        if (!nextRepoState) {
          return {
            id: correlationId,
            type: "response" as const,
            command: "switch_git_branch" as const,
            success: false as const,
            error:
              "Branch switched, but the repository state could not be refreshed",
          };
        }

        return {
          id: correlationId,
          type: "response" as const,
          command: "switch_git_branch" as const,
          success: true as const,
          data: nextRepoState,
        };
      }

      case "create_git_branch": {
        const branchName = command.branchName.trim();
        if (!branchName) {
          return {
            id: correlationId,
            type: "response" as const,
            command: "create_git_branch" as const,
            success: false as const,
            error: "Branch name cannot be empty",
          };
        }

        const activeState = this.sessionRuntime.buildActiveState();
        if (activeState.isStreaming || activeState.isCompacting) {
          return {
            id: correlationId,
            type: "response" as const,
            command: "create_git_branch" as const,
            success: false as const,
            error: "Cannot create branches while the active session is busy",
          };
        }

        const repoState = readGitRepoState(this.sessionRuntime.currentGitCwd());
        if (!repoState) {
          return {
            id: correlationId,
            type: "response" as const,
            command: "create_git_branch" as const,
            success: false as const,
            error: "No git repository found for the active session",
          };
        }

        const branchNameCheck = runGitCommand(repoState.repoRoot, [
          "check-ref-format",
          "--branch",
          branchName,
        ]);
        if (branchNameCheck.error || branchNameCheck.status !== 0) {
          return {
            id: correlationId,
            type: "response" as const,
            command: "create_git_branch" as const,
            success: false as const,
            error: `Invalid branch name: ${branchName}`,
          };
        }

        const branchExists = repoState.branches.some(
          branch => branch.kind === "local" && branch.name === branchName,
        );
        if (branchExists) {
          return {
            id: correlationId,
            type: "response" as const,
            command: "create_git_branch" as const,
            success: false as const,
            error: `Branch already exists: ${branchName}`,
          };
        }

        const createResult = runGitCommand(
          repoState.repoRoot,
          ["switch", "-c", branchName],
          10000,
        );
        if (createResult.error || createResult.status !== 0) {
          const failureOutput = [createResult.stderr, createResult.stdout]
            .map(value => readSpawnText(value).trim())
            .filter(Boolean)
            .join("\n");
          return {
            id: correlationId,
            type: "response" as const,
            command: "create_git_branch" as const,
            success: false as const,
            error: failureOutput || `Failed to create ${branchName}`,
          };
        }

        this.workspaceEntriesCache = null;
        const nextRepoState = readGitRepoState(repoState.repoRoot);
        if (!nextRepoState) {
          return {
            id: correlationId,
            type: "response" as const,
            command: "create_git_branch" as const,
            success: false as const,
            error:
              "Branch created, but the repository state could not be refreshed",
          };
        }

        return {
          id: correlationId,
          type: "response" as const,
          command: "create_git_branch" as const,
          success: true as const,
          data: nextRepoState,
        };
      }

      default: {
        const unknownCommand = command as { type: string };
        return {
          id: correlationId,
          type: "response",
          command: unknownCommand.type,
          success: false,
          error: `Unknown command: ${unknownCommand.type}`,
        };
      }
    }
  }

  /* ------------------------------------------------------------------------
   * Extension UI bridge
   * ---------------------------------------------------------------------- */

  private handleUIResponse(response: RpcExtensionUIResponse): void {
    this.uiBridge.handleResponse(response);
  }

  createExtensionUIContext(): ExtensionUIContext {
    return this.uiBridge.createContext();
  }

  /* ------------------------------------------------------------------------
   * Transport and teardown
   * ---------------------------------------------------------------------- */

  private sendResponse(message: ServerMessage): void {
    if (this.disposed || this.ws.readyState !== 1) {
      // WebSocket.OPEN = 1
      return;
    }
    try {
      this.ws.send(JSON.stringify(message));
    } catch (err) {
      console.error(
        `WsRpcAdapter[${this.client.id}]: Failed to send response:`,
        err,
      );
    }
  }

  /**
   * Dispose the adapter, cleaning up pending requests and subscriptions
   */
  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;

    console.log(`WsRpcAdapter[${this.client.id}]: Disposing adapter`);

    if (this.pendingTranscriptDeltaBatch) {
      clearTimeout(this.pendingTranscriptDeltaBatch.timeoutId);
      this.pendingTranscriptDeltaBatch = null;
    }

    this.uiBridge.dispose();
    this.sessionStatsPusher.dispose();

    if (this.unsubscribeRegistryEvents) {
      this.unsubscribeRegistryEvents();
      this.unsubscribeRegistryEvents = undefined;
    }

    this.sessionRuntime.dispose();

    // Notify event bus
    this.emitEvent({
      type: "client_disconnect",
      client: this.client,
      reason: "adapter_disposed",
    });
  }
}
