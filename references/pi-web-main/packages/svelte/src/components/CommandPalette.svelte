<script lang="ts">
  import type { SlashCommandOption } from "../utils/slashCommands";

  let {
    commands = [] as SlashCommandOption[],
    filter = "",
    isDebugMode = false,
    onSelect = (_: string) => {},
    onClose = () => {},
  }: {
    commands: SlashCommandOption[];
    filter: string;
    isDebugMode?: boolean;
    onSelect?: (commandName: string) => void;
    onClose?: () => void;
  } = $props();

  let highlightedIndex = $state(0);
  let listRef = $state<HTMLElement | null>(null);

  let filtered = $derived.by(() => {
    const q = filter.toLowerCase();
    if (!q) return commands;
    return commands.filter(
      c =>
        c.name.toLowerCase().includes(q) ||
        (c.description ?? "").toLowerCase().includes(q),
    );
  });

  $effect(() => {
    void filter;
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

  export function handleKeydown(e: KeyboardEvent) {
    if (filtered.length === 0) {
      if (e.key === "Escape") onClose();
      return;
    }

    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        highlightedIndex = (highlightedIndex + 1) % filtered.length;
        scrollToHighlighted();
        break;
      case "ArrowUp":
        e.preventDefault();
        highlightedIndex =
          (highlightedIndex - 1 + filtered.length) % filtered.length;
        scrollToHighlighted();
        break;
      case "Enter":
        e.preventDefault();
        if (filtered[highlightedIndex]) {
          onSelect(filtered[highlightedIndex].name);
        }
        break;
      case "Escape":
        e.preventDefault();
        onClose();
        break;
    }
  }
</script>

{#if filtered.length > 0}
  <div class="command-palette">
    {#if isDebugMode}
      <div class="command-hint">Debug session commands run locally in memory.</div>
    {/if}
    <ul bind:this={listRef} class="command-list">
      {#each filtered as cmd, idx (cmd.name)}
        <li
          class="command-item"
          class:highlighted={idx === highlightedIndex}
        >
          <button
            class="command-item-btn"
            type="button"
            onclick={() => onSelect(cmd.name)}
            onmouseenter={() => (highlightedIndex = idx)}
          >
            <div class="command-copy">
              <span class="cmd-name">/{cmd.name}</span>
              {#if cmd.description}
                <span class="cmd-desc">{cmd.description}</span>
              {/if}
            </div>
          </button>
        </li>
      {/each}
    </ul>
  </div>
{:else}
  <div class="command-palette empty">
    <span class="empty-text">
      {isDebugMode ? "No matching debug commands" : "No matching commands"}
    </span>
    {#if isDebugMode}
      <span class="empty-hint">Try `/fixture mixed`, `/fixture edit`, or `/tps 12`.</span>
    {/if}
  </div>
{/if}

<style>
  .command-palette {
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

  .command-palette::-webkit-scrollbar {
    display: none;
  }

  .command-palette.empty {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 12px;
  }

  .command-hint {
    padding: 10px 12px 0;
    font-size: 0.72rem;
    color: var(--text-subtle);
  }

  .command-list {
    list-style: none;
    margin: 0;
    padding: 6px;
  }

  .command-item {
    display: flex;
    align-items: center;
    min-height: 38px;
    padding: 0;
    border-radius: 10px;
    transition: background 0.1s ease;
  }

  .command-item-btn {
    display: flex;
    align-items: center;
    width: 100%;
    min-height: 38px;
    padding: 8px 12px;
    border: none;
    border-radius: 10px;
    background: transparent;
    color: inherit;
    cursor: pointer;
    font: inherit;
    text-align: left;
  }

  .command-item:hover,
  .command-item.highlighted {
    background: var(--panel-2);
  }

  .command-copy {
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 0;
  }

  .cmd-name {
    font-size: 0.78rem;
    color: var(--text);
    white-space: nowrap;
  }

  .cmd-desc {
    font-size: 0.72rem;
    color: var(--text-subtle);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .empty-text {
    font-size: 0.76rem;
    color: var(--text-subtle);
  }

  .empty-hint {
    margin-top: 4px;
    font-size: 0.72rem;
    color: var(--text-subtle);
  }
</style>
