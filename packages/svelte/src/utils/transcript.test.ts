import { describe, expect, it } from "vitest";
import { contentBlocks, normalizeTranscript } from "./transcript";

describe("curl transcript status", () => {
  it("marks a completed non-zero curl result as an error", () => {
    const messages = normalizeTranscript([
      {
        role: "assistant",
        content: [
          {
            type: "toolCall",
            id: "curl-1",
            name: "curl",
            arguments: { args: ["https://example.com"] },
          },
        ],
      },
      {
        role: "toolResult",
        toolCallId: "curl-1",
        toolName: "curl",
        content: [{ type: "text", text: "" }],
        details: { stderr: "curl: (77) missing CA", exitCode: 77 },
        isError: false,
      },
    ] as never);

    const block = contentBlocks(messages[0]!).find(
      item => item.kind === "tool",
    );
    expect(block?.kind === "tool" ? block.toolStatus : undefined).toBe(
      "error",
    );
  });
});

describe("assistant thinking blocks", () => {
  it("keeps structured thinking, text, and tool calls in content order", () => {
    const blocks = contentBlocks({
      role: "assistant",
      content: [
        {
          type: "thinking",
          thinking: "Inspect the repo",
          thinkingSignature: "hidden",
        },
        { type: "text", text: "Final **answer**." },
        {
          type: "toolCall",
          id: "tool-1",
          name: "read",
          arguments: { path: "README.md" },
        },
      ],
    } as never);

    expect(blocks.map(block => block.kind)).toEqual([
      "thinking",
      "text",
      "tool",
    ]);
    expect(blocks[0]).toEqual({ kind: "thinking", text: "Inspect the repo" });
  });
});
