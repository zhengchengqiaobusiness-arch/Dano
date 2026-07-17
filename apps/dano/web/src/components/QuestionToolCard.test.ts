/** @vitest-environment happy-dom */

import { mount, tick, unmount } from "svelte";
import { describe, expect, it, vi } from "vitest";
import type { ToolContentBlock } from "../utils/transcript";
import QuestionToolCard from "./QuestionToolCard.svelte";

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

function submittedFormBlock(
  state?: "awaiting_confirmation" | "confirmed" | "cancelled" | "interrupted",
): ToolContentBlock {
  return {
    kind: "tool",
    toolName: "ask_user_question",
    toolCallId: "form-1",
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
      formId: "form-1",
      answer: { reason: "个人事务" },
    },
    ...(state ? {
      formInteraction: {
        interactionId: "confirm-form-1",
        state,
        revision: state === "awaiting_confirmation" ? 1 : 2,
        allowedActions:
          state === "awaiting_confirmation" ? ["cancel", "confirm"] : [],
      },
    } : {}),
  };
}

function multiFormConfirmationBlock(): ToolContentBlock {
  return {
    kind: "tool",
    toolName: "ask_user_question",
    toolCallId: "confirm-two",
    toolArgs: {},
    argumentsText: "",
    toolStatus: "pending",
    questionRequest: {
      batch: false,
      id: "confirmation",
      kind: "confirm",
      title: "确认 2 份表单",
      confirmationOfToolCallId: "form-a",
      questions: [{ id: "reason", kind: "text", question: "请假原因？" }],
      answer: { reason: "家庭事务" },
      forms: [
        {
          formId: "form-a",
          title: "请假申请",
          questions: [{ id: "reason", kind: "text", question: "请假原因？" }],
          answer: { reason: "家庭事务" },
        },
        {
          formId: "form-b",
          title: "出差申请",
          questions: [{ id: "destination", kind: "text", question: "目的地？" }],
          answer: { destination: "上海" },
        },
      ],
    },
    formInteraction: {
      interactionId: "confirm-two",
      state: "awaiting_confirmation",
      revision: 1,
      allowedActions: ["cancel", "confirm"],
    },
  };
}

describe("QuestionToolCard", () => {
  it("renders an unconfirmed Submitted Form as read-only with a disabled submitted status", async () => {
    const response = vi.fn(async () => {
      throw new Error("a Submitted Form must not issue confirmation RPCs");
    });
    const target = document.createElement("div");
    const component = mount(QuestionToolCard, {
      target,
      props: {
        block: submittedFormBlock(),
        active: false,
        onPresent: response,
        onRespond: response,
        onUpdate: response,
      },
    });
    await tick();

    expect(target.innerHTML).toContain("已提交");
    expect(target.querySelector("article")?.dataset.formId).toBe("form-1");
    expect(target.querySelector("button")?.disabled).toBe(true);
    expect(target.textContent).not.toContain("确认");
    expect(target.textContent).not.toContain("取消");

    unmount(component);
  });

  it("renders one atomic confirmation action set for multiple Submitted Forms", async () => {
    vi.useFakeTimers();
    const response = vi.fn(async () => ({ success: true } as never));
    const target = document.createElement("div");
    const component = mount(QuestionToolCard, {
      target,
      props: {
        block: multiFormConfirmationBlock(),
        active: true,
        onPresent: response,
        onRespond: response,
        onUpdate: response,
      },
    });
    try {
      await vi.advanceTimersByTimeAsync(400);
      await tick();

      expect(target.querySelectorAll("[data-form-id]")).toHaveLength(2);
      expect(target.textContent).toContain("请假申请");
      expect(target.textContent).toContain("出差申请");
      expect(target.textContent).not.toContain("返回修改");
      expect(target.querySelectorAll("button.question-button")).toHaveLength(2);
      expect(target.textContent).toContain("取消");
      expect(target.textContent).toContain("确认");
    } finally {
      unmount(component);
      vi.useRealTimers();
    }
  });

  it("keeps an interrupted Submitted Form terminal while a later turn is streaming", async () => {
    const response = vi.fn(async () => {
      throw new Error("a terminal Form Interaction must not issue RPCs");
    });
    const target = document.createElement("div");
    const component = mount(QuestionToolCard, {
      target,
      props: {
        block: submittedFormBlock("interrupted"),
        active: true,
        onPresent: response,
        onRespond: response,
        onUpdate: response,
      },
    });
    await tick();

    const buttons = target.querySelectorAll<HTMLButtonElement>(
      ".question-actions button.question-button",
    );
    expect(buttons).toHaveLength(1);
    expect(buttons[0]?.textContent).toContain("已中断");
    expect(buttons[0]?.disabled).toBe(true);
    expect(target.textContent).not.toContain("确认");
    expect(target.textContent).not.toContain("取消");
    expect(response).not.toHaveBeenCalled();

    unmount(component);
  });

  it("renders an interrupted confirmation card as a disabled terminal snapshot", async () => {
    const response = vi.fn(async () => ({ success: true } as never));
    const block = multiFormConfirmationBlock();
    block.formInteraction = {
      interactionId: "confirm-two",
      state: "interrupted",
      revision: 2,
      allowedActions: [],
    };
    const target = document.createElement("div");
    const component = mount(QuestionToolCard, {
      target,
      props: {
        block,
        active: true,
        onPresent: response,
        onRespond: response,
        onUpdate: response,
      },
    });
    await tick();

    const buttons = target.querySelectorAll<HTMLButtonElement>("button.question-button");
    expect(buttons).toHaveLength(1);
    expect(buttons[0]?.textContent).toContain("已中断");
    expect(buttons[0]?.disabled).toBe(true);
    expect(response).not.toHaveBeenCalled();

    unmount(component);
  });
});
