<script lang="ts">
  import {
    ACCENT_COLOR_PRESET_KEYS,
    type AccentColorPreset,
  } from "@dano/types/protocol";
  import Check from "lucide-svelte/icons/check";
  import Palette from "lucide-svelte/icons/palette";
  import X from "lucide-svelte/icons/x";
  import * as Dialog from "./ui/dialog";
  import { t } from "../i18n";
  import { ACCENT_COLOR_PRESETS, DEFAULT_ACCENT_COLOR_PRESET } from "../themes";

  let {
    open = false,
    selectedPreset = DEFAULT_ACCENT_COLOR_PRESET,
    themeStyle = "",
    onClose = () => {},
    onSelectPreset = (_: AccentColorPreset) => {},
  }: {
    open?: boolean;
    selectedPreset?: AccentColorPreset;
    themeStyle?: string;
    onClose?: () => void;
    onSelectPreset?: (preset: AccentColorPreset) => void;
  } = $props();

  function presetLabel(preset: AccentColorPreset): Parameters<typeof t>[0] {
    return `themeColor.${preset}` as Parameters<typeof t>[0];
  }

  function handleOpenChange(nextOpen: boolean) {
    if (!nextOpen && open) onClose();
  }
</script>

<Dialog.Root {open} onOpenChange={handleOpenChange}>
  <Dialog.Portal>
    <Dialog.Overlay
      class="theme-dialog-overlay"
      style={themeStyle}
      onclick={onClose}
    />
    <Dialog.Content
      class="theme-dialog"
      style={themeStyle}
      aria-labelledby="theme-color-dialog-title"
    >
      <header class="theme-dialog-top">
        <Dialog.Title
          id="theme-color-dialog-title"
          class="theme-dialog-title"
          level={2}
        >
          <Palette size={18} aria-hidden="true" />
          <span>{t("appHeader.themeColor")}</span>
        </Dialog.Title>
        <Dialog.Close
          class="theme-dialog-close"
          aria-label={t("themeColor.close")}
        >
          <X size={17} aria-hidden="true" />
        </Dialog.Close>
      </header>

      <div class="theme-color-list">
        {#each ACCENT_COLOR_PRESET_KEYS as preset (preset)}
          <button
            class="theme-color-row"
            class:selected={selectedPreset === preset}
            type="button"
            data-theme-color-preset={preset}
            aria-pressed={selectedPreset === preset}
            onclick={() => onSelectPreset(preset)}
          >
            <span
              class="theme-color-swatch"
              style={`background: ${ACCENT_COLOR_PRESETS[preset]}`}
              aria-hidden="true"
            ></span>
            <span>{t(presetLabel(preset))}</span>
            <span class="theme-color-check-slot" aria-hidden="true">
              {#if selectedPreset === preset}
                <Check class="theme-color-check" size={16} strokeWidth={2.5} />
              {/if}
            </span>
          </button>
        {/each}
      </div>
    </Dialog.Content>
  </Dialog.Portal>
</Dialog.Root>

<style>
  :global(.theme-dialog-overlay) {
    position: fixed;
    inset: 0;
    z-index: 1000;
    background: var(--overlay);
  }

  :global(.theme-dialog) {
    box-sizing: border-box;
    position: fixed;
    top: 50%;
    left: 50%;
    z-index: 1001;
    width: min(380px, calc(100vw - 40px));
    max-height: calc(100vh - 40px);
    padding: 14px;
    overflow-y: auto;
    transform: translate(-50%, -50%);
    border: 0;
    border-radius: 22px;
    background: var(--panel);
    color: var(--text);
    box-shadow: var(--shadow-floating);
  }

  :global(.theme-dialog-top) {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
    margin-bottom: 18px;
  }

  :global(.theme-dialog-title) {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 0;
    color: var(--text);
    font-size: 1.05rem;
    font-weight: 700;
    line-height: 1.25;
  }

  :global(.theme-dialog-close) {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 40px;
    height: 40px;
    flex: 0 0 40px;
    padding: 0;
    border: 0;
    border-radius: 999px;
    background: transparent;
    color: var(--text-subtle);
    cursor: pointer;
    transition:
      background 150ms ease,
      color 150ms ease,
      transform 150ms ease;
  }

  :global(.theme-dialog-close:hover) {
    background: var(--surface-hover);
    color: var(--text);
  }

  :global(.theme-dialog-close:active),
  .theme-color-row:active {
    transform: scale(0.96);
  }

  :global(.theme-dialog-close:focus-visible),
  .theme-color-row:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }

  .theme-color-list {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .theme-color-row {
    box-sizing: border-box;
    display: grid;
    grid-template-columns: 14px minmax(0, 1fr) 20px;
    align-items: center;
    gap: 12px;
    min-height: 48px;
    padding: 0 14px;
    border: 0;
    border-radius: 12px;
    background: transparent;
    color: var(--text);
    font: inherit;
    font-size: 0.9rem;
    text-align: left;
    cursor: pointer;
    transition:
      background 150ms ease,
      transform 150ms ease;
  }

  .theme-color-row:hover {
    background: var(--surface-hover);
  }

  .theme-color-row.selected {
    background: var(--surface-active);
  }

  .theme-color-swatch {
    box-sizing: border-box;
    display: block;
    width: 12px;
    height: 12px;
    border: 1px solid color-mix(in srgb, var(--text) 12%, transparent);
    border-radius: 999px;
  }

  .theme-color-check-slot {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 20px;
    height: 20px;
  }

  @media (max-width: 640px) {
    :global(.theme-dialog) {
      width: min(380px, calc(100vw - 40px));
      padding: 16px;
      border-radius: 20px;
    }
  }
</style>
