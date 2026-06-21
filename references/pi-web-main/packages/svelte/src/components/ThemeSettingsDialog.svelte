<script lang="ts">
  import Moon from "lucide-svelte/icons/moon";
  import Sun from "lucide-svelte/icons/sun";
  import X from "lucide-svelte/icons/x";
  import { onMount } from "svelte";
  import type { Base46Theme, ThemeMode } from "../themes";

  let {
    open = false,
    mode = "dark" as ThemeMode,
    darkThemeId = "",
    lightThemeId = "",
    darkThemes = [] as Base46Theme[],
    lightThemes = [] as Base46Theme[],
    themeStyle = "",
    onClose = () => {},
    onSetTheme = (_: string) => {},
  }: {
    open?: boolean;
    mode?: ThemeMode;
    darkThemeId?: string;
    lightThemeId?: string;
    darkThemes?: Base46Theme[];
    lightThemes?: Base46Theme[];
    themeStyle?: string;
    onClose?: () => void;
    onSetTheme?: (themeId: string) => void;
  } = $props();

  let currentThemes = $derived(
    mode === "dark" ? darkThemes : lightThemes,
  );
  let currentThemeId = $derived(
    mode === "dark" ? darkThemeId : lightThemeId,
  );
  let currentModeLabel = $derived(
    mode === "dark" ? "Dark theme" : "Light theme",
  );
  let defaultThemeId = $derived(
    mode === "dark" ? "pi-base46-dark" : "pi-base46-light",
  );
  let displayThemes = $derived.by(() => {
    const def = currentThemes.find(t => t.id === defaultThemeId);
    const rest = currentThemes.filter(t => t.id !== defaultThemeId);
    return [
      ...(def
        ? [{ theme: def, label: "Default (Built-in)", isDefault: true }]
        : []),
      ...rest.map(t => ({ theme: t, label: t.label, isDefault: false })),
    ];
  });

  function handleKeydown(event: KeyboardEvent) {
    if (event.key === "Escape" && open) onClose();
  }

  $effect(() => {
    if (typeof document === "undefined") return;
    document.body.style.overflow = open ? "hidden" : "";
  });

  onMount(() => {
    window.addEventListener("keydown", handleKeydown);
    return () => {
      window.removeEventListener("keydown", handleKeydown);
      if (typeof document !== "undefined") {
        document.body.style.removeProperty("overflow");
      }
    };
  });
</script>

{#if open}
  <div
    class="theme-dialog-overlay"
    style={themeStyle}
    role="button"
    tabindex="0"
    onclick={() => onClose()}
    onkeydown={(e) => (e.key === "Enter" || e.key === " ") && onClose()}
  >
    <div
      class="theme-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="theme-dialog-title"
      tabindex="-1"
      onclick={(e) => e.stopPropagation()}
      onkeydown={(e) => e.stopPropagation()}
    >
      <div class="theme-dialog-body">
        <div class="theme-dialog-top">
          <div class="theme-dialog-intro">
            <div class="theme-title-row">
              {#if mode === "dark"}
                <Moon aria-hidden="true" size={18} style="color: var(--accent-hover); flex-shrink: 0" />
              {:else}
                <Sun aria-hidden="true" size={18} style="color: var(--accent-hover); flex-shrink: 0" />
              {/if}
              <h2 id="theme-dialog-title" class="theme-dialog-title">
                {currentModeLabel}
              </h2>
            </div>
          </div>
          <button
            class="theme-dialog-close"
            type="button"
            aria-label="Close theme settings"
            onclick={() => onClose()}
          >
            <X aria-hidden="true" size={15} />
          </button>
        </div>

        <!-- <div class="theme-section-meta">
          <span class="theme-section-label">{currentModeLabel}</span>
          <span class="theme-section-subtle">
            {displayThemes.length} themes available
          </span>
        </div> -->

        <div class="theme-grid">
          {#each displayThemes as item (item.theme.id)}
            <button
              class="theme-card"
              class:current={currentThemeId === item.theme.id}
              type="button"
              onclick={() => onSetTheme(item.theme.id)}
            >
              <div class="theme-card-preview">
                <span class="theme-swatch background" style="background: {item.theme.base16.base00}"></span>
                <span class="theme-swatch panel" style="background: {item.theme.base30.one_bg2}"></span>
                <span class="theme-swatch accent" style="background: {item.theme.base16.base0D}"></span>
                <span class="theme-swatch success" style="background: {item.theme.base16.base0B}"></span>
                <span class="theme-swatch warning" style="background: {item.theme.base16.base0A}"></span>
                <span class="theme-swatch neutral" style="background: {item.theme.base16.base05}"></span>
              </div>
              <span class="theme-card-title-row">
                <span class="theme-card-title">{item.label}</span>
                {#if currentThemeId === item.theme.id}
                  <span class="theme-card-badge">Active</span>
                {/if}
              </span>
            </button>
          {/each}
        </div>
      </div>
    </div>
  </div>
{/if}

<style>
  .theme-dialog-overlay {
    position: fixed;
    inset: 0;
    z-index: 1000;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 20px;
    background: var(--overlay);
  }

  .theme-dialog {
    width: min(1120px, 100%);
    max-height: min(82vh, 760px);
    overflow: hidden;
    border: 1px solid color-mix(in srgb, var(--border-strong) 86%, transparent);
    border-radius: 18px;
    background-color: var(--panel);
    background-image: linear-gradient(
      180deg,
      color-mix(in srgb, var(--panel-2) 94%, var(--panel) 6%),
      var(--panel)
    );
    box-shadow: var(--shadow-floating);
  }

  .theme-dialog-body {
    max-height: min(82vh, 760px);
    overflow-y: auto;
    padding: 18px 20px 20px;
    background: var(--panel);
  }

  .theme-dialog-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    margin-bottom: 16px;
  }

  .theme-dialog-intro {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .theme-title-row {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .theme-dialog-title {
    margin: 0;
    color: var(--text);
    font-size: 1.02rem;
    line-height: 1.15;
    font-weight: 700;
  }

  .theme-dialog-close {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    border: none;
    border-radius: 999px;
    background: transparent;
    color: var(--text-subtle);
    cursor: pointer;
    transition:
      color 0.15s ease,
      transform 0.15s ease;
  }

  .theme-dialog-close:hover,
  .theme-dialog-close:focus-visible {
    border-color: var(--accent);
    color: var(--text);
    transform: translateY(-1px);
  }

  .theme-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 10px;
  }

  .theme-card {
    display: grid;
    gap: 6px;
    padding: 10px;
    text-align: left;
    border: 1px solid color-mix(in srgb, var(--border) 84%, transparent);
    border-radius: 14px;
    background: color-mix(in srgb, var(--panel) 92%, var(--panel-2) 8%);
    color: inherit;
    cursor: pointer;
    transition:
      border-color 0.16s ease,
      background 0.16s ease,
      transform 0.16s ease,
      box-shadow 0.16s ease;
  }

  .theme-card:hover {
    transform: translateY(-1px);
    border-color: color-mix(in srgb, var(--accent) 38%, var(--border-strong));
  }

  .theme-card:focus-visible {
    outline: none;
    border-color: var(--accent);
    box-shadow: 0 0 0 3px var(--focus-ring);
  }

  .theme-card.current {
    border-color: var(--accent);
    background: color-mix(in srgb, var(--surface-active) 68%, var(--panel));
    box-shadow: 0 0 0 1px color-mix(in srgb, var(--accent) 20%, transparent);
  }

  .theme-card-preview {
    display: grid;
    grid-template-columns: repeat(6, minmax(0, 1fr));
    gap: 6px;
  }

  .theme-swatch {
    display: block;
    width: 100%;
    aspect-ratio: 1;
    border-radius: 999px;
    border: 1px solid rgba(255, 255, 255, 0.06);
  }

  .theme-card-title-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }

  .theme-card-title {
    color: var(--text);
    font-size: 0.84rem;
    font-weight: 600;
  }

  .theme-card-badge {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 2px 7px;
    border-radius: 999px;
    background: color-mix(in srgb, var(--accent) 18%, transparent);
    color: var(--accent-hover);
    font-size: 0.62rem;
    font-weight: 600;
  }

  @media (max-width: 900px) {
    .theme-dialog-overlay {
      padding: 10px;
      align-items: flex-end;
    }

    .theme-dialog {
      width: 100%;
      max-height: 88vh;
      border-radius: 18px 18px 0 0;
    }

    .theme-dialog-body {
      max-height: 88vh;
      padding: 16px 16px 20px;
    }

    .theme-grid {
      grid-template-columns: 1fr;
    }
  }
</style>
