/** @vitest-environment happy-dom */

import { mount, tick, unmount } from "svelte";
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
  Element.prototype.animate = vi.fn(() => ({
    cancel: vi.fn(),
    finished: Promise.resolve(),
  })) as never;
});

afterAll(() => {
  Element.prototype.animate = originalAnimate;
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

describe("ChatTranscript Activity Trail", () => {
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

  it("consolidates consecutive tool work across assistant responses", async () => {
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
      expect(target.querySelectorAll(".tool-activity")).toHaveLength(1);
      expect(target.querySelectorAll(".message-row.assistant")).toHaveLength(1);
      expect(target.textContent).toContain("正在查阅资料 2 次");
      expect(target.textContent).not.toContain("已查阅资料");
      expect(target.textContent).not.toContain("继续核对");
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
      expect(target.textContent).not.toContain("Dano 在有限重试后");

      target.querySelector<HTMLButtonElement>(".tool-activity-trigger")?.click();
      await tick();
      expect(target.textContent).toContain("Dano 在有限重试后仍无法显示问题卡");
      expect(target.textContent).not.toContain("internal parser trace");
    } finally {
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

  it("removes only adjacent activity row gaps while preserving conversation spacing and hit areas", () => {
    expect(chatTranscriptSource).toContain("--transcript-row-gap: 8px;");
    expect(chatTranscriptSource).toContain("gap: var(--transcript-row-gap);");
    expect(chatTranscriptSource).toContain(
      ".message-row.activity-trail-row + .message-row.activity-trail-row",
    );
    expect(activityRowSource).toContain("min-height: 36px;");
  });
});
