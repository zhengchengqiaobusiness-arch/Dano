import { defineTool } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { parseAskUserQuestionDateValue, validateAskUserQuestionDateFormat } from "../../types/ask-user-question-date.js";
import {
  ASK_USER_QUESTION_TOOL_NAME,
  type AskUserQuestionAnswer,
  type AskUserQuestionAnswerInput,
  type AskUserQuestionDataSource,
  type AskUserQuestionInputType,
  type AskUserQuestionOption,
  type AskUserQuestionOptionId,
  type AskUserQuestionResult,
} from "./types.js";

const askUserQuestionOptionItemSchema = Type.Object({
  id: Type.Union([Type.String({ minLength: 1 }), Type.Number()]),
  label: Type.String({ minLength: 1 }),
  extra: Type.Optional(Type.Record(Type.String(), Type.Any())),
});

const askUserQuestionAnswerInputSchema = Type.Union([
  Type.String(),
  Type.Number(),
  askUserQuestionOptionItemSchema,
  Type.Array(
    Type.Union([
      Type.String(),
      Type.Number(),
      askUserQuestionOptionItemSchema,
    ]),
  ),
  Type.Boolean(),
], {
  description:
    "Default or answer value: string for text/single-choice labels, string or number option ids, option item objects, arrays for multiple-choice, boolean for confirmation.",
});

const askUserQuestionDefaultSchema = Type.Union([
  Type.String(),
  Type.Number(),
  askUserQuestionOptionItemSchema,
  Type.Array(
    Type.Union([
      Type.String(),
      Type.Number(),
      askUserQuestionOptionItemSchema,
    ]),
  ),
  Type.Boolean(),
], {
  description:
    "Required for every non-confirmation question. Provide a context-based recommended default value. String defaults must be non-empty and must not be placeholders such as \"\".",
});

const askUserQuestionAnswerSchema = Type.Union([
  Type.String(),
  Type.Number(),
  Type.Array(Type.Union([Type.String(), Type.Number()])),
  Type.Boolean(),
], {
  description:
    "Canonical answer value returned to the model: string or number id, id array, text string, or boolean confirmation.",
});

const groupedRetryError =
  "You called ask_user_question more than once in the same response while another question is still pending. Retry silently with exactly one native ask_user_question call using {\"questions\":[...]} so all fields render in one card with one submit button. Put every field's options, inputType, dateFormat, dataSource, multiple, required, and default inside its questions[] item. Do not explain this correction to the user.";

const mixedGroupedFieldsError =
  "Invalid ask_user_question call: when using questions, field configuration belongs inside each questions[] item. Move options, inputType, dateFormat, required, dataSource, multiple, default, and confirm out of the top level. Top-level question/title/label/prompt text is treated only as form instruction text and is ignored. Retry silently; do not explain this correction to the user.";

const askUserQuestionOptionSchema = Type.Union([
  Type.String({ minLength: 1 }),
  askUserQuestionOptionItemSchema,
]);

const askUserQuestionInputTypeSchema = Type.Union([
  Type.Literal("text"),
  Type.Literal("textarea"),
  Type.Literal("date"),
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
        "Single-question call: the clear, specific question to ask the user. With questions[], top-level question/title/label/prompt is treated only as optional form instruction text; each actual field question must be inside questions[].",
    }),
  ),
  title: Type.Optional(Type.String({ minLength: 1 })),
  label: Type.Optional(Type.String({ minLength: 1 })),
  prompt: Type.Optional(Type.String({ minLength: 1 })),
  options: Type.Optional(
    Type.Array(askUserQuestionOptionSchema, {
      minItems: 2,
      description:
        "Choices for this question. Strings remain supported; objects use stable id plus label. Include '其他' or 'Other' to let the user enter one custom answer. Omit for free-text, confirmation, or remote dataSource input.",
    }),
  ),
  choices: Type.Optional(Type.Array(askUserQuestionOptionSchema, { minItems: 2 })),
  inputType: Type.Optional(askUserQuestionInputTypeSchema),
  type: Type.Optional(Type.String({ minLength: 1 })),
  input_type: Type.Optional(Type.String({ minLength: 1 })),
  component: Type.Optional(Type.String({ minLength: 1 })),
  dateFormat: Type.Optional(
    Type.String({
      minLength: 1,
      description:
        "Required when inputType is \"date\". A frontend date-control format such as \"yyyy-MM-dd\" or \"yyyy-MM-dd HH:mm\".",
    }),
  ),
  dataSource: Type.Optional(askUserQuestionDataSourceSchema),
  data_source: Type.Optional(askUserQuestionDataSourceSchema),
  multiple: Type.Optional(
    Type.Boolean({
      default: false,
      description: "Set true with options to allow multiple selections.",
    }),
  ),
  multi: Type.Optional(Type.Boolean()),
  multipleSelect: Type.Optional(Type.Boolean()),
  required: Type.Optional(
    Type.Boolean({
      description:
        "Set true to require a non-empty answer. Defaults to false.",
    }),
  ),
  default: Type.Optional(askUserQuestionDefaultSchema),
  defaultValue: Type.Optional(askUserQuestionDefaultSchema),
  prefill: Type.Optional(askUserQuestionDefaultSchema),
  value: Type.Optional(askUserQuestionDefaultSchema),
};

export const askUserQuestionParameters = Type.Object({
  ...askUserQuestionFields,
  confirm: Type.Optional(
    Type.Literal(true, {
      description: "Set true without options to ask for confirmation.",
    }),
  ),
  questions: Type.Optional(
    Type.Any({
      description:
        "Preferred for collecting more than one answer. Make exactly one ask_user_question call with questions: [{ id, question, default, options?, multiple?, inputType?, dateFormat?, required?, dataSource? }, ...]. Every non-confirmation questions[] item must include a context-based, non-empty default. A single question object is also accepted and normalized to an array. When questions is present, put each field's options, inputType, dateFormat, required, dataSource, multiple, and default inside its questions[] item. Do not include top-level confirm or top-level field configuration with questions.",
    }),
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

type PendingQuestionKind = "text" | "date" | "single" | "multiple" | "confirm";

type AskUserQuestionRequestItem = {
  id?: string;
  key?: string;
  name?: string;
  question?: string;
  title?: string;
  label?: string;
  prompt?: string;
  options?: readonly (string | AskUserQuestionOption)[];
  choices?: readonly (string | AskUserQuestionOption)[];
  inputType?: AskUserQuestionInputType;
  type?: string;
  input_type?: string;
  component?: string;
  dateFormat?: unknown;
  dataSource?: AskUserQuestionDataSource;
  data_source?: AskUserQuestionDataSource;
  multiple?: boolean;
  multi?: boolean;
  multipleSelect?: boolean;
  required?: unknown;
  confirm?: true;
  default?: AskUserQuestionAnswerInput;
  defaultValue?: AskUserQuestionAnswerInput;
  prefill?: AskUserQuestionAnswerInput;
  value?: AskUserQuestionAnswerInput;
};

type AskUserQuestionRequestParams = AskUserQuestionRequestItem & {
  questions?: unknown;
};

type PendingQuestionOption = {
  id: AskUserQuestionOptionId;
  label: string;
};

interface PendingQuestionItem {
  id: string;
  kind: PendingQuestionKind;
  inputType: AskUserQuestionInputType;
  options?: readonly PendingQuestionOption[];
  dataSource?: AskUserQuestionDataSource;
  dateFormat?: string;
  required: boolean;
}

interface PendingQuestion {
  grouped: boolean;
  questions: readonly PendingQuestionItem[];
  resolve(result: AskUserQuestionResult): void;
  reject(error: Error): void;
  cleanup(): void;
}

function isOtherOption(value: string | PendingQuestionOption): boolean {
  const normalized = (typeof value === "string" ? value : value.label)
    .trim()
    .toLocaleLowerCase();
  return normalized === "其他" || normalized === "other";
}

class AskUserQuestionCoordinator {
  private readonly pending = new Map<string, PendingQuestion>();
  private readonly pendingToolCallBySignal = new WeakMap<AbortSignal, string>();

  wait(
    toolCallId: string,
    rawRequest: AskUserQuestionRequestParams,
    signal: AbortSignal | undefined,
  ): Promise<AskUserQuestionResult> {
    if (this.pending.has(toolCallId)) {
      return Promise.reject(
        new Error(`Question is already pending: ${toolCallId}`),
      );
    }

    const request = normalizeCompatibleRequest(rawRequest);
    const questions = normalizeRequestQuestions(request);
    if (typeof questions === "string") {
      return Promise.reject(new Error(questions));
    }
    if (questions.length === 0) {
      return Promise.reject(new Error("Question is required"));
    }
    const pendingInCurrentTurn = signal
      ? this.pendingToolCallBySignal.get(signal)
      : undefined;
    if (
      pendingInCurrentTurn !== undefined &&
      this.pending.has(pendingInCurrentTurn)
    ) {
      return Promise.reject(new Error(groupedRetryError));
    }
    if (request.confirm && (request.options || request.multiple || request.questions || request.dataSource)) {
      return Promise.reject(
        new Error("Confirmation questions cannot provide options or multiple"),
      );
    }

    return new Promise((resolve, reject) => {
      const cleanup = () => {
        this.pending.delete(toolCallId);
        signal?.removeEventListener("abort", abort);
        if (signal && this.pendingToolCallBySignal.get(signal) === toolCallId) {
          this.pendingToolCallBySignal.delete(signal);
        }
      };
      const abort = () => {
        cleanup();
        reject(new Error("Question was aborted"));
      };
      signal?.addEventListener("abort", abort, { once: true });
      if (signal) this.pendingToolCallBySignal.set(signal, toolCallId);

      this.pending.set(toolCallId, {
        grouped: request.questions !== undefined,
        questions,
        resolve,
        reject,
        cleanup,
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
          answer:
            | AskUserQuestionAnswerInput
            | Record<string, AskUserQuestionAnswerInput>;
        },
  ): AskUserQuestionResult {
    const pending = this.pending.get(toolCallId);
    if (!pending) throw new Error(`Pending question not found: ${toolCallId}`);

    let result: AskUserQuestionResult;
    if (response.cancelled) {
      result = { status: "cancelled" };
    } else {
      const { answer } = response;
      if (pending.grouped) {
        if (!isAnswerRecord(answer)) {
          throw new Error("Grouped question answer must be an object");
        }
        const normalized: Record<string, AskUserQuestionAnswer> = {};
        for (const question of pending.questions) {
          if (!(question.id in answer)) {
            if (question.required) {
              throw new Error(`Missing answer for grouped question: ${question.id}`);
            }
            continue;
          }
          normalized[question.id] = normalizeAnswer(question, answer[question.id]);
        }
        result = { status: "answered", answer: normalized };
      } else {
        if (isAnswerRecord(answer) && !isOptionObject(answer)) {
          throw new Error("请选择一个有效选项");
        }
        result = {
          status: "answered",
          answer: normalizeAnswer(
            pending.questions[0],
            answer as AskUserQuestionAnswerInput,
          ),
        };
      }
    }

    pending.cleanup();
    pending.resolve(result);
    return result;
  }

  cancelAll(): void {
    this.rejectAll(new Error("Question coordinator was disposed"));
  }

  private rejectAll(error: Error): void {
    for (const [toolCallId, pending] of this.pending) {
      pending.cleanup();
      pending.reject(error);
    }
  }
}

type NormalizedAskUserQuestionRequestItem = {
  id?: string;
  question?: string;
  options?: readonly (string | AskUserQuestionOption)[];
  inputType?: AskUserQuestionInputType;
  dataSource?: AskUserQuestionDataSource;
  multiple?: boolean;
  dateFormat?: unknown;
  required?: unknown;
  confirm?: true;
  default?: AskUserQuestionAnswerInput;
};

type NormalizedAskUserQuestionRequest = NormalizedAskUserQuestionRequestItem & {
  questions?: readonly NormalizedAskUserQuestionRequestItem[];
};

function normalizeCompatibleRequest(
  request: AskUserQuestionRequestParams,
): NormalizedAskUserQuestionRequest {
  const normalized: NormalizedAskUserQuestionRequest =
    normalizeCompatibleQuestion(request);
  const rawQuestions = request.questions;
  if (rawQuestions !== undefined) {
    normalized.questions = normalizeCompatibleQuestions(rawQuestions);
    return foldCompatibleGroupedFields(normalized);
  }
  return normalized;
}

function normalizeCompatibleQuestions(
  value: AskUserQuestionRequestParams["questions"],
): NormalizedAskUserQuestionRequestItem[] {
  const parsed = parseJsonString(value);
  const rawItems = Array.isArray(parsed) ? parsed : parsed ? [parsed] : [];
  return rawItems
    .filter(isPlainRecord)
    .map(value => normalizeCompatibleQuestion(value));
}

function normalizeCompatibleQuestion(
  request: AskUserQuestionRequestItem | Record<string, unknown>,
): NormalizedAskUserQuestionRequestItem {
  const normalized: NormalizedAskUserQuestionRequestItem = {};
  const id = firstString(request.id, request.key, request.name);
  if (id) normalized.id = id;

  const question = firstString(
    request.question,
    request.title,
    request.label,
    request.prompt,
  );
  if (question) normalized.question = question;

  const options = request.options ?? request.choices;
  if (Array.isArray(options)) {
    normalized.options = options as readonly (string | AskUserQuestionOption)[];
  }

  const inputType = normalizeInputType(
    request.inputType ?? request.input_type ?? request.type ?? request.component,
  );
  if (inputType) normalized.inputType = inputType;

  if ("dateFormat" in request) normalized.dateFormat = request.dateFormat;

  const dataSource = request.dataSource ?? request.data_source;
  if (isCompatibleDataSource(dataSource)) normalized.dataSource = dataSource;

  const multiple = request.multiple ?? request.multi ?? request.multipleSelect;
  if (typeof multiple === "boolean") normalized.multiple = multiple;

  if ("required" in request) normalized.required = request.required;

  if (request.confirm === true || inputType === "confirm") {
    normalized.confirm = true;
  }

  const defaultValue = firstDefined(
    request.default,
    request.defaultValue,
    request.prefill,
    request.value,
  );
  if (isAnswerInput(defaultValue)) normalized.default = defaultValue;

  return normalized;
}

function foldCompatibleGroupedFields(
  request: NormalizedAskUserQuestionRequest,
): NormalizedAskUserQuestionRequest {
  const questions = request.questions ?? [];
  if (request.confirm) return request;
  if (questions.length !== 1) {
    if (
      request.question &&
      request.options === undefined &&
      request.inputType === undefined &&
      request.dateFormat === undefined &&
      request.dataSource === undefined &&
      request.multiple === undefined &&
      request.required === undefined &&
      request.default === undefined
    ) {
      return { questions };
    }
    return request;
  }

  const [question] = questions;
  return {
    questions: [
      {
        ...request,
        ...question,
        question: question.question ?? request.question,
        options: question.options ?? request.options,
        inputType: question.inputType ?? request.inputType,
        dateFormat: question.dateFormat ?? request.dateFormat,
        dataSource: question.dataSource ?? request.dataSource,
        multiple: question.multiple ?? request.multiple,
        required: question.required ?? request.required,
        default: question.default ?? request.default,
      },
    ],
  };
}

function parseJsonString(value: unknown): unknown {
  if (typeof value !== "string") return value;
  const trimmed = value.trim();
  if (!trimmed) return value;
  try {
    return JSON.parse(trimmed) as unknown;
  } catch {
    return value;
  }
}

function isCompatibleDataSource(
  value: unknown,
): value is AskUserQuestionDataSource {
  return (
    isPlainRecord(value) &&
    value.type === "api" &&
    typeof value.endpoint === "string" &&
    value.endpoint.trim().length > 0
  );
}

function isAnswerInput(value: unknown): value is AskUserQuestionAnswerInput {
  return (
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean" ||
    isOptionObject(value) ||
    (Array.isArray(value) &&
      value.every(
        item =>
          typeof item === "string" ||
          typeof item === "number" ||
          isOptionObject(item),
      ))
  );
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function firstString(...values: unknown[]): string | undefined {
  for (const value of values) {
    if (typeof value !== "string") continue;
    const trimmed = value.trim();
    if (trimmed) return trimmed;
  }
  return undefined;
}

function firstDefined<T>(...values: T[]): T | undefined {
  return values.find(value => value !== undefined);
}

function normalizeInputType(value: unknown): AskUserQuestionInputType | undefined {
  if (typeof value !== "string") return undefined;
  const normalized = value.trim().replace(/[-_\s]/g, "").toLocaleLowerCase();
  if (normalized === "textarea" || normalized === "multiline" || normalized === "longtext") {
    return "textarea";
  }
  if (normalized === "text" || normalized === "input" || normalized === "string") {
    return "text";
  }
  if (normalized === "date" || normalized === "datepicker") return "date";
  if (normalized === "radio") return "radio";
  if (normalized === "checkbox" || normalized === "multiselect") return "checkbox";
  if (normalized === "select" || normalized === "dropdown") return "select";
  if (normalized === "treeselect") return "treeSelect";
  if (normalized === "confirm" || normalized === "boolean") return "confirm";
  return undefined;
}

function normalizeRequestQuestions(
  request: NormalizedAskUserQuestionRequest,
): string | PendingQuestionItem[] {
  if (request.questions !== undefined) {
    if (
      request.question ||
      request.options !== undefined ||
      request.inputType ||
      request.dateFormat !== undefined ||
      request.dataSource ||
      request.multiple !== undefined ||
      request.required !== undefined ||
      request.default !== undefined ||
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
    question?: string;
    options?: readonly (string | AskUserQuestionOption)[];
    inputType?: AskUserQuestionInputType;
    dataSource?: AskUserQuestionDataSource;
    multiple?: boolean;
    dateFormat?: unknown;
    required?: unknown;
    confirm?: true;
    default?: AskUserQuestionAnswerInput;
  },
  fallbackId: string,
): PendingQuestionItem | string {
  if (!request.question?.trim()) return "Question is required";
  const inputType = request.confirm
    ? "confirm"
    : (request.inputType ?? (request.multiple ? "checkbox" : request.options ? "radio" : "text"));
  if (inputType === "confirm" && request.options) {
    return "Confirmation questions cannot provide options or multiple";
  }
  if (request.required !== undefined && typeof request.required !== "boolean") {
    return "required must be a boolean. Retry with required:true or required:false.";
  }
  const required = request.required === true;
  const dateFormat =
    inputType === "date" && typeof request.dateFormat === "string"
      ? request.dateFormat.trim()
      : undefined;
  if (inputType === "date") {
    const error = validateAskUserQuestionDateFormat(request.dateFormat);
    if (error) return error;
    if (
      typeof request.default === "string" &&
      request.default.trim() &&
      dateFormat &&
      !parseAskUserQuestionDateValue(request.default, dateFormat)
    ) {
      return `默认日期必须匹配 dateFormat: ${dateFormat}`;
    }
  } else if (request.dateFormat !== undefined) {
    return "dateFormat is only allowed when inputType is \"date\".";
  }
  if (inputType === "date" && (request.options || request.multiple || request.dataSource)) {
    return "Date questions cannot provide options, multiple, or dataSource.";
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
  else if (inputType === "date") kind = "date";
  else if (request.multiple || inputType === "checkbox") kind = "multiple";
  else if (options || request.dataSource || inputType === "radio" || inputType === "select" || inputType === "treeSelect") {
    kind = "single";
  }

  const question: PendingQuestionItem = {
    id: request.id?.trim() || fallbackId,
    kind,
    inputType,
    required,
    ...(options ? { options } : {}),
    ...(request.dataSource ? { dataSource: request.dataSource } : {}),
    ...(dateFormat ? { dateFormat } : {}),
  };
  if (kind !== "confirm" && request.default === undefined) {
    return "默认答案缺失：每个非确认问题都必须提供非空 default 推荐值";
  }
  if (request.default !== undefined) {
    if (typeof request.default === "string" && !request.default.trim()) {
      return "默认答案无效：default 必须是非空推荐值，不能是空字符串";
    }
    try {
      normalizeAnswer(question, request.default);
    } catch (cause) {
      if (cause instanceof Error) return `默认答案无效：${cause.message}`;
      return "默认答案无效";
    }
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
  const id =
    typeof option.id === "string" ? option.id.trim() : option.id;
  const label = option.label.trim();
  return isValidOptionId(id) && label ? { id, label } : null;
}

function normalizeAnswer(
  pending: PendingQuestionItem,
  answer: AskUserQuestionAnswerInput,
): AskUserQuestionAnswer {
  if (pending.kind === "confirm") {
    if (typeof answer !== "boolean") {
      throw new Error("请确认或取消");
    }
    return answer;
  }
  if (pending.kind === "multiple") {
    if (!Array.isArray(answer) || answer.length === 0) {
      if (!pending.required && Array.isArray(answer)) return [];
      throw new Error("请至少选择一个选项");
    }
    const normalized = answer.map(value => normalizeChoiceAnswer(pending, value));
    const keys = normalized.map(optionKey);
    if (new Set(keys).size !== keys.length) throw new Error("不能重复选择同一选项");
    const customAnswers = normalized.filter(value => !hasExactOption(pending, value));
    if (customAnswers.length > 1 && pending.options?.some(isOtherOption)) {
      throw new Error("只能填写一个其他回答");
    }
    return normalized;
  }

  if (pending.kind === "single") {
    if (!pending.required && typeof answer === "string" && !answer.trim()) return "";
    return normalizeChoiceAnswer(pending, answer);
  }

  if (pending.kind === "date") {
    if (typeof answer !== "string") throw new Error("日期答案必须是字符串");
    if (pending.required && !answer.trim()) throw new Error("答案不能为空");
    return answer;
  }

  if (typeof answer !== "string") {
    throw new Error("答案不能为空");
  }
  if (!answer.trim()) {
    if (!pending.required) return "";
    throw new Error("答案不能为空");
  }
  return answer.trim();
}

function normalizeChoiceAnswer(
  pending: PendingQuestionItem,
  answer: AskUserQuestionAnswerInput,
): AskUserQuestionOptionId {
  const rawCandidate = isOptionObject(answer) ? answer.id : answer;
  const candidate =
    typeof rawCandidate === "string" ? rawCandidate.trim() : rawCandidate;
  if (!isValidOptionId(candidate)) throw new Error("请选择一个有效选项");

  const options = pending.options ?? [];
  if (options.length === 0) return candidate;

  const exact = options.find(option => option.id === candidate);
  if (exact) {
    if (isOtherOption(exact)) throw new Error("请输入其他回答");
    return exact.id;
  }

  const byStringifiedId = options.filter(
    option => String(option.id) === String(candidate),
  );
  if (byStringifiedId.length === 1) {
    if (isOtherOption(byStringifiedId[0])) throw new Error("请输入其他回答");
    return byStringifiedId[0].id;
  }
  if (byStringifiedId.length > 1) throw new Error("选项不唯一，请重新选择");

  if (typeof candidate === "string") {
    const byTypedKey = options.filter(option => optionKey(option.id) === candidate);
    if (byTypedKey.length === 1) {
      if (isOtherOption(byTypedKey[0])) throw new Error("请输入其他回答");
      return byTypedKey[0].id;
    }

    const byLabel = options.filter(option => option.label === candidate);
    if (byLabel.length === 1) {
      if (isOtherOption(byLabel[0])) throw new Error("请输入其他回答");
      return byLabel[0].id;
    }
    if (byLabel.length > 1) throw new Error("选项标签不唯一，请重新选择");
    if (options.some(isOtherOption)) return candidate;
  }

  throw new Error("答案必须匹配一个可选项");
}

function isValidOptionId(value: unknown): value is AskUserQuestionOptionId {
  return (
    (typeof value === "string" && value.trim().length > 0) ||
    (typeof value === "number" && Number.isFinite(value))
  );
}

function hasExactOption(
  pending: PendingQuestionItem,
  answer: AskUserQuestionOptionId,
): boolean {
  return pending.options?.some(option => option.id === answer) ?? false;
}

function optionKey(value: AskUserQuestionOptionId): string {
  return `${typeof value}:${String(value)}`;
}

function isOptionObject(value: unknown): value is AskUserQuestionOption {
  return isAnswerRecord(value) && isValidOptionId(value.id);
}

function isAnswerRecord(
  answer: unknown,
): answer is Record<string, AskUserQuestionAnswerInput> {
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

When the user asks to fill in a form, complete a form, or provide form fields, use ask_user_question to collect the fields instead of asking in assistant text. Every non-confirmation question must include a context-based recommended default so the user can usually submit directly. String defaults must be non-empty; never use default:"". required:true controls whether the user may submit an empty answer.

Use exactly one ask_user_question call per assistant response. If you need more than one answer, use only the questions array: {"questions":[{"id":"leave_type","question":"请假类型？","options":["事假",{"id":"sick","label":"病假"}],"default":"事假","required":true},{"id":"start_at","question":"开始时间？","inputType":"date","dateFormat":"yyyy-MM-dd HH:mm","default":"2026-07-08 09:00","required":true},{"id":"reason","question":"原因？","default":"个人事务","required":true}]}. When questions is present, put every field's options, inputType, dateFormat, required, dataSource, multiple, and default inside the matching questions[] item; do not include top-level confirm or top-level field configuration.

For a single question, use top-level question/options/inputType/dateFormat/required/dataSource/multiple/default/confirm. For multiple questions, use questions[]. Dates require inputType:"date" plus dateFormat, for example "yyyy-MM-dd" or "yyyy-MM-dd HH:mm"; Dano returns the user's submitted date value as-is. required defaults to false; set required:true when an empty answer must not be submitted. default is still required for non-confirmation questions whether required is true or false, and string defaults must be non-empty. Use inputType:"select" or inputType:"treeSelect" with dataSource for remote API-backed choices. Confirmation is a separate single-question call with question + confirm: true and no options/multiple/questions. The answer is returned as a tool result and execution then continues.`,
  promptSnippet:
    "Ask the user one native question card; for several fields use one questions array with one submit button",
  promptGuidelines: [
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
    "When using questions, put each field's id, question, options, inputType, dateFormat, required, dataSource, multiple, and default inside its questions item. Do not put top-level field configuration beside questions.",
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
