import { describe, expect, it, vi } from "vitest";
import { canShowFieldAssist, runFieldAssist } from "./fieldAssist";

describe("field assist UI helpers", () => {
  it("hides AI assist for sensitive field context", () => {
    expect(canShowFieldAssist({ title: "API token" })).toBe(false);
    expect(canShowFieldAssist({ title: "事由", placeholder: "请输入验证码" })).toBe(
      false,
    );
  });

  it("keeps AI assist visible for ordinary text fields", () => {
    expect(canShowFieldAssist({ title: "请假原因", placeholder: "请输入原因" })).toBe(
      true,
    );
  });

  it("returns the replacement value from the same-origin API", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ value: "替换内容" }),
    });

    await expect(
      runFieldAssist(
        {
          requestId: "req-1",
          action: "regenerate",
          fieldType: "textarea",
          title: "请假原因",
          currentValue: "",
        },
        fetchMock as unknown as typeof fetch,
      ),
    ).resolves.toBe("替换内容");
  });

  it("throws without replacement value when the API fails", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      json: async () => ({ error: "bad request" }),
    });

    await expect(
      runFieldAssist(
        {
          requestId: "req-1",
          action: "polish",
          fieldType: "input",
          title: "请假原因",
          currentValue: "请假",
        },
        fetchMock as unknown as typeof fetch,
      ),
    ).rejects.toThrow("bad request");
  });
});
