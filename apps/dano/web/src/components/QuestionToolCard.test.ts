import { render } from "svelte/server";
import { describe, expect, it, vi } from "vitest";
import type { ToolContentBlock } from "../utils/transcript";
import QuestionToolCard from "./QuestionToolCard.svelte";
import { questionConfirmationState } from "./questionConfirmationState.svelte";

vi.mock("./MarkdownRenderer.svelte", () => ({
  default: (payload: { out: string }, props: { content: string }) => {
    payload.out += props.content;
  },
}));
vi.mock("./QuestionDateField.svelte", () => ({ default: () => {} }));
vi.mock("./SubmittedAnswerValue.svelte", () => ({ default: () => {} }));
vi.mock("lucide-svelte/icons/chevron-down", () => ({ default: () => {} }));
vi.mock("lucide-svelte/icons/calendar", () => ({ default: () => {} }));
vi.mock("lucide-svelte/icons/check", () => ({ default: () => {} }));
vi.mock("lucide-svelte/icons/circle-check", () => ({ default: () => {} }));
vi.mock("lucide-svelte/icons/list-checks", () => ({ default: () => {} }));
vi.mock("lucide-svelte/icons/message-square-text", () => ({ default: () => {} }));
vi.mock("lucide-svelte/icons/refresh-cw", () => ({ default: () => {} }));
vi.mock("lucide-svelte/icons/sparkle", () => ({ default: () => {} }));

function confirmationBlock(): ToolContentBlock {
  return {
    kind: "tool",
    toolName: "ask_user_question",
    toolCallId: "confirmation-1",
    toolArgs: {},
    argumentsText: "",
    toolStatus: "pending",
    questionRequest: {
      batch: false,
      id: "confirmation",
      kind: "confirm",
      title: "用印申请确认",
      confirmationOfToolCallId: "form-1",
      questions: [{ id: "type", kind: "text", question: "印章类型？" }],
      answer: { type: "财务章" },
    },
  };
}

function answeredFormBlock(): ToolContentBlock {
  return {
    kind: "tool",
    toolName: "ask_user_question",
    toolCallId: "form-2",
    toolArgs: {},
    argumentsText: "",
    toolStatus: "success",
    questionRequest: {
      batch: true,
      title: "测试申请",
      questions: [{ id: "reason", kind: "text", question: "申请原因？" }],
    },
    resultDetails: {
      status: "answered",
      answer: { reason: "个人事务" },
    },
  };
}

describe("QuestionToolCard", () => {
  it("hides confirmation actions after generation is stopped", () => {
    const response = vi.fn(async () => {
      throw new Error("inactive confirmation card must not issue RPCs");
    });
    const { body } = render(QuestionToolCard, {
      props: {
        block: confirmationBlock(),
        active: false,
        onPresent: response,
        onRespond: response,
        onUpdate: response,
      },
    });

    expect(body).toContain("问题已中断");
    expect(body).not.toContain("question-actions");
    expect(body).not.toContain(">确认<");
    expect(body).not.toContain(">取消<");
  });

  it("hides linked confirmation actions on the submitted form after generation is stopped", () => {
    questionConfirmationState.sync(
      {
        batch: false,
        id: "confirmation",
        kind: "confirm",
        title: "测试申请确认",
        confirmationOfToolCallId: "form-2",
        questions: [{ id: "reason", kind: "text", question: "申请原因？" }],
        answer: { reason: "个人事务" },
      },
      "confirmation-2",
    );
    const response = vi.fn(async () => {
      throw new Error("inactive submitted form must not issue RPCs");
    });
    const { body } = render(QuestionToolCard, {
      props: {
        block: answeredFormBlock(),
        active: false,
        onPresent: response,
        onRespond: response,
        onUpdate: response,
      },
    });

    expect(body).toContain('data-status="answered"');
    expect(body).not.toContain(">确认<");
    expect(body).not.toContain(">取消<");
  });
});
