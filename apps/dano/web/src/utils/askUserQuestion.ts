import {
  ASK_USER_QUESTION_TOOL_NAME,
  ASK_USER_QUESTION_PRESENTATION_TERMINAL_CODE,
  ASK_USER_QUESTION_VALIDATION_TERMINAL_CODE,
  type AskUserQuestionAnswer,
  type AskUserQuestionCardItem,
  type AskUserQuestionCardRequest,
  type AskUserQuestionConfirmationCardRequest,
  type AskUserQuestionConfirmationForm,
  type AskUserQuestionOptionId,
  type AskUserQuestionResult,
} from "@dano/types/protocol";
import type { ToolContentBlock } from "./transcript";

export type NormalizedAskUserQuestionOption =
  Extract<AskUserQuestionCardItem, { kind: "single" }>["options"][number];
export type AskUserQuestionItem = AskUserQuestionCardItem;
export type AskUserQuestionRequest = AskUserQuestionCardRequest;
export type AskUserQuestionAnswerItem = {
  id: string;
  kind: AskUserQuestionItem["kind"];
  label: string;
  value: string;
};

export function askUserQuestionConfirmationForms(
  request: AskUserQuestionConfirmationCardRequest,
): AskUserQuestionConfirmationForm[] {
  return request.forms?.length
    ? request.forms
    : [
        {
          formId: request.confirmationOfToolCallId,
          title: request.title,
          questions: request.questions,
          answer: request.answer,
        },
      ];
}

export function askUserQuestionMarkdown(question: string): string {
  return question.replace(/\\+(?:r\\+n|n)/g, "\n");
}

export function askUserQuestionAnswerMarkdown(
  request: AskUserQuestionRequest,
  answer: AskUserQuestionAnswer | Record<string, AskUserQuestionAnswer>,
  labels: { confirm: string; cancel: string },
): string {
  if (!request.batch && request.kind === "confirm") {
    return groupedAnswerMarkdown(request.questions, answer, labels);
  }
  if (!request.batch) return answerValueMarkdown(request, answer, labels);
  return groupedAnswerMarkdown(request.questions, answer, labels);
}

function groupedAnswerMarkdown(
  questions: AskUserQuestionCardItem[],
  answer: AskUserQuestionAnswer | Record<string, AskUserQuestionAnswer>,
  labels: { confirm: string; cancel: string },
): string {
  if (!isAnswerRecord(answer)) return answerValueMarkdown(undefined, answer, labels);

  const used = new Set<string>();
  const lines: string[] = [];
  for (const item of questions) {
    if (!(item.id in answer)) continue;
    used.add(item.id);
    lines.push(`- ${questionLabel(item.question)}：${answerValueMarkdown(item, answer[item.id], labels)}`);
  }
  for (const [key, value] of Object.entries(answer)) {
    if (!used.has(key)) lines.push(`- ${key}：${answerValueMarkdown(undefined, value, labels)}`);
  }
  return lines.length > 0 ? `\n${lines.join("\n")}` : "";
}

export function askUserQuestionAnswerItems(
  request: AskUserQuestionRequest,
  answer: AskUserQuestionAnswer | Record<string, AskUserQuestionAnswer>,
  labels: { confirm: string; cancel: string },
): AskUserQuestionAnswerItem[] {
  if (!request.batch && request.kind !== "confirm") {
    return [{
      id: request.id,
      kind: request.kind,
      label: questionLabel(request.question),
      value: answerValueMarkdown(request, answer, labels),
    }];
  }
  if (!isAnswerRecord(answer)) return [];

  const questions = request.questions;
  return questions.flatMap(item =>
    item.id in answer
      ? [{
          id: item.id,
          kind: item.kind,
          label: questionLabel(item.question),
          value: answerValueMarkdown(item, answer[item.id], labels),
        }]
      : [],
  );
}

export function isAskUserQuestionToolError(block: ToolContentBlock): boolean {
  return (
    block.toolName === ASK_USER_QUESTION_TOOL_NAME && block.toolStatus === "error"
  );
}

export function isAskUserQuestionTerminalFailure(
  block: ToolContentBlock,
): boolean {
  return (
    isAskUserQuestionToolError(block) &&
    (block.questionState === "terminal_failure" ||
      Boolean(block.resultText?.includes(ASK_USER_QUESTION_PRESENTATION_TERMINAL_CODE)) ||
      isAskUserQuestionValidationTerminalFailure(block))
  );
}

export function isAskUserQuestionValidationTerminalFailure(
  block: ToolContentBlock,
): boolean {
  return (
    isAskUserQuestionToolError(block) &&
    Boolean(block.resultText?.includes(ASK_USER_QUESTION_VALIDATION_TERMINAL_CODE))
  );
}

export function isPendingAskUserQuestionBlock(
  block: ToolContentBlock,
): boolean {
  return (
    block.toolName === ASK_USER_QUESTION_TOOL_NAME &&
    block.toolStatus === "pending" &&
    Boolean(block.toolCallId)
  );
}

export function hideAskUserQuestionToolBlock(
  block: ToolContentBlock,
): boolean {
  return false;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function questionLabel(question: string): string {
  return askUserQuestionMarkdown(question)
    .replace(/\s+/g, " ")
    .trim()
    .replace(/[：:？?。.!！]+$/u, "");
}

function answerValueMarkdown(
  item: AskUserQuestionItem | undefined,
  answer: AskUserQuestionAnswer | Record<string, AskUserQuestionAnswer>,
  labels: { confirm: string; cancel: string },
): string {
  if (Array.isArray(answer)) {
    return answer.map(value => answerOptionLabel(item, value)).join("、");
  }
  if (typeof answer === "boolean") return answer ? labels.confirm : labels.cancel;
  if (typeof answer === "object") {
    return Object.entries(answer)
      .map(([key, value]) => `${key}: ${answerValueMarkdown(undefined, value, labels)}`)
      .join("; ");
  }
  return answerOptionLabel(item, answer);
}

function answerOptionLabel(
  item: AskUserQuestionItem | undefined,
  answer: AskUserQuestionOptionId,
): string {
  if (!item || item.kind === "text" || item.kind === "date" || item.kind === "confirm") return String(answer);
  return item.options.find(option => option.id === answer)?.label ?? String(answer);
}

export function askUserQuestionRequest(
  block: ToolContentBlock,
): AskUserQuestionRequest | null {
  return block.toolName === ASK_USER_QUESTION_TOOL_NAME && block.toolCallId
    ? block.questionRequest ?? null
    : null;
}

function isOptionId(value: unknown): value is AskUserQuestionOptionId {
  return typeof value === "string" || typeof value === "number";
}

export function askUserQuestionResult(
  details: unknown,
): AskUserQuestionResult | null {
  if (!isRecord(details)) return null;
  if (details.status === "cancelled") return { status: "cancelled" };
  if (
    details.status === "confirmed" &&
    typeof details.confirmationOfToolCallId === "string" &&
    isAnswerRecord(details.answer)
  ) {
    const forms = isConfirmedForms(details.forms)
      ? details.forms
      : [
          {
            formId: details.confirmationOfToolCallId,
            answer: details.answer,
          },
        ];
    return {
      status: "confirmed",
      confirmationOfToolCallId: details.confirmationOfToolCallId,
      answer: details.answer,
      forms,
    };
  }
  if (
    details.status === "answered" &&
    (typeof details.answer === "string" ||
      typeof details.answer === "number" ||
      typeof details.answer === "boolean" ||
      (Array.isArray(details.answer) &&
        details.answer.every(isOptionId)) ||
      isAnswerRecord(details.answer))
  ) {
    return {
      status: "answered",
      answer: details.answer,
      ...(typeof details.formId === "string" ? { formId: details.formId } : {}),
    };
  }
  return null;
}

function isConfirmedForms(
  value: unknown,
): value is Array<{
  formId: string;
  answer: Record<string, AskUserQuestionAnswer>;
}> {
  return (
    Array.isArray(value) &&
    value.length > 0 &&
    value.every(
      form =>
        isRecord(form) &&
        typeof form.formId === "string" &&
        isAnswerRecord(form.answer),
    )
  );
}

function isAnswerRecord(
  value: unknown,
): value is Record<string, AskUserQuestionAnswer> {
  if (!isRecord(value)) return false;
  return Object.values(value).every(
    answer =>
      typeof answer === "string" ||
      typeof answer === "number" ||
      typeof answer === "boolean" ||
      (Array.isArray(answer) &&
        answer.every(isOptionId)),
  );
}
