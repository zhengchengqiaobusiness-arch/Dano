import { isDeepStrictEqual } from "node:util";
import { defineTool } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { parseAskUserQuestionDateValue, validateAskUserQuestionDateFormat } from "../../types/ask-user-question-date.js";
import {
  ASK_USER_QUESTION_TOOL_NAME,
  ASK_USER_QUESTION_PRESENTATION_RETRY_CODE,
  ASK_USER_QUESTION_PRESENTATION_TERMINAL_CODE,
  ASK_USER_QUESTION_VALIDATION_TERMINAL_CODE,
  type AskUserQuestionAnswer,
  type AskUserQuestionAnswerInput,
  type AskUserQuestionCardItem,
  type AskUserQuestionCardRequest,
  type AskUserQuestionConfirmationCardRequest,
  type AskUserQuestionDataSource,
  type AskUserQuestionInputType,
  type AskUserQuestionLifecycleState,
  type AskUserQuestionOption,
  type AskUserQuestionOptionId,
  type AskUserQuestionResult,
} from "./types.js";

export const ASK_USER_QUESTION_CANCELLED_CODE =
  "ASK_USER_QUESTION_CANCELLED";

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
  "You called ask_user_question more than once in the same response while another question is still pending. Retry silently with exactly one native ask_user_question call using {\"questions\":[...]} so all fields render in one card with one submit button. Put every field's options, inputType, fieldAssist, dateFormat, dataSource, multiple, required, and default inside its questions[] item. Do not explain this correction to the user.";

const missingConfirmationSourceError = JSON.stringify({
  code: "invalid_confirmation_source",
  receivedShape: { formIds: "omitted", formId: "omitted" },
  ignoredReasons: [],
  fallbackAttempted: true,
  retry: "Submit a grouped form first, then confirm it with the returned formId.",
  example: { confirm: true, formIds: ["<formId>"] },
});

const askUserQuestionFields = {
  question: Type.Optional(
    Type.Any({
      description:
        "Single-question call: the clear, specific question to ask the user. With questions[], top-level question/title/label/prompt is treated only as optional form instruction text; each actual field question must be inside questions[].",
    }),
  ),
  title: Type.Optional(Type.Any()),
  label: Type.Optional(Type.Any()),
  prompt: Type.Optional(Type.Any()),
  options: Type.Optional(
    Type.Any({
      description:
        "Choices for this question. Strings remain supported; objects use stable id plus label. Include '其他' or 'Other' to let the user enter one custom answer. Omit for free-text, confirmation, or remote dataSource input.",
    }),
  ),
  choices: Type.Optional(Type.Any()),
  inputType: Type.Optional(Type.Any()),
  type: Type.Optional(Type.Any()),
  input_type: Type.Optional(Type.Any()),
  component: Type.Optional(Type.Any()),
  fieldAssist: Type.Optional(
    Type.Any({
      description:
        "Controls whether text fields show Field Assist generation and polishing actions. Single-line text defaults to false; textarea defaults to true. Enable it when drafting or polishing business text would help; factual short values usually omit it.",
    }),
  ),
  dateFormat: Type.Optional(
    Type.Any({
      description:
        "Required when inputType is \"date\". A frontend date-control format such as \"yyyy-MM-dd\" or \"yyyy-MM-dd HH:mm\".",
    }),
  ),
  dataSource: Type.Optional(Type.Any()),
  data_source: Type.Optional(Type.Any()),
  multiple: Type.Optional(
    Type.Any({
      default: false,
      description: "Set true with options to allow multiple selections.",
    }),
  ),
  multi: Type.Optional(Type.Any()),
  multipleSelect: Type.Optional(Type.Any()),
  required: Type.Optional(
    Type.Any({
      description:
        "Set true to require a non-empty answer. Defaults to false.",
    }),
  ),
  default: Type.Optional(Type.Any()),
  defaultValue: Type.Optional(Type.Any()),
  prefill: Type.Optional(Type.Any()),
  value: Type.Optional(Type.Any()),
};

export const askUserQuestionParameters = Type.Object({
  ...askUserQuestionFields,
  confirm: Type.Optional(
    Type.Any({
      description:
        "Confirm one or more previously submitted grouped forms. Use {confirm:true,formIds:[\"<formId>\"]}; Dano supplies each selected form's title and latest submitted answers.",
    }),
  ),
  formIds: Type.Optional(
    Type.Any({
      description:
        "Standard grouped-form confirmation target: an array of formId strings returned by earlier grouped form submissions in this Assistant Turn.",
    }),
  ),
  questions: Type.Optional(
    Type.Any({
      description:
        "Preferred for collecting more than one answer. Make exactly one ask_user_question call with questions: [{ id, question, default, options?, multiple?, inputType?, fieldAssist?, dateFormat?, required?, dataSource? }, ...]. Every non-confirmation questions[] item must include a context-based, non-empty default. A single question object is also accepted and normalized to an array. When questions is present, put each field's options, inputType, fieldAssist, dateFormat, required, dataSource, multiple, and default inside its questions[] item. Do not include top-level confirm or top-level field configuration with questions.",
    }),
  ),
});

export const askUserQuestionResultSchema = Type.Union([
  Type.Object({
    status: Type.Literal("answered"),
    formId: Type.Optional(Type.String()),
    answer: Type.Union([
      askUserQuestionAnswerSchema,
      Type.Record(Type.String(), askUserQuestionAnswerSchema),
    ]),
  }),
  Type.Object({
    status: Type.Literal("confirmed"),
    answer: Type.Record(Type.String(), askUserQuestionAnswerSchema),
    confirmationOfToolCallId: Type.String(),
    forms: Type.Array(
      Type.Object({
        formId: Type.String(),
        answer: Type.Record(Type.String(), askUserQuestionAnswerSchema),
      }),
    ),
  }),
  Type.Object({ status: Type.Literal("cancelled") }),
]);

type PendingQuestionKind = "text" | "date" | "single" | "multiple" | "confirm";

type AskUserQuestionRequestItem = {
  id?: unknown;
  key?: unknown;
  name?: unknown;
  question?: unknown;
  title?: unknown;
  label?: unknown;
  prompt?: unknown;
  options?: unknown;
  choices?: unknown;
  inputType?: unknown;
  type?: unknown;
  input_type?: unknown;
  component?: unknown;
  fieldAssist?: unknown;
  field_assist?: unknown;
  aiAssist?: unknown;
  ai_assist?: unknown;
  dateFormat?: unknown;
  dataSource?: unknown;
  data_source?: unknown;
  multiple?: unknown;
  multi?: unknown;
  multipleSelect?: unknown;
  required?: unknown;
  confirm?: unknown;
  formId?: unknown;
  formIds?: unknown;
  default?: unknown;
  defaultValue?: unknown;
  prefill?: unknown;
  value?: unknown;
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
  question: string;
  inputType: AskUserQuestionInputType;
  fieldAssist?: boolean;
  options?: readonly PendingQuestionOption[];
  dataSource?: AskUserQuestionDataSource;
  dateFormat?: string;
  required: boolean;
  default?: AskUserQuestionAnswer;
}

interface PendingQuestion {
  grouped: boolean;
  questions: readonly PendingQuestionItem[];
  cardRequest: AskUserQuestionCardRequest;
  confirmation?: SubmittedForm[];
  state: "awaiting_presentation" | "presented";
  signal?: AbortSignal;
  presentationTimer?: ReturnType<typeof setTimeout>;
  resolve(result: AskUserQuestionResult): void;
  reject(error: Error): void;
  cleanup(): void;
}

interface SubmittedForm {
  toolCallId: string;
  questions: readonly PendingQuestionItem[];
  cardRequest: Extract<AskUserQuestionCardRequest, { batch: true }>;
  answer: Record<string, AskUserQuestionAnswer>;
}

function isOtherOption(value: string | PendingQuestionOption): boolean {
  const normalized = (typeof value === "string" ? value : value.label)
    .trim()
    .toLocaleLowerCase();
  return normalized === "其他" || normalized === "other";
}

export class AskUserQuestionCoordinator {
  private readonly pending = new Map<string, PendingQuestion>();
  private readonly pendingToolCallBySignal = new WeakMap<AbortSignal, string>();
  private readonly presentationFailuresBySignal = new WeakMap<AbortSignal, number>();
  private readonly validationFailuresBySignal = new WeakMap<AbortSignal, number>();
  private readonly submittedFormsBySignal = new WeakMap<
    AbortSignal,
    Map<string, SubmittedForm>
  >();

  constructor(
    private readonly presentationTimeoutMs = 5_000,
    private readonly maxRetries = 10,
  ) {}

  wait(
    toolCallId: string,
    rawRequest: AskUserQuestionRequestParams,
    signal: AbortSignal | undefined,
  ): Promise<AskUserQuestionResult> {
    if (this.pending.has(toolCallId)) {
      logQuestionLifecycle(toolCallId, "invalid");
      return Promise.reject(
        new Error(`Question is already pending: ${toolCallId}`),
      );
    }

    const parsedRequest = parseJsonString(rawRequest);
    const confirmationCall = isAskUserQuestionConfirmationCall(parsedRequest);
    const confirmationSelection = confirmationCall
      ? selectSubmittedForms(
          parsedRequest,
          signal ? this.submittedFormsBySignal.get(signal) : undefined,
        )
      : undefined;
    if (confirmationSelection && "error" in confirmationSelection) {
      return this.rejectValidation(
        toolCallId,
        confirmationSelection.error,
        signal,
      );
    }
    const normalized = confirmationCall
      ? confirmationRequest(
          (confirmationSelection as { forms: SubmittedForm[] }).forms,
        )
      : normalizeAskUserQuestionRequest(rawRequest);
    if ("error" in normalized) {
      return this.rejectValidation(toolCallId, normalized.error, signal);
    }
    if (normalized.request.questions !== undefined && !normalized.request.title) {
      return this.rejectValidation(
        toolCallId,
        "Grouped forms require a top-level title",
        signal,
      );
    }
    if (signal) this.validationFailuresBySignal.delete(signal);
    const { request, questions, cardRequest } = normalized;
    const pendingInCurrentTurn = signal
      ? this.pendingToolCallBySignal.get(signal)
      : undefined;
    if (
      pendingInCurrentTurn !== undefined &&
      this.pending.has(pendingInCurrentTurn)
    ) {
      logQuestionLifecycle(toolCallId, "invalid");
      return Promise.reject(new Error(groupedRetryError));
    }
    return new Promise((resolve, reject) => {
      const cleanup = () => {
        const pending = this.pending.get(toolCallId);
        if (pending?.presentationTimer) {
          clearTimeout(pending.presentationTimer);
        }
        this.pending.delete(toolCallId);
        signal?.removeEventListener("abort", abort);
        if (signal && this.pendingToolCallBySignal.get(signal) === toolCallId) {
          this.pendingToolCallBySignal.delete(signal);
        }
      };
      const abort = () => {
        cleanup();
        logQuestionLifecycle(toolCallId, "cancelled");
        reject(
          new Error(
            `${ASK_USER_QUESTION_CANCELLED_CODE}: Question was aborted`,
          ),
        );
      };
      signal?.addEventListener("abort", abort, { once: true });
      if (signal) this.pendingToolCallBySignal.set(signal, toolCallId);

      this.pending.set(toolCallId, {
        grouped: request.questions !== undefined,
        questions,
        cardRequest,
        ...(confirmationSelection && "forms" in confirmationSelection
          ? { confirmation: confirmationSelection.forms }
          : {}),
        state: "awaiting_presentation",
        signal,
        resolve,
        reject,
        cleanup,
      });
      logQuestionLifecycle(toolCallId, "awaiting_presentation");

      const pending = this.pending.get(toolCallId);
      if (pending) {
        pending.presentationTimer = setTimeout(() => {
          const failures = signal
            ? (this.presentationFailuresBySignal.get(signal) ?? 0) + 1
            : this.maxRetries + 1;
          if (signal) this.presentationFailuresBySignal.set(signal, failures);
          const terminal = failures > this.maxRetries;
          cleanup();
          logQuestionLifecycle(
            toolCallId,
            terminal ? "terminal_failure" : "retrying",
          );
          reject(
            new Error(
              terminal
                ? `${ASK_USER_QUESTION_PRESENTATION_TERMINAL_CODE}: Dano could not display the question card after bounded retries. Stop this response and let the user retry.`
                : `${ASK_USER_QUESTION_PRESENTATION_RETRY_CODE}: The accepted question card was not presented. Retry with a corrected native ask_user_question call.`,
            ),
          );
        }, this.presentationTimeoutMs);
      }

      if (signal?.aborted) abort();
    });
  }

  present(toolCallId: string): void {
    const pending = this.pending.get(toolCallId);
    if (!pending) throw new Error(`Pending question not found: ${toolCallId}`);
    if (pending.state === "presented") return;
    if (pending.presentationTimer) {
      clearTimeout(pending.presentationTimer);
      pending.presentationTimer = undefined;
    }
    pending.state = "presented";
    logQuestionLifecycle(toolCallId, "presented");
    if (pending.signal) {
      this.presentationFailuresBySignal.delete(pending.signal);
    }
  }

  state(
    toolCallId: string,
  ): Extract<AskUserQuestionLifecycleState, "awaiting_presentation" | "presented"> | undefined {
    return this.pending.get(toolCallId)?.state;
  }

  cardRequest(toolCallId: string): AskUserQuestionCardRequest | undefined {
    return this.pending.get(toolCallId)?.cardRequest;
  }

  pendingConfirmationRequests(): Array<{
    toolCallId: string;
    request: AskUserQuestionConfirmationCardRequest;
  }> {
    const requests: Array<{
      toolCallId: string;
      request: AskUserQuestionConfirmationCardRequest;
    }> = [];
    for (const [toolCallId, pending] of this.pending) {
      if (!pending.confirmation) continue;
      requests.push({
        toolCallId,
        request: pending.cardRequest as AskUserQuestionConfirmationCardRequest,
      });
    }
    return requests;
  }

  submitConfirmationRevision(
    toolCallId: string,
    answers: Record<string, Record<string, AskUserQuestionAnswerInput>>,
  ): AskUserQuestionConfirmationCardRequest {
    const pending = this.pending.get(toolCallId);
    if (!pending?.confirmation) {
      throw new Error(`Pending confirmation not found: ${toolCallId}`);
    }
    const normalizedAnswers = pending.confirmation.map(form => {
      const answer = answers[form.toolCallId];
      return answer
        ? normalizeGroupedAnswer(form.questions, answer)
        : form.answer;
    });
    for (const [index, form] of pending.confirmation.entries()) {
      form.answer = normalizedAnswers[index];
    }
    pending.cardRequest = confirmationCardRequest(pending.confirmation);
    return pending.cardRequest as AskUserQuestionConfirmationCardRequest;
  }

  confirmationRevisionMatches(
    toolCallId: string,
    answers: Record<string, Record<string, AskUserQuestionAnswerInput>>,
  ): boolean {
    const pending = this.pending.get(toolCallId);
    if (!pending?.confirmation) return false;
    for (const [formId, answer] of Object.entries(answers)) {
      const form = pending.confirmation.find(candidate => candidate.toolCallId === formId);
      if (!form) return false;
      const normalized = normalizeGroupedAnswer(form.questions, answer);
      if (!isDeepStrictEqual(normalized, form.answer)) return false;
    }
    return true;
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
    if (pending.state === "awaiting_presentation") {
      this.present(toolCallId);
    }

    let result: AskUserQuestionResult;
    if (response.cancelled) {
      result = { status: "cancelled" };
    } else if (pending.confirmation) {
      if (response.answer !== true) {
        throw new Error("Confirmation answer must be true");
      }
      const confirmedForms = pending.confirmation.map(form => ({
        formId: form.toolCallId,
        answer: { ...form.answer },
      }));
      const firstConfirmedForm = confirmedForms[0];
      result = {
        status: "confirmed",
        answer: firstConfirmedForm.answer,
        confirmationOfToolCallId: firstConfirmedForm.formId,
        forms: confirmedForms,
      };
    } else {
      const { answer } = response;
      if (pending.grouped) {
        if (!isAnswerRecord(answer)) {
          throw new Error("Grouped question answer must be an object");
        }
        const normalized = normalizeGroupedAnswer(pending.questions, answer);
        result = { status: "answered", answer: normalized, formId: toolCallId };
        if (pending.signal && pending.cardRequest.batch) {
          const submittedForms =
            this.submittedFormsBySignal.get(pending.signal) ?? new Map();
          submittedForms.set(toolCallId, {
            toolCallId,
            questions: pending.questions,
            cardRequest: pending.cardRequest,
            answer: normalized,
          });
          this.submittedFormsBySignal.set(pending.signal, submittedForms);
        }
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

    if (pending.confirmation && pending.signal) {
      const submittedForms = this.submittedFormsBySignal.get(pending.signal);
      for (const form of pending.confirmation) {
        submittedForms?.delete(form.toolCallId);
      }
      if (submittedForms?.size === 0) {
        this.submittedFormsBySignal.delete(pending.signal);
      }
    }
    pending.cleanup();
    logQuestionLifecycle(
      toolCallId,
      result.status === "cancelled" ? "cancelled" : "answered",
    );
    pending.resolve(result);
    return result;
  }

  cancelAll(): void {
    this.rejectAll(
      new Error(
        `${ASK_USER_QUESTION_CANCELLED_CODE}: Question coordinator was disposed`,
      ),
    );
  }

  private rejectAll(error: Error): void {
    for (const [toolCallId, pending] of this.pending) {
      pending.cleanup();
      logQuestionLifecycle(toolCallId, "cancelled");
      pending.reject(error);
    }
  }

  private rejectValidation(
    toolCallId: string,
    message: string,
    signal: AbortSignal | undefined,
  ): Promise<never> {
    const failures = signal
      ? (this.validationFailuresBySignal.get(signal) ?? 0) + 1
      : this.maxRetries + 1;
    if (signal) this.validationFailuresBySignal.set(signal, failures);
    const terminal = failures > this.maxRetries;
    logQuestionLifecycle(toolCallId, terminal ? "terminal_failure" : "invalid");
    return Promise.reject(
      new Error(
        terminal
          ? `${ASK_USER_QUESTION_VALIDATION_TERMINAL_CODE}: Repeated invalid ask_user_question calls. Stop this response and let the user retry. Last validation error: ${message}`
          : message,
      ),
    );
  }
}

function logQuestionLifecycle(
  toolCallId: string,
  state: AskUserQuestionLifecycleState,
): void {
  console.info(`[ask_user_question] state=${state} toolCallId=${toolCallId}`);
}

type NormalizedAskUserQuestionRequestItem = {
  id?: string;
  question?: string;
  options?: readonly (string | AskUserQuestionOption)[];
  inputType?: AskUserQuestionInputType;
  fieldAssist?: boolean;
  dataSource?: AskUserQuestionDataSource;
  multiple?: boolean;
  dateFormat?: unknown;
  required?: unknown;
  confirm?: true;
  default?: AskUserQuestionAnswerInput;
};

type NormalizedAskUserQuestionRequest = NormalizedAskUserQuestionRequestItem & {
  title?: string;
  questions?: readonly NormalizedAskUserQuestionRequestItem[];
};

type CompatibleRequestResult =
  | { request: NormalizedAskUserQuestionRequest }
  | { error: string };

type CompatibleQuestionsResult =
  | { questions: NormalizedAskUserQuestionRequestItem[] }
  | { error: string };

type CompatibleQuestionResult =
  | { question: NormalizedAskUserQuestionRequestItem }
  | { error: string };

function normalizeCompatibleRequest(
  request: AskUserQuestionRequestParams,
): CompatibleRequestResult {
  const rawQuestions = request.questions;
  const questionResult = normalizeCompatibleQuestion(
    request,
    rawQuestions === undefined,
  );
  if ("error" in questionResult) return questionResult;
  const normalized: NormalizedAskUserQuestionRequest = questionResult.question;
  if (rawQuestions !== undefined) {
    normalized.title = firstScalarString(request.title);
    normalized.question = firstScalarString(request.question, request.label, request.prompt);
    if (!normalized.question) delete normalized.question;
    const questionsResult = normalizeCompatibleQuestions(rawQuestions);
    if ("error" in questionsResult) return questionsResult;
    normalized.questions = questionsResult.questions;
    return { request: foldCompatibleGroupedFields(normalized) };
  }
  return { request: normalized };
}

function normalizeCompatibleQuestions(
  value: AskUserQuestionRequestParams["questions"],
): CompatibleQuestionsResult {
  let parsed = value;
  if (typeof value === "string") {
    try {
      parsed = JSON.parse(value) as unknown;
    } catch {
      return {
        error: "Invalid questions: questions must be valid JSON containing an array or object",
      };
    }
  }
  if (!Array.isArray(parsed) && !isPlainRecord(parsed)) {
    return { error: "Invalid questions: questions must be an array or object" };
  }
  const rawItems = Array.isArray(parsed) ? parsed : [parsed];
  if (!rawItems.every(isPlainRecord)) {
    return {
      error: "Invalid questions: every questions array item must be an object",
    };
  }
  const questions: NormalizedAskUserQuestionRequestItem[] = [];
  for (const value of rawItems) {
    const result = normalizeCompatibleQuestion(value);
    if ("error" in result) return result;
    questions.push(result.question);
  }
  return { questions };
}

function normalizeCompatibleQuestion(
  request: AskUserQuestionRequestItem | Record<string, unknown>,
  includeTitleAsQuestion = true,
): CompatibleQuestionResult {
  const normalized: NormalizedAskUserQuestionRequestItem = {};
  const id = normalizeCompatibleAliases(
    "question id",
    [request.id, request.key, request.name],
    firstScalarString,
  );
  if ("error" in id) return id;
  if (id.value) normalized.id = id.value;

  const question = normalizeCompatibleAliases(
    "question text",
    [
      request.question,
      request.label,
      request.prompt,
      ...(includeTitleAsQuestion ? [request.title] : []),
    ],
    firstScalarString,
  );
  if ("error" in question) return question;
  if (question.value) normalized.question = question.value;

  const inputType = normalizeCompatibleAliases(
    "input type",
    [request.inputType, request.input_type, request.type, request.component],
    normalizeInputType,
  );
  if ("error" in inputType) return inputType;
  if (inputType.value) normalized.inputType = inputType.value;

  const options = normalizeCompatibleAliases(
    "options",
    [request.options, request.choices],
    normalizeCompatibleOptionsAlias,
  );
  if ("error" in options) return options;
  const optionsProvided = request.options !== undefined || request.choices !== undefined;
  if (options.value) {
    normalized.options = options.value;
  } else if (
    optionsProvided &&
    inputType.value !== "text" &&
    inputType.value !== "textarea" &&
    inputType.value !== "date" &&
    inputType.value !== "confirm"
  ) {
    normalized.inputType = inputType.value ?? "radio";
  }

  const fieldAssist = firstNormalizedFieldAssistValue(
    request.fieldAssist,
    request.field_assist,
    request.aiAssist,
    request.ai_assist,
  );
  if (fieldAssist !== undefined) normalized.fieldAssist = fieldAssist;

  if ("dateFormat" in request) normalized.dateFormat = request.dateFormat;

  const dataSource = normalizeCompatibleAliases(
    "data source",
    [request.dataSource, request.data_source],
    normalizeCompatibleDataSource,
  );
  if ("error" in dataSource) return dataSource;
  if (dataSource.value) normalized.dataSource = dataSource.value;
  if (dataSource.value && !inputType.value) normalized.inputType = "select";

  const multiple = normalizeCompatibleAliases(
    "multiple",
    [request.multiple, request.multi, request.multipleSelect],
    normalizeBoolean,
  );
  if ("error" in multiple) return multiple;
  if (multiple.value !== undefined) normalized.multiple = multiple.value;

  const required = normalizeBoolean(request.required);
  if (required !== undefined) normalized.required = required;

  if (normalizeBoolean(request.confirm) === true || inputType.value === "confirm") {
    normalized.confirm = true;
  }

  const defaultValues = [
    request.default,
    request.defaultValue,
    request.prefill,
    request.value,
  ];
  const compatibleDefault = normalizeCompatibleAliases(
    "default",
    defaultValues,
    normalizeCompatibleDefault,
  );
  if ("error" in compatibleDefault) return compatibleDefault;
  if (compatibleDefault.value !== undefined) {
    normalized.default = compatibleDefault.value;
  } else {
    const invalidDefault = firstNormalizedValue(
      defaultValues,
      normalizeCompatibleAnswerInput,
    );
    if (invalidDefault !== undefined) normalized.default = invalidDefault;
  }

  return { question: normalized };
}

function foldCompatibleGroupedFields(
  request: NormalizedAskUserQuestionRequest,
): NormalizedAskUserQuestionRequest {
  const questions = request.questions ?? [];
  if (questions.length !== 1) {
    return { title: request.title, questions };
  }

  const [question] = questions;
  return {
    title: request.title,
    questions: [
      {
        ...question,
        id: question.id ?? request.id,
        question: question.question ?? request.question,
        options: question.options ?? request.options,
        inputType: question.inputType ?? request.inputType,
        fieldAssist: question.fieldAssist ?? request.fieldAssist,
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

export function isAskUserQuestionConfirmationCall(
  rawRequest: unknown,
): boolean {
  const parsed = parseJsonString(rawRequest);
  return (
    isPlainRecord(parsed) &&
    normalizeBoolean(parsed.confirm) === true &&
    parsed.questions === undefined
  );
}

function selectSubmittedForms(
  rawRequest: unknown,
  availableForms: Map<string, SubmittedForm> | undefined,
): { forms: SubmittedForm[] } | { error: string } {
  const selection = selectAskUserQuestionConfirmationTargets(
    rawRequest,
    availableForms,
  );
  if (selection.targets.length > 0) {
    return { forms: selection.targets };
  }

  return {
    error: JSON.stringify({
      code: "invalid_confirmation_source",
      receivedShape: selection.receivedShape,
      ignoredReasons: selection.ignoredReasons,
      fallbackAttempted: selection.fallbackAttempted,
      retry:
        "Submit a grouped form first, then confirm it with the returned formId.",
      example: { confirm: true, formIds: ["<formId>"] },
    }),
  };
}

export function selectAskUserQuestionConfirmationTargets<T extends object>(
  rawRequest: unknown,
  availableTargets: Map<string, T> | undefined,
): {
  targets: T[];
  ignoredReasons: string[];
  receivedShape: { formIds: string; formId: string };
  fallbackAttempted: boolean;
} {
  const parsedRequest = parseJsonString(rawRequest);
  const request = isPlainRecord(parsedRequest) ? parsedRequest : {};
  const candidates: unknown[] = [];
  const ignoredReasons = new Set<string>();

  collectConfirmationFormIds(
    "formIds",
    request.formIds,
    candidates,
    ignoredReasons,
  );
  collectConfirmationFormIds(
    "formId",
    request.formId,
    candidates,
    ignoredReasons,
  );

  const selected = new Map<string, T>();
  for (const candidate of candidates) {
    if (typeof candidate !== "string" || !candidate.trim()) {
      ignoredReasons.add("malformed_form_id");
      continue;
    }
    const formId = candidate.trim();
    if (!availableTargets?.has(formId)) {
      ignoredReasons.add("unavailable_form_id");
      continue;
    }
    selected.set(formId, availableTargets.get(formId) as T);
  }
  if (selected.size > 0) {
    return {
      targets: [...selected.values()],
      ignoredReasons: [...ignoredReasons],
      receivedShape: confirmationReceivedShape(request),
      fallbackAttempted: false,
    };
  }

  const latestEligibleTarget = availableTargets
    ? [...availableTargets.values()].at(-1)
    : undefined;
  return {
    targets: latestEligibleTarget ? [latestEligibleTarget] : [],
    ignoredReasons: [...ignoredReasons],
    receivedShape: confirmationReceivedShape(request),
    fallbackAttempted: true,
  };
}

function confirmationReceivedShape(
  request: Record<string, unknown>,
): { formIds: string; formId: string } {
  return {
    formIds: confirmationParameterShape(request.formIds),
    formId: confirmationParameterShape(request.formId),
  };
}

function collectConfirmationFormIds(
  field: "formId" | "formIds",
  value: unknown,
  candidates: unknown[],
  ignoredReasons: Set<string>,
): void {
  if (value === undefined) return;
  if (Array.isArray(value)) {
    candidates.push(...value);
    return;
  }
  if (typeof value === "string") {
    candidates.push(value);
    return;
  }
  ignoredReasons.add(`malformed_${field}`);
}

function confirmationParameterShape(value: unknown): string {
  if (value === undefined) return "omitted";
  if (Array.isArray(value)) return `array(${value.length})`;
  return typeof value;
}

function normalizeCompatibleOptions(
  value: unknown,
): { values: Array<string | AskUserQuestionOption>; invalid: boolean } {
  if (!Array.isArray(value)) {
    return { values: [], invalid: value !== undefined };
  }
  const values = value.flatMap(option => {
    const normalized = normalizeCompatibleOption(option);
    return normalized === undefined ? [] : [normalized];
  });
  return { values, invalid: values.length !== value.length };
}

function normalizeCompatibleOptionsAlias(
  value: unknown,
): Array<string | AskUserQuestionOption> | undefined {
  const normalized = normalizeCompatibleOptions(value);
  return !normalized.invalid && normalized.values.length > 0
    ? normalized.values
    : undefined;
}

function normalizeCompatibleOption(
  value: unknown,
): string | AskUserQuestionOption | undefined {
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed || undefined;
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return { id: value, label: String(value) };
  }
  if (typeof value === "boolean") return String(value);
  if (!isPlainRecord(value)) return undefined;

  const idAliases = normalizeCompatibleAliases(
    "option id",
    [value.id, value.value, value.key],
    normalizeOptionId,
  );
  const labelAliases = normalizeCompatibleAliases(
    "option label",
    [value.label, value.text, value.name],
    firstScalarString,
  );
  if ("error" in idAliases || "error" in labelAliases) return undefined;
  const id = idAliases.value ?? normalizeOptionId(labelAliases.value);
  const label = labelAliases.value ?? firstScalarString(id);
  if (id === undefined || !label) return undefined;
  return {
    id,
    label,
    ...(isPlainRecord(value.extra) ? { extra: value.extra } : {}),
  };
}

function normalizeOptionId(value: unknown): AskUserQuestionOptionId | undefined {
  if (typeof value === "number") return Number.isFinite(value) ? value : undefined;
  if (typeof value === "boolean") return String(value);
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed || undefined;
}

function normalizeCompatibleAnswerInput(
  value: unknown,
): AskUserQuestionAnswerInput | undefined {
  if (
    typeof value === "string" ||
    typeof value === "boolean" ||
    (typeof value === "number" && Number.isFinite(value))
  ) {
    return value;
  }
  if (Array.isArray(value)) {
    const items = value.flatMap(item => {
      const normalized = normalizeCompatibleOption(item);
      return normalized === undefined ? [] : [normalized];
    });
    return items;
  }
  return normalizeCompatibleOption(value);
}

function normalizeCompatibleDefault(
  value: unknown,
): AskUserQuestionAnswerInput | undefined {
  const normalized = normalizeCompatibleAnswerInput(value);
  if (typeof normalized === "string" && !normalized.trim()) return undefined;
  if (Array.isArray(normalized) && normalized.length === 0) return undefined;
  return normalized;
}

function normalizeCompatibleDataSource(
  value: unknown,
): AskUserQuestionDataSource | undefined {
  const parsed = parseJsonString(value);
  if (!isPlainRecord(parsed)) return undefined;
  if (firstScalarString(parsed.type)?.toLowerCase() !== "api") return undefined;
  const endpoint = firstScalarString(parsed.endpoint)?.trim();
  if (!endpoint) return undefined;

  const methodValue = firstScalarString(parsed.method)?.toUpperCase();
  const method = methodValue === "GET" || methodValue === "POST"
    ? methodValue
    : undefined;
  const pageSizeValue =
    typeof parsed.pageSize === "number"
      ? parsed.pageSize
      : typeof parsed.pageSize === "string"
        ? Number(parsed.pageSize.trim())
        : Number.NaN;
  const pageSize = Number.isFinite(pageSizeValue) && pageSizeValue >= 1
    ? pageSizeValue
    : undefined;
  const extraFieldsValue = Array.isArray(parsed.extraFields)
    ? parsed.extraFields
    : parsed.extraFields === undefined
      ? []
      : [parsed.extraFields];
  const extraFields = extraFieldsValue.flatMap(field => {
    const normalized = firstScalarString(field);
    return normalized ? [normalized] : [];
  });

  return {
    type: "api",
    endpoint,
    ...(method ? { method } : {}),
    ...(isPlainRecord(parsed.params) ? { params: parsed.params } : {}),
    ...normalizedDataSourceStrings(parsed),
    ...(pageSize !== undefined ? { pageSize } : {}),
    ...(extraFields.length > 0 ? { extraFields } : {}),
  };
}

function normalizedDataSourceStrings(
  source: Record<string, unknown>,
): Partial<AskUserQuestionDataSource> {
  const normalized: Partial<AskUserQuestionDataSource> = {};
  for (const key of [
    "searchParam",
    "pageParam",
    "pageSizeParam",
    "resultPath",
    "totalPath",
    "idField",
    "labelField",
    "childrenField",
  ] as const) {
    const value = firstScalarString(source[key]);
    if (value) normalized[key] = value;
  }
  return normalized;
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function firstScalarString(...values: unknown[]): string | undefined {
  for (const value of values) {
    if (
      typeof value !== "string" &&
      typeof value !== "number" &&
      typeof value !== "boolean"
    ) {
      continue;
    }
    if (typeof value === "number" && !Number.isFinite(value)) continue;
    const trimmed = String(value).trim();
    if (trimmed) return trimmed;
  }
  return undefined;
}

function normalizeCompatibleAliases<T>(
  name: string,
  values: unknown[],
  normalize: (value: unknown) => T | undefined,
): { value?: T } | { error: string } {
  let selected: T | undefined;
  for (const value of values) {
    if (value === undefined) continue;
    const normalized = normalize(value);
    if (normalized === undefined) continue;
    if (selected === undefined) {
      selected = normalized;
      continue;
    }
    if (!isDeepStrictEqual(selected, normalized)) {
      return { error: `Conflicting compatible aliases for ${name}` };
    }
  }
  return selected === undefined ? {} : { value: selected };
}

function normalizeBoolean(value: unknown): boolean | undefined {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") {
    if (value === 0) return false;
    if (value === 1) return true;
    return undefined;
  }
  if (typeof value !== "string") return undefined;
  const normalized = value.trim().toLowerCase();
  if (["true", "1", "yes", "on", "是", "开启", "启用"].includes(normalized)) {
    return true;
  }
  if (["false", "0", "no", "off", "否", "关闭", "禁用"].includes(normalized)) {
    return false;
  }
  return undefined;
}

function normalizeFieldAssistValue(value: unknown): boolean | undefined {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value === 0 ? false : true;
  if (typeof value !== "string") return undefined;

  const normalized = value.trim().toLowerCase();
  if (["false", "0", "no", "off", "disable", "disabled", "否", "关闭", "禁用"].includes(normalized)) {
    return false;
  }
  if (["true", "1", "yes", "on", "enable", "enabled", "是", "开启", "启用"].includes(normalized)) {
    return true;
  }
  return undefined;
}

function firstNormalizedFieldAssistValue(...values: unknown[]): boolean | undefined {
  return firstNormalizedValue(values, normalizeFieldAssistValue);
}

function firstNormalizedValue<T>(
  values: unknown[],
  normalize: (value: unknown) => T | undefined,
): T | undefined {
  for (const value of values) {
    const normalized = normalize(value);
    if (normalized !== undefined) return normalized;
  }
  return undefined;
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

export function normalizeAskUserQuestionCardRequest(
  rawRequest: unknown,
): AskUserQuestionCardRequest | null {
  const normalized = normalizeAskUserQuestionRequest(rawRequest);
  return "error" in normalized ? null : normalized.cardRequest;
}

function normalizeAskUserQuestionRequest(rawRequest: unknown):
  | {
      request: NormalizedAskUserQuestionRequest;
      questions: PendingQuestionItem[];
      cardRequest: AskUserQuestionCardRequest;
    }
  | { error: string } {
  const parsed = parseJsonString(rawRequest);
  if (!isPlainRecord(parsed)) return { error: "Question cannot be rendered" };
  const compatible = normalizeCompatibleRequest(
    parsed as AskUserQuestionRequestParams,
  );
  if ("error" in compatible) return compatible;
  const { request } = compatible;
  if (request.confirm && request.questions === undefined) {
    return { error: missingConfirmationSourceError };
  }
  const questions = normalizeRequestQuestions(request);
  if (typeof questions === "string") return { error: questions };
  if (questions.length === 0) return { error: "Question is required" };
  if (
    request.confirm &&
    (request.options || request.multiple || request.questions || request.dataSource)
  ) {
    return {
      error: "Confirmation questions cannot provide options or multiple",
    };
  }
  const cardItems = questions.map(toQuestionCardItem);
  const singleCard = cardItems[0];
  if (request.questions === undefined && singleCard.kind === "confirm") {
    return { error: missingConfirmationSourceError };
  }
  return {
    request,
    questions,
    cardRequest:
      request.questions === undefined
        ? {
            ...(singleCard as Exclude<AskUserQuestionCardItem, { kind: "confirm" }>),
            batch: false,
          }
        : {
            batch: true,
            ...(request.title ? { title: request.title } : {}),
            questions: cardItems,
          },
  };
}

function confirmationRequest(forms: SubmittedForm[]): {
  request: NormalizedAskUserQuestionRequest;
  questions: PendingQuestionItem[];
  cardRequest: AskUserQuestionConfirmationCardRequest;
} {
  return {
    request: { confirm: true },
    questions: forms.length === 1 ? [...forms[0].questions] : [],
    cardRequest: confirmationCardRequest(forms),
  };
}

function confirmationCardRequest(
  forms: SubmittedForm[],
): AskUserQuestionConfirmationCardRequest {
  return buildAskUserQuestionConfirmationCardRequestForForms(
    forms.map(form => ({
      formId: form.toolCallId,
      request: form.cardRequest,
      answer: form.answer,
    })),
  );
}

export function buildAskUserQuestionConfirmationCardRequest(
  confirmationOfToolCallId: string,
  request: Extract<AskUserQuestionCardRequest, { batch: true }>,
  answer: Record<string, AskUserQuestionAnswer>,
): AskUserQuestionConfirmationCardRequest {
  return buildAskUserQuestionConfirmationCardRequestForForms([
    { formId: confirmationOfToolCallId, request, answer },
  ]);
}

export function buildAskUserQuestionConfirmationCardRequestForForms(
  forms: Array<{
    formId: string;
    request: Extract<AskUserQuestionCardRequest, { batch: true }>;
    answer: Record<string, AskUserQuestionAnswer>;
  }>,
): AskUserQuestionConfirmationCardRequest {
  const firstForm = forms[0];
  if (!firstForm) {
    throw new Error("At least one Submitted Form is required for confirmation");
  }
  return {
    batch: false,
    kind: "confirm",
    id: "confirmation",
    title:
      forms.length === 1
        ? `${firstForm.request.title ?? "表单"}确认`
        : `确认 ${forms.length} 份表单`,
    confirmationOfToolCallId: firstForm.formId,
    questions: [...firstForm.request.questions],
    answer: { ...firstForm.answer },
    forms: forms.map(form => ({
      formId: form.formId,
      title: form.request.title ?? "表单",
      questions: [...form.request.questions],
      answer: { ...form.answer },
    })),
  };
}

function normalizeGroupedAnswer(
  questions: readonly PendingQuestionItem[],
  answer: Record<string, AskUserQuestionAnswerInput>,
): Record<string, AskUserQuestionAnswer> {
  const normalized: Record<string, AskUserQuestionAnswer> = {};
  for (const question of questions) {
    if (!(question.id in answer)) {
      if (question.required) {
        throw new Error(`Missing answer for grouped question: ${question.id}`);
      }
      continue;
    }
    normalized[question.id] = normalizeAnswer(question, answer[question.id]);
  }
  return normalized;
}

function toQuestionCardItem(
  question: PendingQuestionItem,
): AskUserQuestionCardItem {
  const common = {
    id: question.id,
    question: question.question,
    ...(question.required ? { required: true } : {}),
  };
  if (question.kind === "confirm") {
    return {
      ...common,
      kind: "confirm",
      ...(typeof question.default === "boolean"
        ? { default: question.default }
        : {}),
    };
  }
  if (question.kind === "date") {
    return {
      ...common,
      kind: "date",
      dateFormat: question.dateFormat ?? "",
      ...(typeof question.default === "string"
        ? { default: question.default }
        : {}),
    };
  }
  if (question.kind === "text") {
    return {
      ...common,
      kind: "text",
      fieldAssist: question.fieldAssist ?? question.inputType === "textarea",
      ...(question.inputType === "textarea"
        ? { inputType: "textarea" as const }
        : {}),
      ...(typeof question.default === "string"
        ? { default: question.default }
        : {}),
    };
  }
  if (question.kind === "multiple") {
    return {
      ...common,
      kind: "multiple",
      options: [...(question.options ?? [])],
      ...(question.dataSource ? { dataSource: question.dataSource } : {}),
      ...(question.inputType === "treeSelect"
        ? { inputType: "treeSelect" as const }
        : {}),
      ...(Array.isArray(question.default)
        ? { default: question.default }
        : {}),
    };
  }
  const choice = {
    ...common,
    options: [...(question.options ?? [])],
    ...(question.dataSource ? { dataSource: question.dataSource } : {}),
    ...(typeof question.default === "string" ||
    typeof question.default === "number"
      ? { default: question.default }
      : {}),
  };
  return question.inputType === "select" || question.inputType === "treeSelect"
    ? { ...choice, kind: question.inputType }
    : { ...choice, kind: "single" };
}

function normalizeQuestion(
  request: {
    id?: string;
    question?: string;
    options?: readonly (string | AskUserQuestionOption)[];
    inputType?: AskUserQuestionInputType;
    fieldAssist?: boolean;
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
  const required = request.required === true;
  const acceptsChoices =
    inputType === "radio" ||
    inputType === "checkbox" ||
    inputType === "select" ||
    inputType === "treeSelect";
  const acceptsDataSource = inputType === "select" || inputType === "treeSelect";
  const compatibleOptions = acceptsChoices ? request.options : undefined;
  const compatibleDataSource = acceptsDataSource ? request.dataSource : undefined;
  const multiple = acceptsChoices && (request.multiple || inputType === "checkbox");
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
  }
  const rawOptions = compatibleOptions?.map(normalizeOption);
  if (rawOptions?.some(option => !option)) {
    return "Question options must be non-empty and unique";
  }
  const options = rawOptions as PendingQuestionOption[] | undefined;
  const optionIds = options?.map(option => option.id) ?? [];
  if (new Set(optionIds).size !== optionIds.length) {
    return "Question options must be non-empty and unique";
  }
  if (
    multiple &&
    !options &&
    !compatibleDataSource
  ) {
    return "Multiple-choice questions require options or dataSource";
  }
  if (
    (inputType === "radio" || inputType === "select" || inputType === "treeSelect") &&
    !options &&
    !compatibleDataSource
  ) {
    return "Choice questions require options or dataSource";
  }

  let kind: PendingQuestionKind = "text";
  if (inputType === "confirm") kind = "confirm";
  else if (inputType === "date") kind = "date";
  else if (multiple) kind = "multiple";
  else if (options || compatibleDataSource || inputType === "radio" || inputType === "select" || inputType === "treeSelect") {
    kind = "single";
  }

  const question: PendingQuestionItem = {
    id: request.id?.trim() || fallbackId,
    kind,
    question: request.question.trim(),
    inputType,
    ...(kind === "text"
      ? { fieldAssist: request.fieldAssist ?? inputType === "textarea" }
      : {}),
    required,
    ...(options ? { options } : {}),
    ...(compatibleDataSource ? { dataSource: compatibleDataSource } : {}),
    ...(dateFormat ? { dateFormat } : {}),
  };
  if (kind !== "confirm" && request.default === undefined) {
    return "默认答案缺失：每个非确认问题都必须提供非空 default 推荐值";
  }
  if (request.default !== undefined) {
    const compatibleDefault =
      kind === "text" &&
      (typeof request.default === "number" || typeof request.default === "boolean")
        ? String(request.default)
        : request.default;
    if (typeof compatibleDefault === "string" && !compatibleDefault.trim()) {
      return "默认答案无效：default 必须是非空推荐值，不能是空字符串";
    }
    try {
      question.default = normalizeAnswer(question, compatibleDefault);
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

export function createAskUserQuestionTool(
  coordinator: AskUserQuestionCoordinator,
) {
  return defineTool({
  name: ASK_USER_QUESTION_TOOL_NAME,
  label: "Ask User Question",
  description: `Ask the user for structured input during execution.

When the user asks to fill in a form, complete a form, or provide form fields, use ask_user_question to collect the fields instead of asking in assistant text. Every non-confirmation question must include a context-based recommended default so the user can usually submit directly. String defaults must be non-empty; never use default:"". required:true controls whether the user may submit an empty answer.

Use exactly one ask_user_question call per assistant response. If you need more than one answer, provide a form title and use only the questions array: {"title":"请假申请","questions":[{"id":"leave_type","question":"请假类型？","options":["事假",{"id":"sick","label":"病假"}],"default":"事假","required":true},{"id":"start_at","question":"开始时间？","inputType":"date","dateFormat":"yyyy-MM-dd HH:mm","default":"2026-07-08 09:00","required":true},{"id":"reason","question":"原因？","default":"个人事务","fieldAssist":true,"required":true}]}. When questions is present, put every field's options, inputType, fieldAssist, dateFormat, required, dataSource, multiple, and default inside the matching questions[] item; do not include top-level confirm or top-level field configuration.

For a single question, use top-level question/options/inputType/fieldAssist/dateFormat/required/dataSource/multiple/default. For multiple questions, use title plus questions[]. fieldAssist controls generation and polishing actions for text fields; it defaults to false for single-line text and true for textarea. Dates require inputType:"date" plus dateFormat, for example "yyyy-MM-dd" or "yyyy-MM-dd HH:mm"; Dano returns the user's submitted date value as-is. required defaults to false; set required:true when an empty answer must not be submitted. default is required and string defaults must be non-empty. Use inputType:"select" or inputType:"treeSelect" with dataSource for remote API-backed choices. Dano normalizes unambiguous aliases and safe scalar deviations, ignores unknown or inapplicable optional fields, and rejects only inputs that cannot preserve rendering, submission, or answer mapping. When the workflow needs final confirmation for submitted grouped forms, call {"confirm":true,"formIds":["<formId>"]} with the formId values returned by those submissions. This is only for grouped-form confirmation; use a normal single-choice question to confirm an ordinary sentence or operation. If final confirmation is not needed, continue without this call.`,
  promptSnippet:
    "Ask the user one native question card; for several fields use one questions array with one submit button",
  promptGuidelines: [
    "Use ask_user_question whenever you need user input to continue; do not ask the question only in assistant text.",
    "When the user asks to fill in a form, complete a form, or provide form fields, collect the fields with ask_user_question.",
    "Call ask_user_question at most once per assistant response. If you need several answers, put every item in one questions array.",
    "If the user cancels ask_user_question, stop the current workflow. Do not ask again or retry unless the user sends a new message explicitly requesting it.",
    "Invoke ask_user_question as a native tool call. Never print, describe, or wrap a tool call in <question> tags, XML, JSON, Markdown, or other assistant text.",
    "If ask_user_question returns a validation error, retry silently with a corrected native tool call; do not explain the correction to the user.",
    "Use the documented canonical parameters. Dano treats model-generated arguments as best-effort input and normalizes safe aliases or coercions, but still rejects ambiguity that could change rendering, submission, or answer mapping.",
    "Give every non-confirmation question a context-based recommended non-empty default. Do not use empty string or placeholder defaults.",
    "Set required:true only when an answer is mandatory. required defaults to false.",
    "For date fields, use inputType:\"date\" and provide dateFormat such as \"yyyy-MM-dd\" or \"yyyy-MM-dd HH:mm\". The dateFormat configures the frontend date control display and submitted output.",
    "Dano returns the user's date answer as submitted; convert it yourself if a downstream interface needs another business format.",
    "Use fieldAssist to control generation and polishing actions on text fields. It defaults to false for single-line text and true for textarea; enable it when drafting or polishing business text would help, while factual short values usually omit it.",
    "When using questions, provide a concise top-level title and put each field's id, question, options, inputType, fieldAssist, dateFormat, required, dataSource, multiple, and default inside its questions item.",
    "When one or more submitted grouped forms require final confirmation, call ask_user_question with {confirm:true,formIds:[\"<formId>\"]} using their returned formId values. Do not send confirmation text or prior answers. If confirmation is not required, continue normally.",
    "Use confirm:true only for submitted grouped forms. To confirm an ordinary sentence or operation, ask a normal single-choice question instead.",
  ],
  parameters: askUserQuestionParameters,
  executionMode: "sequential",
  async execute(toolCallId, params, signal) {
    const result = await coordinator.wait(
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
              ? `User answered the question: ${JSON.stringify(result.answer)}.${result.formId ? ` Form ID: ${JSON.stringify(result.formId)}.` : ""} Continue with this answer.`
              : result.status === "confirmed"
                ? `User confirmed the final submitted forms: ${JSON.stringify(result.forms)}. Continue with these authoritative answers.`
              : "User cancelled the question. Stop the current workflow. Do not ask another question or retry unless the user sends a new message explicitly requesting it.",
        },
      ],
      details: result,
    };
  },
  });
}

export interface AskUserQuestionRuntime {
  coordinator: AskUserQuestionCoordinator;
  tool: ReturnType<typeof createAskUserQuestionTool>;
}

export function createAskUserQuestionRuntime(
  maxRetries = 10,
): AskUserQuestionRuntime {
  const coordinator = new AskUserQuestionCoordinator(5_000, maxRetries);
  return {
    coordinator,
    tool: createAskUserQuestionTool(coordinator),
  };
}

const runtimeState = globalThis as typeof globalThis & {
  __danoAskUserQuestionRuntime?: AskUserQuestionRuntime;
};

// ponytail: dev runtime reloads create separate module graphs in one process.
export const askUserQuestionRuntime =
  (runtimeState.__danoAskUserQuestionRuntime ??=
    createAskUserQuestionRuntime());
export const askUserQuestionCoordinator = askUserQuestionRuntime.coordinator;
export const askUserQuestionTool = askUserQuestionRuntime.tool;
