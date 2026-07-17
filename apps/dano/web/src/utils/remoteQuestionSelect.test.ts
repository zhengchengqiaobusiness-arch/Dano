import { describe, expect, it } from "vitest";
import {
  remoteQuestionSelectStatus,
  selectedRemoteQuestionOption,
} from "./remoteQuestionSelect";

describe("remote question select", () => {
  it("uses a friendly state instead of exposing request errors", () => {
    expect(remoteQuestionSelectStatus({ loading: false, error: true, optionCount: 0 }))
      .toBe("error");
  });

  it("keeps existing options visible while a new request loads", () => {
    expect(remoteQuestionSelectStatus({ loading: true, error: false, optionCount: 1 }))
      .toBe("ready");
  });

  it("resolves the selected label from its stable typed key", () => {
    expect(selectedRemoteQuestionOption("number:310000", [
      { key: "string:310000", label: "字符串城市" },
      { key: "number:310000", label: "上海" },
    ])?.label).toBe("上海");
  });
});
