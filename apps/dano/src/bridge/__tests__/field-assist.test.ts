import { describe, expect, it, vi } from "vitest";
import {
  FieldAssistError,
  buildFieldAssistMessages,
  createFieldAssistHandler,
  parseFieldAssistRequest,
} from "../field-assist.js";

describe("field assist", () => {
  it("keeps polish user prompt as the raw current value", () => {
    const messages = buildFieldAssistMessages({
      requestId: "req-1",
      action: "polish",
      fieldType: "textarea",
      title: "请假原因",
        currentValue: " 明天上午请假半天 ",
    });

    expect(messages[1]).toEqual({
      role: "user",
      content: " 明天上午请假半天 ",
    });
  });

  it("uses structured context for regenerate", () => {
    const messages = buildFieldAssistMessages({
      requestId: "req-1",
      action: "regenerate",
      fieldType: "input",
      title: "请假原因",
      placeholder: "请输入原因",
      currentValue: "",
      prefill: "病假",
    });

    expect(JSON.parse(messages[1]?.content ?? "{}")).toEqual({
      title: "请假原因",
      placeholder: "请输入原因",
      fieldType: "input",
      currentValue: "",
      prefill: "病假",
    });
  });

  it("rejects empty polish requests before calling the model", () => {
    expect(() =>
      parseFieldAssistRequest({
        requestId: "req-1",
        action: "polish",
        fieldType: "input",
        title: "摘要",
        currentValue: " ",
      }),
    ).toThrowError(FieldAssistError);
  });

  it("rejects sensitive fields before model calls", () => {
    expect(() =>
      parseFieldAssistRequest({
        requestId: "req-1",
        action: "regenerate",
        fieldType: "input",
        title: "API token",
        currentValue: "",
      }),
    ).toThrowError(FieldAssistError);
  });

  it("posts OpenAI-compatible requests and returns text", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        choices: [{ message: { content: "润色后" } }],
      }),
    });
    const handler = createFieldAssistHandler({
      getCurrentModel: () => ({
        id: "mimo-v2.5",
        provider: "xiaomi-token-plan-cn",
        api: "openai-chat-completions",
        baseUrl: "https://example.test/v1",
      }),
      env: { XIAOMI_TOKEN_PLAN_CN_API_KEY: "key" },
      fetch: fetchMock as unknown as typeof fetch,
    });

    await expect(
      handler({
        requestId: "req-1",
        action: "polish",
        fieldType: "input",
        title: "事由",
        currentValue: "请假",
      }),
    ).resolves.toEqual({ value: "润色后" });
    expect(fetchMock).toHaveBeenCalledWith(
      "https://example.test/v1/chat/completions",
      expect.objectContaining({
        method: "POST",
      }),
    );
  });
});
