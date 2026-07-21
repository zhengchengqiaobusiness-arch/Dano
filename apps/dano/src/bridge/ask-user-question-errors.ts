import type {
  AskUserQuestionErrorCategory,
  AskUserQuestionErrorCode,
  AskUserQuestionErrorIssue,
  AskUserQuestionErrorProjection,
  AskUserQuestionInvalidResult,
  AskUserQuestionIssueCode,
} from "./types.js";

export function askUserQuestionIssue(
  code: AskUserQuestionIssueCode,
  message: string,
  path?: string,
): AskUserQuestionErrorIssue {
  return { code, ...(path ? { path } : {}), message };
}

export function askUserQuestionFailure(
  code: AskUserQuestionErrorCode,
  category: AskUserQuestionErrorCategory,
  message: string,
  retryable: boolean,
  issues: AskUserQuestionErrorIssue[],
  metadata: Pick<
    AskUserQuestionInvalidResult["error"],
    "terminalCode" | "sourceCode" | "context"
  > = {},
): AskUserQuestionInvalidResult {
  return {
    status: "invalid",
    error: { code, category, message, retryable, issues, ...metadata },
  };
}

export function serializeAskUserQuestionFailure(
  result: AskUserQuestionInvalidResult,
): string {
  return JSON.stringify(result);
}

export function parseAskUserQuestionFailure(
  value: unknown,
): AskUserQuestionInvalidResult | undefined {
  if (isAskUserQuestionInvalidResult(value)) return value;
  if (typeof value !== "string") return undefined;

  for (const line of value.split("\n")) {
    const candidate = line.trim().replace(/^Error:\s*/, "");
    if (!candidate.startsWith("{")) continue;
    try {
      const parsed = JSON.parse(candidate) as unknown;
      if (isAskUserQuestionInvalidResult(parsed)) return parsed;
    } catch {
      // Ignore non-JSON tool error text and retain legacy recovery below.
    }
  }
  return undefined;
}

export function projectAskUserQuestionFailure(
  result: AskUserQuestionInvalidResult,
): AskUserQuestionErrorProjection {
  const { code, category, retryable } = result.error;
  return {
    code,
    category,
    message: browserFailureMessage(code),
    retryable,
  };
}

function browserFailureMessage(
  code: AskUserQuestionErrorCode,
): string {
  switch (code) {
    case "invalid_question_arguments":
      return "Question fields contain invalid arguments.";
    case "invalid_confirmation_source":
      return "Confirmation requires a submitted grouped form.";
    case "duplicate_question_call":
      return "Only one question card can be pending at a time.";
    case "question_presentation_timeout":
      return "The question card was not presented in time.";
    case "question_presentation_failed":
      return "Dano could not display the question card.";
    case "question_validation_failed":
      return "The question call remained invalid after retrying.";
    case "question_cancelled":
      return "The question flow was cancelled.";
  }
}

function isAskUserQuestionInvalidResult(
  value: unknown,
): value is AskUserQuestionInvalidResult {
  if (!isRecord(value) || value.status !== "invalid" || !isRecord(value.error)) {
    return false;
  }
  const error = value.error;
  return (
    isAskUserQuestionErrorCode(error.code) &&
    isAskUserQuestionErrorCategory(error.category) &&
    typeof error.message === "string" &&
    typeof error.retryable === "boolean" &&
    Array.isArray(error.issues) &&
    error.issues.every(issue =>
      isRecord(issue) &&
      typeof issue.code === "string" &&
      typeof issue.message === "string" &&
      (issue.path === undefined || typeof issue.path === "string"),
    )
  );
}

function isAskUserQuestionErrorCode(
  value: unknown,
): value is AskUserQuestionErrorCode {
  return typeof value === "string" && [
    "invalid_question_arguments",
    "invalid_confirmation_source",
    "duplicate_question_call",
    "question_presentation_timeout",
    "question_presentation_failed",
    "question_validation_failed",
    "question_cancelled",
  ].includes(value);
}

function isAskUserQuestionErrorCategory(
  value: unknown,
): value is AskUserQuestionErrorCategory {
  return typeof value === "string" && [
    "validation",
    "confirmation",
    "duplicate_call",
    "lifecycle",
  ].includes(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
