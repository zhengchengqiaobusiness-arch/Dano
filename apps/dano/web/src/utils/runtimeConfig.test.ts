import { afterEach, describe, expect, it, vi } from "vitest";
import {
  applyRuntimeProductTitle,
  getRuntimeEmptyStateConfig,
  getRuntimeLocale,
  getRuntimeProductName,
  getRuntimeQuickActions,
  getRuntimeShortcutLinks,
  getRuntimeSlashCommandsAndMentionsEnabled,
  getRuntimeTranscriptProcessSummaryEnabled,
} from "./runtimeConfig";

function stubRuntimeConfig(config: NonNullable<Window["__PI_WEB_CONFIG__"]>) {
  vi.stubGlobal("window", { __PI_WEB_CONFIG__: config });
}

describe("runtimeConfig", () => {
  it("uses the server shortcut list, including an explicit empty list", () => {
    const shortcutLinks = [{ label: "自定义入口", href: "/custom", color: "#123456" }];
    stubRuntimeConfig({ shortcutLinks });
    expect(getRuntimeShortcutLinks()).toEqual(shortcutLinks);
    stubRuntimeConfig({ shortcutLinks: [] });
    expect(getRuntimeShortcutLinks()).toEqual([]);
  });

  it("uses shipped shortcuts when no server config was injected", () => {
    vi.stubGlobal("window", {});
    expect(getRuntimeShortcutLinks().map(link => link.href)).toEqual(["/help"]);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uses 小络助手 defaults when runtime config is absent", () => {
    vi.stubGlobal("window", {});

    expect(getRuntimeProductName()).toBe("小络助手");
    expect(getRuntimeLocale()).toBe("zh-CN");
    expect(getRuntimeEmptyStateConfig()).toEqual({
      mode: "text",
      content: "给小络助手发消息",
    });
    expect(getRuntimeSlashCommandsAndMentionsEnabled()).toBe(false);
    expect(getRuntimeTranscriptProcessSummaryEnabled()).toBe(false);
  });

  it("uses the configured locale for default empty state copy", () => {
    stubRuntimeConfig({
      locale: "en-US",
      productName: "My Agent",
    });

    expect(getRuntimeLocale()).toBe("en-US");
    expect(getRuntimeEmptyStateConfig()).toEqual({
      mode: "text",
      content: "Message My Agent",
    });
  });

  it("applies the configured assistant name to the document title", () => {
    stubRuntimeConfig({ productName: "测试助手" });
    vi.stubGlobal("document", { title: "" });

    applyRuntimeProductTitle();

    expect(document.title).toBe("测试助手");
  });

  it("renders configured text with the Chinese product placeholder", () => {
    stubRuntimeConfig({
      productName: "My Agent",
      emptyState: { mode: "text", content: "给{产品名称}发消息" },
    });

    expect(getRuntimeEmptyStateConfig()).toEqual({
      mode: "text",
      content: "给My Agent发消息",
    });
  });

  it("renders configured html with the product placeholder", () => {
    stubRuntimeConfig({
      productName: "My Agent",
      emptyState: {
        mode: "html",
        content: "<strong>给{产品名称}发消息</strong>",
      },
    });

    expect(getRuntimeEmptyStateConfig()).toEqual({
      mode: "html",
      content: "<strong>给My Agent发消息</strong>",
    });
  });

  it("supports the English productName placeholder alias", () => {
    stubRuntimeConfig({
      productName: "Dano Pro",
      emptyState: { mode: "text", content: "Message {productName}" },
    });

    expect(getRuntimeEmptyStateConfig().content).toBe("Message Dano Pro");
  });

  it("returns configured quick actions and removes invalid entries", () => {
    stubRuntimeConfig({
      quickActions: [
        { label: " 请假 ", prompt: " 帮我申请请假 " },
        { label: "", prompt: "ignored" },
        { label: "ignored", prompt: " " },
      ],
    });

    expect(getRuntimeQuickActions()).toEqual([
      { label: "请假", prompt: "帮我申请请假" },
    ]);
  });

  it("uses no quick actions when runtime config is absent", () => {
    vi.stubGlobal("window", {});

    expect(getRuntimeQuickActions()).toEqual([]);
  });

  it("exposes the server-resolved slash command capability", () => {
    stubRuntimeConfig({ slashCommandsAndMentionsEnabled: true });

    expect(getRuntimeSlashCommandsAndMentionsEnabled()).toBe(true);
  });

  it("exposes the configured transcript process summary preference", () => {
    stubRuntimeConfig({ transcriptProcessSummaryEnabled: true });

    expect(getRuntimeTranscriptProcessSummaryEnabled()).toBe(true);
  });
});
