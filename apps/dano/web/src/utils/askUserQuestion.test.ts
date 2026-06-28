import { describe, expect, it } from "vitest";
import {
  askUserQuestionAnswerMarkdown,
  askUserQuestionMarkdown,
  askUserQuestionRequest,
  askUserQuestionResult,
  hideAskUserQuestionToolBlock,
  isAskUserQuestionToolError,
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
      batch: false,
      id: "answer",
      kind: "text",
      question: "Name?",
    });
  });

  it("parses and trims a single-choice question", () => {
    expect(
      askUserQuestionRequest(
        block({ question: " Choose? ", options: [" A ", "B"] }),
      ),
    ).toEqual({
      batch: false,
      id: "answer",
      kind: "single",
      question: "Choose?",
      options: [
        { id: "A", label: "A" },
        { id: "B", label: "B" },
      ],
    });
  });

  it("parses multiple-choice and confirmation questions", () => {
    expect(
      askUserQuestionRequest(
        block({ question: "Choose?", options: ["A", "B"], multiple: true }),
      ),
    ).toEqual({
      batch: false,
      id: "answer",
      kind: "multiple",
      question: "Choose?",
      options: [
        { id: "A", label: "A" },
        { id: "B", label: "B" },
      ],
    });
    expect(
      askUserQuestionRequest(block({ question: "Continue?", confirm: true })),
    ).toEqual({
      batch: false,
      id: "answer",
      kind: "confirm",
      question: "Continue?",
    });
  });

  it("parses defaults for text, choice, multiple-choice, and confirmation questions", () => {
    expect(
      askUserQuestionRequest(block({ question: "Name?", default: " Dano " })),
    ).toMatchObject({ kind: "text", default: "Dano" });
    expect(
      askUserQuestionRequest(
        block({ question: "Pick?", options: ["A", "B"], default: "B" }),
      ),
    ).toMatchObject({ kind: "single", default: "B" });
    expect(
      askUserQuestionRequest(
        block({
          question: "Pick?",
          options: ["A", "B"],
          multiple: true,
          default: ["A"],
        }),
      ),
    ).toMatchObject({ kind: "multiple", default: ["A"] });
    expect(
      askUserQuestionRequest(
        block({ question: "Continue?", confirm: true, default: false }),
      ),
    ).toMatchObject({ kind: "confirm", default: false });
  });

  it("parses grouped questions for one shared submit", () => {
    expect(
      askUserQuestionRequest(
        block({
          questions: [
            { id: "name", question: "Name?", default: "Dano" },
            {
              id: "env",
              question: "Environment?",
              options: ["Test", "Prod"],
              default: "Test",
            },
            {
              question: "Features?",
              options: ["Chat", "Deploy"],
              multiple: true,
              default: ["Chat"],
            },
          ],
        }),
      ),
    ).toEqual({
      batch: true,
      questions: [
        { id: "name", kind: "text", question: "Name?", default: "Dano" },
        {
          id: "env",
          kind: "single",
          question: "Environment?",
          options: [
            { id: "Test", label: "Test" },
            { id: "Prod", label: "Prod" },
          ],
          default: "Test",
        },
        {
          id: "q3",
          kind: "multiple",
          question: "Features?",
          options: [
            { id: "Chat", label: "Chat" },
            { id: "Deploy", label: "Deploy" },
          ],
          default: ["Chat"],
        },
      ],
    });
  });

  it("parses structured options and remote select data sources", () => {
    expect(
      askUserQuestionRequest(
        block({
          question: "Employee?",
          inputType: "select",
          options: [
            { id: "emp_1001", label: "Alice Chen", extra: { title: "Manager" } },
            { id: "emp_1002", label: "Bob Li" },
          ],
          default: "emp_1002",
        }),
      ),
    ).toEqual({
      batch: false,
      id: "answer",
      kind: "select",
      question: "Employee?",
      options: [
        { id: "emp_1001", label: "Alice Chen", extra: { title: "Manager" } },
        { id: "emp_1002", label: "Bob Li" },
      ],
      default: "emp_1002",
    });

    expect(
      askUserQuestionRequest(
        block({
          question: "Employee?",
          inputType: "select",
          dataSource: {
            type: "api",
            endpoint: "/api/employees",
            searchParam: "keyword",
            pageParam: "page",
            pageSizeParam: "pageSize",
            resultPath: "data.list",
            totalPath: "data.total",
            idField: "id",
            labelField: "name",
          },
        }),
      ),
    ).toMatchObject({
      batch: false,
      kind: "select",
      dataSource: { endpoint: "/api/employees" },
      options: [],
    });
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
    expect(
      askUserQuestionRequest(
        block({ questions: [{ id: "dup", question: "A?" }, { id: "dup", question: "B?" }] }),
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
    expect(
      askUserQuestionResult({
        status: "answered",
        answer: { name: "Dano", features: ["Chat"], ok: true },
      }),
    ).toEqual({
      status: "answered",
      answer: { name: "Dano", features: ["Chat"], ok: true },
    });
  });

  it("formats grouped answers with question labels and markdown bullets", () => {
    const request = askUserQuestionRequest(
      block({
        questions: [
          { id: "expense_type", question: "费用类型？", default: "交通费" },
          { id: "expense_date", question: "发生时间：", default: "2026-06-28" },
          { id: "expense_amount", question: "金额", default: "0" },
          { id: "expense_reason", question: "事由", default: "办公相关支出" },
        ],
      }),
    );

    expect(request).not.toBeNull();
    expect(
      askUserQuestionAnswerMarkdown(
        request!,
        {
          expense_type: "交通费",
          expense_date: "2026-06-28",
          expense_amount: "0",
          expense_reason: "办公相关支出",
        },
        { confirm: "确认", cancel: "取消" },
      ),
    ).toBe(
      "\n- 费用类型：交通费\n- 发生时间：2026-06-28\n- 金额：0\n- 事由：办公相关支出",
    );
  });

  it("formats structured option answers with user-facing labels", () => {
    const request = askUserQuestionRequest(
      block({
        question: "请选择员工",
        options: [
          { id: "emp_1001", label: "张三" },
          { id: "emp_1002", label: "李四" },
        ],
      }),
    );

    expect(request).not.toBeNull();
    expect(
      askUserQuestionAnswerMarkdown(
        request!,
        "emp_1002",
        { confirm: "确认", cancel: "取消" },
      ),
    ).toBe("李四");
  });

  it("parses cancellation and rejects invalid results", () => {
    expect(askUserQuestionResult({ status: "cancelled" })).toEqual({
      status: "cancelled",
    });
    expect(askUserQuestionResult({ status: "answered" })).toBeNull();
    expect(askUserQuestionResult(null)).toBeNull();
  });

  it("shows failed ask_user_question tool calls in the transcript UI", () => {
    const failedBlock = block({ question: "Name?" }, { toolStatus: "error" });

    expect(isAskUserQuestionToolError(failedBlock)).toBe(true);
    expect(
      hideAskUserQuestionToolBlock(
        failedBlock,
      ),
    ).toBe(false);
    expect(
      hideAskUserQuestionToolBlock(
        block({ question: "Name?" }, { toolStatus: "success" }),
      ),
    ).toBe(false);
    expect(
      hideAskUserQuestionToolBlock(
        block({ question: "Name?" }, { toolName: "curl", toolStatus: "error" }),
      ),
    ).toBe(false);
  });
});
