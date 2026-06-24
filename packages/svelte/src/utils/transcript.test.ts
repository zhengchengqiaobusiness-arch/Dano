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
        { type: "thinking", thinking: "Inspect the repo", thinkingSignature: "hidden" },
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

  it("keeps redacted thinking visible as a placeholder block", () => {
    const blocks = contentBlocks({
      role: "assistant",
      content: [{ type: "thinking", thinking: "", redacted: true }],
    } as never);

    expect(blocks).toEqual([
      { kind: "thinking", text: "", redacted: true },
    ]);
  });

  it("parses complete assistant think tags outside Markdown fences", () => {
    const blocks = contentBlocks({
      role: "assistant",
      content: [
        {
          type: "text",
          text: "<think>\nCheck constraints\n</think>\n\nVisible answer.",
        },
      ],
    } as never);

    expect(blocks).toEqual([
      { kind: "thinking", text: "Check constraints" },
      { kind: "text", text: "\n\nVisible answer." },
    ]);
  });

  it("does not parse think tags inside fenced code", () => {
    const text = "```xml\n<think>literal</think>\n```\nVisible answer.";
    const blocks = contentBlocks({
      role: "assistant",
      content: [{ type: "text", text }],
    } as never);

    expect(blocks).toEqual([{ kind: "text", text }]);
  });

  it("does not parse think tags inside quoted prose", () => {
    const text = 'The docs say "<think>literal</think>" before continuing.';
    const blocks = contentBlocks({
      role: "assistant",
      content: [{ type: "text", text }],
    } as never);

    expect(blocks).toEqual([{ kind: "text", text }]);
  });

  it("does not parse think tags inside Markdown blockquotes", () => {
    const text = "> <think>quoted</think>\n\nVisible answer.";
    const blocks = contentBlocks({
      role: "assistant",
      content: [{ type: "text", text }],
    } as never);

    expect(blocks).toEqual([{ kind: "text", text }]);
  });

  it("leaves unmatched assistant think tags as normal text while streaming", () => {
    const text = "<think>still streaming";
    const blocks = contentBlocks({
      role: "assistant",
      content: [{ type: "text", text }],
    } as never);

    expect(blocks).toEqual([{ kind: "text", text }]);
  });

  it("does not parse user-authored think tags", () => {
    const text = "<think>example</think>\nPlease explain this tag.";
    const blocks = contentBlocks({
      role: "user",
      content: [{ type: "text", text }],
    } as never);

    expect(blocks).toEqual([{ kind: "text", text }]);
  });
});
