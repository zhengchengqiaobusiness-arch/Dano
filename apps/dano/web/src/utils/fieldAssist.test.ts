import { describe, expect, it } from "vitest";
import {
  getFieldAssistWarning,
  readFieldAssistResponse,
  toFieldAssistErrorMessage,
} from "./fieldAssist";

describe("field assist UI helpers", () => {
  it("warns for sensitive field context without hiding actions", () => {
    expect(getFieldAssistWarning({ title: "API token" })).toContain("敏感信息");
    expect(getFieldAssistWarning({ title: "请假原因" })).toBe("");
  });

  it("reads field assist command responses", () => {
    expect(
      readFieldAssistResponse({
        id: "cmd-1",
        type: "response",
        command: "field_assist",
        success: true,
        data: {
          value: "替换内容",
          metadata: {
            action: "regenerate",
            fieldType: "textarea",
            inputLength: 0,
            outputLength: 4,
            elapsedMs: 1,
          },
        },
      }),
    ).toMatchObject({ value: "替换内容" });
  });

  it("converts failed command responses to user-facing errors", () => {
    expect(() =>
      readFieldAssistResponse({
        id: "cmd-1",
        type: "response",
        command: "field_assist",
        success: false,
        error: "bad request",
      }),
    ).toThrow("bad request");
    expect(toFieldAssistErrorMessage("nope")).toBe("AI 辅助请求失败");
  });
});
