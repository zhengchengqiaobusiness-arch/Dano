/** @vitest-environment happy-dom */

import { mount, tick, unmount } from "svelte";
import { createClassComponent } from "svelte/legacy";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import ChatTranscript from "./ChatTranscript.svelte";
import chatTranscriptSource from "./ChatTranscript.svelte?raw";
import activityRowSource from "./ToolActivityRow.svelte?raw";

vi.mock("../composables/bridgeStore.svelte", () => ({
  abortGeneration: vi.fn(),
  answerQuestion: vi.fn(),
  cancelQuestionRevision: vi.fn(),
  getBridgeClientId: () => null,
  presentQuestion: vi.fn(),
  reviseQuestion: vi.fn(),
  submitQuestionRevision: vi.fn(),
}));

const originalAnimate = Element.prototype.animate;

beforeAll(() => {
  document.body.classList.add("app-shell");
  Element.prototype.animate = vi.fn(() => ({
    cancel: vi.fn(),
    finished: Promise.resolve(),
  })) as never;
});

afterAll(() => {
  document.body.classList.remove("app-shell");
  Element.prototype.animate = originalAnimate;
});

function assistantToolCall(
  id: string,
  name: string,
  args: Record<string, unknown>,
  result?: { text: string; isError?: boolean },
) {
  return {
    id: `assistant-${id}`,
    role: "assistant",
    content: [
      {
        type: "toolCall",
        id,
        name,
        arguments: args,
      },
      ...(result
        ? [{
            type: "toolResult",
            text: result.text,
            ...(result.isError ? { isError: true } : {}),
            sourceMessageId: `result-${id}`,
          }]
        : []),
    ],
  };
}

describe("ChatTranscript completed Assistant Turn actions", () => {
  it("shows one copy and Chinese timestamp container below a completed direct answer", async () => {
    const completedAt = new Date(2026, 6, 27, 14, 32).toISOString();
    const target = document.createElement("div");
    document.body.appendChild(target);
    const component = mount(ChatTranscript, {
      target,
      props: {
        messages: [
          {
            id: "user-1",
            role: "user",
            content: "请分析配置更新问题",
            timestamp: "2026-07-27T14:31:00+08:00",
          },
          {
            id: "assistant-final",
            role: "assistant",
            content: "配置只在启动时读取。",
            timestamp: completedAt,
          },
        ] as never,
      },
    });

    try {
      await tick();
      const actions = target.querySelectorAll(".assistant-turn-actions");
      expect(actions).toHaveLength(1);
      expect(actions[0]?.textContent).toContain("7月27日 14:32");
      expect(
        actions[0]?.querySelector('button[aria-label="复制消息"]'),
      ).not.toBeNull();
      expect(
        actions[0]?.querySelector('button[aria-label="复制消息"]')?.hasAttribute("title"),
      ).toBe(false);
      expect(chatTranscriptSource).toContain(
        '<Tooltip.Content side="bottom">{t("common.copy")}</Tooltip.Content>',
      );
      expect(chatTranscriptSource.match(/<Tooltip\.Content side="bottom">/g)).toHaveLength(3);
    } finally {
      await unmount(component);
      target.remove();
    }
  });

  it("includes the year for a completed answer outside the current year", async () => {
    const previousYear = new Date().getFullYear() - 1;
    const completedAt = new Date(previousYear, 6, 27, 14, 32).toISOString();
    const target = document.createElement("div");
    document.body.appendChild(target);
    const component = mount(ChatTranscript, {
      target,
      props: {
        messages: [
          { id: "user-1", role: "user", content: "历史问题" },
          {
            id: "assistant-final",
            role: "assistant",
            content: "历史回答",
            timestamp: completedAt,
          },
        ] as never,
      },
    });

    try {
      await tick();
      expect(target.querySelector(".assistant-turn-actions time")?.textContent)
        .toBe(`${previousYear}年7月27日 14:32`);
    } finally {
      await unmount(component);
      target.remove();
    }
  });

  it("formats the timestamp with the configured Dano interface locale", async () => {
    const completedAt = new Date(new Date().getFullYear(), 6, 27, 14, 32);
    const originalConfig = window.__PI_WEB_CONFIG__;
    window.__PI_WEB_CONFIG__ = { ...originalConfig, locale: "en-US" };
    const target = document.createElement("div");
    document.body.appendChild(target);
    const component = mount(ChatTranscript, {
      target,
      props: {
        messages: [
          { id: "user-1", role: "user", content: "Historical question" },
          {
            id: "assistant-final",
            role: "assistant",
            content: "Historical answer",
            timestamp: completedAt.toISOString(),
          },
        ] as never,
      },
    });

    try {
      await tick();
      const expected = new Intl.DateTimeFormat("en-US", {
        month: "long",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        hourCycle: "h23",
      }).format(completedAt);
      expect(target.querySelector(".assistant-turn-actions time")?.textContent)
        .toBe(expected);
    } finally {
      await unmount(component);
      target.remove();
      window.__PI_WEB_CONFIG__ = originalConfig;
    }
  });

  it("shows actions when a paginated transcript starts inside a completed turn", async () => {
    const target = document.createElement("div");
    document.body.appendChild(target);
    const component = mount(ChatTranscript, {
      target,
      props: {
        messages: [
          { id: "assistant-progress", role: "assistant", content: "我先检查。" },
          assistantToolCall("read-1", "read", {}, { text: "done" }),
          {
            id: "assistant-final",
            role: "assistant",
            content: "检查完成。",
            timestamp: "2026-07-27T14:32:00+08:00",
          },
        ] as never,
      },
    });

    try {
      await tick();
      expect(target.querySelectorAll(".assistant-turn-actions")).toHaveLength(1);
      expect(
        target.querySelector('[data-message-id="assistant-final"] .assistant-turn-actions'),
      ).not.toBeNull();
    } finally {
      await unmount(component);
      target.remove();
    }
  });

  it("copies only the final answer Markdown from a completed tool-using turn", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    const originalClipboard = navigator.clipboard;
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    const target = document.createElement("div");
    document.body.appendChild(target);
    const component = mount(ChatTranscript, {
      target,
      props: {
        messages: [
          { id: "user-1", role: "user", content: "检查配置" },
          {
            id: "assistant-final",
            role: "assistant",
            timestamp: "2026-07-27T14:32:00+08:00",
            content: [
              { type: "thinking", thinking: "读取内部配置" },
              { type: "text", text: "我先检查。" },
              { type: "toolCall", id: "read-1", name: "read", arguments: {} },
              { type: "toolResult", text: "SECRET=value", sourceMessageId: "result-1" },
              { type: "text", text: "## 结论\n\n配置只在启动时读取。" },
            ],
          },
        ] as never,
      },
    });

    try {
      await tick();
      target.querySelector<HTMLButtonElement>(
        ".assistant-turn-actions button",
      )?.click();
      await tick();
      expect(writeText).toHaveBeenCalledWith(
        "## 结论\n\n配置只在启动时读取。",
      );
      await vi.waitFor(() => {
        expect(
          target.querySelector(".assistant-turn-actions")?.classList.contains("copied"),
        ).toBe(true);
      });
    } finally {
      await unmount(component);
      target.remove();
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: originalClipboard,
      });
    }
  });

  it("does not show actions while the final answer is still streaming", async () => {
    const target = document.createElement("div");
    document.body.appendChild(target);
    const component = mount(ChatTranscript, {
      target,
      props: {
        isStreaming: true,
        messages: [
          { id: "user-1", role: "user", content: "继续" },
          {
            id: "assistant-streaming",
            role: "assistant",
            content: "尚未完成",
            timestamp: "2026-07-27T14:32:00+08:00",
          },
        ] as never,
      },
    });

    try {
      await tick();
      expect(target.querySelector(".assistant-turn-actions")).toBeNull();
    } finally {
      await unmount(component);
      target.remove();
    }
  });

  it("does not expose an earlier partial answer after the turn is aborted", async () => {
    const target = document.createElement("div");
    document.body.appendChild(target);
    const component = mount(ChatTranscript, {
      target,
      props: {
        messages: [
          { id: "user-1", role: "user", content: "继续" },
          { id: "assistant-partial", role: "assistant", content: "部分回答" },
          {
            id: "assistant-aborted",
            role: "assistant",
            content: [],
            stopReason: "aborted",
            timestamp: "2026-07-27T14:32:00+08:00",
          },
        ] as never,
      },
    });

    try {
      await tick();
      expect(target.querySelector(".assistant-turn-actions")).toBeNull();
    } finally {
      await unmount(component);
      target.remove();
    }
  });

  it("does not show actions for length-truncated or tool-use continuation messages", async () => {
    const target = document.createElement("div");
    document.body.appendChild(target);
    const component = mount(ChatTranscript, {
      target,
      props: {
        messages: [
          { id: "user-length", role: "user", content: "详细回答" },
          {
            id: "assistant-length",
            role: "assistant",
            content: "尚未输出完整的回答",
            stopReason: "length",
          },
          { id: "user-tool", role: "user", content: "检查后回答" },
          {
            id: "assistant-tool-use",
            role: "assistant",
            stopReason: "toolUse",
            content: [
              { type: "toolCall", id: "read-1", name: "read", arguments: {} },
              { type: "toolResult", text: "done", sourceMessageId: "result-1" },
              { type: "text", text: "仍需继续处理。" },
            ],
          },
        ] as never,
      },
    });

    try {
      await tick();
      expect(target.querySelector(".assistant-turn-actions")).toBeNull();
    } finally {
      await unmount(component);
      target.remove();
    }
  });

  it("keeps copy available when the completed answer timestamp is missing or invalid", async () => {
    const target = document.createElement("div");
    document.body.appendChild(target);
    const component = mount(ChatTranscript, {
      target,
      props: {
        messages: [
          { id: "user-1", role: "user", content: "第一问" },
          { id: "assistant-1", role: "assistant", content: "第一答" },
          { id: "user-2", role: "user", content: "第二问" },
          {
            id: "assistant-2",
            role: "assistant",
            content: "第二答",
            timestamp: "not-a-timestamp",
          },
        ] as never,
      },
    });

    try {
      await tick();
      const actions = target.querySelectorAll(".assistant-turn-actions");
      expect(actions).toHaveLength(2);
      expect(target.querySelectorAll(".assistant-turn-actions button")).toHaveLength(2);
      expect(target.querySelector(".assistant-turn-actions time")).toBeNull();
    } finally {
      await unmount(component);
      target.remove();
    }
  });

  it("clears copied feedback when the displayed session changes", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    const originalClipboard = navigator.clipboard;
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    const messages = [
      { id: "user-1", role: "user", content: "问题" },
      { id: "assistant-1", role: "assistant", content: "回答" },
    ] as never;
    const target = document.createElement("div");
    document.body.appendChild(target);
    const component = createClassComponent({
      component: ChatTranscript,
      target,
      props: { sessionPath: "session-1", messages },
    });

    try {
      await tick();
      target.querySelector<HTMLButtonElement>(
        ".assistant-turn-actions button",
      )?.click();
      await vi.waitFor(() => {
        expect(target.querySelector(".assistant-turn-actions.copied")).not.toBeNull();
      });

      component.$set({ sessionPath: "session-2", messages });
      await tick();
      expect(target.querySelector(".assistant-turn-actions.copied")).toBeNull();
    } finally {
      component.$destroy();
      target.remove();
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: originalClipboard,
      });
    }
  });

  it("attaches one action container only to the last textual answer in a multi-message turn", async () => {
    const target = document.createElement("div");
    document.body.appendChild(target);
    const component = mount(ChatTranscript, {
      target,
      props: {
        messages: [
          { id: "user-1", role: "user", content: "检查后回答" },
          { id: "assistant-progress", role: "assistant", content: "我先检查。" },
          assistantToolCall("read-1", "read", {}, { text: "done" }),
          {
            id: "assistant-final",
            role: "assistant",
            content: "检查完成。",
            timestamp: "2026-07-27T14:32:00+08:00",
          },
        ] as never,
      },
    });

    try {
      await tick();
      expect(target.querySelectorAll(".assistant-turn-actions")).toHaveLength(1);
      expect(
        target.querySelector('[data-message-id="assistant-final"] .assistant-turn-actions'),
      ).not.toBeNull();
      expect(
        target.querySelector('[data-message-id="assistant-progress"] .assistant-turn-actions'),
      ).toBeNull();

      target.querySelector<HTMLButtonElement>(".process-summary-toggle")?.click();
      await tick();
      const progressRow = target.querySelector<HTMLElement>(
        '[data-message-id="assistant-progress"]',
      );
      expect(progressRow).not.toBeNull();
      progressRow?.dispatchEvent(new PointerEvent("pointerover", { bubbles: true }));
      await tick();
      expect(
        target.querySelector(".assistant-turn-actions")?.classList.contains("interaction-active"),
      ).toBe(true);

      progressRow?.dispatchEvent(new PointerEvent("pointerout", { bubbles: true }));
      await tick();
      expect(
        target.querySelector(".assistant-turn-actions")?.classList.contains("interaction-active"),
      ).toBe(false);

      const copyButton = target.querySelector<HTMLButtonElement>(
        ".assistant-turn-actions button",
      );
      copyButton?.focus();
      await tick();
      expect(
        target.querySelector(".assistant-turn-actions")?.classList.contains("interaction-active"),
      ).toBe(true);

      copyButton?.blur();
      await tick();
      expect(
        target.querySelector(".assistant-turn-actions")?.classList.contains("interaction-active"),
      ).toBe(false);
    } finally {
      await unmount(component);
      target.remove();
    }
  });
});

describe("ChatTranscript assistant pending indicator", () => {
  it("marks post-tool waiting for delayed presentation", async () => {
    const target = document.createElement("div");
    document.body.appendChild(target);
    const component = mount(ChatTranscript, {
      target,
      props: {
        isStreaming: true,
        messages: [
          { id: "user-1", role: "user", content: "hello" },
          {
            id: "assistant-1",
            role: "assistant",
            content: [
              { type: "toolCall", id: "tool-1", name: "read", arguments: {} },
              { type: "toolResult", text: "done", sourceMessageId: "tool-result-1" },
            ],
          },
        ] as never,
      },
    });

    try {
      await tick();
      const pendingRow = target.querySelector<HTMLElement>(
        ".assistant-pending-row",
      );
      expect(
        pendingRow?.classList.contains("assistant-pending-delayed"),
      ).toBe(true);
      expect(chatTranscriptSource).toContain("visibility: hidden;");
      expect(chatTranscriptSource).toContain(
        "animation: assistant-pending-reveal 0s linear 500ms forwards;",
      );
    } finally {
      await unmount(component);
      target.remove();
    }
  });
});

describe("ChatTranscript compaction presentation", () => {
  it("does not display persistent compaction summaries", async () => {
    const target = document.createElement("div");
    document.body.appendChild(target);
    const component = mount(ChatTranscript, {
      target,
      props: {
        messages: [
          {
            id: "compaction-1",
            role: "system",
            content: [
              {
                type: "compaction",
                summary: "保留了当前任务、关键决定与后续步骤。",
                tokensBefore: 22400,
                firstKeptEntryId: "message-1",
              },
            ],
          },
        ] as never,
      },
    });

    try {
      await tick();
      expect(target.querySelector('[data-system-type="compaction"]')).toBeNull();
      expect(target.textContent).not.toContain("保留了当前任务");
      expect(target.textContent).not.toContain("上下文已压缩");
    } finally {
      await unmount(component);
      target.remove();
    }
  });

  it.each([
    ["threshold", "正在压缩上下文…"],
    ["overflow", "上下文已满，正在压缩并重试…"],
  ] as const)("shows the %s transient status", async (compactionReason, label) => {
    const target = document.createElement("div");
    document.body.appendChild(target);
    const component = mount(ChatTranscript, {
      target,
      props: {
        isCompacting: true,
        compactionReason,
        messages: [{ id: "user-1", role: "user", content: "继续" }] as never,
      },
    });

    try {
      await tick();
      expect(target.querySelector('[data-compaction-status="true"]')?.textContent)
        .toContain(label);
    } finally {
      await unmount(component);
      target.remove();
    }
  });
});

describe("ChatTranscript Activity Trail", () => {
  it.each(["inline", "orphan"])("expands %s read images and opens the attachment preview", async (projection) => {
    const images = [
      { type: "image", data: "Zmlyc3Q=", mimeType: "image/png" },
      { type: "image", data: "c2Vjb25k", mimeType: "image/png" },
    ];
    const result = {
      id: "result-images",
      role: "toolResult",
      toolCallId: "read-images",
      toolName: "read",
      content: images,
    };
    const messages = [
      { id: "user-1", role: "user", content: "查看图片" },
      ...(projection === "inline"
        ? [{
            id: "assistant-images",
            role: "assistant",
            content: [
              { type: "toolCall", id: "read-images", name: "read", arguments: { path: "/private/docs/chart.png" } },
              { type: "toolResult", content: images, sourceMessageId: result.id },
            ],
          }]
        : [result]),
      { id: "answer", role: "assistant", content: "已查看。" },
    ];
    const target = document.createElement("div");
    document.body.appendChild(target);
    const showModal = vi.spyOn(HTMLDialogElement.prototype, "showModal")
      .mockImplementation(function (this: HTMLDialogElement) {
        this.open = true;
        this.querySelector<HTMLButtonElement>("button")?.focus();
      });
    const component = createClassComponent({
      component: ChatTranscript,
      target,
      props: { messages: messages as never },
    });

    try {
      await tick();
      const trigger = target.querySelector<HTMLButtonElement>(".tool-activity-trigger")!;
      expect(trigger.getAttribute("aria-expanded")).toBe("true");
      const thumbnails = target.querySelectorAll<HTMLButtonElement>(".tool-activity-image-button");
      expect(thumbnails).toHaveLength(2);
      thumbnails[1]!.focus();
      await tick();
      thumbnails[1]!.click();
      await tick();
      const preview = target.querySelector<HTMLDialogElement>(".file-preview-shell");
      expect(preview?.open).toBe(true);
      expect(preview?.querySelector("img")?.getAttribute("src"))
        .toBe("data:image/png;base64,c2Vjb25k");
      expect(preview?.querySelector('[aria-label="放大图片"]')).not.toBeNull();
      expect(target.querySelector(".image-lightbox-shell")).toBeNull();
      expect(showModal).toHaveBeenCalledOnce();
      preview?.querySelector<HTMLButtonElement>('[aria-label="最大化"]')?.click();
      await tick();
      expect(preview?.querySelector(".file-preview-dialog.maximized")).not.toBeNull();
      preview?.querySelector<HTMLButtonElement>('[aria-label="关闭"]')?.click();
      await tick();
      expect(target.querySelector(".file-preview-shell")).toBeNull();

      trigger.click();
      await tick();
      expect(trigger.getAttribute("aria-expanded")).toBe("false");
      component.$set({
        messages: [...messages, { id: "final", role: "assistant", content: "已查看。" }] as never,
      });
      await tick();
      expect(trigger.getAttribute("aria-expanded")).toBe("false");
      trigger.click();
      await tick();
      expect(trigger.getAttribute("aria-expanded")).toBe("true");
    } finally {
      component.$destroy();
      target.remove();
      showModal.mockRestore();
    }
  });

  it("expands a read when images arrive while keeping text reads collapsed", async () => {
    const target = document.createElement("div");
    document.body.appendChild(target);
    const pending = assistantToolCall("read-image", "read", { path: "/private/docs/chart.png" });
    const textRead = assistantToolCall("read-text", "read", { path: "/private/docs/notes.md" }, { text: "notes" });
    const component = createClassComponent({
      component: ChatTranscript,
      target,
      props: { messages: [pending, textRead] as never, isStreaming: true },
    });
    const expansion = () => [...target.querySelectorAll(".tool-activity-trigger")]
      .map(trigger => trigger.getAttribute("aria-expanded"));
    try {
      await tick();
      expect(expansion()).toEqual(["false", "false"]);
      component.$set({
        messages: [{
          ...pending,
          content: [...pending.content, {
            type: "toolResult",
            content: [{ type: "image", data: "aW1hZ2U=", mimeType: "image/png" }],
          }],
        }, textRead] as never,
      });
      await tick();
      expect(expansion()).toEqual(["true", "false"]);
      expect(target.querySelectorAll(".tool-activity-image-button")).toHaveLength(1);
    } finally {
      component.$destroy();
      target.remove();
    }
  });

  it("shows a sanitized activity summary and controlled inline details", async () => {
    const target = document.createElement("div");
    document.body.appendChild(target);
    const component = mount(ChatTranscript, {
      target,
      props: {
        messages: [
          { id: "user-1", role: "user", content: "review" },
          {
            id: "assistant-1",
            role: "assistant",
            content: [
              {
                type: "toolCall",
                id: "read-1",
                name: "read",
                arguments: { path: "/private/company/contracts/采购合同.pdf" },
              },
              {
                type: "toolResult",
                text: "secret contract content",
                sourceMessageId: "result-1",
              },
            ],
          },
        ] as never,
      },
    });

    try {
      await tick();
      expect(target.textContent).toContain("已查阅资料");
      expect(target.textContent).not.toContain("/private/company");
      expect(target.textContent).not.toContain("secret contract content");

      const activity = target.querySelector<HTMLButtonElement>(
        ".tool-activity-trigger",
      );
      expect(activity).not.toBeNull();
      activity?.click();
      await tick();

      expect(target.textContent).toContain("采购合同.pdf");
      expect(target.textContent).not.toContain("/private/company");
      expect(target.textContent).not.toContain("secret contract content");
    } finally {
      await unmount(component);
      target.remove();
    }
  });

  it("keeps consecutive tool calls separate across assistant responses", async () => {
    const target = document.createElement("div");
    document.body.appendChild(target);
    const component = mount(ChatTranscript, {
      target,
      props: {
        isStreaming: true,
        messages: [
          { id: "user-1", role: "user", content: "review" },
          {
            id: "assistant-1",
            role: "assistant",
            content: [
              {
                type: "toolCall",
                id: "read-1",
                name: "read",
                arguments: { path: "/private/docs/合同.pdf" },
              },
              { type: "toolResult", text: "done", sourceMessageId: "result-1" },
            ],
          },
          {
            id: "assistant-2",
            role: "assistant",
            content: [
              { type: "thinking", thinking: "继续核对" },
              {
                type: "toolCall",
                id: "read-2",
                name: "read",
                arguments: { path: "/private/docs/补充协议.pdf" },
              },
            ],
          },
        ] as never,
      },
    });

    try {
      await tick();
      expect(target.querySelectorAll(".tool-activity")).toHaveLength(2);
      expect(target.querySelectorAll(".message-row.assistant")).toHaveLength(2);
      expect(target.textContent).toContain("已查阅资料");
      expect(target.textContent).toContain("正在查阅资料");
      expect(target.textContent).not.toContain("继续核对");
    } finally {
      await unmount(component);
      target.remove();
    }
  });

  it("keeps expansion state scoped to one tool call", async () => {
    const target = document.createElement("div");
    document.body.appendChild(target);
    const firstCall = assistantToolCall(
      "read-1",
      "read",
      { path: "/private/docs/合同.pdf" },
      { text: "done" },
    );
    const secondCall = assistantToolCall(
      "read-2",
      "read",
      { path: "/private/docs/补充协议.pdf" },
    );
    const component = createClassComponent({
      component: ChatTranscript,
      target,
      props: {
        messages: [
          { id: "user-1", role: "user", content: "review" },
          firstCall,
        ] as never,
      },
    });

    try {
      await tick();
      let triggers = [...target.querySelectorAll<HTMLButtonElement>(
        ".tool-activity-trigger",
      )];
      expect(triggers).toHaveLength(1);

      triggers[0]?.click();
      await tick();

      component.$set({
        messages: [
          { id: "user-1", role: "user", content: "review" },
          firstCall,
          secondCall,
        ] as never,
      });
      await tick();

      triggers = [...target.querySelectorAll<HTMLButtonElement>(
        ".tool-activity-trigger",
      )];
      expect(triggers.map(trigger => trigger.getAttribute("aria-expanded")))
        .toEqual(["true", "false"]);
      expect(target.textContent).toContain("合同.pdf");
      expect(target.textContent).not.toContain("补充协议.pdf");

      triggers[1]?.click();
      await tick();
      component.$set({
        messages: [
          { id: "user-1", role: "user", content: "review" },
          firstCall,
          assistantToolCall(
            "read-2",
            "read",
            { path: "/private/docs/补充协议.pdf" },
            { text: "done" },
          ),
        ] as never,
      });
      await tick();

      triggers = [...target.querySelectorAll<HTMLButtonElement>(
        ".tool-activity-trigger",
      )];
      expect(triggers.map(trigger => trigger.getAttribute("aria-expanded")))
        .toEqual(["true", "true"]);
    } finally {
      component.$destroy();
      target.remove();
    }
  });

  it("keeps every projected tool kind separated by invocation", async () => {
    const target = document.createElement("div");
    document.body.appendChild(target);
    const calls = [
      ["read", { path: "/private/docs/one.pdf" }],
      ["read", { path: "/private/docs/two.pdf" }],
      ["edit", { path: "/private/docs/one.md" }],
      ["write", { path: "/private/docs/two.md" }],
      ["curl", { url: "https://one.example.com" }],
      ["curl", { url: "https://two.example.com" }],
      ["bash", { command: "/bin/ls" }],
      ["bash", { command: "/usr/bin/pwd" }],
      ["internal_sync_v1", {}],
      ["internal_sync_v2", {}],
    ] as const;
    const component = mount(ChatTranscript, {
      target,
      props: {
        messages: [
          { id: "user-1", role: "user", content: "work" },
          ...calls.map(([name, args], index) =>
            assistantToolCall(
              `tool-${index}`,
              name,
              args,
              { text: "done" },
            )
          ),
        ] as never,
      },
    });

    try {
      await tick();
      expect([...target.querySelectorAll(".tool-activity-label")].map(
        label => label.textContent,
      )).toEqual([
        "已查阅资料",
        "已查阅资料",
        "已更新内容",
        "已更新内容",
        "已获取外部信息",
        "已获取外部信息",
        "已执行命令",
        "已执行命令",
        "已处理任务",
        "已处理任务",
      ]);
    } finally {
      await unmount(component);
      target.remove();
    }
  });

  it("keeps a failed call visible when a later call succeeds", async () => {
    const target = document.createElement("div");
    document.body.appendChild(target);
    const component = mount(ChatTranscript, {
      target,
      props: {
        messages: [
          { id: "user-1", role: "user", content: "review" },
          assistantToolCall(
            "read-failed",
            "read",
            { path: "/private/docs/合同.pdf" },
            { text: "failed", isError: true },
          ),
          assistantToolCall(
            "read-success",
            "read",
            { path: "/private/docs/合同.pdf" },
            { text: "done" },
          ),
        ] as never,
      },
    });

    try {
      await tick();
      expect([...target.querySelectorAll(".tool-activity-label")].map(
        label => label.textContent,
      )).toEqual(["资料查阅失败", "已查阅资料"]);
    } finally {
      await unmount(component);
      target.remove();
    }
  });

  it("marks only activity-only message rows for zero-gap layout", async () => {
    const target = document.createElement("div");
    document.body.appendChild(target);
    const component = mount(ChatTranscript, {
      target,
      props: {
        messages: [
          { id: "user-1", role: "user", content: "review" },
          {
            id: "assistant-read",
            role: "assistant",
            content: [
              {
                type: "toolCall",
                id: "read-1",
                name: "read",
                arguments: { path: "/private/docs/合同.pdf" },
              },
              { type: "toolResult", text: "done", sourceMessageId: "result-read" },
            ],
          },
          {
            id: "assistant-bash",
            role: "assistant",
            content: [
              {
                type: "toolCall",
                id: "bash-1",
                name: "bash",
                arguments: { command: "/bin/ls -la /private/docs" },
              },
              { type: "toolResult", text: "done", sourceMessageId: "result-bash" },
            ],
          },
          { id: "assistant-text", role: "assistant", content: "完成。" },
        ] as never,
      },
    });

    try {
      await tick();
      const rows = [...target.querySelectorAll(".message-row.assistant")];
      expect(rows).toHaveLength(3);
      expect(rows[0]?.classList.contains("activity-trail-row")).toBe(true);
      expect(rows[1]?.classList.contains("activity-trail-row")).toBe(true);
      expect(rows[2]?.classList.contains("activity-trail-row")).toBe(false);
    } finally {
      await unmount(component);
      target.remove();
    }
  });

  it("shows the failed action and its matching icon", async () => {
    const target = document.createElement("div");
    document.body.appendChild(target);
    const component = mount(ChatTranscript, {
      target,
      props: {
        messages: [
          { id: "user-1", role: "user", content: "list files" },
          {
            id: "assistant-1",
            role: "assistant",
            content: [
              {
                type: "toolCall",
                id: "bash-1",
                name: "bash",
                arguments: { command: "ls -l" },
              },
              {
                type: "toolResult",
                text: "restricted",
                details: {},
                isError: true,
                sourceMessageId: "result-1",
              },
            ],
          },
        ] as never,
      },
    });

    try {
      await tick();
      expect(target.textContent).toContain("命令执行失败");
      expect(target.querySelector(".tool-activity .lucide-square-terminal")).not.toBeNull();
      expect(target.querySelector(".tool-activity .lucide-circle-alert")).toBeNull();
      expect(target.textContent).not.toContain("restricted");

      target.querySelector<HTMLButtonElement>(".tool-activity-trigger")?.click();
      await tick();
      expect(target.textContent).toContain("restricted");
      expect(target.textContent).not.toContain("{}");
    } finally {
      await unmount(component);
      target.remove();
    }
  });

  it("keeps thinking and question cards outside the Activity Trail", async () => {
    const target = document.createElement("div");
    document.body.appendChild(target);
    const component = mount(ChatTranscript, {
      target,
      props: {
        isStreaming: true,
        messages: [
          { id: "user-1", role: "user", content: "help" },
          {
            id: "assistant-1",
            role: "assistant",
            content: [
              {
                type: "toolCall",
                id: "question-1",
                name: "ask_user_question",
                arguments: {},
                questionRequest: {
                  batch: true,
                  questions: [{ id: "confirm", kind: "confirm", question: "是否继续？" }],
                },
              },
            ],
          },
          {
            id: "assistant-2",
            role: "assistant",
            content: [{ type: "thinking", thinking: "正在判断需要确认的信息" }],
          },
        ] as never,
      },
    });

    try {
      await tick();
      expect(target.textContent).toContain("正在判断需要确认的信息");
      expect(target.textContent).toContain("问题已中断");
      expect(target.querySelectorAll(".tool-activity")).toHaveLength(0);
    } finally {
      await unmount(component);
      target.remove();
    }
  });

  it.each([
    ["duplicate_question_call", "同一响应中已有待处理的问题卡"],
    ["invalid_confirmation_source", "确认卡缺少已提交的分组表单来源"],
    ["question_presentation_timeout", "问题卡未能在规定时间内完成显示确认"],
    ["invalid_question_arguments", "问题卡字段参数未通过校验"],
  ])("shows the actual question failure cause for %s", async (code, expected) => {
    const target = document.createElement("div"); document.body.appendChild(target);
    const component = mount(ChatTranscript, { target, props: { messages: [{
      id: "failure", role: "assistant", content: [{ type: "toolCall", id: "q", name: "ask_user_question",
        arguments: {}, questionState: "retrying", questionError: { code, category: "validation", retryable: true, message: "private detail" } },
        { type: "toolResult", text: "private detail", isError: true, sourceMessageId: "result-q" }],
    }] as never } });
    try {
      await tick(); target.querySelector<HTMLButtonElement>(".tool-activity-trigger")?.click(); await tick();
      expect(target.textContent).toContain(expected);
      expect(target.textContent).not.toContain("private detail");
      expect(target.textContent).not.toContain("模型刚才生成的问题卡参数不符合要求");
    } finally { await unmount(component); target.remove(); }
  });

  it("hides recovered question-card retry failures", async () => {
    const target = document.createElement("div");
    document.body.appendChild(target);
    const retry = (id: string) => ({
      id: `assistant-${id}`,
      role: "assistant",
      content: [
        {
          type: "toolCall",
          id,
          name: "ask_user_question",
          arguments: {},
          questionState: "retrying",
        },
        {
          type: "toolResult",
          text: "invalid optional presentation metadata",
          isError: true,
          sourceMessageId: `result-${id}`,
        },
      ],
    });
    const component = mount(ChatTranscript, {
      target,
      props: {
        messages: [
          { id: "user-1", role: "user", content: "help" },
          retry("question-1"),
          retry("question-2"),
          {
            id: "assistant-success",
            role: "assistant",
            content: [{
              type: "toolCall",
              id: "question-3",
              name: "ask_user_question",
              arguments: {},
              questionRequest: {
                batch: true,
                questions: [{ id: "confirm", kind: "confirm", question: "是否继续？" }],
              },
            }],
          },
        ] as never,
      },
    });

    try {
      await tick();
      expect(target.textContent).not.toContain("问题卡调用失败");
      expect(target.textContent).not.toContain("invalid optional presentation metadata");
      expect(target.querySelectorAll(".tool-activity")).toHaveLength(0);
      expect(target.querySelector(".question-card")).not.toBeNull();
    } finally {
      await unmount(component);
      target.remove();
    }
  });

  it("shows a terminal question-card failure with its matching icon and useful detail", async () => {
    const originalConfig = window.__PI_WEB_CONFIG__;
    window.__PI_WEB_CONFIG__ = { productName: "测试助手" };
    const target = document.createElement("div");
    document.body.appendChild(target);
    const component = mount(ChatTranscript, {
      target,
      props: {
        messages: [
          { id: "user-1", role: "user", content: "help" },
          {
            id: "assistant-1",
            role: "assistant",
            content: [
              {
                type: "toolCall",
                id: "question-1",
                name: "ask_user_question",
                arguments: {},
                questionState: "terminal_failure",
              },
              {
                type: "toolResult",
                text: "internal parser trace",
                isError: true,
                sourceMessageId: "result-question-1",
              },
            ],
          },
        ] as never,
      },
    });

    try {
      await tick();
      expect(target.textContent).toContain("问题卡显示失败");
      expect(target.querySelector(".tool-activity .lucide-list-checks")).not.toBeNull();
      expect(target.textContent).not.toContain("internal parser trace");
      expect(target.textContent).not.toContain("测试助手在有限重试后");

      target.querySelector<HTMLButtonElement>(".tool-activity-trigger")?.click();
      await tick();
      expect(target.textContent).toContain("测试助手在有限重试后仍无法显示问题卡");
      expect(target.textContent).not.toContain("internal parser trace");
    } finally {
      window.__PI_WEB_CONFIG__ = originalConfig;
      await unmount(component);
      target.remove();
    }
  });

  it("does not expose raw orphan tool results", async () => {
    const target = document.createElement("div");
    document.body.appendChild(target);
    const component = mount(ChatTranscript, {
      target,
      props: {
        messages: [{
          id: "orphan-result",
          role: "toolResult",
          toolName: "bash",
          content: "cat /private/company/secrets.txt\nAPI_TOKEN=secret",
        }] as never,
      },
    });

    try {
      await tick();
      expect(target.textContent).toContain("已执行命令");
      expect(target.textContent).not.toContain("/private/company");
      expect(target.textContent).not.toContain("API_TOKEN");
    } finally {
      await unmount(component);
      target.remove();
    }
  });

  it("starts historical-message editing with the exact supported payload", async () => {
    const onRevise = vi.fn();
    const target = document.createElement("div");
    document.body.appendChild(target);
    const component = mount(ChatTranscript, {
      target,
      props: {
        allowRevision: true,
        onRevise,
        messages: [{
          id: "user-with-image",
          role: "user",
          content: [
            { type: "text", text: "  当前有哪些能力  " },
            { type: "image", data: "aGVsbG8=", mimeType: "image/png" },
          ],
        }] as never,
      },
    });

    try {
      await tick();
      const edit = target.querySelector<HTMLButtonElement>(
        'button[aria-label="编辑消息"]',
      );
      expect(edit).not.toBeNull();

      edit?.click();
      await tick();

      expect(onRevise).toHaveBeenCalledWith({
        entryId: "user-with-image",
        text: "当前有哪些能力",
        images: [{ type: "image", data: "aGVsbG8=", mimeType: "image/png" }],
      });
    } finally {
      await unmount(component);
      target.remove();
    }
  });

  it("starts historical-message editing for a text-only user message", async () => {
    const onRevise = vi.fn();
    const target = document.createElement("div");
    document.body.appendChild(target);
    const component = mount(ChatTranscript, {
      target,
      props: {
        allowRevision: true,
        onRevise,
        messages: [{
          id: "text-only-user",
          role: "user",
          content: "  text only  ",
        }] as never,
      },
    });

    try {
      await tick();
      target.querySelector<HTMLButtonElement>(
        'button[aria-label="编辑消息"]',
      )?.click();
      await tick();

      expect(onRevise).toHaveBeenCalledWith({
        entryId: "text-only-user",
        text: "text only",
        images: [],
      });
    } finally {
      await unmount(component);
      target.remove();
    }
  });

  it("keeps the active edit target visible and cancels it from the same edit action", async () => {
    const onRevise = vi.fn();
    const onCancelRevision = vi.fn();
    const target = document.createElement("div");
    document.body.appendChild(target);
    const component = mount(ChatTranscript, {
      target,
      props: {
        allowRevision: true,
        isStreaming: true,
        revisionEntryId: "active-user-message",
        onRevise,
        onCancelRevision,
        messages: [{
          id: "active-user-message",
          role: "user",
          content: "message being edited",
        }] as never,
      },
    });

    try {
      await tick();
      const message = target.querySelector<HTMLElement>(
        '[data-message-id="active-user-message"] [aria-current="true"]',
      );
      const edit = target.querySelector<HTMLButtonElement>(
        'button[aria-label="取消编辑"]',
      );
      expect(message).not.toBeNull();
      expect(edit?.hasAttribute("title")).toBe(false);

      edit?.click();
      await tick();

      expect(onCancelRevision).toHaveBeenCalledTimes(1);
      expect(onRevise).not.toHaveBeenCalled();
    } finally {
      await unmount(component);
      target.remove();
    }
  });

  it("preserves historical node projection while entering and cancelling editing", async () => {
    const onRevise = vi.fn();
    const onCancelRevision = vi.fn();
    const target = document.createElement("div");
    document.body.appendChild(target);
    const component = createClassComponent({
      component: ChatTranscript,
      target,
      props: {
        allowRevision: true,
        revisionEntryId: null as string | null,
        onRevise,
        onCancelRevision,
        messages: [
          { id: "history-a", role: "user", content: "first historical node" },
          { id: "history-b", role: "user", content: "second historical node" },
        ] as never,
      },
    });

    const projectedNodeIds = () =>
      [...target.querySelectorAll<HTMLElement>("[data-tree-entry-id]")]
        .map(element => element.dataset.treeEntryId);

    try {
      await tick();
      const initialNodeIds = projectedNodeIds();
      target.querySelector<HTMLButtonElement>(
        '[data-message-id="history-a"] button[aria-label="编辑消息"]',
      )?.click();
      await tick();
      expect(onRevise).toHaveBeenCalledWith({
        entryId: "history-a",
        text: "first historical node",
        images: [],
      });

      component.$set({ revisionEntryId: "history-a" });
      await tick();
      expect(projectedNodeIds()).toEqual(initialNodeIds);

      target.querySelector<HTMLButtonElement>(
        'button[aria-label="取消编辑"]',
      )?.click();
      await tick();
      expect(onCancelRevision).toHaveBeenCalledTimes(1);

      component.$set({ revisionEntryId: null });
      await tick();
      expect(projectedNodeIds()).toEqual(initialNodeIds);
    } finally {
      component.$destroy();
      target.remove();
    }
  });

  it("removes native title tooltips from edit and copy actions", async () => {
    const target = document.createElement("div");
    document.body.appendChild(target);
    const component = mount(ChatTranscript, {
      target,
      props: {
        allowRevision: true,
        messages: [{
          id: "user-actions",
          role: "user",
          content: "message actions",
        }] as never,
      },
    });

    try {
      await tick();
      const edit = target.querySelector<HTMLButtonElement>(
        'button[aria-label="编辑消息"]',
      );
      const copy = target.querySelector<HTMLButtonElement>(
        'button[aria-label="复制消息"]',
      );
      expect(edit?.hasAttribute("title")).toBe(false);
      expect(copy?.hasAttribute("title")).toBe(false);
    } finally {
      await unmount(component);
      target.remove();
    }
  });

  it.each([
    ["revision is disabled", { allowRevision: false, messages: [{ id: "user-1", role: "user", content: "message" }] }],
    ["the transcript is busy", { allowRevision: true, isStreaming: true, messages: [{ id: "user-1", role: "user", content: "message" }] }],
    ["the message is not from the user", { allowRevision: true, messages: [{ id: "assistant-1", role: "assistant", content: "answer" }] }],
    ["the user message has no identity", { allowRevision: true, messages: [{ role: "user", content: "message" }] }],
    ["the user message is blank", { allowRevision: true, messages: [{ id: "user-1", role: "user", content: "   " }] }],
  ])("does not expose historical editing when %s", async (_case, props) => {
    const onRevise = vi.fn();
    const target = document.createElement("div");
    document.body.appendChild(target);
    const component = mount(ChatTranscript, {
      target,
      props: { ...props, onRevise } as never,
    });

    try {
      await tick();
      expect(
        target.querySelector('button[aria-label="编辑消息"]'),
      ).toBeNull();
      expect(onRevise).not.toHaveBeenCalled();
    } finally {
      await unmount(component);
      target.remove();
    }
  });

  it("removes only adjacent activity row gaps while preserving conversation spacing and hit areas", () => {
    expect(chatTranscriptSource).toContain("--transcript-row-gap: 8px;");
    expect(chatTranscriptSource).toContain("gap: var(--transcript-row-gap);");
    expect(chatTranscriptSource).toContain(
      ".message-row.activity-trail-row + .message-row.activity-trail-row",
    );
    expect(activityRowSource).toContain("min-height: 36px;");
  });

  it("keeps the hover chevron eight pixels after the activity label", () => {
    expect(activityRowSource).toContain("width: fit-content;");
    expect(activityRowSource).toContain("max-width: 100%;");
    expect(activityRowSource).toContain("flex: 0 1 auto;");
    expect(activityRowSource).toMatch(
      /\.tool-activity-chevron\s*\{[\s\S]*?margin-left: 8px;/,
    );
    expect(activityRowSource).toMatch(
      /\.tool-activity-trigger:hover \.tool-activity-chevron\s*\{[\s\S]*?opacity: 1;/,
    );
    expect(activityRowSource).toMatch(
      /\.tool-activity-chevron\.expanded\s*\{[\s\S]*?opacity: 1;[\s\S]*?rotate: 90deg;/,
    );
    expect(activityRowSource).not.toContain(
      "background: color-mix(in srgb, var(--panel-2) 68%, transparent);",
    );
  });
});
