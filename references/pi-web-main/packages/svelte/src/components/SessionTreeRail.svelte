<script lang="ts">
  import type { TreeEntry } from "../composables/bridgeStore.svelte";
  import {
    filterTreeEntries,
    getTreeEntryDisplayParts,
    type TreeFilterMode,
  } from "../utils/treeOutline";

  let {
    entries = [] as readonly TreeEntry[],
    sessionPath = null as string | null,
    onSelect = (_: string) => {},
  }: {
    entries?: readonly TreeEntry[];
    sessionPath?: string | null;
    onSelect?: (entryId: string) => void;
  } = $props();

  let query = $state("");
  let filterMode = $state<TreeFilterMode>("default");

  const filterOptions: Array<{ mode: TreeFilterMode; label: string }> = [
    { mode: "default", label: "Default" },
    { mode: "no-tools", label: "No tools" },
    { mode: "user-only", label: "User" },
    { mode: "labeled-only", label: "Labels" },
    { mode: "all", label: "All" },
  ];

  $effect(() => {
    void sessionPath;
    query = "";
    filterMode = "default";
  });

  let filteredEntries = $derived(
    filterTreeEntries(entries, filterMode, query),
  );

  function displayParts(entry: TreeEntry) {
    return getTreeEntryDisplayParts(entry);
  }
</script>

<div class="tree-rail">
  <div class="tree-toolbar">
    <input
      bind:value={query}
      class="search-input"
      type="search"
      placeholder="Search..."
    />
    <div class="filter-row">
      {#each filterOptions as option (option.mode)}
        <button
          class="filter-chip"
          class:active={filterMode === option.mode}
          type="button"
          onclick={() => (filterMode = option.mode)}
        >
          {option.label}
        </button>
      {/each}
    </div>
  </div>

  {#if filteredEntries.length > 0}
    <ol class="tree-list">
      {#each filteredEntries as entry (entry.id)}
        <li class="tree-row">
          <button
            class="tree-item"
            class:role-user={displayParts(entry).role === "user"}
            class:role-assistant={displayParts(entry).role === "assistant"}
            class:role-tool={displayParts(entry).role === "tool"}
            class:role-meta={displayParts(entry).role === "meta"}
            class:active={entry.isActive}
            class:in-path={entry.isOnActivePath}
            class:dimmed={!entry.isOnActivePath}
            type="button"
            title={displayParts(entry).title}
            onclick={() => onSelect(entry.id)}
          >
            <span class="tree-guides" aria-hidden="true">
              {#each (entry.trackColumns ?? []) as column, ci (`${entry.id}-${ci}`)}
                <span class="track-column" class:line={column === "line"} class:branch={column === "branch"} class:branch-last={column === "branch-last"} class:blank={column === "blank"}></span>
              {/each}
            </span>
            <span class="tree-content">
              <span class="tree-marker" aria-hidden="true"></span>
              <span class="tree-line">
                <span class="tree-role">{displayParts(entry).roleLabel}</span>
                {#if displayParts(entry).labelTag}
                  <span class="tree-tag">
                    [{displayParts(entry).labelTag}]
                  </span>
                {/if}
                <span class="tree-preview">{displayParts(entry).previewText}</span>
              </span>
            </span>
          </button>
        </li>
      {/each}
    </ol>
  {:else}
    <div class="empty-state">
      <p class="empty-title">No matching tree entries</p>
      <p class="empty-copy">Try another filter or search term.</p>
    </div>
  {/if}
</div>

<style>
  .tree-rail {
    display: flex;
    flex-direction: column;
    min-height: 0;
    height: 100%;
    padding: 10px 8px 8px;
    background:
      linear-gradient(
        180deg,
        color-mix(in srgb, var(--rail-bg) 96%, white 4%),
        color-mix(in srgb, var(--rail-bg) 90%, var(--panel) 10%)
      ),
      var(--rail-bg);
  }

  .tree-toolbar {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 0 3px 6px;
  }

  .search-input {
    height: 26px;
    width: 100%;
    border-radius: 7px;
    border: 1px solid var(--border);
    background: color-mix(in srgb, var(--panel) 88%, transparent);
    color: var(--text);
    padding: 0 8px;
    font-size: 0.73rem;
    outline: none;
  }

  .search-input:focus {
    border-color: var(--border);
    box-shadow: none;
  }

  .filter-row {
    display: flex;
    flex-wrap: nowrap;
    gap: 3px;
    overflow-x: auto;
    overflow-y: hidden;
    padding-bottom: 1px;
    scrollbar-width: none;
  }

  .filter-row::-webkit-scrollbar {
    display: none;
  }

  .filter-chip {
    height: 22px;
    flex: 0 0 auto;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 0 6px;
    border-radius: 6px;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--text-subtle);
    white-space: nowrap;
    cursor: pointer;
    transition:
      background 0.12s ease,
      border-color 0.12s ease,
      color 0.12s ease;
  }

  .filter-chip:hover {
    background: var(--surface-hover);
    border-color: var(--border-strong);
    color: var(--text-muted);
  }

  .filter-chip.active {
    background: var(--surface-selected);
    border-color: color-mix(in srgb, var(--accent) 24%, var(--border-strong));
    color: var(--text);
  }

  .tree-list {
    list-style: none;
    margin: 0;
    padding: 0 1px 0 3px;
    overflow-y: auto;
    flex: 1;
    scrollbar-width: none;
  }

  .tree-list::-webkit-scrollbar {
    display: none;
  }

  .tree-item {
    width: 100%;
    min-height: 20px;
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    gap: 5px;
    align-items: center;
    padding: 0px 4px;
    border: 1px solid transparent;
    border-radius: 5px;
    appearance: none;
    background: transparent;
    color: inherit;
    text-align: left;
    cursor: pointer;
    transition: opacity 0.12s ease;
  }

  .tree-content {
    min-width: 0;
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    gap: 5px;
    align-items: center;
    padding: 0px 4px;
    border: 1px solid transparent;
    border-radius: 5px;
    transition:
      background 0.12s ease,
      border-color 0.12s ease,
      box-shadow 0.12s ease;
  }

  .tree-item:hover .tree-content {
    background: var(--surface-hover);
  }

  .tree-item.dimmed {
    opacity: 0.46;
  }

  .tree-item.in-path {
    opacity: 1;
  }

  .tree-item.active .tree-content {
    background: var(--surface-active);
    border-color: color-mix(in srgb, var(--accent) 24%, var(--border-strong));
  }

  .tree-guides {
    display: inline-flex;
    align-items: stretch;
    height: 16px;
  }

  .track-column {
    position: relative;
    width: 9px;
    flex-shrink: 0;
  }

  .track-column.line::before,
  .track-column.branch::before,
  .track-column.branch-last::before {
    content: "";
    position: absolute;
    left: 50%;
    width: 1px;
    background: color-mix(in srgb, var(--border) 82%, transparent);
    transform: translateX(-50%);
  }

  .track-column.line::before,
  .track-column.branch::before {
    top: -7px;
    bottom: -7px;
  }

  .track-column.branch-last::before {
    top: -7px;
    height: calc(50% + 1px);
  }

  .track-column.branch::after,
  .track-column.branch-last::after {
    content: "";
    position: absolute;
    left: 50%;
    top: 50%;
    width: 7px;
    height: 1px;
    background: color-mix(in srgb, var(--border) 82%, transparent);
  }

  .tree-marker {
    width: 5px;
    height: 5px;
    border-radius: 999px;
    background: color-mix(in srgb, var(--border-strong) 86%, transparent);
    flex-shrink: 0;
    transition:
      background 0.12s ease,
      box-shadow 0.12s ease,
      transform 0.12s ease;
  }

  .tree-item.in-path .tree-marker {
    background: color-mix(in srgb, var(--text-muted) 84%, var(--text));
  }

  .tree-item.role-user .tree-marker {
    background: var(--text);
  }

  .tree-item.active .tree-marker {
    background: color-mix(in srgb, var(--diff-added-accent) 54%, var(--text));
  }

  .tree-line {
    min-width: 0;
    display: flex;
    align-items: baseline;
    gap: 4px;
    overflow: hidden;
    font-family: var(--pi-font-mono);
    font-size: 0.68rem;
    line-height: 1.05;
  }

  .tree-role {
    flex-shrink: 0;
    color: var(--text-subtle);
  }

  .tree-role::after {
    content: ":";
  }

  .tree-preview {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--text-muted);
  }

  .tree-tag {
    flex-shrink: 0;
    max-width: 6.5rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: color-mix(in srgb, var(--text-subtle) 78%, var(--text));
    font-size: 0.61rem;
    line-height: 1.05;
  }

  .tree-item.active .tree-preview,
  .tree-item.active .tree-role,
  .tree-item.role-user .tree-preview,
  .tree-item.role-user .tree-role {
    color: var(--text);
  }

  .tree-item.role-assistant .tree-role {
    color: color-mix(in srgb, var(--text-subtle) 68%, var(--diff-added-accent));
  }

  .tree-item.role-tool .tree-role {
    color: color-mix(in srgb, var(--text-subtle) 88%, var(--border-strong));
  }

  .tree-item.role-meta .tree-role {
    color: color-mix(in srgb, var(--text-subtle) 76%, var(--text-muted));
  }

  .empty-state {
    margin: 6px 4px 0;
    padding: 12px 10px;
    border-radius: 9px;
    border: 1px dashed var(--border-strong);
    background: color-mix(in srgb, var(--panel) 58%, transparent);
  }

  .empty-title {
    margin: 0 0 3px;
    font-size: 0.78rem;
    color: var(--text);
  }

  .empty-copy {
    margin: 0;
    font-size: 0.71rem;
    line-height: 1.4;
    color: var(--text-subtle);
  }

  @media (max-width: 640px) {
    .tree-rail {
      padding: 12px 10px max(10px, env(safe-area-inset-bottom));
    }

    .search-input {
      height: 36px;
      border-radius: 10px;
      padding: 0 12px;
      font-size: 0.9rem;
    }

    .filter-chip {
      height: 30px;
      padding: 0 10px;
      border-radius: 999px;
      font-size: 0.65rem;
    }

    .tree-item {
      min-height: 32px;
      gap: 6px;
      align-items: flex-start;
      padding: 3px 2px;
    }

    .tree-content {
      gap: 8px;
      align-items: flex-start;
      padding: 6px 8px;
      border-radius: 8px;
    }

    .tree-guides {
      min-height: 24px;
      height: 100%;
      padding-top: 7px;
    }

    .tree-line {
      flex-wrap: wrap;
      row-gap: 2px;
      font-size: 0.78rem;
      line-height: 1.25;
    }

    .tree-tag {
      max-width: 100%;
    }

    .tree-preview {
      flex-basis: 100%;
      white-space: normal;
      overflow: visible;
      text-overflow: clip;
      overflow-wrap: anywhere;
    }
  }
</style>
