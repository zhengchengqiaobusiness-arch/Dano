import { afterEach, describe, expect, it, vi } from "vitest";
import {
  getRuntimeEmptyStateConfig,
  getRuntimeLocale,
  getRuntimeProductName,
} from "./runtimeConfig";

function stubRuntimeConfig(config: NonNullable<Window["__PI_WEB_CONFIG__"]>) {
  vi.stubGlobal("window", { __PI_WEB_CONFIG__: config });
}

describe("runtimeConfig", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uses Dano defaults when runtime config is absent", () => {
    vi.stubGlobal("window", {});

    expect(getRuntimeProductName()).toBe("Dano");
    expect(getRuntimeLocale()).toBe("zh-CN");
    expect(getRuntimeEmptyStateConfig()).toEqual({
      mode: "text",
      content: "给 Dano 发消息",
    });
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

  it("renders configured text with the Chinese product placeholder", () => {
    stubRuntimeConfig({
      productName: "My Agent",
      emptyState: { mode: "text", content: "给 {产品名称} 发消息" },
    });

    expect(getRuntimeEmptyStateConfig()).toEqual({
      mode: "text",
      content: "给 My Agent 发消息",
    });
  });

  it("renders configured html with the product placeholder", () => {
    stubRuntimeConfig({
      productName: "My Agent",
      emptyState: {
        mode: "html",
        content: "<strong>给 {产品名称} 发消息</strong>",
      },
    });

    expect(getRuntimeEmptyStateConfig()).toEqual({
      mode: "html",
      content: "<strong>给 My Agent 发消息</strong>",
    });
  });

  it("supports the English productName placeholder alias", () => {
    stubRuntimeConfig({
      productName: "Dano Pro",
      emptyState: { mode: "text", content: "Message {productName}" },
    });

    expect(getRuntimeEmptyStateConfig().content).toBe("Message Dano Pro");
  });
});
