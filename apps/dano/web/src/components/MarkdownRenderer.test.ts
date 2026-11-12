/** @vitest-environment jsdom */

import { mount, tick, unmount } from "svelte";
import { readFileSync } from "node:fs";
import { URL as NodeURL } from "node:url";
import { describe, expect, it, vi } from "vitest";
import MarkdownRenderer from "./MarkdownRenderer.svelte";
import MarkdownRendererStreamingHarness from "./MarkdownRenderer.test-harness.svelte";

const loginHtml = readFileSync(new NodeURL("../../../../../deploy/runtime-defaults/SYSTEM.md", import.meta.url), "utf8")
  .match(/<a\b[^>]*>点击登录<\/a>/)?.[0] ?? "";

async function settleMarkdown() {
  await tick();
  await new Promise(resolve => setTimeout(resolve, 0));
  await tick();
}

describe("MarkdownRenderer HTML links", () => {
  it("renders an HTML login link and preserves native current-tab activation", async () => {
    const target = document.createElement("div");
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    const component = mount(MarkdownRenderer, {
      target,
      props: { content: loginHtml },
    });
    try {
      await settleMarkdown();
      await vi.waitFor(() => expect(target.querySelector("a")).not.toBeNull());
      const link = target.querySelector<HTMLAnchorElement>("a");
      expect(link?.textContent).toBe("点击登录");
      expect(link?.getAttribute("href")).toBe("/api/auth/login?returnTo=%2F");
      expect(link?.target).toBe("_self");
      expect(link?.style.background).toBe("var(--accent)");
      expect(link?.style.color).toBe("var(--on-accent)");
      expect(link?.style.borderRadius).toBe("var(--radius-md)");
      expect(target.querySelector(".markdown-body")?.textContent?.trim()).toBe("点击登录");
      let componentPreventedClick: boolean | undefined;
      target.addEventListener("click", event => {
        componentPreventedClick = event.defaultPrevented;
        // jsdom cannot navigate documents; observe native activation first.
        event.preventDefault();
      }, { once: true });
      const click = new MouseEvent("click", { bubbles: true, cancelable: true });
      link!.dispatchEvent(click);
      const enter = new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true });
      link!.dispatchEvent(enter);
      expect(componentPreventedClick).toBe(false);
      expect(enter.defaultPrevented).toBe(false);
      expect(open).not.toHaveBeenCalled();
    } finally {
      open.mockRestore();
      await unmount(component);
    }
  });

  it("sanitizes active HTML and bound attributes before rendering", async () => {
    const target = document.createElement("div");
    const component = mount(MarkdownRenderer, {
      target,
      props: { content: [
        '<strong>安全内容</strong>',
        '<script>window.untrustedHtml = true</script>',
        '<iframe srcdoc="untrusted"></iframe>',
        '<style>body { display: none }</style>',
        '<form action="/submit"><input name="token"><button>提交</button></form>',
        '<img src="/synthetic.png" onerror="window.untrustedHtml = true">',
        '<a href="javascript:alert(1)" onclick="alert(1)">危险链接</a>',
        '<a :href="\'javascript:alert(1)\'" :onclick="\'alert(1)\'">绑定链接</a>',
        '<span data-markdown-copy="true">文本</span>',
      ].join("\n\n") },
    });
    try {
      await vi.waitFor(() => expect(target.querySelector("strong")?.textContent).toBe("安全内容"));
      expect(target.querySelector("script, iframe, style, form, input, button")).toBeNull();
      expect(target.querySelector("[onerror]")).toBeNull();
      expect(target.querySelector("[onclick]")).toBeNull();
      for (const link of target.querySelectorAll("a")) {
        expect(link.hasAttribute("href")).toBe(false);
        expect(link.hasAttribute("onclick")).toBe(false);
        expect(link.hasAttribute(":href")).toBe(false);
      }
      expect(target.querySelector("[data-markdown-copy]")).toBeNull();
    } finally {
      await unmount(component);
    }
  });

  it.each([
    '<iframe $="override" srcdoc="<script>alert(1)</script>"></iframe>',
    '<script $="override" src="https://untrusted.test/script.js"></script>',
    '<div $="override" onclick="alert(1)">被覆盖的元数据</div>',
    '<img $="" src="/synthetic.png" onerror="alert(1)">',
  ])("sanitizes HTML with overwritten parser metadata: %s", async content => {
    const target = document.createElement("div");
    const component = mount(MarkdownRenderer, { target, props: { content } });
    try {
      await settleMarkdown();
      expect(target.querySelector("script, iframe, [onclick], [onerror]")).toBeNull();
    } finally {
      await unmount(component);
    }
  });

  it("keeps HTML code examples as text and ordinary links in new tabs", async () => {
    const target = document.createElement("div");
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    const component = mount(MarkdownRenderer, {
      target,
      props: { content: '```html\n<a href="/example" target="_self">示例</a>\n```\n\n[外部链接](https://example.test/help)' },
    });
    try {
      await settleMarkdown();
      expect(target.querySelector("pre code")?.textContent).toContain('<a href="/example"');
      expect(target.querySelectorAll("a")).toHaveLength(1);
      const click = new MouseEvent("click", { bubbles: true, cancelable: true });
      target.querySelector("a")!.dispatchEvent(click);
      expect(click.defaultPrevented).toBe(true);
      expect(open).toHaveBeenCalledWith("https://example.test/help", "_blank", "noreferrer");
    } finally {
      open.mockRestore();
      await unmount(component);
    }
  });
});

describe("MarkdownRenderer tables", () => {
  it("renders Markdown tables through the shared semantic table contract", async () => {
    const target = document.createElement("div");
    const component = mount(MarkdownRenderer, {
      target,
      props: {
        content: [
          "| Left | Center | Right |",
          "| :--- | :---: | ---: |",
          "| alpha | beta | gamma |",
        ].join("\n"),
      },
    });

    try {
      await settleMarkdown();

      const scroll = target.querySelector(".ui-table-scroll");
      const table = scroll?.querySelector(":scope > table.ui-table");
      const headers = table?.querySelectorAll("thead th.ui-table-head");
      const cells = table?.querySelectorAll("tbody td.ui-table-cell");

      expect(scroll).not.toBeNull();
      expect(table).not.toBeNull();
      expect(headers).toHaveLength(3);
      expect(cells).toHaveLength(3);
      expect(headers?.[0]?.getAttribute("style")).toContain("text-align: left");
      expect(headers?.[1]?.getAttribute("style")).toContain("text-align: center");
      expect(cells?.[2]?.getAttribute("style")).toContain("text-align: right");
    } finally {
      await unmount(component);
    }
  });

  it("uses one table wrapper while an incomplete stream becomes complete", async () => {
    const target = document.createElement("div");
    const component = mount(MarkdownRendererStreamingHarness, { target });

    try {
      await settleMarkdown();
      expect(target.querySelectorAll(".ui-table-scroll")).toHaveLength(1);
      expect(target.querySelector(".ui-table tbody")).toBeNull();

      component.completeTable();
      await settleMarkdown();

      expect(target.querySelectorAll(".ui-table-scroll")).toHaveLength(1);
      expect(target.querySelectorAll(".ui-table tbody td")).toHaveLength(2);
    } finally {
      await unmount(component);
    }
  });

  it.each(["---", "----", "-----"])(
    "does not treat a partial streamed %s table separator as an element name",
    async (dashRun) => {
      const target = document.createElement("div");
      const createElement = document.createElement.bind(document);
      const createdTags: string[] = [];
      const createElementSpy = vi.spyOn(document, "createElement").mockImplementation((tagName, options) => {
        createdTags.push(tagName);
        return createElement(tagName, options);
      });
      const component = mount(MarkdownRenderer, {
        target,
        props: {
          content: `| 项目| 项目名称 | 审批状态 | 负责人 | 预算额度 | 优先级 |\n| :${dashRun} | :${dashRun}: | ${dashRun}: | :${dashRun} | :${dashRun}: |\n| [长期审批流`,
          streaming: true,
        },
      });

      try {
        await settleMarkdown();
        expect(createdTags).not.toContainEqual(expect.stringMatching(/^-{3,}$/));
      } finally {
        createElementSpy.mockRestore();
        await unmount(component);
      }
    },
  );

  it("leaves non-table Markdown outside the table contract", async () => {
    const target = document.createElement("div");
    const component = mount(MarkdownRenderer, {
      target,
      props: { content: "A plain paragraph with **emphasis**." },
    });

    try {
      await settleMarkdown();
      expect(target.querySelector(".markdown-body p")?.textContent).toBe(
        "A plain paragraph with emphasis.",
      );
      expect(target.querySelector(".ui-table-scroll")).toBeNull();
    } finally {
      await unmount(component);
    }
  });
});

 it("resolves help-relative images without rewriting code samples", async () => {
 const target=document.createElement("div");const component=mount(MarkdownRenderer,{target,props:{resourceBase:"https://example.test/api/help/guide/start.md",content:"![示意](../media/demo.png)\n\n`![示意](../media/demo.png)`"}});
 try {await vi.waitFor(()=>expect(target.querySelector("img")?.getAttribute("src")).toBe("https://example.test/api/help/media/demo.png"));expect(target.querySelector("code")?.textContent).toBe("![示意](../media/demo.png)");} finally {await unmount(component);}
 });

it("keeps host manual videos controlled and resolves their relative sources", async () => {
 const target=document.createElement("div");const component=mount(MarkdownRenderer,{target,props:{resourceBase:"https://example.test/api/help/guide/start.md",content:'<video autoplay poster="../media/cover.png"><source src="../media/demo.mp4"><track src="../media/demo.vtt"></video>'}});
 try {await vi.waitFor(()=>expect(target.querySelector("video")).not.toBeNull());expect(target.querySelector("video")?.hasAttribute("autoplay")).toBe(false);expect(target.querySelector("source")?.getAttribute("src")).toBe("https://example.test/api/help/media/demo.mp4");}finally{await unmount(component);}
});
