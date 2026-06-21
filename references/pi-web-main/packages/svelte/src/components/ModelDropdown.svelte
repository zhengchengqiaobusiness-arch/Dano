<script lang="ts">
  import Check from "lucide-svelte/icons/check";
  import Search from "lucide-svelte/icons/search";
  import { onMount } from "svelte";
  import { filterModels, getModelKey, type RpcModelInfo } from "../utils/models";
  import ProviderIcon from "./ProviderIcon.svelte";

  let {
    models = [] as readonly RpcModelInfo[],
    selectedModel = null as RpcModelInfo | null,
    label = "",
    disabled = false,
    onSelect = (_: RpcModelInfo) => {},
  }: {
    models: readonly RpcModelInfo[];
    selectedModel: RpcModelInfo | null;
    label?: string;
    disabled?: boolean;
    onSelect?: (model: RpcModelInfo) => void;
  } = $props();

  let rootRef = $state<HTMLElement | null>(null);
  let searchInputRef = $state<HTMLInputElement | null>(null);
  let listRef = $state<HTMLElement | null>(null);
  let isOpen = $state(false);
  let searchText = $state("");
  let highlightedIndex = $state(0);

  let hasModels = $derived(models.length > 0);
  let selectedKey = $derived(
    selectedModel ? getModelKey(selectedModel) : "",
  );
  let filteredModels = $derived(filterModels(models, searchText));
  let triggerTitle = $derived.by(() => {
    if (!selectedModel)
      return hasModels ? "Select Pi model" : "No Pi models available";
    return `${selectedModel.name} (${selectedModel.provider}/${selectedModel.id})`;
  });

  function syncHighlightedIndex() {
    if (filteredModels.length === 0) {
      highlightedIndex = 0;
      return;
    }

    const si = filteredModels.findIndex(
      m => getModelKey(m) === selectedKey,
    );
    highlightedIndex = si >= 0 ? si : 0;
  }

  function scrollToHighlighted() {
    queueMicrotask(() => {
      const el = listRef?.children[highlightedIndex] as HTMLElement | undefined;
      el?.scrollIntoView({ block: "nearest" });
    });
  }

  async function openDropdown() {
    if (disabled || !hasModels) return;
    isOpen = true;
    searchText = "";
    syncHighlightedIndex();
    scrollToHighlighted();
  }

  function closeDropdown() {
    isOpen = false;
    searchText = "";
  }

  function toggleDropdown() {
    if (isOpen) {
      closeDropdown();
      return;
    }
    void openDropdown();
  }

  function selectModel(model: RpcModelInfo) {
    onSelect(model);
    closeDropdown();
  }

  function handleSearchKeydown(event: KeyboardEvent) {
    if (!isOpen) return;

    if (filteredModels.length === 0) {
      if (event.key === "Escape") {
        event.preventDefault();
        closeDropdown();
      }
      return;
    }

    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        highlightedIndex =
          (highlightedIndex + 1) % filteredModels.length;
        scrollToHighlighted();
        break;
      case "ArrowUp":
        event.preventDefault();
        highlightedIndex =
          (highlightedIndex - 1 + filteredModels.length) %
          filteredModels.length;
        scrollToHighlighted();
        break;
      case "Enter": {
        event.preventDefault();
        const m = filteredModels[highlightedIndex];
        if (m) selectModel(m);
        break;
      }
      case "Escape":
        event.preventDefault();
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
    void searchText;
    highlightedIndex = 0;
    scrollToHighlighted();
  });

  $effect(() => {
    void selectedModel;
    if (!isOpen) return;
    syncHighlightedIndex();
    scrollToHighlighted();
  });
</script>

<div bind:this={rootRef} class="model-dropdown">
  <button
    class="model-trigger"
    type="button"
    disabled={disabled || !hasModels}
    title={triggerTitle}
    aria-expanded={isOpen}
    aria-haspopup="dialog"
    onclick={toggleDropdown}
  >
    {#if selectedModel}
      <ProviderIcon provider={selectedModel.provider} size={14} />
      <span class="model-trigger-label">{label}</span>
    {:else if label}
      <span class="model-trigger-label">{label}</span>
    {:else}
      <span class="sr-only">Select model</span>
    {/if}
  </button>

  {#if isOpen}
    <div class="model-menu">
      <label class="model-search">
        <Search aria-hidden="true" size={13} style="flex-shrink: 0; color: var(--text-subtle)" />
        <input
          bind:this={searchInputRef}
          bind:value={searchText}
          class="model-search-input"
          type="text"
          placeholder="Search models"
          onkeydown={handleSearchKeydown}
        />
      </label>

      {#if filteredModels.length > 0}
        <ul bind:this={listRef} class="model-list">
          {#each filteredModels as model, index (getModelKey(model))}
            <li class="model-list-item">
              <button
                class="model-option"
                type="button"
                class:highlighted={index === highlightedIndex}
                class:selected={getModelKey(model) === selectedKey}
                onclick={() => selectModel(model)}
                onmouseenter={() => (highlightedIndex = index)}
              >
                <ProviderIcon provider={model.provider} size={16} />
                <div class="model-option-copy">
                  <span class="model-option-name">{model.name}</span>
                  <span class="model-option-meta"
                    >{model.provider}/{model.id}</span
                  >
                </div>
                {#if getModelKey(model) === selectedKey}
                  <Check aria-hidden="true" size={14} />
                {/if}
              </button>
            </li>
          {/each}
        </ul>
      {:else}
        <div class="model-empty">No matching models</div>
      {/if}
    </div>
  {/if}
</div>

<style>
  .model-dropdown {
    position: relative;
    flex-shrink: 0;
  }

  .model-trigger {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    min-width: 0;
    max-width: min(100%, 440px);
    height: 24px;
    padding: 0 9px;
    border-radius: 999px;
    border: none;
    background: var(--bg);
    color: var(--text-subtle);
    cursor: pointer;
    transition:
      background 0.15s ease,
      color 0.15s ease,
      transform 0.15s ease;
  }

  .model-trigger:hover:not(:disabled) {
    background: var(--bg);
    color: var(--text);
  }

  .model-trigger[aria-expanded="true"] {
    background: var(--bg);
    color: var(--text);
  }

  .model-trigger:focus-visible {
    outline: none;
  }

  .model-trigger:disabled {
    opacity: 0.45;
    cursor: not-allowed;
  }

  .model-trigger-label {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-family: var(--pi-font-mono);
    font-size: 0.64rem;
  }

  .model-menu {
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

  .model-search {
    display: flex;
    align-items: center;
    gap: 7px;
    height: 34px;
    padding: 0 9px;
    margin-bottom: 6px;
    border-radius: 10px;
    border: 1px solid var(--border);
    background: color-mix(in srgb, var(--bg-elevated) 88%, transparent);
  }

  .model-search:focus-within {
    background: var(--panel);
  }

  .model-search-input {
    width: 100%;
    border: none;
    background: transparent;
    color: var(--text);
    font-size: 0.78rem;
    outline: none;
  }

  .model-search-input::placeholder {
    color: var(--text-subtle);
  }

  .model-list {
    max-height: 240px;
    margin: 0;
    padding: 0 6px 0 0;
    list-style: none;
    overflow-y: auto;
    scrollbar-gutter: stable;
    scrollbar-width: none;
  }

  .model-list::-webkit-scrollbar {
    display: none;
  }

  .model-list-item + .model-list-item {
    margin-top: 3px;
  }

  .model-option {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    width: 100%;
    padding: 5px 10px;
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

  .model-option:hover,
  .model-option.highlighted {
    background: var(--surface-hover);
    border-color: color-mix(in srgb, var(--border-strong) 84%, transparent);
    transform: translateX(1px);
  }

  .model-option.selected {
    background: var(--surface-selected);
    border-color: color-mix(in srgb, var(--accent) 24%, var(--border-strong));
  }

  .model-option-copy {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
    flex: 1;
  }

  .model-option-name,
  .model-option-meta,
  .model-empty {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .model-option-name {
    font-size: 0.8rem;
    font-weight: 600;
    color: var(--text);
  }

  .model-option-meta {
    font-family: var(--pi-font-mono);
    font-size: 0.62rem;
    color: var(--text-subtle);
  }

  .model-empty {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 54px;
    border-radius: 10px;
    font-size: 0.74rem;
    color: var(--text-subtle);
    background: color-mix(in srgb, var(--panel-2) 60%, transparent);
  }

  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }

  @media (max-width: 640px) {
    .model-trigger {
      max-width: min(56vw, 210px);
    }

    .model-menu {
      width: min(296px, calc(100vw - 24px));
    }
  }
</style>
