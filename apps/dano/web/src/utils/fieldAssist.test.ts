import { describe, expect, it } from "vitest";
import {
  getFieldAssistWarning,
  invalidateFieldAssistRuns,
  isCurrentFieldAssistRun,
  nextFieldAssistRunId,
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

  it("tracks concurrent field assist runs per field", () => {
    const runs: Record<string, number> = {};

    const firstNameRun = nextFieldAssistRunId(runs, "name");
    runs.name = firstNameRun;
    expect(isCurrentFieldAssistRun(runs, "name", firstNameRun)).toBe(true);

    const firstReasonRun = nextFieldAssistRunId(runs, "reason");
    runs.reason = firstReasonRun;
    expect(isCurrentFieldAssistRun(runs, "name", firstNameRun)).toBe(true);
    expect(isCurrentFieldAssistRun(runs, "reason", firstReasonRun)).toBe(true);

    const secondNameRun = nextFieldAssistRunId(runs, "name");
    runs.name = secondNameRun;
    expect(isCurrentFieldAssistRun(runs, "name", firstNameRun)).toBe(false);
    expect(isCurrentFieldAssistRun(runs, "name", secondNameRun)).toBe(true);

    const resetRuns = invalidateFieldAssistRuns(runs);
    expect(isCurrentFieldAssistRun(resetRuns, "name", secondNameRun)).toBe(false);
    expect(nextFieldAssistRunId(resetRuns, "name")).toBeGreaterThan(secondNameRun);
  });
});
