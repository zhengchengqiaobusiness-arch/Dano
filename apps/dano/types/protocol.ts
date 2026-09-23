/**
 * Browser-safe HTTP/SSE protocol derived from Pi's public runtime types and
 * composed with Dano-owned workspace, form, transcript, safety, and client
 * semantics.
 */

import type {
  Api,
  ImageContent as PiImageContent,
  Model as PiModel,
} from "@earendil-works/pi-ai";
import type {
  AgentSession,
  AgentSessionEvent,
  RpcCommand as PiRpcCommand,
  RpcExtensionUIRequest as PiRpcExtensionUIRequest,
  RpcExtensionUIResponse as PiRpcExtensionUIResponse,
  RpcResponse as PiRpcResponse,
  RpcSessionState as PiRpcSessionState,
} from "@earendil-works/pi-coding-agent";

type PiRpcCommandPayload<T extends PiRpcCommand["type"]> = Omit<
  Extract<PiRpcCommand, { type: T }>,
  "id" | "type"
>;

type PiRpcSuccessResponse = Extract<PiRpcResponse, { success: true }>;
type PiRpcResponseData<T extends PiRpcSuccessResponse["command"]> = Extract<
  PiRpcSuccessResponse,
  { command: T }
> extends { data: infer TData }
  ? TData
  : void;
type PiRpcSlashCommand = PiRpcResponseData<"get_commands">["commands"][number];

// ============================================================================
// RPC Commands (client → server)
// ============================================================================

export type RpcImageContent = PiImageContent;

export interface RpcUploadedFileRef {
  id: string;
  name: string;
  size: number;
  mimeType: string;
  path: string;
  relativePath?: string;
  previewUrl?: string;
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

/** Browser-safe subset of Pi's model catalog entry. */
export type RpcModel = Pick<PiModel<Api>, "id" | "provider"> &
  Partial<
    Pick<
      PiModel<Api>,
      "name" | "api" | "reasoning" | "contextWindow" | "maxTokens"
    >
  >;

/** Dano's existing browser controls intentionally do not expose Pi's `max`. */
export type RpcThinkingLevel = Exclude<
  AgentSession["thinkingLevel"],
  "max"
>;

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

export const ASK_USER_QUESTION_TOOL_NAME = "ask_user_question";
export const ASK_USER_QUESTION_PRESENTATION_RETRY_CODE =
  "QUESTION_PRESENTATION_TIMEOUT";
export const ASK_USER_QUESTION_PRESENTATION_TERMINAL_CODE =
  "QUESTION_PRESENTATION_FAILED";
export const ASK_USER_QUESTION_VALIDATION_TERMINAL_CODE =
  "QUESTION_VALIDATION_FAILED";
export const ASK_USER_QUESTION_CANCELLED_CODE =
  "ASK_USER_QUESTION_CANCELLED";

export const ASK_USER_QUESTION_ERROR_CATEGORIES = [
  "validation",
  "confirmation",
  "duplicate_call",
  "lifecycle",
] as const;

export type AskUserQuestionErrorCategory =
  (typeof ASK_USER_QUESTION_ERROR_CATEGORIES)[number];

export const ASK_USER_QUESTION_ERROR_CODES = [
  "invalid_question_arguments",
  "invalid_confirmation_source",
  "duplicate_question_call",
  "question_presentation_timeout",
  "question_presentation_failed",
  "question_validation_failed",
  "question_cancelled",
] as const;

export type AskUserQuestionErrorCode =
  (typeof ASK_USER_QUESTION_ERROR_CODES)[number];

export const ASK_USER_QUESTION_ISSUE_CODES = [
  "invalid_request_shape",
  "invalid_questions_json",
  "invalid_questions_shape",
  "invalid_question_item",
  "conflicting_aliases",
  "missing_question_id",
  "duplicate_question_id",
  "missing_question_text",
  "invalid_input_type",
  "invalid_options",
  "duplicate_option_id",
  "missing_choice_source",
  "invalid_default",
  "invalid_date_format",
  "invalid_data_source",
  "invalid_confirmation_target",
  "duplicate_tool_call",
  "presentation_timeout",
  "presentation_failed",
  "validation_retry_exhausted",
  "cancelled",
] as const;

export type AskUserQuestionIssueCode =
  (typeof ASK_USER_QUESTION_ISSUE_CODES)[number];

export type AskUserQuestionErrorIssue = {
  code: AskUserQuestionIssueCode;
  path?: string;
  message: string;
};

export type AskUserQuestionError = {
  code: AskUserQuestionErrorCode;
  category: AskUserQuestionErrorCategory;
  message: string;
  retryable: boolean;
  issues: AskUserQuestionErrorIssue[];
  sourceCode?: AskUserQuestionErrorCode;
  /** Legacy Pi error token retained as structured metadata during migration. */
  terminalCode?:
    | typeof ASK_USER_QUESTION_PRESENTATION_RETRY_CODE
    | typeof ASK_USER_QUESTION_PRESENTATION_TERMINAL_CODE
    | typeof ASK_USER_QUESTION_VALIDATION_TERMINAL_CODE
    | typeof ASK_USER_QUESTION_CANCELLED_CODE;
  context?: {
    receivedShape?: { formIds: string; formId: string };
    ignoredReasons?: string[];
    fallbackAttempted?: boolean;
  };
};

export type AskUserQuestionInvalidResult = {
  status: "invalid";
  error: AskUserQuestionError;
};

/** Browser-safe summary; field-level diagnostics remain model-facing only. */
export type AskUserQuestionErrorProjection = {
  code: AskUserQuestionErrorCode;
  category: AskUserQuestionErrorCategory;
  message: string;
  retryable: boolean;
};

export type AskUserQuestionOptionId = string | number;

export type AskUserQuestionOption = {
  id: AskUserQuestionOptionId;
  label: string;
  extra?: Record<string, unknown>;
};

export type AskUserQuestionChoiceAnswerInput =
  | AskUserQuestionOptionId
  | AskUserQuestionOption;

export type AskUserQuestionAnswerInput =
  | AskUserQuestionOptionId
  | boolean
  | AskUserQuestionOption
  | AskUserQuestionChoiceAnswerInput[];

export type AskUserQuestionAnswer =
  | AskUserQuestionOptionId
  | AskUserQuestionOptionId[]
  | boolean;

export type AskUserQuestionInputType =
  | "text"
  | "textarea"
  | "date"
  | "radio"
  | "checkbox"
  | "select"
  | "treeSelect"
  | "confirm";

export type AskUserQuestionDataSource = {
  type: "api";
  endpoint: string;
  method?: "GET" | "POST";
  params?: Record<string, unknown>;
  searchParam?: string;
  pageParam?: string;
  pageSizeParam?: string;
  pageSize?: number;
  resultPath?: string;
  totalPath?: string;
  idField?: string;
  labelField?: string;
  childrenField?: string;
  extraFields?: string[];
};

export type AskUserQuestionResult =
  | {
      status: "answered";
      answer: AskUserQuestionAnswer | Record<string, AskUserQuestionAnswer>;
      formId?: string;
    }
  | {
      status: "confirmed";
      answer: Record<string, AskUserQuestionAnswer>;
      confirmationOfToolCallId: string;
      forms: AskUserQuestionConfirmedForm[];
    }
  | { status: "cancelled" }
  | AskUserQuestionInvalidResult;

export type AskUserQuestionConfirmedForm = {
  formId: string;
  answer: Record<string, AskUserQuestionAnswer>;
};

export type AskUserQuestionCardItem =
  | {
      id: string;
      kind: "text";
      question: string;
      inputType?: "text" | "textarea";
      fieldAssist: boolean;
      required?: boolean;
      default?: string;
    }
  | {
      id: string;
      kind: "date";
      question: string;
      dateFormat: string;
      required?: boolean;
      default?: string;
    }
  | {
      id: string;
      kind: "single";
      question: string;
      options: AskUserQuestionOption[];
      required?: boolean;
      default?: AskUserQuestionOptionId;
    }
  | {
      id: string;
      kind: "select" | "treeSelect";
      question: string;
      options: AskUserQuestionOption[];
      dataSource?: AskUserQuestionDataSource;
      required?: boolean;
      default?: AskUserQuestionOptionId;
    }
  | {
      id: string;
      kind: "multiple";
      question: string;
      options: AskUserQuestionOption[];
      dataSource?: AskUserQuestionDataSource;
      inputType?: "treeSelect";
      required?: boolean;
      default?: AskUserQuestionOptionId[];
    }
  | {
      id: string;
      kind: "confirm";
      question: string;
      required?: boolean;
      default?: boolean;
    };

export type AskUserQuestionBatchCardRequest = {
  batch: true;
  /** Optional only when replaying pre-title transcript entries. New tool calls require it. */
  title?: string;
  questions: AskUserQuestionCardItem[];
};

export type AskUserQuestionConfirmationForm = {
  formId: string;
  title: string;
  questions: AskUserQuestionCardItem[];
  answer: Record<string, AskUserQuestionAnswer>;
};

export type FormInteractionState =
  | "awaiting_confirmation"
  | "revising"
  | "confirmed"
  | "cancelled"
  | "interrupted";

export type FormInteractionAction =
  | "confirm"
  | "cancel"
  | "return_modify"
  | "cancel_revision"
  | "submit_revision";

export type FormRevisionProjection = AskUserQuestionConfirmationForm & {
  revision: number;
};

export type FormInteractionProjection = {
  interactionId: string;
  state: FormInteractionState;
  revision: number;
  allowedActions: FormInteractionAction[];
  forms: FormRevisionProjection[];
};

export type AskUserQuestionConfirmationCardRequest = {
  batch: false;
  kind: "confirm";
  id: "confirmation";
  title: string;
  confirmationOfToolCallId: string;
  questions: AskUserQuestionCardItem[];
  answer: Record<string, AskUserQuestionAnswer>;
  forms?: AskUserQuestionConfirmationForm[];
};

export type AskUserQuestionCardRequest =
  | (Exclude<AskUserQuestionCardItem, { kind: "confirm" }> & { batch: false })
  | AskUserQuestionBatchCardRequest
  | AskUserQuestionConfirmationCardRequest;

export type AskUserQuestionLifecycleState =
  | "invalid"
  | "retrying"
  | "awaiting_presentation"
  | "presented"
  | "answered"
  | "cancelled"
  | "terminal_failure";

export type FieldAssistAction = "regenerate" | "polish";
export type FieldAssistFieldType = "input" | "textarea";
export type FieldAssistWarningCode = "SENSITIVE_FIELD";

export interface FieldAssistCommandPayload {
  requestId: string;
  action: FieldAssistAction;
  fieldType: FieldAssistFieldType;
  requestMethod: "input" | "editor";
  title: string;
  placeholder?: string;
  currentValue: string;
  prefill?: string;
}

export interface FieldAssistWarning {
  code: FieldAssistWarningCode;
  message: string;
}

export interface FieldAssistMetadata {
  action: FieldAssistAction;
  fieldType: FieldAssistFieldType;
  inputLength: number;
  outputLength: number;
  elapsedMs: number;
  model?: RpcModel;
  degraded?: boolean;
  warnings?: FieldAssistWarning[];
}

export interface FieldAssistResult {
  value: string;
  metadata: FieldAssistMetadata;
}

type PiCompactionEndEvent = Extract<
  AgentSessionEvent,
  { type: "compaction_end" }
>;
type PiCompactionResult = PiRpcResponseData<"compact">;
type PiAgentEndEvent = Extract<AgentSessionEvent, { type: "agent_end" }>;
type PiAgentMessage = PiAgentEndEvent["messages"][number];
type PiAgentUserMessage = Extract<PiAgentMessage, { role: "user" }>;
type PiAgentAssistantMessage = Extract<PiAgentMessage, { role: "assistant" }>;
type PiAgentToolResultMessage = Extract<PiAgentMessage, { role: "toolResult" }>;
type PiAgentUserContentBlock = Exclude<
  PiAgentUserMessage["content"],
  string
>[number];
type PiAgentAssistantContentBlock = PiAgentAssistantMessage["content"][number];
type PiAgentToolResultContentBlock = PiAgentToolResultMessage["content"][number];

export type RpcCompactionResult = Pick<
  PiCompactionResult,
  "summary" | "firstKeptEntryId" | "tokensBefore" | "details"
>;

export type RpcBashResult = PiRpcResponseData<"bash">;

export type RpcAgentTextContent = Pick<
  Extract<PiAgentUserContentBlock, { type: "text" }>,
  "type" | "text" | "textSignature"
>;

export type RpcAgentThinkingContent = Pick<
  Extract<PiAgentAssistantContentBlock, { type: "thinking" }>,
  "type" | "thinking" | "thinkingSignature" | "redacted"
>;

export type RpcAgentToolCall = Pick<
  Extract<PiAgentAssistantContentBlock, { type: "toolCall" }>,
  "type" | "id" | "name" | "thoughtSignature"
> & {
  arguments: RpcJsonObject;
  questionRequest?: AskUserQuestionCardRequest;
  questionState?: AskUserQuestionLifecycleState;
  questionError?: AskUserQuestionErrorProjection;
  formInteraction?: FormInteractionProjection;
};

export type RpcAgentUsageCost = Pick<
  PiAgentAssistantMessage["usage"]["cost"],
  "input" | "output" | "cacheRead" | "cacheWrite" | "total"
>;

export type RpcAgentUsage = Pick<
  PiAgentAssistantMessage["usage"],
  "input" | "output" | "cacheRead" | "cacheWrite" | "totalTokens"
> & { cost: RpcAgentUsageCost };

export type RpcAgentStopReason = PiAgentAssistantMessage["stopReason"];

export type RpcAgentUserMessage = Pick<
  PiAgentUserMessage,
  "role" | "timestamp"
> & {
  content: string | Array<RpcAgentTextContent | RpcImageContent>;
};

export type RpcAgentAssistantMessage = Pick<
  PiAgentAssistantMessage,
  | "role"
  | "api"
  | "provider"
  | "model"
  | "responseId"
  | "stopReason"
  | "errorMessage"
  | "timestamp"
> & {
  content: Array<
    RpcAgentTextContent | RpcAgentThinkingContent | RpcAgentToolCall
  >;
  usage: RpcAgentUsage;
};

export type RpcAgentToolResultMessage = Pick<
  PiAgentToolResultMessage,
  "role" | "toolCallId" | "toolName" | "isError" | "timestamp"
> & {
  content: Array<RpcAgentTextContent | RpcImageContent>;
  details?: unknown;
};

export type RpcAgentMessage =
  | RpcAgentUserMessage
  | RpcAgentAssistantMessage
  | RpcAgentToolResultMessage;

export type RpcAgentStartEvent = Pick<
  Extract<AgentSessionEvent, { type: "agent_start" }>,
  "type"
> & { sessionPath?: string };

export type RpcAgentEndEvent = Pick<PiAgentEndEvent, "type"> & {
  sessionPath?: string;
  messages?: RpcAgentMessage[];
};

export type RpcAutoRetryStartEvent = Pick<
  Extract<AgentSessionEvent, { type: "auto_retry_start" }>,
  "type" | "attempt" | "maxAttempts" | "delayMs"
> & {
  sessionPath?: string;
};

export interface RpcModelSelectEvent {
  type: "model_select";
  model: RpcModel;
  previousModel?: RpcModel;
  source: "set" | "cycle" | "restore";
}

export type RpcCompactionReason = Extract<
  AgentSessionEvent,
  { type: "compaction_start" }
>["reason"];

export type RpcCompactionStartEvent = Pick<
  Extract<AgentSessionEvent, { type: "compaction_start" }>,
  "type" | "reason"
>;

export type RpcCompactionEndEvent = Pick<
  PiCompactionEndEvent,
  "type" | "reason" | "aborted" | "willRetry" | "errorMessage"
> & {
  result: RpcCompactionResult | null;
};

export interface RpcCommandMap {
  /** Prompting */
  prompt: PiRpcCommandPayload<"prompt"> & {
    files?: RpcUploadedFileRef[];
  };
  steer: PiRpcCommandPayload<"steer"> & {
    files?: RpcUploadedFileRef[];
  };
  follow_up: PiRpcCommandPayload<"follow_up"> & {
    files?: RpcUploadedFileRef[];
  };
  abort: PiRpcCommandPayload<"abort">;
  field_assist: FieldAssistCommandPayload;
  present_question: { toolCallId: string };
  answer_question:
    | { toolCallId: string; cancelled: true; expectedRevision?: number }
    | {
        toolCallId: string;
        cancelled: false;
        expectedRevision?: number;
        answer:
          | AskUserQuestionAnswerInput
          | Record<string, AskUserQuestionAnswerInput>;
      };
  revise_question: { toolCallId: string; expectedRevision: number };
  cancel_question_revision: { toolCallId: string; expectedRevision: number };
  submit_question_revision: {
    toolCallId: string;
    expectedRevision: number;
    answers: Record<string, Record<string, AskUserQuestionAnswerInput>>;
  };
  new_session: PiRpcCommandPayload<"new_session"> & {
    limit?: number;
    workspacePath?: string;
  };
  register_workspace: {
    workspacePath?: string;
  };

  /** State */
  get_state: PiRpcCommandPayload<"get_state">;

  /** Model */
  set_model: PiRpcCommandPayload<"set_model">;
  cycle_model: PiRpcCommandPayload<"cycle_model">;
  get_available_models: PiRpcCommandPayload<"get_available_models">;

  /** Thinking */
  set_thinking_level: Omit<
    PiRpcCommandPayload<"set_thinking_level">,
    "level"
  > & { level: RpcThinkingLevel };
  cycle_thinking_level: PiRpcCommandPayload<"cycle_thinking_level">;

  /** Queue modes */
  set_steering_mode: PiRpcCommandPayload<"set_steering_mode">;
  set_follow_up_mode: PiRpcCommandPayload<"set_follow_up_mode">;

  /** Compaction */
  compact: PiRpcCommandPayload<"compact">;
  set_auto_compaction: PiRpcCommandPayload<"set_auto_compaction">;

  /** Retry */
  set_auto_retry: PiRpcCommandPayload<"set_auto_retry">;
  abort_retry: PiRpcCommandPayload<"abort_retry">;

  /** Bash */
  bash: Pick<PiRpcCommandPayload<"bash">, "command">;
  abort_bash: PiRpcCommandPayload<"abort_bash">;

  /** Session */
  export_html: PiRpcCommandPayload<"export_html">;
  set_session_name: PiRpcCommandPayload<"set_session_name">;
  switch_session: PiRpcCommandPayload<"switch_session"> & { limit?: number };
  select_tree_entry: { entryId: string };
  navigate_tree: {
    entryId: string;
    summarize?: boolean;
    customInstructions?: string;
    replaceInstructions?: boolean;
    label?: string;
  };
  fork: PiRpcCommandPayload<"fork">;
  get_fork_messages: PiRpcCommandPayload<"get_fork_messages">;
  get_last_assistant_text: PiRpcCommandPayload<"get_last_assistant_text">;
  delete_session: { sessionPath: string };

  /** Messages / Commands */
  get_messages: {
    sessionPath?: string;
    direction?: "latest" | "older";
    cursor?: string;
    limit?: number;
  };
  get_commands: PiRpcCommandPayload<"get_commands">;

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

export type RpcSessionState = Omit<
  PiRpcSessionState,
  "model" | "thinkingLevel"
> & {
  model?: RpcModel;
  thinkingLevel: RpcThinkingLevel;
  workspacePath?: string;
  workspaceEnvironments?: RpcWorkspaceEnvironment[];
  gitBranch?: string;
};

/** A command available for invocation via prompt. */
export type RpcSlashCommand = Pick<
  PiRpcSlashCommand,
  "name" | "description" | "source"
>;

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

export interface RpcTranscriptFileBlock {
  type: "file";
  id?: string;
  name: string;
  size?: number;
  mimeType?: string;
  path: string;
  relativePath?: string;
  previewUrl?: string;
}

export interface RpcTranscriptToolCallBlock {
  type: "toolCall";
  id?: string;
  name?: string;
  arguments?: RpcToolArguments;
  questionRequest?: AskUserQuestionCardRequest;
  questionState?: AskUserQuestionLifecycleState;
  questionError?: AskUserQuestionErrorProjection;
  formInteraction?: FormInteractionProjection;
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
  | RpcTranscriptFileBlock
  | RpcTranscriptToolCallBlock
  | RpcTranscriptToolResultBlock
  | RpcTranscriptSystemBlock;

export type RpcTranscriptContent =
  | string
  | Array<string | RpcTranscriptContentBlock>;

export const DANO_LLM_TIMEOUT_ERROR = "DANO_LLM_TIMEOUT";
export const DANO_LLM_AUTHENTICATION_ERROR = "DANO_LLM_AUTHENTICATION";
export const DANO_LLM_RATE_LIMIT_ERROR = "DANO_LLM_RATE_LIMIT";
export const DANO_LLM_QUOTA_ERROR = "DANO_LLM_QUOTA";
export const DANO_LLM_SERVICE_ERROR = "DANO_LLM_SERVICE";
export const DANO_LLM_NETWORK_ERROR = "DANO_LLM_NETWORK";
export const DANO_LLM_INCOMPLETE_ERROR = "DANO_LLM_INCOMPLETE";
export const DANO_LLM_UNKNOWN_ERROR = "DANO_LLM_UNKNOWN";
export const DANO_SESSION_PERSISTENCE_ERROR = "DANO_SESSION_PERSISTENCE";

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

export interface RpcCommandErrorEvent {
  type: "command_error";
  commandType: string;
  correlationId?: string;
  error: string;
}

// ============================================================================
// RPC Responses (server → client)
// ============================================================================

/** Map of RPC command types to their success response data shapes. */
export interface RpcResponseMap {
  prompt: PiRpcResponseData<"prompt">;
  steer: PiRpcResponseData<"steer">;
  follow_up: PiRpcResponseData<"follow_up">;
  abort: PiRpcResponseData<"abort">;
  field_assist: FieldAssistResult;
  present_question: FormInteractionProjection | null;
  answer_question: AskUserQuestionResult;
  revise_question: FormInteractionProjection;
  cancel_question_revision: FormInteractionProjection;
  submit_question_revision: FormInteractionProjection;
  new_session: PiRpcResponseData<"new_session"> & {
    transcript: RpcTranscriptPage;
    treeEntries: RpcTreeEntry[];
    model?: RpcModel;
    thinkingLevel: RpcThinkingLevel;
    sessionId: string;
    sessionName: string;
    sessionPath: string;
    workspacePath?: string;
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
  cycle_model:
    | (Omit<
        NonNullable<PiRpcResponseData<"cycle_model">>,
        "model" | "thinkingLevel"
      > & {
        model: RpcModel;
        thinkingLevel: RpcThinkingLevel;
      })
    | null;
  get_available_models: Omit<
    PiRpcResponseData<"get_available_models">,
    "models"
  > & { models: RpcModel[] };
  set_thinking_level: PiRpcResponseData<"set_thinking_level">;
  cycle_thinking_level:
    | (Omit<
        NonNullable<PiRpcResponseData<"cycle_thinking_level">>,
        "level"
      > & { level: RpcThinkingLevel })
    | null;
  set_steering_mode: PiRpcResponseData<"set_steering_mode">;
  set_follow_up_mode: PiRpcResponseData<"set_follow_up_mode">;
  compact: RpcCompactionResult;
  set_auto_compaction: PiRpcResponseData<"set_auto_compaction">;
  set_auto_retry: PiRpcResponseData<"set_auto_retry">;
  abort_retry: PiRpcResponseData<"abort_retry">;
  bash: RpcBashResult;
  abort_bash: PiRpcResponseData<"abort_bash">;
  export_html: PiRpcResponseData<"export_html">;
  switch_session: PiRpcResponseData<"switch_session"> & {
    transcript: RpcTranscriptPage;
    treeEntries: RpcTreeEntry[];
    sessionId: string;
    sessionName: string;
    sessionPath: string;
    workspacePath?: string;
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
  fork: PiRpcResponseData<"fork">;
  get_fork_messages: PiRpcResponseData<"get_fork_messages">;
  get_last_assistant_text: PiRpcResponseData<"get_last_assistant_text">;
  delete_session: void;
  set_session_name: PiRpcResponseData<"set_session_name">;
  get_messages: RpcTranscriptPage & { direction: "latest" | "older" };
  get_commands: Omit<PiRpcResponseData<"get_commands">, "commands"> & {
    commands: RpcSlashCommand[];
  };
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
      data?: unknown;
    };

// ============================================================================
// Extension UI (routed over HTTP/SSE)
// ============================================================================

/** Existing in-process Extension UI bridge, typed from Pi's root contract. */
export type RpcExtensionUIRequest = PiRpcExtensionUIRequest;
export type RpcExtensionUIResponse = PiRpcExtensionUIResponse;

// ============================================================================
// Bridge Configuration
// ============================================================================

/** Configuration for the bridge server, sourced from extension config or defaults. */
export type BridgeEmptyStateMode = "text" | "html";

export interface BridgeEmptyStateConfig {
  /** Render mode for the browser empty transcript area. */
  readonly mode: BridgeEmptyStateMode;
  /** Content rendered in the empty transcript area. Supports {产品名称}. */
  readonly content: string;
}

export interface BridgeQuickActionConfig {
  readonly label: string;
  readonly prompt: string;
}

/** Browser-safe projection of the server-authenticated User. */
export interface BridgeUserSummary {
  /** Opaque canonical Dano User ID; display data must never replace it. */
  readonly id: string;
  readonly username: string;
  readonly avatarUrl?: string;
}

export const BRIDGE_LOGIN_ERROR_CODES = [
  "authorization_invalid",
  "provider_unavailable",
  "provider_identity_invalid",
  "login_configuration_error",
  "login_session_failed",
  "user_data_transfer_failed",
  "login_failed",
] as const;

export type BridgeLoginErrorCode = (typeof BRIDGE_LOGIN_ERROR_CODES)[number];

/** Recoverable, browser-safe failure from the latest login attempt. */
export interface BridgeLoginError {
  readonly code: BridgeLoginErrorCode;
}

/** Browser-safe authentication state for the current Dano client. */
export type BridgeAuthenticationState =
  | {
      readonly status: "anonymous";
      readonly loginError?: BridgeLoginError;
    }
  | {
      readonly status: "reauth_required";
      readonly loginError?: BridgeLoginError;
    }
  | {
      readonly status: "authenticated";
      readonly user: BridgeUserSummary;
      readonly loginError?: BridgeLoginError;
    };

/** Stable keys accepted by the server-owned Theme Color preference. */
export const ACCENT_COLOR_PRESET_KEYS = [
  "default",
  "blue",
  "gray",
  "yellow",
  "pink",
  "purple",
] as const;

export type AccentColorPreset = (typeof ACCENT_COLOR_PRESET_KEYS)[number];

export const DEFAULT_ACCENT_COLOR_PRESET: AccentColorPreset = "default";

export interface BridgeThemeColorPreference {
  readonly accentColorPreset: AccentColorPreset;
}

/** JSON-safe configuration injected by the Dano server into the browser page. */
export interface BridgeBrowserRuntimeConfig {
  readonly debugModeAvailable?: boolean;
  readonly locale?: "zh-CN" | "en-US";
  readonly productName?: string;
  readonly emptyState?: Partial<BridgeEmptyStateConfig>;
  readonly quickActions?: readonly BridgeQuickActionConfig[];
  readonly slashCommandsAndMentionsEnabled?: boolean;
  readonly transcriptProcessSummaryEnabled?: boolean;
}

// ============================================================================
// Wire Protocol (JSON over HTTP/SSE)
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
  | RpcAutoRetryStartEvent
  | RpcModelSelectEvent
  | RpcCompactionStartEvent
  | RpcCompactionEndEvent
  | RpcCommandErrorEvent
  | {
      type: "heartbeat";
      serverInstanceId: string;
      serverStartTime: string;
    }
  | { type: "session_compact" };

export type ServerMessage =
  | { type: "event"; payload: RpcBridgeEvent }
  | { type: "authentication"; payload: BridgeAuthenticationState }
  | { type: "extension_ui_request"; payload: RpcExtensionUIRequest }
  | { type: "response"; payload: RpcResponse };

/** Envelope for messages sent from browser client → server. */
export type ClientMessage =
  | { type: "command"; payload: RpcCommand }
  | { type: "extension_ui_response"; payload: RpcExtensionUIResponse };
