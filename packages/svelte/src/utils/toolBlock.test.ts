import { describe, expect, it } from "vitest";
import {
  buildToolDetailModel,
  buildToolInlineModel,
  detailText,
} from "./toolBlock";
import type { ChatContentBlock } from "../composables/bridgeStore.svelte";

type ToolContentBlock = Extract<ChatContentBlock, { kind: "tool" }>;

describe("toolBlock", () => {
  it("summarizes bash invocation with command and exit status", () => {
    const block: ToolContentBlock = {
      kind: "tool",
      toolName: "bash",
      toolArgs: { command: "pnpm test\npnpm run build", timeout: 180 },
      argumentsText: "",
      resultText: "ok",
      toolStatus: "success",
    };

    expect(buildToolInlineModel(block)).toMatchObject({
      label: "Bash",
      title: "pnpm test (+1 more line)",
      meta: "exit 0 · timeout 180s",
    });
  });

  it("keeps running tool calls collapsed with status metadata", () => {
    const block: ToolContentBlock = {
      kind: "tool",
      toolName: "read",
      toolArgs: { path: "README.md", offset: 3, limit: 2 },
      argumentsText: "",
      toolStatus: "pending",
    };

    expect(buildToolInlineModel(block)).toMatchObject({
      title: "README.md:3-4",
      meta: "running",
    });
    expect(buildToolDetailModel(block)).toMatchObject({ kind: "empty" });
  });

  it("derives edit diff stats from result details", () => {
    const block: ToolContentBlock = {
      kind: "tool",
      toolName: "edit",
      toolArgs: { path: "src/App.svelte" },
      argumentsText: "",
      resultDetails: "@@ -1 +1 @@\n-old\n+new\n+next",
      toolStatus: "success",
    };

    expect(buildToolInlineModel(block).diffStats).toEqual({
      added: 2,
      removed: 1,
    });
    expect(detailText(buildToolDetailModel(block))).toBe(
      "@@ -1 +1 @@\n-old\n+new\n+next",
    );
  });

  it("falls back to edit arguments when no diff result is present", () => {
    const block: ToolContentBlock = {
      kind: "tool",
      toolName: "edit",
      toolArgs: {
        path: "src/App.svelte",
        edits: [{ oldText: "old\n", newText: "new\nnext\n" }],
      },
      argumentsText: "",
      toolStatus: "success",
    };

    expect(buildToolInlineModel(block).diffStats).toEqual({
      added: 2,
      removed: 1,
    });
    expect(detailText(buildToolDetailModel(block))).toContain("@@ edit 1 @@");
  });

  it("formats skill-like runtime invocations when emitted as tool blocks", () => {
    const block: ToolContentBlock = {
      kind: "tool",
      toolName: "invoke_skill",
      toolArgs: { skillName: "oa-approval" },
      argumentsText: "",
      toolStatus: "pending",
    };

    expect(buildToolInlineModel(block)).toMatchObject({
      variant: "skill",
      label: "使用技能",
      title: "oa-approval",
      meta: "running",
    });
  });

  it("presents SKILL.md reads as skill invocations", () => {
    const block: ToolContentBlock = {
      kind: "tool",
      toolName: "read",
      toolArgs: {
        path: "/Users/joseph/.agents/skills/oa-approval/SKILL.md",
      },
      argumentsText: "",
      resultText: "---\nname: oa-approval\n---",
      toolStatus: "success",
    };

    expect(buildToolInlineModel(block)).toMatchObject({
      variant: "skill",
      label: "使用技能",
      title: "oa-approval",
      meta: undefined,
    });
    expect(buildToolDetailModel(block)).toMatchObject({ kind: "code" });
  });
});
