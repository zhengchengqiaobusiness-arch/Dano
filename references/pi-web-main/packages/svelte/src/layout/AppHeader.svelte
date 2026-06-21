<script lang="ts">
  import Menu from "lucide-svelte/icons/menu";
  import Moon from "lucide-svelte/icons/moon";
  import Palette from "lucide-svelte/icons/palette";
  import PanelLeftClose from "lucide-svelte/icons/panel-left-close";
  import PanelLeftOpen from "lucide-svelte/icons/panel-left-open";
  import PanelRightClose from "lucide-svelte/icons/panel-right-close";
  import PanelRightOpen from "lucide-svelte/icons/panel-right-open";
  import Sun from "lucide-svelte/icons/sun";
  import type { ThemeMode } from "../themes";

  let {
    theme,
    nextThemeLabel,
    sessionTitle,
    workspaceName,
    sidebarCollapsed = false,
    showOutlineToggle = false,
    outlineSidebarOpen = false,
    desktopPlatform = null,
    desktopTitleBarStyle = "system",
    onToggleSidebar = () => {},
    onToggleSidebarCollapse = () => {},
    onToggleOutlineSidebar = () => {},
    onToggleTheme = () => {},
    onOpenThemeSettings = () => {},
  }: {
    theme: "dark" | "light";
    nextThemeLabel: ThemeMode;
    sessionTitle: string;
    workspaceName: string | null;
    sidebarCollapsed?: boolean;
    showOutlineToggle?: boolean;
    outlineSidebarOpen?: boolean;
    desktopPlatform?: PiDesktopPlatform | null;
    desktopTitleBarStyle?: PiDesktopTitleBarStyle;
    onToggleSidebar?: () => void;
    onToggleSidebarCollapse?: () => void;
    onToggleOutlineSidebar?: () => void;
    onToggleTheme?: () => void;
    onOpenThemeSettings?: () => void;
  } = $props();
</script>

<header
  class="app-header"
  class:desktop-chrome={desktopTitleBarStyle !== "system"}
  class:desktop-mac={desktopPlatform === "darwin"}
  class:desktop-win={desktopPlatform === "win32"}
  class:desktop-left-safe={desktopPlatform === "darwin" && sidebarCollapsed}
>
  <div class="header-leading">
    <button
      class="hamburger"
      aria-label="Toggle sidebar"
      onclick={onToggleSidebar}
    >
      <Menu aria-hidden="true" size={20} />
    </button>
    <button
      class="sidebar-collapse"
      type="button"
      aria-label={sidebarCollapsed
        ? "Expand sessions sidebar"
        : "Collapse sessions sidebar"}
      title={sidebarCollapsed
        ? "Expand sessions sidebar"
        : "Collapse sessions sidebar"}
      onclick={onToggleSidebarCollapse}
    >
      {#if sidebarCollapsed}
        <PanelLeftOpen aria-hidden="true" size={18} />
      {:else}
        <PanelLeftClose aria-hidden="true" size={18} />
      {/if}
    </button>
    <div class="header-brand">
      {#if workspaceName}
        <p class="workspace-name">{workspaceName}</p>
      {/if}
      <h1 class="app-title">{sessionTitle}</h1>
    </div>
  </div>
  <div class="header-status">
    {#if showOutlineToggle}
      <button
        class="outline-toggle"
        type="button"
        aria-label={outlineSidebarOpen
          ? "Collapse right sidebar"
          : "Expand right sidebar"}
        title={outlineSidebarOpen
          ? "Collapse right sidebar"
          : "Expand right sidebar"}
        onclick={onToggleOutlineSidebar}
      >
        {#if outlineSidebarOpen}
          <PanelRightClose aria-hidden="true" size={18} />
        {:else}
          <PanelRightOpen aria-hidden="true" size={18} />
        {/if}
      </button>
    {/if}
    <button
      class="appearance-toggle"
      type="button"
      aria-label="Open appearance settings"
      title="Open appearance settings"
      onclick={onOpenThemeSettings}
    >
      <Palette aria-hidden="true" size={18} />
    </button>
    <button
      class="theme-toggle"
      type="button"
      aria-label={`Switch to ${nextThemeLabel} theme`}
      title={`Switch to ${nextThemeLabel} theme`}
      onclick={onToggleTheme}
    >
      {#if theme === "dark"}
        <Sun aria-hidden="true" size={18} />
      {:else}
        <Moon aria-hidden="true" size={18} />
      {/if}
    </button>
  </div>
</header>

<style>
  .app-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 6px 8px 10px 8px;
    height: 44px;
    border-bottom: 1px solid var(--border);
    border-top-left-radius: 14px;
    background: var(--bg);
    flex-shrink: 0;
    z-index: 20;
  }

  .app-header.desktop-chrome {
    -webkit-app-region: drag;
    padding-top: calc(6px + var(--desktop-top-inset, 0px));
    padding-left: 8px;
    padding-right: calc(8px + var(--desktop-right-inset, 0px));
    height: calc(44px + var(--desktop-top-inset, 0px));
  }

  .app-header.desktop-left-safe {
    padding-left: calc(8px + var(--desktop-left-inset, 0px));
  }

  .header-leading {
    display: flex;
    align-items: center;
    gap: 10px;
    min-width: 0;
  }

  .hamburger {
    display: none;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    padding: 0;
    background: none;
    border: none;
    color: var(--text-muted);
    cursor: pointer;
  }

  .header-brand {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    min-width: 0;
  }

  .workspace-name,
  .app-title {
    margin: 0;
    max-width: min(100%, 48vw);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .workspace-name {
    font-size: 0.64rem;
    line-height: 1.05;
    font-weight: 600;
    letter-spacing: 0.03em;
    color: var(--text-subtle);
  }

  .app-title {
    font-size: 0.9rem;
    line-height: 1;
    font-weight: 600;
    letter-spacing: 0.01em;
    color: var(--text);
  }

  .header-status {
    display: flex;
    align-items: center;
    gap: 4px;
    flex-shrink: 0;
  }

  .app-header.desktop-chrome button {
    -webkit-app-region: no-drag;
  }

  .sidebar-collapse,
  .outline-toggle,
  .appearance-toggle,
  .theme-toggle {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    height: 28px;
    width: 28px;
    flex: 0 0 28px;
    padding: 0 7px;
    border-radius: 8px;
    border: none;
    background: transparent;
    font-size: 0.72rem;
    color: var(--text-subtle);
    cursor: pointer;
    transition:
      background 0.15s ease,
      color 0.15s ease,
      transform 0.15s ease;
  }

  .sidebar-collapse:hover,
  .outline-toggle:hover,
  .appearance-toggle:hover,
  .theme-toggle:hover {
    background: var(--surface-hover);
    color: var(--text-muted);
    transform: translateY(-1px);
  }

  .sidebar-collapse:focus-visible,
  .outline-toggle:focus-visible,
  .appearance-toggle:focus-visible,
  .theme-toggle:focus-visible {
    outline: none;
    box-shadow: 0 0 0 3px var(--focus-ring);
  }

  .sidebar-collapse {
    justify-content: center;
    width: 28px;
    height: 28px;
    padding: 0;
    flex: 0 0 28px;
  }

  .outline-toggle,
  .appearance-toggle,
  .theme-toggle {
    justify-content: center;
    width: 28px;
    height: 28px;
    padding: 0;
    flex: 0 0 28px;
  }

  @media (max-width: 900px) {
    .hamburger {
      display: flex;
    }

    .sidebar-collapse {
      display: none;
    }

    .app-header {
      height: auto;
      padding: calc(env(safe-area-inset-top) + 7px) 11px 9px;
      border-top-left-radius: 0;
    }

    .header-status {
      gap: 4px;
    }
  }

  @media (max-width: 640px) {
    .app-header {
      padding-inline: 9px;
      gap: 8px;
    }

    .header-leading {
      gap: 8px;
    }

    .workspace-name,
    .app-title {
      max-width: min(100%, 42vw);
    }

    .hamburger {
      width: 24px;
      height: 24px;
    }

    .outline-toggle,
    .appearance-toggle,
    .theme-toggle {
      width: 26px;
      height: 26px;
      flex: 0 0 26px;
    }
  }
</style>
