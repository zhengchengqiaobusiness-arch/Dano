/** @vitest-environment happy-dom */
import { mount, tick, unmount } from "svelte";
import { it, expect, vi } from "vitest";
import HelpCenter from "./HelpCenter.svelte";
it("opens host chapters, filters by keyword and returns without sending a prompt", async () => {
  const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation(
    async (input) =>
      new Response(
        String(input).endsWith("index.json")
          ? JSON.stringify([
              {
                id: "first",
                title: "第一章",
                file: "first.md",
                group: "开始",
                keywords: ["图片"],
              },
              { id: "next", title: "第二章", file: "next.md", group: "开始" },
            ])
          : "# 操作步骤\n\n宿主机内容",
      ),
  );
  const target = document.createElement("div");
  target.className = "app-shell";
  document.body.append(target);
  const close = vi.fn();
  const component = mount(HelpCenter, { target, props: { onClose: close } });
  try {
    await vi.waitFor(() => expect(target.textContent).toContain("宿主机内容"));
    const input = target.querySelector("input")!;
    input.value = "图片";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    await tick();
    expect(target.querySelector('nav[aria-label="帮助手册章节"]')?.textContent).toContain("第一章");
    expect(target.querySelector('nav[aria-label="帮助手册章节"]')?.textContent).not.toContain("第二章");
    [...target.querySelectorAll("button")]
      .find((b) => b.textContent?.includes("返回对话"))!
      .click();
    expect(close).toHaveBeenCalledOnce();
    const breadcrumb = target.querySelector('nav[aria-label="帮助导航"]')!;
    expect(breadcrumb.querySelector('[aria-current="page"]')?.textContent).toBe("帮助手册");
    (breadcrumb.querySelector("button") as HTMLButtonElement).click();
    expect(close).toHaveBeenCalledTimes(2);
  } finally {
    await unmount(component);
    target.remove();
    fetcher.mockRestore();
  }
});

it("opens a discovered standalone video without requiring a Markdown file", async () => {
  const fetcher = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify([
    { id: "file:介绍视频.mp4", title: "介绍视频", video: "介绍视频.mp4" },
  ])));
  const target = document.createElement("div");
  document.body.append(target);
  const component = mount(HelpCenter, { target, props: { onClose: vi.fn() } });
  try {
    await vi.waitFor(() => expect(target.querySelector("video")?.getAttribute("src")).toBe("/api/help/%E4%BB%8B%E7%BB%8D%E8%A7%86%E9%A2%91.mp4"));
    expect(target.textContent).not.toContain("暂不可用");
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(target.querySelector("video")?.autoplay).toBe(false);
  } finally {
    await unmount(component); target.remove(); fetcher.mockRestore();
  }
});
