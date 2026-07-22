/** @vitest-environment happy-dom */

import { mount, tick, unmount } from "svelte";
import { afterEach, describe, expect, it, vi } from "vitest";
import AppHeader from "./AppHeader.svelte";

type AppHeaderProps = {
  connectionStatus?: "connected" | "connecting" | "disconnected";
  disconnectReason?: string;
  onReconnect?: () => void;
  onNewSession?: () => void;
  newSessionPending?: boolean;
  showNewSession?: boolean;
  currentUser?: { username: string; avatarUrl?: string };
  onOpenTheme?: () => void;
};

async function renderHeader(
  props: AppHeaderProps = {},
) {
  const shell = document.createElement("div");
  shell.className = "app-shell";
  const target = document.createElement("div");
  shell.append(target);
  document.body.append(shell);
  const component = mount(AppHeader, {
    target,
    props: {
      connectionStatus: "connected",
      ...props,
    },
  });
  await tick();
  return { component, shell, target };
}

describe("AppHeader", () => {
  afterEach(() => {
    document.body.replaceChildren();
  });

  it("keeps the conversation action on the left and utilities on the right", async () => {
    const { component, target } = await renderHeader({ showNewSession: true });

    try {
      const header = target.querySelector("header")!;
      expect(header.firstElementChild?.classList.contains("header-leading")).toBe(true);
      expect(header.lastElementChild?.classList.contains("header-trailing")).toBe(true);
      expect(header.querySelector(".header-leading .new-session-button")).not.toBeNull();
      expect(header.querySelector(".header-trailing .connection-status")).not.toBeNull();
      expect(header.querySelector(".header-trailing .menu-button")).not.toBeNull();
    } finally {
      await unmount(component);
    }
  });

  it("hides only the new-session action for an empty conversation", async () => {
    const { component, target } = await renderHeader({ showNewSession: false });

    try {
      expect(target.querySelector(".new-session-button")).toBeNull();
      expect(target.querySelector(".header-trailing .connection-status")).not.toBeNull();
      expect(target.querySelector(".header-trailing .menu-button")).not.toBeNull();
    } finally {
      await unmount(component);
    }
  });

  it("shows only the theme entry and authenticated User summary", async () => {
    const { component, target } = await renderHeader({
      currentUser: {
        username: "Alice",
        avatarUrl: "https://example.test/alice.png",
      },
    });

    try {
      target.querySelector<HTMLButtonElement>(".menu-button")!.click();
      await tick();

      const menu = document.querySelector<HTMLElement>(".header-menu")!;
      expect(menu).not.toBeNull();
      expect(menu.querySelector(".theme-menu-item")?.textContent).toContain("主题色");
      expect(menu.querySelector(".header-user-summary")?.textContent).toContain("Alice");
      expect(menu.querySelector<HTMLImageElement>(".header-user-avatar")?.src).toBe(
        "https://example.test/alice.png",
      );
      expect(menu.textContent).not.toMatch(/Keyboard shortcuts|Dano preview/i);
    } finally {
      await unmount(component);
    }
  });

  it("falls back to the neutral User icon when an avatar cannot load", async () => {
    const { component, target } = await renderHeader({
      currentUser: {
        username: "Alice",
        avatarUrl: "https://example.test/missing.png",
      },
    });

    try {
      target.querySelector<HTMLButtonElement>(".menu-button")!.click();
      await tick();
      document
        .querySelector<HTMLImageElement>(".header-user-avatar")!
        .dispatchEvent(new Event("error"));
      await tick();

      expect(document.querySelector(".header-user-avatar")).toBeNull();
      expect(document.querySelector(".header-user-placeholder")).not.toBeNull();
      expect(document.querySelector(".header-user-summary")?.textContent).toContain("Alice");
    } finally {
      await unmount(component);
    }
  });

  it("uses the default visual placeholder until User data arrives", async () => {
    const { component, target } = await renderHeader();

    try {
      target.querySelector<HTMLButtonElement>(".menu-button")!.click();
      await tick();

      const summary = document.querySelector(".header-user-summary")!;
      expect(summary.textContent).toContain("默认用户");
      expect(summary.querySelector(".header-user-placeholder")).not.toBeNull();
      expect(summary.querySelector("img")).toBeNull();
    } finally {
      await unmount(component);
    }
  });

  it("closes after choosing the theme entry", async () => {
    const onOpenTheme = vi.fn();
    const { component, target } = await renderHeader({ onOpenTheme });
    const trigger = target.querySelector<HTMLButtonElement>(".menu-button")!;

    try {
      trigger.click();
      await tick();
      document.querySelector<HTMLButtonElement>(".theme-menu-item")!.click();
      await tick();
      expect(onOpenTheme).toHaveBeenCalledOnce();
      expect(document.querySelector('.header-menu[data-state="open"]')).toBeNull();
    } finally {
      await unmount(component);
    }
  });

  it("keeps the Menu button keyboard-focusable and closes on Escape", async () => {
    const { component, target } = await renderHeader();
    const trigger = target.querySelector<HTMLButtonElement>(".menu-button")!;

    try {
      trigger.focus();
      expect(document.activeElement).toBe(trigger);
      trigger.click();
      await tick();
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      await tick();
      expect(document.querySelector('.header-menu[data-state="open"]')).toBeNull();
    } finally {
      await unmount(component);
    }
  });
});
