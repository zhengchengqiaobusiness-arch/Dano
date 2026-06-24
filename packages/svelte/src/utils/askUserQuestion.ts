import {
  ASK_USER_QUESTION_TOOL_NAME,
  type AskUserQuestionAnswer,
  type AskUserQuestionResult,
} from "@dano/bridge/types";
import type { ToolContentBlock } from "./transcript";

export type AskUserQuestionItem =
  | { id: string; kind: "text"; question: string; default?: string }
  | {
      id: string;
      kind: "single";
      question: string;
      options: string[];
      default?: string;
    }
  | {
      id: string;
      kind: "multiple";
      question: string;
      options: string[];
      default?: string[];
    }
  | { id: string; kind: "confirm"; question: string; default?: boolean };

export type AskUserQuestionRequest =
  | (AskUserQuestionItem & { batch: false })
  | { batch: true; questions: AskUserQuestionItem[] };

export function askUserQuestionMarkdown(question: string): string {
  return question.replace(/\\+(?:r\\+n|n)/g, "\n");
}

export function hideAskUserQuestionToolBlock(block: ToolContentBlock): boolean {
  return (
    block.toolName === ASK_USER_QUESTION_TOOL_NAME && block.toolStatus === "error"
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function askUserQuestionRequest(
  block: ToolContentBlock,
): AskUserQuestionRequest | null {
  if (
    block.toolName !== ASK_USER_QUESTION_TOOL_NAME ||
    !block.toolCallId ||
    !isRecord(block.toolArgs)
  ) {
    return null;
  }

  const rawQuestions = block.toolArgs.questions;
  if (rawQuestions !== undefined) {
    if (!Array.isArray(rawQuestions) || rawQuestions.length === 0) return null;
    const seenIds = new Set<string>();
    const questions: AskUserQuestionItem[] = [];
    for (let index = 0; index < rawQuestions.length; index += 1) {
      const rawQuestion = rawQuestions[index];
      if (!isRecord(rawQuestion)) return null;
      const item = parseQuestionItem(
        rawQuestion,
        `q${index + 1}`,
        false,
      );
      if (!item || seenIds.has(item.id)) return null;
      seenIds.add(item.id);
      questions.push(item);
    }
    return { batch: true, questions };
  }

  const item = parseQuestionItem(block.toolArgs, "answer", true);
  return item ? { ...item, batch: false } : null;
}

function parseQuestionItem(
  args: Record<string, unknown>,
  fallbackId: string,
  allowConfirm: boolean,
): AskUserQuestionItem | null {
  const question =
    typeof args.question === "string" ? args.question.trim() : "";
  if (!question) return null;

  const rawOptions = args.options;
  const multiple = args.multiple;
  const confirm = args.confirm;
  const rawDefault = args.default;
  if (multiple !== undefined && typeof multiple !== "boolean") return null;
  if (confirm !== undefined && confirm !== true) return null;
  if (confirm === true) {
    return allowConfirm && rawOptions === undefined && multiple !== true
      ? {
          id: stringOrFallback(args.id, fallbackId),
          kind: "confirm",
          question,
          ...(typeof rawDefault === "boolean" ? { default: rawDefault } : {}),
        }
      : null;
  }
  if (rawOptions === undefined) {
    return multiple === true
      ? null
      : {
          id: stringOrFallback(args.id, fallbackId),
          kind: "text",
          question,
          ...(typeof rawDefault === "string" && rawDefault.trim()
            ? { default: rawDefault.trim() }
            : {}),
        };
  }
  if (!Array.isArray(rawOptions) || rawOptions.length < 2) return null;

  const options: string[] = [];
  for (const option of rawOptions) {
    if (typeof option !== "string" || !option.trim()) return null;
    options.push(option.trim());
  }
  if (new Set(options).size !== options.length) return null;

  if (multiple) {
    return {
      id: stringOrFallback(args.id, fallbackId),
      kind: "multiple",
      question,
      options,
      ...(Array.isArray(rawDefault) &&
      rawDefault.every(value => typeof value === "string")
        ? { default: rawDefault.map(value => value.trim()).filter(Boolean) }
        : {}),
    };
  }

  return {
    id: stringOrFallback(args.id, fallbackId),
    kind: "single",
    question,
    options,
    ...(typeof rawDefault === "string" && rawDefault.trim()
      ? { default: rawDefault.trim() }
      : {}),
  };
}

function stringOrFallback(value: unknown, fallback: string): string {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

export function askUserQuestionResult(
  details: unknown,
): AskUserQuestionResult | null {
  if (!isRecord(details)) return null;
  if (details.status === "cancelled") return { status: "cancelled" };
  if (
    details.status === "answered" &&
    (typeof details.answer === "string" ||
      typeof details.answer === "boolean" ||
      (Array.isArray(details.answer) &&
        details.answer.every(value => typeof value === "string")) ||
      isAnswerRecord(details.answer))
  ) {
    return { status: "answered", answer: details.answer };
  }
  return null;
}

function isAnswerRecord(
  value: unknown,
): value is Record<string, AskUserQuestionAnswer> {
  if (!isRecord(value)) return false;
  return Object.values(value).every(
    answer =>
      typeof answer === "string" ||
      typeof answer === "boolean" ||
      (Array.isArray(answer) &&
        answer.every(item => typeof item === "string")),
  );
}
