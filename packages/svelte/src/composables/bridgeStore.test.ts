import { describe, expect, it } from "vitest";
import {
  applyServerEvent,
  canSend,
  createInitialChatState,
} from "./bridgeStore.svelte";

describe("bridgeStore", () => {
  it("blocks empty input before HTTP submission", () => {
    expect(canSend("   ", createInitialChatState())).toBe(false);
  });

  it("applies ordered SSE chat events", () => {
    let state = createInitialChatState();
    state = applyServerEvent(state, "conversation.ready", {
      conversationId: "conv_1",
    });
    state = applyServerEvent(state, "message.accepted", {
      conversationId: "conv_1",
      messageId: "msg_1",
      role: "user",
      content: "Hello",
    });
    state = applyServerEvent(state, "assistant.started", {
      conversationId: "conv_1",
      messageId: "msg_2",
    });
    state = applyServerEvent(state, "assistant.delta", {
      conversationId: "conv_1",
      messageId: "msg_2",
      delta: "Hi",
    });
    state = applyServerEvent(state, "assistant.completed", {
      conversationId: "conv_1",
      messageId: "msg_2",
      content: "Hi",
    });

    expect(state.messages).toEqual([
      {
        id: "msg_1",
        role: "user",
        content: "Hello",
        status: "completed",
      },
      {
        id: "msg_2",
        role: "assistant",
        content: "Hi",
        status: "completed",
      },
    ]);
  });

  it("marks failed assistant messages retryable", () => {
    let state = createInitialChatState();
    state = applyServerEvent(state, "assistant.started", {
      messageId: "msg_2",
    });
    state = applyServerEvent(state, "message.failed", {
      messageId: "msg_2",
      errorMessage: "bad credentials",
      retryable: true,
    });

    expect(state.messages[0]).toMatchObject({
      id: "msg_2",
      status: "failed",
      errorMessage: "bad credentials",
      retryable: true,
    });
    expect(state.lastError).toBe("");
  });

  it("applies assistant tool blocks to the active assistant message", () => {
    let state = createInitialChatState();
    state = applyServerEvent(state, "assistant.started", {
      messageId: "msg_2",
    });
    state = applyServerEvent(state, "assistant.blocks", {
      messageId: "msg_2",
      blocks: [
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
      ],
    });

    expect(state.messages[0]).toMatchObject({
      id: "msg_2",
      content: "Done.",
      contentBlocks: [
        {
          kind: "tool",
          toolName: "bash",
          resultText: "hello",
          toolStatus: "success",
        },
        {
          kind: "text",
          text: "Done.",
        },
      ],
      status: "streaming",
    });
  });

  it("preserves thinking blocks without mixing them into visible answer text", () => {
    let state = createInitialChatState();
    state = applyServerEvent(state, "assistant.started", {
      messageId: "msg_2",
    });
    state = applyServerEvent(state, "assistant.blocks", {
      messageId: "msg_2",
      blocks: [
        {
          kind: "thinking",
          text: "I should inspect the files before answering.",
        },
        {
          kind: "text",
          text: "The files are ready.",
        },
      ],
    });

    expect(state.messages[0]).toMatchObject({
      id: "msg_2",
      content: "The files are ready.",
      contentBlocks: [
        {
          kind: "thinking",
          text: "I should inspect the files before answering.",
        },
        {
          kind: "text",
          text: "The files are ready.",
        },
      ],
      status: "streaming",
    });
  });

  it("preserves tool blocks when a later empty block snapshot arrives", () => {
    let state = createInitialChatState();
    state = applyServerEvent(state, "assistant.started", {
      messageId: "msg_2",
    });
    state = applyServerEvent(state, "assistant.blocks", {
      messageId: "msg_2",
      blocks: [
        {
          kind: "tool",
          toolName: "read",
          toolCallId: "tool-1",
          toolArgs: { path: "README.md" },
          argumentsText: '{\n  "path": "README.md"\n}',
          resultText: "# Dano\n",
          toolStatus: "success",
        },
      ],
    });
    state = applyServerEvent(state, "assistant.blocks", {
      messageId: "msg_2",
      blocks: [],
    });
    state = applyServerEvent(state, "assistant.delta", {
      messageId: "msg_2",
      delta: "Read complete.",
    });
    state = applyServerEvent(state, "assistant.completed", {
      messageId: "msg_2",
      content: "Read complete.",
    });

    expect(state.messages[0]).toMatchObject({
      content: "Read complete.",
      contentBlocks: [
        {
          kind: "tool",
          toolName: "read",
          resultText: "# Dano\n",
          toolStatus: "success",
        },
      ],
      status: "completed",
    });
  });
});
