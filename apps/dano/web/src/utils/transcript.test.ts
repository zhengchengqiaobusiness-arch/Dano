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

  it("marks structured tool results as complete even when text content is empty", () => {
    const messages = normalizeTranscript([
      {
        role: "assistant",
        content: [
          {
            type: "toolCall",
            id: "question-1",
            name: "ask_user_question",
            arguments: { question: "请填写说明", inputType: "textarea" },
          },
        ],
      },
      {
        role: "toolResult",
        toolCallId: "question-1",
        toolName: "ask_user_question",
        content: [{ type: "text", text: "" }],
        details: { status: "answered", answer: "默认内容" },
        isError: false,
      },
    ] as never);

    const block = contentBlocks(messages[0]!).find(
      item => item.kind === "tool",
    );

    expect(block?.kind === "tool" ? block.toolStatus : undefined).toBe(
      "success",
    );
    expect(block?.kind === "tool" ? block.resultDetails : undefined).toEqual({
      status: "answered",
      answer: "默认内容",
    });
  });

  it("attaches question results to the matching tool call id", () => {
    const messages = normalizeTranscript([
      {
        role: "assistant",
        content: [
          {
            type: "toolCall",
            id: "question-old",
            name: "ask_user_question",
            arguments: { question: "旧问题", options: ["A", "B"] },
          },
          {
            type: "toolCall",
            id: "question-current",
            name: "ask_user_question",
            arguments: {
              question: "请填写说明",
              inputType: "textarea",
              default: "默认内容",
            },
          },
        ],
      },
      {
        role: "toolResult",
        toolCallId: "question-current",
        toolName: "ask_user_question",
        content: [{ type: "text", text: "" }],
        details: { status: "answered", answer: "默认内容" },
        isError: false,
      },
    ] as never);

    const blocks = contentBlocks(messages[0]!).filter(
      item => item.kind === "tool",
    );

    expect(blocks.map(block => block.kind === "tool" ? block.toolCallId : "")).toEqual([
      "question-old",
      "question-current",
    ]);
    expect(blocks[0]?.kind === "tool" ? blocks[0].toolStatus : undefined).toBe(
      "pending",
    );
    expect(blocks[1]?.kind === "tool" ? blocks[1].resultDetails : undefined).toEqual({
      status: "answered",
      answer: "默认内容",
    });
  });
});

describe("assistant thinking blocks", () => {
  it("keeps structured thinking, text, and tool calls in content order without exposing signatures", () => {
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
          type: "thinking",
          thinking: "Check tool result",
          thinkingSignature: "also hidden",
        },
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
      "thinking",
      "tool",
    ]);
    expect(blocks[0]).toEqual({ kind: "thinking", text: "Inspect the repo" });
    expect(blocks[2]).toEqual({ kind: "thinking", text: "Check tool result" });
  });
});

describe("uploaded file blocks", () => {
  it("renders structured file blocks as file cards", () => {
    const blocks = contentBlocks({
      role: "user",
      content: [
        {
          type: "file",
          name: "photo.png",
          path: "/workspace/uploads/abc123.png",
          relativePath: "uploads/abc123.png",
        },
      ],
    } as never);

    expect(blocks).toEqual([
      { kind: "file", name: "photo.png", path: "uploads/abc123.png" },
    ]);
  });
});
