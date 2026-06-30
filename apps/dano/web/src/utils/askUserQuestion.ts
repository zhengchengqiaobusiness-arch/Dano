import {
  ASK_USER_QUESTION_TOOL_NAME,
  type AskUserQuestionAnswer,
  type AskUserQuestionDataSource,
  type AskUserQuestionInputType,
  type AskUserQuestionOption,
  type AskUserQuestionOptionId,
  type AskUserQuestionResult,
} from "@dano/types/protocol";
import type { ToolContentBlock } from "./transcript";

export type NormalizedAskUserQuestionOption = AskUserQuestionOption;

export type AskUserQuestionItem =
  | { id: string; kind: "text"; question: string; default?: string }
  | {
      id: string;
      kind: "single";
      question: string;
      options: NormalizedAskUserQuestionOption[];
      default?: AskUserQuestionOptionId;
    }
  | {
      id: string;
      kind: "select" | "treeSelect";
      question: string;
      options: NormalizedAskUserQuestionOption[];
      dataSource?: AskUserQuestionDataSource;
      default?: AskUserQuestionOptionId;
    }
  | {
      id: string;
      kind: "multiple";
      question: string;
      options: NormalizedAskUserQuestionOption[];
      dataSource?: AskUserQuestionDataSource;
      inputType?: "treeSelect";
      default?: AskUserQuestionOptionId[];
    }
  | { id: string; kind: "confirm"; question: string; default?: boolean };

export type AskUserQuestionRequest =
  | (AskUserQuestionItem & { batch: false })
  | { batch: true; questions: AskUserQuestionItem[] };

export function askUserQuestionMarkdown(question: string): string {
  return question.replace(/\\+(?:r\\+n|n)/g, "\n");
}

export function askUserQuestionAnswerMarkdown(
  request: AskUserQuestionRequest,
  answer: AskUserQuestionAnswer | Record<string, AskUserQuestionAnswer>,
  labels: { confirm: string; cancel: string },
): string {
  if (!request.batch) return answerValueMarkdown(request, answer, labels);
  if (!isAnswerRecord(answer)) return answerValueMarkdown(undefined, answer, labels);

  const used = new Set<string>();
  const lines: string[] = [];
  for (const item of request.questions) {
    if (!(item.id in answer)) continue;
    used.add(item.id);
    lines.push(`- ${questionLabel(item.question)}：${answerValueMarkdown(item, answer[item.id], labels)}`);
  }
  for (const [key, value] of Object.entries(answer)) {
    if (!used.has(key)) lines.push(`- ${key}：${answerValueMarkdown(undefined, value, labels)}`);
  }
  return lines.length > 0 ? `\n${lines.join("\n")}` : "";
}

export function isAskUserQuestionToolError(block: ToolContentBlock): boolean {
  return (
    block.toolName === ASK_USER_QUESTION_TOOL_NAME && block.toolStatus === "error"
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
  if (!item || item.kind === "text" || item.kind === "confirm") return String(answer);
  return item.options.find(option => option.id === answer)?.label ?? String(answer);
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
  const inputType = parseInputType(args.inputType);
  const dataSource = parseDataSource(args.dataSource);
  const multiple = args.multiple;
  const confirm = args.confirm;
  const rawDefault = args.default;
  if (multiple !== undefined && typeof multiple !== "boolean") return null;
  if (confirm !== undefined && confirm !== true) return null;
  if (confirm === true || inputType === "confirm") {
    return allowConfirm && rawOptions === undefined && multiple !== true && !dataSource
      ? {
          id: stringOrFallback(args.id, fallbackId),
          kind: "confirm",
          question,
          ...(typeof rawDefault === "boolean" ? { default: rawDefault } : {}),
        }
      : null;
  }
  if (rawOptions === undefined && !dataSource) {
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
  if (dataSource && inputType !== "select" && inputType !== "treeSelect") return null;
  if (rawOptions !== undefined && (!Array.isArray(rawOptions) || rawOptions.length < 2)) return null;

  const options: NormalizedAskUserQuestionOption[] = [];
  for (const option of rawOptions ?? []) {
    const normalized = normalizeOption(option);
    if (!normalized) return null;
    options.push(normalized);
  }
  if (new Set(options.map(option => option.id)).size !== options.length) return null;

  if (multiple) {
    return {
      id: stringOrFallback(args.id, fallbackId),
      kind: "multiple",
      question,
      options,
      ...(dataSource ? { dataSource } : {}),
      ...(inputType === "treeSelect" ? { inputType } : {}),
      ...(Array.isArray(rawDefault)
        ? { default: rawDefault.map(normalizeAnswerInput).filter(isOptionId) }
        : {}),
    };
  }

  if (inputType === "select" || inputType === "treeSelect") {
    return {
      id: stringOrFallback(args.id, fallbackId),
      kind: inputType,
      question,
      options,
      ...(dataSource ? { dataSource } : {}),
      ...(isOptionId(normalizeAnswerInput(rawDefault))
        ? { default: normalizeAnswerInput(rawDefault) }
        : {}),
    };
  }

  return {
    id: stringOrFallback(args.id, fallbackId),
    kind: "single",
    question,
    options,
    ...(isOptionId(normalizeAnswerInput(rawDefault))
      ? { default: normalizeAnswerInput(rawDefault) }
      : {}),
  };
}

function normalizeOption(value: unknown): NormalizedAskUserQuestionOption | null {
  if (typeof value === "string") {
    const option = value.trim();
    return option ? { id: option, label: option } : null;
  }
  if (!isRecord(value)) return null;
  const id = normalizeOptionId(value.id);
  const label = typeof value.label === "string" ? value.label.trim() : "";
  if (id === undefined || !label) return null;
  return {
    id,
    label,
    ...(isRecord(value.extra) ? { extra: value.extra } : {}),
  };
}

function normalizeAnswerInput(value: unknown): AskUserQuestionOptionId | undefined {
  if (isRecord(value)) return normalizeOption(value)?.id;
  return normalizeOptionId(value);
}

function normalizeOptionId(value: unknown): AskUserQuestionOptionId | undefined {
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed ? trimmed : undefined;
  }
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function isOptionId(value: unknown): value is AskUserQuestionOptionId {
  return typeof value === "string" || typeof value === "number";
}

function parseInputType(value: unknown): AskUserQuestionInputType | undefined {
  return value === "text" ||
    value === "radio" ||
    value === "checkbox" ||
    value === "select" ||
    value === "treeSelect" ||
    value === "confirm"
    ? value
    : undefined;
}

function parseDataSource(value: unknown): AskUserQuestionDataSource | undefined {
  if (!isRecord(value) || value.type !== "api" || typeof value.endpoint !== "string" || !value.endpoint.trim()) {
    return undefined;
  }
  return value as AskUserQuestionDataSource;
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
      typeof details.answer === "number" ||
      typeof details.answer === "boolean" ||
      (Array.isArray(details.answer) &&
        details.answer.every(isOptionId)) ||
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
      typeof answer === "number" ||
      typeof answer === "boolean" ||
      (Array.isArray(answer) &&
        answer.every(isOptionId)),
  );
}
