import { describe, expect, it, vi } from "vitest";
import {
  FieldAssistError,
  assertAllowed,
  assertFieldAssistOutput,
  buildPolishMessages,
  buildRegenerateMessages,
  createFieldAssistService,
  getFieldAssistWarnings,
  normalizeFieldAssistOutput,
} from "../field-assist.js";

describe("field assist", () => {
  it("keeps polish user prompt as the raw current value", () => {
    const messages = buildPolishMessages({
      requestId: "req-1",
      action: "polish",
      fieldType: "textarea",
      requestMethod: "editor",
      title: "请假原因",
      currentValue: " 明天上午请假半天 ",
    });

    expect(messages[1]).toEqual({
      role: "user",
      content: " 明天上午请假半天 ",
    });
    expect(messages[0]?.content).toContain("不要追问用户");
  });

  it("uses structured context for regenerate, including empty current value", () => {
    const messages = buildRegenerateMessages({
      requestId: "req-1",
      action: "regenerate",
      fieldType: "input",
      requestMethod: "input",
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

  it("warns on sensitive field labels without blocking the request", async () => {
    const ai = { generateText: vi.fn().mockResolvedValue("可用内容") };
    const service = createFieldAssistService({
      ai,
      getCurrentModel: () => ({ id: "gpt-4", provider: "openai" }),
    });

    await expect(
      service.assist({
        requestId: "req-1",
        action: "regenerate",
        fieldType: "input",
        requestMethod: "input",
        title: "API token 说明",
        currentValue: "",
      }),
    ).resolves.toMatchObject({
      value: "可用内容",
      metadata: {
        warnings: [{ code: "SENSITIVE_FIELD" }],
      },
    });
    expect(ai.generateText).toHaveBeenCalledTimes(1);
  });

  it("retries when model output asks a follow-up question", async () => {
    const ai = {
      generateText: vi.fn()
        .mockResolvedValueOnce("请问您需要请假的具体原因是什么？")
        .mockResolvedValueOnce("因个人事务需要请假处理。"),
    };
    const service = createFieldAssistService({
      ai,
      getCurrentModel: () => ({ id: "gpt-4", provider: "openai" }),
    });

    await expect(
      service.assist({
        requestId: "req-1",
        action: "polish",
        fieldType: "textarea",
        requestMethod: "editor",
        title: "请假原因",
        currentValue: "个人事务",
      }),
    ).resolves.toMatchObject({
      value: "因个人事务需要请假处理。",
    });
    expect(ai.generateText).toHaveBeenCalledTimes(2);
    expect(ai.generateText.mock.calls[1]?.[0].messages.at(-1).content).toContain(
      "不要追问用户",
    );
  });

  it("defaults to ten field assist retries", async () => {
    const ai = {
      generateText: vi.fn()
        .mockResolvedValueOnce("请问您需要请假的具体原因是什么？")
        .mockResolvedValueOnce("请补充一下请假原因")
        .mockResolvedValueOnce("请问需要写什么说明？")
        .mockResolvedValueOnce("请问您希望我生成什么内容？")
        .mockResolvedValueOnce("还需要更多信息才能生成")
        .mockResolvedValueOnce("请提供字段内容")
        .mockResolvedValueOnce("请确认要填写什么")
        .mockResolvedValueOnce("需要您补充说明")
        .mockResolvedValueOnce("麻烦您输入说明")
        .mockResolvedValueOnce("请填写具体原因")
        .mockResolvedValueOnce("因个人事务需要请假处理。"),
    };
    const service = createFieldAssistService({
      ai,
      getCurrentModel: () => ({ id: "gpt-4", provider: "openai" }),
    });

    await expect(
      service.assist({
        requestId: "req-1",
        action: "regenerate",
        fieldType: "textarea",
        requestMethod: "editor",
        title: "请假原因",
        currentValue: "",
      }),
    ).resolves.toMatchObject({
      value: "因个人事务需要请假处理。",
    });
    expect(ai.generateText).toHaveBeenCalledTimes(11);
  });

  it("uses configured field assist retry count", async () => {
    const ai = {
      generateText: vi.fn()
        .mockResolvedValueOnce("请问您需要请假的具体原因是什么？")
        .mockResolvedValueOnce("请补充一下请假原因"),
    };
    const service = createFieldAssistService({
      ai,
      getCurrentModel: () => ({ id: "gpt-4", provider: "openai" }),
      maxRetries: 1,
    });

    await expect(
      service.assist({
        requestId: "req-1",
        action: "regenerate",
        fieldType: "textarea",
        requestMethod: "editor",
        title: "请假原因",
        currentValue: "",
      }),
    ).rejects.toThrow("AI 辅助返回了追问内容，请重试");
    expect(ai.generateText).toHaveBeenCalledTimes(2);
  });

  it("rejects obvious secret values before model calls", () => {
    expect(() =>
      assertAllowed({
        requestId: "req-1",
        action: "polish",
        fieldType: "input",
        requestMethod: "input",
        title: "备注",
        currentValue: "api_key=sk-1234567890abcdefghijklmnop",
      }),
    ).toThrowError(FieldAssistError);
  });

  it("normalizes input and textarea output within field limits", () => {
    expect(normalizeFieldAssistOutput(" a\n b\t c ", "input")).toBe("a b c");
    expect(normalizeFieldAssistOutput("a \r\nb  ", "textarea")).toBe("a\nb");
  });

  it("rejects follow-up questions from field assist output", () => {
    expect(() =>
      assertFieldAssistOutput("请问您需要请假的具体原因是什么？"),
    ).toThrowError(FieldAssistError);
  });

  it("keeps warning detection text-only and reusable", () => {
    expect(getFieldAssistWarnings({ title: "手机号" })).toHaveLength(1);
    expect(getFieldAssistWarnings({ title: "请假原因" })).toHaveLength(0);
  });
});
