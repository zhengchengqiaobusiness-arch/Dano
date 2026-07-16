import { describe, expect, it } from "vitest";
import { transcriptToolIconName } from "./toolPresentation";

describe("transcript tool icon presentation", () => {
  it.each([
    ["bash", "code-xml"],
    ["read", "book-open-text"],
    ["write", "file-pen-line"],
    ["edit", "pen-line"],
  ] as const)("maps %s to %s", (toolName, iconName) => {
    expect(transcriptToolIconName(toolName)).toBe(iconName);
  });

  it("keeps unknown and missing tools on the text fallback", () => {
    expect(transcriptToolIconName("curl")).toBeUndefined();
    expect(transcriptToolIconName(undefined)).toBeUndefined();
  });
});
