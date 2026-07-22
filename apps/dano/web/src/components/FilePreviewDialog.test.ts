/** @vitest-environment happy-dom */

import { mount, tick, unmount } from "svelte";
import { afterEach, describe, expect, it, vi } from "vitest";
import FilePreviewDialog from "./FilePreviewDialog.svelte";

describe("FilePreviewDialog stacking", () => {
  afterEach(() => {
    document.body.replaceChildren();
  });

  it("keeps the maximized preview in the browser modal layer", async () => {
    const isolatedContent = document.createElement("div");
    isolatedContent.style.isolation = "isolate";
    document.body.append(isolatedContent);
    const onClose = vi.fn();

    const component = mount(FilePreviewDialog, {
      target: isolatedContent,
      props: {
        preview: {
          name: "preview.png",
          src: "data:image/png;base64,aGVsbG8=",
          loading: false,
          error: "",
        },
        onClose,
      },
    });

    try {
      await tick();

      const shell = document.querySelector<HTMLDialogElement>(
        ".file-preview-shell",
      );
      expect(shell).toBeInstanceOf(HTMLDialogElement);
      expect(shell?.open).toBe(true);
      expect(shell?.parentElement).toBe(isolatedContent);

      const maximize = shell?.querySelector<HTMLButtonElement>(
        '[aria-label="Maximize dialog"]',
      );
      maximize?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await tick();
      expect(shell?.querySelector(".file-preview-dialog.maximized")).not.toBeNull();

      const restore = shell?.querySelector<HTMLButtonElement>(
        '[aria-label="Restore dialog"]',
      );
      restore?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await tick();
      expect(shell?.querySelector(".file-preview-dialog.maximized")).toBeNull();

      maximize?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await tick();
      shell
        ?.querySelector<HTMLButtonElement>(".file-preview-close")
        ?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      expect(onClose).toHaveBeenCalledOnce();

      const cancelEvent = new Event("cancel", { cancelable: true });
      shell?.dispatchEvent(cancelEvent);
      expect(cancelEvent.defaultPrevented).toBe(true);
      expect(onClose).toHaveBeenCalledTimes(2);
    } finally {
      await unmount(component);
    }
  });
});
