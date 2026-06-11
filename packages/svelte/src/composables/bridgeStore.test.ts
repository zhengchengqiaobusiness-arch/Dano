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
  });
});
