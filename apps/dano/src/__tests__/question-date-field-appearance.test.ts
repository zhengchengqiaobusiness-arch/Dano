import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  new URL("../../web/src/components/QuestionDateField.svelte", import.meta.url),
  "utf8",
);

describe("QuestionDateField time picker appearance", () => {
  it("suppresses the native time-input arrow when rendering the shared chevron", () => {
    const timeControlMarkup = source.match(
      /<div class="question-time-control">([\s\S]*?)<\/div>/,
    )?.[1] ?? "";
    const timeInputRule = source.match(/\.question-time-input\s*\{([^}]*)\}/)?.[1] ?? "";

    expect(timeControlMarkup).toContain('<ChevronDown size={16} aria-hidden="true" />');
    expect(timeInputRule).toContain("-webkit-appearance: none");
    expect(timeInputRule).toContain("appearance: none");
  });
});
