/** @vitest-environment happy-dom */

import { mount, tick, unmount } from "svelte";
import { describe, expect, it, vi } from "vitest";
import ChatTranscript from "./ChatTranscript.svelte";
import chatTranscriptSource from "./ChatTranscript.svelte?raw";

vi.mock("../composables/bridgeStore.svelte", () => ({
  abortGeneration: vi.fn(),
  answerQuestion: vi.fn(),
  cancelQuestionRevision: vi.fn(),
  getBridgeClientId: () => null,
  presentQuestion: vi.fn(),
  reviseQuestion: vi.fn(),
  submitQuestionRevision: vi.fn(),
}));

describe("ChatTranscript assistant pending indicator", () => {
  it("marks post-tool waiting for delayed presentation", async () => {
    const target = document.createElement("div");
    document.body.appendChild(target);
    const component = mount(ChatTranscript, {
      target,
      props: {
        isStreaming: true,
        messages: [
          { id: "user-1", role: "user", content: "hello" },
          {
            id: "assistant-1",
            role: "assistant",
            content: [
              { type: "toolCall", id: "tool-1", name: "read", arguments: {} },
              { type: "toolResult", text: "done", sourceMessageId: "tool-result-1" },
            ],
          },
        ] as never,
      },
    });

    try {
      await tick();
      const pendingRow = target.querySelector<HTMLElement>(
        ".assistant-pending-row",
      );
      expect(
        pendingRow?.classList.contains("assistant-pending-delayed"),
      ).toBe(true);
      expect(chatTranscriptSource).toContain("visibility: hidden;");
      expect(chatTranscriptSource).toContain(
        "animation: assistant-pending-reveal 0s linear 500ms forwards;",
      );
    } finally {
      await unmount(component);
      target.remove();
    }
  });
});
