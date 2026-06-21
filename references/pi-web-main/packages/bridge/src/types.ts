/**
 * Bridge type definitions for the Pi Web Bridge extension.
 *
 * Defines RPC protocol types (mirrored from the coding-agent's internal
 * rpc-types module, which is not exported from the npm package) and
 * bridge-specific types for server configuration, runtime state, and
 * WebSocket client tracking.
 */

// ============================================================================
// RPC Commands (client → server)
// ============================================================================

export interface RpcImageContent {
  type: "image";
  data: string;
  mimeType: string;
}

export interface RpcQueuedMessage {
  text: string;
  images: RpcImageContent[];
  timestamp: number;
  queueType?: "steering" | "followUp";
}

export interface RpcWorkspaceEntry {
  path: string;
  kind: "file" | "directory";
}

export interface RpcWorkspaceSummary {
  id: string;
  name: string;
  path: string;
  updatedAt?: string;
}

export interface RpcWorkspaceFile {
  path: string;
  absolutePath: string;
  content: string;
  truncated: boolean;
  totalBytes: number;
  lineCount: number;
}

export interface RpcGitBranch {
  name: string;
  shortName: string;
  kind: "local" | "remote";
  remoteName?: string;
  isCurrent: boolean;
}

export interface RpcGitRepoState {
  repoRoot: string;
  headLabel: string;
  currentBranch?: string;
  detached: boolean;
  isDirty: boolean;
  branches: RpcGitBranch[];
}

/** Map of RPC command types to their specific payload shapes. */
export interface RpcModel {
  id: string;
  provider: string;
  name?: string;
  api?: string;
  reasoning?: boolean;
  contextWindow?: number;
  maxTokens?: number;
}

export type RpcThinkingLevel =
  | "off"
  | "minimal"
  | "low"
  | "medium"
  | "high"
  | "xhigh";

export type RpcJsonValue =
  | string
  | number
  | boolean
  | null
  | RpcJsonValue[]
  | { [key: string]: RpcJsonValue };

export type RpcJsonObject = { [key: string]: RpcJsonValue };

export type RpcToolArguments = string | RpcJsonObject;

export type RpcToolResultDetails = RpcJsonValue;

export interface RpcCompactionResult {
  summary: string;
  firstKeptEntryId: string;
  tokensBefore: number;
  details?: unknown;
}

export interface RpcBashResult {
  output: string;
  exitCode: number | undefined;
  cancelled: boolean;
  truncated: boolean;
  fullOutputPath?: string;
}

export interface RpcAgentTextContent {
  type: "text";
  text: string;
  textSignature?: string;
}

export interface RpcAgentThinkingContent {
  type: "thinking";
  thinking: string;
  thinkingSignature?: string;
  redacted?: boolean;
}

export interface RpcAgentToolCall {
  type: "toolCall";
  id: string;
  name: string;
  arguments: RpcJsonObject;
  thoughtSignature?: string;
}

export interface RpcAgentUsageCost {
  input: number;
  output: number;
  cacheRead: number;
  cacheWrite: number;
  total: number;
}

export interface RpcAgentUsage {
  input: number;
  output: number;
  cacheRead: number;
  cacheWrite: number;
  totalTokens: number;
  cost: RpcAgentUsageCost;
}

export type RpcAgentStopReason =
  | "stop"
  | "length"
  | "toolUse"
  | "error"
  | "aborted";

export interface RpcAgentUserMessage {
  role: "user";
  content: string | Array<RpcAgentTextContent | RpcImageContent>;
  timestamp: number;
}

export interface RpcAgentAssistantMessage {
  role: "assistant";
  content: Array<
    RpcAgentTextContent | RpcAgentThinkingContent | RpcAgentToolCall
  >;
  api: string;
  provider: string;
  model: string;
  responseId?: string;
  usage: RpcAgentUsage;
  stopReason: RpcAgentStopReason;
  errorMessage?: string;
  timestamp: number;
}

export interface RpcAgentToolResultMessage {
  role: "toolResult";
  toolCallId: string;
  toolName: string;
  content: Array<RpcAgentTextContent | RpcImageContent>;
  details?: unknown;
  isError: boolean;
  timestamp: number;
}

export type RpcAgentMessage =
  | RpcAgentUserMessage
  | RpcAgentAssistantMessage
  | RpcAgentToolResultMessage;

export interface RpcAgentStartEvent {
  type: "agent_start";
  sessionPath?: string;
}

export interface RpcAgentEndEvent {
  type: "agent_end";
  sessionPath?: string;
  messages?: RpcAgentMessage[];
}

export interface RpcModelSelectEvent {
  type: "model_select";
  model: RpcModel;
  previousModel?: RpcModel;
  source: "set" | "cycle" | "restore";
}

export type RpcCompactionReason = "manual" | "threshold" | "overflow";

export interface RpcCompactionStartEvent {
  type: "compaction_start";
  reason: RpcCompactionReason;
}

export interface RpcCompactionEndEvent {
  type: "compaction_end";
  reason: RpcCompactionReason;
  result: RpcCompactionResult | null;
  aborted: boolean;
  willRetry: boolean;
  errorMessage?: string;
}

export interface RpcCommandMap {
  /** Prompting */
  prompt: {
    message: string;
    images?: RpcImageContent[];
    streamingBehavior?: "steer" | "followUp";
  };
  steer: {
    message: string;
    images?: RpcImageContent[];
  };
  follow_up: {
    message: string;
    images?: RpcImageContent[];
  };
  abort: {};
  new_session: {
    parentSession?: string;
    limit?: number;
    workspacePath?: string;
  };
  register_workspace: {
    workspacePath?: string;
  };

  /** State */
  get_state: {};

  /** Model */
  set_model: { provider: string; modelId: string };
  cycle_model: {};
  get_available_models: {};

  /** Thinking */
  set_thinking_level: { level: RpcThinkingLevel };
  cycle_thinking_level: {};

  /** Queue modes */
  set_steering_mode: { mode: "all" | "one-at-a-time" };
  set_follow_up_mode: { mode: "all" | "one-at-a-time" };

  /** Compaction */
  compact: { customInstructions?: string };
  set_auto_compaction: { enabled: boolean };

  /** Retry */
  set_auto_retry: { enabled: boolean };
  abort_retry: {};

  /** Bash */
  bash: { command: string };
  abort_bash: {};

  /** Session */
  export_html: { outputPath?: string };
  set_session_name: { name: string };
  switch_session: { sessionPath: string; limit?: number };
  select_tree_entry: { entryId: string };
  navigate_tree: {
    entryId: string;
    summarize?: boolean;
    customInstructions?: string;
    replaceInstructions?: boolean;
    label?: string;
  };
  fork: { entryId: string };
  get_fork_messages: {};
  get_last_assistant_text: {};
  delete_session: { sessionPath: string };

  /** Messages / Commands */
  get_messages: {
    sessionPath?: string;
    direction?: "latest" | "older";
    cursor?: string;
    limit?: number;
  };
  get_commands: {};

  /** Discovery */
  list_workspaces: {};
  list_sessions: {
    workspacePath?: string;
    limit?: number;
    cursor?: string;
    query?: string;
    includeActive?: boolean;
    merge?: "replace" | "append";
  };
  list_tree_entries: { sessionPath?: string };
  list_workspace_entries: { force?: boolean; workspacePath?: string };
  read_workspace_file: { path: string; workspacePath?: string };

  /** Git */
  list_git_branches: {};
  switch_git_branch: { branchName: string };
  create_git_branch: { branchName: string };

  /** Detached follow-up queue */
  dequeue_follow_up_message: { index: number };
}

/** All RPC command types that a browser client can send. */
export type RpcCommand = {
  [K in keyof RpcCommandMap]: { id?: string; type: K } & RpcCommandMap[K];
}[keyof RpcCommandMap];

/** Helper type to extract the `type` discriminant. */
export type RpcCommandType = keyof RpcCommandMap;

/** Extract payload fields for a specific command type. */
export type RpcCommandPayload<T extends RpcCommandType> = Omit<
  Extract<RpcCommand, { type: T }>,
  "id" | "type"
>;

// ============================================================================
// RPC State
// ============================================================================

export interface RpcWorkspaceEnvironment {
  type: "direnv" | "python-venv";
  label: string;
  detail?: string;
}

export interface RpcSessionState {
  model?: RpcModel;
  thinkingLevel: RpcThinkingLevel;
  isStreaming: boolean;
  isCompacting: boolean;
  steeringMode: "all" | "one-at-a-time";
  followUpMode: "all" | "one-at-a-time";
  sessionFile?: string;
  sessionId: string;
  sessionName?: string;
  workspacePath?: string;
  workspaceEnvironments?: RpcWorkspaceEnvironment[];
  gitBranch?: string;
  autoCompactionEnabled: boolean;
  messageCount: number;
  pendingMessageCount: number;
}

/** A command available for invocation via prompt. */
export interface RpcSlashCommand {
  name: string;
  description?: string;
  source: "extension" | "prompt" | "skill";
}

export type RpcTreeTrackColumn = "blank" | "line" | "branch" | "branch-last";

export interface RpcTreeEntry {
  id: string;
  label?: string;
  type: string;
  timestamp?: string;
  parentId?: string | null;
  depth?: number;
  trackColumns?: RpcTreeTrackColumn[];
  isActive?: boolean;
  isOnActivePath?: boolean;
  role?: "user" | "assistant" | "tool" | "meta" | "other";
  labelTag?: string;
  previewText?: string;
  searchText?: string;
  isSettingsEntry?: boolean;
  isLabeled?: boolean;
  isToolOnlyAssistant?: boolean;
}

export interface RpcSessionStats {
  tokens: number | null;
  contextWindow: number;
  percent: number | null;
  messageCount: number;
  cost: number;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
}

export type RpcTranscriptRole =
  | "user"
  | "assistant"
  | "toolResult"
  | "tool"
  | "system"
  | "bashExecution"
  | (string & {});

export interface RpcTranscriptTextBlock {
  type: "text";
  text: string;
  textSignature?: string;
}

export interface RpcTranscriptThinkingBlock {
  type: "thinking";
  thinking: string;
  thinkingSignature?: string;
  redacted?: boolean;
}

export interface RpcTranscriptImageBlock {
  type: "image";
  data?: string;
  mimeType?: string;
  text?: string;
  url?: string;
}

export interface RpcTranscriptImageUrlBlock {
  type: "image_url";
  image_url?: string | { url?: string };
  text?: string;
  mimeType?: string;
  url?: string;
}

export interface RpcTranscriptToolCallBlock {
  type: "toolCall";
  id?: string;
  name?: string;
  arguments?: RpcToolArguments;
  thoughtSignature?: string;
}

export interface RpcTranscriptToolResultBlock {
  type: "toolResult";
  text?: string;
  content?: Array<
    | string
    | RpcTranscriptTextBlock
    | RpcTranscriptImageBlock
    | RpcTranscriptImageUrlBlock
  >;
  details?: RpcToolResultDetails;
  isError?: boolean;
}

export interface RpcTranscriptCompactionBlock {
  type: "compaction";
  summary: string;
  tokensBefore: number;
  firstKeptEntryId?: string;
}

export interface RpcTranscriptBranchSummaryBlock {
  type: "branch_summary";
  summary: string;
  fromId: string;
}

export interface RpcTranscriptModelChangeBlock {
  type: "model_change";
  provider: string;
  modelId: string;
}

export interface RpcTranscriptThinkingLevelChangeBlock {
  type: "thinking_level_change";
  thinkingLevel: string;
}

export interface RpcTranscriptSessionInfoBlock {
  type: "session_info";
  name?: string;
}

export type RpcTranscriptSystemBlock =
  | RpcTranscriptCompactionBlock
  | RpcTranscriptBranchSummaryBlock
  | RpcTranscriptModelChangeBlock
  | RpcTranscriptThinkingLevelChangeBlock
  | RpcTranscriptSessionInfoBlock;

export type RpcTranscriptContentBlock =
  | RpcTranscriptTextBlock
  | RpcTranscriptThinkingBlock
  | RpcTranscriptImageBlock
  | RpcTranscriptImageUrlBlock
  | RpcTranscriptToolCallBlock
  | RpcTranscriptToolResultBlock
  | RpcTranscriptSystemBlock;

export type RpcTranscriptContent =
  | string
  | Array<string | RpcTranscriptContentBlock>;

export interface RpcTranscriptMessage {
  transcriptKey?: string;
  id?: string;
  role: RpcTranscriptRole;
  content?: RpcTranscriptContent;
  text?: string;
  timestamp?: string;
  stopReason?: string;
  errorMessage?: string;
  toolCallId?: string;
  toolName?: string;
  isError?: boolean;
  details?: RpcToolResultDetails;
}

export interface RpcTranscriptPage {
  sessionPath?: string;
  messages: RpcTranscriptMessage[];
  oldestCursor?: string;
  newestCursor?: string;
  hasOlder: boolean;
  hasNewer: boolean;
}

export interface RpcTranscriptSnapshotEvent extends RpcTranscriptPage {
  type: "transcript_snapshot";
}

export interface RpcTranscriptStartEvent {
  type: "transcript_start";
  sessionPath?: string;
  message: RpcTranscriptMessage;
  treeEntries?: RpcTreeEntry[];
}

export interface RpcTranscriptUpsertEvent {
  type: "transcript_upsert";
  sessionPath?: string;
  message: RpcTranscriptMessage;
  treeEntries?: RpcTreeEntry[];
}

export interface RpcTranscriptDeltaEvent {
  type: "transcript_delta";
  sessionPath?: string;
  transcriptKey: string;
  messageId?: string;
  role: RpcTranscriptRole;
  contentIndex: number;
  blockType: "text" | "thinking" | "toolCall";
  delta: string;
  toolCallId?: string;
  toolName?: string;
}

export interface RpcSessionStatsEvent {
  type: "session_stats";
  sessionPath?: string;
  stats: RpcSessionStats;
}

export interface RpcQueueUpdateEvent {
  type: "queue_update";
  sessionPath?: string;
  steering: RpcQueuedMessage[];
  followUp: RpcQueuedMessage[];
}

// ============================================================================
// RPC Responses (server → client)
// ============================================================================

/** Map of RPC command types to their success response data shapes. */
export interface RpcResponseMap {
  prompt: void;
  steer: void;
  follow_up: void;
  abort: void;
  new_session: {
    transcript: RpcTranscriptPage;
    treeEntries: RpcTreeEntry[];
    sessionId: string;
    sessionName: string;
    sessionPath: string;
    workspacePath?: string;
    cancelled: boolean;
  };
  register_workspace: {
    workspaceId: string;
    workspaceName: string;
    workspacePath: string;
    created: boolean;
    cancelled: boolean;
  };
  get_state: RpcSessionState;
  set_model: RpcModel;
  cycle_model: {
    model: RpcModel;
    thinkingLevel: RpcThinkingLevel;
    isScoped: boolean;
  } | null;
  get_available_models: { models: RpcModel[] };
  set_thinking_level: void;
  cycle_thinking_level: { level: RpcThinkingLevel } | null;
  set_steering_mode: void;
  set_follow_up_mode: void;
  compact: RpcCompactionResult;
  set_auto_compaction: void;
  set_auto_retry: void;
  abort_retry: void;
  bash: RpcBashResult;
  abort_bash: void;
  export_html: { path: string };
  switch_session: {
    transcript: RpcTranscriptPage;
    treeEntries: RpcTreeEntry[];
    sessionId: string;
    sessionName: string;
    sessionPath: string;
    workspacePath?: string;
    cancelled: boolean;
  };
  select_tree_entry: {
    transcript: RpcTranscriptPage;
    treeEntries: RpcTreeEntry[];
    sessionId: string;
    sessionName: string;
    sessionPath: string;
    workspacePath?: string;
    cancelled: boolean;
  };
  navigate_tree: { cancelled: boolean };
  fork: { text: string; cancelled: boolean };
  get_fork_messages: { messages: Array<{ entryId: string; text: string }> };
  get_last_assistant_text: { text: string | null };
  delete_session: void;
  set_session_name: void;
  get_messages: RpcTranscriptPage & { direction: "latest" | "older" };
  get_commands: { commands: RpcSlashCommand[] };
  list_workspaces: { workspaces: RpcWorkspaceSummary[] };
  list_sessions: {
    sessions: Array<{
      id: string;
      name: string;
      path: string;
      isRunning?: boolean;
      timestamp?: string;
      updatedAt?: string;
      workspaceId?: string;
      workspaceName?: string;
      workspacePath?: string;
    }>;
    workspacePath?: string;
    nextCursor?: string;
    merge?: "replace" | "append";
  };
  list_tree_entries: { entries: RpcTreeEntry[]; sessionPath?: string };
  list_workspace_entries: { entries: RpcWorkspaceEntry[] };
  read_workspace_file: RpcWorkspaceFile;
  list_git_branches: RpcGitRepoState;
  switch_git_branch: RpcGitRepoState;
  create_git_branch: RpcGitRepoState;
  dequeue_follow_up_message: { removed: RpcQueuedMessage };
}

type RpcResponseData<T> = [T] extends [void]
  ? { data?: undefined }
  : { data: T };

/** Structured responses sent back to the browser client after command dispatch. */
export type RpcResponse =
  | {
      [K in keyof RpcResponseMap]: {
        id?: string;
        type: "response";
        command: K;
        success: true;
      } & RpcResponseData<RpcResponseMap[K]>;
    }[keyof RpcResponseMap]
  | {
      id?: string;
      type: "response";
      command: string;
      success: false;
      error: string;
    };

// ============================================================================
// Extension UI (routed over WebSocket)
// ============================================================================

/** UI request forwarded from Pi to a specific browser client. */
export type RpcExtensionUIRequest =
  | {
      type: "extension_ui_request";
      id: string;
      method: "select";
      title: string;
      options: string[];
      timeout?: number;
    }
  | {
      type: "extension_ui_request";
      id: string;
      method: "confirm";
      title: string;
      message: string;
      timeout?: number;
    }
  | {
      type: "extension_ui_request";
      id: string;
      method: "input";
      title: string;
      placeholder?: string;
      timeout?: number;
    }
  | {
      type: "extension_ui_request";
      id: string;
      method: "editor";
      title: string;
      prefill?: string;
    }
  | {
      type: "extension_ui_request";
      id: string;
      method: "notify";
      message: string;
      notifyType?: "info" | "warning" | "error";
    }
  | {
      type: "extension_ui_request";
      id: string;
      method: "setStatus";
      statusKey: string;
      statusText: string | undefined;
    }
  | {
      type: "extension_ui_request";
      id: string;
      method: "setWidget";
      widgetKey: string;
      widgetLines: string[] | undefined;
      widgetPlacement?: "aboveEditor" | "belowEditor";
    }
  | {
      type: "extension_ui_request";
      id: string;
      method: "setTitle";
      title: string;
    }
  | {
      type: "extension_ui_request";
      id: string;
      method: "set_editor_text";
      text: string;
    };

/** Response from the browser client resolving a UI request. */
export type RpcExtensionUIResponse =
  | { type: "extension_ui_response"; id: string; value: string }
  | { type: "extension_ui_response"; id: string; confirmed: boolean }
  | { type: "extension_ui_response"; id: string; cancelled: true };

// ============================================================================
// Bridge Configuration
// ============================================================================

/** Configuration for the bridge server, sourced from extension config or defaults. */
export interface BridgeConfig {
  /** Host to bind the HTTP/WebSocket server to. Default: "localhost" */
  readonly host: string;
  /** Preferred port; 0 means OS-assigned. Default: 8080 */
  readonly port: number;
  /** Upper bound for port-range fallback when the preferred port is in use. Default: 0 (no fallback) */
  readonly portMax: number;
  /** Directory containing static files to serve (for the web UI bundle). Default: undefined (404) */
  readonly staticDir?: string;
  /** Timeout in ms for extension UI dialog requests routed to WS clients. Default: 60_000 */
  readonly uiRequestTimeout: number;
  /** Maximum number of WS frames to buffer per client before dropping oldest. Default: 256 */
  readonly clientBufferSize: number;
}

/** Sensible defaults for bridge configuration. */
export const DEFAULT_BRIDGE_CONFIG: BridgeConfig = {
  host: "0.0.0.0",
  port: 7036,
  portMax: 0,
  uiRequestTimeout: 60_000,
  clientBufferSize: 256,
};

// ============================================================================
// Bridge Runtime State
// ============================================================================

/** The lifecycle state of the bridge server. */
export type BridgeState =
  | { status: "stopped" }
  | { status: "starting"; port: number }
  | { status: "running"; host: string; port: number }
  | { status: "stopping" };

// ============================================================================
// WebSocket Client
// ============================================================================

/** Metadata for a connected WebSocket client. */
export interface WsClient {
  /** Unique identifier assigned on connection. */
  readonly id: string;
  /** Monotonic connection sequence number (1-based). */
  readonly seq: number;
  /** ISO-8601 timestamp of when the client connected. */
  readonly connectedAt: string;
}

// ============================================================================
// Bridge Events (internal event bus)
// ============================================================================

/** Events emitted by the bridge runtime for terminal log view and internal wiring. */
export type BridgeEvent =
  | { type: "server_start"; host: string; port: number }
  | { type: "server_stop" }
  | { type: "client_connect"; client: WsClient }
  | { type: "client_disconnect"; client: WsClient; reason?: string }
  | {
      type: "command_received";
      client: WsClient;
      commandType: string;
      correlationId?: string;
    }
  | {
      type: "command_error";
      client: WsClient;
      commandType: string;
      correlationId?: string;
      error: string;
    }
  | { type: "auth_rejected"; clientIp: string; protocol: "http" | "ws" }
  | { type: "sigint_received" }
  | { type: "shutdown_complete" };

// ============================================================================
// Wire Protocol (JSON over WebSocket)
// ============================================================================

/** Envelope for messages sent from server → browser client. */
export type RpcBridgeEvent =
  | RpcTranscriptSnapshotEvent
  | RpcTranscriptStartEvent
  | RpcTranscriptUpsertEvent
  | RpcTranscriptDeltaEvent
  | RpcSessionStatsEvent
  | RpcQueueUpdateEvent
  | RpcAgentStartEvent
  | RpcAgentEndEvent
  | RpcModelSelectEvent
  | RpcCompactionStartEvent
  | RpcCompactionEndEvent
  | { type: "session_compact" };

export type ServerMessage =
  | { type: "event"; payload: RpcBridgeEvent }
  | { type: "extension_ui_request"; payload: RpcExtensionUIRequest }
  | { type: "response"; payload: RpcResponse };

/** Envelope for messages sent from browser client → server. */
export type ClientMessage =
  | { type: "command"; payload: RpcCommand }
  | { type: "extension_ui_response"; payload: RpcExtensionUIResponse };
