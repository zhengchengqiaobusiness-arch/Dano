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
      "给Dano发消息",
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

  it.each([
    [
      "chatTranscript.askUserQuestionRetryFailureDetail",
      "问题卡调用未完成，请重试。",
    ],
    [
      "chatTranscript.askUserQuestionTerminalFailureDetail",
      "测试助手在有限重试后仍无法显示问题卡，已结束本轮响应。请重新发送，或换一种方式描述需要填写的信息。",
    ],
    [
      "chatTranscript.askUserQuestionValidationFailureDetail",
      "模型重试后仍未生成有效的问题参数，测试助手已停止问题流程。请重新发送，或换一种方式描述需要填写的信息。",
    ],
  ] as const)("uses the configured name without Chinese spacing in %s", (key, expected) => {
    vi.stubGlobal("window", {});

    expect(t(key, { productName: "测试助手" })).toBe(expected);
  });

  it("falls back to the key when no message exists", () => {
    vi.stubGlobal("window", {});

    expect(t("missing.key", { productName: "Dano" })).toBe("missing.key");
  });
});
