import { describe, expect, it } from "vitest";
import {
  askUserQuestionAnswerMarkdown,
  askUserQuestionMarkdown,
  askUserQuestionRequest,
  askUserQuestionResult,
  hideAskUserQuestionToolBlock,
  isAskUserQuestionToolError,
  isAskUserQuestionTerminalFailure,
  isPendingAskUserQuestionBlock,
} from "./askUserQuestion";
import type { ToolContentBlock } from "./transcript";
import type { AskUserQuestionCardRequest } from "@dano/types/protocol";

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

function questionBlock(
  questionRequest: AskUserQuestionCardRequest,
): ToolContentBlock {
  return block({}, { questionRequest });
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

  it("renders the canonical question request carried by the transcript", () => {
    const questionRequest = {
      batch: true as const,
      questions: [
        {
          id: "leave_type",
          kind: "single" as const,
          question: "请假类型？",
          options: [
            { id: "annual", label: "年假" },
            { id: "sick", label: "病假" },
          ],
          default: "annual",
        },
        {
          id: "reason",
          kind: "text" as const,
          inputType: "textarea" as const,
          question: "请假原因？",
          default: "个人事务",
        },
      ],
    };

    expect(
      askUserQuestionRequest(
        block(
          { malformed: "browser must not reinterpret this" },
          { questionRequest },
        ),
      ),
    ).toEqual(questionRequest);
  });

  it("does not reinterpret raw model arguments without a canonical request", () => {
    expect(
      askUserQuestionRequest(
        block({ question: "Name?", default: "Dano" }),
      ),
    ).toBeNull();
  });

  it("detects pending native question cards", () => {
    expect(isPendingAskUserQuestionBlock(block({ question: "Name?" }))).toBe(true);
    expect(
      isPendingAskUserQuestionBlock(
        block({ question: "Name?" }, { toolStatus: "success" }),
      ),
    ).toBe(false);
    expect(
      isPendingAskUserQuestionBlock(
        block({ question: "Name?" }, { toolName: "curl" }),
      ),
    ).toBe(false);
    expect(
      isPendingAskUserQuestionBlock(
        block({ question: "Name?" }, { toolCallId: undefined }),
      ),
    ).toBe(false);
  });

  it("recognizes terminal presentation state after transcript recovery", () => {
    expect(
      isAskUserQuestionTerminalFailure(
        block({}, { questionState: "terminal_failure", toolStatus: "error" }),
      ),
    ).toBe(true);
  });

  it("parses answered result details", () => {
    expect(
      askUserQuestionResult({ status: "answered", answer: "Blue" }),
    ).toEqual({ status: "answered", answer: "Blue" });
    expect(
      askUserQuestionResult({ status: "answered", answer: ["Blue", "Green"] }),
    ).toEqual({ status: "answered", answer: ["Blue", "Green"] });
    expect(
      askUserQuestionResult({ status: "answered", answer: 1 }),
    ).toEqual({ status: "answered", answer: 1 });
    expect(
      askUserQuestionResult({ status: "answered", answer: [1, "2"] }),
    ).toEqual({ status: "answered", answer: [1, "2"] });
    expect(
      askUserQuestionResult({ status: "answered", answer: true }),
    ).toEqual({ status: "answered", answer: true });
    expect(
      askUserQuestionResult({
        status: "answered",
        answer: { name: "Dano", department: 1, features: ["Chat"], ok: true },
      }),
    ).toEqual({
      status: "answered",
      answer: { name: "Dano", department: 1, features: ["Chat"], ok: true },
    });
  });

  it("formats grouped answers with question labels and markdown bullets", () => {
    const request = askUserQuestionRequest(
      questionBlock({
        batch: true,
        questions: [
          { id: "expense_type", kind: "text", question: "费用类型？", default: "交通费" },
          { id: "expense_date", kind: "text", question: "发生时间：", default: "2026-06-28" },
          { id: "expense_amount", kind: "text", question: "金额", default: "0" },
          { id: "expense_reason", kind: "text", question: "事由", default: "办公相关支出" },
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
      questionBlock({
        batch: false,
        id: "answer",
        kind: "single",
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

  it("formats number option answers with user-facing labels", () => {
    const request = askUserQuestionRequest(
      questionBlock({
        batch: false,
        id: "answer",
        kind: "single",
        question: "请选择部门",
        options: [
          { id: 1, label: "研发部" },
          { id: 2, label: "财务部" },
        ],
      }),
    );

    expect(request).not.toBeNull();
    expect(
      askUserQuestionAnswerMarkdown(
        request!,
        2,
        { confirm: "确认", cancel: "取消" },
      ),
    ).toBe("财务部");
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

  it("distinguishes bounded terminal presentation failures from retryable errors", () => {
    const retryable = block({}, {
      toolStatus: "error",
      resultText: "QUESTION_PRESENTATION_TIMEOUT: retry",
    });
    const terminal = block({}, {
      toolStatus: "error",
      resultText: "QUESTION_PRESENTATION_FAILED: stop",
    });

    expect(isAskUserQuestionTerminalFailure(retryable)).toBe(false);
    expect(isAskUserQuestionTerminalFailure(terminal)).toBe(true);
  });
});
