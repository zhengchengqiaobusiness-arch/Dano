import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  new URL("../../web/src/components/QuestionDateField.svelte", import.meta.url),
  "utf8",
);

describe("QuestionDateField pointer boundary", () => {
  it("delegates outside interaction and instance isolation to the date-picker primitive", () => {
    expect(source).toContain("<DatePicker.Root");
    expect(source).toContain("<DatePicker.Trigger");
    expect(source).toContain("<DatePicker.Content");
    expect(source).not.toContain("<svelte:window");
    expect(source).not.toContain("controlRowEl");
    expect(source).not.toContain("onInteractOutside");
  });

  it("renders the datetime input inside the date-picker popover", () => {
    const contentStart = source.indexOf("<DatePicker.Content");
    const timeInput = source.indexOf("<TimeField.Root");
    const contentEnd = source.indexOf("</DatePicker.Content>");

    expect(contentStart).toBeGreaterThan(-1);
    expect(timeInput).toBeGreaterThan(contentStart);
    expect(timeInput).toBeLessThan(contentEnd);
    expect(source).toContain("closeOnDateSelect={false}");
    expect(source).toContain("if (!includesTime) open = false");
    expect(source).not.toContain('type="time"');
  });
});
