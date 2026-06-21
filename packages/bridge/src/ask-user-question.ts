import { defineTool } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import {
  ASK_USER_QUESTION_TOOL_NAME,
  type AskUserQuestionResult,
} from "./types.js";

export const askUserQuestionParameters = Type.Object({
  question: Type.String({
    minLength: 1,
    description: "The clear, specific question to ask the user.",
  }),
  options: Type.Optional(
    Type.Array(Type.String({ minLength: 1 }), {
      minItems: 2,
      uniqueItems: true,
      description:
        "Answers for a single-choice or multiple-choice question. Include '其他' or 'Other' to let the user enter one custom answer. Omit for free-text or confirmation input.",
    }),
  ),
  multiple: Type.Optional(
    Type.Boolean({
      default: false,
      description: "Set true with options to allow multiple selections.",
    }),
  ),
  confirm: Type.Optional(
    Type.Literal(true, {
      description: "Set true without options to ask for confirmation.",
    }),
  ),
});

export const askUserQuestionResultSchema = Type.Union([
  Type.Object({
    status: Type.Literal("answered"),
    answer: Type.Union([
      Type.String(),
      Type.Array(Type.String()),
      Type.Boolean(),
    ]),
  }),
  Type.Object({ status: Type.Literal("cancelled") }),
]);

interface PendingQuestion {
  kind: "text" | "single" | "multiple" | "confirm";
  options?: readonly string[];
  resolve(result: AskUserQuestionResult): void;
  reject(error: Error): void;
  removeAbortListener(): void;
}

function isOtherOption(value: string): boolean {
  const normalized = value.trim().toLocaleLowerCase();
  return normalized === "其他" || normalized === "other";
}

class AskUserQuestionCoordinator {
  private readonly pending = new Map<string, PendingQuestion>();

  wait(
    toolCallId: string,
    request: {
      options?: readonly string[];
      multiple?: boolean;
      confirm?: true;
    },
    signal: AbortSignal | undefined,
  ): Promise<AskUserQuestionResult> {
    if (this.pending.has(toolCallId)) {
      return Promise.reject(
        new Error(`Question is already pending: ${toolCallId}`),
      );
    }

    if (request.confirm && (request.options || request.multiple)) {
      return Promise.reject(
        new Error("Confirmation questions cannot provide options or multiple"),
      );
    }
    if (request.multiple && !request.options) {
      return Promise.reject(new Error("Multiple-choice questions require options"));
    }
    const options = request.options?.map(option => option.trim());
    if (
      options?.some(option => !option) ||
      (options && new Set(options).size !== options.length)
    ) {
      return Promise.reject(
        new Error("Question options must be non-empty and unique"),
      );
    }

    let kind: PendingQuestion["kind"] = "text";
    if (request.confirm) kind = "confirm";
    else if (request.multiple) kind = "multiple";
    else if (options) kind = "single";

    return new Promise((resolve, reject) => {
      const abort = () => {
        this.pending.delete(toolCallId);
        reject(new Error("Question was aborted"));
      };
      signal?.addEventListener("abort", abort, { once: true });

      this.pending.set(toolCallId, {
        kind,
        options,
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
          answer: string | string[] | boolean;
        },
  ): AskUserQuestionResult {
    const pending = this.pending.get(toolCallId);
    if (!pending) throw new Error(`Pending question not found: ${toolCallId}`);

    let result: AskUserQuestionResult;
    if (response.cancelled) {
      result = { status: "cancelled" };
    } else {
      const { answer } = response;
      if (pending.kind === "confirm") {
        if (typeof answer !== "boolean") {
          throw new Error("Confirmation answer must be boolean");
        }
        result = { status: "answered", answer };
      } else if (pending.kind === "multiple") {
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
        const customAnswers = normalized.filter(
          value => !pending.options?.includes(value),
        );
        const allowsCustom = pending.options?.some(isOtherOption) ?? false;
        if (customAnswers.length > 1 && allowsCustom) {
          throw new Error(
            "Multiple-choice answer may contain only one custom answer",
          );
        }
        if (customAnswers.length > 0 && !allowsCustom) {
          throw new Error(
            "Multiple-choice answer must contain unique provided options",
          );
        }
        result = { status: "answered", answer: normalized };
      } else {
        if (typeof answer !== "string" || !answer.trim()) {
          throw new Error("Question answer cannot be empty");
        }
        const normalized = answer.trim();
        if (pending.options && isOtherOption(normalized)) {
          throw new Error("Other requires a custom answer");
        }
        if (
          pending.options &&
          !pending.options.includes(normalized) &&
          !pending.options.some(isOtherOption)
        ) {
          throw new Error(
            "Question answer must match one of the provided options",
          );
        }
        result = { status: "answered", answer: normalized };
      }
    }

    this.pending.delete(toolCallId);
    pending.removeAbortListener();
    pending.resolve(result);
    return result;
  }

  cancelAll(): void {
    for (const [toolCallId, pending] of this.pending) {
      this.pending.delete(toolCallId);
      pending.removeAbortListener();
      pending.reject(new Error("Question coordinator was disposed"));
    }
  }
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
  description: `Ask the user one structured question during execution.

Provide options for a single-choice question, add multiple: true for multiple choice, set confirm: true without options for confirmation, or omit all three for free-text input. The user may cancel non-confirmation questions; cancellation stops the current workflow. The answer is returned as a tool result and execution then continues.`,
  promptSnippet:
    "Ask the user one single-choice, multiple-choice, confirmation, or free-text question when execution requires their decision",
  promptGuidelines: [
    "Use ask_user_question whenever you need user input to continue; do not ask the question only in assistant text.",
    "If the user cancels ask_user_question, stop the current workflow. Do not ask again or retry unless the user sends a new message explicitly requesting it.",
    "Invoke ask_user_question as a native tool call. Never print, describe, or wrap a tool call in <question> tags, XML, JSON, Markdown, or other assistant text.",
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
