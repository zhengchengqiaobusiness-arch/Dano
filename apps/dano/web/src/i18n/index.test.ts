import { afterEach, describe, expect, it, vi } from "vitest";
import { t } from "./index";

function stubRuntimeConfig(config: NonNullable<Window["__PI_WEB_CONFIG__"]>) {
  vi.stubGlobal("window", { __PI_WEB_CONFIG__: config });
}

describe("i18n", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uses zh-CN by default", () => {
    vi.stubGlobal("window", {});

    expect(t("emptyState.message", { productName: "Dano" })).toBe(
      "给 Dano 发消息",
    );
  });

  it("uses the runtime locale override", () => {
    stubRuntimeConfig({ locale: "en-US" });

    expect(t("emptyState.message", { productName: "Dano" })).toBe(
      "Message Dano",
    );
  });

  it("interpolates params", () => {
    stubRuntimeConfig({ locale: "en-US" });

    expect(t("emptyState.message", { productName: "Dano Pro" })).toBe(
      "Message Dano Pro",
    );
  });

  it("falls back to the key when no message exists", () => {
    vi.stubGlobal("window", {});

    expect(t("missing.key", { productName: "Dano" })).toBe("missing.key");
  });
});
