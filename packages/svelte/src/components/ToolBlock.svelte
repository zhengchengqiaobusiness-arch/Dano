<script lang="ts">
  import { tick } from "svelte";
  import type { ChatContentBlock } from "../composables/bridgeStore.svelte";
  import {
    buildToolDetailModel,
    buildToolInlineModel,
    detailText,
    type ToolContentBlock,
  } from "../utils/toolBlock";

  let {
    block,
    onrendered,
  }: {
    block: Extract<ChatContentBlock, { kind: "tool" }>;
    onrendered?: () => void;
  } = $props();

  let expanded = $state(false);

  const inline = $derived(buildToolInlineModel(block as ToolContentBlock));
  const detail = $derived(buildToolDetailModel(block as ToolContentBlock));
  const trailingKind = $derived(
    inline.diffStats ? "diff" : inline.meta ? "meta" : "empty",
  );
  const visibleDetailText = $derived(detailText(detail));

  $effect(() => {
    block;
    expanded;
    void tick().then(() => onrendered?.());
  });

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
        <span class="tool-inline-name">{inline.label}</span>
        {#if inline.variant === "skill"}
          <span class="tool-inline-skill" title={inline.title}>
            <span class="tool-inline-skill-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" focusable="false">
                <rect x="4" y="4" width="16" height="16" rx="5" />
                <path d="M13.2 6.8 8.8 12h3.4l-1.4 5.2 4.5-6h-3.4l1.3-4.4Z" />
              </svg>
            </span>
            <span class="tool-inline-skill-name">{inline.title}</span>
          </span>
        {:else if inline.title !== inline.label}
          <span class="tool-inline-params">{inline.title}</span>
        {/if}
      </span>
      {#if trailingKind !== "empty"}
        <span class="tool-inline-trailing">
          {#if trailingKind === "meta"}
            <span class="tool-inline-meta">{inline.meta ?? ""}</span>
          {:else if inline.diffStats}
            <span
              class="tool-inline-diff"
              aria-label={`${inline.diffStats.added} additions, ${inline.diffStats.removed} deletions`}
            >
              <span class="tool-inline-diff-added">+{inline.diffStats.added}</span>
              <span class="tool-inline-diff-removed">-{inline.diffStats.removed}</span>
            </span>
          {/if}
        </span>
      {/if}
    </button>

    {#if expanded}
      <div class="tool-inline-details">
        {#if detail.kind !== "empty" && visibleDetailText}
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
                <pre class="tool-inline-code-output">{visibleDetailText}</pre>
              </div>
            {:else}
              <pre class="tool-inline-pre">{visibleDetailText}</pre>
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
    overflow-anchor: none;
  }

  .tool-inline {
    --tool-skill-accent: #2563eb;
    --tool-skill-accent-soft: #dbeafe;
    display: flex;
    flex-direction: column;
    gap: 4px;
    overflow-anchor: none;
  }

  .tool-inline-toggle {
    width: 100%;
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    column-gap: 8px;
    border: 0;
    background: transparent;
    padding: 0;
    color: inherit;
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
    display: inline-flex;
    align-items: center;
    gap: 8px;
    line-height: 17px;
  }

  .tool-inline-name {
    flex: none;
    display: inline-flex;
    align-items: center;
    color: #475569;
    font-family:
      ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono",
      monospace;
    font-size: 12px;
    font-weight: 700;
    line-height: 17px;
    white-space: nowrap;
  }

  .tool-inline-params {
    min-width: 0;
    overflow: hidden;
    color: #64748b;
    font-size: 12px;
    line-height: 17px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .tool-inline-skill {
    min-width: 0;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    line-height: 17px;
  }

  .tool-inline-skill-icon {
    width: 17px;
    height: 17px;
    flex: 0 0 auto;
    display: grid;
    place-items: center;
    color: var(--tool-skill-accent);
  }

  .tool-inline-skill-icon svg {
    width: 17px;
    height: 17px;
    display: block;
  }

  .tool-inline-skill-icon rect {
    fill: var(--tool-skill-accent);
  }

  .tool-inline-skill-icon path {
    fill: #ffffff;
  }

  .tool-inline-skill-name {
    min-width: 0;
    overflow: hidden;
    color: var(--tool-skill-accent);
    font-size: 13px;
    font-weight: 680;
    line-height: 17px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .tool-inline-trailing {
    flex: none;
    align-self: center;
    min-width: 0;
    max-width: 180px;
  }

  .tool-inline-meta,
  .tool-inline-diff {
    overflow: hidden;
    color: #64748b;
    font-size: 11px;
    line-height: 17px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .tool-inline-meta {
    display: inline-block;
  }

  .tool-inline-diff {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    font-family:
      ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono",
      monospace;
    font-weight: 700;
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
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding-top: 1px;
  }

  .tool-inline-section {
    margin: 0;
  }

  .tool-inline-code-panel {
    max-height: 360px;
    overflow: auto;
    margin: 0;
    padding: 12px 14px;
    border: 1px solid #d8dee8;
    border-radius: 10px;
    background: #f8fafc;
  }

  .tool-inline-code-output,
  .tool-inline-pre {
    margin: 0;
    color: #334155;
    font-family:
      ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono",
      monospace;
    font-size: 12px;
    line-height: 1.65;
    white-space: pre-wrap;
    word-break: break-word;
  }

  .tool-inline-command-output {
    padding-bottom: 6px;
    margin-bottom: 8px;
    border-bottom: 1px solid #d8dee8;
    color: #64748b;
  }

  .tool-inline-empty {
    padding: 8px 0;
    color: #64748b;
    font-size: 12px;
    line-height: 1.45;
  }
</style>
