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
        forms: [],
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
      allowedActions: ["cancel", "return_modify", "confirm"],
      forms: [],
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
        onRevise: response,
        onSubmitRevision: response,
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
        onRevise: response,
        onSubmitRevision: response,
      },
    });
    try {
      await vi.advanceTimersByTimeAsync(400);
      await tick();

      expect(target.querySelectorAll("[data-form-id]")).toHaveLength(2);
      expect(target.textContent).toContain("请假申请");
      expect(target.textContent).toContain("出差申请");
      expect(target.textContent).toContain("返回修改");
      expect(target.querySelectorAll("button.question-button")).toHaveLength(3);
      expect(target.textContent).toContain("取消");
      expect(target.textContent).toContain("确认");
    } finally {
      unmount(component);
      vi.useRealTimers();
    }
  });

  it("presents a confirmation without locally authorizing actions before projection", async () => {
    vi.useFakeTimers();
    const response = vi.fn(async () => ({ success: true } as never));
    const block = multiFormConfirmationBlock();
    block.formInteraction = undefined;
    block.questionState = "awaiting_presentation";
    const target = document.createElement("div");
    const component = mount(QuestionToolCard, {
      target,
      props: {
        block,
        active: true,
        onPresent: response,
        onRespond: response,
        onRevise: response,
        onSubmitRevision: response,
      },
    });
    try {
      await vi.advanceTimersByTimeAsync(400);
      await tick();

      expect(response).toHaveBeenCalledWith("confirm-two");
      expect(target.querySelectorAll(".question-actions button")).toHaveLength(0);
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
        onRevise: response,
        onSubmitRevision: response,
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
      forms: [],
    };
    const target = document.createElement("div");
    const component = mount(QuestionToolCard, {
      target,
      props: {
        block,
        active: true,
        onPresent: response,
        onRespond: response,
        onRevise: response,
        onSubmitRevision: response,
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

  it("submits all projected form revisions while preserving unchanged values", async () => {
    const response = vi.fn(async () => ({ success: true } as never));
    const submitRevision = vi.fn(async () => ({ success: true } as never));
    const block = multiFormConfirmationBlock();
    block.formInteraction = {
      interactionId: "confirm-two",
      state: "revising",
      revision: 2,
      allowedActions: ["cancel", "submit_revision"],
      forms: [
        {
          formId: "form-a",
          title: "请假申请",
          revision: 2,
          questions: [{ id: "reason", kind: "text", question: "请假原因？" }],
          answer: { reason: "家庭事务" },
        },
        {
          formId: "form-b",
          title: "出差申请",
          revision: 2,
          questions: [{ id: "destination", kind: "text", question: "目的地？" }],
          answer: { destination: "上海" },
        },
      ],
    };
    const target = document.createElement("div");
    const component = mount(QuestionToolCard, {
      target,
      props: {
        block,
        active: true,
        onPresent: response,
        onRespond: response,
        onRevise: response,
        onSubmitRevision: submitRevision,
      },
    });
    await tick();

    const inputs = target.querySelectorAll<HTMLInputElement>('input[type="text"]');
    expect([...inputs].map(input => input.value)).toEqual(["家庭事务", "上海"]);
    inputs[0]!.value = "照顾家人";
    inputs[0]!.dispatchEvent(new Event("input", { bubbles: true }));
    await tick();
    target.querySelector("form")?.dispatchEvent(
      new SubmitEvent("submit", { bubbles: true, cancelable: true }),
    );
    await tick();

    expect(submitRevision).toHaveBeenCalledWith("confirm-two", 2, {
      "form-a": { reason: "照顾家人" },
      "form-b": { destination: "上海" },
    });
    expect(target.textContent).toContain("请假申请");
    expect(target.textContent).toContain("出差申请");

    unmount(component);
  });

  it("omits old Submitted Forms while their interaction is revising", async () => {
    const response = vi.fn(async () => ({ success: true } as never));
    const block = submittedFormBlock();
    block.formInteraction = {
      interactionId: "confirm-form-1",
      state: "revising",
      revision: 2,
      allowedActions: ["cancel", "submit_revision"],
      forms: [],
    };
    const target = document.createElement("div");
    const component = mount(QuestionToolCard, {
      target,
      props: {
        block,
        active: true,
        onPresent: response,
        onRespond: response,
        onRevise: response,
        onSubmitRevision: response,
      },
    });
    await tick();

    expect(target.querySelector("article")).toBeNull();
    unmount(component);
  });
});
