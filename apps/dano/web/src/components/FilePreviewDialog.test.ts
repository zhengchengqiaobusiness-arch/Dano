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

      const controls = [
        ["缩小", "lucide-zoom-out"],
        ["放大图片", "lucide-zoom-in"],
        ["原始尺寸", null],
        ["适应窗口", "lucide-scan"],
        ["最大化", "lucide-expand"],
        ["关闭", "lucide-x"],
      ] as const;
      for (const [label, iconClass] of controls) {
        const control = shell?.querySelector<HTMLButtonElement>(
          `[aria-label="${label}"]`,
        );
        expect(control?.title).toBe(label);
        if (iconClass) expect(control?.querySelector(`.${iconClass}`)).not.toBeNull();
      }

      expect(
        Array.from(shell?.querySelectorAll(".file-preview-controls button") ?? []).map(
          (control) => control.getAttribute("aria-label"),
        ),
      ).toEqual(["缩小", "放大图片", "原始尺寸", "适应窗口"]);
      expect(
        Array.from(
          shell?.querySelectorAll(".file-preview-header button") ?? [],
        ).map((control) => control.getAttribute("aria-label")),
      ).toEqual(["最大化", "关闭", "缩小", "放大图片", "原始尺寸", "适应窗口"]);

      const expand = shell?.querySelector<HTMLButtonElement>(
        '[aria-label="最大化"]',
      );
      expand?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await tick();
      expect(shell?.querySelector(".file-preview-dialog.maximized")).not.toBeNull();

      const shrink = shell?.querySelector<HTMLButtonElement>(
        '[aria-label="还原"]',
      );
      expect(shrink?.title).toBe("还原");
      expect(shrink?.querySelector(".lucide-shrink")).not.toBeNull();
      shrink?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await tick();
      expect(shell?.querySelector(".file-preview-dialog.maximized")).toBeNull();

      expand?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
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
