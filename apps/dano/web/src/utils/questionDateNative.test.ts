import { describe, expect, it } from "vitest";
import {
  formatNativeDateInputValue,
  parseNativeDateInputValue,
} from "./questionDateNative";

const parts = {
  year: 2026,
  month: 7,
  day: 16,
  hour: 8,
  minute: 5,
};

describe("native date input values", () => {
  it("formats date-only and date-time values for native controls", () => {
    expect(formatNativeDateInputValue(parts, false)).toBe("2026-07-16");
    expect(formatNativeDateInputValue(parts, true)).toBe("2026-07-16T08:05");
    expect(formatNativeDateInputValue(undefined, true)).toBe("");
  });

  it("parses date-only and date-time values", () => {
    expect(parseNativeDateInputValue("2026-07-16", false)).toEqual({
      ...parts,
      hour: 0,
      minute: 0,
    });
    expect(parseNativeDateInputValue("2026-07-16T08:05", true)).toEqual(parts);
  });

  it("rejects malformed or impossible values", () => {
    expect(parseNativeDateInputValue("2026-02-30", false)).toBeUndefined();
    expect(parseNativeDateInputValue("2026-07-16T24:00", true)).toBeUndefined();
    expect(parseNativeDateInputValue("2026-07-16", true)).toBeUndefined();
  });
});
