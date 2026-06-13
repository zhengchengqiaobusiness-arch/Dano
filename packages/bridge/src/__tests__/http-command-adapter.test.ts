import { describe, expect, it, vi } from "vitest";
import { ConversationController, HttpApiError } from "../http-command-adapter.js";
import type { RuntimeCallbacks, ServerLlmRuntime } from "../types.js";

class FakeRuntime implements ServerLlmRuntime {
  readonly prompts: string[] = [];
  nextFailure: Error | null = null;

  async sendUserMessage(text: string, callbacks: RuntimeCallbacks): Promise<void> {
    this.prompts.push(text);
    if (this.nextFailure) {
      const failure = this.nextFailure;
      this.nextFailure = null;
      throw failure;
    }
    callbacks.onDelta("Hello ");
    callbacks.onDelta("from Dano.");
    callbacks.onComplete("Hello from Dano.");
  }
}

class ToolRuntime implements ServerLlmRuntime {
  async sendUserMessage(_text: string, callbacks: RuntimeCallbacks): Promise<void> {
    callbacks.onContentBlocks([
      {
        kind: "tool",
        toolName: "bash",
        toolCallId: "tool-1",
        toolArgs: { command: "echo hello" },
        argumentsText: '{\n  "command": "echo hello"\n}',
        toolStatus: "pending",
      },
    ]);
    callbacks.onContentBlocks([
      {
        kind: "tool",
        toolName: "bash",
        toolCallId: "tool-1",
        toolArgs: { command: "echo hello" },
        argumentsText: '{\n  "command": "echo hello"\n}',
        resultText: "hello",
        toolStatus: "success",
      },
      {
        kind: "text",
        text: "Done.",
      },
    ]);
    callbacks.onComplete("Done.");
  }
}

class TimeoutRuntime implements ServerLlmRuntime {
  async sendUserMessage(_text: string, callbacks: RuntimeCallbacks): Promise<void> {
    callbacks.onFailure({
      code: "LLM_TIMEOUT",
      errorMessage: "The assistant stopped making progress in time.",
      retryable: true,
    });
  }
}

function waitForMicrotasks(): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, 0));
}

describe("ConversationController", () => {
  it("accepts a message and emits accepted, started, delta, and completed events", async () => {
    const runtime = new FakeRuntime();
    const controller = new ConversationController({
      runtimeFactory: () => runtime,
    });
    const conversation = controller.createConversation();

    const response = await controller.sendMessage(conversation.conversationId, {
      clientMessageId: "client-1",
      text: "Hello",
    });
    await waitForMicrotasks();

    expect(response).toEqual({
      conversationId: "conv_1",
      messageId: "msg_1",
      status: "accepted",
    });
    expect(runtime.prompts).toEqual(["Hello"]);
    expect(
      controller.eventBus
        .getHistory(conversation.conversationId)
        .map(event => event.event),
    ).toEqual([
      "conversation.ready",
      "message.accepted",
      "assistant.started",
      "assistant.delta",
      "assistant.delta",
      "assistant.completed",
    ]);
  });

  it("rejects whitespace-only input before the runtime is called", async () => {
    const runtime = new FakeRuntime();
    const controller = new ConversationController({
      runtimeFactory: () => runtime,
    });
    const conversation = controller.createConversation();

    await expect(
      controller.sendMessage(conversation.conversationId, {
        clientMessageId: "client-empty",
        text: "   ",
      }),
    ).rejects.toMatchObject({
      status: 400,
      code: "EMPTY_MESSAGE",
    });
    expect(runtime.prompts).toEqual([]);
  });

  it("deduplicates client message IDs so double-click sends cannot create two assistant turns", async () => {
    const runtime = new FakeRuntime();
    const controller = new ConversationController({
      runtimeFactory: () => runtime,
    });
    const conversation = controller.createConversation();

    const first = await controller.sendMessage(conversation.conversationId, {
      clientMessageId: "dupe",
      text: "Hello",
    });
    const second = await controller.sendMessage(conversation.conversationId, {
      clientMessageId: "dupe",
      text: "Hello",
    });
    await waitForMicrotasks();

    expect(second).toEqual(first);
    expect(runtime.prompts).toEqual(["Hello"]);
  });

  it("emits a clear failure event and retries the original text", async () => {
    const runtime = new FakeRuntime();
    const controller = new ConversationController({
      runtimeFactory: () => runtime,
    });
    const conversation = controller.createConversation();
    runtime.nextFailure = new Error("bad credentials");

    await controller.sendMessage(conversation.conversationId, {
      clientMessageId: "fail-1",
      text: "Hello",
    });
    await waitForMicrotasks();

    const failed = controller.eventBus
      .getHistory(conversation.conversationId)
      .find(event => event.event === "message.failed");
    expect(failed?.data).toMatchObject({
      messageId: "msg_2",
      code: "LLM_UNAVAILABLE",
      errorMessage: "bad credentials",
      retryable: true,
    });

    const retry = await controller.retryMessage(conversation.conversationId, "msg_2");
    await waitForMicrotasks();

    expect(retry).toEqual({
      conversationId: "conv_1",
      messageId: "msg_3",
      status: "accepted",
    });
    expect(runtime.prompts).toEqual(["Hello", "Hello"]);
  });

  it("emits timeout failures as retryable message.failed events", async () => {
    const controller = new ConversationController({
      runtimeFactory: () => new TimeoutRuntime(),
    });
    const conversation = controller.createConversation();

    await controller.sendMessage(conversation.conversationId, {
      clientMessageId: "timeout-1",
      text: "Long task",
    });
    await waitForMicrotasks();

    const failed = controller.eventBus
      .getHistory(conversation.conversationId)
      .find(event => event.event === "message.failed");
    expect(failed?.data).toMatchObject({
      messageId: "msg_2",
      code: "LLM_TIMEOUT",
      errorMessage: "The assistant stopped making progress in time.",
      retryable: true,
    });
  });

  it("treats business-action requests as chat text only", async () => {
    const runtime = new FakeRuntime();
    const runtimeFactory = vi.fn(() => runtime);
    const controller = new ConversationController({ runtimeFactory });
    const conversation = controller.createConversation();

    await controller.sendMessage(conversation.conversationId, {
      clientMessageId: "business-1",
      text: "Submit a leave request for Friday.",
    });
    await waitForMicrotasks();

    expect(runtime.prompts).toEqual(["Submit a leave request for Friday."]);
    expect(runtimeFactory).toHaveBeenCalledTimes(1);
    expect(
      controller.eventBus
        .getHistory(conversation.conversationId)
        .some(event => event.event === "assistant.completed"),
    ).toBe(true);
  });

  it("emits assistant tool blocks before completion", async () => {
    const controller = new ConversationController({
      runtimeFactory: () => new ToolRuntime(),
    });
    const conversation = controller.createConversation();

    await controller.sendMessage(conversation.conversationId, {
      clientMessageId: "tool-1",
      text: "Run a tool",
    });
    await waitForMicrotasks();

    const toolEvents = controller.eventBus
      .getHistory(conversation.conversationId)
      .filter(event => event.event === "assistant.blocks");
    expect(toolEvents).toHaveLength(2);
    expect(toolEvents[0]?.data).toMatchObject({
      messageId: "msg_2",
      blocks: [
        {
          kind: "tool",
          toolName: "bash",
          toolStatus: "pending",
        },
      ],
    });
    expect(toolEvents[1]?.data).toMatchObject({
      blocks: [
        {
          kind: "tool",
          resultText: "hello",
          toolStatus: "success",
        },
        {
          kind: "text",
          text: "Done.",
        },
      ],
    });
  });

  it("fails loud for unknown conversations", async () => {
    const controller = new ConversationController({
      runtimeFactory: () => new FakeRuntime(),
    });

    await expect(
      controller.sendMessage("conv_missing", { text: "Hello" }),
    ).rejects.toBeInstanceOf(HttpApiError);
  });
});
