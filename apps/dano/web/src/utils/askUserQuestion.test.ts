import { describe, expect, it } from "vitest";
import {
  askUserQuestionAnswerItems,
  askUserQuestionAnswerMarkdown,
  askUserQuestionConfirmationFormIds,
  askUserQuestionReturnedConfirmationFormIds,
  askUserQuestionMarkdown,
  askUserQuestionRequest,
  askUserQuestionResult,
  hideAskUserQuestionToolBlock,
  isAskUserQuestionToolError,
  isAskUserQuestionTerminalFailure,
  isPendingAskUserQuestionBlock,
  isAskUserQuestionValidationTerminalFailure,
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
          fieldAssist: true,
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
        formId: "grouped-form-1",
        answer: { name: "Dano", department: 1, features: ["Chat"], ok: true },
      }),
    ).toEqual({
      status: "answered",
      formId: "grouped-form-1",
      answer: { name: "Dano", department: 1, features: ["Chat"], ok: true },
    });
  });

  it("parses every Submitted Form in an atomic confirmation result", () => {
    expect(
      askUserQuestionResult({
        status: "confirmed",
        confirmationOfToolCallId: "form-a",
        answer: { reason: "家庭事务" },
        forms: [
          { formId: "form-a", answer: { reason: "家庭事务" } },
          { formId: "form-b", answer: { destination: "上海" } },
        ],
      }),
    ).toEqual({
      status: "confirmed",
      confirmationOfToolCallId: "form-a",
      answer: { reason: "家庭事务" },
      forms: [
        { formId: "form-a", answer: { reason: "家庭事务" } },
        { formId: "form-b", answer: { destination: "上海" } },
      ],
    });
  });

  it("normalizes a legacy single-form confirmation result", () => {
    expect(
      askUserQuestionResult({
        status: "confirmed",
        confirmationOfToolCallId: "form-a",
        answer: { reason: "家庭事务" },
      }),
    ).toEqual({
      status: "confirmed",
      confirmationOfToolCallId: "form-a",
      answer: { reason: "家庭事务" },
      forms: [{ formId: "form-a", answer: { reason: "家庭事务" } }],
    });
  });

  it("formats grouped answers with question labels and markdown bullets", () => {
    const request = askUserQuestionRequest(
      questionBlock({
        batch: true,
        questions: [
          { id: "expense_type", kind: "text", question: "费用类型？", fieldAssist: false, default: "交通费" },
          { id: "expense_date", kind: "text", question: "发生时间：", fieldAssist: false, default: "2026-06-28" },
          { id: "expense_amount", kind: "text", question: "金额", fieldAssist: false, default: "0" },
          { id: "expense_reason", kind: "text", question: "事由", fieldAssist: false, default: "办公相关支出" },
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

  it("builds ordered submitted-answer fields for the desktop result card", () => {
    const request = askUserQuestionRequest(
      questionBlock({
        batch: true,
        questions: [
          {
            id: "stamp_type",
            kind: "single",
            question: "印章类型？",
            options: [
              { id: "finance", label: "财务章" },
              { id: "contract", label: "合同章" },
            ],
          },
          {
            id: "use_date",
            kind: "date",
            question: "使用日期：",
            dateFormat: "yyyy-MM-dd HH:mm",
          },
          { id: "note", kind: "text", question: "备注。", fieldAssist: false },
        ],
      }),
    );

    expect(request).not.toBeNull();
    expect(
      askUserQuestionAnswerItems(
        request!,
        {
          stamp_type: "finance",
          use_date: "2026-07-16 08:00",
          note: "一段很长但必须完整保留给 tooltip 的备注",
        },
        { confirm: "确认", cancel: "取消" },
      ),
    ).toEqual([
      {
        id: "stamp_type",
        kind: "single",
        label: "印章类型",
        value: "财务章",
      },
      {
        id: "use_date",
        kind: "date",
        label: "使用日期",
        value: "2026-07-16 08:00",
      },
      {
        id: "note",
        kind: "text",
        label: "备注",
        value: "一段很长但必须完整保留给 tooltip 的备注",
      },
    ]);
  });

  it("builds submitted-answer fields for a linked confirmation", () => {
    const request = askUserQuestionRequest(
      questionBlock({
        batch: false,
        id: "confirmation",
        kind: "confirm",
        title: "公章使用申请确认",
        confirmationOfToolCallId: "form-1",
        questions: [{ id: "type", kind: "text", question: "印章类型？", fieldAssist: false }],
        answer: { type: "财务章" },
      }),
    );

    expect(request).not.toBeNull();
    expect(
      askUserQuestionAnswerItems(
        request!,
        { type: "财务章" },
        { confirm: "确认", cancel: "取消" },
      ),
    ).toEqual([
      { id: "type", kind: "text", label: "印章类型", value: "财务章" },
    ]);
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

  it("hides only source forms covered by a confirmation card", () => {
    const source = questionBlock({
      batch: true,
      title: "出差申请",
      questions: [{ id: "city", kind: "text", question: "城市？", fieldAssist: false }],
    });
    source.toolCallId = "form-1";
    const confirmation = questionBlock({
      batch: false,
      id: "confirmation",
      kind: "confirm",
      title: "确认表单",
      confirmationOfToolCallId: "form-1",
      questions: [{ id: "city", kind: "text", question: "城市？", fieldAssist: false }],
      answer: { city: "北京" },
    });
    confirmation.toolCallId = "confirm-1";

    const coveredIds = new Set(askUserQuestionConfirmationFormIds(confirmation));
    expect(coveredIds).toEqual(new Set(["form-1"]));
    expect(askUserQuestionReturnedConfirmationFormIds(confirmation)).toEqual([]);
    expect(hideAskUserQuestionToolBlock(source, coveredIds)).toBe(true);
    expect(hideAskUserQuestionToolBlock(confirmation, coveredIds)).toBe(false);
    expect(hideAskUserQuestionToolBlock(source, new Set(["form-2"]))).toBe(false);

    confirmation.formInteraction = {
      interactionId: "confirm-1",
      state: "confirmed",
      revision: 2,
      allowedActions: [],
      forms: [],
    };
    expect(askUserQuestionReturnedConfirmationFormIds(confirmation)).toEqual([
      "form-1",
    ]);
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
    const validationTerminal = block({}, {
      toolStatus: "error",
      resultText: "QUESTION_VALIDATION_FAILED: stop",
    });

    expect(isAskUserQuestionTerminalFailure(retryable)).toBe(false);
    expect(isAskUserQuestionTerminalFailure(terminal)).toBe(true);
    expect(isAskUserQuestionTerminalFailure(validationTerminal)).toBe(true);
    expect(isAskUserQuestionValidationTerminalFailure(validationTerminal)).toBe(true);
    expect(isAskUserQuestionValidationTerminalFailure(terminal)).toBe(false);
  });
});
