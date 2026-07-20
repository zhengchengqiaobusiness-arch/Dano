/** @vitest-environment happy-dom */

import { mount, tick, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AskUserQuestionItem } from "../utils/askUserQuestion";
import QuestionFieldLabel from "./QuestionFieldLabel.svelte";

describe("QuestionFieldLabel", () => {
  let resize: ResizeObserverCallback | undefined;
  let appShell: HTMLElement;
  const originalResizeObserver = globalThis.ResizeObserver;

  beforeEach(() => {
    appShell = document.createElement("div");
    appShell.className = "app-shell";
    document.body.append(appShell);
    resize = undefined;
    globalThis.ResizeObserver = class {
      constructor(callback: ResizeObserverCallback) {
        resize = callback;
      }

      observe() {}
      unobserve() {}
      disconnect() {}
    };
  });

  afterEach(() => {
    globalThis.ResizeObserver = originalResizeObserver;
    appShell.remove();
  });

  it.each([
    ["date", "lucide-calendar"],
    ["single", "lucide-list-checks"],
    ["multiple", "lucide-list-checks"],
    ["select", "lucide-list-checks"],
    ["treeSelect", "lucide-list-checks"],
    ["confirm", "lucide-circle-check"],
    ["text", "lucide-message-square-text"],
  ] as const)("renders the semantic %s field icon", async (kind, iconClass) => {
    const target = document.createElement("div");
    const component = mount(QuestionFieldLabel, {
      target,
      props: {
        kind: kind satisfies AskUserQuestionItem["kind"],
        label: "字段名称",
      },
    });
    await tick();

    const icon = target.querySelector(".question-field-icon");
    const svg = icon?.querySelector("svg");
    expect(icon?.getAttribute("aria-hidden")).toBe("true");
    expect(svg?.classList).toContain(iconClass);
    expect(svg?.getAttribute("width")).toBe("18");
    expect(svg?.getAttribute("height")).toBe("18");

    unmount(component);
  });

  it("adds a keyboard stop only while the label text is truncated", async () => {
    const target = document.createElement("div");
    const component = mount(QuestionFieldLabel, {
      target,
      props: {
        kind: "text",
        label: "一段必须完整显示在提示中的字段名称",
      },
    });
    await tick();

    const trigger = target.querySelector<HTMLElement>(".question-field-label");
    const text = target.querySelector<HTMLElement>(".question-field-label-content");
    expect(trigger).not.toBeNull();
    expect(text).not.toBeNull();

    Object.defineProperties(text!, {
      clientWidth: { configurable: true, value: 160 },
      scrollWidth: { configurable: true, value: 120 },
    });
    resize?.([], {} as ResizeObserver);
    await tick();
    expect(trigger?.tabIndex).toBe(-1);
    expect(target.querySelectorAll('[tabindex="0"]')).toHaveLength(0);

    Object.defineProperty(text!, "scrollWidth", { configurable: true, value: 240 });
    resize?.([], {} as ResizeObserver);
    await tick();
    expect(trigger?.tabIndex).toBe(0);
    expect(target.querySelectorAll('[tabindex="0"]')).toHaveLength(1);

    Object.defineProperty(text!, "scrollWidth", { configurable: true, value: 120 });
    resize?.([], {} as ResizeObserver);
    await tick();
    expect(trigger?.tabIndex).toBe(-1);

    unmount(component);
  });

  it("exposes the complete plain-text label when the tooltip is enabled", async () => {
    const target = document.createElement("div");
    appShell.append(target);
    const component = mount(QuestionFieldLabel, {
      target,
      props: {
        kind: "text",
        label: "**完整字段** [查看说明](https://example.com)",
      },
    });
    await tick();

    const trigger = target.querySelector<HTMLElement>(".question-field-label");
    const text = target.querySelector<HTMLElement>(".question-field-label-content");
    await vi.waitFor(() => expect(text?.textContent).toContain("完整字段"));
    Object.defineProperties(text!, {
      clientWidth: { configurable: true, value: 120 },
      scrollWidth: { configurable: true, value: 240 },
    });
    resize?.([], {} as ResizeObserver);
    await tick();

    expect(trigger?.getAttribute("aria-label")).toBe("完整字段 查看说明");
    expect(trigger?.getAttribute("aria-label")).not.toContain("https://");
    expect(trigger?.getAttribute("aria-label")).not.toContain("**");
    expect(target.querySelector<HTMLAnchorElement>("a")?.tabIndex).toBe(-1);

    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true }));
    trigger?.focus();
    await vi.waitFor(() => {
      const tooltip = appShell.querySelector<HTMLElement>(".tooltip-content");
      expect(tooltip?.textContent).toBe("完整字段 查看说明");
      expect(tooltip?.getAttribute("data-state")).toMatch(/open$/);
    });

    unmount(component);
  });
});
