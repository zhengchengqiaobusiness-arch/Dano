<script lang="ts">
  import type { RpcThinkingLevel } from "@pi-web/bridge/types";
  import Check from "lucide-svelte/icons/check";
  import ChevronDown from "lucide-svelte/icons/chevron-down";
  import {
    DEFAULT_THINKING_LEVEL,
    THINKING_LEVEL_OPTIONS,
  } from "../utils/thinkingLevels";

  let {
    value = null as RpcThinkingLevel | null,
    disabled = false,
    onSelect = (_: RpcThinkingLevel) => {},
  }: {
    value: RpcThinkingLevel | null;
    disabled?: boolean;
    onSelect?: (level: RpcThinkingLevel) => void;
  } = $props();

  let rootRef = $state<HTMLElement | null>(null);
  let triggerRef = $state<HTMLButtonElement | null>(null);
  let listRef = $state<HTMLElement | null>(null);
  let isOpen = $state(false);
  let highlightedIndex = $state(0);

  let selectedLevel = $derived(value ?? DEFAULT_THINKING_LEVEL);
  let selectedLabel = $derived(
    THINKING_LEVEL_OPTIONS.find(o => o.value === selectedLevel)?.label ??
      "Off",
  );
  let selectedIndex = $derived(
    THINKING_LEVEL_OPTIONS.findIndex(o => o.value === selectedLevel),
  );

  function syncHighlightedIndex() {
    highlightedIndex = selectedIndex >= 0 ? selectedIndex : 0;
  }

  function scrollToHighlighted() {
    queueMicrotask(() => {
      const el = listRef?.children[highlightedIndex] as
        | HTMLElement
        | undefined;
      el?.scrollIntoView({ block: "nearest" });
    });
  }

  async function openDropdown() {
    if (disabled) return;
    isOpen = true;
    syncHighlightedIndex();
    await tick();
    listRef?.focus();
    scrollToHighlighted();
  }

  function closeDropdown(options?: { focusTrigger?: boolean }) {
    isOpen = false;
    if (options?.focusTrigger) {
      queueMicrotask(() => triggerRef?.focus());
    }
  }

  function toggleDropdown() {
    if (isOpen) {
      closeDropdown();
      return;
    }
    void openDropdown();
  }

  function updateHighlight(nextIndex: number) {
    const maxIndex = THINKING_LEVEL_OPTIONS.length - 1;
    highlightedIndex = Math.min(Math.max(nextIndex, 0), maxIndex);
    scrollToHighlighted();
  }

  function selectLevel(level: RpcThinkingLevel) {
    onSelect(level);
    closeDropdown({ focusTrigger: true });
  }

  function handleTriggerKeydown(event: KeyboardEvent) {
    if (disabled) return;

    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        if (!isOpen) {
          void openDropdown();
          return;
        }
        updateHighlight(highlightedIndex + 1);
        break;
      case "ArrowUp":
        event.preventDefault();
        if (!isOpen) {
          void openDropdown();
          return;
        }
        updateHighlight(highlightedIndex - 1);
        break;
      case "Enter":
      case " ":
        event.preventDefault();
        toggleDropdown();
        break;
    }
  }

  function handleListKeydown(event: KeyboardEvent) {
    if (!isOpen) return;

    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        updateHighlight(
          (highlightedIndex + 1) % THINKING_LEVEL_OPTIONS.length,
        );
        break;
      case "ArrowUp":
        event.preventDefault();
        updateHighlight(
          (highlightedIndex - 1 + THINKING_LEVEL_OPTIONS.length) %
            THINKING_LEVEL_OPTIONS.length,
        );
        break;
      case "Home":
        event.preventDefault();
        updateHighlight(0);
        break;
      case "End":
        event.preventDefault();
        updateHighlight(THINKING_LEVEL_OPTIONS.length - 1);
        break;
      case "Enter":
      case " ": {
        event.preventDefault();
        const option = THINKING_LEVEL_OPTIONS[highlightedIndex];
        if (option) selectLevel(option.value);
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
    void value;
    if (!isOpen) return;
    syncHighlightedIndex();
    scrollToHighlighted();
  });
</script>

<div bind:this={rootRef} class="thinking-dropdown">
  <button
    bind:this={triggerRef}
    class="thinking-trigger"
    type="button"
    disabled={disabled}
    aria-expanded={isOpen}
    aria-haspopup="listbox"
    aria-label="Thinking level"
    aria-keyshortcuts="Shift+Tab"
    title="Thinking level · Shift+Tab"
    onclick={toggleDropdown}
    onkeydown={handleTriggerKeydown}
  >
    <span class="thinking-trigger-label" aria-hidden="true">Thinking</span>
    <span class="thinking-trigger-value">{selectedLabel}</span>
    <ChevronDown aria-hidden="true" size={11} style="flex-shrink: 0; color: var(--text-subtle)" />
  </button>

  {#if isOpen}
    <div class="thinking-menu">
      <ul
        bind:this={listRef}
        class="thinking-list"
        tabindex="-1"
        role="listbox"
        aria-label="Thinking level options"
        onkeydown={handleListKeydown}
      >
        {#each THINKING_LEVEL_OPTIONS as option, index (option.value)}
          <li class="thinking-list-item">
            <button
              class="thinking-option"
              type="button"
              class:highlighted={index === highlightedIndex}
              class:selected={option.value === selectedLevel}
              role="option"
              aria-selected={option.value === selectedLevel}
              onclick={() => selectLevel(option.value)}
              onmouseenter={() => (highlightedIndex = index)}
            >
              <span class="thinking-option-label">{option.label}</span>
              {#if option.value === selectedLevel}
                <Check
                  aria-hidden="true"
                  size={14}
                />
              {/if}
            </button>
          </li>
        {/each}
      </ul>
    </div>
  {/if}
</div>

<style>
  .thinking-dropdown {
    position: relative;
    flex-shrink: 0;
  }

  .thinking-trigger {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    height: 24px;
    padding: 0 9px;
    border-radius: 999px;
    border: none;
    background: var(--bg);
    color: var(--text-subtle);
    cursor: pointer;
    user-select: none;
    transition:
      background 0.15s ease,
      border-color 0.15s ease,
      color 0.15s ease,
      opacity 0.15s ease;
  }

  .thinking-trigger:hover:not(:disabled) {
    background: var(--bg);
    color: var(--text);
  }

  .thinking-trigger[aria-expanded="true"] {
    background: var(--bg);
    color: var(--text);
  }

  .thinking-trigger:focus-visible {
    outline: none;
    color: var(--text);
  }

  .thinking-trigger:disabled {
    opacity: 0.45;
    cursor: not-allowed;
  }

  .thinking-trigger-label {
    display: inline-flex;
    align-items: center;
    color: var(--text-subtle);
    font-family: var(--pi-font-sans);
    font-size: 0.64rem;
    line-height: 1.2;
    white-space: nowrap;
  }

  .thinking-trigger-value {
    min-width: 0;
    color: var(--text);
    font-family: var(--pi-font-mono);
    font-size: 0.64rem;
    line-height: 1.2;
    white-space: nowrap;
  }

  .thinking-menu {
    position: absolute;
    left: 0;
    bottom: calc(100% + 10px);
    width: 156px;
    padding: 6px;
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

  .thinking-list {
    margin: 0;
    padding: 0;
    list-style: none;
  }

  .thinking-list:focus {
    outline: none;
  }

  .thinking-list-item + .thinking-list-item {
    margin-top: 3px;
  }

  .thinking-option {
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

  .thinking-option:hover,
  .thinking-option.highlighted {
    background: var(--surface-hover);
    border-color: color-mix(in srgb, var(--border-strong) 84%, transparent);
    transform: translateX(1px);
  }

  .thinking-option.selected {
    background: var(--surface-selected);
    border-color: color-mix(in srgb, var(--accent) 24%, var(--border-strong));
  }

  .thinking-option-label {
    font-family: var(--pi-font-mono);
    font-size: 0.72rem;
    color: var(--text);
  }

  @media (max-width: 640px) {
    .thinking-menu {
      width: min(156px, calc(100vw - 24px));
    }
  }
</style>
