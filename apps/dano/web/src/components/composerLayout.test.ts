import { describe, expect, it } from "vitest";
import { shouldComposerBeMultiline } from "./composerLayout";

describe("shouldComposerBeMultiline", () => {
  it("keeps non-empty multiline input stable until it is cleared", () => {
    expect(
      shouldComposerBeMultiline({
        hasText: true,
        wasMultiline: true,
        hasExplicitNewline: false,
        wrapsAtCurrentWidth: false,
      }),
    ).toBe(true);

    expect(
      shouldComposerBeMultiline({
        hasText: false,
        wasMultiline: true,
        hasExplicitNewline: false,
        wrapsAtCurrentWidth: false,
      }),
    ).toBe(false);
  });

  it("enters multiline for explicit newlines or soft wrapping", () => {
    expect(
      shouldComposerBeMultiline({
        hasText: true,
        wasMultiline: false,
        hasExplicitNewline: true,
        wrapsAtCurrentWidth: false,
      }),
    ).toBe(true);

    expect(
      shouldComposerBeMultiline({
        hasText: true,
        wasMultiline: false,
        hasExplicitNewline: false,
        wrapsAtCurrentWidth: true,
      }),
    ).toBe(true);
  });
});
