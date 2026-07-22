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
      expect(
        shell?.querySelector('[aria-label="Maximize dialog"]'),
      ).not.toBeNull();

      const cancelEvent = new Event("cancel", { cancelable: true });
      shell?.dispatchEvent(cancelEvent);
      expect(cancelEvent.defaultPrevented).toBe(true);
      expect(onClose).toHaveBeenCalledOnce();
    } finally {
      await unmount(component);
    }
  });
});
