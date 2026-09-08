/** @vitest-environment happy-dom */

import { mount, tick, unmount } from "svelte";
import { createClassComponent } from "svelte/legacy";
import { afterEach, describe, expect, it, vi } from "vitest";
import ComposerBar from "./ComposerBar.svelte";

const bridgeClient = vi.hoisted(() => ({ id: null as string | null }));

vi.mock("../composables/bridgeStore.svelte", () => ({
  getBridgeClientId: () => bridgeClient.id,
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(r => {
    resolve = r;
  });
  return { promise, resolve };
}

describe("ComposerBar prompt submission", () => {
  afterEach(() => {
    bridgeClient.id = null;
    vi.unstubAllGlobals();
    document.body.replaceChildren();
  });

  it("keeps the original display name while submitting the uploaded WebP metadata", async () => {
    bridgeClient.id = "client_1";
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      id: "converted", name: "screen.webp", size: 3, mimeType: "image/webp",
      path: "/uploads/hash.webp", relativePath: "uploads/hash.webp",
      previewUrl: "/api/uploads/converted/preview",
    }), { status: 200, headers: { "Content-Type": "application/json" } })));
    const onSubmit = vi.fn().mockResolvedValue(false);
    const target = document.createElement("div");
    document.body.append(target);
    const component = mount(ComposerBar, { target, props: { connectionStatus: "connected", onSubmit } });
    try {
      await tick();
      const input = target.querySelector<HTMLInputElement>('input[type="file"]')!;
      Object.defineProperty(input, "files", { value: [new File(["original bytes"], "screen.png", { type: "image/png" })] });
      input.dispatchEvent(new Event("change", { bubbles: true }));
      await vi.waitFor(() => expect(target.textContent).toContain("3 B"));
      expect(target.querySelector('button[aria-label="查看 screen.png"]')).not.toBeNull();
      target.querySelector<HTMLButtonElement>('button[aria-label="发送消息"]')!.click();
      await vi.waitFor(() => expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
        files: [expect.objectContaining({ name: "screen.png", size: 3, mimeType: "image/webp", relativePath: "uploads/hash.webp" })],
      })));
    } finally {
      await unmount(component);
    }
  });

  it("rejects oversized originals at the file input before uploading", async () => {
    bridgeClient.id = "client_1";
    vi.stubGlobal("fetch", vi.fn(async (_url: unknown, init?: RequestInit) => {
      if (init?.method === "GET") return new Response("", { status: 404 });
      return new Response(JSON.stringify({ id: "small", name: "notes.txt", size: 4, mimeType: "text/plain", path: "/uploads/notes.txt" }));
    }));
    const target = document.createElement("div");
    document.body.append(target);
    const component = mount(ComposerBar, { target, props: { connectionStatus: "connected" } });
    try {
      await tick();
      const file = new File(["image"], "large.png", { type: "image/png" });
      Object.defineProperty(file, "size", { value: 50 * 1024 * 1024 + 1 });
      const input = target.querySelector<HTMLInputElement>('input[type="file"]')!;
      Object.defineProperty(input, "files", { value: [file, new File(["note"], "notes.txt", { type: "text/plain" })] });
      input.dispatchEvent(new Event("change", { bubbles: true }));
      await vi.waitFor(() => expect(target.textContent).toContain("4 B"));
      const posts = vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === "POST");
      expect(posts).toHaveLength(1);
      expect((posts[0]![1]?.body as File).name).toBe("notes.txt");
      expect(target.querySelector('button[aria-label="查看 large.png"]')).toBeNull();
    } finally {
      await unmount(component);
    }
  });

  it("uses the configured assistant name in the input placeholder", async () => {
    const originalConfig = window.__PI_WEB_CONFIG__;
    window.__PI_WEB_CONFIG__ = { productName: "My Agent" };
    const target = document.createElement("div");
    document.body.append(target);
    const component = mount(ComposerBar, {
      target,
      props: { connectionStatus: "connected" },
    });

    try {
      expect(
        target.querySelector<HTMLTextAreaElement>("textarea")?.placeholder,
      ).toBe("想让My Agent帮你处理什么问题");
    } finally {
      window.__PI_WEB_CONFIG__ = originalConfig;
      await unmount(component);
    }
  });

  it("keeps input available during compaction and exposes stop when empty", async () => {
    const onAbort = vi.fn();
    const onSubmit = vi.fn().mockResolvedValue(true);
    const target = document.createElement("div");
    document.body.appendChild(target);
    const component = mount(ComposerBar, {
      target,
      props: {
        connectionStatus: "connected",
        isCompacting: true,
        onAbort,
        onSubmit,
      },
    });

    try {
      await tick();
      const textarea = target.querySelector<HTMLTextAreaElement>("textarea")!;
      const action = target.querySelector<HTMLButtonElement>(".send-btn")!;
      expect(textarea.disabled).toBe(false);
      expect(action.getAttribute("aria-label")).toBe("停止响应");
      action.click();
      expect(onAbort).toHaveBeenCalledOnce();

      textarea.value = "压缩完成后继续";
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
      await tick();
      expect(action.getAttribute("aria-label")).toBe("发送消息");
      action.click();
      await tick();
      expect(onSubmit).toHaveBeenCalledWith(
        expect.objectContaining({ message: "压缩完成后继续" }),
      );
    } finally {
      await unmount(component);
    }
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
      expect(
        target.querySelectorAll('button[aria-label^="查看 "]'),
      ).toHaveLength(1);
    } finally {
      await unmount(component);
    }
  });

  it("retains the edited payload and edit state when revision submission is rejected", async () => {
    const onSubmit = vi.fn().mockResolvedValue(false);
    const target = document.createElement("div");
    document.body.append(target);
    const component = mount(ComposerBar, {
      target,
      props: {
        connectionStatus: "connected",
        revision: {
          entryId: "historical-user-message",
          text: "historical message",
          images: [
            { type: "image", data: "aGlzdG9yaWNhbA==", mimeType: "image/jpeg" },
          ],
        },
        onSubmit,
      },
    });

    try {
      await tick();
      const textarea = target.querySelector<HTMLTextAreaElement>("textarea")!;
      textarea.value = "edited historical message";
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
      target.querySelector<HTMLButtonElement>(
        'button[aria-label="发送消息"]',
      )!.click();
      await Promise.resolve();
      await Promise.resolve();
      await tick();

      expect(onSubmit).toHaveBeenCalledWith({
        message: "edited historical message",
        images: [
          { type: "image", data: "aGlzdG9yaWNhbA==", mimeType: "image/jpeg" },
        ],
        files: [],
        revisionEntryId: "historical-user-message",
        steer: false,
      });
      expect(textarea.value).toBe("edited historical message");
      expect(
        target.querySelector('[role="status"]')?.textContent,
      ).toContain("historical message");
      expect(
        target.querySelector('button[aria-label="查看 image-1.jpg"]'),
      ).not.toBeNull();
    } finally {
      await unmount(component);
    }
  });

  it("submits an added file together with the edited revision payload", async () => {
    bridgeClient.id = "test-client";
    const uploadedFile = {
      id: "uploaded-notes",
      name: "notes.txt",
      size: 13,
      mimeType: "text/plain",
      path: "/uploads/uploaded-notes/notes.txt",
      relativePath: "uploads/uploaded-notes/notes.txt",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === "GET") return new Response("", { status: 404 });
        return new Response(JSON.stringify(uploadedFile), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
    const onSubmit = vi.fn().mockResolvedValue(false);
    const target = document.createElement("div");
    document.body.append(target);
    const component = mount(ComposerBar, {
      target,
      props: {
        connectionStatus: "connected",
        revision: {
          entryId: "historical-user-message",
          text: "historical message",
          images: [
            { type: "image", data: "aGlzdG9yaWNhbA==", mimeType: "image/jpeg" },
          ],
        },
        onSubmit,
      },
    });

    try {
      await tick();
      const textarea = target.querySelector<HTMLTextAreaElement>("textarea")!;
      textarea.value = "edited historical message";
      textarea.dispatchEvent(new Event("input", { bubbles: true }));

      const fileInput = target.querySelector<HTMLInputElement>('input[type="file"]')!;
      Object.defineProperty(fileInput, "files", {
        configurable: true,
        value: [new File(["test contents"], "notes.txt", { type: "text/plain" })],
      });
      fileInput.dispatchEvent(new Event("change", { bubbles: true }));
      await vi.waitFor(() => {
        expect(
          target.querySelectorAll('button[aria-label^="查看 "]'),
        ).toHaveLength(2);
        expect(
          target.querySelector<HTMLButtonElement>(
            'button[aria-label="发送消息"]',
          )?.disabled,
        ).toBe(false);
      });

      target.querySelector<HTMLButtonElement>(
        'button[aria-label="发送消息"]',
      )!.click();
      await vi.waitFor(() => {
        expect(onSubmit).toHaveBeenCalledWith({
          message: "edited historical message",
          images: [
            { type: "image", data: "aGlzdG9yaWNhbA==", mimeType: "image/jpeg" },
          ],
          files: [uploadedFile],
          revisionEntryId: "historical-user-message",
          steer: false,
        });
      });
      expect(
        target.querySelector('[role="status"]')?.textContent,
      ).toContain("historical message");
    } finally {
      await unmount(component);
    }
  });

  it("restores the exact pre-edit draft and attachments when editing is cancelled", async () => {
    const onCancelRevision = vi.fn();
    const target = document.createElement("div");
    document.body.append(target);
    const component = createClassComponent({
      component: ComposerBar,
      target,
      props: {
        connectionStatus: "connected",
        editQueuedPayload: {
          text: "draft before editing",
          images: [
            { type: "image", data: "ZHJhZnQ=", mimeType: "image/png" },
          ],
        },
        onCancelRevision,
      },
    });

    try {
      await tick();
      component.$set({
        revision: {
          entryId: "historical-user-message",
          text: "historical message",
          images: [
            { type: "image", data: "aGlzdG9yaWNhbA==", mimeType: "image/jpeg" },
          ],
        },
      });
      await tick();

      const textarea = target.querySelector<HTMLTextAreaElement>("textarea");
      expect(textarea?.value).toBe("historical message");
      const revisionHeader = target.querySelector<HTMLElement>('[role="status"]');
      expect(revisionHeader).not.toBeNull();
      expect(revisionHeader?.textContent).toContain("historical message");
      expect(
        target.querySelectorAll('button[aria-label^="查看 "]'),
      ).toHaveLength(1);
      expect(
        target.querySelector<HTMLImageElement>(
          'button[aria-label="查看 image-1.jpg"] img',
        )?.src,
      ).toContain("aGlzdG9yaWNhbA==");

      if (textarea) {
        textarea.value = "changed historical message";
        textarea.dispatchEvent(new Event("input", { bubbles: true }));
      }
      target.querySelector<HTMLButtonElement>(
        'button[aria-label="移除 image-1.jpg"]',
      )?.click();
      await tick();
      expect(textarea?.value).toBe("changed historical message");
      expect(
        target.querySelectorAll('button[aria-label^="查看 "]'),
      ).toHaveLength(0);

      const cancel = target.querySelector<HTMLButtonElement>(
        'button[aria-label="取消编辑"]',
      );
      expect(cancel).not.toBeNull();
      cancel?.click();
      await tick();

      expect(onCancelRevision).toHaveBeenCalledTimes(1);
      expect(textarea?.value).toBe("draft before editing");
      expect(
        target.querySelectorAll('button[aria-label^="查看 "]'),
      ).toHaveLength(1);
      expect(
        target.querySelector<HTMLImageElement>(
          'button[aria-label="查看 image-1.png"] img',
        )?.src,
      ).toContain("ZHJhZnQ=");
    } finally {
      component.$destroy();
      target.remove();
    }
  });

  it("restores the pre-edit draft through the shared revision cancellation action", async () => {
    const onCancelRevision = vi.fn();
    const target = document.createElement("div");
    document.body.append(target);
    const component = createClassComponent({
      component: ComposerBar,
      target,
      props: {
        connectionStatus: "connected",
        editQueuedPayload: {
          text: "draft before editing",
          images: [],
        },
        onCancelRevision,
      },
    });

    try {
      await tick();
      component.$set({
        revision: {
          entryId: "historical-user-message",
          text: "historical message",
          images: [],
        },
      });
      await tick();

      expect(target.querySelector<HTMLTextAreaElement>("textarea")?.value)
        .toBe("historical message");

      (component as typeof component & { cancelRevision: () => void })
        .cancelRevision();
      await tick();

      expect(onCancelRevision).toHaveBeenCalledTimes(1);
      expect(target.querySelector<HTMLTextAreaElement>("textarea")?.value)
        .toBe("draft before editing");
    } finally {
      component.$destroy();
      target.remove();
    }
  });

  it("renders a bounded single-line original-message preview with an icon-only cancel action", async () => {
    const target = document.createElement("div");
    document.body.appendChild(target);
    const component = mount(ComposerBar, {
      target,
      props: {
        connectionStatus: "connected",
        revision: {
          entryId: "historical-user-message",
          text: "first line\n\nsecond line with more detail",
          images: [],
        },
      },
    });

    try {
      await tick();
      expect(
        target.querySelector('[role="status"]')?.textContent,
      ).toContain("first line second line with more detail");
      const composerRegion = target.querySelector('[role="region"]');
      expect(composerRegion?.querySelector('[role="status"]')).toBeNull();
      expect(composerRegion?.previousElementSibling?.getAttribute("role"))
        .toBe("status");
      const cancel = target.querySelector<HTMLButtonElement>(
        'button[aria-label="取消编辑"]',
      );
      expect(cancel).not.toBeNull();
      expect(cancel?.hasAttribute("title")).toBe(false);
    } finally {
      await unmount(component);
      target.remove();
    }
  });

});
