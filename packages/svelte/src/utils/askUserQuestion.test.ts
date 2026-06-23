import { describe, expect, it } from "vitest";
import {
  askUserQuestionMarkdown,
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
  it.each([
    ["real newlines", "| A | B |\n| --- | --- |", "| A | B |\n| --- | --- |"],
    ["escaped LF", String.raw`| A | B |\n| --- | --- |`, "| A | B |\n| --- | --- |"],
    ["escaped CRLF", String.raw`| A | B |\r\n| --- | --- |`, "| A | B |\n| --- | --- |"],
    ["double-escaped LF", String.raw`| A | B |\\n| --- | --- |`, "| A | B |\n| --- | --- |"],
    ["double-escaped CRLF", String.raw`| A | B |\\r\\n| --- | --- |`, "| A | B |\n| --- | --- |"],
  ])("normalizes %s for Markdown block rows", (_, question, expected) => {
    expect(askUserQuestionMarkdown(question)).toBe(expected);
  });

  it("parses a text question", () => {
    expect(askUserQuestionRequest(block({ question: "Name?" }))).toEqual({
      kind: "text",
      question: "Name?",
    });
  });

  it("parses and trims a single-choice question", () => {
    expect(
      askUserQuestionRequest(
        block({ question: " Choose? ", options: [" A ", "B"] }),
      ),
    ).toEqual({ kind: "single", question: "Choose?", options: ["A", "B"] });
  });

  it("parses multiple-choice and confirmation questions", () => {
    expect(
      askUserQuestionRequest(
        block({ question: "Choose?", options: ["A", "B"], multiple: true }),
      ),
    ).toEqual({
      kind: "multiple",
      question: "Choose?",
      options: ["A", "B"],
    });
    expect(
      askUserQuestionRequest(block({ question: "Continue?", confirm: true })),
    ).toEqual({ kind: "confirm", question: "Continue?" });
  });

  it("rejects malformed or unrelated tool calls", () => {
    expect(askUserQuestionRequest(block({ question: "" }))).toBeNull();
    expect(
      askUserQuestionRequest(block({ question: "Choose?", options: ["A"] })),
    ).toBeNull();
    expect(
      askUserQuestionRequest(block({ question: "Choose?", multiple: true })),
    ).toBeNull();
    expect(
      askUserQuestionRequest(
        block({ question: "Continue?", options: ["A", "B"], confirm: true }),
      ),
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
    expect(
      askUserQuestionResult({ status: "answered", answer: ["Blue", "Green"] }),
    ).toEqual({ status: "answered", answer: ["Blue", "Green"] });
    expect(
      askUserQuestionResult({ status: "answered", answer: true }),
    ).toEqual({ status: "answered", answer: true });
  });

  it("parses cancellation and rejects invalid results", () => {
    expect(askUserQuestionResult({ status: "cancelled" })).toEqual({
      status: "cancelled",
    });
    expect(askUserQuestionResult({ status: "answered" })).toBeNull();
    expect(askUserQuestionResult(null)).toBeNull();
  });
});
