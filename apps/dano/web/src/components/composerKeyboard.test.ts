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

  it("keeps Enter as a newline on touch or narrow inputs", () => {
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

  it("detects touch or narrow input environments without user agent checks", () => {
    const matchMedia = vi.fn().mockReturnValue({ matches: true });

    expect(shouldEnterInsertNewline({ matchMedia })).toBe(true);
    expect(matchMedia).toHaveBeenCalledWith(
      "(hover: none) and (pointer: coarse), (max-width: 768px)",
    );
  });

  it("falls back to desktop behavior when matchMedia is unavailable", () => {
    expect(shouldEnterInsertNewline(undefined)).toBe(false);
  });
});
