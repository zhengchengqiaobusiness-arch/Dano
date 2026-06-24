import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AskUserQuestionAnswer } from "../types.js";
import {
  askUserQuestionCoordinator,
  askUserQuestionTool,
} from "../ask-user-question.js";

function executeQuestion(
  toolCallId: string,
  params: {
    question: string;
    options?: string[];
    multiple?: boolean;
    confirm?: true;
    default?: AskUserQuestionAnswer;
    questions?: {
      id?: string;
      question: string;
      options?: string[];
      multiple?: boolean;
      default?: AskUserQuestionAnswer;
    }[];
  },
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

  it("instructs the model to collect required input and confirm final summaries", () => {
    expect(askUserQuestionTool.promptGuidelines).toEqual([
      "Use ask_user_question whenever you need user input to continue; do not ask the question only in assistant text.",
      "Call ask_user_question at most once per assistant response. If you need several answers, put every item in one questions array.",
      "If the user cancels ask_user_question, stop the current workflow. Do not ask again or retry unless the user sends a new message explicitly requesting it.",
      "Invoke ask_user_question as a native tool call. Never print, describe, or wrap a tool call in <question> tags, XML, JSON, Markdown, or other assistant text.",
      "If ask_user_question returns a validation error, retry silently with a corrected native tool call; do not explain the correction to the user.",
      "Set default on every non-confirmation question, including every item in questions, using the most likely or safest answer while still letting the user change it.",
      "When using questions, the top level must contain only questions. Put id, question, options, multiple, and default inside each questions item.",
      "For forms, applications, or other user-reviewed summaries, call ask_user_question with confirm: true after presenting the final summary and before treating it as confirmed, ready to submit, or complete.",
    ]);
  });

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

  it("accepts a valid default answer without changing the final user answer", async () => {
    const execution = executeQuestion("choice-default", {
      question: "Deploy now?",
      options: ["Yes", "No"],
      default: "No",
    });

    askUserQuestionCoordinator.answer("choice-default", {
      cancelled: false,
      answer: "Yes",
    });
    await expect(execution).resolves.toMatchObject({
      details: { status: "answered", answer: "Yes" },
    });
  });

  it("returns multiple selected options", async () => {
    const execution = executeQuestion("multiple-1", {
      question: "Choose environments",
      options: ["Test", "Staging", "Production"],
      multiple: true,
    });

    askUserQuestionCoordinator.answer("multiple-1", {
      cancelled: false,
      answer: ["Test", "Staging"],
    });
    await expect(execution).resolves.toMatchObject({
      details: {
        status: "answered",
        answer: ["Test", "Staging"],
      },
    });
  });

  it("returns a custom answer when a single-choice question includes Other", async () => {
    const execution = executeQuestion("single-other", {
      question: "Leave type?",
      options: ["Annual leave", "Other"],
    });

    askUserQuestionCoordinator.answer("single-other", {
      cancelled: false,
      answer: "Volunteer leave",
    });
    await expect(execution).resolves.toMatchObject({
      details: { status: "answered", answer: "Volunteer leave" },
    });
  });

  it("returns one custom answer with multiple selected options", async () => {
    const execution = executeQuestion("multiple-other", {
      question: "Choose environments",
      options: ["Test", "Production", "Other"],
      multiple: true,
    });

    askUserQuestionCoordinator.answer("multiple-other", {
      cancelled: false,
      answer: ["Test", "Disaster recovery"],
    });
    await expect(execution).resolves.toMatchObject({
      details: {
        status: "answered",
        answer: ["Test", "Disaster recovery"],
      },
    });
  });

  it("rejects a bare Other answer", async () => {
    const execution = executeQuestion("bare-other", {
      question: "Leave type?",
      options: ["事假", "其他"],
    });

    expect(() =>
      askUserQuestionCoordinator.answer("bare-other", {
        cancelled: false,
        answer: "其他",
      }),
    ).toThrow("custom answer");
    askUserQuestionCoordinator.answer("bare-other", {
      cancelled: false,
      answer: "志愿者假",
    });
    await expect(execution).resolves.toMatchObject({
      details: { status: "answered", answer: "志愿者假" },
    });
  });

  it("rejects custom answers when Other was not offered", async () => {
    const execution = executeQuestion("custom-not-offered", {
      question: "Leave type?",
      options: ["年假", "事假"],
    });

    expect(() =>
      askUserQuestionCoordinator.answer("custom-not-offered", {
        cancelled: false,
        answer: "志愿者假",
      }),
    ).toThrow("provided options");
    askUserQuestionCoordinator.answer("custom-not-offered", {
      cancelled: false,
      answer: "年假",
    });
    await expect(execution).resolves.toMatchObject({
      details: { status: "answered", answer: "年假" },
    });
  });

  it("rejects more than one custom answer in multiple choice", async () => {
    const execution = executeQuestion("multiple-custom", {
      question: "Choose environments",
      options: ["Test", "Other"],
      multiple: true,
    });

    expect(() =>
      askUserQuestionCoordinator.answer("multiple-custom", {
        cancelled: false,
        answer: ["Disaster recovery", "Development"],
      }),
    ).toThrow("one custom answer");
    askUserQuestionCoordinator.answer("multiple-custom", {
      cancelled: false,
      answer: ["Test", "Development"],
    });
    await expect(execution).resolves.toMatchObject({
      details: { status: "answered", answer: ["Test", "Development"] },
    });
  });

  it("returns a boolean confirmation", async () => {
    const execution = executeQuestion("confirm-1", {
      question: "Deploy now?",
      confirm: true,
    });

    askUserQuestionCoordinator.answer("confirm-1", {
      cancelled: false,
      answer: false,
    });
    await expect(execution).resolves.toMatchObject({
      details: { status: "answered", answer: false },
    });
  });

  it("returns grouped answers from one tool confirmation", async () => {
    const execution = askUserQuestionTool.execute(
      "group-1",
      {
        questions: [
          { id: "name", question: "Name?", default: "Dano" },
          {
            id: "env",
            question: "Environment?",
            options: ["Test", "Production"],
            default: "Test",
          },
          {
            id: "features",
            question: "Features?",
            options: ["Chat", "Deploy"],
            multiple: true,
            default: ["Chat"],
          },
        ],
      },
      undefined,
      undefined,
      {} as never,
    );

    askUserQuestionCoordinator.answer("group-1", {
      cancelled: false,
      answer: {
        name: "Dano",
        env: "Production",
        features: ["Chat", "Deploy"],
      },
    });
    await expect(execution).resolves.toMatchObject({
      details: {
        status: "answered",
        answer: {
          name: "Dano",
          env: "Production",
          features: ["Chat", "Deploy"],
        },
      },
    });
  });

  it("rejects simultaneous separate questions so the model retries as one grouped card", async () => {
    const first = executeQuestion("separate-1", {
      question: "Leave type?",
      options: ["Annual", "Sick"],
      default: "Annual",
    });
    const second = executeQuestion("separate-2", {
      question: "Start date?",
      options: ["Today", "Tomorrow"],
      default: "Today",
    });

    await expect(second).rejects.toThrow("exactly one ask_user_question call");
    await expect(first).rejects.toThrow("exactly one ask_user_question call");
    expect(() =>
      askUserQuestionCoordinator.answer("separate-1", {
        cancelled: false,
        answer: "Annual",
      }),
    ).toThrow("Pending question not found");
  });

  it("explains how to fix grouped calls that mix top-level question fields", async () => {
    await expect(
      askUserQuestionTool.execute(
        "group-mixed",
        {
          question: "Leave details?",
          default: "事假",
          questions: [
            { id: "leave_type", question: "Leave type?", default: "事假" },
          ],
        },
        undefined,
        undefined,
        {} as never,
      ),
    ).rejects.toThrow("top level may contain only questions");
  });

  it("rejects grouped answers missing a question id", async () => {
    const execution = askUserQuestionTool.execute(
      "group-missing",
      {
        questions: [
          { id: "name", question: "Name?" },
          { id: "env", question: "Environment?", options: ["Test", "Prod"] },
        ],
      },
      undefined,
      undefined,
      {} as never,
    );

    expect(() =>
      askUserQuestionCoordinator.answer("group-missing", {
        cancelled: false,
        answer: { name: "Dano" },
      }),
    ).toThrow("Missing answer");
    askUserQuestionCoordinator.answer("group-missing", {
      cancelled: false,
      answer: { name: "Dano", env: "Test" },
    });
    await expect(execution).resolves.toMatchObject({
      details: { status: "answered" },
    });
  });

  it("rejects invalid multiple-choice answers without settling", async () => {
    const execution = executeQuestion("multiple-2", {
      question: "Choose environments",
      options: ["Test", "Production"],
      multiple: true,
    });

    expect(() =>
      askUserQuestionCoordinator.answer("multiple-2", {
        cancelled: false,
        answer: ["Unknown"],
      }),
    ).toThrow("unique provided options");
    askUserQuestionCoordinator.answer("multiple-2", {
      cancelled: false,
      answer: ["Production"],
    });
    await expect(execution).resolves.toMatchObject({
      details: { status: "answered", answer: ["Production"] },
    });
  });

  it("rejects incompatible confirmation parameters", async () => {
    await expect(
      executeQuestion("confirm-invalid", {
        question: "Deploy now?",
        options: ["Yes", "No"],
        confirm: true,
      }),
    ).rejects.toThrow("cannot provide options");
  });

  it("rejects grouped confirmation parameters instead of waiting forever", async () => {
    await expect(
      askUserQuestionTool.execute(
        "group-confirm-invalid",
        {
          questions: [
            {
              id: "confirm_leave",
              question: "Submit leave request?",
              options: ["Submit", "Revise"],
              confirm: true,
            },
          ],
        },
        undefined,
        undefined,
        {} as never,
      ),
    ).rejects.toThrow("cannot provide options");
    expect(() =>
      askUserQuestionCoordinator.answer("group-confirm-invalid", {
        cancelled: false,
        answer: true,
      }),
    ).toThrow("Pending question not found");
  });

  it("returns cancellation as a successful tool result", async () => {
    const execution = executeQuestion("cancel-1", { question: "Continue?" });

    askUserQuestionCoordinator.answer("cancel-1", { cancelled: true });
    await expect(execution).resolves.toMatchObject({
      content: [
        {
          type: "text",
          text: "User cancelled the question. Stop the current workflow. Do not ask another question or retry unless the user sends a new message explicitly requesting it.",
        },
      ],
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

  it("shares pending questions across standalone dev runtime module reloads", async () => {
    vi.resetModules();
    const firstRuntime = await import("../ask-user-question.js");
    vi.resetModules();
    const reloadedRuntime = await import("../ask-user-question.js");

    expect(reloadedRuntime.askUserQuestionCoordinator).toBe(
      firstRuntime.askUserQuestionCoordinator,
    );
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
