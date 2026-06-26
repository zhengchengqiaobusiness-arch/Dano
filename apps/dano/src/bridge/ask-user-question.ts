import { defineTool } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import {
  ASK_USER_QUESTION_TOOL_NAME,
  type AskUserQuestionAnswer,
  type AskUserQuestionDataSource,
  type AskUserQuestionInputType,
  type AskUserQuestionOption,
  type AskUserQuestionResult,
} from "./types.js";

const askUserQuestionAnswerSchema = Type.Union([
  Type.String(),
  Type.Array(Type.String()),
  Type.Boolean(),
], {
  description:
    "Default or answer value: string for text/single-choice, string[] for multiple-choice, boolean for confirmation.",
});

const groupedRetryError =
  "You called ask_user_question while another question card is still waiting. Do not call ask_user_question multiple times in the same response. Retry silently with exactly one ask_user_question call using {\"questions\":[...]} so all fields render in one card with one submit button. When using questions, omit top-level question, options, multiple, default, and confirm. Do not explain this correction to the user.";

const mixedGroupedFieldsError =
  "Invalid ask_user_question call: when using questions, the top level may contain only questions. Move question, options, inputType, dataSource, multiple, and default into each questions[] item. Do not include top-level question, options, inputType, dataSource, multiple, default, or confirm with questions. Retry silently; do not explain this correction to the user.";

const askUserQuestionOptionSchema = Type.Union([
  Type.String({ minLength: 1 }),
  Type.Object({
    id: Type.String({ minLength: 1 }),
    label: Type.String({ minLength: 1 }),
    extra: Type.Optional(Type.Record(Type.String(), Type.Any())),
  }),
]);

const askUserQuestionInputTypeSchema = Type.Union([
  Type.Literal("text"),
  Type.Literal("radio"),
  Type.Literal("checkbox"),
  Type.Literal("select"),
  Type.Literal("treeSelect"),
  Type.Literal("confirm"),
]);

const askUserQuestionDataSourceSchema = Type.Object({
  type: Type.Literal("api"),
  endpoint: Type.String({ minLength: 1 }),
  method: Type.Optional(Type.Union([Type.Literal("GET"), Type.Literal("POST")])),
  params: Type.Optional(Type.Record(Type.String(), Type.Any())),
  searchParam: Type.Optional(Type.String({ minLength: 1 })),
  pageParam: Type.Optional(Type.String({ minLength: 1 })),
  pageSizeParam: Type.Optional(Type.String({ minLength: 1 })),
  pageSize: Type.Optional(Type.Number({ minimum: 1 })),
  resultPath: Type.Optional(Type.String({ minLength: 1 })),
  totalPath: Type.Optional(Type.String({ minLength: 1 })),
  idField: Type.Optional(Type.String({ minLength: 1 })),
  labelField: Type.Optional(Type.String({ minLength: 1 })),
  childrenField: Type.Optional(Type.String({ minLength: 1 })),
  extraFields: Type.Optional(Type.Array(Type.String({ minLength: 1 }))),
});

const askUserQuestionFields = {
  question: Type.Optional(
    Type.String({
      minLength: 1,
      description:
        "Single-question call only: the clear, specific question to ask the user. If collecting more than one answer, omit this top-level field and put every question inside questions[].",
    }),
  ),
  options: Type.Optional(
    Type.Array(askUserQuestionOptionSchema, {
      minItems: 2,
      description:
        "Choices for this question. Strings remain supported; objects use stable id plus label. Include '其他' or 'Other' to let the user enter one custom answer. Omit for free-text, confirmation, or remote dataSource input.",
    }),
  ),
  inputType: Type.Optional(askUserQuestionInputTypeSchema),
  dataSource: Type.Optional(askUserQuestionDataSourceSchema),
  multiple: Type.Optional(
    Type.Boolean({
      default: false,
      description: "Set true with options to allow multiple selections.",
    }),
  ),
  default: Type.Optional(askUserQuestionAnswerSchema),
};

export const askUserQuestionParameters = Type.Object({
  ...askUserQuestionFields,
  confirm: Type.Optional(
    Type.Literal(true, {
      description: "Set true without options to ask for confirmation.",
    }),
  ),
  questions: Type.Optional(
    Type.Array(
      Type.Object({
        id: Type.Optional(
          Type.String({
            minLength: 1,
            description:
              "Stable key for this answer. If omitted, answers use q1, q2, and so on.",
          }),
        ),
        ...askUserQuestionFields,
        question: Type.String({
          minLength: 1,
          description: "The clear, specific question to ask the user.",
        }),
      }),
      {
        minItems: 1,
        description:
          "Preferred for collecting more than one answer. Make exactly one ask_user_question call with questions: [{ id, question, default, options?, multiple? }, ...]. This renders one card with one submit button. Do not include top-level question, options, multiple, default, or confirm when questions is present.",
      },
    ),
  ),
});

export const askUserQuestionResultSchema = Type.Union([
  Type.Object({
    status: Type.Literal("answered"),
    answer: Type.Union([
      askUserQuestionAnswerSchema,
      Type.Record(Type.String(), askUserQuestionAnswerSchema),
    ]),
  }),
  Type.Object({ status: Type.Literal("cancelled") }),
]);

type PendingQuestionKind = "text" | "single" | "multiple" | "confirm";

type PendingQuestionOption = {
  id: string;
  label: string;
};

interface PendingQuestionItem {
  id: string;
  kind: PendingQuestionKind;
  inputType: AskUserQuestionInputType;
  options?: readonly PendingQuestionOption[];
  dataSource?: AskUserQuestionDataSource;
}

interface PendingQuestion {
  questions: readonly PendingQuestionItem[];
  resolve(result: AskUserQuestionResult): void;
  reject(error: Error): void;
  removeAbortListener(): void;
}

function isOtherOption(value: string | PendingQuestionOption): boolean {
  const normalized = (typeof value === "string" ? value : value.label)
    .trim()
    .toLocaleLowerCase();
  return normalized === "其他" || normalized === "other";
}

class AskUserQuestionCoordinator {
  private readonly pending = new Map<string, PendingQuestion>();

  wait(
    toolCallId: string,
    request: {
      question?: string;
      options?: readonly (string | AskUserQuestionOption)[];
      inputType?: AskUserQuestionInputType;
      dataSource?: AskUserQuestionDataSource;
      multiple?: boolean;
      confirm?: true;
      default?: AskUserQuestionAnswer;
      questions?: readonly {
        id?: string;
        question: string;
        options?: readonly (string | AskUserQuestionOption)[];
        inputType?: AskUserQuestionInputType;
        dataSource?: AskUserQuestionDataSource;
        multiple?: boolean;
        confirm?: true;
        default?: AskUserQuestionAnswer;
      }[];
    },
    signal: AbortSignal | undefined,
  ): Promise<AskUserQuestionResult> {
    if (this.pending.has(toolCallId)) {
      return Promise.reject(
        new Error(`Question is already pending: ${toolCallId}`),
      );
    }

    const questions = normalizeRequestQuestions(request);
    if (typeof questions === "string") {
      return Promise.reject(new Error(questions));
    }
    if (questions.length === 0) {
      return Promise.reject(new Error("Question is required"));
    }
    if (this.pending.size > 0) {
      const error = new Error(groupedRetryError);
      this.rejectAll(error);
      return Promise.reject(error);
    }
    if (request.confirm && (request.options || request.multiple || request.questions || request.dataSource)) {
      return Promise.reject(
        new Error("Confirmation questions cannot provide options or multiple"),
      );
    }

    return new Promise((resolve, reject) => {
      const abort = () => {
        this.pending.delete(toolCallId);
        reject(new Error("Question was aborted"));
      };
      signal?.addEventListener("abort", abort, { once: true });

      this.pending.set(toolCallId, {
        questions,
        resolve,
        reject,
        removeAbortListener: () => signal?.removeEventListener("abort", abort),
      });

      if (signal?.aborted) abort();
    });
  }

  answer(
    toolCallId: string,
    response:
      | { cancelled: true; answer?: undefined }
      | {
          cancelled: false;
          answer: AskUserQuestionAnswer | Record<string, AskUserQuestionAnswer>;
        },
  ): AskUserQuestionResult {
    const pending = this.pending.get(toolCallId);
    if (!pending) throw new Error(`Pending question not found: ${toolCallId}`);

    let result: AskUserQuestionResult;
    if (response.cancelled) {
      result = { status: "cancelled" };
    } else {
      const { answer } = response;
      if (pending.questions.length > 1) {
        if (!isAnswerRecord(answer)) {
          throw new Error("Grouped question answer must be an object");
        }
        const normalized: Record<string, AskUserQuestionAnswer> = {};
        for (const question of pending.questions) {
          if (!(question.id in answer)) {
            throw new Error(`Missing answer for grouped question: ${question.id}`);
          }
          normalized[question.id] = normalizeAnswer(question, answer[question.id]);
        }
        result = { status: "answered", answer: normalized };
      } else {
        if (isAnswerRecord(answer)) {
          throw new Error("Single question answer cannot be an object");
        }
        result = {
          status: "answered",
          answer: normalizeAnswer(pending.questions[0], answer),
        };
      }
    }

    this.pending.delete(toolCallId);
    pending.removeAbortListener();
    pending.resolve(result);
    return result;
  }

  cancelAll(): void {
    this.rejectAll(new Error("Question coordinator was disposed"));
  }

  private rejectAll(error: Error): void {
    for (const [toolCallId, pending] of this.pending) {
      this.pending.delete(toolCallId);
      pending.removeAbortListener();
      pending.reject(error);
    }
  }
}

function normalizeRequestQuestions(
  request: Parameters<AskUserQuestionCoordinator["wait"]>[1],
): string | PendingQuestionItem[] {
  if (request.questions) {
    if (
      request.question ||
      request.options ||
      request.inputType ||
      request.dataSource ||
      request.multiple ||
      request.default ||
      request.confirm
    ) {
      return mixedGroupedFieldsError;
    }
    const seenIds = new Set<string>();
    const questions: PendingQuestionItem[] = [];
    for (let index = 0; index < request.questions.length; index += 1) {
      const question = request.questions[index];
      const normalized = normalizeQuestion(question, `q${index + 1}`);
      if (typeof normalized === "string") return normalized;
      if (seenIds.has(normalized.id)) {
        return `Grouped question ids must be unique: ${normalized.id}`;
      }
      seenIds.add(normalized.id);
      questions.push(normalized);
    }
    return questions;
  }

  if (!request.question?.trim()) return "Question is required";
  const question = normalizeQuestion(
    { ...request, id: "answer", question: request.question },
    "answer",
  );
  return typeof question === "string" ? question : [question];
}

function normalizeQuestion(
  request: {
    id?: string;
    question: string;
    options?: readonly (string | AskUserQuestionOption)[];
    inputType?: AskUserQuestionInputType;
    dataSource?: AskUserQuestionDataSource;
    multiple?: boolean;
    confirm?: true;
    default?: AskUserQuestionAnswer;
  },
  fallbackId: string,
): PendingQuestionItem | string {
  const inputType = request.confirm
    ? "confirm"
    : (request.inputType ?? (request.multiple ? "checkbox" : request.options ? "radio" : "text"));
  if (inputType === "confirm" && request.options) {
    return "Confirmation questions cannot provide options or multiple";
  }
  if (request.confirm && (request.options || request.multiple || request.dataSource)) {
    return "Confirmation questions cannot provide options or multiple";
  }
  const rawOptions = request.options?.map(normalizeOption);
  if (rawOptions?.some(option => !option)) {
    return "Question options must be non-empty and unique";
  }
  const options = rawOptions as PendingQuestionOption[] | undefined;
  const optionIds = options?.map(option => option.id) ?? [];
  if (new Set(optionIds).size !== optionIds.length) {
    return "Question options must be non-empty and unique";
  }
  if (request.dataSource && inputType !== "select" && inputType !== "treeSelect") {
    return "Data sources require select or treeSelect inputType";
  }
  if (
    (request.multiple || inputType === "checkbox") &&
    !options &&
    !request.dataSource
  ) {
    return "Multiple-choice questions require options or dataSource";
  }
  if (
    (inputType === "radio" || inputType === "select" || inputType === "treeSelect") &&
    !options &&
    !request.dataSource
  ) {
    return "Choice questions require options or dataSource";
  }

  let kind: PendingQuestionKind = "text";
  if (inputType === "confirm") kind = "confirm";
  else if (request.multiple || inputType === "checkbox") kind = "multiple";
  else if (options || request.dataSource || inputType === "radio" || inputType === "select" || inputType === "treeSelect") {
    kind = "single";
  }

  const question: PendingQuestionItem = {
    id: request.id?.trim() || fallbackId,
    kind,
    inputType,
    ...(options ? { options } : {}),
    ...(request.dataSource ? { dataSource: request.dataSource } : {}),
  };
  if (request.default !== undefined) {
    normalizeAnswer(question, request.default);
  }
  return question;
}

function normalizeOption(
  option: string | AskUserQuestionOption,
): PendingQuestionOption | null {
  if (typeof option === "string") {
    const value = option.trim();
    return value ? { id: value, label: value } : null;
  }
  const id = option.id.trim();
  const label = option.label.trim();
  return id && label ? { id, label } : null;
}

function normalizeAnswer(
  pending: PendingQuestionItem,
  answer: AskUserQuestionAnswer,
): AskUserQuestionAnswer {
  if (pending.kind === "confirm") {
    if (typeof answer !== "boolean") {
      throw new Error("Confirmation answer must be boolean");
    }
    return answer;
  }
  if (pending.kind === "multiple") {
    if (
      !Array.isArray(answer) ||
      answer.length === 0 ||
      !answer.every(value => typeof value === "string")
    ) {
      throw new Error(
        "Multiple-choice answer must contain unique provided options",
      );
    }
    const normalized = answer.map(value => value.trim());
    if (
      normalized.some(value => !value) ||
      new Set(normalized).size !== normalized.length
    ) {
      throw new Error(
        "Multiple-choice answer must contain unique provided options",
      );
    }
    if (normalized.some(isOtherOption)) {
      throw new Error("Other requires a custom answer");
    }
    const optionIds = pending.options?.map(option => option.id);
    const customAnswers = normalized.filter(
      value => !optionIds?.includes(value),
    );
    const allowsCustom = pending.options?.some(isOtherOption) ?? false;
    if (customAnswers.length > 1 && allowsCustom) {
      throw new Error("Multiple-choice answer may contain only one custom answer");
    }
    if (customAnswers.length > 0 && !allowsCustom && !pending.dataSource) {
      throw new Error(
        "Multiple-choice answer must contain unique provided options",
      );
    }
    return normalized;
  }

  if (typeof answer !== "string" || !answer.trim()) {
    throw new Error("Question answer cannot be empty");
  }
  const normalized = answer.trim();
  if (pending.options && isOtherOption(normalized)) {
    throw new Error("Other requires a custom answer");
  }
  const optionIds = pending.options?.map(option => option.id);
  if (
    optionIds &&
    !optionIds.includes(normalized) &&
    !pending.options?.some(isOtherOption)
  ) {
    throw new Error("Question answer must match one of the provided options");
  }
  return normalized;
}

function isAnswerRecord(
  answer: AskUserQuestionAnswer | Record<string, AskUserQuestionAnswer>,
): answer is Record<string, AskUserQuestionAnswer> {
  return typeof answer === "object" && answer !== null && !Array.isArray(answer);
}

const coordinatorState = globalThis as typeof globalThis & {
  __danoAskUserQuestionCoordinator?: AskUserQuestionCoordinator;
};

// ponytail: dev runtime reloads create separate module graphs in one process.
export const askUserQuestionCoordinator =
  (coordinatorState.__danoAskUserQuestionCoordinator ??=
    new AskUserQuestionCoordinator());

export const askUserQuestionTool = defineTool({
  name: ASK_USER_QUESTION_TOOL_NAME,
  label: "Ask User Question",
  description: `Ask the user for structured input during execution.

Use exactly one ask_user_question call per assistant response. If you need more than one answer, use only the questions array: {"questions":[{"id":"leave_type","question":"请假类型？","options":["事假",{"id":"sick","label":"病假"}],"default":"事假"},{"id":"reason","question":"原因？","default":"个人事务"}]}. Do not include top-level question, options, inputType, dataSource, multiple, default, or confirm when questions is present.

For a single question, use top-level question/options/inputType/dataSource/multiple/default/confirm. For multiple questions, use questions[]. Set default on every non-confirmation question, including every questions[] item. Use inputType:"select" or inputType:"treeSelect" with dataSource for remote API-backed choices. Confirmation is a separate single-question call with question + confirm: true and no options/multiple/questions. The answer is returned as a tool result and execution then continues.`,
  promptSnippet:
    "Ask the user one native question card; for several fields use one questions array with one submit button",
  promptGuidelines: [
    "Use ask_user_question whenever you need user input to continue; do not ask the question only in assistant text.",
    "Call ask_user_question at most once per assistant response. If you need several answers, put every item in one questions array.",
    "If the user cancels ask_user_question, stop the current workflow. Do not ask again or retry unless the user sends a new message explicitly requesting it.",
    "Invoke ask_user_question as a native tool call. Never print, describe, or wrap a tool call in <question> tags, XML, JSON, Markdown, or other assistant text.",
    "If ask_user_question returns a validation error, retry silently with a corrected native tool call; do not explain the correction to the user.",
    "Set default on every non-confirmation question, including every item in questions, using the most likely or safest answer while still letting the user change it.",
    "When using questions, the top level must contain only questions. Put id, question, options, inputType, dataSource, multiple, and default inside each questions item.",
    "For forms, applications, or other user-reviewed summaries, call ask_user_question with confirm: true after presenting the final summary and before treating it as confirmed, ready to submit, or complete.",
  ],
  parameters: askUserQuestionParameters,
  executionMode: "sequential",
  async execute(toolCallId, params, signal) {
    const result = await askUserQuestionCoordinator.wait(
      toolCallId,
      params,
      signal,
    );

    return {
      content: [
        {
          type: "text",
          text:
            result.status === "answered"
              ? `User answered the question: ${JSON.stringify(result.answer)}. Continue with this answer.`
              : "User cancelled the question. Stop the current workflow. Do not ask another question or retry unless the user sends a new message explicitly requesting it.",
        },
      ],
      details: result,
    };
  },
});
