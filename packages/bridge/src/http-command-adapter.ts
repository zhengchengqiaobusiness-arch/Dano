import { SseEventBus } from "./sse-event-bus.js";
import type {
  ApiErrorResponse,
  ChatContentBlock,
  ChatMessage,
  Conversation,
  CreateConversationResponse,
  FailureCode,
  RuntimeFailure,
  SendMessageRequest,
  SendMessageResponse,
  ServerLlmRuntime,
  ServerLlmRuntimeFactory,
} from "./types.js";

const MAX_MESSAGE_LENGTH = 20_000;

export class HttpApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: FailureCode,
    readonly errorMessage: string,
  ) {
    super(errorMessage);
  }

  toResponse(): ApiErrorResponse {
    return {
      code: this.code,
      errorMessage: this.errorMessage,
    };
  }
}

interface ConversationRecord extends Conversation {
  clientMessageIds: Map<string, string>;
  retryTextByMessageId: Map<string, string>;
  runtimePromise?: Promise<ServerLlmRuntime>;
}

export interface ConversationControllerOptions {
  runtimeFactory: ServerLlmRuntimeFactory;
  eventBus?: SseEventBus;
  now?: () => Date;
}

export class ConversationController {
  readonly eventBus: SseEventBus;
  private conversations = new Map<string, ConversationRecord>();
  private conversationSeq = 0;
  private messageSeq = 0;
  private now: () => Date;

  constructor(private readonly options: ConversationControllerOptions) {
    this.eventBus = options.eventBus ?? new SseEventBus();
    this.now = options.now ?? (() => new Date());
  }

  createConversation(): CreateConversationResponse {
    const id = this.nextConversationId();
    const timestamp = this.timestamp();
    const conversation: ConversationRecord = {
      id,
      createdAt: timestamp,
      updatedAt: timestamp,
      status: "active",
      messages: [],
      clientMessageIds: new Map(),
      retryTextByMessageId: new Map(),
    };

    this.conversations.set(id, conversation);
    this.eventBus.emit(id, "conversation.ready", { conversationId: id });

    return {
      conversationId: id,
      eventsUrl: `/api/conversations/${id}/events`,
    };
  }

  hasConversation(conversationId: string): boolean {
    return this.conversations.has(conversationId);
  }

  async sendMessage(
    conversationId: string,
    request: SendMessageRequest,
    options: { retryOfMessageId?: string } = {},
  ): Promise<SendMessageResponse> {
    const conversation = this.requireConversation(conversationId);
    const clientMessageId = request.clientMessageId?.trim();
    if (clientMessageId) {
      const existingMessageId = conversation.clientMessageIds.get(clientMessageId);
      if (existingMessageId) {
        return {
          conversationId,
          messageId: existingMessageId,
          status: "accepted",
        };
      }
    }

    if (conversation.status === "answering") {
      throw new HttpApiError(
        409,
        "CONVERSATION_BUSY",
        "Wait for the current assistant answer before sending another message.",
      );
    }

    const text = this.normalizeMessageText(request.text);
    const timestamp = this.timestamp();
    const userMessage: ChatMessage = {
      id: this.nextMessageId(),
      conversationId,
      role: "user",
      content: text,
      status: "completed",
      createdAt: timestamp,
      completedAt: timestamp,
      ...(clientMessageId ? { clientMessageId } : {}),
      ...(options.retryOfMessageId
        ? { retryOfMessageId: options.retryOfMessageId }
        : {}),
    };
    const assistantMessage: ChatMessage = {
      id: this.nextMessageId(),
      conversationId,
      role: "assistant",
      content: "",
      status: "streaming",
      createdAt: timestamp,
    };

    conversation.status = "answering";
    conversation.updatedAt = timestamp;
    conversation.messages.push(userMessage, assistantMessage);
    conversation.retryTextByMessageId.set(userMessage.id, text);
    conversation.retryTextByMessageId.set(assistantMessage.id, text);
    if (clientMessageId) {
      conversation.clientMessageIds.set(clientMessageId, userMessage.id);
    }

    this.eventBus.emit(conversationId, "message.accepted", {
      conversationId,
      messageId: userMessage.id,
      role: "user",
      content: text,
    });
    this.eventBus.emit(conversationId, "assistant.started", {
      conversationId,
      messageId: assistantMessage.id,
    });

    void this.answer(conversation, assistantMessage, text);

    return {
      conversationId,
      messageId: userMessage.id,
      status: "accepted",
    };
  }

  async retryMessage(
    conversationId: string,
    messageId: string,
  ): Promise<SendMessageResponse> {
    const conversation = this.requireConversation(conversationId);
    const message = conversation.messages.find(candidate => candidate.id === messageId);
    if (!message) {
      throw new HttpApiError(404, "MESSAGE_NOT_FOUND", "Message was not found.");
    }

    const text = conversation.retryTextByMessageId.get(messageId);
    if (!text || message.status !== "failed") {
      throw new HttpApiError(
        409,
        "MESSAGE_NOT_RETRYABLE",
        "This message cannot be retried.",
      );
    }

    return this.sendMessage(
      conversationId,
      {
        clientMessageId: `retry_${messageId}_${this.messageSeq + 1}`,
        text,
      },
      { retryOfMessageId: messageId },
    );
  }

  async dispose(): Promise<void> {
    const runtimes = await Promise.all(
      [...this.conversations.values()].map(conversation =>
        conversation.runtimePromise?.catch(() => undefined),
      ),
    );

    await Promise.all(
      runtimes
        .filter((runtime): runtime is ServerLlmRuntime => Boolean(runtime))
        .map(runtime => runtime.dispose?.()),
    );
  }

  private async answer(
    conversation: ConversationRecord,
    assistantMessage: ChatMessage,
    text: string,
  ): Promise<void> {
    let settled = false;

    const complete = (content: string) => {
      if (settled) {
        return;
      }
      settled = true;
      const timestamp = this.timestamp();
      assistantMessage.content = content;
      assistantMessage.status = "completed";
      assistantMessage.completedAt = timestamp;
      conversation.status = "active";
      conversation.updatedAt = timestamp;
      this.eventBus.emit(conversation.id, "assistant.completed", {
        conversationId: conversation.id,
        messageId: assistantMessage.id,
        content,
      });
    };

    const fail = (failure: RuntimeFailure) => {
      if (settled) {
        return;
      }
      settled = true;
      const timestamp = this.timestamp();
      assistantMessage.status = "failed";
      assistantMessage.errorCode = failure.code;
      assistantMessage.errorMessage = failure.errorMessage;
      assistantMessage.completedAt = timestamp;
      conversation.status = "failed";
      conversation.updatedAt = timestamp;
      this.eventBus.emit(conversation.id, "message.failed", {
        conversationId: conversation.id,
        messageId: assistantMessage.id,
        code: failure.code,
        errorMessage: failure.errorMessage,
        retryable: failure.retryable,
      });
    };

    try {
      const runtime = await this.runtimeForConversation(conversation);
      await runtime.sendUserMessage(text, {
        onDelta: delta => {
          if (!delta || settled) {
            return;
          }
          assistantMessage.content += delta;
          this.eventBus.emit(conversation.id, "assistant.delta", {
            conversationId: conversation.id,
            messageId: assistantMessage.id,
            delta,
          });
        },
        onContentBlocks: blocks => {
          if (settled) {
            return;
          }
          assistantMessage.contentBlocks = blocks;
          assistantMessage.content = textFromContentBlocks(blocks);
          this.eventBus.emit(conversation.id, "assistant.blocks", {
            conversationId: conversation.id,
            messageId: assistantMessage.id,
            blocks,
          });
        },
        onComplete: content => {
          complete(content);
        },
        onFailure: failure => {
          fail(failure);
        },
      });
    } catch (error) {
      conversation.runtimePromise = undefined;
      const message = error instanceof Error ? error.message : String(error);
      fail({
        code: "LLM_UNAVAILABLE",
        errorMessage: message.trim() || "The assistant is unavailable.",
        retryable: true,
      });
    }
  }

  private async runtimeForConversation(
    conversation: ConversationRecord,
  ): Promise<ServerLlmRuntime> {
    conversation.runtimePromise ??= Promise.resolve(
      this.options.runtimeFactory(conversation.id),
    );
    return conversation.runtimePromise;
  }

  private requireConversation(conversationId: string): ConversationRecord {
    const conversation = this.conversations.get(conversationId);
    if (!conversation) {
      throw new HttpApiError(
        404,
        "CONVERSATION_NOT_FOUND",
        "Conversation was not found.",
      );
    }
    return conversation;
  }

  private normalizeMessageText(value: unknown): string {
    const text = typeof value === "string" ? value.trim() : "";
    if (!text) {
      throw new HttpApiError(
        400,
        "EMPTY_MESSAGE",
        "Enter a message before sending.",
      );
    }
    if (text.length > MAX_MESSAGE_LENGTH) {
      throw new HttpApiError(
        400,
        "MESSAGE_TOO_LONG",
        `Message must be ${MAX_MESSAGE_LENGTH} characters or fewer.`,
      );
    }
    return text;
  }

  private nextConversationId(): string {
    this.conversationSeq += 1;
    return `conv_${this.conversationSeq}`;
  }

  private nextMessageId(): string {
    this.messageSeq += 1;
    return `msg_${this.messageSeq}`;
  }

  private timestamp(): string {
    return this.now().toISOString();
  }
}

function textFromContentBlocks(blocks: ChatContentBlock[]): string {
  return blocks
    .flatMap(block => (block.kind === "text" ? [block.text] : []))
    .join("");
}
