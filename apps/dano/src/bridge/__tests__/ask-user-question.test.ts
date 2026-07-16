import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  AskUserQuestionAnswerInput,
  AskUserQuestionDataSource,
  AskUserQuestionInputType,
  AskUserQuestionOption,
} from "../types.js";
import {
  ASK_USER_QUESTION_PRESENTATION_RETRY_CODE,
  ASK_USER_QUESTION_PRESENTATION_TERMINAL_CODE,
  ASK_USER_QUESTION_VALIDATION_TERMINAL_CODE,
} from "../types.js";
import {
  ASK_USER_QUESTION_CANCELLED_CODE,
  AskUserQuestionCoordinator,
  askUserQuestionCoordinator,
  askUserQuestionTool,
  normalizeAskUserQuestionCardRequest,
} from "../ask-user-question.js";

function executeQuestion(
  toolCallId: string,
  params: {
    question?: string;
    title?: string;
    options?: (string | AskUserQuestionOption)[];
    inputType?: AskUserQuestionInputType;
    dataSource?: AskUserQuestionDataSource;
    multiple?: boolean;
    confirm?: true;
    dateFormat?: unknown;
    required?: unknown;
    default?: AskUserQuestionAnswerInput;
    questions?: {
      id?: string;
      question: string;
      options?: (string | AskUserQuestionOption)[];
      inputType?: AskUserQuestionInputType;
      dataSource?: AskUserQuestionDataSource;
      multiple?: boolean;
      dateFormat?: unknown;
      required?: unknown;
      default?: AskUserQuestionAnswerInput;
    }[];
  },
  signal?: AbortSignal,
) {
  return askUserQuestionTool.execute(
    toolCallId,
    withRequiredDefault(params),
    signal,
    undefined,
    {} as never,
  );
}

function withRequiredDefault<T extends {
  title?: string;
  options?: (string | AskUserQuestionOption)[];
  inputType?: AskUserQuestionInputType;
  multiple?: boolean;
  confirm?: true;
  default?: AskUserQuestionAnswerInput;
  questions?: {
    options?: (string | AskUserQuestionOption)[];
    inputType?: AskUserQuestionInputType;
    multiple?: boolean;
    confirm?: true;
    default?: AskUserQuestionAnswerInput;
  }[];
}>(params: T): T {
  if (params.confirm) return params;
  if (params.questions) {
    return {
      ...params,
      title: params.title ?? "测试表单",
      questions: params.questions.map(question => question.confirm || question.default !== undefined
        ? question
        : { ...question, default: defaultForQuestion(question) }),
    };
  }
  return params.default !== undefined
    ? params
    : { ...params, default: defaultForQuestion(params) };
}

function defaultForQuestion(question: {
  options?: (string | AskUserQuestionOption)[];
  inputType?: AskUserQuestionInputType;
  multiple?: boolean;
}): AskUserQuestionAnswerInput {
  const firstOption = question.options?.[0];
  const firstOptionValue =
    typeof firstOption === "object" ? firstOption.id : firstOption;
  if (question.multiple || question.inputType === "checkbox") {
    return firstOptionValue === undefined ? ["Default"] : [firstOptionValue];
  }
  if (firstOptionValue !== undefined) return firstOptionValue;
  if (question.inputType === "date") return "2026-07-07";
  return "Default answer";
}

describe("ask_user_question tool", () => {
  beforeEach(() => askUserQuestionCoordinator.cancelAll());

  it("times out only before presentation and bounds presentation retries", async () => {
    vi.useFakeTimers();
    try {
      const coordinator = new AskUserQuestionCoordinator(100, 1);
      const controller = new AbortController();
      const first = coordinator.wait(
        "presentation-1",
        { question: "姓名？", default: "张三" },
        controller.signal,
      );
      const firstFailure = expect(first).rejects.toThrow(
        ASK_USER_QUESTION_PRESENTATION_RETRY_CODE,
      );
      await vi.advanceTimersByTimeAsync(100);
      await firstFailure;

      const second = coordinator.wait(
        "presentation-2",
        { question: "姓名？", default: "张三" },
        controller.signal,
      );
      const terminalFailure = expect(second).rejects.toThrow(
        ASK_USER_QUESTION_PRESENTATION_TERMINAL_CODE,
      );
      await vi.advanceTimersByTimeAsync(100);
      await terminalFailure;
    } finally {
      vi.useRealTimers();
    }
  });

  it("fails terminally when presentation retries cannot be correlated without a signal", async () => {
    vi.useFakeTimers();
    try {
      const coordinator = new AskUserQuestionCoordinator(100, 2);
      const pending = coordinator.wait(
        "presentation-without-signal",
        { question: "姓名？", default: "张三" },
        undefined,
      );
      const terminalFailure = expect(pending).rejects.toThrow(
        ASK_USER_QUESTION_PRESENTATION_TERMINAL_CODE,
      );

      await vi.advanceTimersByTimeAsync(100);
      await terminalFailure;
    } finally {
      vi.useRealTimers();
    }
  });

  it("disables the presentation watchdog after the matching card is visible", async () => {
    vi.useFakeTimers();
    try {
      const coordinator = new AskUserQuestionCoordinator(100, 2);
      const pending = coordinator.wait(
        "presented-question",
        { question: "姓名？", default: "张三" },
        undefined,
      );
      coordinator.present("presented-question");
      await vi.advanceTimersByTimeAsync(10_000);
      coordinator.answer("presented-question", {
        cancelled: false,
        answer: "李四",
      });
      await expect(pending).resolves.toEqual({
        status: "answered",
        answer: "李四",
      });
    } finally {
      vi.useRealTimers();
    }
  });

  it("logs lifecycle transitions without question content", async () => {
    const info = vi.spyOn(console, "info").mockImplementation(() => {});
    const coordinator = new AskUserQuestionCoordinator();
    const pending = coordinator.wait(
      "logged-question",
      { question: "不得写入日志的请假原因", default: "个人事务" },
      undefined,
    );

    coordinator.present("logged-question");
    coordinator.answer("logged-question", {
      cancelled: false,
      answer: "家庭事务",
    });
    await pending;

    expect(info.mock.calls.map(([message]) => message)).toEqual([
      "[ask_user_question] state=awaiting_presentation toolCallId=logged-question",
      "[ask_user_question] state=presented toolCallId=logged-question",
      "[ask_user_question] state=answered toolCallId=logged-question",
    ]);
    expect(JSON.stringify(info.mock.calls)).not.toContain("请假原因");
  });

  it("clears the presentation watchdog on abort and disposal", async () => {
    vi.useFakeTimers();
    try {
      const coordinator = new AskUserQuestionCoordinator(100, 2);
      const controller = new AbortController();
      const aborted = coordinator.wait(
        "aborted-question",
        { question: "姓名？", default: "张三" },
        controller.signal,
      );
      const abortedFailure = expect(aborted).rejects.toThrow(
        ASK_USER_QUESTION_CANCELLED_CODE,
      );
      controller.abort();
      await abortedFailure;

      const disposed = coordinator.wait(
        "disposed-question",
        { question: "姓名？", default: "张三" },
        undefined,
      );
      const disposedFailure = expect(disposed).rejects.toThrow("disposed");
      coordinator.cancelAll();
      await disposedFailure;
      await vi.advanceTimersByTimeAsync(1_000);
    } finally {
      vi.useRealTimers();
    }
  });

  it("instructs the model to collect required input and confirm final summaries", () => {
    expect(askUserQuestionTool.promptGuidelines).toEqual([
      "Use ask_user_question whenever you need user input to continue; do not ask the question only in assistant text.",
      "When the user asks to fill in a form, complete a form, or provide form fields, collect the fields with ask_user_question.",
      "Call ask_user_question at most once per assistant response. If you need several answers, put every item in one questions array.",
      "If the user cancels ask_user_question, stop the current workflow. Do not ask again or retry unless the user sends a new message explicitly requesting it.",
      "Invoke ask_user_question as a native tool call. Never print, describe, or wrap a tool call in <question> tags, XML, JSON, Markdown, or other assistant text.",
      "If ask_user_question returns a validation error, retry silently with a corrected native tool call; do not explain the correction to the user.",
      "Give every non-confirmation question a context-based recommended non-empty default. Do not use empty string or placeholder defaults.",
      "Set required:true only when an answer is mandatory. required defaults to false.",
      "For date fields, use inputType:\"date\" and provide dateFormat such as \"yyyy-MM-dd\" or \"yyyy-MM-dd HH:mm\". The dateFormat configures the frontend date control display and submitted output.",
      "Dano returns the user's date answer as submitted; convert it yourself if a downstream interface needs another business format.",
      "When using questions, provide a concise top-level title and put each field's id, question, options, inputType, dateFormat, required, dataSource, multiple, and default inside its questions item.",
      "After a grouped form is answered, call ask_user_question with only {confirm:true}. Do not send confirmation text, the prior answers, or a relation id; Dano binds the latest saved form.",
    ]);
  });

  it.each([
    [
      "single text",
      { question: "姓名？", default: "张三" },
      { batch: false, kind: "text", id: "answer", question: "姓名？", default: "张三" },
    ],
    [
      "compatible aliases",
      {
        questions: {
          key: "reason",
          title: "请假原因？",
          type: "textarea",
          defaultValue: "个人事务",
        },
      },
      {
        batch: true,
        questions: [
          {
            id: "reason",
            kind: "text",
            inputType: "textarea",
            question: "请假原因？",
            default: "个人事务",
          },
        ],
      },
    ],
    [
      "grouped fields with top-level instruction text",
      {
        prompt: "请补充请假信息",
        questions: [
          {
            id: "leave_type",
            question: "请假类型？",
            options: ["事假", "病假"],
            default: "事假",
          },
          { id: "reason", question: "原因？", default: "个人事务" },
        ],
      },
      {
        batch: true,
        questions: [
          {
            id: "leave_type",
            kind: "single",
            question: "请假类型？",
            options: [
              { id: "事假", label: "事假" },
              { id: "病假", label: "病假" },
            ],
            default: "事假",
          },
          {
            id: "reason",
            kind: "text",
            question: "原因？",
            default: "个人事务",
          },
        ],
      },
    ],
    [
      "date",
      {
        question: "开始日期？",
        inputType: "date",
        dateFormat: "yyyy-MM-dd",
        default: "2026-07-14",
      },
      {
        batch: false,
        id: "answer",
        kind: "date",
        question: "开始日期？",
        dateFormat: "yyyy-MM-dd",
        default: "2026-07-14",
      },
    ],
  ])("normalizes accepted %s calls into browser-safe card requests", (_, input, expected) => {
    expect(normalizeAskUserQuestionCardRequest(input)).toEqual(expected);
  });

  it("drops redundant grouped top-level semantics from the card protocol", () => {
    expect(
      normalizeAskUserQuestionCardRequest({
        title: "测试表单",
        options: ["A", "B"],
        questions: [
          { id: "reason", question: "原因？", default: "个人事务" },
          { id: "note", question: "备注？", default: "无" },
        ],
      }),
    ).toMatchObject({
      batch: true,
      title: "测试表单",
      questions: [
        { id: "reason", kind: "text", default: "个人事务" },
        { id: "note", kind: "text", default: "无" },
      ],
    });
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

  it("rejects empty string defaults", async () => {
    await expect(executeQuestion("empty-default", {
      question: "Reason?",
      default: "",
    })).rejects.toThrow("default 必须是非空推荐值");
  });

  it("rejects non-confirmation questions without defaults", async () => {
    await expect(
      askUserQuestionTool.execute(
        "missing-default",
        { question: "Reason?" },
        undefined,
        undefined,
        {} as never,
      ),
    ).rejects.toThrow("默认答案缺失");
  });

  it("returns date answers exactly as submitted", async () => {
    const execution = executeQuestion("date-1", {
      question: "Start date?",
      inputType: "date",
      dateFormat: "yyyy-MM-dd",
      required: true,
    });

    askUserQuestionCoordinator.answer("date-1", {
      cancelled: false,
      answer: "2026/07/03",
    });
    await expect(execution).resolves.toMatchObject({
      details: { status: "answered", answer: "2026/07/03" },
    });
  });

  it("accepts date-time-to-minute formats for date questions", async () => {
    const execution = executeQuestion("date-time-1", {
      question: "Start time?",
      inputType: "date",
      dateFormat: "yyyy-MM-dd HH:mm",
      default: "2026-07-03 09:30",
    });

    askUserQuestionCoordinator.answer("date-time-1", {
      cancelled: false,
      answer: "2026-07-03 10:45",
    });
    await expect(execution).resolves.toMatchObject({
      details: { status: "answered", answer: "2026-07-03 10:45" },
    });
  });

  it("accepts custom dateFormat strings supported by the frontend formatter", async () => {
    const execution = executeQuestion("date-custom-format", {
      question: "Start time?",
      inputType: "date",
      dateFormat: "yyyy/MM/dd HH:mm",
      default: "2026/07/03 09:30",
    });

    askUserQuestionCoordinator.answer("date-custom-format", {
      cancelled: false,
      answer: "2026/07/03 10:45",
    });
    await expect(execution).resolves.toMatchObject({
      details: { status: "answered", answer: "2026/07/03 10:45" },
    });
  });

  it("requires dateFormat on date questions", async () => {
    await expect(
      executeQuestion("date-missing-format", {
        question: "Start date?",
        inputType: "date",
      }),
    ).rejects.toThrow("dateFormat is required");
  });

  it("rejects unsupported dateFormat values before rendering", async () => {
    await expect(
      executeQuestion("date-format-seconds", {
        question: "Start time?",
        inputType: "date",
        dateFormat: "yyyy-MM-dd HH:mm:ss",
      }),
    ).rejects.toThrow("seconds and time zones are not supported");

    await expect(
      executeQuestion("date-format-missing-day", {
        question: "Start month?",
        inputType: "date",
        dateFormat: "yyyy-MM",
      }),
    ).rejects.toThrow("must include year, month, and day");

    await expect(
      executeQuestion("date-format-12-hour", {
        question: "Start time?",
        inputType: "date",
        dateFormat: "yyyy-MM-dd h:mm",
      }),
    ).rejects.toThrow("must use 24-hour H/HH tokens");
  });

  it("uses required only to decide whether blank date answers are allowed", async () => {
    const optional = executeQuestion("date-empty-optional", {
      question: "Start date?",
      inputType: "date",
      dateFormat: "yyyy-MM-dd",
    });
    askUserQuestionCoordinator.answer("date-empty-optional", {
      cancelled: false,
      answer: "",
    });
    await expect(optional).resolves.toMatchObject({
      details: { status: "answered", answer: "" },
    });

    const required = executeQuestion("date-empty-required", {
      question: "Start date?",
      inputType: "date",
      dateFormat: "yyyy-MM-dd",
      required: true,
    });
    expect(() =>
      askUserQuestionCoordinator.answer("date-empty-required", {
        cancelled: false,
        answer: "",
      }),
    ).toThrow("答案不能为空");
    askUserQuestionCoordinator.answer("date-empty-required", {
      cancelled: false,
      answer: "2026-07-03",
    });
    await expect(required).resolves.toMatchObject({
      details: { status: "answered", answer: "2026-07-03" },
    });
  });

  it("validates non-empty date defaults against dateFormat", async () => {
    await expect(
      executeQuestion("date-invalid-default", {
        question: "Start date?",
        inputType: "date",
        dateFormat: "yyyy-MM-dd",
        default: "2026/07/03",
      }),
    ).rejects.toThrow("默认日期必须匹配 dateFormat: yyyy-MM-dd");
  });

  it("rejects non-boolean required values", async () => {
    await expect(
      executeQuestion("required-string", {
        question: "Start date?",
        inputType: "date",
        dateFormat: "yyyy-MM-dd",
        required: "true",
      }),
    ).rejects.toThrow("required must be a boolean");
  });

  it("rejects dateFormat on non-date questions", async () => {
    await expect(
      executeQuestion("text-date-format", {
        question: "Reason?",
        dateFormat: "yyyy-MM-dd",
      }),
    ).rejects.toThrow("dateFormat is only allowed");
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

  it("binds confirm-only calls to the latest submitted grouped form", async () => {
    const coordinator = new AskUserQuestionCoordinator();
    const controller = new AbortController();
    const form = coordinator.wait(
      "form-1",
      {
        title: "公章使用申请",
        questions: [{ id: "reason", question: "用途？", default: "签署合同" }],
      },
      controller.signal,
    );
    coordinator.present("form-1");
    coordinator.answer("form-1", {
      cancelled: false,
      answer: { reason: "签署采购合同" },
    });
    await expect(form).resolves.toEqual({
      status: "answered",
      answer: { reason: "签署采购合同" },
    });

    const confirmation = coordinator.wait(
      "confirm-1",
      {
        confirm: true,
        title: "旧版确认标题",
        question: "是否确认提交？",
        options: ["确认", "返回修改"],
        default: "确认",
      },
      controller.signal,
    );
    expect(coordinator.cardRequest("confirm-1")).toEqual({
      batch: false,
      kind: "confirm",
      id: "confirmation",
      title: "公章使用申请确认",
      confirmationOfToolCallId: "form-1",
      questions: [expect.objectContaining({ id: "reason", question: "用途？" })],
      answer: { reason: "签署采购合同" },
    });

    expect(coordinator.update("confirm-1", { reason: "签署销售合同" })).toMatchObject({
      answer: { reason: "签署销售合同" },
    });
    coordinator.answer("confirm-1", { cancelled: false, answer: true });
    await expect(confirmation).resolves.toEqual({
      status: "confirmed",
      confirmationOfToolCallId: "form-1",
      answer: { reason: "签署销售合同" },
    });
  });

  it("rejects confirm-only calls without a submitted grouped form", async () => {
    const coordinator = new AskUserQuestionCoordinator();
    await expect(
      coordinator.wait("confirm-without-form", { confirm: true }, new AbortController().signal),
    ).rejects.toThrow("only be called after the user submitted a grouped form");
  });

  it("rejects grouped forms without a top-level title", async () => {
    const coordinator = new AskUserQuestionCoordinator();
    await expect(
      coordinator.wait(
        "form-without-title",
        { questions: [{ id: "reason", question: "用途？", default: "签署合同" }] },
        new AbortController().signal,
      ),
    ).rejects.toThrow("Grouped forms require a top-level title");
  });

  it("returns grouped answers from one tool confirmation", async () => {
    const execution = askUserQuestionTool.execute(
      "group-1",
      {
        title: "测试表单",
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

  it("returns grouped date answers from one valid batch card", async () => {
    const execution = askUserQuestionTool.execute(
      "group-date",
      {
        title: "测试表单",
        questions: [
          {
            id: "start_at",
            question: "Start date?",
            inputType: "date",
            dateFormat: "yyyy-MM-dd",
            default: "2026-07-03",
            required: true,
          },
          { id: "reason", question: "Reason?", default: "Annual leave", required: true },
        ],
      },
      undefined,
      undefined,
      {} as never,
    );

    askUserQuestionCoordinator.answer("group-date", {
      cancelled: false,
      answer: { start_at: "2026-07-03", reason: "Annual leave" },
    });
    await expect(execution).resolves.toMatchObject({
      details: {
        status: "answered",
        answer: { start_at: "2026-07-03", reason: "Annual leave" },
      },
    });
  });

  it("accepts compatible single-question object and alias fields", async () => {
    const execution = askUserQuestionTool.execute(
      "compat-single-object",
      {
        title: "测试表单",
        questions: {
          key: "description",
          title: "请填写说明",
          type: "textarea",
          defaultValue: "默认内容",
        },
      } as never,
      undefined,
      undefined,
      {} as never,
    );

    askUserQuestionCoordinator.answer("compat-single-object", {
      cancelled: false,
      answer: { description: "更新后的说明" },
    });
    await expect(execution).resolves.toMatchObject({
      details: {
        status: "answered",
        answer: { description: "更新后的说明" },
      },
    });
  });

  it("accepts JSON-stringified compatible questions", async () => {
    const execution = askUserQuestionTool.execute(
      "compat-json-string",
      {
        title: "测试表单",
        questions: JSON.stringify({
          key: "description",
          title: "请填写说明",
          type: "textarea",
          defaultValue: "默认内容",
        }),
      } as never,
      undefined,
      undefined,
      {} as never,
    );

    askUserQuestionCoordinator.answer("compat-json-string", {
      cancelled: false,
      answer: { description: "默认内容" },
    });
    await expect(execution).resolves.toMatchObject({
      details: {
        status: "answered",
        answer: { description: "默认内容" },
      },
    });
  });

  it("accepts a JSON-stringified questions array as a grouped form", async () => {
    const coordinator = new AskUserQuestionCoordinator();
    const execution = coordinator.wait(
      "compat-json-array",
      {
        title: "公章使用申请",
        questions: JSON.stringify([
          {
            id: "seal_id",
            question: "印章类型？",
            options: ["公章", "合同章"],
            default: "公章",
            required: true,
          },
          {
            id: "reason",
            question: "用章事由？",
            inputType: "textarea",
            default: "签署合同",
            required: true,
          },
        ]),
      },
      new AbortController().signal,
    );

    expect(coordinator.cardRequest("compat-json-array")).toMatchObject({
      batch: true,
      title: "公章使用申请",
      questions: [
        { id: "seal_id", kind: "single", default: "公章" },
        { id: "reason", kind: "text", default: "签署合同" },
      ],
    });
    coordinator.present("compat-json-array");
    coordinator.answer("compat-json-array", {
      cancelled: false,
      answer: { seal_id: "合同章", reason: "签署采购合同" },
    });
    await expect(execution).resolves.toEqual({
      status: "answered",
      answer: { seal_id: "合同章", reason: "签署采购合同" },
    });
  });

  it("rejects malformed JSON-stringified questions with a clear error", async () => {
    const coordinator = new AskUserQuestionCoordinator();
    await expect(
      coordinator.wait(
        "compat-json-malformed",
        { title: "公章使用申请", questions: '[{"id":"seal_id"' },
        new AbortController().signal,
      ),
    ).rejects.toThrow("questions must be valid JSON");
  });

  it("uses the configured validation retry count before terminating", async () => {
    const coordinator = new AskUserQuestionCoordinator(5_000, 3);
    const controller = new AbortController();
    const malformed = {
      title: "公章使用申请",
      questions: '[{"id":"seal_id"',
    };

    for (let attempt = 1; attempt <= 3; attempt += 1) {
      const error: Error = await coordinator
        .wait(`invalid-retry-${attempt}`, malformed, controller.signal)
        .then(
          () => { throw new Error("Expected validation failure"); },
          cause => cause instanceof Error ? cause : new Error(String(cause)),
        );
      expect(error.message).toContain("questions must be valid JSON");
      expect(error.message).not.toContain(
        ASK_USER_QUESTION_VALIDATION_TERMINAL_CODE,
      );
    }
    await expect(
      coordinator.wait("invalid-retry-4", malformed, controller.signal),
    ).rejects.toThrow(ASK_USER_QUESTION_VALIDATION_TERMINAL_CODE);
  });

  it("still requires a title for JSON-stringified grouped forms", async () => {
    const coordinator = new AskUserQuestionCoordinator();
    await expect(
      coordinator.wait(
        "compat-json-missing-title",
        {
          questions: JSON.stringify([
            { id: "seal_id", question: "印章类型？", default: "公章" },
          ]),
        },
        new AbortController().signal,
      ),
    ).rejects.toThrow("Grouped forms require a top-level title");
  });

  it("ignores redundant top-level fields on a complete JSON-stringified grouped form", async () => {
    const coordinator = new AskUserQuestionCoordinator();
    const pending = coordinator.wait(
      "compat-json-mixed-fields",
      {
        title: "公章使用申请",
        options: ["应忽略 A", "应忽略 B"],
        questions: JSON.stringify([
          { id: "seal_id", question: "印章类型？", default: "公章" },
          { id: "reason", question: "用章事由？", default: "签署合同" },
        ]),
      },
      new AbortController().signal,
    );

    expect(coordinator.cardRequest("compat-json-mixed-fields")).toMatchObject({
      batch: true,
      title: "公章使用申请",
      questions: [
        { id: "seal_id", kind: "text", default: "公章" },
        { id: "reason", kind: "text", default: "签署合同" },
      ],
    });
    coordinator.answer("compat-json-mixed-fields", {
      cancelled: false,
      answer: { seal_id: "公章", reason: "签署合同" },
    });
    await expect(pending).resolves.toMatchObject({ status: "answered" });
  });

  it("folds compatible top-level fields into a single grouped question", async () => {
    const execution = askUserQuestionTool.execute(
      "compat-single-mixed",
      {
        title: "测试表单",
        question: "请假类型？",
        options: ["事假", "病假"],
        default: "事假",
        required: true,
        confirm: true,
        questions: [{ id: "leave_type" }],
      } as never,
      undefined,
      undefined,
      {} as never,
    );

    askUserQuestionCoordinator.answer("compat-single-mixed", {
      cancelled: false,
      answer: { leave_type: "病假" },
    });
    await expect(execution).resolves.toMatchObject({
      details: {
        status: "answered",
        answer: { leave_type: "病假" },
      },
    });
  });

  it.each([
    ["question", { question: "请一次补充请假信息" }],
    ["title", { title: "请一次补充请假信息" }],
    ["label", { label: "请一次补充请假信息" }],
    ["prompt", { prompt: "请一次补充请假信息" }],
  ])("ignores top-level %s text on compatible multi-question forms", async (_, mixed) => {
    const execution = askUserQuestionTool.execute(
      "compat-multi-text-mixed",
      {
        title: "测试表单",
        ...mixed,
        questions: [
          {
            id: "leave_type",
            question: "请假类型？",
            options: ["事假", "病假"],
            default: "事假",
          },
          {
            id: "reason",
            question: "请假原因？",
            default: "个人事务",
          },
        ],
      } as never,
      undefined,
      undefined,
      {} as never,
    );

    askUserQuestionCoordinator.answer("compat-multi-text-mixed", {
      cancelled: false,
      answer: { leave_type: "病假", reason: "发烧" },
    });
    await expect(execution).resolves.toMatchObject({
      details: {
        status: "answered",
        answer: { leave_type: "病假", reason: "发烧" },
      },
    });
  });

  it("rejects a retry question without cancelling the pending form", async () => {
    const controller = new AbortController();
    const first = executeQuestion("separate-1", {
      question: "Leave type?",
      options: ["Annual", "Sick"],
      default: "Annual",
    }, controller.signal);
    const second = executeQuestion("separate-2", {
      question: "Start date?",
      options: ["Today", "Tomorrow"],
      default: "Today",
    }, controller.signal);

    await expect(second).rejects.toThrow("exactly one native ask_user_question call");
    await expect(second).rejects.not.toThrow("waiting");
    askUserQuestionCoordinator.answer("separate-1", {
      cancelled: false,
      answer: "Annual",
    });
    await expect(first).resolves.toMatchObject({
      details: { status: "answered", answer: "Annual" },
    });
  });

  it("allows pending questions from different agent turns", async () => {
    const firstController = new AbortController();
    const secondController = new AbortController();
    const first = executeQuestion("turn-1", {
      question: "First turn?",
      default: "Yes",
    }, firstController.signal);
    const second = executeQuestion("turn-2", {
      question: "Second turn?",
      default: "No",
    }, secondController.signal);

    askUserQuestionCoordinator.answer("turn-2", {
      cancelled: false,
      answer: "No",
    });
    askUserQuestionCoordinator.answer("turn-1", {
      cancelled: false,
      answer: "Yes",
    });
    await expect(second).resolves.toMatchObject({
      details: { status: "answered", answer: "No" },
    });
    await expect(first).resolves.toMatchObject({
      details: { status: "answered", answer: "Yes" },
    });
  });

  it.each([
    ["options", { options: ["A", "B"] }],
    ["inputType", { inputType: "date" }],
    ["dateFormat", { dateFormat: "yyyy-MM-dd" }],
    ["dataSource", { dataSource: { type: "api", endpoint: "/api/options" } }],
    ["multiple false", { multiple: false }],
    ["required false", { required: false }],
    ["empty default", { default: "" }],
    ["zero default", { default: 0 }],
    ["false default", { default: false }],
    ["confirm", { confirm: true }],
  ])("ignores redundant grouped top-level %s", (_, mixed) => {
    expect(
      normalizeAskUserQuestionCardRequest({
        title: "测试表单",
        ...mixed,
        questions: [
          { id: "leave_type", question: "Leave type?", default: "事假" },
          { id: "reason", question: "Reason?", default: "个人事务" },
        ],
      }),
    ).toMatchObject({
      batch: true,
      title: "测试表单",
      questions: [
        { id: "leave_type", question: "Leave type?", default: "事假" },
        { id: "reason", question: "Reason?", default: "个人事务" },
      ],
    });
  });

  it("omits optional grouped answers that are not submitted", async () => {
    const execution = askUserQuestionTool.execute(
      "group-missing",
      {
        title: "测试表单",
        questions: [
          { id: "name", question: "Name?", default: "Dano" },
          { id: "env", question: "Environment?", options: ["Test", "Prod"], default: "Test" },
        ],
      },
      undefined,
      undefined,
      {} as never,
    );

    askUserQuestionCoordinator.answer("group-missing", {
      cancelled: false,
      answer: { name: "Dano" },
    });
    await expect(execution).resolves.toMatchObject({
      details: { status: "answered", answer: { name: "Dano" } },
    });
  });

  it("rejects grouped answers missing a required question id", async () => {
    const execution = askUserQuestionTool.execute(
      "group-missing-required",
      {
        title: "测试表单",
        questions: [
          { id: "name", question: "Name?", default: "Dano" },
          { id: "env", question: "Environment?", options: ["Test", "Prod"], default: "Test", required: true },
        ],
      },
      undefined,
      undefined,
      {} as never,
    );

    expect(() =>
      askUserQuestionCoordinator.answer("group-missing-required", {
        cancelled: false,
        answer: { name: "Dano" },
      }),
    ).toThrow("Missing answer");
    askUserQuestionCoordinator.answer("group-missing-required", {
      cancelled: false,
      answer: { name: "Dano", env: "Test" },
    });
    await expect(execution).resolves.toMatchObject({
      details: { status: "answered", answer: { name: "Dano", env: "Test" } },
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

  it("still requires a submitted form when confirmation has legacy extra fields", async () => {
    await expect(
      executeQuestion("confirm-invalid", {
        question: "Deploy now?",
        options: ["Yes", "No"],
        confirm: true,
      }),
    ).rejects.toThrow("only be called after the user submitted a grouped form");
  });

  it("rejects grouped confirmation parameters instead of waiting forever", async () => {
    await expect(
      askUserQuestionTool.execute(
        "group-confirm-invalid",
        {
          title: "测试表单",
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

  it("releases the turn question lock when a pending question aborts", async () => {
    const controller = new AbortController();
    const first = executeQuestion(
      "abort-lock-1",
      { question: "Wait?", default: "Yes" },
      controller.signal,
    );

    controller.abort();
    await expect(first).rejects.toThrow("Question was aborted");

    const nextController = new AbortController();
    const second = executeQuestion(
      "abort-lock-2",
      { question: "Continue?", default: "Yes" },
      nextController.signal,
    );
    askUserQuestionCoordinator.answer("abort-lock-2", {
      cancelled: false,
      answer: "Yes",
    });
    await expect(second).resolves.toMatchObject({
      details: { status: "answered", answer: "Yes" },
    });
  });
});
