import { describe, expect, it } from "vitest";
import {
  buildTranscriptDisplayItems,
  buildTranscriptProcessGroups,
  contentBlocks,
  formatTranscriptDuration,
  latestThinkingLine,
  normalizeTranscript,
} from "./transcript";

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

describe("transcript process groups", () => {
  it("collapses structured thinking and tool work before a final answer", () => {
    const messages = normalizeTranscript([
      {
        id: "user-1",
        role: "user",
        content: "inspect",
        timestamp: "2026-01-01T00:00:00.000Z",
      },
      {
        id: "assistant-1",
        role: "assistant",
        content: [
          { type: "thinking", thinking: "Check files" },
          {
            type: "toolCall",
            id: "tool-1",
            name: "read",
            arguments: { path: "README.md" },
          },
        ],
        stopReason: "toolUse",
        timestamp: "2026-01-01T00:00:02.000Z",
      },
      {
        id: "tool-result-1",
        role: "toolResult",
        toolCallId: "tool-1",
        toolName: "read",
        content: [{ type: "text", text: "README" }],
        isError: false,
        timestamp: "2026-01-01T00:00:03.000Z",
      },
      {
        id: "assistant-2",
        role: "assistant",
        content: [
          { type: "thinking", thinking: "Summarize" },
          { type: "text", text: "Final answer" },
        ],
        stopReason: "stop",
        timestamp: "2026-01-01T00:00:04.000Z",
      },
    ] as never);

    const groups = buildTranscriptProcessGroups(buildTranscriptDisplayItems(messages));

    expect(groups).toEqual([
      {
        key: "user-1",
        startItemIndex: 0,
        endItemIndex: 2,
        finalAnswerItemIndex: 2,
        finalAnswerBlockIndex: 1,
        entryIds: ["user-1", "assistant-1", "tool-result-1", "assistant-2"],
        durationMs: 4000,
      },
    ]);
  });

  it("does not collapse plain text that merely mentions thinking", () => {
    const groups = buildTranscriptProcessGroups(buildTranscriptDisplayItems([
      { id: "user-1", role: "user", content: "question" },
      {
        id: "assistant-1",
        role: "assistant",
        content: "我的思考过程是：final answer",
        stopReason: "stop",
      },
    ] as never));

    expect(groups).toEqual([]);
  });

  it("does not collapse a still-active turn", () => {
    const items = buildTranscriptDisplayItems([
      { id: "user-1", role: "user", content: "question" },
      {
        id: "assistant-1",
        role: "assistant",
        content: [
          { type: "thinking", thinking: "still going" },
          { type: "text", text: "Final answer" },
        ],
      },
    ] as never);

    const groups = buildTranscriptProcessGroups(items, {
      isMessageActive: message => message.id === "assistant-1",
    });

    expect(groups).toEqual([]);
  });

  it("does not hide errors when there is no final answer", () => {
    const groups = buildTranscriptProcessGroups(buildTranscriptDisplayItems([
      { id: "user-1", role: "user", content: "question" },
      {
        id: "assistant-1",
        role: "assistant",
        content: [{ type: "thinking", thinking: "try" }],
        stopReason: "error",
        errorMessage: "failed",
      },
    ] as never));

    expect(groups).toEqual([]);
  });
});

describe("thinking transcript helpers", () => {
  it("uses the latest non-empty thinking line while streaming", () => {
    expect(latestThinkingLine("first\nsecond\n")).toBe("second");
    expect(latestThinkingLine("first\n  ")).toBe("first");
  });

  it("formats compact process durations", () => {
    expect(formatTranscriptDuration(4200)).toBe("4s");
    expect(formatTranscriptDuration(62_000)).toBe("1m2s");
    expect(formatTranscriptDuration(3_780_000)).toBe("1h03m");
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
