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

  it("keeps Enter as a newline only in mobile touch environments", () => {
    const matchMedia = vi.fn().mockReturnValue({ matches: true });

    expect(
      shouldEnterInsertNewline({
        matchMedia,
        navigator: { userAgent: "Mozilla/5.0 (Linux; Android 15; Mobile)" },
      }),
    ).toBe(true);
    expect(matchMedia).toHaveBeenCalledWith(
      "(hover: none) and (pointer: coarse)",
    );
  });

  it("submits Enter on desktop even with a coarse pointer", () => {
    expect(
      shouldEnterInsertNewline({
        matchMedia: vi.fn().mockReturnValue({ matches: true }),
        navigator: { userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X)" },
      }),
    ).toBe(false);
  });

  it("submits Enter on mobile browsers without a touch-primary pointer", () => {
    expect(
      shouldEnterInsertNewline({
        matchMedia: vi.fn().mockReturnValue({ matches: false }),
        navigator: { userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0)" },
      }),
    ).toBe(false);
  });

  it("uses user-agent client hints when available", () => {
    expect(
      shouldEnterInsertNewline({
        matchMedia: vi.fn().mockReturnValue({ matches: true }),
        navigator: {
          userAgent: "Mozilla/5.0 (X11; Linux x86_64)",
          userAgentData: { mobile: true },
        },
      }),
    ).toBe(true);
  });

  it("falls back to desktop behavior when matchMedia is unavailable", () => {
    expect(shouldEnterInsertNewline(undefined)).toBe(false);
  });
});
