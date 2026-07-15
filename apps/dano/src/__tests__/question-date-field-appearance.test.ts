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

  it("suppresses the native time-input arrow when rendering the shared chevron", () => {
    const timeControlMarkup = source.match(
      /<div class="question-time-control">([\s\S]*?)<\/div>/,
    )?.[1] ?? "";
    const timeInputRule = source.match(/\n  \.question-time-input\s*\{([^}]*)\}/)?.[1] ?? "";

    expect(timeControlMarkup).toContain('<ChevronDown size={16} aria-hidden="true" />');
    expect(timeInputRule).toContain("-webkit-appearance: none");
    expect(timeInputRule).toContain("appearance: none");
    expect(timeInputRule).toContain("width: 100%");
  });
});
