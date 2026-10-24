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
  authentication?:
    | { status: "checking" }
    | { status: "anonymous" }
    | { status: "reauth_required" }
    | {
        status: "authenticated";
        user: { id: string; username: string; avatarUrl?: string };
      };
  onOpenTheme?: () => void;
  onOpenScheduledTasks?: () => void;
  onLogin?: () => void;
  onLogout?: () => void | Promise<void>;
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
      authentication: { status: "checking" },
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

  it("keeps Menu and status on the left and the conversation action on the right", async () => {
    const { component, target } = await renderHeader({ showNewSession: true });

    try {
      const header = target.querySelector("header")!;
      expect(header.firstElementChild?.classList.contains("header-leading")).toBe(true);
      expect(header.lastElementChild?.classList.contains("header-trailing")).toBe(true);
      const leadingButtons = header.querySelectorAll<HTMLButtonElement>(
        ".header-leading button",
      );
      expect(leadingButtons[0]?.classList.contains("menu-button")).toBe(true);
      expect(leadingButtons[1]?.classList.contains("connection-status")).toBe(true);
      expect(header.querySelector(".header-trailing .new-session-button")).not.toBeNull();
    } finally {
      await unmount(component);
    }
  });

  it("hides only the new-session action for an empty conversation", async () => {
    const { component, target } = await renderHeader({ showNewSession: false });

    try {
      expect(target.querySelector(".new-session-button")).toBeNull();
      expect(target.querySelector(".header-leading .connection-status")).not.toBeNull();
      expect(target.querySelector(".header-leading .menu-button")).not.toBeNull();
    } finally {
      await unmount(component);
    }
  });

  it("asks for confirmation before logging out the authenticated User", async () => {
    const onLogout = vi.fn();
    const { component, target } = await renderHeader({
      authentication: {
        status: "authenticated",
        user: {
          id: "user-alice",
          username: "Alice",
          avatarUrl: "https://example.test/alice.png",
        },
      },
      onLogout,
    });

    try {
      target.querySelector<HTMLButtonElement>(".menu-button")!.click();
      await tick();

      const menu = document.querySelector<HTMLElement>(".header-menu")!;
      expect(menu).not.toBeNull();
      expect(menu.querySelector(".theme-menu-item")?.textContent).toContain("主题色");
      expect(menu.querySelector(".header-user-summary")?.textContent).toContain("Alice");
      expect(menu.querySelector(".login-menu-item")).toBeNull();
      menu.querySelector<HTMLButtonElement>(".logout-menu-item")!.click();
      await tick();
      expect(onLogout).not.toHaveBeenCalled();
      expect(document.querySelector('.header-menu[data-state="open"]')).toBeNull();

      const confirmation = document.querySelector<HTMLElement>(
        '[data-slot="alert-dialog-content"]',
      )!;
      expect(confirmation.textContent).toContain("退出登录");
      expect(confirmation.textContent).toContain("取消");
      expect(confirmation.textContent).toContain("确认");
      confirmation
        .querySelector<HTMLButtonElement>('[data-slot="alert-dialog-cancel"]')!
        .click();
      await tick();
      expect(onLogout).not.toHaveBeenCalled();
      expect(
        document.querySelector('[data-slot="alert-dialog-content"][data-state="open"]'),
      ).toBeNull();

      target.querySelector<HTMLButtonElement>(".menu-button")!.click();
      await tick();
      document.querySelector<HTMLButtonElement>(".logout-menu-item")!.click();
      await tick();
      document
        .querySelector<HTMLButtonElement>(".logout-confirm-action")!
        .click();
      await tick();
      expect(onLogout).toHaveBeenCalledOnce();
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
      authentication: {
        status: "authenticated",
        user: {
          id: "user-alice",
          username: "Alice",
          avatarUrl: "https://example.test/missing.png",
        },
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

  it("shows login in the existing Menu for an Anonymous User", async () => {
    const onLogin = vi.fn();
    const { component, target } = await renderHeader({
      authentication: { status: "anonymous" },
      onLogin,
    });

    try {
      target.querySelector<HTMLButtonElement>(".menu-button")!.click();
      await tick();

      const login = document.querySelector<HTMLButtonElement>(".login-menu-item")!;
      expect(login.textContent).toContain("登录");
      expect(document.querySelector(".header-user-summary")).toBeNull();
      expect(document.querySelector(".logout-menu-item")).toBeNull();
      login.click();
      await tick();
      expect(onLogin).toHaveBeenCalledOnce();
    } finally {
      await unmount(component);
    }
  });

  it("does not offer login while authentication is still being checked", async () => {
    const { component, target } = await renderHeader({
      authentication: { status: "checking" },
    });

    try {
      target.querySelector<HTMLButtonElement>(".menu-button")!.click();
      await tick();

      expect(document.querySelector(".login-menu-item")).toBeNull();
      expect(document.querySelector(".logout-menu-item")).toBeNull();
      expect(document.querySelector(".header-user-summary")).toBeNull();
    } finally {
      await unmount(component);
    }
  });

  it("shows scheduled tasks in the existing Menu", async () => {
    const onOpenScheduledTasks = vi.fn();
    const { component, target } = await renderHeader({ onOpenScheduledTasks });

    try {
      target.querySelector<HTMLButtonElement>(".menu-button")!.click();
      await tick();
      const item = document.querySelector<HTMLButtonElement>(".scheduled-tasks-menu-item")!;
      expect(item.textContent).toContain("定时任务");
      item.click();
      await tick();
      expect(onOpenScheduledTasks).toHaveBeenCalledOnce();
      expect(document.querySelector('.header-menu[data-state="open"]')).toBeNull();
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
