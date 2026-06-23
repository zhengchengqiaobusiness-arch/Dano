import {
  ASK_USER_QUESTION_TOOL_NAME,
  type AskUserQuestionResult,
} from "@dano/bridge/types";
import type { ToolContentBlock } from "./transcript";

export type AskUserQuestionRequest =
  | { kind: "text"; question: string }
  | { kind: "single"; question: string; options: string[] }
  | { kind: "multiple"; question: string; options: string[] }
  | { kind: "confirm"; question: string };

export function askUserQuestionMarkdown(question: string): string {
  return question.replace(/\\+(?:r\\+n|n)/g, "\n");
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

  const question =
    typeof block.toolArgs.question === "string"
      ? block.toolArgs.question.trim()
      : "";
  if (!question) return null;

  const rawOptions = block.toolArgs.options;
  const multiple = block.toolArgs.multiple;
  const confirm = block.toolArgs.confirm;
  if (multiple !== undefined && typeof multiple !== "boolean") return null;
  if (confirm !== undefined && confirm !== true) return null;
  if (confirm === true) {
    return rawOptions === undefined && multiple !== true
      ? { kind: "confirm", question }
      : null;
  }
  if (rawOptions === undefined) {
    return multiple === true ? null : { kind: "text", question };
  }
  if (!Array.isArray(rawOptions) || rawOptions.length < 2) return null;

  const options: string[] = [];
  for (const option of rawOptions) {
    if (typeof option !== "string" || !option.trim()) return null;
    options.push(option.trim());
  }
  if (new Set(options).size !== options.length) return null;

  return {
    kind: multiple ? "multiple" : "single",
    question,
    options,
  };
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
        details.answer.every(value => typeof value === "string")))
  ) {
    return { status: "answered", answer: details.answer };
  }
  return null;
}
