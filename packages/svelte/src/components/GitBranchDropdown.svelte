<script lang="ts">
  import type { RpcGitBranch, RpcGitRepoState } from "@pi-web/bridge/types";
  import Check from "lucide-svelte/icons/check";
  import GitBranchIcon from "lucide-svelte/icons/git-branch";
  import Plus from "lucide-svelte/icons/plus";
  import RefreshCw from "lucide-svelte/icons/refresh-cw";

  let {
    label = null as string | null,
    repoState = null as RpcGitRepoState | null,
    loading = false,
    switching = false,
    disabled = false,
    refresh = (_?: boolean) =>
      Promise.resolve(null as RpcGitRepoState | null),
    switchBranch = (_: string) =>
      Promise.resolve(null as RpcGitRepoState | null),
    createBranch = (_: string) =>
      Promise.resolve(null as RpcGitRepoState | null),
  } = $props();

  let rootRef = $state<HTMLElement | null>(null);
  let triggerRef = $state<HTMLButtonElement | null>(null);
  let searchInputRef = $state<HTMLInputElement | null>(null);
  let listRef = $state<HTMLElement | null>(null);
  let isOpen = $state(false);
  let searchText = $state("");
  let highlightedIndex = $state(0);

  let displayLabel = $derived.by(() => {
    const fb = repoState?.headLabel ?? label;
    const branch = fb?.trim();
    return branch ? branch : null;
  });
  let isBusy = $derived(loading || switching);
  let normalizedQuery = $derived(searchText.trim());
  let mergedBranches = $derived.by(() => {
    if (!repoState) return [] as RpcGitBranch[];
    const byShortName = new Map<
      string,
      { local?: RpcGitBranch; remotes: RpcGitBranch[] }
    >();
    for (const branch of repoState.branches) {
      const group = byShortName.get(branch.shortName) ?? { remotes: [] };
      if (branch.kind === "local") {
        group.local = branch;
      } else {
        group.remotes.push(branch);
      }
      byShortName.set(branch.shortName, group);
    }
    const result: RpcGitBranch[] = [];
    const seen = new Set<string>();
    for (const branch of repoState.branches) {
      const group = byShortName.get(branch.shortName);
      if (!group || seen.has(branch.shortName)) continue;
      seen.add(branch.shortName);
      if (group.local) {
        result.push(group.local);
      } else if (group.remotes.length > 0) {
        result.push(group.remotes[0]);
      }
    }
    return result;
  });
  let filteredBranches = $derived.by(() => {
    if (!repoState) return [] as RpcGitBranch[];
    const query = normalizedQuery.toLowerCase();
    if (!query) return mergedBranches;
    return mergedBranches.filter(branch => {
      const display =
        branch.kind === "remote" && branch.remoteName
          ? `${branch.remoteName}/${branch.shortName}`
          : branch.shortName;
      return [branch.name, display].join(" ").toLowerCase().includes(query);
    });
  });
  let exactBranchMatch = $derived(
    normalizedQuery
      ? mergedBranches.find(b => b.name === normalizedQuery) ?? null
      : null,
  );
  let canCreateBranch = $derived(Boolean(normalizedQuery) && !exactBranchMatch);
  let createButtonLabel = $derived(
    normalizedQuery ? `Create ${normalizedQuery}` : "Create branch",
  );
  let triggerTitle = $derived.by(() => {
    if (!displayLabel) return "Git branch";
    if (repoState?.isDirty)
      return `${displayLabel} (working tree has uncommitted changes)`;
    return displayLabel;
  });

  function branchDisplayName(branch: RpcGitBranch): string {
    if (branch.kind === "remote" && branch.remoteName) {
      return `${branch.remoteName}/${branch.shortName}`;
    }
    return branch.shortName;
  }

  function syncHighlightedIndex() {
    if (filteredBranches.length === 0) {
      highlightedIndex = 0;
      return;
    }
    const emi = filteredBranches.findIndex(
      b => b.name === normalizedQuery,
    );
    if (emi >= 0) { highlightedIndex = emi; return; }
    const ci = filteredBranches.findIndex(b => b.isCurrent);
    highlightedIndex = ci >= 0 ? ci : 0;
  }

  function scrollToHighlighted() {
    queueMicrotask(() => {
      const el = listRef?.children[highlightedIndex] as
        | HTMLElement
        | undefined;
      el?.scrollIntoView({ block: "nearest" });
    });
  }

  async function ensureRepoState(force = false) {
    await refresh(force);
    syncHighlightedIndex();
    scrollToHighlighted();
  }

  async function openDropdown() {
    if (disabled || !displayLabel) return;
    isOpen = true;
    searchText = "";
    syncHighlightedIndex();
    if (!repoState && !loading) {
      void ensureRepoState(true);
    } else {
      scrollToHighlighted();
    }
  }

  function closeDropdown(options?: { focusTrigger?: boolean }) {
    isOpen = false;
    searchText = "";
    if (options?.focusTrigger) {
      queueMicrotask(() => triggerRef?.focus());
    }
  }

  function toggleDropdown() {
    if (isOpen) { closeDropdown(); return; }
    void openDropdown();
  }

  function updateHighlight(nextIndex: number) {
    const maxIndex = filteredBranches.length - 1;
    highlightedIndex = Math.min(Math.max(nextIndex, 0), maxIndex);
    scrollToHighlighted();
  }

  async function handleRefresh(force = true) {
    if (isBusy) return;
    await ensureRepoState(force);
  }

  async function selectBranch(branch: RpcGitBranch) {
    if (switching) return;
    if (branch.isCurrent) {
      closeDropdown({ focusTrigger: true });
      return;
    }
    const nextState = await switchBranch(branch.name);
    if (nextState) closeDropdown({ focusTrigger: true });
  }

  async function handleCreateBranch() {
    if (!canCreateBranch || switching) return;
    const nextState = await createBranch(normalizedQuery);
    if (nextState) closeDropdown({ focusTrigger: true });
  }

  function handleTriggerKeydown(event: KeyboardEvent) {
    if (disabled || !displayLabel) return;
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        if (!isOpen) { void openDropdown(); return; }
        updateHighlight(highlightedIndex + 1);
        break;
      case "ArrowUp":
        event.preventDefault();
        if (!isOpen) { void openDropdown(); return; }
        updateHighlight(highlightedIndex - 1);
        break;
      case "Enter":
      case " ":
        event.preventDefault();
        toggleDropdown();
        break;
    }
  }

  function handleSearchKeydown(event: KeyboardEvent) {
    if (!isOpen) return;
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        if (filteredBranches.length > 0)
          updateHighlight(
            (highlightedIndex + 1) % filteredBranches.length,
          );
        break;
      case "ArrowUp":
        event.preventDefault();
        if (filteredBranches.length > 0)
          updateHighlight(
            (highlightedIndex - 1 + filteredBranches.length) %
              filteredBranches.length,
          );
        break;
      case "Enter": {
        event.preventDefault();
        if (canCreateBranch) { void handleCreateBranch(); return; }
        const branch = filteredBranches[highlightedIndex];
        if (branch) void selectBranch(branch);
        break;
      }
      case "Escape":
        event.preventDefault();
        closeDropdown({ focusTrigger: true });
        break;
      case "Tab":
        closeDropdown();
        break;
    }
  }

  function handleDocumentMousedown(event: MouseEvent) {
    const target = event.target;
    if (!(target instanceof Node)) return;
    if (!rootRef?.contains(target)) closeDropdown();
  }

  function tick(): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, 0));
  }

  $effect(() => {
    if (typeof document === "undefined") return;
    if (isOpen) {
      document.addEventListener("mousedown", handleDocumentMousedown);
      return () =>
        document.removeEventListener("mousedown", handleDocumentMousedown);
    }
  });

  $effect(() => {
    void filteredBranches;
    if (highlightedIndex >= filteredBranches.length)
      highlightedIndex = Math.max(0, filteredBranches.length - 1);
    scrollToHighlighted();
  });

  $effect(() => {
    void repoState;
    if (!isOpen) return;
    syncHighlightedIndex();
    scrollToHighlighted();
  });
</script>

{#if displayLabel}
  <div bind:this={rootRef} class="git-dropdown">
    <button
      bind:this={triggerRef}
      class="git-trigger"
      type="button"
      disabled={disabled || isBusy}
      title={triggerTitle}
      aria-expanded={isOpen}
      aria-haspopup="dialog"
      onclick={toggleDropdown}
      onkeydown={handleTriggerKeydown}
    >
      <GitBranchIcon aria-hidden="true" size={12} />
      <span class="git-trigger-text">{displayLabel}</span>
    </button>

    {#if isOpen}
      <div class="git-menu">
        <div class="git-search-row">
          <label class="git-search">
            <input
              bind:this={searchInputRef}
              bind:value={searchText}
              class="git-search-input"
              type="text"
              placeholder="Find or create branch"
              onkeydown={handleSearchKeydown}
            />
          </label>
          <button
            class="git-refresh"
            type="button"
            disabled={isBusy}
            title="Refresh branches"
            onclick={() => handleRefresh(true)}
          >
            <span
              class="git-refresh-icon"
              class:spin={loading}
            >
              <RefreshCw
                aria-hidden="true"
                size={14}
              />
            </span>
          </button>
        </div>

        {#if repoState && canCreateBranch}
          <button
            class="git-create"
            type="button"
            disabled={switching}
            onclick={handleCreateBranch}
          >
            <Plus class="git-create-icon" aria-hidden="true" size={14} />
            <span class="git-create-label">{createButtonLabel}</span>
          </button>
        {:else if repoState && exactBranchMatch}
          <div class="git-match-note">
            Branch already exists. Press Enter to switch.
          </div>
        {/if}

        {#if loading && !repoState}
          <div class="git-empty">Loading branches...</div>
        {:else if !repoState}
          <div class="git-empty">No git repository found.</div>
        {:else if filteredBranches.length === 0}
          <div class="git-empty">No matching branches</div>
        {:else}
          <ul
            bind:this={listRef}
            class="git-list"
            role="listbox"
            tabindex="-1"
            onkeydown={handleSearchKeydown}
          >
            {#each filteredBranches as branch, index (`${branch.kind}:${branch.name}`)}
              <li class="git-list-item">
                <button
                  class="git-option"
                  type="button"
                  class:highlighted={index === highlightedIndex}
                  class:selected={branch.isCurrent}
                  disabled={switching}
                  onclick={() => selectBranch(branch)}
                  onmouseenter={() => (highlightedIndex = index)}
                >
                  <span class="git-option-name">{branchDisplayName(branch)}</span>
                  {#if branch.isCurrent}
                    <Check
                      class="git-option-check"
                      aria-hidden="true"
                      size={14}
                    />
                  {/if}
                </button>
              </li>
            {/each}
          </ul>
        {/if}
      </div>
    {/if}
  </div>
{/if}

<style>
  .git-dropdown {
    position: relative;
    min-width: 0;
  }

  .git-trigger {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    max-width: 100%;
    height: 26px;
    padding: 0 10px;
    border-radius: 999px;
    border: none;
    background: transparent;
    color: var(--text-muted);
    cursor: pointer;
    transition:
      background 0.15s ease,
      border-color 0.15s ease,
      color 0.15s ease;
  }

  .git-trigger:hover:not(:disabled) {
    background: var(--surface-hover);
    color: var(--text);
  }

  .git-trigger[aria-expanded="true"] {
    background: var(--surface-active);
    color: var(--text);
  }

  .git-trigger:focus-visible {
    outline: none;
    color: var(--text);
  }

  .git-trigger:disabled {
    cursor: not-allowed;
    opacity: 0.72;
  }

  .git-trigger-text {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-family: var(--pi-font-mono);
    font-size: 0.64rem;
  }

  .git-menu {
    position: absolute;
    left: 0;
    bottom: calc(100% + 10px);
    width: min(332px, calc(100vw - 48px));
    padding: 8px;
    border: 1px solid var(--border-strong);
    border-radius: 14px;
    background: linear-gradient(
      180deg,
      color-mix(in srgb, var(--panel) 97%, transparent),
      var(--bg-elevated)
    );
    box-shadow: var(--shadow-floating);
    backdrop-filter: blur(18px);
    z-index: 18;
  }

  .git-search-row {
    display: flex;
    align-items: stretch;
    gap: 7px;
    margin-bottom: 6px;
  }

  .git-refresh {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    align-self: stretch;
    width: 34px;
    padding: 0;
    border-radius: 10px;
    border: none;
    background: color-mix(in srgb, var(--bg-elevated) 88%, transparent);
    color: var(--text-subtle);
    cursor: pointer;
    flex-shrink: 0;
  }

  .git-refresh:hover:not(:disabled) {
    background: var(--surface-hover);
    color: var(--text);
  }

  .git-refresh:focus-visible {
    background: var(--surface-hover);
    color: var(--text);
    outline: none;
    box-shadow: 0 0 0 3px var(--focus-ring);
  }

  .git-refresh:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .git-search {
    display: flex;
    align-items: center;
    flex: 1;
    min-width: 0;
    height: 34px;
    padding: 0 9px;
    border-radius: 10px;
    border: 1px solid var(--border);
    background: color-mix(in srgb, var(--bg-elevated) 88%, transparent);
  }

  .git-search:focus-within {
    background: var(--panel);
  }

  .git-search-input {
    width: 100%;
    border: none;
    background: transparent;
    color: var(--text);
    font-family: var(--pi-font-mono);
    font-size: 0.78rem;
    outline: none;
  }

  .git-search-input::placeholder {
    color: var(--text-subtle);
  }

  .git-refresh-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    line-height: 0;
  }

  .git-create,
  .git-match-note,
  .git-empty {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    margin-bottom: 8px;
    padding: 9px 10px;
    border-radius: 10px;
    font-size: 0.68rem;
    line-height: 1.45;
  }

  .git-create {
    width: 100%;
    border: 1px solid color-mix(in srgb, var(--border-strong) 84%, transparent);
    background: color-mix(in srgb, var(--panel-2) 82%, var(--button-bg));
    color: var(--text);
    cursor: pointer;
    text-align: left;
  }

  .git-create:hover:not(:disabled) {
    background: var(--surface-hover);
    border-color: var(--border-strong);
  }

  .git-create:focus-visible {
    background: var(--surface-hover);
    border-color: var(--accent);
    outline: none;
    box-shadow: 0 0 0 3px var(--focus-ring);
  }

  .git-create:disabled {
    opacity: 0.6;
    cursor: wait;
  }

  .git-create-label {
    font-family: var(--pi-font-mono);
    font-size: 0.68rem;
  }

  .git-match-note {
    background: color-mix(in srgb, var(--panel-2) 86%, transparent);
    color: var(--text-muted);
  }

  .git-empty {
    justify-content: center;
    color: var(--text-subtle);
    background: color-mix(in srgb, var(--panel-2) 70%, transparent);
  }

  .git-list {
    margin: 0;
    padding: 0 6px 0 0;
    list-style: none;
    max-height: 280px;
    overflow-y: auto;
    scrollbar-gutter: stable;
    scrollbar-width: none;
  }

  .git-list::-webkit-scrollbar {
    display: none;
  }

  .git-list:focus {
    outline: none;
  }

  .git-list-item + .git-list-item {
    margin-top: 3px;
  }

  .git-option {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    width: 100%;
    padding: 6px 10px;
    border: 1px solid transparent;
    border-radius: 10px;
    background: transparent;
    color: var(--text);
    cursor: pointer;
    text-align: left;
    transition:
      background 0.12s ease,
      border-color 0.12s ease,
      transform 0.12s ease;
  }

  .git-option:hover:not(:disabled),
  .git-option.highlighted {
    background: var(--surface-hover);
    border-color: color-mix(in srgb, var(--border-strong) 84%, transparent);
    transform: translateX(1px);
  }

  .git-option.selected {
    background: var(--surface-selected);
    border-color: color-mix(in srgb, var(--accent) 24%, var(--border-strong));
  }

  .git-option:disabled {
    cursor: wait;
  }

  .git-option-name {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-family: var(--pi-font-mono);
    font-size: 0.8rem;
    color: var(--text);
  }

  @media (max-width: 640px) {
    .git-menu {
      width: min(296px, calc(100vw - 24px));
    }
  }

  .spin {
    animation: git-spin 0.85s linear infinite;
  }

  @keyframes git-spin {
    from {
      transform: rotate(0deg);
    }
    to {
      transform: rotate(360deg);
    }
  }
</style>
