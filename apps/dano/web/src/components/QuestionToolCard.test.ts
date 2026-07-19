/** @vitest-environment happy-dom */

import { mount, tick, unmount } from "svelte";
import { describe, expect, it, vi } from "vitest";
import type { ToolContentBlock } from "../utils/transcript";
import QuestionToolCard from "./QuestionToolCard.svelte";
import questionToolCardSource from "./QuestionToolCard.svelte?raw";

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

function revisingMultiFormBlock(): ToolContentBlock {
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
        questions: [
          { id: "reason", kind: "text", question: "请假原因？" },
          {
            id: "departure_date",
            kind: "date",
            question: "出发日期是哪天？",
            dateFormat: "yyyy-MM-dd",
          },
          {
            id: "activity_type",
            kind: "single",
            question: "活动类型？",
            options: [
              { id: "training", label: "培训" },
              { id: "team_building", label: "团建" },
            ],
          },
        ],
        answer: {
          reason: "家庭事务",
          departure_date: "2026-07-25",
          activity_type: "team_building",
        },
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
  return block;
}

function revisingSingleFormBlock(): ToolContentBlock {
  const block = revisingMultiFormBlock();
  block.formInteraction!.forms = block.formInteraction!.forms.slice(0, 1);
  return block;
}

function pendingGroupedFormBlock(): ToolContentBlock {
  return {
    kind: "tool",
    toolName: "ask_user_question",
    toolCallId: "grouped-form-1",
    toolArgs: {},
    argumentsText: "",
    toolStatus: "pending",
    questionRequest: {
      batch: true,
      title: "团建行程信息",
      questions: [
        { id: "destination", kind: "text", question: "目的地？" },
        { id: "people", kind: "text", question: "参与人数？" },
      ],
    },
  };
}

function pendingSingleQuestionBlock(): ToolContentBlock {
  return {
    kind: "tool",
    toolName: "ask_user_question",
    toolCallId: "single-question-1",
    toolArgs: {},
    argumentsText: "",
    toolStatus: "pending",
    questionRequest: {
      batch: false,
      id: "destination",
      kind: "text",
      question: "目的地？",
    },
  };
}

describe("QuestionToolCard", () => {
  it("requests center focus for the live grouped form only after presentation succeeds", async () => {
    vi.useFakeTimers();
    const present = vi.fn(async () => ({ success: true } as never));
    const focusChange = vi.fn();
    const target = document.createElement("div");
    const component = mount(QuestionToolCard, {
      target,
      props: {
        block: pendingGroupedFormBlock(),
        active: true,
        onPresent: present,
        onRespond: present,
        onRevise: present,
        onSubmitRevision: present,
        onFocusChange: focusChange,
      },
    });
    try {
      expect(focusChange).not.toHaveBeenCalled();

      await vi.advanceTimersByTimeAsync(400);
      await tick();
      await tick();

      const card = target.querySelector("article");
      expect(present).toHaveBeenCalledWith("grouped-form-1");
      expect(focusChange).toHaveBeenCalledWith({
        toolCallId: "grouped-form-1",
        element: card,
      });

      target.querySelector("form")?.dispatchEvent(
        new SubmitEvent("submit", { bubbles: true, cancelable: true }),
      );
      await vi.advanceTimersByTimeAsync(0);
      await tick();
      await tick();

      expect(focusChange).toHaveBeenLastCalledWith({
        toolCallId: "grouped-form-1",
        element: card,
      });
    } finally {
      unmount(component);
      vi.useRealTimers();
    }
  });

  it("keeps an ordinary single question inline after presentation", async () => {
    vi.useFakeTimers();
    const present = vi.fn(async () => ({ success: true } as never));
    const focusChange = vi.fn();
    const target = document.createElement("div");
    const component = mount(QuestionToolCard, {
      target,
      props: {
        block: pendingSingleQuestionBlock(),
        active: true,
        onPresent: present,
        onRespond: present,
        onRevise: present,
        onSubmitRevision: present,
        onFocusChange: focusChange,
      },
    });
    try {
      await vi.advanceTimersByTimeAsync(400);
      await tick();
      await tick();

      expect(present).toHaveBeenCalledWith("single-question-1");
      expect(focusChange).not.toHaveBeenCalled();
    } finally {
      unmount(component);
      vi.useRealTimers();
    }
  });

  it("keeps a one-field questions array inline", async () => {
    vi.useFakeTimers();
    const present = vi.fn(async () => ({ success: true } as never));
    const focusChange = vi.fn();
    const block = pendingGroupedFormBlock();
    if (!block.questionRequest?.batch) throw new Error("expected grouped request");
    block.questionRequest.questions = block.questionRequest.questions.slice(0, 1);
    const target = document.createElement("div");
    const component = mount(QuestionToolCard, {
      target,
      props: {
        block,
        active: true,
        onPresent: present,
        onRespond: present,
        onRevise: present,
        onSubmitRevision: present,
        onFocusChange: focusChange,
      },
    });
    try {
      await vi.advanceTimersByTimeAsync(400);
      await tick();
      await tick();

      expect(present).toHaveBeenCalledWith("grouped-form-1");
      expect(focusChange).not.toHaveBeenCalled();
    } finally {
      unmount(component);
      vi.useRealTimers();
    }
  });

  it("ignores a presentation response that arrives after the card is destroyed", async () => {
    vi.useFakeTimers();
    let resolvePresentation: ((response: { success: true }) => void) | undefined;
    const present = vi.fn(() => new Promise<{ success: true }>(resolve => {
      resolvePresentation = resolve;
    }) as never);
    const focusChange = vi.fn();
    const target = document.createElement("div");
    const component = mount(QuestionToolCard, {
      target,
      props: {
        block: pendingGroupedFormBlock(),
        active: true,
        onPresent: present,
        onRespond: present,
        onRevise: present,
        onSubmitRevision: present,
        onFocusChange: focusChange,
      },
    });
    try {
      await vi.advanceTimersByTimeAsync(400);
      await tick();
      expect(present).toHaveBeenCalledOnce();

      unmount(component);
      resolvePresentation?.({ success: true });
      await tick();

      expect(focusChange).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

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

  it("waits for the confirmation presentation handshake before requesting focus", async () => {
    vi.useFakeTimers();
    let finishPresentation: ((response: { success: true }) => void) | undefined;
    const present = vi.fn(() => new Promise<{ success: true }>(resolve => {
      finishPresentation = resolve;
    }) as never);
    const focusChange = vi.fn();
    const target = document.createElement("div");
    const component = mount(QuestionToolCard, {
      target,
      props: {
        block: multiFormConfirmationBlock(),
        active: true,
        onPresent: present,
        onRespond: present,
        onRevise: present,
        onSubmitRevision: present,
        onFocusChange: focusChange,
      },
    });
    try {
      await vi.advanceTimersByTimeAsync(400);
      await tick();

      expect(present).toHaveBeenCalledWith("confirm-two");
      expect(focusChange).not.toHaveBeenCalled();

      finishPresentation?.({ success: true });
      await tick();
      await tick();

      expect(focusChange).toHaveBeenCalledWith({
        toolCallId: "confirm-two",
        element: target.querySelector("article"),
      });
    } finally {
      unmount(component);
      vi.useRealTimers();
    }
  });

  it("keeps the confirmation focused while Return to Modify projects revisions", async () => {
    vi.useFakeTimers();
    const present = vi.fn(async () => ({ success: true } as never));
    const revise = vi.fn(async () => ({
      success: true,
      data: revisingMultiFormBlock().formInteraction,
    } as never));
    const focusChange = vi.fn();
    const target = document.createElement("div");
    const component = mount(QuestionToolCard, {
      target,
      props: {
        block: multiFormConfirmationBlock(),
        active: true,
        onPresent: present,
        onRespond: present,
        onRevise: revise,
        onSubmitRevision: present,
        onFocusChange: focusChange,
      },
    });
    try {
      await vi.advanceTimersByTimeAsync(400);
      await tick();
      await tick();

      const card = target.querySelector("article");
      [...target.querySelectorAll<HTMLButtonElement>("button")]
        .find(button => button.textContent?.trim() === "返回修改")
        ?.click();
      await tick();
      await tick();

      expect(revise).toHaveBeenCalledWith("confirm-two", 1);
      expect(target.querySelectorAll('input[type="text"]')).toHaveLength(2);
      expect(focusChange).toHaveBeenLastCalledWith({
        toolCallId: "confirm-two",
        element: card,
      });
    } finally {
      unmount(component);
      vi.useRealTimers();
    }
  });

  it("presents a confirmation without locally authorizing actions before projection", async () => {
    vi.useFakeTimers();
    const response = vi.fn(async () => ({ success: true } as never));
    const focusChange = vi.fn();
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
        onFocusChange: focusChange,
      },
    });
    try {
      await vi.advanceTimersByTimeAsync(400);
      await tick();

      expect(response).toHaveBeenCalledWith("confirm-two");
      expect(target.querySelectorAll(".question-actions button")).toHaveLength(0);
      expect(focusChange).not.toHaveBeenCalled();
    } finally {
      unmount(component);
      vi.useRealTimers();
    }
  });

  it("bootstraps a live confirmation when lifecycle projection races tool execution", async () => {
    vi.useFakeTimers();
    const block = multiFormConfirmationBlock();
    block.formInteraction = undefined;
    block.questionState = undefined;
    const response = vi.fn(async () => ({
      success: true,
      data: {
        interactionId: "confirm-two",
        state: "awaiting_confirmation",
        revision: 1,
        allowedActions: ["cancel", "return_modify", "confirm"],
        forms: [],
      },
    } as never));
    const focusChange = vi.fn();
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
        onFocusChange: focusChange,
      },
    });
    try {
      await vi.advanceTimersByTimeAsync(400);
      await tick();
      await tick();

      expect(response).toHaveBeenCalledWith("confirm-two");
      expect(target.textContent).toContain("返回修改");
      expect(target.textContent).toContain("取消");
      expect(target.textContent).toContain("确认");
      expect(focusChange).toHaveBeenCalledWith({
        toolCallId: "confirm-two",
        element: target.querySelector("article"),
      });
    } finally {
      unmount(component);
      vi.useRealTimers();
    }
  });

  it("does not bootstrap a historical confirmation during a later turn", async () => {
    const block = multiFormConfirmationBlock();
    block.formInteraction = undefined;
    block.questionState = undefined;
    const response = vi.fn(async () => ({ success: true } as never));
    const target = document.createElement("div");
    const component = mount(QuestionToolCard, {
      target,
      props: {
        block,
        active: false,
        onPresent: response,
        onRespond: response,
        onRevise: response,
        onSubmitRevision: response,
      },
    });
    await tick();

    expect(response).not.toHaveBeenCalled();
    expect(target.querySelectorAll(".question-actions button")).toHaveLength(0);
    unmount(component);
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

  it.each([
    [
      "confirmed confirmation",
      () => {
        const block = multiFormConfirmationBlock();
        block.toolStatus = "success";
        block.formInteraction = {
          interactionId: "confirm-two",
          state: "confirmed",
          revision: 2,
          allowedActions: [],
          forms: [],
        };
        return block;
      },
      ".desktop-question-result .question-form-scroll-region",
    ],
    [
      "submitted source form",
      () => submittedFormBlock("confirmed"),
      ".answered-source-form .question-form-scroll-region",
    ],
    [
      "submitted source mobile summary",
      () => submittedFormBlock("confirmed"),
      ".mobile-answered-result.question-form-scroll-region",
    ],
  ] as const)(
    "caps inline %s content height and enables overflow scrolling",
    async (_label, blockFactory, selector) => {
      const response = vi.fn(async () => ({ success: true } as never));
      const target = document.createElement("div");
      const component = mount(QuestionToolCard, {
        target,
        props: {
          block: blockFactory(),
          active: false,
          onPresent: response,
          onRespond: response,
          onRevise: response,
          onSubmitRevision: response,
        },
      });
      await tick();

      const scrollRegion = target.querySelector<HTMLElement>(selector);
      expect(scrollRegion).not.toBeNull();
      expect(target.querySelector("article")?.classList).toContain("inline-readonly-card");

      unmount(component);
    },
  );

  it("defines the inline read-only form height and overflow contract", () => {
    const rule = questionToolCardSource.match(
      /\.question-card\.inline-readonly-card \.question-form-scroll-region \{([\s\S]*?)\n  \}/,
    )?.[1];

    expect(rule).toContain("max-height: 420px;");
    expect(rule).toContain("overflow-y: auto;");
    expect(rule).toContain("overscroll-behavior: contain;");
  });

  it("submits all projected form revisions while preserving unchanged values", async () => {
    const response = vi.fn(async () => ({ success: true } as never));
    const submitRevision = vi.fn(async () => ({
      success: true,
      data: {
        ...multiFormConfirmationBlock().formInteraction,
        revision: 3,
      },
    } as never));
    const focusChange = vi.fn();
    const block = revisingMultiFormBlock();
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
        onFocusChange: focusChange,
      },
    });
    await tick();

    expect(focusChange).toHaveBeenCalledWith({
      toolCallId: "confirm-two",
      element: target.querySelector("article"),
    });

    const inputs = target.querySelectorAll<HTMLInputElement>('input[type="text"]');
    expect([...inputs].map(input => input.value)).toEqual(["家庭事务", "上海"]);
    inputs[0]!.value = "照顾家人";
    inputs[0]!.dispatchEvent(new Event("input", { bubbles: true }));
    await tick();
    target.querySelector("form")?.dispatchEvent(
      new SubmitEvent("submit", { bubbles: true, cancelable: true }),
    );
    await tick();
    await tick();
    await tick();

    expect(submitRevision).toHaveBeenCalledWith("confirm-two", 2, {
      "form-a": {
        reason: "照顾家人",
        departure_date: "2026-07-25",
        activity_type: "team_building",
      },
      "form-b": { destination: "上海" },
    });
    expect(target.textContent).toContain("请假申请");
    expect(target.textContent).toContain("出差申请");
    expect(target.querySelectorAll('input[type="text"]')).toHaveLength(0);
    expect(target.textContent).toContain("返回修改");
    expect(focusChange).toHaveBeenLastCalledWith({
      toolCallId: "confirm-two",
      element: target.querySelector("article"),
    });

    unmount(component);
  });

  it("shows each Form Revision field label once while preserving accessible labels", async () => {
    const response = vi.fn(async () => ({ success: true } as never));
    const target = document.createElement("div");
    const component = mount(QuestionToolCard, {
      target,
      props: {
        block: revisingMultiFormBlock(),
        active: true,
        onPresent: response,
        onRespond: response,
        onRevise: response,
        onSubmitRevision: response,
      },
    });
    await tick();

    expect(target.querySelectorAll(".question-text")).toHaveLength(4);
    expect(
      target.querySelector('label.sr-only[for="question-confirm-two-form-a:departure_date-trigger"]')
        ?.textContent,
    ).toBe("出发日期是哪天？");
    expect(target.querySelector("fieldset.single-options legend.sr-only")?.textContent)
      .toBe("活动类型？");

    unmount(component);
  });

  it("shows the edit heading and individual form titles while revising", async () => {
    const response = vi.fn(async () => ({ success: true } as never));
    const target = document.createElement("div");
    const component = mount(QuestionToolCard, {
      target,
      props: {
        block: revisingMultiFormBlock(),
        active: true,
        onPresent: response,
        onRespond: response,
        onRevise: response,
        onSubmitRevision: response,
      },
    });
    await tick();

    expect(target.querySelector(".question-form-title")?.textContent).toBe("修改");
    expect(
      [...target.querySelectorAll(".revision-form-title")]
        .map(title => title.textContent?.trim()),
    ).toEqual(["请假申请", "出差申请"]);

    unmount(component);
  });

  it("keeps the title and actions outside the focused form scroll region", async () => {
    const response = vi.fn(async () => ({ success: true } as never));
    const target = document.createElement("div");
    const component = mount(QuestionToolCard, {
      target,
      props: {
        block: revisingSingleFormBlock(),
        active: true,
        onPresent: response,
        onRespond: response,
        onRevise: response,
        onSubmitRevision: response,
      },
    });
    await tick();

    const form = target.querySelector("form");
    const scrollRegion = form?.querySelector(":scope > .question-form-scroll-region");
    expect(scrollRegion).not.toBeNull();
    expect(scrollRegion?.querySelector('input[type="text"]')).not.toBeNull();
    expect(form?.querySelector(":scope > .question-form-title")?.textContent).toBe("修改");
    expect(scrollRegion?.querySelector(".revision-form-title")?.textContent).toBe("请假申请");
    expect(scrollRegion?.querySelector(".question-actions")).toBeNull();
    expect(form?.querySelector(":scope > .question-actions")).not.toBeNull();

    unmount(component);
  });

  it("keeps confirmation heading and actions outside its summary scroll region", async () => {
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
    await tick();

    const summary = target.querySelector(".desktop-question-result");
    const scrollRegion = summary?.querySelector(":scope > .question-form-scroll-region");
    expect(summary?.querySelector(":scope > .submitted-header h3")?.textContent)
      .toBe("确认 2 份表单");
    expect(scrollRegion?.querySelector(".confirmation-form")).not.toBeNull();
    expect(scrollRegion?.querySelector(".submitted-header")).toBeNull();
    expect(scrollRegion?.querySelector(".question-actions")).toBeNull();
    expect(target.querySelector("article > .question-actions")).not.toBeNull();

    unmount(component);
  });

  it("keeps a new grouped form title and actions outside its scroll region", async () => {
    vi.useFakeTimers();
    const response = vi.fn(async () => ({ success: true } as never));
    const target = document.createElement("div");
    const component = mount(QuestionToolCard, {
      target,
      props: {
        block: pendingGroupedFormBlock(),
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
      await tick();

      const form = target.querySelector("form");
      const scrollRegion = form?.querySelector(":scope > .question-form-scroll-region");
      expect(target.querySelector(".question-form-title")?.textContent)
        .toBe("团建行程信息");
      expect(scrollRegion?.querySelector('input[type="text"]')).not.toBeNull();
      expect(scrollRegion?.querySelector(".question-form-title")).toBeNull();
      expect(scrollRegion?.querySelector(".question-actions")).toBeNull();
      expect(form?.querySelector(":scope > .question-actions")).not.toBeNull();
    } finally {
      unmount(component);
      vi.useRealTimers();
    }
  });

  it("recovers a stale revision from the authoritative response projection", async () => {
    const response = vi.fn(async () => ({ success: true } as never));
    const submitRevision = vi.fn(async () => ({
      success: false,
      error: "stale revision",
      data: {
        code: "stale_revision",
        interaction: {
          interactionId: "confirm-two",
          state: "awaiting_confirmation",
          revision: 3,
          allowedActions: ["cancel", "return_modify", "confirm"],
          forms: [
            {
              formId: "form-a",
              title: "请假申请",
              revision: 2,
              questions: [{ id: "reason", kind: "text", question: "请假原因？" }],
              answer: { reason: "另一标签页已修改" },
            },
            {
              formId: "form-b",
              title: "出差申请",
              revision: 2,
              questions: [{ id: "destination", kind: "text", question: "目的地？" }],
              answer: { destination: "上海" },
            },
          ],
        },
      },
    } as never));
    const target = document.createElement("div");
    const component = mount(QuestionToolCard, {
      target,
      props: {
        block: revisingMultiFormBlock(),
        active: true,
        onPresent: response,
        onRespond: response,
        onRevise: response,
        onSubmitRevision: submitRevision,
      },
    });
    await tick();
    target.querySelector("form")?.dispatchEvent(
      new SubmitEvent("submit", { bubbles: true, cancelable: true }),
    );
    await tick();
    await tick();

    expect(target.querySelectorAll('input[type="text"]')).toHaveLength(0);
    expect(target.textContent).toContain("返回修改");
    unmount(component);
  });

  it.each([
    ["confirmed", "已确认"],
    ["cancelled", "已取消"],
    ["interrupted", "已中断"],
  ] as const)(
    "exits focus when the authoritative response is %s",
    async (terminalState, terminalLabel) => {
      vi.useFakeTimers();
      const success = vi.fn(async () => ({ success: true } as never));
      const terminal = vi.fn(async () => ({
        success: false,
        error: "already terminal",
        data: {
          code: "already_terminal",
          interaction: {
            interactionId: "confirm-two",
            state: terminalState,
            revision: 2,
            allowedActions: [],
            forms: [],
          },
        },
      } as never));
      const focusChange = vi.fn();
      const target = document.createElement("div");
      const component = mount(QuestionToolCard, {
        target,
        props: {
          block: multiFormConfirmationBlock(),
          active: true,
          onPresent: success,
          onRespond: terminal,
          onRevise: success,
          onSubmitRevision: success,
          onFocusChange: focusChange,
        },
      });
      try {
        await vi.advanceTimersByTimeAsync(400);
        await tick();
        await tick();
        expect(focusChange).toHaveBeenCalledWith({
          toolCallId: "confirm-two",
          element: target.querySelector("article"),
        });
        [...target.querySelectorAll<HTMLButtonElement>("button")]
          .find(button => button.textContent?.trim() === "确认")
          ?.click();
        await tick();
        await tick();

        const actions = target.querySelectorAll<HTMLButtonElement>(
          ".question-actions button",
        );
        expect(actions).toHaveLength(1);
        expect(actions[0]?.textContent).toContain(terminalLabel);
        expect(actions[0]?.disabled).toBe(true);
        expect(focusChange).toHaveBeenLastCalledWith({
          toolCallId: "confirm-two",
          element: null,
        });
      } finally {
        unmount(component);
        vi.useRealTimers();
      }
    },
  );

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

  it("renders the latest revised answer when the Submitted Form returns", async () => {
    const response = vi.fn(async () => ({ success: true } as never));
    const block = submittedFormBlock("awaiting_confirmation");
    block.formInteraction!.revision = 3;
    block.formInteraction!.forms = [{
      formId: "form-1",
      title: "测试申请",
      revision: 2,
      questions: [{ id: "reason", kind: "text", question: "申请原因？" }],
      answer: { reason: "照顾家人" },
    }];
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

    expect(target.querySelector<HTMLInputElement>('input[type="text"]')?.value)
      .toBe("照顾家人");
    expect(target.textContent).toContain("待确认");
    unmount(component);
  });

  it("keeps an optional answer cleared by the latest revision", async () => {
    const response = vi.fn(async () => ({ success: true } as never));
    const block = submittedFormBlock("awaiting_confirmation");
    if (!block.questionRequest?.batch) throw new Error("expected grouped form");
    block.questionRequest.questions[0]!.default = "个人事务";
    block.formInteraction!.revision = 3;
    block.formInteraction!.forms = [{
      formId: "form-1",
      title: "测试申请",
      revision: 2,
      questions: [{
        id: "reason",
        kind: "text",
        question: "申请原因？",
        default: "个人事务",
      }],
      answer: {},
    }];
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

    expect(target.querySelector<HTMLInputElement>('input[type="text"]')?.value)
      .toBe("");
    unmount(component);
  });
});
