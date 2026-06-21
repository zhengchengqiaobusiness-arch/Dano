import { describe, expect, it } from "vitest";
import {
  askUserQuestionRequest,
  askUserQuestionResult,
} from "./askUserQuestion";
import type { ToolContentBlock } from "./transcript";

function block(
  toolArgs: ToolContentBlock["toolArgs"],
  overrides: Partial<ToolContentBlock> = {},
): ToolContentBlock {
  return {
    kind: "tool",
    toolName: "ask_user_question",
    toolCallId: "call-1",
    toolArgs,
    argumentsText: "",
    toolStatus: "pending",
    ...overrides,
  };
}

describe("ask user question transcript data", () => {
  it("parses a text question", () => {
    expect(askUserQuestionRequest(block({ question: "Name?" }))).toEqual({
      question: "Name?",
    });
  });

  it("parses and trims a single-choice question", () => {
    expect(
      askUserQuestionRequest(
        block({ question: " Choose? ", options: [" A ", "B"] }),
      ),
    ).toEqual({ question: "Choose?", options: ["A", "B"] });
  });

  it("rejects malformed or unrelated tool calls", () => {
    expect(askUserQuestionRequest(block({ question: "" }))).toBeNull();
    expect(
      askUserQuestionRequest(block({ question: "Choose?", options: ["A"] })),
    ).toBeNull();
    expect(
      askUserQuestionRequest(
        block({ question: "Name?" }, { toolName: "other_tool" }),
      ),
    ).toBeNull();
  });

  it("parses answered result details", () => {
    expect(
      askUserQuestionResult({ status: "answered", answer: "Blue" }),
    ).toEqual({ status: "answered", answer: "Blue" });
  });

  it("parses cancellation and rejects invalid results", () => {
    expect(askUserQuestionResult({ status: "cancelled" })).toEqual({
      status: "cancelled",
    });
    expect(askUserQuestionResult({ status: "answered" })).toBeNull();
    expect(askUserQuestionResult(null)).toBeNull();
  });
});
