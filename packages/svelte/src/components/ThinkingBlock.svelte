<script lang="ts">
  import { tick } from "svelte";
  import { slide } from "svelte/transition";
  import MarkdownMessage from "./MarkdownMessage.svelte";
  import type { ChatContentBlock } from "../composables/bridgeStore.svelte";

  type MessageStatus = "pending" | "streaming" | "completed" | "failed";

  let {
    block,
    status,
    onrendered,
  }: {
    block: Extract<ChatContentBlock, { kind: "thinking" }>;
    status: MessageStatus;
    onrendered?: () => void;
  } = $props();

  let expanded = $state(false);
  const contentId = `thinking-${crypto.randomUUID()}`;
  const label = $derived(status === "streaming" ? "思考中" : "已思考");

  $effect(() => {
    block;
    expanded;
    void tick().then(() => onrendered?.());
  });
</script>

<div class="thinking-block" class:expanded>
  <button
    type="button"
    class="thinking-toggle"
    aria-expanded={expanded}
    aria-controls={contentId}
    title={expanded ? "收起思考过程" : "展开思考过程"}
    onclick={() => (expanded = !expanded)}
  >
    <span class="thinking-label">{label}</span>
    <span class="thinking-chevron" aria-hidden="true"></span>
  </button>

  {#if expanded}
    <div id={contentId} class="thinking-content" transition:slide={{ duration: 140 }}>
      <MarkdownMessage {status} content={block.text} {onrendered} />
    </div>
  {/if}
</div>

<style>
  .thinking-block {
    max-width: 100%;
    color: #7a7f87;
    overflow-anchor: none;
  }

  .thinking-toggle {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    border: 0;
    padding: 0;
    background: transparent;
    color: inherit;
    cursor: pointer;
    font-size: 16px;
    font-weight: 450;
    line-height: 1.5;
    text-align: left;
  }

  .thinking-toggle:hover,
  .thinking-toggle:focus-visible,
  .thinking-block.expanded .thinking-toggle {
    color: #5f6673;
  }

  .thinking-toggle:focus-visible {
    outline: 2px solid rgba(47, 102, 255, 0.26);
    outline-offset: 3px;
    border-radius: 4px;
  }

  .thinking-label {
    white-space: nowrap;
  }

  .thinking-chevron {
    width: 7px;
    height: 7px;
    display: inline-block;
    border-right: 2px solid currentColor;
    border-bottom: 2px solid currentColor;
    opacity: 1;
    transform: rotate(-45deg);
    transition:
      color 120ms ease,
      transform 140ms ease;
  }

  .thinking-toggle:hover .thinking-chevron,
  .thinking-toggle:focus-visible .thinking-chevron,
  .thinking-block.expanded .thinking-chevron {
    opacity: 1;
  }

  .thinking-block.expanded .thinking-chevron {
    transform: rotate(45deg) translate(-1px, -1px);
  }

  .thinking-content {
    margin: 14px 0 0 7px;
    padding-left: 18px;
    border-left: 2px solid #d8dee8;
    color: #6b7280;
    font-size: 15px;
    line-height: 1.75;
  }

  .thinking-content :global(.markdown-body) {
    color: inherit;
    font-size: inherit;
    line-height: inherit;
  }

  @media (prefers-reduced-motion: reduce) {
    .thinking-chevron {
      transition: none;
    }
  }
</style>
