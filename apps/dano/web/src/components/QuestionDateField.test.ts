/** @vitest-environment happy-dom */

import { mount, tick, unmount } from "svelte";
import { afterEach, describe, expect, it, vi } from "vitest";
import QuestionDateField from "./QuestionDateField.svelte";
import questionDateFieldSource from "./QuestionDateField.svelte?raw";
import datePickerContentSource from "./ui/date-picker/date-picker-content.svelte?raw";
import datePickerIndexSource from "./ui/date-picker/index.ts?raw";

function mockMobilePicker(matches: boolean) {
  vi.spyOn(window, "matchMedia").mockReturnValue({
    matches,
    media: "(max-width: 640px)",
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  } as unknown as MediaQueryList);
}

function createAppTarget() {
  const target = document.createElement("div");
  target.className = "app-shell";
  document.body.append(target);
  return target;
}

describe("QuestionDateField", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    document.body.replaceChildren();
  });

  it("uses the project date-picker component layer instead of Bits UI directly", () => {
    expect(questionDateFieldSource).toContain('from "./ui/date-picker"');
    expect(questionDateFieldSource).not.toContain('from "bits-ui"');
  });

  it("delegates date popover layering to the shared portalled content", () => {
    expect(datePickerIndexSource).toContain('from "./date-picker-content.svelte"');
    expect(datePickerContentSource).toContain(
      '<DatePickerPrimitive.Portal to=".app-shell">',
    );
    expect(datePickerContentSource).toMatch(
      /:global\(\.date-picker-content\)\s*\{[\s\S]*?z-index:\s*30;/,
    );
    expect(questionDateFieldSource).not.toMatch(
      /:global\(\.question-date-popover\)\s*\{[\s\S]*?z-index:/,
    );
  });

  it("keeps the formatted desktop trigger and disabled state", async () => {
    mockMobilePicker(false);
    const target = createAppTarget();
    const component = mount(QuestionDateField, {
      target,
      props: {
        id: "departure",
        value: "2026-08-01",
        dateFormat: "yyyy-MM-dd",
        disabled: true,
        placeholder: "yyyy-MM-dd",
        onValueChange: vi.fn(),
      },
    });
    await tick();

    try {
      const trigger = target.querySelector<HTMLButtonElement>("#departure-trigger");
      expect(target.querySelector('input[type="date"]')).toBeNull();
      expect(trigger?.textContent).toContain("2026-08-01");
      expect(trigger?.disabled).toBe(true);
    } finally {
      unmount(component);
    }
  });

  it("preserves the mobile native date input and clear behavior", async () => {
    mockMobilePicker(true);
    const onValueChange = vi.fn();
    const target = createAppTarget();
    const component = mount(QuestionDateField, {
      target,
      props: {
        id: "departure",
        value: "2026-08-01",
        dateFormat: "yyyy-MM-dd",
        required: true,
        placeholder: "yyyy-MM-dd",
        onValueChange,
      },
    });
    await tick();

    try {
      const input = target.querySelector<HTMLInputElement>('input[type="date"]');
      expect(input?.value).toBe("2026-08-01");
      expect(input?.required).toBe(true);
      input!.value = "";
      input!.dispatchEvent(new Event("input", { bubbles: true }));
      expect(onValueChange).toHaveBeenLastCalledWith(undefined);
    } finally {
      unmount(component);
    }
  });

  it("preserves the mobile datetime-local minute contract", async () => {
    mockMobilePicker(true);
    const onValueChange = vi.fn();
    const target = createAppTarget();
    const component = mount(QuestionDateField, {
      target,
      props: {
        id: "meeting",
        value: "2026-08-01 09:30",
        dateFormat: "yyyy-MM-dd HH:mm",
        onValueChange,
      },
    });
    await tick();

    try {
      const input = target.querySelector<HTMLInputElement>('input[type="datetime-local"]');
      expect(input?.value).toBe("2026-08-01T09:30");
      expect(input?.step).toBe("60");
      input!.value = "2026-08-02T10:45";
      input!.dispatchEvent(new Event("input", { bubbles: true }));
      expect(onValueChange).toHaveBeenLastCalledWith("2026-08-02 10:45");
    } finally {
      unmount(component);
    }
  });
});
