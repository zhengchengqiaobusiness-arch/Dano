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

function submittedFormBlock(): ToolContentBlock {
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
});
