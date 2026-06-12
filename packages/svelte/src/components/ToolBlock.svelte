<script lang="ts">
  import { tick } from "svelte";
  import type {
    ChatContentBlock,
    JsonObject,
    JsonValue,
    ToolBlockStatus,
  } from "../composables/bridgeStore.svelte";

  type ToolContentBlock = Extract<ChatContentBlock, { kind: "tool" }>;

  let {
    block,
    onrendered,
  }: {
    block: ToolContentBlock;
    onrendered?: () => void;
  } = $props();

  let expanded = $state(false);

  const inline = $derived(buildToolInlineModel(block));
  const detail = $derived(buildToolDetailModel(block));
  const trailingKind = $derived(
    inline.meta ? "meta" : inline.diffStats ? "diff" : "empty",
  );

  $effect(() => {
    block;
    void tick().then(() => onrendered?.());
  });

  function buildToolInlineModel(toolBlock: ToolContentBlock) {
    const args = asRecord(toolBlock.toolArgs);

    return {
      label: humanizeToolName(toolBlock.toolName),
      title: formatToolTitle(toolBlock.toolName, args),
      meta:
        formatToolMeta(
          toolBlock.toolName,
          args,
          toolBlock.resultText,
          toolBlock.toolStatus,
        ) ?? toolStatusMeta(toolBlock.toolStatus),
      diffStats: buildDiffStats(
        toolBlock.toolName,
        args,
        toolBlock.resultDetails,
        toolBlock.toolStatus,
      ),
    };
  }

  function buildToolDetailModel(toolBlock: ToolContentBlock) {
    const args = asRecord(toolBlock.toolArgs);
    const path = stringValue(args, "path");
    const command =
      toolBlock.toolName === "bash"
        ? formatBashCommand(stringValue(args, "command"))
        : undefined;
    const diff = blockResultDiff(toolBlock.resultDetails)?.replace(/\r/g, "").trim();

    if (toolBlock.toolName === "edit") {
      if (diff) {
        return { kind: "diff", text: diff, path } as const;
      }
    }

    if (toolBlock.toolName === "write") {
      const content = stringValue(args, "content");
      if (typeof content === "string") {
        return content.length > 0
          ? ({ kind: "code", text: content.replace(/\r/g, ""), path } as const)
          : ({ kind: "empty", path } as const);
      }
    }

    const text = toolBlock.resultText?.replace(/\r/g, "").trim();
    if (!text) {
      if (toolBlock.toolName === "bash" && command) {
        return { kind: "bash", path, command } as const;
      }
      return { kind: "empty", path } as const;
    }
    if (toolBlock.toolName === "read") {
      return { kind: "code", text, path } as const;
    }
    if (toolBlock.toolName === "bash") {
      return { kind: "bash", text, path, command } as const;
    }
    return { kind: "text", text, path } as const;
  }

  function formatToolTitle(
    toolName: string,
    args: JsonObject | undefined,
  ): string {
    switch (toolName) {
      case "read": {
        const path = stringValue(args, "path");
        if (!path) return humanizeToolName(toolName);
        const offset = numberValue(args, "offset");
        const limit = numberValue(args, "limit");
        if (offset === undefined && limit === undefined) return path;
        const startLine = offset ?? 1;
        const endLine = limit !== undefined ? startLine + limit - 1 : undefined;
        return endLine !== undefined
          ? `${path}:${startLine}-${endLine}`
          : `${path}:${startLine}`;
      }
      case "bash": {
        const command = stringValue(args, "command");
        if (!command) return humanizeToolName(toolName);
        const lines = command.replace(/\r/g, "").split("\n");
        const suffix =
          lines.length > 1
            ? ` (+${lines.length - 1} more line${lines.length - 1 > 1 ? "s" : ""})`
            : "";
        return `${lines[0]}${suffix}`;
      }
      case "edit":
      case "write": {
        return stringValue(args, "path") || humanizeToolName(toolName);
      }
      default:
        return humanizeToolName(toolName);
    }
  }

  function formatToolMeta(
    toolName: string,
    args: JsonObject | undefined,
    resultText: string | undefined,
    status: ToolBlockStatus,
  ): string | undefined {
    switch (toolName) {
      case "bash": {
        const parts: string[] = [];
        const exitCode = bashExitCode(resultText, status);
        if (exitCode !== undefined) parts.push(`exit ${exitCode}`);
        const timeout = numberValue(args, "timeout");
        if (timeout !== undefined) parts.push(`timeout ${timeout}s`);
        return parts.join(" · ") || undefined;
      }
      case "write": {
        const content = stringValue(args, "content");
        if (!content) return undefined;
        const lines = content.replace(/\r/g, "").split("\n").length;
        return `${lines} line${lines === 1 ? "" : "s"}`;
      }
      default:
        return undefined;
    }
  }

  function humanizeToolName(toolName: string): string {
    if (!toolName) return "Tool";
    return toolName
      .split(/[_-]+/)
      .filter(Boolean)
      .map(part => part[0]!.toUpperCase() + part.slice(1))
      .join(" ");
  }

  function asRecord(value: JsonValue | undefined): JsonObject | undefined {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return undefined;
    }
    return value;
  }

  function stringValue(args: JsonObject | undefined, key: string): string | undefined {
    const value = args?.[key];
    return typeof value === "string" ? value : undefined;
  }

  function numberValue(args: JsonObject | undefined, key: string): number | undefined {
    const value = args?.[key];
    return typeof value === "number" ? value : undefined;
  }

  function bashExitCode(
    resultText: string | undefined,
    status: ToolBlockStatus,
  ): number | undefined {
    if (!resultText) return status === "success" ? 0 : undefined;
    const match = resultText.match(/Command exited with code (\d+)/i);
    if (match) return Number(match[1]);
    return status === "success" ? 0 : undefined;
  }

  function formatBashCommand(command: string | undefined): string | undefined {
    if (!command) return undefined;
    const normalized = command.replace(/\r/g, "");
    if (!normalized.trim()) return undefined;
    return normalized
      .split("\n")
      .map(line => `$ ${line}`)
      .join("\n");
  }

  function toolStatusMeta(status: ToolBlockStatus): string | undefined {
    if (status === "pending") return "running";
    if (status === "error") return "error";
    return undefined;
  }

  function blockResultDiff(resultDetails: JsonValue | undefined): string | undefined {
    return findDiffString(resultDetails, 0);
  }

  function findDiffString(
    value: JsonValue | undefined,
    depth: number,
  ): string | undefined {
    if (depth > 3 || value === undefined || value === null) return undefined;

    if (typeof value === "string") {
      const trimmed = value.trim();
      if (!trimmed) return undefined;
      if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
        try {
          return findDiffString(JSON.parse(trimmed) as JsonValue, depth + 1);
        } catch {
          // Fall through and treat the string itself as a possible diff.
        }
      }
      return looksLikeDiffString(trimmed) ? trimmed : undefined;
    }

    if (Array.isArray(value)) {
      for (const item of value) {
        const diff = findDiffString(item, depth + 1);
        if (diff) return diff;
      }
      return undefined;
    }

    if (typeof value !== "object") return undefined;
    for (const key of ["diff", "patch", "unifiedDiff"]) {
      const diff = findDiffString(value[key], depth + 1);
      if (diff) return diff;
    }
    for (const key of ["details", "result", "data"]) {
      const diff = findDiffString(value[key], depth + 1);
      if (diff) return diff;
    }
    return undefined;
  }

  function looksLikeDiffString(value: string): boolean {
    return (
      value.startsWith("--- ") ||
      value.startsWith("+++ ") ||
      value.startsWith("@@ ") ||
      value.includes("\n@@ ") ||
      /^[ +-]\s*\d+\s/m.test(value)
    );
  }

  function buildDiffStats(
    toolName: string,
    _args: JsonObject | undefined,
    resultDetails: JsonValue | undefined,
    status: ToolBlockStatus,
  ): { added: number; removed: number } | undefined {
    if (toolName !== "edit" || status !== "success") return undefined;
    const diffText = blockResultDiff(resultDetails);
    if (!diffText) return undefined;
    let added = 0;
    let removed = 0;
    for (const line of diffText.replace(/\r/g, "").split("\n")) {
      if (
        line.startsWith("+++") ||
        line.startsWith("---") ||
        line.startsWith("@@")
      ) {
        continue;
      }
      if (line.startsWith("+")) added += 1;
      if (line.startsWith("-")) removed += 1;
    }
    return added || removed ? { added, removed } : undefined;
  }

  function emptyState(): string {
    if (block.toolStatus === "pending") return "Waiting for tool result.";
    if (block.toolName === "write" && detail.kind === "empty") return "File is empty.";
    return "No text result.";
  }
</script>

<div class="tool-inline-block">
  <div class="tool-inline" data-status={block.toolStatus}>
    <button
      type="button"
      class="tool-inline-toggle"
      aria-expanded={expanded}
      onclick={() => (expanded = !expanded)}
    >
      <span class="tool-inline-summary">
        <span class="tool-inline-name">{block.toolName || "tool"}</span>
        {#if inline.title !== inline.label}
          <span class="tool-inline-params">{inline.title}</span>
        {/if}
      </span>
      <span class="tool-inline-trailing" hidden={trailingKind === "empty"}>
        <span class="tool-inline-meta" hidden={trailingKind !== "meta"}>
          {inline.meta}
        </span>
        <span class="tool-inline-diff" hidden={trailingKind !== "diff"}>
          <span class="tool-inline-diff-added">+{inline.diffStats?.added ?? 0}</span>
          <span class="tool-inline-diff-removed">-{inline.diffStats?.removed ?? 0}</span>
        </span>
      </span>
    </button>

    {#if expanded}
      <div class="tool-inline-details">
        {#if detail.kind !== "empty"}
          <section class="tool-inline-section">
            {#if detail.kind === "bash"}
              <div class="tool-inline-code-panel">
                {#if detail.command}
                  <pre class="tool-inline-code-output tool-inline-command-output">{detail.command}</pre>
                {/if}
                {#if detail.text}
                  <pre class="tool-inline-code-output">{detail.text}</pre>
                {/if}
              </div>
            {:else if detail.kind === "code" || detail.kind === "diff"}
              <div class="tool-inline-code-panel">
                <pre class="tool-inline-code-output">{detail.text}</pre>
              </div>
            {:else}
              <pre class="tool-inline-pre">{detail.text}</pre>
            {/if}
          </section>
        {:else}
          <div class="tool-inline-empty">{emptyState()}</div>
        {/if}
      </div>
    {/if}
  </div>
</div>

<style>
  .tool-inline-block {
    max-width: 100%;
  }

  .tool-inline {
    border: 1px solid #d8dee8;
    border-radius: 7px;
    background: #f8fafc;
    overflow: hidden;
  }

  .tool-inline-toggle {
    width: 100%;
    min-height: 38px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    border: 0;
    background: transparent;
    padding: 8px 10px;
    color: #172033;
    cursor: pointer;
    text-align: left;
  }

  .tool-inline-toggle:hover .tool-inline-name,
  .tool-inline-toggle:hover .tool-inline-params,
  .tool-inline-toggle:hover .tool-inline-meta {
    color: #111827;
  }

  .tool-inline-summary {
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .tool-inline-name {
    color: #334155;
    font-size: 13px;
    font-weight: 700;
    white-space: nowrap;
  }

  .tool-inline-params {
    min-width: 0;
    overflow: hidden;
    color: #64748b;
    font-family:
      ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono",
      monospace;
    font-size: 12px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .tool-inline-trailing {
    display: flex;
    flex: 0 0 auto;
    align-items: center;
    gap: 8px;
  }

  .tool-inline-meta,
  .tool-inline-diff {
    color: #64748b;
    font-size: 12px;
  }

  .tool-inline-diff {
    display: inline-flex;
    gap: 6px;
    font-family:
      ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono",
      monospace;
  }

  .tool-inline-trailing[hidden],
  .tool-inline-meta[hidden],
  .tool-inline-diff[hidden] {
    display: none;
  }

  .tool-inline-diff-added {
    color: #047857;
  }

  .tool-inline-diff-removed {
    color: #be123c;
  }

  .tool-inline[data-status="error"] .tool-inline-name,
  .tool-inline[data-status="error"] .tool-inline-meta {
    color: #9f1239;
  }

  .tool-inline-details {
    border-top: 1px solid #d8dee8;
    background: #ffffff;
  }

  .tool-inline-section {
    margin: 0;
  }

  .tool-inline-code-panel {
    max-width: 100%;
    overflow-x: auto;
    background: #f8fafc;
  }

  .tool-inline-code-output,
  .tool-inline-pre {
    margin: 0;
    padding: 10px 12px;
    color: #172033;
    font-family:
      ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono",
      monospace;
    font-size: 12px;
    line-height: 1.55;
    white-space: pre-wrap;
  }

  .tool-inline-command-output {
    border-bottom: 1px solid #d8dee8;
    color: #475569;
  }

  .tool-inline-empty {
    padding: 10px 12px;
    color: #64748b;
    font-size: 13px;
  }
</style>
