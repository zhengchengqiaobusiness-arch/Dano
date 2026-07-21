/** @vitest-environment happy-dom */

import { mount, tick, unmount } from "svelte";
import { afterEach, describe, expect, it, vi } from "vitest";
import ComposerBar from "./ComposerBar.svelte";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(r => {
    resolve = r;
  });
  return { promise, resolve };
}

describe("ComposerBar prompt submission", () => {
  afterEach(() => {
    document.body.replaceChildren();
  });

  it("keeps the submitted draft and blocks duplicate sends until acceptance", async () => {
    const acceptance = deferred<boolean>();
    const onSubmit = vi.fn(() => acceptance.promise);
    const target = document.createElement("div");
    document.body.append(target);
    const component = mount(ComposerBar, {
      target,
      props: {
        connectionStatus: "connected",
        onSubmit,
      },
    });

    try {
      const textarea = target.querySelector<HTMLTextAreaElement>("textarea")!;
      const send = target.querySelector<HTMLButtonElement>(".send-btn")!;
      textarea.value = "需要确认后再清空";
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
      await tick();

      send.click();
      await tick();

      expect(onSubmit).toHaveBeenCalledTimes(1);
      expect(textarea.value).toBe("需要确认后再清空");
      expect(send.disabled).toBe(true);

      send.click();
      expect(onSubmit).toHaveBeenCalledTimes(1);

      acceptance.resolve(true);
      await acceptance.promise;
      await tick();

      expect(textarea.value).toBe("");
    } finally {
      await unmount(component);
    }
  });

  it("uses the same retained-draft behavior for Enter and controlled failures", async () => {
    const onSubmit = vi.fn().mockResolvedValue(false);
    const target = document.createElement("div");
    document.body.append(target);
    const component = mount(ComposerBar, {
      target,
      props: { connectionStatus: "connected", onSubmit },
    });

    try {
      const textarea = target.querySelector<HTMLTextAreaElement>("textarea")!;
      textarea.value = "  exact draft  ";
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
      textarea.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Enter", bubbles: true }),
      );
      await Promise.resolve();
      await Promise.resolve();
      await tick();

      expect(onSubmit).toHaveBeenCalledWith(
        expect.objectContaining({ message: "exact draft" }),
      );
      expect(textarea.value).toBe("  exact draft  ");
      expect(target.querySelector<HTMLButtonElement>(".send-btn")?.disabled).toBe(
        false,
      );
    } finally {
      await unmount(component);
    }
  });

  it("clears only the acknowledged text snapshot when the user keeps editing", async () => {
    const acceptance = deferred<boolean>();
    const target = document.createElement("div");
    document.body.append(target);
    const component = mount(ComposerBar, {
      target,
      props: {
        connectionStatus: "connected",
        onSubmit: () => acceptance.promise,
      },
    });

    try {
      const textarea = target.querySelector<HTMLTextAreaElement>("textarea")!;
      textarea.value = "submitted snapshot";
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
      target.querySelector<HTMLButtonElement>(".send-btn")!.click();
      await tick();

      textarea.value = "newer unsent edit";
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
      acceptance.resolve(true);
      await acceptance.promise;
      await tick();

      expect(textarea.value).toBe("newer unsent edit");
    } finally {
      await unmount(component);
    }
  });

  it("retains the exact attachment snapshot when submission is rejected", async () => {
    const onSubmit = vi.fn().mockResolvedValue(false);
    const target = document.createElement("div");
    document.body.append(target);
    const component = mount(ComposerBar, {
      target,
      props: {
        connectionStatus: "connected",
        editQueuedPayload: {
          text: "draft with image",
          images: [
            { type: "image", data: "aGVsbG8=", mimeType: "image/png" },
          ],
        },
        onSubmit,
      },
    });

    try {
      await tick();
      target.querySelector<HTMLButtonElement>(".send-btn")!.click();
      await Promise.resolve();
      await Promise.resolve();
      await tick();

      expect(onSubmit).toHaveBeenCalledWith(
        expect.objectContaining({
          message: "draft with image",
          images: [
            { type: "image", data: "aGVsbG8=", mimeType: "image/png" },
          ],
        }),
      );
      expect(target.querySelector<HTMLTextAreaElement>("textarea")?.value).toBe(
        "draft with image",
      );
      expect(target.querySelectorAll(".attachment-chip")).toHaveLength(1);
    } finally {
      await unmount(component);
    }
  });
});
