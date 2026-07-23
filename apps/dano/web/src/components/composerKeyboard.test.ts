import { describe, expect, it, vi } from "vitest";
import {
  shouldEnterInsertNewline,
  shouldSubmitComposerEnter,
} from "./composerKeyboard";

function keyEvent(
  overrides: Partial<
    Pick<KeyboardEvent, "key" | "shiftKey" | "isComposing" | "keyCode">
  > = {},
) {
  return {
    key: "Enter",
    shiftKey: false,
    isComposing: false,
    keyCode: 13,
    ...overrides,
  };
}

describe("composer keyboard enter handling", () => {
  it("uses Enter to submit on desktop input environments", () => {
    expect(shouldSubmitComposerEnter(keyEvent(), false, false)).toBe(true);
  });

  it("keeps Shift+Enter as a newline on desktop", () => {
    expect(
      shouldSubmitComposerEnter(keyEvent({ shiftKey: true }), false, false),
    ).toBe(false);
  });

  it("keeps Enter as a newline when the environment policy requests it", () => {
    expect(shouldSubmitComposerEnter(keyEvent(), false, true)).toBe(false);
  });

  it("does not intercept Enter while an IME is composing", () => {
    expect(
      shouldSubmitComposerEnter(keyEvent({ isComposing: true }), false, false),
    ).toBe(false);
    expect(shouldSubmitComposerEnter(keyEvent({ keyCode: 229 }), false, false)).toBe(
      false,
    );
    expect(shouldSubmitComposerEnter(keyEvent(), true, false)).toBe(false);
  });

  it("ignores non-Enter keys", () => {
    expect(
      shouldSubmitComposerEnter(keyEvent({ key: "Tab" }), false, false),
    ).toBe(false);
  });

  it("keeps Enter as a newline only in mobile touch environments", () => {
    const matchMedia = vi.fn().mockReturnValue({ matches: true });

    expect(
      shouldEnterInsertNewline({
        matchMedia,
        navigator: { maxTouchPoints: 5 },
      }),
    ).toBe(true);
    expect(matchMedia).toHaveBeenCalledTimes(2);
  });

  it("submits Enter without an active touch pointer", () => {
    expect(
      shouldEnterInsertNewline({
        matchMedia: vi.fn().mockReturnValue({ matches: true }),
        navigator: { maxTouchPoints: 0 },
      }),
    ).toBe(false);
  });

  it("submits Enter when a hover-capable pointer is also available", () => {
    expect(
      shouldEnterInsertNewline({
        matchMedia: vi.fn((query: string) => ({
          matches: query === "(hover: none) and (pointer: coarse)",
        })),
        navigator: { maxTouchPoints: 5 },
      }),
    ).toBe(false);
  });

  it("submits Enter when the primary pointer is not touch-oriented", () => {
    expect(
      shouldEnterInsertNewline({
        matchMedia: vi.fn().mockReturnValue({ matches: false }),
        navigator: { maxTouchPoints: 5 },
      }),
    ).toBe(false);
  });

  it("falls back to desktop behavior when matchMedia is unavailable", () => {
    expect(shouldEnterInsertNewline(undefined)).toBe(false);
  });
});
