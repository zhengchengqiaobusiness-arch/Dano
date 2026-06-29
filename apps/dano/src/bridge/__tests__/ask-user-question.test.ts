import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  AskUserQuestionAnswerInput,
  AskUserQuestionDataSource,
  AskUserQuestionInputType,
  AskUserQuestionOption,
} from "../types.js";
import {
  askUserQuestionCoordinator,
  askUserQuestionTool,
} from "../ask-user-question.js";

function executeQuestion(
  toolCallId: string,
  params: {
    question: string;
    options?: (string | AskUserQuestionOption)[];
    inputType?: AskUserQuestionInputType;
    dataSource?: AskUserQuestionDataSource;
    multiple?: boolean;
    confirm?: true;
    default?: AskUserQuestionAnswerInput;
    questions?: {
      id?: string;
      question: string;
      options?: (string | AskUserQuestionOption)[];
      inputType?: AskUserQuestionInputType;
      dataSource?: AskUserQuestionDataSource;
      multiple?: boolean;
      default?: AskUserQuestionAnswerInput;
    }[];
  },
  signal?: AbortSignal,
) {
  return askUserQuestionTool.execute(
    toolCallId,
    params,
    signal,
    undefined,
    {} as never,
  );
}

describe("ask_user_question tool", () => {
  beforeEach(() => askUserQuestionCoordinator.cancelAll());

  it("instructs the model to collect required input and confirm final summaries", () => {
    expect(askUserQuestionTool.promptGuidelines).toEqual([
      "Use ask_user_question whenever you need user input to continue; do not ask the question only in assistant text.",
      "Call ask_user_question at most once per assistant response. If you need several answers, put every item in one questions array.",
      "If the user cancels ask_user_question, stop the current workflow. Do not ask again or retry unless the user sends a new message explicitly requesting it.",
      "Invoke ask_user_question as a native tool call. Never print, describe, or wrap a tool call in <question> tags, XML, JSON, Markdown, or other assistant text.",
      "If ask_user_question returns a validation error, retry silently with a corrected native tool call; do not explain the correction to the user.",
      "Set default on every non-confirmation question, including every item in questions, using the most likely or safest answer while still letting the user change it.",
      "When using questions, the top level must contain only questions. Put id, question, options, inputType, dataSource, multiple, and default inside each questions item.",
      "For forms, applications, or other user-reviewed summaries, call ask_user_question with confirm: true after presenting the final summary and before treating it as confirmed, ready to submit, or complete.",
    ]);
  });

  it("returns a free-text answer as structured tool details", async () => {
    const execution = executeQuestion("text-1", { question: "Project name?" });

    expect(
      askUserQuestionCoordinator.answer("text-1", {
        cancelled: false,
        answer: "  Dano  ",
      }),
    ).toEqual({ status: "answered", answer: "Dano" });
    await expect(execution).resolves.toMatchObject({
      content: [
        expect.objectContaining({ text: expect.stringContaining('"Dano"') }),
      ],
      details: { status: "answered", answer: "Dano" },
    });
  });

  it("returns a selected option", async () => {
    const execution = executeQuestion("choice-1", {
      question: "Deploy now?",
      options: ["Yes", "No"],
    });

    askUserQuestionCoordinator.answer("choice-1", {
      cancelled: false,
      answer: "Yes",
    });
    await expect(execution).resolves.toMatchObject({
      details: { status: "answered", answer: "Yes" },
    });
  });

  it("returns structured option ids while keeping labels for display", async () => {
    const execution = executeQuestion("structured-choice", {
      question: "Employee?",
      inputType: "select",
      options: [
        { id: "emp_1001", label: "Alice Chen", extra: { title: "Manager" } },
        { id: "emp_1002", label: "Bob Li" },
      ],
      default: "emp_1002",
    });

    askUserQuestionCoordinator.answer("structured-choice", {
      cancelled: false,
      answer: "emp_1001",
    });
    await expect(execution).resolves.toMatchObject({
      details: { status: "answered", answer: "emp_1001" },
    });
  });

  it.each([
    ["string id", "string:emp_1001", "emp_1001"],
    ["number id", "number:1", 1],
    ["numeric string id", "string:1", "1"],
    ["date string id", "string:2026-06-30 18:00:00", "2026-06-30 18:00:00"],
    ["colon string id", "string:leave:end", "leave:end"],
  ])("accepts select DOM typed keys for %s", async (_, answer, expected) => {
    const execution = executeQuestion(`typed-key-${String(expected)}`, {
      question: "Pick one",
      inputType: "select",
      options: [
        { id: "emp_1001", label: "Alice Chen" },
        { id: 1, label: "研发部" },
        { id: "1", label: "财务部" },
        { id: "2026-06-30 18:00:00", label: "2026-06-30 18:00:00（下班时间）" },
        { id: "leave:end", label: "结束时间" },
      ],
    });

    askUserQuestionCoordinator.answer(`typed-key-${String(expected)}`, {
      cancelled: false,
      answer,
    });
    await expect(execution).resolves.toMatchObject({
      details: { status: "answered", answer: expected },
    });
  });

  it("normalizes choice answers from ids, labels, and option items", async () => {
    const numberId = executeQuestion("choice-number", {
      question: "Department?",
      options: [
        { id: 1, label: "研发部" },
        { id: 2, label: "财务部" },
      ],
    });
    askUserQuestionCoordinator.answer("choice-number", {
      cancelled: false,
      answer: 1,
    });
    await expect(numberId).resolves.toMatchObject({
      details: { status: "answered", answer: 1 },
    });

    const objectItem = executeQuestion("choice-object", {
      question: "Department?",
      options: [{ id: 1, label: "研发部" }, { id: 2, label: "财务部" }],
    });
    askUserQuestionCoordinator.answer("choice-object", {
      cancelled: false,
      answer: { id: 1, label: "研发部" },
    });
    await expect(objectItem).resolves.toMatchObject({
      details: { status: "answered", answer: 1 },
    });

    const label = executeQuestion("choice-label", {
      question: "Department?",
      options: [{ id: 1, label: "研发部" }, { id: 2, label: "财务部" }],
    });
    askUserQuestionCoordinator.answer("choice-label", {
      cancelled: false,
      answer: "研发部",
    });
    await expect(label).resolves.toMatchObject({
      details: { status: "answered", answer: 1 },
    });

    const zero = executeQuestion("choice-zero", {
      question: "Department?",
      options: [{ id: 0, label: "未分配" }, { id: 1, label: "研发部" }],
    });
    askUserQuestionCoordinator.answer("choice-zero", {
      cancelled: false,
      answer: 0,
    });
    await expect(zero).resolves.toMatchObject({
      details: { status: "answered", answer: 0 },
    });

    const stringified = executeQuestion("choice-stringified", {
      question: "Department?",
      options: [{ id: 1, label: "研发部" }],
    });
    askUserQuestionCoordinator.answer("choice-stringified", {
      cancelled: false,
      answer: "1",
    });
    await expect(stringified).resolves.toMatchObject({
      details: { status: "answered", answer: 1 },
    });
  });

  it("keeps number and string option ids distinct", async () => {
    const execution = executeQuestion("choice-id-type", {
      question: "Department?",
      options: [{ id: 1, label: "A" }, { id: "1", label: "B" }],
    });

    askUserQuestionCoordinator.answer("choice-id-type", {
      cancelled: false,
      answer: "1",
    });
    await expect(execution).resolves.toMatchObject({
      details: { status: "answered", answer: "1" },
    });
  });

  it("normalizes multiple-choice option items to ids", async () => {
    const execution = executeQuestion("multiple-object", {
      question: "Departments?",
      options: [{ id: 1, label: "研发部" }, { id: 2, label: "财务部" }],
      multiple: true,
    });

    askUserQuestionCoordinator.answer("multiple-object", {
      cancelled: false,
      answer: [{ id: 1, label: "研发部" }, "财务部"],
    });
    await expect(execution).resolves.toMatchObject({
      details: { status: "answered", answer: [1, 2] },
    });
  });

  it("reports ambiguous labels as a user-facing validation error", async () => {
    const execution = executeQuestion("choice-duplicate-label", {
      question: "Department?",
      options: [{ id: 1, label: "研发部" }, { id: 2, label: "研发部" }],
    });

    expect(() =>
      askUserQuestionCoordinator.answer("choice-duplicate-label", {
        cancelled: false,
        answer: "研发部",
      }),
    ).toThrow("选项标签不唯一，请重新选择");
    askUserQuestionCoordinator.answer("choice-duplicate-label", {
      cancelled: false,
      answer: 1,
    });
    await expect(execution).resolves.toMatchObject({
      details: { status: "answered", answer: 1 },
    });
  });

  it("rejects duplicate structured option ids", async () => {
    await expect(
      executeQuestion("duplicate-ids", {
        question: "Employee?",
        options: [
          { id: "emp_1001", label: "Alice" },
          { id: "emp_1001", label: "Alice duplicate" },
        ],
      }),
    ).rejects.toThrow("non-empty and unique");
  });

  it("accepts remote select ids without requiring static options", async () => {
    const execution = executeQuestion("remote-select", {
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
      default: "emp_1002",
    });

    askUserQuestionCoordinator.answer("remote-select", {
      cancelled: false,
      answer: "emp_1001",
    });
    await expect(execution).resolves.toMatchObject({
      details: { status: "answered", answer: "emp_1001" },
    });
  });

  it("accepts a valid default answer without changing the final user answer", async () => {
    const execution = executeQuestion("choice-default", {
      question: "Deploy now?",
      options: ["Yes", "No"],
      default: "No",
    });

    askUserQuestionCoordinator.answer("choice-default", {
      cancelled: false,
      answer: "Yes",
    });
    await expect(execution).resolves.toMatchObject({
      details: { status: "answered", answer: "Yes" },
    });
  });

  it("returns multiple selected options", async () => {
    const execution = executeQuestion("multiple-1", {
      question: "Choose environments",
      options: ["Test", "Staging", "Production"],
      multiple: true,
    });

    askUserQuestionCoordinator.answer("multiple-1", {
      cancelled: false,
      answer: ["Test", "Staging"],
    });
    await expect(execution).resolves.toMatchObject({
      details: {
        status: "answered",
        answer: ["Test", "Staging"],
      },
    });
  });

  it("returns a custom answer when a single-choice question includes Other", async () => {
    const execution = executeQuestion("single-other", {
      question: "Leave type?",
      options: ["Annual leave", "Other"],
    });

    askUserQuestionCoordinator.answer("single-other", {
      cancelled: false,
      answer: "Volunteer leave",
    });
    await expect(execution).resolves.toMatchObject({
      details: { status: "answered", answer: "Volunteer leave" },
    });
  });

  it("returns one custom answer with multiple selected options", async () => {
    const execution = executeQuestion("multiple-other", {
      question: "Choose environments",
      options: ["Test", "Production", "Other"],
      multiple: true,
    });

    askUserQuestionCoordinator.answer("multiple-other", {
      cancelled: false,
      answer: ["Test", "Disaster recovery"],
    });
    await expect(execution).resolves.toMatchObject({
      details: {
        status: "answered",
        answer: ["Test", "Disaster recovery"],
      },
    });
  });

  it("rejects a bare Other answer", async () => {
    const execution = executeQuestion("bare-other", {
      question: "Leave type?",
      options: ["事假", "其他"],
    });

    expect(() =>
      askUserQuestionCoordinator.answer("bare-other", {
        cancelled: false,
        answer: "其他",
      }),
    ).toThrow("请输入其他回答");
    askUserQuestionCoordinator.answer("bare-other", {
      cancelled: false,
      answer: "志愿者假",
    });
    await expect(execution).resolves.toMatchObject({
      details: { status: "answered", answer: "志愿者假" },
    });
  });

  it("rejects custom answers when Other was not offered", async () => {
    const execution = executeQuestion("custom-not-offered", {
      question: "Leave type?",
      options: ["年假", "事假"],
    });

    expect(() =>
      askUserQuestionCoordinator.answer("custom-not-offered", {
        cancelled: false,
        answer: "志愿者假",
      }),
    ).toThrow("答案必须匹配一个可选项");
    askUserQuestionCoordinator.answer("custom-not-offered", {
      cancelled: false,
      answer: "年假",
    });
    await expect(execution).resolves.toMatchObject({
      details: { status: "answered", answer: "年假" },
    });
  });

  it("rejects more than one custom answer in multiple choice", async () => {
    const execution = executeQuestion("multiple-custom", {
      question: "Choose environments",
      options: ["Test", "Other"],
      multiple: true,
    });

    expect(() =>
      askUserQuestionCoordinator.answer("multiple-custom", {
        cancelled: false,
        answer: ["Disaster recovery", "Development"],
      }),
    ).toThrow("只能填写一个其他回答");
    askUserQuestionCoordinator.answer("multiple-custom", {
      cancelled: false,
      answer: ["Test", "Development"],
    });
    await expect(execution).resolves.toMatchObject({
      details: { status: "answered", answer: ["Test", "Development"] },
    });
  });

  it("returns a boolean confirmation", async () => {
    const execution = executeQuestion("confirm-1", {
      question: "Deploy now?",
      confirm: true,
    });

    askUserQuestionCoordinator.answer("confirm-1", {
      cancelled: false,
      answer: false,
    });
    await expect(execution).resolves.toMatchObject({
      details: { status: "answered", answer: false },
    });
  });

  it("returns grouped answers from one tool confirmation", async () => {
    const execution = askUserQuestionTool.execute(
      "group-1",
      {
        questions: [
          { id: "name", question: "Name?", default: "Dano" },
          {
            id: "env",
            question: "Environment?",
            options: ["Test", "Production"],
            default: "Test",
          },
          {
            id: "features",
            question: "Features?",
            options: ["Chat", "Deploy"],
            multiple: true,
            default: ["Chat"],
          },
        ],
      },
      undefined,
      undefined,
      {} as never,
    );

    askUserQuestionCoordinator.answer("group-1", {
      cancelled: false,
      answer: {
        name: "Dano",
        env: "Production",
        features: ["Chat", "Deploy"],
      },
    });
    await expect(execution).resolves.toMatchObject({
      details: {
        status: "answered",
        answer: {
          name: "Dano",
          env: "Production",
          features: ["Chat", "Deploy"],
        },
      },
    });
  });

  it("rejects simultaneous separate questions so the model retries as one grouped card", async () => {
    const first = executeQuestion("separate-1", {
      question: "Leave type?",
      options: ["Annual", "Sick"],
      default: "Annual",
    });
    const second = executeQuestion("separate-2", {
      question: "Start date?",
      options: ["Today", "Tomorrow"],
      default: "Today",
    });

    await expect(second).rejects.toThrow("exactly one ask_user_question call");
    await expect(first).rejects.toThrow("exactly one ask_user_question call");
    expect(() =>
      askUserQuestionCoordinator.answer("separate-1", {
        cancelled: false,
        answer: "Annual",
      }),
    ).toThrow("Pending question not found");
  });

  it("explains how to fix grouped calls that mix top-level question fields", async () => {
    await expect(
      askUserQuestionTool.execute(
        "group-mixed",
        {
          question: "Leave details?",
          default: "事假",
          questions: [
            { id: "leave_type", question: "Leave type?", default: "事假" },
          ],
        },
        undefined,
        undefined,
        {} as never,
      ),
    ).rejects.toThrow("top level may contain only questions");
  });

  it("rejects grouped answers missing a question id", async () => {
    const execution = askUserQuestionTool.execute(
      "group-missing",
      {
        questions: [
          { id: "name", question: "Name?" },
          { id: "env", question: "Environment?", options: ["Test", "Prod"] },
        ],
      },
      undefined,
      undefined,
      {} as never,
    );

    expect(() =>
      askUserQuestionCoordinator.answer("group-missing", {
        cancelled: false,
        answer: { name: "Dano" },
      }),
    ).toThrow("Missing answer");
    askUserQuestionCoordinator.answer("group-missing", {
      cancelled: false,
      answer: { name: "Dano", env: "Test" },
    });
    await expect(execution).resolves.toMatchObject({
      details: { status: "answered" },
    });
  });

  it("rejects invalid multiple-choice answers without settling", async () => {
    const execution = executeQuestion("multiple-2", {
      question: "Choose environments",
      options: ["Test", "Production"],
      multiple: true,
    });

    expect(() =>
      askUserQuestionCoordinator.answer("multiple-2", {
        cancelled: false,
        answer: ["Unknown"],
      }),
    ).toThrow("答案必须匹配一个可选项");
    askUserQuestionCoordinator.answer("multiple-2", {
      cancelled: false,
      answer: ["Production"],
    });
    await expect(execution).resolves.toMatchObject({
      details: { status: "answered", answer: ["Production"] },
    });
  });

  it("rejects incompatible confirmation parameters", async () => {
    await expect(
      executeQuestion("confirm-invalid", {
        question: "Deploy now?",
        options: ["Yes", "No"],
        confirm: true,
      }),
    ).rejects.toThrow("cannot provide options");
  });

  it("rejects grouped confirmation parameters instead of waiting forever", async () => {
    await expect(
      askUserQuestionTool.execute(
        "group-confirm-invalid",
        {
          questions: [
            {
              id: "confirm_leave",
              question: "Submit leave request?",
              options: ["Submit", "Revise"],
              confirm: true,
            },
          ],
        },
        undefined,
        undefined,
        {} as never,
      ),
    ).rejects.toThrow("cannot provide options");
    expect(() =>
      askUserQuestionCoordinator.answer("group-confirm-invalid", {
        cancelled: false,
        answer: true,
      }),
    ).toThrow("Pending question not found");
  });

  it("returns cancellation as a successful tool result", async () => {
    const execution = executeQuestion("cancel-1", { question: "Continue?" });

    askUserQuestionCoordinator.answer("cancel-1", { cancelled: true });
    await expect(execution).resolves.toMatchObject({
      content: [
        {
          type: "text",
          text: "User cancelled the question. Stop the current workflow. Do not ask another question or retry unless the user sends a new message explicitly requesting it.",
        },
      ],
      details: { status: "cancelled" },
    });
  });

  it("rejects an invalid option without settling the question", async () => {
    const execution = executeQuestion("choice-2", {
      question: "Pick one",
      options: ["A", "B"],
    });

    expect(() =>
      askUserQuestionCoordinator.answer("choice-2", {
        cancelled: false,
        answer: "C",
      }),
    ).toThrow("答案必须匹配一个可选项");
    askUserQuestionCoordinator.answer("choice-2", {
      cancelled: false,
      answer: "B",
    });
    await expect(execution).resolves.toMatchObject({
      details: { status: "answered", answer: "B" },
    });
  });

  it("rejects answers for unknown tool calls", () => {
    expect(() =>
      askUserQuestionCoordinator.answer("missing", { cancelled: true }),
    ).toThrow("Pending question not found");
  });

  it("shares pending questions across Dano dev runtime module reloads", async () => {
    vi.resetModules();
    const firstRuntime = await import("../ask-user-question.js");
    vi.resetModules();
    const reloadedRuntime = await import("../ask-user-question.js");

    expect(reloadedRuntime.askUserQuestionCoordinator).toBe(
      firstRuntime.askUserQuestionCoordinator,
    );
  });

  it("removes a pending question when its agent turn aborts", async () => {
    const controller = new AbortController();
    const execution = executeQuestion(
      "abort-1",
      { question: "Wait?" },
      controller.signal,
    );

    controller.abort();
    await expect(execution).rejects.toThrow("Question was aborted");
    expect(() =>
      askUserQuestionCoordinator.answer("abort-1", { cancelled: true }),
    ).toThrow("Pending question not found");
  });
});
