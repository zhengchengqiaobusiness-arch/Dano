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
        "Mutually exclusive answers for a single-choice question. Omit for free-text input.",
    }),
  ),
});

export const askUserQuestionResultSchema = Type.Union([
  Type.Object({
    status: Type.Literal("answered"),
    answer: Type.String(),
  }),
  Type.Object({ status: Type.Literal("cancelled") }),
]);

interface PendingQuestion {
  options?: readonly string[];
  resolve(result: AskUserQuestionResult): void;
  reject(error: Error): void;
  removeAbortListener(): void;
}

class AskUserQuestionCoordinator {
  private readonly pending = new Map<string, PendingQuestion>();

  wait(
    toolCallId: string,
    options: readonly string[] | undefined,
    signal: AbortSignal | undefined,
  ): Promise<AskUserQuestionResult> {
    if (this.pending.has(toolCallId)) {
      return Promise.reject(
        new Error(`Question is already pending: ${toolCallId}`),
      );
    }

    return new Promise((resolve, reject) => {
      const abort = () => {
        this.pending.delete(toolCallId);
        reject(new Error("Question was aborted"));
      };
      signal?.addEventListener("abort", abort, { once: true });

      this.pending.set(toolCallId, {
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
      | { cancelled: false; answer: string },
  ): AskUserQuestionResult {
    const pending = this.pending.get(toolCallId);
    if (!pending) throw new Error(`Pending question not found: ${toolCallId}`);

    let result: AskUserQuestionResult;
    if (response.cancelled) {
      result = { status: "cancelled" };
    } else {
      const answer = response.answer.trim();
      if (!answer) throw new Error("Question answer cannot be empty");
      if (pending.options && !pending.options.includes(answer)) {
        throw new Error("Question answer must match one of the provided options");
      }
      result = { status: "answered", answer };
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

export const askUserQuestionCoordinator = new AskUserQuestionCoordinator();

export const askUserQuestionTool = defineTool({
  name: ASK_USER_QUESTION_TOOL_NAME,
  label: "Ask User Question",
  description: `Ask the user one structured question during execution.

Provide options for a single-choice question, or omit options for free-text input. The user may cancel. The answer is returned as a tool result and execution then continues.`,
  promptSnippet:
    "Ask the user one single-choice or free-text question when execution requires their decision",
  parameters: askUserQuestionParameters,
  executionMode: "sequential",
  async execute(toolCallId, params, signal) {
    const result = await askUserQuestionCoordinator.wait(
      toolCallId,
      params.options,
      signal,
    );

    return {
      content: [
        {
          type: "text",
          text:
            result.status === "answered"
              ? `User answered the question: ${JSON.stringify(result.answer)}. Continue with this answer.`
              : "User cancelled the question. Continue without an answer.",
        },
      ],
      details: result,
    };
  },
});
