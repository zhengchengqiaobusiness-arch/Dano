import { describe, expect, it, vi } from "vitest";
import { isBridgeExitInput } from "../exit-input.js";

describe("isBridgeExitInput", () => {
  it("recognizes raw Ctrl+C bytes", () => {
    expect(isBridgeExitInput("\u0003")).toBe(true);
  });

  it("recognizes Kitty Ctrl+C sequences", () => {
    expect(isBridgeExitInput("\x1b[99;5u")).toBe(true);
    expect(isBridgeExitInput("\x1b[99;5:1u")).toBe(true);
  });

  it("recognizes xterm modifyOtherKeys Ctrl+C sequences", () => {
    expect(isBridgeExitInput("\x1b[27;5;99~")).toBe(true);
  });

  it("recognizes selectCancel bindings from Pi keybindings", () => {
    const keybindings = {
      matches: vi.fn(
        (input: string, action: string) =>
          input === "kitty-ctrl-c" && action === "selectCancel",
      ),
    };

    expect(isBridgeExitInput("kitty-ctrl-c", keybindings)).toBe(true);
  });

  it("recognizes copy bindings as a fallback", () => {
    const keybindings = {
      matches: vi.fn(
        (input: string, action: string) =>
          input === "kitty-ctrl-c" && action === "copy",
      ),
    };

    expect(isBridgeExitInput("kitty-ctrl-c", keybindings)).toBe(true);
  });

  it("ignores unrelated input", () => {
    const keybindings = {
      matches: vi.fn(() => false),
    };

    expect(isBridgeExitInput("x", keybindings)).toBe(false);
    expect(isBridgeExitInput("\x1b[99;6u")).toBe(false);
  });
});
