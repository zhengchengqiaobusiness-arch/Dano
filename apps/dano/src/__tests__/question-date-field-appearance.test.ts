import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  new URL("../../web/src/components/QuestionDateField.svelte", import.meta.url),
  "utf8",
);
const controlsSource = readFileSync(
  new URL("../../web/src/components/questionToolControls.css", import.meta.url),
  "utf8",
);

describe("QuestionDateField time picker appearance", () => {
  it("shares one desktop width between the trigger and popover", () => {
    expect(source).toContain("--question-date-picker-width: 260px");
    expect(source).toContain("width: var(--question-date-picker-width)");
    expect(source).toContain(":global(.question-date-popover)");
  });

  it("uses themeable hour and minute selects on desktop", () => {
    expect(source).toContain("hourOptions");
    expect(source).toContain("minuteOptions");
    expect(source).toContain('part: "hour", labelKey: "questionTool.hour"');
    expect(source).toContain('part: "minute", labelKey: "questionTool.minute"');
    expect(source).toContain("{#each timeSelectControls as control}");
    expect(source).toContain("handleTimePartChange(control.part, event)");
    expect(source).toContain('class="question-input question-time-select"');
    expect(source).toContain("font-variant-numeric: tabular-nums");
    expect(controlsSource).toContain("--question-control-height: 36px");
    expect(controlsSource).toContain(".question-input:not(textarea)");
    expect(controlsSource).toContain("--question-control-height: 44px");
    expect(source).not.toContain("<TimeField.Root");
  });

  it("switches to native date controls on mobile", () => {
    expect(source).toContain('MOBILE_PICKER_QUERY = "(max-width: 640px)"');
    expect(source).toContain("window.matchMedia(MOBILE_PICKER_QUERY)");
    expect(source).toContain("{#if useNativePicker}");
    expect(source).toContain('type={includesTime ? "datetime-local" : "date"}');
    expect(source).toContain("parseNativeDateInputValue");
    expect(source).toContain("if (useNativePicker) open = false");
    expect(source).not.toContain('class="question-date-native-icon"');
    expect(source).not.toContain(".question-date-native::-webkit-calendar-picker-indicator");
    expect(source).not.toContain('class="question-button secondary question-date-clear"');
  });

  it("preserves the supplied placeholder over an empty native control", () => {
    expect(source).toContain("{placeholder}");
    expect(source).toContain("aria-placeholder={placeholder || undefined}");
    expect(source).toContain("{#if !nativeInputValue && placeholder}");
    expect(source).toContain('class="question-date-native-placeholder"');
    expect(source).toContain("pointer-events: none");
  });
});
