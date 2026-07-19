/** @vitest-environment happy-dom */

import { describe, expect, it, vi } from "vitest";
import {
  createCenterFocusStage,
  hasActiveCenterFocusStage,
  isDesktopCenterFocusViewport,
} from "./centerFocusStage";

function rect(left: number, top: number, width: number, height: number): DOMRect {
  return {
    x: left,
    y: top,
    left,
    top,
    width,
    height,
    right: left + width,
    bottom: top + height,
    toJSON: () => ({}),
  } as DOMRect;
}

describe("Center Focus Stage", () => {
  it("recenters the active card when a confirmation expands into a form revision", () => {
    const root = document.createElement("main");
    const transcript = document.createElement("div");
    const anchor = document.createElement("div");
    const card = document.createElement("article");
    transcript.dataset.centerFocusTranscript = "";
    anchor.className = "question-card-anchor";
    anchor.append(card);
    transcript.append(anchor);
    root.append(transcript);
    document.body.append(root);
    vi.spyOn(root, "getBoundingClientRect").mockReturnValue(rect(0, 40, 1000, 800));
    let cardHeight = 360;
    vi.spyOn(card, "getBoundingClientRect").mockImplementation(
      () => rect(200, Number.parseFloat(card.style.top || "180"), 600, cardHeight),
    );
    vi.spyOn(window, "matchMedia").mockImplementation(query => ({
      matches: query === "(min-width: 901px)" ||
        query === "(prefers-reduced-motion: reduce)",
    } as MediaQueryList));
    let resizeCallback: ResizeObserverCallback | undefined;
    const disconnect = vi.fn();
    const OriginalResizeObserver = globalThis.ResizeObserver;
    globalThis.ResizeObserver = class {
      constructor(callback: ResizeObserverCallback) {
        resizeCallback = callback;
      }
      observe() {}
      unobserve() {}
      disconnect = disconnect;
    } as typeof ResizeObserver;

    try {
      const stage = createCenterFocusStage(root);
      stage.show({ sessionKey: "session-a", toolCallId: "confirm-form", element: card });

      expect(card.style.top).toBe("260px");

      cardHeight = 752;
      resizeCallback?.([], {} as ResizeObserver);

      expect(card.style.top).toBe("64px");
      expect(Number.parseFloat(card.style.top) + cardHeight).toBeLessThanOrEqual(816);

      stage.hide("confirm-form");
      expect(disconnect).toHaveBeenCalledOnce();
      resizeCallback?.([], {} as ResizeObserver);
      expect(card.getAttribute("style")).toBeNull();
      stage.destroy();
    } finally {
      globalThis.ResizeObserver = OriginalResizeObserver;
    }
  });

  it("focuses the same card within center bounds and restores the inline presentation", () => {
    const root = document.createElement("main");
    const transcript = document.createElement("div");
    const anchor = document.createElement("div");
    const card = document.createElement("article");
    const backgroundButton = document.createElement("button");
    const composer = document.createElement("div");
    const composerInput = document.createElement("textarea");
    const composerAttachment = document.createElement("button");
    transcript.dataset.centerFocusTranscript = "";
    composer.dataset.centerFocusComposer = "";
    composerInput.value = "保留的草稿";
    composerAttachment.className = "attachment-chip";
    anchor.className = "question-card-anchor";
    anchor.append(card);
    transcript.append(anchor, backgroundButton);
    composer.append(composerInput, composerAttachment);
    root.append(transcript, composer);
    document.body.append(root);
    vi.spyOn(root, "getBoundingClientRect").mockReturnValue(rect(100, 40, 1000, 800));
    vi.spyOn(card, "getBoundingClientRect").mockReturnValue(rect(220, 180, 600, 500));
    composerInput.focus();

    const activeChange = vi.fn();
    const stage = createCenterFocusStage(root, activeChange);
    stage.show({ sessionKey: "session-a", toolCallId: "form-a", element: card });

    expect(root.dataset.centerFocusActive).toBe("true");
    expect(card.classList.contains("center-focused-card")).toBe(true);
    expect(card.style.width).toBe("720px");
    expect(anchor.style.height).toBe("500px");
    expect(composer.inert).toBe(true);
    expect(backgroundButton.inert).toBe(true);
    expect(composer.querySelector("textarea")).toBe(composerInput);
    expect(composer.querySelector(".attachment-chip")).toBe(composerAttachment);
    expect(composerInput.value).toBe("保留的草稿");
    expect(document.activeElement).not.toBe(composerInput);
    expect(activeChange).toHaveBeenLastCalledWith(true);

    root.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    const escaped = vi.fn();
    document.addEventListener("keydown", escaped);
    card.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    expect(root.dataset.centerFocusActive).toBe("true");
    expect(hasActiveCenterFocusStage()).toBe(true);
    expect(escaped).toHaveBeenCalledOnce();
    document.removeEventListener("keydown", escaped);

    stage.hide();

    expect(root.dataset.centerFocusActive).toBeUndefined();
    expect(card.classList.contains("center-focused-card")).toBe(false);
    expect(anchor.style.height).toBe("");
    expect(composer.inert).toBe(false);
    expect(backgroundButton.inert).toBe(false);
    expect(composer.querySelector("textarea")).toBe(composerInput);
    expect(composerInput.value).toBe("保留的草稿");
    expect(composer.querySelector(".attachment-chip")).toBe(composerAttachment);
    expect(activeChange).toHaveBeenLastCalledWith(false);
    stage.destroy();
  });

  it("releases focus when the active session changes", () => {
    const root = document.createElement("main");
    const transcript = document.createElement("div");
    const anchor = document.createElement("div");
    const card = document.createElement("article");
    const composer = document.createElement("div");
    transcript.dataset.centerFocusTranscript = "";
    composer.dataset.centerFocusComposer = "";
    anchor.className = "question-card-anchor";
    anchor.append(card);
    transcript.append(anchor);
    root.append(transcript, composer);
    document.body.append(root);
    vi.spyOn(root, "getBoundingClientRect").mockReturnValue(rect(0, 0, 900, 700));
    vi.spyOn(card, "getBoundingClientRect").mockReturnValue(rect(100, 100, 600, 400));
    const stage = createCenterFocusStage(root);

    stage.show({ sessionKey: "session-a", toolCallId: "form-a", element: card });
    stage.setSession("session-b");

    expect(root.dataset.centerFocusActive).toBeUndefined();
    expect(card.classList.contains("center-focused-card")).toBe(false);
    stage.destroy();
  });

  it("ignores a stale release from a previously focused tool call", () => {
    const root = document.createElement("main");
    const transcript = document.createElement("div");
    const composer = document.createElement("div");
    transcript.dataset.centerFocusTranscript = "";
    composer.dataset.centerFocusComposer = "";
    root.append(transcript, composer);
    document.body.append(root);
    vi.spyOn(root, "getBoundingClientRect").mockReturnValue(rect(0, 0, 900, 700));

    const cards = ["form-a", "form-b"].map(id => {
      const anchor = document.createElement("div");
      const card = document.createElement("article");
      anchor.className = "question-card-anchor";
      anchor.append(card);
      transcript.append(anchor);
      vi.spyOn(card, "getBoundingClientRect").mockReturnValue(rect(100, 100, 600, 400));
      return { id, card };
    });
    const stage = createCenterFocusStage(root);
    stage.show({ sessionKey: "session-a", toolCallId: cards[0]!.id, element: cards[0]!.card });
    stage.show({ sessionKey: "session-a", toolCallId: cards[1]!.id, element: cards[1]!.card });

    stage.hide("form-a");

    expect(root.dataset.centerFocusActive).toBe("true");
    expect(cards[1]!.card.classList.contains("center-focused-card")).toBe(true);
    stage.destroy();
  });

  it("limits the first stage implementation to desktop viewports", () => {
    expect(isDesktopCenterFocusViewport(query => ({
      matches: query === "(min-width: 901px)",
    } as MediaQueryList))).toBe(true);
    expect(isDesktopCenterFocusViewport(() => ({
      matches: false,
    } as MediaQueryList))).toBe(false);
  });

  it("switches immediately without View Transition movement when reduced motion is preferred", () => {
    const root = document.createElement("main");
    const transcript = document.createElement("div");
    const anchor = document.createElement("div");
    const card = document.createElement("article");
    const composer = document.createElement("div");
    transcript.dataset.centerFocusTranscript = "";
    composer.dataset.centerFocusComposer = "";
    anchor.className = "question-card-anchor";
    anchor.append(card);
    transcript.append(anchor);
    root.append(transcript, composer);
    document.body.append(root);
    vi.spyOn(root, "getBoundingClientRect").mockReturnValue(rect(0, 0, 1000, 800));
    vi.spyOn(card, "getBoundingClientRect").mockReturnValue(rect(200, 160, 600, 400));
    vi.spyOn(window, "matchMedia").mockImplementation(query => ({
      matches: query === "(prefers-reduced-motion: reduce)",
    } as MediaQueryList));
    const startViewTransition = vi.fn((update: () => void) => {
      update();
      return { finished: Promise.resolve() };
    });
    Object.defineProperty(document, "startViewTransition", {
      configurable: true,
      value: startViewTransition,
    });
    const stage = createCenterFocusStage(root);

    stage.show({ sessionKey: "session-a", toolCallId: "form-a", element: card });

    expect(startViewTransition).not.toHaveBeenCalled();
    expect(root.dataset.centerFocusActive).toBe("true");
    expect(card.classList.contains("center-focused-card")).toBe(true);
    expect(composer.inert).toBe(true);

    stage.hide("form-a");

    expect(startViewTransition).not.toHaveBeenCalled();
    expect(root.dataset.centerFocusActive).toBeUndefined();
    expect(card.classList.contains("center-focused-card")).toBe(false);
    expect(composer.inert).toBe(false);
    stage.destroy();
  });

  it("uses the shared View Transition path when motion is allowed", () => {
    const root = document.createElement("main");
    const transcript = document.createElement("div");
    const anchor = document.createElement("div");
    const card = document.createElement("article");
    transcript.dataset.centerFocusTranscript = "";
    anchor.className = "question-card-anchor";
    anchor.append(card);
    transcript.append(anchor);
    root.append(transcript);
    document.body.append(root);
    vi.spyOn(root, "getBoundingClientRect").mockReturnValue(rect(0, 0, 1000, 800));
    vi.spyOn(card, "getBoundingClientRect").mockReturnValue(rect(200, 160, 600, 400));
    vi.spyOn(window, "matchMedia").mockImplementation(() => ({
      matches: false,
    } as MediaQueryList));
    const startViewTransition = vi.fn((update: () => void) => {
      update();
      return { finished: Promise.resolve() };
    });
    Object.defineProperty(document, "startViewTransition", {
      configurable: true,
      value: startViewTransition,
    });
    const stage = createCenterFocusStage(root);

    stage.show({ sessionKey: "session-a", toolCallId: "form-a", element: card });
    stage.hide("form-a");

    expect(startViewTransition).toHaveBeenCalledTimes(2);
    expect(root.dataset.centerFocusActive).toBeUndefined();
    stage.destroy();
  });

  it("keeps the complete focused interaction when View Transition is unavailable", () => {
    const root = document.createElement("main");
    const transcript = document.createElement("div");
    const anchor = document.createElement("div");
    const card = document.createElement("article");
    const backgroundButton = document.createElement("button");
    const composer = document.createElement("div");
    transcript.dataset.centerFocusTranscript = "";
    composer.dataset.centerFocusComposer = "";
    anchor.className = "question-card-anchor";
    anchor.append(card);
    transcript.append(anchor, backgroundButton);
    root.append(transcript, composer);
    document.body.append(root);
    vi.spyOn(root, "getBoundingClientRect").mockReturnValue(rect(0, 0, 1000, 800));
    vi.spyOn(card, "getBoundingClientRect").mockReturnValue(rect(200, 160, 600, 400));
    vi.spyOn(window, "matchMedia").mockImplementation(() => ({
      matches: false,
    } as MediaQueryList));
    Object.defineProperty(document, "startViewTransition", {
      configurable: true,
      value: undefined,
    });
    const stage = createCenterFocusStage(root);

    stage.show({ sessionKey: "session-a", toolCallId: "form-a", element: card });

    expect(root.dataset.centerFocusActive).toBe("true");
    expect(card.classList.contains("center-focused-card")).toBe(true);
    expect(transcript.dataset.centerFocusLocked).toBe("true");
    expect(backgroundButton.inert).toBe(true);
    expect(composer.inert).toBe(true);

    stage.hide("form-a");

    expect(root.dataset.centerFocusActive).toBeUndefined();
    expect(card.classList.contains("center-focused-card")).toBe(false);
    expect(transcript.dataset.centerFocusLocked).toBeUndefined();
    expect(backgroundButton.inert).toBe(false);
    expect(composer.inert).toBe(false);
    stage.destroy();
  });

  it("keeps a focused mobile card inside dynamic viewport and safe-area bounds", () => {
    const root = document.createElement("main");
    const transcript = document.createElement("div");
    const anchor = document.createElement("div");
    const card = document.createElement("article");
    const composer = document.createElement("div");
    transcript.dataset.centerFocusTranscript = "";
    composer.dataset.centerFocusComposer = "";
    anchor.className = "question-card-anchor";
    anchor.append(card);
    transcript.append(anchor);
    root.append(transcript, composer);
    document.body.append(root);
    const rootRect = vi.spyOn(root, "getBoundingClientRect")
      .mockReturnValue(rect(0, 60, 390, 700));
    vi.spyOn(card, "getBoundingClientRect").mockReturnValue(rect(16, 220, 358, 900));
    vi.spyOn(window, "matchMedia").mockImplementation(query => ({
      matches: query === "(prefers-reduced-motion: reduce)",
    } as MediaQueryList));
    Object.defineProperty(document, "startViewTransition", {
      configurable: true,
      value: undefined,
    });
    const stage = createCenterFocusStage(root);

    stage.show({ sessionKey: "session-a", toolCallId: "form-a", element: card });

    expect(root.dataset.centerFocusActive).toBe("true");
    expect(anchor.style.height).toBe("900px");
    expect(card.style.getPropertyValue("--center-focus-left")).toBe(
      "calc(0px + 14px + env(safe-area-inset-left, 0px))",
    );
    expect(card.style.getPropertyValue("--center-focus-top")).toBe(
      "calc(max(60px, var(--mobile-header-offset, calc(env(safe-area-inset-top, 0px) + 50px))) + 14px)",
    );
    expect(card.style.getPropertyValue("--center-focus-width")).toBe(
      "calc(390px - 28px - env(safe-area-inset-left, 0px) - env(safe-area-inset-right, 0px))",
    );
    expect(card.style.getPropertyValue("--center-focus-max-height")).toBe(
      "calc(min(760px, 100dvh) - max(60px, var(--mobile-header-offset, calc(env(safe-area-inset-top, 0px) + 50px))) - 28px - env(safe-area-inset-bottom, 0px))",
    );

    rootRect.mockReturnValue(rect(0, 48, 430, 620));
    window.dispatchEvent(new Event("resize"));

    expect(root.dataset.centerFocusActive).toBe("true");
    expect(card.style.getPropertyValue("--center-focus-top")).toBe(
      "calc(max(48px, var(--mobile-header-offset, calc(env(safe-area-inset-top, 0px) + 50px))) + 14px)",
    );
    expect(card.style.getPropertyValue("--center-focus-width")).toContain("430px");

    stage.hide("form-a");

    expect(root.dataset.centerFocusActive).toBeUndefined();
    expect(card.getAttribute("style")).toBeNull();
    expect(anchor.getAttribute("style")).toBeNull();
    expect(composer.inert).toBe(false);
    stage.destroy();
  });

  it("keeps long mobile interactions scrollable without moving the transcript", () => {
    const root = document.createElement("main");
    const transcript = document.createElement("div");
    const anchor = document.createElement("div");
    const card = document.createElement("article");
    const scrollRegion = document.createElement("div");
    const fields = document.createElement("div");
    const actions = document.createElement("div");
    const submit = document.createElement("button");
    const composer = document.createElement("div");
    const composerInput = document.createElement("textarea");
    transcript.dataset.centerFocusTranscript = "";
    composer.dataset.centerFocusComposer = "";
    anchor.className = "question-card-anchor";
    actions.className = "question-actions";
    scrollRegion.className = "question-form-scroll-region";
    submit.textContent = "提交";
    actions.append(submit);
    scrollRegion.append(fields);
    card.append(scrollRegion, actions);
    anchor.append(card);
    transcript.append(anchor);
    composer.append(composerInput);
    root.append(transcript, composer);
    document.body.append(root);
    transcript.scrollTop = 240;
    vi.spyOn(root, "getBoundingClientRect").mockReturnValue(rect(0, 60, 390, 700));
    vi.spyOn(card, "getBoundingClientRect").mockReturnValue(rect(16, 220, 358, 1_200));
    Object.defineProperties(scrollRegion, {
      clientHeight: { configurable: true, value: 620 },
      scrollHeight: { configurable: true, value: 1_200 },
    });
    vi.spyOn(window, "matchMedia").mockImplementation(query => ({
      matches: query === "(prefers-reduced-motion: reduce)",
    } as MediaQueryList));
    const submitted = vi.fn();
    submit.addEventListener("click", submitted);
    composerInput.focus();
    const stage = createCenterFocusStage(root);

    stage.show({ sessionKey: "session-a", toolCallId: "long-form", element: card });
    scrollRegion.scrollTop = scrollRegion.scrollHeight - scrollRegion.clientHeight;
    scrollRegion.dispatchEvent(new Event("scroll"));
    submit.click();

    expect(card.classList).toContain("center-focus-mobile-card");
    expect(card.scrollTop).toBe(0);
    expect(scrollRegion.scrollTop).toBe(580);
    expect(transcript.scrollTop).toBe(240);
    expect(transcript.dataset.centerFocusLocked).toBe("true");
    expect(actions.closest(".center-focused-card")).toBe(card);
    expect(scrollRegion.contains(actions)).toBe(false);
    expect(submitted).toHaveBeenCalledOnce();
    expect(document.activeElement).not.toBe(composerInput);
    expect(composer.inert).toBe(true);

    stage.hide("long-form");

    expect(card.classList).not.toContain("center-focus-mobile-card");
    expect(transcript.dataset.centerFocusLocked).toBeUndefined();
    expect(composer.inert).toBe(false);
    stage.destroy();
  });
});
