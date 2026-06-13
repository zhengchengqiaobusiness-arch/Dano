export type ConversationStatus = "active" | "answering" | "failed" | "closed";

export type ChatRole = "user" | "assistant" | "system";

export type MessageStatus = "pending" | "streaming" | "completed" | "failed";

export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };

export type JsonObject = { [key: string]: JsonValue };

export type ToolArguments = string | JsonObject;

export type ToolBlockStatus = "pending" | "success" | "error";

export interface TextContentBlock {
  kind: "text";
  text: string;
}

export interface ThinkingContentBlock {
  kind: "thinking";
  text: string;
}

export interface ToolContentBlock {
  kind: "tool";
  toolName: string;
  toolCallId?: string;
  toolArgs?: ToolArguments;
  argumentsText: string;
  resultText?: string;
  resultDetails?: JsonValue;
  toolStatus: ToolBlockStatus;
}

export type ChatContentBlock =
  | TextContentBlock
  | ThinkingContentBlock
  | ToolContentBlock;

export interface ChatMessage {
  id: string;
  conversationId: string;
  role: ChatRole;
  content: string;
  contentBlocks?: ChatContentBlock[];
  status: MessageStatus;
  createdAt: string;
  completedAt?: string;
  clientMessageId?: string;
  retryOfMessageId?: string;
  errorCode?: FailureCode;
  errorMessage?: string;
}

export interface Conversation {
  id: string;
  createdAt: string;
  updatedAt: string;
  status: ConversationStatus;
  messages: ChatMessage[];
}

export interface CreateConversationResponse {
  conversationId: string;
  eventsUrl: string;
}

export interface SendMessageRequest {
  clientMessageId?: string;
  text?: string;
}

export interface SendMessageResponse {
  conversationId: string;
  messageId: string;
  status: "accepted";
}

export type FailureCode =
  | "EMPTY_MESSAGE"
  | "MESSAGE_TOO_LONG"
  | "CONVERSATION_BUSY"
  | "CONVERSATION_NOT_FOUND"
  | "MESSAGE_NOT_FOUND"
  | "MESSAGE_NOT_RETRYABLE"
  | "LLM_UNAVAILABLE"
  | "LLM_TIMEOUT"
  | "INVALID_RESPONSE"
  | "CONNECTION_INTERRUPTED";

export interface ApiErrorResponse {
  code: FailureCode;
  errorMessage: string;
}

export type SseEventName =
  | "conversation.ready"
  | "message.accepted"
  | "assistant.started"
  | "assistant.delta"
  | "assistant.blocks"
  | "assistant.completed"
  | "message.failed"
  | "heartbeat";

export interface SseEvent<TData = unknown> {
  id?: number;
  event: SseEventName;
  data: TData;
}

export interface RuntimeFailure {
  code: Exclude<FailureCode, "EMPTY_MESSAGE" | "CONVERSATION_NOT_FOUND">;
  errorMessage: string;
  retryable: boolean;
}

export interface RuntimeCallbacks {
  onDelta(delta: string): void;
  onContentBlocks(blocks: ChatContentBlock[]): void;
  onComplete(content: string): void;
  onFailure(failure: RuntimeFailure): void;
}

export interface ServerLlmRuntime {
  sendUserMessage(text: string, callbacks: RuntimeCallbacks): Promise<void>;
  dispose?(): Promise<void> | void;
}

export type ServerLlmRuntimeFactory = (
  conversationId: string,
) => ServerLlmRuntime | Promise<ServerLlmRuntime>;

export interface ChatServerConfig {
  host: string;
  port: number;
  staticDir?: string;
  cwd: string;
  sessionDir?: string;
  heartbeatMs: number;
}

export const DEFAULT_CHAT_SERVER_CONFIG: ChatServerConfig = {
  host: "127.0.0.1",
  port: 8080,
  cwd: process.cwd(),
  heartbeatMs: 15_000,
};
