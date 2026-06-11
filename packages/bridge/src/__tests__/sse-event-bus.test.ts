import { describe, expect, it } from "vitest";
import { SseEventBus, formatSseEvent } from "../sse-event-bus.js";

describe("SseEventBus", () => {
  it("formats named SSE events with id and JSON payload", () => {
    expect(
      formatSseEvent({
        id: 7,
        event: "assistant.delta",
        data: { conversationId: "conv_1", delta: "Hi" },
      }),
    ).toBe(
      [
        "id: 7",
        "event: assistant.delta",
        'data: {"conversationId":"conv_1","delta":"Hi"}',
        "",
        "",
      ].join("\n"),
    );
  });

  it("keeps per-conversation history for EventSource reconnects", () => {
    const bus = new SseEventBus();
    bus.emit("conv_1", "conversation.ready", { conversationId: "conv_1" });
    bus.emit("conv_2", "conversation.ready", { conversationId: "conv_2" });
    bus.emit("conv_1", "assistant.started", {
      conversationId: "conv_1",
      messageId: "msg_1",
    });

    expect(bus.getHistory("conv_1").map(event => event.event)).toEqual([
      "conversation.ready",
      "assistant.started",
    ]);
    expect(bus.getHistory("conv_2").map(event => event.event)).toEqual([
      "conversation.ready",
    ]);
  });

  it("fans out live events to active subscribers", () => {
    const bus = new SseEventBus();
    const received: string[] = [];
    const unsubscribe = bus.subscribe("conv_1", event => {
      received.push(event.event);
    });

    bus.emit("conv_1", "message.accepted", {
      conversationId: "conv_1",
      messageId: "msg_1",
      role: "user",
      content: "Hello",
    });
    unsubscribe();
    bus.emit("conv_1", "assistant.started", {
      conversationId: "conv_1",
      messageId: "msg_2",
    });

    expect(received).toEqual(["message.accepted"]);
  });
});
