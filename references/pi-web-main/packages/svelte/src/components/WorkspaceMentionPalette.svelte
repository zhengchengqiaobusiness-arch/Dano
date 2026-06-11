<script lang="ts">
  import type { WorkspaceMentionSuggestion } from "../utils/workspaceMentions";

  let {
    items = [] as readonly WorkspaceMentionSuggestion[],
    loading = false,
    emptyText = "No matching files",
    onSelect = (_: WorkspaceMentionSuggestion) => {},
    onClose = () => {},
  }: {
    items: readonly WorkspaceMentionSuggestion[];
    loading: boolean;
    emptyText?: string;
    onSelect?: (item: WorkspaceMentionSuggestion) => void;
    onClose?: () => void;
  } = $props();

  let highlightedIndex = $state(0);
  let listRef = $state<HTMLElement | null>(null);

  let hasItems = $derived(items.length > 0);

  $effect(() => {
    void items;
    highlightedIndex = 0;
  });

  function scrollToHighlighted() {
    queueMicrotask(() => {
      const el = listRef?.children[highlightedIndex] as
        | HTMLElement
        | undefined;
      el?.scrollIntoView({ block: "nearest" });
    });
  }

  export function handleKeydown(event: KeyboardEvent) {
    if (loading) {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
      return;
    }

    if (!hasItems) {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
      return;
    }

    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        highlightedIndex = (highlightedIndex + 1) % items.length;
        scrollToHighlighted();
        break;
      case "ArrowUp":
        event.preventDefault();
        highlightedIndex =
          (highlightedIndex - 1 + items.length) % items.length;
        scrollToHighlighted();
        break;
      case "Enter":
      case "Tab":
        event.preventDefault();
        if (items[highlightedIndex]) onSelect(items[highlightedIndex]);
        break;
      case "Escape":
        event.preventDefault();
        onClose();
        break;
    }
  }
</script>

<div class="workspace-palette">
  {#if loading}
    <div class="workspace-palette-empty">
      <span class="workspace-empty-text">Indexing workspace...</span>
    </div>
  {:else if hasItems}
    <ul bind:this={listRef} class="workspace-list">
      {#each items as item, idx (`${item.kind}:${item.path}`)}
        <li
          class="workspace-item"
          class:highlighted={idx === highlightedIndex}
        >
          <button
            class="workspace-item-btn"
            type="button"
            onclick={() => onSelect(item)}
            onmouseenter={() => (highlightedIndex = idx)}
          >
            <div class="workspace-copy">
              <span class="workspace-name">{item.label}</span>
              <span class="workspace-path">{item.description}</span>
            </div>
          </button>
        </li>
      {/each}
    </ul>
  {:else}
    <div class="workspace-palette-empty">
      <span class="workspace-empty-text">{emptyText}</span>
    </div>
  {/if}
</div>

<style>
  .workspace-palette {
    position: absolute;
    left: 0;
    right: 0;
    bottom: calc(100% + 8px);
    max-height: 320px;
    overflow-y: auto;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 14px;
    box-shadow: var(--shadow);
    z-index: 10;
    scrollbar-width: none;
  }

  .workspace-palette::-webkit-scrollbar {
    display: none;
  }

  .workspace-list {
    list-style: none;
    margin: 0;
    padding: 6px;
  }

  .workspace-item {
    display: flex;
    align-items: center;
    min-height: 42px;
    padding: 0;
    border-radius: 10px;
    transition: background 0.1s ease;
  }

  .workspace-item-btn {
    display: flex;
    align-items: center;
    width: 100%;
    min-height: 42px;
    padding: 8px 12px;
    border: none;
    border-radius: 10px;
    background: transparent;
    color: inherit;
    cursor: pointer;
    font: inherit;
    text-align: left;
  }

  .workspace-item:hover,
  .workspace-item.highlighted {
    background: var(--panel-2);
  }

  .workspace-copy {
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 0;
  }

  .workspace-name,
  .workspace-path {
    font-family: var(--pi-font-mono);
  }

  .workspace-empty-text {
    font-family: var(--pi-font-sans);
  }

  .workspace-name {
    font-size: 0.74rem;
    color: var(--text);
    white-space: nowrap;
  }

  .workspace-path {
    font-size: 0.68rem;
    color: var(--text-subtle);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .workspace-palette-empty {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 12px;
  }

  .workspace-empty-text {
    font-size: 0.72rem;
    color: var(--text-subtle);
  }
</style>
