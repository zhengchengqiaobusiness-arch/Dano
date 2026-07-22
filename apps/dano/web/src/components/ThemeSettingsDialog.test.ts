/** @vitest-environment happy-dom */

import type { AccentColorPreset } from "@dano/types/protocol";
import { mount, tick, unmount } from "svelte";
import { afterEach, describe, expect, it, vi } from "vitest";
import ThemeSettingsDialog from "./ThemeSettingsDialog.svelte";

async function renderDialog(
  props: {
    selectedPreset?: AccentColorPreset;
    onSelectPreset?: (preset: AccentColorPreset) => void;
    onClose?: () => void;
  } = {},
) {
  const shell = document.createElement("div");
  shell.className = "app-shell";
  document.body.append(shell);
  const component = mount(ThemeSettingsDialog, {
    target: shell,
    props: {
      open: true,
      selectedPreset: "default",
      ...props,
    },
  });
  await tick();
  return { component, shell };
}

describe("ThemeSettingsDialog", () => {
  afterEach(() => {
    document.body.replaceChildren();
    document.body.style.removeProperty("overflow");
  });

  it("shows only the six approved Theme Color presets and the selected state", async () => {
    const { component, shell } = await renderDialog({ selectedPreset: "gray" });

    try {
      expect(shell.querySelector("[role=dialog]")?.getAttribute("aria-labelledby")).toBe(
        "theme-color-dialog-title",
      );
      expect(shell.querySelector("#theme-color-dialog-title")?.textContent?.trim()).toBe("主题色");
      expect(shell.textContent).not.toMatch(/明暗|Dark theme|Light theme|Built-in/i);

      const rows = Array.from(
        shell.querySelectorAll<HTMLButtonElement>("[data-theme-color-preset]"),
      );
      expect(rows.map(row => row.textContent?.trim())).toEqual([
        "默认",
        "蓝色",
        "灰色",
        "黄色",
        "粉色",
        "紫色",
      ]);
      expect(
        shell.querySelector('[data-theme-color-preset="gray"]')?.getAttribute("aria-pressed"),
      ).toBe("true");
      expect(shell.querySelectorAll(".theme-color-check")).toHaveLength(1);
    } finally {
      await unmount(component);
    }
  });

  it("applies consecutive selections without closing the dialog", async () => {
    const onSelectPreset = vi.fn();
    const { component, shell } = await renderDialog({ onSelectPreset });

    try {
      shell.querySelector<HTMLButtonElement>('[data-theme-color-preset="blue"]')!.click();
      shell.querySelector<HTMLButtonElement>('[data-theme-color-preset="purple"]')!.click();
      await tick();

      expect(onSelectPreset.mock.calls).toEqual([["blue"], ["purple"]]);
      expect(shell.querySelector("[role=dialog]")).not.toBeNull();
    } finally {
      await unmount(component);
    }
  });

  it.each([
    ["close control", (shell: HTMLElement) => shell.querySelector<HTMLButtonElement>(".theme-dialog-close")!.click()],
    ["Escape", () => document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }))],
    ["backdrop", (shell: HTMLElement) => {
      const backdrop = shell.querySelector<HTMLElement>(".theme-dialog-overlay")!;
      backdrop.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true }));
      backdrop.click();
    }],
  ])("closes from the %s", async (_label, close) => {
    const onClose = vi.fn();
    const { component, shell } = await renderDialog({ onClose });

    try {
      close(shell);
      await tick();
      expect(onClose).toHaveBeenCalledOnce();
    } finally {
      await unmount(component);
    }
  });
});
