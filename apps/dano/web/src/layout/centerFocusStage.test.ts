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
  it("focuses the same card within center bounds and restores the inline presentation", () => {
    const root = document.createElement("main");
    const transcript = document.createElement("div");
    const anchor = document.createElement("div");
    const card = document.createElement("article");
    const backgroundButton = document.createElement("button");
    const composer = document.createElement("div");
    const composerInput = document.createElement("textarea");
    transcript.dataset.centerFocusTranscript = "";
    composer.dataset.centerFocusComposer = "";
    composerInput.value = "保留的草稿";
    anchor.className = "question-card-anchor";
    anchor.append(card);
    transcript.append(anchor, backgroundButton);
    composer.append(composerInput);
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
});
