import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  new URL("../../web/src/components/QuestionDateField.svelte", import.meta.url),
  "utf8",
);

describe("QuestionDateField time picker appearance", () => {
  it("shares one desktop width between the trigger and popover", () => {
    expect(source).toContain("--question-date-picker-width: 260px");
    expect(source).toContain("width: var(--question-date-picker-width)");
    expect(source).toContain(":global(.question-date-popover)");
  });

  it("uses a themeable segmented time field instead of the native time picker", () => {
    expect(source).toContain("<TimeField.Root");
    expect(source).toContain("<TimeField.Input");
    expect(source).toContain("<TimeField.Segment");
    expect(source).toContain('data-segment="literal"');
    expect(source).toContain("font-variant-numeric: tabular-nums");
    expect(source).toContain("background: var(--accent)");
    expect(source).toContain("color: var(--on-accent)");
    expect(source).toContain("min-height: 44px");
    expect(source).not.toContain('type="time"');
    expect(source).not.toContain("::-webkit-calendar-picker-indicator");
  });
});
