import {
  DANO_LLM_AUTHENTICATION_ERROR,
  DANO_LLM_INCOMPLETE_ERROR,
  DANO_LLM_TIMEOUT_ERROR,
} from "@dano/types/protocol";
import { afterEach, describe, expect, it, vi } from "vitest";
import { errorMessageText } from "./transcript";

describe("LLM error messages", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders actionable Chinese errors without exposing provider details", () => {
    vi.stubGlobal("window", {});

    expect(
      errorMessageText({
        role: "assistant",
        errorMessage: DANO_LLM_TIMEOUT_ERROR,
      }),
    ).toBe("模型服务在规定时间内未返回数据，请重试或切换模型。");
    expect(
      errorMessageText({
        role: "assistant",
        errorMessage: DANO_LLM_AUTHENTICATION_ERROR,
      }),
    ).toBe("模型服务认证失败，请检查服务端凭据或联系管理员。");
    expect(
      errorMessageText({
        role: "assistant",
        errorMessage: DANO_LLM_INCOMPLETE_ERROR,
      }),
    ).toBe("模型响应中断；已保留收到的内容。请重试或切换模型。");
  });

  it("uses the configured English locale", () => {
    vi.stubGlobal("window", { __PI_WEB_CONFIG__: { locale: "en-US" } });

    expect(
      errorMessageText({
        role: "assistant",
        errorMessage: DANO_LLM_TIMEOUT_ERROR,
      }),
    ).toBe(
      "The model service did not return data within the configured timeout. Retry or switch models.",
    );
  });
});
