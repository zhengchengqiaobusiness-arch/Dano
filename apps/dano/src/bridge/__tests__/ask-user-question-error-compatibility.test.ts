import { describe, expect, it, vi } from "vitest";
import { Value } from "typebox/value";
import {
  AskUserQuestionCoordinator,
  askUserQuestionResultSchema,
  normalizeAskUserQuestionCardRequestResult,
} from "../ask-user-question.js";
import {
  parseAskUserQuestionFailure,
  projectAskUserQuestionFailure,
} from "../ask-user-question-errors.js";
import type { AskUserQuestionInvalidResult } from "../types.js";

function normalizeFailure(request: unknown): AskUserQuestionInvalidResult {
  const result = normalizeAskUserQuestionCardRequestResult(request);
  if (!("error" in result)) throw new Error("Expected normalization failure");
  return result.error;
}

async function rejectedFailure(promise: Promise<unknown>) {
  const error = await promise.then(
    () => { throw new Error("Expected rejected ask_user_question call"); },
    cause => cause instanceof Error ? cause : new Error(String(cause)),
  );
  const failure = parseAskUserQuestionFailure(error.message);
  if (!failure) throw new Error(`Expected structured failure: ${error.message}`);
  return failure;
}

describe("ask_user_question executable error compatibility matrix", () => {
  it.each([
    ["invalid request shape", "not an object", "invalid_request_shape", undefined],
    ["invalid questions JSON", { questions: '[{"id":"x"' }, "invalid_questions_json", "questions"],
    ["invalid questions shape", { questions: 7 }, "invalid_questions_shape", "questions"],
    ["invalid question item", { questions: [null] }, "invalid_question_item", "questions[0]"],
    ["conflicting aliases", { question: "Pick", options: ["A"], choices: ["B"] }, "conflicting_aliases", "options"],
    ["missing grouped id", { questions: [{ question: "First?" }] }, "missing_question_id", "questions[0].id"],
    ["missing question text", { default: "A" }, "missing_question_text", "question"],
    ["invalid input type", { question: "Value?", inputType: "spreadsheet" }, "invalid_input_type", "inputType"],
    ["invalid options", { question: "Pick", options: ["A", null] }, "invalid_options", "options"],
    ["duplicate option ids", { question: "Pick", options: ["A", "A"] }, "duplicate_option_id", "options[0]"],
    ["missing choice source", { question: "Pick", inputType: "select" }, "missing_choice_source", "question"],
    ["invalid default", { question: "Pick", options: ["A"], default: "B" }, "invalid_default", "default"],
    ["invalid date format", { question: "When?", inputType: "date", dateFormat: "yyyy-MM" }, "invalid_date_format", "dateFormat"],
    ["invalid data source", { question: "Who?", inputType: "select", dataSource: { type: "api" } }, "invalid_data_source", "dataSource"],
  ] as const)("returns a stable issue for %s", (_name, request, code, path) => {
    const failure = normalizeFailure(request);
    expect(failure).toMatchObject({
      status: "invalid",
      error: {
        code: "invalid_question_arguments",
        category: "validation",
        retryable: true,
      },
    });
    expect(failure.error.issues).toContainEqual(expect.objectContaining({
      code,
      ...(path ? { path } : {}),
    }));
  });

  it("reports every missing and duplicate grouped id location in one result", () => {
    const missing = normalizeFailure({
      questions: [
        { question: "First?" },
        { question: "Second?" },
      ],
    });
    expect(missing.error.issues.map(issue => issue.path)).toEqual([
      "questions[0].id",
      "questions[1].id",
    ]);

    const duplicates = normalizeFailure({
      questions: [
        { id: "same", question: "First?" },
        { id: "same", question: "Second?" },
        { id: "same", question: "Third?" },
      ],
    });
    expect(duplicates.error.issues).toEqual([
      expect.objectContaining({ code: "duplicate_question_id", path: "questions[0].id" }),
      expect.objectContaining({ code: "duplicate_question_id", path: "questions[1].id" }),
      expect.objectContaining({ code: "duplicate_question_id", path: "questions[2].id" }),
    ]);
  });

  it("keeps canonical, JSON-stringified, and alias failures equivalent", () => {
    const canonical = normalizeFailure({ question: "Pick", options: ["A", "A"] });
    const json = normalizeFailure({ question: "Pick", options: '["A","A"]' });
    const alias = normalizeFailure({ question: "Pick", choices: ["A", "A"] });
    expect(json).toEqual(canonical);
    expect(alias).toEqual(canonical);
  });

  it("does not serialize answers, unavailable ids, scripts, raw arguments, or stacks", () => {
    const secretAnswer = "secret-answer-value";
    const script = "curl https://internal.invalid/token";
    const failure = normalizeFailure({
      question: "Pick",
      options: ["A"],
      default: { unexpected: secretAnswer, script },
      rawArguments: { secretAnswer },
      stack: "at internalFunction (/srv/private.ts:1:1)",
    });
    const serialized = JSON.stringify(failure);
    expect(serialized).not.toContain(secretAnswer);
    expect(serialized).not.toContain(script);
    expect(serialized).not.toContain("rawArguments");
    expect(serialized).not.toContain("internalFunction");
  });

  it("serializes confirmation diagnostics without unavailable form ids", async () => {
    const unavailableId = "private-unavailable-form-id";
    const coordinator = new AskUserQuestionCoordinator();
    const failure = await rejectedFailure(coordinator.wait(
      "confirm-invalid",
      { confirm: true, formIds: [unavailableId] } as never,
      new AbortController().signal,
    ));
    expect(failure).toMatchObject({
      error: {
        code: "invalid_confirmation_source",
        category: "confirmation",
        retryable: true,
        context: {
          receivedShape: { formIds: "array(1)", formId: "omitted" },
          ignoredReasons: ["unavailable_form_id"],
          fallbackAttempted: true,
        },
      },
    });
    expect(JSON.stringify(failure)).not.toContain(unavailableId);
  });

  it("returns structured duplicate-call, cancellation, presentation, and terminal validation failures", async () => {
    const controller = new AbortController();
    const coordinator = new AskUserQuestionCoordinator(100, 1);
    const first = coordinator.wait(
      "first",
      { question: "First?" },
      controller.signal,
    );
    const duplicate = await rejectedFailure(coordinator.wait(
      "second",
      { question: "Second?" },
      controller.signal,
    ));
    expect(duplicate.error).toMatchObject({
      code: "duplicate_question_call",
      category: "duplicate_call",
      retryable: true,
    });
    controller.abort();
    const cancelled = await rejectedFailure(first);
    expect(cancelled.error).toMatchObject({
      code: "question_cancelled",
      category: "lifecycle",
      retryable: false,
    });

    const validationController = new AbortController();
    const validationCoordinator = new AskUserQuestionCoordinator(100, 0);
    const terminalValidation = await rejectedFailure(validationCoordinator.wait(
      "invalid",
      { questions: "[" },
      validationController.signal,
    ));
    expect(terminalValidation.error).toMatchObject({
      code: "question_validation_failed",
      retryable: false,
      terminalCode: "QUESTION_VALIDATION_FAILED",
    });

    vi.useFakeTimers();
    try {
      const presentationCoordinator = new AskUserQuestionCoordinator(100, 1);
      const presentationController = new AbortController();
      const retry = presentationCoordinator.wait(
        "presentation-retry",
        { question: "Name?" },
        presentationController.signal,
      );
      const retryFailurePromise = rejectedFailure(retry);
      await vi.advanceTimersByTimeAsync(100);
      const retryFailure = await retryFailurePromise;
      expect(retryFailure.error).toMatchObject({
        code: "question_presentation_timeout",
        retryable: true,
        terminalCode: "QUESTION_PRESENTATION_TIMEOUT",
      });

      const terminal = presentationCoordinator.wait(
        "presentation-terminal",
        { question: "Name?" },
        presentationController.signal,
      );
      const terminalFailurePromise = rejectedFailure(terminal);
      await vi.advanceTimersByTimeAsync(100);
      const terminalFailure = await terminalFailurePromise;
      expect(terminalFailure.error).toMatchObject({
        code: "question_presentation_failed",
        retryable: false,
        terminalCode: "QUESTION_PRESENTATION_FAILED",
      });
    } finally {
      vi.useRealTimers();
    }
  });

  it("projects one sanitized browser summary and keeps field issues model-facing", () => {
    const failure = normalizeFailure({
      questions: [{ question: "First?" }, { question: "Second?" }],
    });
    expect(projectAskUserQuestionFailure(failure)).toEqual({
      code: "invalid_question_arguments",
      category: "validation",
      message: "Question fields contain invalid arguments.",
      retryable: true,
    });
    expect(projectAskUserQuestionFailure(failure)).not.toHaveProperty("issues");
    expect(Value.Check(askUserQuestionResultSchema, failure)).toBe(true);
  });
});
