import * as path from "node:path";
import {
  SessionManager,
  createAgentSessionFromServices,
  createAgentSessionServices,
  type AgentSession,
  type AgentSessionEvent,
  type ToolDefinition,
} from "@earendil-works/pi-coding-agent";
import { createHeadlessUIContext } from "./headless-ui-context.js";
import type {
  RuntimeCallbacks,
  RuntimeFailure,
  ServerLlmRuntime,
  ServerLlmRuntimeFactory,
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
    let settled = false;
    let timeoutId: ReturnType<typeof setTimeout> | undefined;
    let resolveTurn: (() => void) | undefined;
    const turnDone = new Promise<void>(resolve => {
      resolveTurn = resolve;
    });

    const finish = (content: string) => {
      if (settled) {
        return;
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

      if (event.type === "message_update" && isAssistantMessage(message)) {
        const delta = assistantDeltaFromEvent(event, message, assistantText);
        if (delta) {
          assistantText += delta;
          callbacks.onDelta(delta);
        }
        return;
      }

      if (event.type === "message_end" && isAssistantMessage(message)) {
        const errorMessage = messageError(message);
        if (errorMessage) {
          fail(runtimeFailure("INVALID_RESPONSE", errorMessage, true));
          return;
        }
        finish(visibleAssistantText(message) || assistantText);
        return;
      }

      if (event.type === "agent_end") {
        const messages = (event as { messages?: unknown }).messages;
        const finalText = latestAssistantTextFromMessages(messages);
        if (finalText && !settled) {
          finish(finalText);
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
    const result = await createAgentSessionFromServices({
      services,
      sessionManager,
      customTools: [] as ToolDefinition[],
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
