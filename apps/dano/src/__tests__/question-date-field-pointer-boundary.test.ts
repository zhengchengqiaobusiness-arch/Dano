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

  it("renders desktop time selects inside the date-picker popover", () => {
    const contentStart = source.indexOf("<DatePicker.Content");
    const timeInput = source.indexOf('class="question-input question-time-select"');
    const contentEnd = source.indexOf("</DatePicker.Content>");

    expect(contentStart).toBeGreaterThan(-1);
    expect(timeInput).toBeGreaterThan(contentStart);
    expect(timeInput).toBeLessThan(contentEnd);
    expect(source).toContain("closeOnDateSelect={false}");
    expect(source).toContain("if (!includesTime) open = false");
  });

  it("keeps the native mobile control outside the custom date picker", () => {
    const nativeInput = source.indexOf('class="question-input question-date-native"');
    const datePickerRoot = source.indexOf("<DatePicker.Root");

    expect(nativeInput).toBeGreaterThan(-1);
    expect(nativeInput).toBeLessThan(datePickerRoot);
    expect(source).toContain("{:else}\n    <DatePicker.Root");
  });
});
