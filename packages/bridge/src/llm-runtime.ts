import * as path from "node:path";
import {
  SessionManager,
  createAgentSessionFromServices,
  createAgentSessionServices,
  createBashToolDefinition,
  createEditToolDefinition,
  type AgentSession,
  type AgentSessionEvent,
  type ToolDefinition,
  createReadToolDefinition,
  createWriteToolDefinition,
} from "@earendil-works/pi-coding-agent";
import { createHeadlessUIContext } from "./headless-ui-context.js";
import type {
  ChatContentBlock,
  JsonObject,
  JsonValue,
  RuntimeCallbacks,
  RuntimeFailure,
  ServerLlmRuntime,
  ServerLlmRuntimeFactory,
  ToolArguments,
  ToolContentBlock,
} from "./types.js";

export interface PiCodingAgentRuntimeOptions {
  cwd: string;
  sessionDir?: string;
  timeoutMs?: number;
}

type PromptableSession = AgentSession & {
  prompt?: (
    message: string,
    options?: { source?: string; streamingBehavior?: "steer" | "followUp" },
  ) => Promise<void>;
  sendUserMessage?: (
    message: string,
    options: { deliverAs: "steer" | "followUp" },
  ) => Promise<void> | void;
  dispose?: () => void;
};

type SettingsManagerWithToolSettings = {
  getShellCommandPrefix?: () => string | undefined;
  getImageAutoResize?: () => boolean;
};

function normalizeOptionalText(value: string | undefined): string | undefined {
  const trimmed = value?.trim();
  return trimmed || undefined;
}

function runtimeFailure(
  code: RuntimeFailure["code"],
  errorMessage: string,
  retryable = true,
): RuntimeFailure {
  return { code, errorMessage, retryable };
}

function failureFromError(error: unknown): RuntimeFailure {
  const message = error instanceof Error ? error.message : String(error);
  const normalized = message.trim();
  return runtimeFailure(
    "LLM_UNAVAILABLE",
    normalized || "The assistant is unavailable.",
    true,
  );
}

function eventMessage(event: AgentSessionEvent): Record<string, unknown> | null {
  if (!event || typeof event !== "object") {
    return null;
  }

  const data = event as { message?: unknown; role?: unknown };
  if (data.message && typeof data.message === "object") {
    return data.message as Record<string, unknown>;
  }

  if (typeof data.role === "string") {
    return event as unknown as Record<string, unknown>;
  }

  return null;
}

function isAssistantMessage(
  message: Record<string, unknown> | null,
): message is Record<string, unknown> {
  return message?.role === "assistant";
}

function isToolResultMessage(
  message: Record<string, unknown> | null,
): message is Record<string, unknown> {
  return message?.role === "toolResult" || message?.role === "tool";
}

function textFromContentItem(item: unknown): string {
  if (typeof item === "string") {
    return item;
  }

  if (!item || typeof item !== "object") {
    return "";
  }

  const data = item as {
    type?: unknown;
    text?: unknown;
    content?: unknown;
  };

  if (data.type === "text" && typeof data.text === "string") {
    return data.text;
  }

  if (typeof data.text === "string") {
    return data.text;
  }

  if (typeof data.content === "string") {
    return data.content;
  }

  return "";
}

function isJsonObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function jsonValue(value: unknown): JsonValue | undefined {
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  ) {
    return value;
  }

  if (Array.isArray(value)) {
    const items = value
      .map(item => jsonValue(item))
      .filter((item): item is JsonValue => item !== undefined);
    return items;
  }

  if (typeof value === "object" && value) {
    const record: JsonObject = {};
    for (const [key, item] of Object.entries(value)) {
      const normalized = jsonValue(item);
      if (normalized !== undefined) {
        record[key] = normalized;
      }
    }
    return record;
  }

  return undefined;
}

function toolArguments(value: unknown): ToolArguments | undefined {
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) {
      return "";
    }
    try {
      const parsed = JSON.parse(trimmed) as unknown;
      return isJsonObject(parsed) ? parsed : value;
    } catch {
      return value;
    }
  }

  return isJsonObject(value) ? value : undefined;
}

function toolArgumentsText(value: ToolArguments | undefined): string {
  if (typeof value === "string") {
    return value;
  }

  return JSON.stringify(value ?? "", null, 2);
}

function toolResultText(message: Record<string, unknown>): string {
  if (typeof message.text === "string") {
    return message.text;
  }

  if (typeof message.content === "string") {
    return message.content;
  }

  if (Array.isArray(message.content)) {
    return message.content
      .map(item => textFromContentItem(item))
      .filter(Boolean)
      .join("\n");
  }

  return "";
}

function toolResultFromMessage(message: Record<string, unknown>): {
  toolCallId?: string;
  toolName?: string;
  resultText: string;
  resultDetails?: JsonValue;
  isError?: boolean;
} {
  return {
    toolCallId:
      typeof message.toolCallId === "string" ? message.toolCallId : undefined,
    toolName: typeof message.toolName === "string" ? message.toolName : undefined,
    resultText: toolResultText(message),
    resultDetails: jsonValue(message.details),
    isError: message.isError === true,
  };
}

function toolResultFromExecutionEvent(event: AgentSessionEvent): {
  toolCallId?: string;
  toolName?: string;
  resultText: string;
  resultDetails?: JsonValue;
  isError?: boolean;
} | null {
  const data = event as Record<string, unknown>;
  if (
    data.type !== "tool_execution_update" &&
    data.type !== "tool_execution_end"
  ) {
    return null;
  }

  const result =
    data.type === "tool_execution_update" ? data.partialResult : data.result;
  const resultText =
    typeof result === "string" ? result : result ? JSON.stringify(result, null, 2) : "";

  return {
    toolCallId:
      typeof data.toolCallId === "string" ? data.toolCallId : undefined,
    toolName: typeof data.toolName === "string" ? data.toolName : undefined,
    resultText,
    resultDetails: jsonValue(result),
    isError: data.isError === true,
  };
}

function contentBlocksFromAssistantMessage(
  message: Record<string, unknown>,
  toolResults: Map<string, ReturnType<typeof toolResultFromMessage>>,
): ChatContentBlock[] {
  if (!Array.isArray(message.content)) {
    const text = visibleAssistantText(message);
    return text ? [{ kind: "text", text }] : [];
  }

  return message.content.flatMap((item): ChatContentBlock[] => {
    if (typeof item === "string") {
      return item ? [{ kind: "text", text: item }] : [];
    }
    if (!item || typeof item !== "object") {
      return [];
    }

    const block = item as Record<string, unknown>;
    if (block.type === "text") {
      const text = typeof block.text === "string" ? block.text : "";
      return text ? [{ kind: "text", text }] : [];
    }
    if (block.type !== "toolCall") {
      return [];
    }

    const toolCallId = typeof block.id === "string" ? block.id : undefined;
    const result = toolCallId ? toolResults.get(toolCallId) : undefined;
    const args = toolArguments(block.arguments);
    const toolBlock: ToolContentBlock = {
      kind: "tool",
      toolName: typeof block.name === "string" ? block.name : "unknown",
      ...(toolCallId ? { toolCallId } : {}),
      ...(args !== undefined ? { toolArgs: args } : {}),
      argumentsText: toolArgumentsText(args),
      ...(result?.resultText ? { resultText: result.resultText } : {}),
      ...(result?.resultDetails !== undefined
        ? { resultDetails: result.resultDetails }
        : {}),
      toolStatus: result ? (result.isError ? "error" : "success") : "pending",
    };

    return [toolBlock];
  });
}

function blocksChanged(
  previous: ChatContentBlock[],
  next: ChatContentBlock[],
): boolean {
  return JSON.stringify(previous) !== JSON.stringify(next);
}

function contentTextFromBlocks(blocks: ChatContentBlock[]): string {
  return blocks
    .flatMap(block => (block.kind === "text" ? [block.text] : []))
    .join("");
}

function mergeContentBlocks(
  previous: ChatContentBlock[],
  next: ChatContentBlock[],
): ChatContentBlock[] {
  if (next.length === 0) {
    return previous;
  }

  const merged = [...previous];
  for (const block of next) {
    if (block.kind === "text") {
      if (!block.text) {
        continue;
      }
      const last = merged.at(-1);
      if (last?.kind === "text") {
        merged[merged.length - 1] = block;
      } else {
        merged.push(block);
      }
      continue;
    }

    const existingIndex = block.toolCallId
      ? merged.findIndex(
          item => item.kind === "tool" && item.toolCallId === block.toolCallId,
        )
      : -1;
    if (existingIndex === -1) {
      merged.push(block);
    } else {
      merged[existingIndex] = block;
    }
  }

  return merged;
}

function contentBlocksWithToolResult(
  blocks: ChatContentBlock[],
  result: ReturnType<typeof toolResultFromMessage>,
): ChatContentBlock[] {
  if (!result.toolCallId) {
    return blocks;
  }

  return blocks.map(block => {
    if (block.kind !== "tool" || block.toolCallId !== result.toolCallId) {
      return block;
    }

    return {
      ...block,
      ...(result.toolName ? { toolName: result.toolName } : {}),
      ...(result.resultText ? { resultText: result.resultText } : {}),
      ...(result.resultDetails !== undefined
        ? { resultDetails: result.resultDetails }
        : {}),
      toolStatus: result.isError ? "error" : "success",
    };
  });
}

function hasToolBlocks(blocks: ChatContentBlock[]): boolean {
  return blocks.some(block => block.kind === "tool");
}

function visibleAssistantText(message: Record<string, unknown> | null): string {
  if (!message) {
    return "";
  }

  if (typeof message.text === "string") {
    return message.text;
  }

  if (typeof message.content === "string") {
    return message.content;
  }

  if (Array.isArray(message.content)) {
    return message.content
      .map(item => textFromContentItem(item))
      .filter(Boolean)
      .join("");
  }

  return "";
}

function assistantDeltaFromEvent(
  event: AgentSessionEvent,
  message: Record<string, unknown>,
  previousText: string,
): string {
  const data = event as {
    assistantMessageEvent?: {
      type?: unknown;
      delta?: unknown;
    };
  };

  if (
    data.assistantMessageEvent?.type === "text_delta" &&
    typeof data.assistantMessageEvent.delta === "string"
  ) {
    const fullText = visibleAssistantText(message);
    if (fullText && fullText.startsWith(previousText)) {
      const synthesizedDelta = fullText.slice(previousText.length);
      return synthesizedDelta || "";
    }
    return data.assistantMessageEvent.delta;
  }

  const nextText = visibleAssistantText(message);
  if (!nextText || !nextText.startsWith(previousText)) {
    return "";
  }

  return nextText.slice(previousText.length);
}

function messageError(message: Record<string, unknown> | null): string | null {
  if (typeof message?.errorMessage === "string" && message.errorMessage.trim()) {
    return message.errorMessage.trim();
  }
  return null;
}

function latestAssistantTextFromMessages(messages: unknown): string {
  if (!Array.isArray(messages)) {
    return "";
  }

  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const raw = messages[index];
    const message =
      raw && typeof raw === "object" && "message" in raw
        ? (raw as { message?: unknown }).message
        : raw;
    if (!message || typeof message !== "object") {
      continue;
    }
    const data = message as Record<string, unknown>;
    if (data.role !== "assistant") {
      continue;
    }
    const text = visibleAssistantText(data);
    if (text.trim()) {
      return text;
    }
  }

  return "";
}

export class PiCodingAgentRuntime implements ServerLlmRuntime {
  private sessionPromise: Promise<PromptableSession> | undefined;

  constructor(private readonly options: PiCodingAgentRuntimeOptions) {}

  async sendUserMessage(
    text: string,
    callbacks: RuntimeCallbacks,
  ): Promise<void> {
    const session = await this.ensureSession();
    let assistantText = "";
    let assistantBlocks: ChatContentBlock[] = [];
    let settled = false;
    let timeoutId: ReturnType<typeof setTimeout> | undefined;
    let resolveTurn: (() => void) | undefined;
    const toolResults = new Map<
      string,
      ReturnType<typeof toolResultFromMessage>
    >();
    const turnDone = new Promise<void>(resolve => {
      resolveTurn = resolve;
    });

    const emitBlocks = (blocks: ChatContentBlock[]) => {
      const nextBlocks = mergeContentBlocks(assistantBlocks, blocks);
      if (!hasToolBlocks(nextBlocks) && assistantBlocks.length === 0) {
        return;
      }
      if (!blocksChanged(assistantBlocks, nextBlocks)) {
        return;
      }

      assistantBlocks = nextBlocks;
      callbacks.onContentBlocks(nextBlocks);
    };

    const finish = (content: string) => {
      if (settled) {
        return;
      }
      if (
        assistantBlocks.length > 0 &&
        content.trim() &&
        contentTextFromBlocks(assistantBlocks) !== content
      ) {
        emitBlocks([{ kind: "text", text: content }]);
      }
      settled = true;
      callbacks.onComplete(content);
      resolveTurn?.();
    };

    const fail = (failure: RuntimeFailure) => {
      if (settled) {
        return;
      }
      settled = true;
      callbacks.onFailure(failure);
      resolveTurn?.();
    };

    const unsubscribe = session.subscribe(event => {
      const message = eventMessage(event);
      const executionResult = toolResultFromExecutionEvent(event);

      if (executionResult?.toolCallId) {
        toolResults.set(executionResult.toolCallId, executionResult);
        emitBlocks(contentBlocksWithToolResult(assistantBlocks, executionResult));
        return;
      }

      if (event.type === "message_update" && isAssistantMessage(message)) {
        const delta = assistantDeltaFromEvent(event, message, assistantText);
        if (delta) {
          assistantText += delta;
          callbacks.onDelta(delta);
        }
        const blocks = contentBlocksFromAssistantMessage(message, toolResults);
        if (hasToolBlocks(blocks)) {
          emitBlocks(blocks);
        }
        return;
      }

      if (event.type === "message_end" && isAssistantMessage(message)) {
        const blocks = contentBlocksFromAssistantMessage(message, toolResults);
        emitBlocks(blocks);
        if (message.stopReason === "toolUse") {
          return;
        }
        const errorMessage = messageError(message);
        if (errorMessage) {
          fail(runtimeFailure("INVALID_RESPONSE", errorMessage, true));
          return;
        }
        finish(visibleAssistantText(message) || assistantText);
        return;
      }

      if (event.type === "message_end" && isToolResultMessage(message)) {
        const result = toolResultFromMessage(message);
        if (result.toolCallId) {
          toolResults.set(result.toolCallId, result);
          emitBlocks(contentBlocksWithToolResult(assistantBlocks, result));
        }
        return;
      }

      if (event.type === "agent_end") {
        const messages = (event as { messages?: unknown }).messages;
        const finalText = latestAssistantTextFromMessages(messages);
        if (finalText && !settled) {
          finish(finalText);
          return;
        }
        if (assistantBlocks.length > 0 && !settled) {
          finish(assistantText);
        }
      }
    });

    const timeoutMs = this.options.timeoutMs ?? 30_000;
    timeoutId = setTimeout(() => {
      fail(
        runtimeFailure(
          "LLM_TIMEOUT",
          "The assistant did not answer in time.",
          true,
        ),
      );
    }, timeoutMs);

    try {
      void this.prompt(session, text).then(
        () => {
          if (!settled) {
            const fallback = latestAssistantTextFromMessages(
              session.sessionManager.getBranch(),
            );
            if (fallback) {
              finish(fallback);
            }
          }
        },
        error => {
          fail(failureFromError(error));
        },
      );

      await turnDone;
    } finally {
      if (timeoutId) {
        clearTimeout(timeoutId);
      }
      unsubscribe();
    }
  }

  async dispose(): Promise<void> {
    const session = await this.sessionPromise?.catch(() => undefined);
    session?.dispose?.();
  }

  private async ensureSession(): Promise<PromptableSession> {
    this.sessionPromise ??= this.createSession();
    return this.sessionPromise;
  }

  private async createSession(): Promise<PromptableSession> {
    const cwd = this.options.cwd.trim() || process.cwd();
    const sessionDir = this.options.sessionDir
      ? path.join(this.options.sessionDir, "sessions")
      : undefined;
    const sessionManager = SessionManager.create(cwd, sessionDir);
    const services = await createAgentSessionServices({ cwd });
    const settingsManager =
      services.settingsManager as SettingsManagerWithToolSettings;
    const shellCommandPrefix = normalizeOptionalText(
      settingsManager.getShellCommandPrefix?.(),
    );
    const result = await createAgentSessionFromServices({
      services,
      sessionManager,
      customTools: [
        createReadToolDefinition(cwd, {
          autoResizeImages: settingsManager.getImageAutoResize?.() ?? false,
        }),
        createBashToolDefinition(cwd, {
          commandPrefix: shellCommandPrefix,
        }),
        createEditToolDefinition(cwd),
        createWriteToolDefinition(cwd),
      ] as unknown as ToolDefinition[],
    });

    const session = result.session as PromptableSession;
    await session.bindExtensions({
      uiContext: createHeadlessUIContext(),
      onError: error => {
        console.error(
          `[dano] Runtime extension error (${error.extensionPath}):`,
          error.error,
        );
      },
      shutdownHandler: () => {},
    });

    return session;
  }

  private async prompt(session: PromptableSession, text: string): Promise<void> {
    if (typeof session.prompt === "function") {
      await session.prompt(text, { source: "http-sse" });
      return;
    }

    if (typeof session.sendUserMessage === "function") {
      await session.sendUserMessage(text, { deliverAs: "followUp" });
      return;
    }

    throw new Error("The server LLM runtime cannot accept chat messages.");
  }
}

export function createPiCodingAgentRuntimeFactory(
  options: PiCodingAgentRuntimeOptions,
): ServerLlmRuntimeFactory {
  return conversationId =>
    new PiCodingAgentRuntime({
      ...options,
      sessionDir: options.sessionDir
        ? path.join(options.sessionDir, conversationId)
        : undefined,
    });
}
