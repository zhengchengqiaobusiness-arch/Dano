import { isDeepStrictEqual } from "node:util";
import { defineTool } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { parseAskUserQuestionDateValue, validateAskUserQuestionDateFormat } from "../../types/ask-user-question-date.js";
import { DANO_DEFAULT_CONFIG } from "./dano-config.js";
import {
  askUserQuestionFailure,
  askUserQuestionIssue,
  serializeAskUserQuestionFailure,
} from "./ask-user-question-errors.js";
import {
  ASK_USER_QUESTION_CANCELLED_CODE,
  ASK_USER_QUESTION_ERROR_CATEGORIES,
  ASK_USER_QUESTION_ERROR_CODES,
  ASK_USER_QUESTION_ISSUE_CODES,
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
  type AskUserQuestionErrorIssue,
  type AskUserQuestionInputType,
  type AskUserQuestionInvalidResult,
  type AskUserQuestionLifecycleState,
  type AskUserQuestionOption,
  type AskUserQuestionOptionId,
  type AskUserQuestionResult,
} from "./types.js";

export { ASK_USER_QUESTION_CANCELLED_CODE };

const askUserQuestionAnswerSchema = Type.Union([
  Type.String(),
  Type.Number(),
  Type.Array(Type.Union([Type.String(), Type.Number()])),
  Type.Boolean(),
], {
  description:
    "Canonical answer value returned to the model: string or number id, id array, text string, or boolean confirmation.",
});

const literalUnion = (values: readonly string[]) =>
  Type.Union(values.map(value => Type.Literal(value)));

const duplicateCallMessage =
  "Another ask_user_question call is still pending in this assistant response. Retry with exactly one native ask_user_question call and combine all fields into one questions array.";

const askUserQuestionFields = {
  question: Type.Optional(
    Type.Any({
      description:
        "Single-question call: the clear, specific question to ask the user. With questions[], top-level question/title/label/prompt is treated only as optional form instruction text; each actual field question must be inside questions[].",
    }),
  ),
  title: Type.Optional(Type.Any({
    description:
      "Canonical grouped-form title. If omitted or malformed with questions, Dano uses the configured product default title.",
  })),
  label: Type.Optional(Type.Any()),
  prompt: Type.Optional(Type.Any()),
  options: Type.Optional(
    Type.Any({
      description:
        "Canonical choices array for this question. Dano also accepts a one-level JSON-stringified array and the choices alias. Strings remain supported; objects use stable id plus label. Include '其他' or 'Other' to let the user enter one custom answer. Omit for free-text, confirmation, or remote dataSource input.",
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
        "Preferred for collecting more than one answer. Make exactly one ask_user_question call with questions: [{ id, question, default, options?, multiple?, inputType?, fieldAssist?, dateFormat?, required?, dataSource? }, ...]. Every canonical non-confirmation questions[] item should include a context-based, non-empty default. A single question object or one-level JSON-stringified object/array is also accepted and normalized to an array. If title is omitted or malformed, Dano uses the configured product default. When questions is present, put each field's options, inputType, fieldAssist, dateFormat, required, dataSource, multiple, and default inside its questions[] item. Do not include top-level confirm or top-level field configuration with questions.",
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
  Type.Object({
    status: Type.Literal("invalid"),
    error: Type.Object({
      code: literalUnion(ASK_USER_QUESTION_ERROR_CODES),
      category: literalUnion(ASK_USER_QUESTION_ERROR_CATEGORIES),
      message: Type.String(),
      retryable: Type.Boolean(),
      issues: Type.Array(Type.Object({
        code: literalUnion(ASK_USER_QUESTION_ISSUE_CODES),
        path: Type.Optional(Type.String()),
        message: Type.String(),
      }), { minItems: 1 }),
      sourceCode: Type.Optional(literalUnion(ASK_USER_QUESTION_ERROR_CODES)),
      terminalCode: Type.Optional(Type.Union([
        Type.Literal(ASK_USER_QUESTION_PRESENTATION_RETRY_CODE),
        Type.Literal(ASK_USER_QUESTION_PRESENTATION_TERMINAL_CODE),
        Type.Literal(ASK_USER_QUESTION_VALIDATION_TERMINAL_CODE),
        Type.Literal(ASK_USER_QUESTION_CANCELLED_CODE),
      ])),
      context: Type.Optional(Type.Object({
        receivedShape: Type.Optional(Type.Object({
          formIds: Type.String(),
          formId: Type.String(),
        })),
        ignoredReasons: Type.Optional(Type.Array(Type.String())),
        fallbackAttempted: Type.Optional(Type.Boolean()),
      })),
    }),
  }),
]);

type PendingQuestionKind = "text" | "date" | "single" | "multiple" | "confirm";
type AskUserQuestionCompletedResult = Exclude<
  AskUserQuestionResult,
  { status: "invalid" }
>;

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
  resolve(result: AskUserQuestionCompletedResult): void;
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
    private readonly maxRetries =
      DANO_DEFAULT_CONFIG.askUserQuestion.maxRetries,
    private readonly defaultTitle =
      DANO_DEFAULT_CONFIG.askUserQuestion.defaultTitle,
  ) {}

  wait(
    toolCallId: string,
    rawRequest: AskUserQuestionRequestParams,
    signal: AbortSignal | undefined,
  ): Promise<AskUserQuestionCompletedResult> {
    if (this.pending.has(toolCallId)) {
      logQuestionLifecycle(toolCallId, "invalid");
      return rejectAskUserQuestionFailure(
        duplicateQuestionCallFailure("This ask_user_question call is already pending."),
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
      : normalizeAskUserQuestionRequest(rawRequest, this.defaultTitle);
    if ("error" in normalized) {
      return this.rejectValidation(toolCallId, normalized.error, signal);
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
      return rejectAskUserQuestionFailure(
        duplicateQuestionCallFailure(duplicateCallMessage),
      );
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
        reject(askUserQuestionFailureError(questionCancelledFailure()));
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
          reject(askUserQuestionFailureError(
            presentationFailure(terminal),
          ));
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
  ): AskUserQuestionCompletedResult {
    const pending = this.pending.get(toolCallId);
    if (!pending) throw new Error(`Pending question not found: ${toolCallId}`);
    if (pending.state === "awaiting_presentation") {
      this.present(toolCallId);
    }

    let result: AskUserQuestionCompletedResult;
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
    this.rejectAll(askUserQuestionFailureError(questionCancelledFailure()));
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
    failure: AskUserQuestionInvalidResult,
    signal: AbortSignal | undefined,
  ): Promise<never> {
    const failures = signal
      ? (this.validationFailuresBySignal.get(signal) ?? 0) + 1
      : this.maxRetries + 1;
    if (signal) this.validationFailuresBySignal.set(signal, failures);
    const terminal = failures > this.maxRetries;
    logQuestionLifecycle(toolCallId, terminal ? "terminal_failure" : "invalid");
    return rejectAskUserQuestionFailure(
      terminal ? terminalValidationFailure(failure) : failure,
    );
  }
}

function rejectAskUserQuestionFailure(
  failure: AskUserQuestionInvalidResult,
): Promise<never> {
  return Promise.reject(askUserQuestionFailureError(failure));
}

function askUserQuestionFailureError(
  failure: AskUserQuestionInvalidResult,
): Error {
  return new Error(serializeAskUserQuestionFailure(failure));
}

function duplicateQuestionCallFailure(message: string): AskUserQuestionInvalidResult {
  return askUserQuestionFailure(
    "duplicate_question_call",
    "duplicate_call",
    message,
    true,
    [askUserQuestionIssue("duplicate_tool_call", message)],
  );
}

function questionCancelledFailure(): AskUserQuestionInvalidResult {
  return askUserQuestionFailure(
    "question_cancelled",
    "lifecycle",
    "The question flow was cancelled.",
    false,
    [askUserQuestionIssue("cancelled", "Question was aborted or the coordinator was disposed.")],
    { terminalCode: ASK_USER_QUESTION_CANCELLED_CODE },
  );
}

function presentationFailure(terminal: boolean): AskUserQuestionInvalidResult {
  return askUserQuestionFailure(
    terminal ? "question_presentation_failed" : "question_presentation_timeout",
    "lifecycle",
    terminal
      ? "Dano could not display the question card after bounded retries."
      : "The accepted question card was not presented in time.",
    !terminal,
    [askUserQuestionIssue(
      terminal ? "presentation_failed" : "presentation_timeout",
      terminal
        ? "Stop this response and let the user retry."
        : "Retry with one corrected native ask_user_question call.",
    )],
    {
      terminalCode: terminal
        ? ASK_USER_QUESTION_PRESENTATION_TERMINAL_CODE
        : ASK_USER_QUESTION_PRESENTATION_RETRY_CODE,
    },
  );
}

function terminalValidationFailure(
  source: AskUserQuestionInvalidResult,
): AskUserQuestionInvalidResult {
  return askUserQuestionFailure(
    "question_validation_failed",
    "lifecycle",
    "Repeated invalid ask_user_question calls exhausted automatic retries.",
    false,
    [
      askUserQuestionIssue(
        "validation_retry_exhausted",
        "Stop this response and let the user retry.",
      ),
      ...source.error.issues,
    ],
    {
      terminalCode: ASK_USER_QUESTION_VALIDATION_TERMINAL_CODE,
      sourceCode: source.error.code,
      ...(source.error.context ? { context: source.error.context } : {}),
    },
  );
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
  | {
      request: NormalizedAskUserQuestionRequest;
      issues: AskUserQuestionErrorIssue[];
    }
  | { error: AskUserQuestionInvalidResult };

type CompatibleQuestionsResult =
  | {
      questions: NormalizedAskUserQuestionRequestItem[];
      issues: AskUserQuestionErrorIssue[];
    }
  | { error: AskUserQuestionInvalidResult };

type CompatibleQuestionResult = {
  question: NormalizedAskUserQuestionRequestItem;
  issues: AskUserQuestionErrorIssue[];
};

function invalidQuestionArguments(
  issues: AskUserQuestionErrorIssue[],
): AskUserQuestionInvalidResult {
  return askUserQuestionFailure(
    "invalid_question_arguments",
    "validation",
    "Question fields contain invalid arguments.",
    true,
    issues,
  );
}

function normalizeCompatibleRequest(
  request: AskUserQuestionRequestParams,
  defaultTitle: string,
): CompatibleRequestResult {
  const rawQuestions = request.questions;
  const questionResult = normalizeCompatibleQuestion(
    request,
    rawQuestions === undefined,
    "",
  );
  const normalized: NormalizedAskUserQuestionRequest = questionResult.question;
  const issues = [...questionResult.issues];
  if (rawQuestions !== undefined) {
    normalized.title = firstScalarString(request.title) ?? defaultTitle;
    normalized.question = firstScalarString(request.question, request.label, request.prompt);
    if (!normalized.question) delete normalized.question;
    const questionsResult = normalizeCompatibleQuestions(rawQuestions);
    if ("error" in questionsResult) return questionsResult;
    normalized.questions = questionsResult.questions;
    issues.push(...questionsResult.issues);
    return { request: foldCompatibleGroupedFields(normalized), issues };
  }
  return { request: normalized, issues };
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
        error: invalidQuestionArguments([
          askUserQuestionIssue(
            "invalid_questions_json",
            "questions must be valid JSON containing one object or an array of objects.",
            "questions",
          ),
        ]),
      };
    }
  }
  if (!Array.isArray(parsed) && !isPlainRecord(parsed)) {
    return { error: invalidQuestionArguments([
      askUserQuestionIssue(
        "invalid_questions_shape",
        "questions must be one object or an array of objects.",
        "questions",
      ),
    ]) };
  }
  const rawItems = Array.isArray(parsed) ? parsed : [parsed];
  const questions: NormalizedAskUserQuestionRequestItem[] = [];
  const issues: AskUserQuestionErrorIssue[] = [];
  for (const [index, value] of rawItems.entries()) {
    const path = `questions[${index}]`;
    if (!isPlainRecord(value)) {
      issues.push(askUserQuestionIssue(
        "invalid_question_item",
        "Each questions item must be an object.",
        path,
      ));
      continue;
    }
    const result = normalizeCompatibleQuestion(value, true, path);
    questions.push(result.question);
    issues.push(...result.issues);
  }
  return { questions, issues };
}

function normalizeCompatibleQuestion(
  request: AskUserQuestionRequestItem | Record<string, unknown>,
  includeTitleAsQuestion = true,
  path = "",
): CompatibleQuestionResult {
  const normalized: NormalizedAskUserQuestionRequestItem = {};
  const issues: AskUserQuestionErrorIssue[] = [];
  const id = normalizeCompatibleAliases(
    "question id",
    [request.id, request.key, request.name],
    firstScalarString,
  );
  if ("error" in id) {
    issues.push(aliasConflictIssue(pathFor(path, "id"), id.error));
  } else if (id.value) normalized.id = id.value;

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
  if ("error" in question) {
    issues.push(aliasConflictIssue(pathFor(path, "question"), question.error));
  } else if (question.value) normalized.question = question.value;

  const inputType = normalizeCompatibleAliases(
    "input type",
    [request.inputType, request.input_type, request.type, request.component],
    normalizeInputType,
  );
  if ("error" in inputType) {
    issues.push(aliasConflictIssue(pathFor(path, "inputType"), inputType.error));
  } else if (inputType.value) normalized.inputType = inputType.value;
  const normalizedInputType = "error" in inputType ? undefined : inputType.value;
  const inputTypeProvided = [
    request.inputType,
    request.input_type,
    request.type,
    request.component,
  ].some(value => value !== undefined);
  if (!("error" in inputType) && inputTypeProvided && !normalizedInputType) {
    issues.push(askUserQuestionIssue(
      "invalid_input_type",
      "inputType must identify a supported question control.",
      pathFor(path, "inputType"),
    ));
  }

  const options = normalizeCompatibleAliases(
    "options",
    [request.options, request.choices],
    normalizeCompatibleOptionsAlias,
  );
  if ("error" in options) {
    issues.push(aliasConflictIssue(pathFor(path, "options"), options.error));
  }
  const optionsProvided = request.options !== undefined || request.choices !== undefined;
  if (!("error" in options) && options.value) {
    normalized.options = options.value;
  } else if (
    !("error" in options) &&
    optionsProvided &&
    normalizedInputType !== "text" &&
    normalizedInputType !== "textarea" &&
    normalizedInputType !== "date" &&
    normalizedInputType !== "confirm"
  ) {
    normalized.inputType = normalizedInputType ?? "radio";
  }
  if (
    optionsProvided &&
    !("error" in options) &&
    !options.value &&
    normalized.inputType !== "text" &&
    normalized.inputType !== "textarea" &&
    normalized.inputType !== "date" &&
    normalized.inputType !== "confirm"
  ) {
    issues.push(...invalidOptionsIssues(
      request.options !== undefined ? request.options : request.choices,
      pathFor(path, "options"),
    ));
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
  if ("error" in dataSource) {
    issues.push(aliasConflictIssue(pathFor(path, "dataSource"), dataSource.error));
  }
  const dataSourceProvided =
    request.dataSource !== undefined || request.data_source !== undefined;
  if (
    dataSourceProvided &&
    !("error" in dataSource) &&
    !dataSource.value &&
    (normalizedInputType === "select" || normalizedInputType === "treeSelect")
  ) {
    issues.push(askUserQuestionIssue(
        "invalid_data_source",
        "dataSource must define an api type and a non-empty endpoint.",
        pathFor(path, "dataSource"),
      ));
  }
  if (!("error" in dataSource) && dataSource.value) {
    normalized.dataSource = dataSource.value;
  }
  if (
    !("error" in dataSource) && dataSource.value &&
    !normalizedInputType
  ) normalized.inputType = "select";

  const multiple = normalizeCompatibleAliases(
    "multiple",
    [request.multiple, request.multi, request.multipleSelect],
    normalizeBoolean,
  );
  if ("error" in multiple) {
    issues.push(aliasConflictIssue(pathFor(path, "multiple"), multiple.error));
  } else if (multiple.value !== undefined) normalized.multiple = multiple.value;

  const required = normalizeBoolean(request.required);
  if (required !== undefined) normalized.required = required;

  if (
    normalizeBoolean(request.confirm) === true ||
    normalizedInputType === "confirm"
  ) {
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
  if ("error" in compatibleDefault) {
    issues.push(aliasConflictIssue(pathFor(path, "default"), compatibleDefault.error));
  } else if (compatibleDefault.value !== undefined) {
    normalized.default = compatibleDefault.value;
  } else {
    const invalidDefault = firstNormalizedValue(
      defaultValues,
      normalizeCompatibleAnswerInput,
    );
    if (invalidDefault !== undefined) {
      normalized.default = invalidDefault;
    } else if (defaultValues.some(value => value !== undefined && value !== null)) {
      issues.push(askUserQuestionIssue(
          "invalid_default",
          "default must match the selected question control.",
          pathFor(path, "default"),
        ));
    }
  }

  return { question: normalized, issues };
}

function pathFor(base: string, field: string): string {
  return base ? `${base}.${field}` : field;
}

function aliasConflictIssue(
  path: string,
  message: string,
): AskUserQuestionErrorIssue {
  return askUserQuestionIssue("conflicting_aliases", message, path);
}

function invalidOptionsIssues(
  value: unknown,
  path: string,
): AskUserQuestionErrorIssue[] {
  const parsed = parseJsonString(value);
  if (!Array.isArray(parsed) || parsed.length === 0) {
    return [askUserQuestionIssue(
      "invalid_options",
      "options must be a non-empty array of valid, unambiguous choices.",
      path,
    )];
  }
  const issues = parsed.flatMap((option, index) =>
    normalizeCompatibleOption(option) === undefined
      ? [askUserQuestionIssue(
          "invalid_options",
          "Each option must have a non-empty, unambiguous id and label.",
          `${path}[${index}]`,
        )]
      : [],
  );
  return issues.length > 0
    ? issues
    : [askUserQuestionIssue(
        "invalid_options",
        "options must be a non-empty array of valid, unambiguous choices.",
        path,
      )];
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
): { forms: SubmittedForm[] } | { error: AskUserQuestionInvalidResult } {
  const selection = selectAskUserQuestionConfirmationTargets(
    rawRequest,
    availableForms,
  );
  if (selection.targets.length > 0) {
    return { forms: selection.targets };
  }

  return {
    error: invalidConfirmationSourceFailure(
      selection.receivedShape,
      selection.ignoredReasons,
    ),
  };
}

function invalidConfirmationSourceFailure(
  receivedShape: { formIds: string; formId: string } = {
    formIds: "omitted",
    formId: "omitted",
  },
  ignoredReasons: string[] = [],
): AskUserQuestionInvalidResult {
  const issues: AskUserQuestionErrorIssue[] = [];
  if (receivedShape.formIds !== "omitted") {
    issues.push(askUserQuestionIssue(
      "invalid_confirmation_target",
      confirmationTargetMessage(receivedShape.formIds, ignoredReasons),
      "formIds",
    ));
  }
  if (receivedShape.formId !== "omitted") {
    issues.push(askUserQuestionIssue(
      "invalid_confirmation_target",
      confirmationTargetMessage(receivedShape.formId, ignoredReasons),
      "formId",
    ));
  }
  if (issues.length === 0) {
    issues.push(askUserQuestionIssue(
      "invalid_confirmation_target",
      "No submitted grouped form target was provided.",
      "formIds",
    ));
  }
  return askUserQuestionFailure(
    "invalid_confirmation_source",
    "confirmation",
    "Confirmation requires an available submitted grouped form.",
    true,
    issues,
    {
      context: {
        receivedShape,
        ignoredReasons,
        fallbackAttempted: true,
      },
    },
  );
}

function confirmationTargetMessage(
  shape: string,
  ignoredReasons: string[],
): string {
  if (ignoredReasons.includes("unavailable_form_id")) {
    return `The ${shape} confirmation target did not identify an available submitted form.`;
  }
  if (ignoredReasons.some(reason => reason.startsWith("malformed_"))) {
    return `The ${shape} confirmation target has an unsupported shape.`;
  }
  return `The ${shape} confirmation target did not identify a submitted form.`;
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
    const parsed = parseJsonString(value);
    if (Array.isArray(parsed)) {
      candidates.push(...parsed);
      return;
    }
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
  const parsed = parseJsonString(value);
  if (!Array.isArray(parsed)) {
    return { values: [], invalid: value !== undefined };
  }
  const values = parsed.flatMap(option => {
    const normalized = normalizeCompatibleOption(option);
    return normalized === undefined ? [] : [normalized];
  });
  return { values, invalid: values.length !== parsed.length };
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

type NormalizedQuestionsResult =
  | { questions: PendingQuestionItem[] }
  | { issues: AskUserQuestionErrorIssue[] };

type NormalizedQuestionResult =
  | { question: PendingQuestionItem }
  | { issues: AskUserQuestionErrorIssue[] };

function normalizeRequestQuestions(
  request: NormalizedAskUserQuestionRequest,
  requireDefault: boolean,
): NormalizedQuestionsResult {
  if (request.questions !== undefined) {
    const questions: PendingQuestionItem[] = [];
    const issues: AskUserQuestionErrorIssue[] = [];
    const idIndexes = new Map<string, number[]>();
    for (let index = 0; index < request.questions.length; index += 1) {
      const question = request.questions[index];
      const path = `questions[${index}]`;
      if (!question.id?.trim()) {
        issues.push(askUserQuestionIssue(
          "missing_question_id",
          "Grouped question field id is required.",
          `${path}.id`,
        ));
      } else {
        const id = question.id.trim();
        const indexes = idIndexes.get(id) ?? [];
        indexes.push(index);
        idIndexes.set(id, indexes);
      }
      const normalized = normalizeQuestion(
        question,
        `q${index + 1}`,
        requireDefault,
        path,
      );
      if ("issues" in normalized) {
        issues.push(...normalized.issues);
      } else {
        questions.push(normalized.question);
      }
    }
    for (const indexes of idIndexes.values()) {
      if (indexes.length < 2) continue;
      for (const index of indexes) {
        issues.push(askUserQuestionIssue(
          "duplicate_question_id",
          "Grouped question field id conflicts with another field.",
          `questions[${index}].id`,
        ));
      }
    }
    return issues.length > 0 ? { issues } : { questions };
  }

  if (!request.question?.trim()) {
    return { issues: [askUserQuestionIssue(
      "missing_question_text",
      "Question text is required.",
      "question",
    )] };
  }
  const question = normalizeQuestion(
    { ...request, id: "answer", question: request.question },
    "answer",
    requireDefault,
    "",
  );
  return "issues" in question ? question : { questions: [question.question] };
}

export function normalizeAskUserQuestionCardRequest(
  rawRequest: unknown,
  options: {
    defaultTitle?: string;
    requireDefault?: boolean;
  } = {},
): AskUserQuestionCardRequest | null {
  const normalized = normalizeAskUserQuestionRequest(
    rawRequest,
    options.defaultTitle ?? DANO_DEFAULT_CONFIG.askUserQuestion.defaultTitle,
    options.requireDefault,
  );
  return "error" in normalized ? null : normalized.cardRequest;
}

export function normalizeAskUserQuestionCardRequestResult(
  rawRequest: unknown,
  options: {
    defaultTitle?: string;
    requireDefault?: boolean;
  } = {},
):
  | { request: AskUserQuestionCardRequest }
  | { error: AskUserQuestionInvalidResult } {
  const normalized = normalizeAskUserQuestionRequest(
    rawRequest,
    options.defaultTitle ?? DANO_DEFAULT_CONFIG.askUserQuestion.defaultTitle,
    options.requireDefault,
  );
  return "error" in normalized
    ? normalized
    : { request: normalized.cardRequest };
}

function normalizeAskUserQuestionRequest(
  rawRequest: unknown,
  defaultTitle: string,
  requireDefault = false,
):
  | {
      request: NormalizedAskUserQuestionRequest;
      questions: PendingQuestionItem[];
      cardRequest: AskUserQuestionCardRequest;
    }
  | { error: AskUserQuestionInvalidResult } {
  const parsed = parseJsonString(rawRequest);
  if (!isPlainRecord(parsed)) {
    return { error: invalidQuestionArguments([
      askUserQuestionIssue(
        "invalid_request_shape",
        "ask_user_question arguments must be an object.",
      ),
    ]) };
  }
  const compatible = normalizeCompatibleRequest(
    parsed as AskUserQuestionRequestParams,
    defaultTitle,
  );
  if ("error" in compatible) return compatible;
  const { request, issues: compatibilityIssues } = compatible;
  if (request.confirm && request.questions === undefined) {
    return { error: invalidConfirmationSourceFailure() };
  }
  const questionsResult = normalizeRequestQuestions(request, requireDefault);
  if ("issues" in questionsResult) {
    return {
      error: invalidQuestionArguments([
        ...compatibilityIssues,
        ...questionsResult.issues,
      ]),
    };
  }
  if (compatibilityIssues.length > 0) {
    return { error: invalidQuestionArguments(compatibilityIssues) };
  }
  const { questions } = questionsResult;
  if (questions.length === 0) {
    return { error: invalidQuestionArguments([
      askUserQuestionIssue(
        "missing_question_text",
        "At least one question is required.",
        "questions",
      ),
    ]) };
  }
  if (
    request.confirm &&
    (request.options || request.multiple || request.questions || request.dataSource)
  ) {
    return {
      error: invalidQuestionArguments([
        askUserQuestionIssue(
          "invalid_options",
          "Confirmation questions cannot provide options, multiple selection, grouped fields, or dataSource.",
        ),
      ]),
    };
  }
  const cardItems = questions.map(toQuestionCardItem);
  const singleCard = cardItems[0];
  if (request.questions === undefined && singleCard.kind === "confirm") {
    return { error: invalidConfirmationSourceFailure() };
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
  requireDefault = false,
  path = "",
): NormalizedQuestionResult {
  const issues: AskUserQuestionErrorIssue[] = [];
  const questionText = request.question?.trim() ?? "";
  if (!questionText) {
    issues.push(askUserQuestionIssue(
      "missing_question_text",
      "Question text is required.",
      pathFor(path, "question"),
    ));
  }
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
    if (error) {
      issues.push(askUserQuestionIssue(
        "invalid_date_format",
        safeDateFormatErrorMessage(error),
        pathFor(path, "dateFormat"),
      ));
    }
    if (
      !error &&
      typeof request.default === "string" &&
      request.default.trim() &&
      dateFormat &&
      !parseAskUserQuestionDateValue(request.default, dateFormat)
    ) {
      issues.push(askUserQuestionIssue(
        "invalid_default",
        "The default date must match dateFormat.",
        pathFor(path, "default"),
      ));
    }
  }
  const rawOptions = compatibleOptions?.map(normalizeOption);
  const optionIssues: AskUserQuestionErrorIssue[] = [];
  rawOptions?.forEach((option, index) => {
    if (!option) {
      optionIssues.push(askUserQuestionIssue(
        "invalid_options",
        "Each option must have a non-empty id and label.",
        `${pathFor(path, "options")}[${index}]`,
      ));
    }
  });
  issues.push(...optionIssues);
  const options = rawOptions?.filter(
    (option): option is PendingQuestionOption => option !== null,
  );
  const optionIndexes = new Map<string, number[]>();
  options?.forEach((option, index) => {
    const key = optionKey(option.id);
    const indexes = optionIndexes.get(key) ?? [];
    indexes.push(index);
    optionIndexes.set(key, indexes);
  });
  const duplicateOptionIssues = [...optionIndexes.values()].flatMap(indexes =>
    indexes.length < 2
      ? []
      : indexes.map(index => askUserQuestionIssue(
          "duplicate_option_id",
          "Option id conflicts with another option.",
          `${pathFor(path, "options")}[${index}]`,
        )),
  );
  issues.push(...duplicateOptionIssues);
  if (
    multiple &&
    !options &&
    !compatibleDataSource
  ) {
    issues.push(askUserQuestionIssue(
      "missing_choice_source",
      "Multiple-choice questions require options or dataSource.",
      path || "question",
    ));
  }
  if (
    !multiple &&
    (inputType === "radio" || inputType === "select" || inputType === "treeSelect") &&
    !options &&
    !compatibleDataSource
  ) {
    issues.push(askUserQuestionIssue(
      "missing_choice_source",
      "Choice questions require options or dataSource.",
      path || "question",
    ));
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
    question: questionText,
    inputType,
    ...(kind === "text"
      ? { fieldAssist: request.fieldAssist ?? inputType === "textarea" }
      : {}),
    required,
    ...(options ? { options } : {}),
    ...(compatibleDataSource ? { dataSource: compatibleDataSource } : {}),
    ...(dateFormat ? { dateFormat } : {}),
  };
  if (requireDefault && kind !== "confirm" && request.default === undefined) {
    issues.push(askUserQuestionIssue(
      "invalid_default",
      "Every non-confirmation question must provide a non-empty default.",
      pathFor(path, "default"),
    ));
  }
  if (request.default !== undefined) {
    const compatibleDefault =
      kind === "text" &&
      (typeof request.default === "number" || typeof request.default === "boolean")
        ? String(request.default)
        : request.default;
    if (typeof compatibleDefault === "string" && !compatibleDefault.trim()) {
      issues.push(askUserQuestionIssue(
        "invalid_default",
        "default must be a non-empty recommended value.",
        pathFor(path, "default"),
      ));
    } else try {
      question.default = normalizeAnswer(question, compatibleDefault);
    } catch (cause) {
      issues.push(askUserQuestionIssue(
        "invalid_default",
        cause instanceof AskUserQuestionAnswerValidationError
          ? `default is invalid: ${cause.message}`
          : "default is invalid.",
        pathFor(path, "default"),
      ));
    }
  }
  return issues.length > 0 ? { issues } : { question };
}

function safeDateFormatErrorMessage(message: string): string {
  return message.startsWith("dateFormat is not supported:")
    ? "dateFormat is not supported by the date control. Use yyyy-MM-dd or yyyy-MM-dd HH:mm."
    : message;
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

const answerValidationMessages = {
  confirmation_required: "请确认或取消",
  selection_required: "请至少选择一个选项",
  duplicate_selection: "不能重复选择同一选项",
  multiple_custom_answers: "只能填写一个其他回答",
  invalid_choice: "请选择一个有效选项",
  custom_answer_required: "请输入其他回答",
  ambiguous_option_id: "选项不唯一，请重新选择",
  ambiguous_option_label: "选项标签不唯一，请重新选择",
  unmatched_choice: "答案必须匹配一个可选项",
  date_string_required: "日期答案必须是字符串",
  answer_required: "答案不能为空",
} as const;

type AskUserQuestionAnswerValidationCode = keyof typeof answerValidationMessages;

class AskUserQuestionAnswerValidationError extends Error {
  constructor(readonly code: AskUserQuestionAnswerValidationCode) {
    super(answerValidationMessages[code]);
  }
}

function rejectAnswer(code: AskUserQuestionAnswerValidationCode): never {
  throw new AskUserQuestionAnswerValidationError(code);
}

function normalizeAnswer(
  pending: PendingQuestionItem,
  answer: AskUserQuestionAnswerInput,
): AskUserQuestionAnswer {
  if (pending.kind === "confirm") {
    if (typeof answer !== "boolean") {
      rejectAnswer("confirmation_required");
    }
    return answer;
  }
  if (pending.kind === "multiple") {
    if (!Array.isArray(answer) || answer.length === 0) {
      if (!pending.required && Array.isArray(answer)) return [];
      rejectAnswer("selection_required");
    }
    const normalized = answer.map(value => normalizeChoiceAnswer(pending, value));
    const keys = normalized.map(optionKey);
    if (new Set(keys).size !== keys.length) rejectAnswer("duplicate_selection");
    const customAnswers = normalized.filter(value => !hasExactOption(pending, value));
    if (customAnswers.length > 1 && pending.options?.some(isOtherOption)) {
      rejectAnswer("multiple_custom_answers");
    }
    return normalized;
  }

  if (pending.kind === "single") {
    if (!pending.required && typeof answer === "string" && !answer.trim()) return "";
    return normalizeChoiceAnswer(pending, answer);
  }

  if (pending.kind === "date") {
    if (typeof answer !== "string") rejectAnswer("date_string_required");
    if (pending.required && !answer.trim()) rejectAnswer("answer_required");
    return answer;
  }

  if (typeof answer !== "string") {
    rejectAnswer("answer_required");
  }
  if (!answer.trim()) {
    if (!pending.required) return "";
    rejectAnswer("answer_required");
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
  if (!isValidOptionId(candidate)) rejectAnswer("invalid_choice");

  const options = pending.options ?? [];
  if (options.length === 0) return candidate;

  const exact = options.find(option => option.id === candidate);
  if (exact) {
    if (isOtherOption(exact)) rejectAnswer("custom_answer_required");
    return exact.id;
  }

  const byStringifiedId = options.filter(
    option => String(option.id) === String(candidate),
  );
  if (byStringifiedId.length === 1) {
    if (isOtherOption(byStringifiedId[0])) rejectAnswer("custom_answer_required");
    return byStringifiedId[0].id;
  }
  if (byStringifiedId.length > 1) rejectAnswer("ambiguous_option_id");

  if (typeof candidate === "string") {
    const byTypedKey = options.filter(option => optionKey(option.id) === candidate);
    if (byTypedKey.length === 1) {
      if (isOtherOption(byTypedKey[0])) rejectAnswer("custom_answer_required");
      return byTypedKey[0].id;
    }

    const byLabel = options.filter(option => option.label === candidate);
    if (byLabel.length === 1) {
      if (isOtherOption(byLabel[0])) rejectAnswer("custom_answer_required");
      return byLabel[0].id;
    }
    if (byLabel.length > 1) rejectAnswer("ambiguous_option_label");
    if (options.some(isOtherOption)) return candidate;
  }

  rejectAnswer("unmatched_choice");
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

For a single question, use top-level question/options/inputType/fieldAssist/dateFormat/required/dataSource/multiple/default. For multiple questions, use title plus questions[]. fieldAssist controls generation and polishing actions for text fields; it defaults to false for single-line text and true for textarea. Dates require inputType:"date" plus dateFormat, for example "yyyy-MM-dd" or "yyyy-MM-dd HH:mm"; Dano returns the user's submitted date value as-is. required defaults to false; set required:true when an empty answer must not be submitted. Canonical calls should provide a non-empty default; compatibility input without one renders without a prefill. Use inputType:"select" or inputType:"treeSelect" with dataSource for remote API-backed choices. Dano normalizes unambiguous aliases, safe scalar deviations, and one-level JSON-stringified collections; it uses the configured product title when a grouped title is missing, ignores unknown or inapplicable optional fields, and rejects only inputs that cannot preserve rendering, submission, or answer mapping. When the workflow needs final confirmation for submitted grouped forms, call {"confirm":true,"formIds":["<formId>"]} with the formId values returned by those submissions. This is only for grouped-form confirmation; use a normal single-choice question to confirm an ordinary sentence or operation. If final confirmation is not needed, continue without this call.

Failures use one JSON result shape: {"status":"invalid","error":{"code":"...","category":"...","message":"...","retryable":true,"issues":[{"code":"...","path":"questions[0].id","message":"..."}]}}. Correct every reported issue path in one replacement call only when retryable is true. Never retry terminal or cancelled failures.`,
  promptSnippet:
    "Ask the user one native question card; for several fields use one questions array with one submit button",
  promptGuidelines: [
    "Use ask_user_question whenever you need user input to continue; do not ask the question only in assistant text.",
    "When the user asks to fill in a form, complete a form, or provide form fields, collect the fields with ask_user_question.",
    "Call ask_user_question at most once per assistant response. If you need several answers, put every item in one questions array.",
    "If the user cancels ask_user_question, stop the current workflow. Do not ask again or retry unless the user sends a new message explicitly requesting it.",
    "Invoke ask_user_question as a native tool call. Never print, describe, or wrap a tool call in <question> tags, XML, JSON, Markdown, or other assistant text.",
    "If ask_user_question returns status:invalid, inspect error.code, category, retryable, and every issues[] entry. Retry silently with one corrected native tool call only when retryable is true; correct all reported paths together and do not explain the correction to the user.",
    "Do not retry question_presentation_failed, question_validation_failed, or question_cancelled results. Stop the current response and let the user decide whether to try again.",
    "Use the documented canonical parameters. Dano treats model-generated arguments as best-effort input, normalizes safe aliases and one-level JSON collection strings, uses the configured product title when a grouped title is missing, and admits an omitted default without a prefill. It still rejects ambiguity that could change rendering, submission, or answer mapping.",
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

export interface AskUserQuestionRuntimeOptions {
  maxRetries?: number;
  defaultTitle?: string;
}

export function createAskUserQuestionRuntime(
  options: AskUserQuestionRuntimeOptions = {},
): AskUserQuestionRuntime {
  const coordinator = new AskUserQuestionCoordinator(
    5_000,
    options.maxRetries ?? DANO_DEFAULT_CONFIG.askUserQuestion.maxRetries,
    options.defaultTitle ?? DANO_DEFAULT_CONFIG.askUserQuestion.defaultTitle,
  );
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
