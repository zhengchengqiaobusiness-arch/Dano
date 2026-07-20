/** @vitest-environment happy-dom */

import { mount, tick, unmount } from "svelte";
import { describe, expect, it, vi } from "vitest";
import MarkdownRenderer from "./MarkdownRenderer.svelte";
import MarkdownRendererStreamingHarness from "./MarkdownRenderer.test-harness.svelte";

async function settleMarkdown() {
  await tick();
  await new Promise(resolve => setTimeout(resolve, 0));
  await tick();
}

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
