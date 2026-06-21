import { beforeEach, describe, expect, it } from "vitest";
import {
  askUserQuestionCoordinator,
  askUserQuestionTool,
} from "../ask-user-question.js";

function executeQuestion(
  toolCallId: string,
  params: { question: string; options?: string[] },
  signal?: AbortSignal,
) {
  return askUserQuestionTool.execute(
    toolCallId,
    params,
    signal,
    undefined,
    {} as never,
  );
}

describe("ask_user_question tool", () => {
  beforeEach(() => askUserQuestionCoordinator.cancelAll());

  it("returns a free-text answer as structured tool details", async () => {
    const execution = executeQuestion("text-1", { question: "Project name?" });

    expect(
      askUserQuestionCoordinator.answer("text-1", {
        cancelled: false,
        answer: "  Dano  ",
      }),
    ).toEqual({ status: "answered", answer: "Dano" });
    await expect(execution).resolves.toMatchObject({
      content: [
        expect.objectContaining({ text: expect.stringContaining('"Dano"') }),
      ],
      details: { status: "answered", answer: "Dano" },
    });
  });

  it("returns a selected option", async () => {
    const execution = executeQuestion("choice-1", {
      question: "Deploy now?",
      options: ["Yes", "No"],
    });

    askUserQuestionCoordinator.answer("choice-1", {
      cancelled: false,
      answer: "Yes",
    });
    await expect(execution).resolves.toMatchObject({
      details: { status: "answered", answer: "Yes" },
    });
  });

  it("returns cancellation as a successful tool result", async () => {
    const execution = executeQuestion("cancel-1", { question: "Continue?" });

    askUserQuestionCoordinator.answer("cancel-1", { cancelled: true });
    await expect(execution).resolves.toMatchObject({
      content: [expect.objectContaining({ text: expect.stringContaining("cancelled") })],
      details: { status: "cancelled" },
    });
  });

  it("rejects an invalid option without settling the question", async () => {
    const execution = executeQuestion("choice-2", {
      question: "Pick one",
      options: ["A", "B"],
    });

    expect(() =>
      askUserQuestionCoordinator.answer("choice-2", {
        cancelled: false,
        answer: "C",
      }),
    ).toThrow("must match");
    askUserQuestionCoordinator.answer("choice-2", {
      cancelled: false,
      answer: "B",
    });
    await expect(execution).resolves.toMatchObject({
      details: { status: "answered", answer: "B" },
    });
  });

  it("rejects answers for unknown tool calls", () => {
    expect(() =>
      askUserQuestionCoordinator.answer("missing", { cancelled: true }),
    ).toThrow("Pending question not found");
  });

  it("removes a pending question when its agent turn aborts", async () => {
    const controller = new AbortController();
    const execution = executeQuestion(
      "abort-1",
      { question: "Wait?" },
      controller.signal,
    );

    controller.abort();
    await expect(execution).rejects.toThrow("Question was aborted");
    expect(() =>
      askUserQuestionCoordinator.answer("abort-1", { cancelled: true }),
    ).toThrow("Pending question not found");
  });
});
