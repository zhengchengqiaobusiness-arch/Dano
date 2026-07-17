<script lang="ts">
  import { onDestroy, tick } from "svelte";
  import { Command, Popover } from "bits-ui";
  import Check from "lucide-svelte/icons/check";
  import ChevronDown from "lucide-svelte/icons/chevron-down";
  import CircleX from "lucide-svelte/icons/circle-x";
  import LoaderCircle from "lucide-svelte/icons/loader-circle";
  import RotateCw from "lucide-svelte/icons/rotate-cw";
  import Search from "lucide-svelte/icons/search";
  import "./questionToolControls.css";

  const SEARCH_DELAY_MS = 300;
  const POPOVER_GAP_PX = 6;

  type RemoteQuestionSelectOption = {
    key: string;
    label: string;
  };

  let {
    id,
    label,
    value,
    options,
    disabled = false,
    loading = false,
    error = false,
    hasMore = false,
    placeholder,
    searchPlaceholder,
    loadingLabel,
    emptyLabel,
    errorLabel,
    retryLabel,
    clearLabel,
    loadMoreLabel,
    onValueChange,
    onSearch,
    onLoadMore,
  }: {
    id: string;
    label: string;
    value: string;
    options: RemoteQuestionSelectOption[];
    disabled?: boolean;
    loading?: boolean;
    error?: boolean;
    hasMore?: boolean;
    placeholder: string;
    searchPlaceholder: string;
    loadingLabel: string;
    emptyLabel: string;
    errorLabel: string;
    retryLabel: string;
    clearLabel: string;
    loadMoreLabel: string;
    onValueChange: (value: string) => void;
    onSearch: (search: string) => void;
    onLoadMore: () => void;
  } = $props();

  let open = $state(false);
  let search = $state("");
  let inputElement = $state<HTMLInputElement | null>(null);
  let searchTimer: ReturnType<typeof setTimeout> | undefined;
  const selected = $derived(options.find(option => option.key === value));
  const status = $derived(
    loading && options.length === 0
      ? "loading"
      : error
        ? "error"
        : options.length === 0
          ? "empty"
          : "ready",
  );

  onDestroy(() => {
    if (searchTimer) clearTimeout(searchTimer);
  });

  function handleOpenChange(nextOpen: boolean) {
    open = nextOpen;
    if (!nextOpen) {
      if (search) {
        search = "";
        if (searchTimer) clearTimeout(searchTimer);
        onSearch("");
      }
      return;
    }
    void tick().then(() => inputElement?.focus());
  }

  function handleSearchInput(event: Event) {
    if (!(event.currentTarget instanceof HTMLInputElement)) return;
    search = event.currentTarget.value;
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(() => onSearch(search.trim()), SEARCH_DELAY_MS);
  }

  function selectValue(nextValue: string) {
    onValueChange(nextValue === value ? "" : nextValue);
    open = false;
  }

  function clearValue() {
    onValueChange("");
    open = false;
  }
</script>

<Popover.Root {open} onOpenChange={handleOpenChange}>
  <Popover.Trigger
    id={`${id}-trigger`}
    class="question-input question-combobox-trigger"
    role="combobox"
    aria-label={label}
    aria-expanded={open}
    {disabled}
  >
    <span class:placeholder={!selected}>{selected?.label ?? placeholder}</span>
    <ChevronDown size={16} aria-hidden="true" />
  </Popover.Trigger>

  <Popover.Portal to=".app-shell">
    <Popover.Content
      class="question-combobox-popover"
      align="start"
      sideOffset={POPOVER_GAP_PX}
      collisionPadding={12}
      trapFocus={false}
      onOpenAutoFocus={(event) => event.preventDefault()}
    >
      <Command.Root shouldFilter={false} label={label} loop>
        <div class="question-combobox-search">
          <Search size={16} aria-hidden="true" />
          <Command.Input
            bind:ref={inputElement}
            value={search}
            class="question-combobox-input"
            placeholder={searchPlaceholder}
            aria-label={searchPlaceholder}
            oninput={handleSearchInput}
          />
          {#if loading}
            <LoaderCircle class="question-combobox-spinner" size={16} aria-label={loadingLabel} />
          {/if}
        </div>

        <Command.List class="question-combobox-list">
          {#if value}
            <Command.Item class="question-combobox-item muted" value="__clear-selection" onSelect={clearValue}>
              <CircleX size={16} aria-hidden="true" />
              <span>{clearLabel}</span>
            </Command.Item>
          {/if}

          {#if status === "loading"}
            <div class="question-combobox-state" aria-live="polite">{loadingLabel}</div>
          {:else if status === "error"}
            <div class="question-combobox-state error" role="alert">
              <span>{errorLabel}</span>
              <button type="button" class="question-combobox-retry" onclick={() => onSearch(search.trim())}>
                <RotateCw size={14} aria-hidden="true" />
                {retryLabel}
              </button>
            </div>
          {:else if status === "empty"}
            <div class="question-combobox-state">{emptyLabel}</div>
          {:else}
            {#each options as option (option.key)}
              <Command.Item
                class="question-combobox-item"
                value={option.key}
                data-committed-selected={option.key === value ? "" : undefined}
                onSelect={() => selectValue(option.key)}
              >
                <Check class={option.key === value ? "visible" : ""} size={16} aria-hidden="true" />
                <span>{option.label}</span>
              </Command.Item>
            {/each}
          {/if}

          {#if hasMore && status === "ready"}
            <button
              type="button"
              class="question-combobox-load-more"
              disabled={loading}
              onclick={onLoadMore}
            >
              {loading ? loadingLabel : loadMoreLabel}
            </button>
          {/if}
        </Command.List>
      </Command.Root>
    </Popover.Content>
  </Popover.Portal>
</Popover.Root>

<style>
  :global(.question-combobox-trigger) {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    width: min(100%, 320px);
    color: var(--text);
    text-align: left;
    cursor: pointer;
    transition-property: border-color, background-color, box-shadow, transform;
    transition-duration: 120ms;
    transition-timing-function: ease;
  }

  :global(.question-combobox-trigger:hover:not(:disabled)) {
    border-color: var(--border-strong);
    background: color-mix(in srgb, var(--control-bg) 92%, var(--accent));
  }

  :global(.question-combobox-trigger:active:not(:disabled)) {
    transform: scale(0.96);
  }

  :global(.question-combobox-trigger[data-state="open"]) {
    border-color: var(--accent);
    box-shadow: 0 0 0 2px color-mix(in srgb, var(--focus-ring) 45%, transparent);
  }

  :global(.question-combobox-trigger .placeholder) {
    color: var(--text-subtle);
  }

  :global(.question-combobox-trigger > span) {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  :global(.question-combobox-trigger > svg) {
    flex: 0 0 auto;
    color: var(--text-subtle);
    transition: transform 120ms ease;
  }

  :global(.question-combobox-trigger[data-state="open"] > svg) {
    transform: rotate(180deg);
  }

  :global(.question-combobox-popover) {
    z-index: 30;
    width: min(var(--bits-popover-anchor-width), calc(100vw - 24px));
    min-width: min(260px, calc(100vw - 24px));
    max-height: min(360px, var(--bits-popover-content-available-height));
    overflow: hidden;
    border: 1px solid color-mix(in srgb, var(--border) 82%, transparent);
    border-radius: 12px;
    background: var(--panel);
    color: var(--text);
    box-shadow: var(--shadow-raised);
  }

  :global(.question-combobox-search) {
    display: flex;
    align-items: center;
    gap: 9px;
    min-height: 40px;
    padding: 0 12px;
    border-bottom: 1px solid var(--border);
    color: var(--text-subtle);
  }

  :global(.question-combobox-input) {
    min-width: 0;
    flex: 1;
    height: 40px;
    padding: 0;
    border: 0;
    outline: 0;
    background: transparent;
    color: var(--text);
    font: inherit;
  }

  :global(.question-combobox-input::placeholder) {
    color: var(--text-subtle);
  }

  :global(.question-combobox-spinner) {
    flex: 0 0 auto;
    animation: question-combobox-spin 0.75s linear infinite;
  }

  :global(.question-combobox-list) {
    max-height: min(300px, calc(var(--bits-popover-content-available-height) - 42px));
    overflow-y: auto;
    padding: 5px;
    overscroll-behavior: contain;
  }

  :global(.question-combobox-item) {
    display: grid;
    grid-template-columns: 18px minmax(0, 1fr);
    align-items: center;
    gap: 8px;
    min-height: 36px;
    padding: 7px 9px;
    border-radius: 8px;
    color: var(--text);
    font-size: 0.9rem;
    line-height: 1.35;
    cursor: pointer;
    outline: none;
  }

  :global(.question-combobox-item[data-selected]),
  :global(.question-combobox-item[data-committed-selected]),
  :global(.question-combobox-item:hover) {
    background: color-mix(in srgb, var(--accent) 11%, var(--panel));
    color: var(--accent);
  }

  :global(.question-combobox-item.muted) {
    color: var(--text-subtle);
  }

  :global(.question-combobox-item > svg:not(.visible)) {
    opacity: 0;
  }

  :global(.question-combobox-item.muted > svg) {
    opacity: 1;
  }

  :global(.question-combobox-item > span) {
    min-width: 0;
    overflow-wrap: anywhere;
  }

  :global(.question-combobox-state) {
    display: grid;
    place-items: center;
    gap: 8px;
    min-height: 88px;
    padding: 16px;
    color: var(--text-subtle);
    font-size: 0.86rem;
    text-align: center;
    text-wrap: pretty;
  }

  :global(.question-combobox-state.error) {
    color: var(--text);
  }

  :global(.question-combobox-retry),
  :global(.question-combobox-load-more) {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    min-height: 36px;
    border: 0;
    border-radius: 8px;
    background: transparent;
    color: var(--accent);
    font: inherit;
    font-weight: 600;
    cursor: pointer;
  }

  :global(.question-combobox-retry:focus-visible),
  :global(.question-combobox-load-more:focus-visible) {
    outline: 2px solid var(--focus-ring);
    outline-offset: 1px;
  }

  :global(.question-combobox-load-more) {
    width: 100%;
    margin-top: 3px;
  }

  :global(.question-combobox-load-more:hover:not(:disabled)),
  :global(.question-combobox-retry:hover) {
    background: color-mix(in srgb, var(--accent) 9%, transparent);
  }

  :global(.question-combobox-load-more:disabled) {
    cursor: wait;
    opacity: 0.55;
  }

  @keyframes question-combobox-spin {
    to { transform: rotate(360deg); }
  }

  @media (max-width: 640px) {
    :global(.question-combobox-trigger) {
      width: 100%;
    }

    :global(.question-combobox-item) {
      min-height: 44px;
    }
  }
</style>
