/** @vitest-environment happy-dom */

import { mount, tick, unmount } from "svelte";
import { afterEach, describe, expect, it, vi } from "vitest";
import ImageLightbox from "./ImageLightbox.svelte";

function createAppTarget() {
  const target = document.createElement("div");
  target.className = "app-shell";
  document.body.append(target);
  return target;
}

describe("ImageLightbox", () => {
  afterEach(() => {
    document.body.replaceChildren();
    document.body.style.removeProperty("overflow");
  });

  it("uses the shared modal layer while retaining image navigation", async () => {
    const target = createAppTarget();
    const onClose = vi.fn();
    const onPrevious = vi.fn();
    const onNext = vi.fn();
    const component = mount(ImageLightbox, {
      target,
      props: {
        open: true,
        images: [
          { kind: "image", src: "data:image/gif;base64,R0lGODlhAQABAAAAACw=", alt: "First" },
          { kind: "image", src: "data:image/gif;base64,R0lGODlhAQABAAAAACw=", alt: "Second" },
        ],
        index: 0,
        onClose,
        onPrevious,
        onNext,
      },
    });
    await tick();

    try {
      expect(target.querySelector("[data-slot=dialog-overlay].image-lightbox-backdrop")).not.toBeNull();
      expect(target.querySelector("[data-slot=dialog-content].image-lightbox-shell")).not.toBeNull();
      expect(target.querySelector("[data-slot=dialog-overlay]")?.getAttribute("data-dano-layer")).toBe(
        "lightbox-overlay",
      );
      expect(target.querySelector("[data-slot=dialog-content]")?.getAttribute("data-dano-layer")).toBe(
        "lightbox",
      );
      expect(target.querySelector("img")?.getAttribute("alt")).toBe("First");

      document.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true }));
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
      expect(onPrevious).toHaveBeenCalledOnce();
      expect(onNext).toHaveBeenCalledOnce();

      target.querySelector<HTMLButtonElement>(".image-lightbox-close")!.click();
      expect(onClose).toHaveBeenCalledOnce();
    } finally {
      await unmount(component);
      // Dialog releases its scroll lock asynchronously. Finish that cleanup
      // while happy-dom is still alive, before tearing down document.
      await vi.waitFor(() => {
        expect(document.body.style.overflow).not.toBe("hidden");
      });
    }
  });
});
