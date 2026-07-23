<script lang="ts">
  import CircleUserRound from "lucide-svelte/icons/circle-user-round";
  import Menu from "lucide-svelte/icons/menu";
  import Palette from "lucide-svelte/icons/palette";
  import SquarePen from "lucide-svelte/icons/square-pen";
  import type { BridgeUserSummary } from "@dano/types/protocol";
  import * as Popover from "../components/ui/popover";
  import type { ConnectionStatus } from "../composables/bridgeStore.svelte";
  import { t } from "../i18n";

  let {
    connectionStatus,
    disconnectReason = "",
    onReconnect,
    onNewSession,
    newSessionPending = false,
    showNewSession = true,
    currentUser,
    onOpenTheme,
  }: {
    connectionStatus: ConnectionStatus;
    disconnectReason?: string;
    onReconnect?: () => void;
    onNewSession?: () => void;
    newSessionPending?: boolean;
    showNewSession?: boolean;
    currentUser?: BridgeUserSummary;
    onOpenTheme?: () => void;
  } = $props();

  let menuOpen = $state(false);
  let failedAvatarUrl = $state<string | null>(null);

  const statusMeta = $derived.by(() => {
    switch (connectionStatus) {
      case "connected":
        return { className: "connected", label: t("appHeader.connection.connected") };
      case "connecting":
        return { className: "connecting", label: t("appHeader.connection.connecting") };
      case "disconnected":
      default:
        return { className: "disconnected", label: t("appHeader.connection.disconnected") };
    }
  });

  const title = $derived(
    connectionStatus === "disconnected" && disconnectReason
      ? disconnectReason
      : statusMeta.label,
  );

  function openTheme() {
    menuOpen = false;
    onOpenTheme?.();
  }
</script>

<header class="app-header">
  <div class="header-leading">
    <Popover.Root open={menuOpen} onOpenChange={(open) => (menuOpen = open)}>
      <Popover.Trigger
        class="menu-button"
        aria-label={t("appHeader.menu")}
        title={t("appHeader.menu")}
      >
        <Menu size={14} strokeWidth={2.5} aria-hidden="true" />
      </Popover.Trigger>

      <Popover.Content
        class="header-menu"
        align="end"
        sideOffset={8}
        collisionPadding={10}
        trapFocus={false}
      >
        <button class="theme-menu-item" type="button" onclick={openTheme}>
          <Palette size={16} aria-hidden="true" />
          <span>{t("appHeader.themeColor")}</span>
        </button>
        <div class="header-menu-separator" role="separator"></div>
        <div class="header-user-summary">
          {#if currentUser?.avatarUrl && currentUser.avatarUrl !== failedAvatarUrl}
            <img
              class="header-user-avatar"
              src={currentUser.avatarUrl}
              alt=""
              width="20"
              height="20"
              onerror={() => (failedAvatarUrl = currentUser?.avatarUrl ?? null)}
            />
          {:else}
            <CircleUserRound
              class="header-user-placeholder"
              size={17}
              aria-hidden="true"
            />
          {/if}
          <span>{currentUser?.username ?? t("appHeader.defaultUser")}</span>
        </div>
      </Popover.Content>
    </Popover.Root>
    <button
      class={`connection-status ${statusMeta.className}`}
      type="button"
      title={title}
      onclick={() => {
        if (connectionStatus === "disconnected") onReconnect?.();
      }}
    >
      <span class="status-dot" aria-hidden="true"></span>
      <span>{statusMeta.label}</span>
    </button>
  </div>
  <div class="header-trailing">
    {#if showNewSession}
      <button
        class="new-session-button"
        type="button"
        aria-label={t("appHeader.newSession")}
        title={t("appHeader.newSession")}
        disabled={newSessionPending}
        onclick={() => onNewSession?.()}
      >
        <SquarePen size={18} strokeWidth={2.5} aria-hidden="true" />
        <span>{t("appHeader.newSession")}</span>
      </button>
    {/if}
  </div>
</header>

<style>
  .app-header {
    --header-control-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);

    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    height: auto;
    margin: 10px 10px 0;
    border-radius: 999px;
    flex-shrink: 0;
    z-index: 20;
  }

  .new-session-button {
    box-sizing: border-box;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    height: 40px;
    padding: 0 14px;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: var(--panel);
    color: var(--text);
    font: inherit;
    font-size: 0.82rem;
    font-weight: 700;
    line-height: 1;
    cursor: pointer;
    box-shadow: var(--header-control-shadow);
    transition:
      background 150ms ease,
      transform 150ms ease;
  }

  .new-session-button:hover:not(:disabled) {
    background: var(--panel-2);
  }

  .new-session-button:active:not(:disabled) {
    transform: scale(0.96);
  }

  .new-session-button:focus-visible {
    outline: 2px solid var(--text-muted);
    outline-offset: 2px;
  }

  .new-session-button:disabled {
    cursor: wait;
    opacity: 0.6;
  }

  .header-leading {
    display: flex;
    align-items: center;
    gap: 8px;
    height: 100%;
    min-width: 0;
  }

  .header-trailing {
    display: flex;
    align-items: center;
    gap: 12px;
    flex: 0 0 auto;
  }

  .connection-status {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    flex: 0 0 auto;
    height: 26px;
    padding: 0 10px;
    border: none;
    border-radius: 999px;
    color: var(--text);
    font-size: 0.78rem;
    font-weight: 700;
    line-height: 1;
    background: color-mix(in srgb, var(--panel) 65%, transparent);
    -webkit-backdrop-filter: blur(2px);
    backdrop-filter: blur(2px);
    box-shadow: var(--header-control-shadow);
    cursor: pointer;
    transition:
      background 150ms ease,
      box-shadow 150ms ease,
      transform 150ms ease;
  }

  .connection-status:hover {
    background: color-mix(in srgb, var(--panel-2) 76%, transparent);
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
  }

  .connection-status:active {
    transform: scale(0.96);
  }

  .connection-status:focus-visible,
  :global(.menu-button:focus-visible),
  :global(.theme-menu-item:focus-visible) {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }

  :global(.menu-button) {
    box-sizing: border-box;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 26px;
    height: 26px;
    padding: 0;
    border: 0;
    border-radius: 999px;
    background: color-mix(in srgb, var(--panel) 65%, transparent);
    color: var(--text);
    -webkit-backdrop-filter: blur(2px);
    backdrop-filter: blur(2px);
    box-shadow: var(--header-control-shadow);
    cursor: pointer;
    transition:
      background 150ms ease,
      box-shadow 150ms ease,
      transform 150ms ease;
  }

  :global(.menu-button:hover),
  :global(.menu-button[data-state="open"]) {
    background: color-mix(in srgb, var(--panel-2) 76%, transparent);
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
  }

  :global(.menu-button:active) {
    transform: scale(0.96);
  }

  :global(.header-menu) {
    box-sizing: border-box;
    z-index: var(--layer-popover);
    width: 248px;
    padding: 6px;
    border: 0;
    border-radius: 16px;
    background: color-mix(in srgb, var(--panel) 65%, transparent);
    color: var(--text);
    -webkit-backdrop-filter: blur(14px);
    backdrop-filter: blur(14px);
    box-shadow:
      0 0 0 1px color-mix(in srgb, var(--text) 9%, transparent),
      0 12px 32px color-mix(in srgb, var(--text) 14%, transparent);
  }

  :global(.theme-menu-item) {
    display: grid;
    grid-template-columns: 20px minmax(0, 1fr) auto;
    align-items: center;
    gap: 8px;
    width: 100%;
    min-height: 40px;
    padding: 0 10px;
    border: 0;
    border-radius: 10px;
    background: transparent;
    color: var(--text);
    font: inherit;
    font-size: 0.84rem;
    text-align: left;
    cursor: pointer;
    transition:
      background 150ms ease,
      transform 150ms ease;
  }

  :global(.theme-menu-item:hover),
  :global(.theme-menu-item:focus-visible) {
    background: var(--surface-hover);
  }

  :global(.theme-menu-item:active) {
    transform: scale(0.96);
  }

  :global(.header-menu-separator) {
    height: 1px;
    margin: 5px 8px;
    background: var(--border);
  }

  :global(.header-user-summary) {
    display: flex;
    align-items: center;
    gap: 9px;
    min-height: 40px;
    padding: 0 10px;
    color: var(--text);
    font-size: 0.82rem;
    font-weight: 600;
  }

  :global(.header-user-avatar) {
    display: block;
    width: 20px;
    height: 20px;
    flex: 0 0 20px;
    border-radius: 50%;
    object-fit: cover;
  }

  :global(.header-user-placeholder) {
    flex: 0 0 auto;
    color: var(--text-muted);
    background: transparent;
  }

  .status-dot {
    width: 8px;
    height: 8px;
    border-radius: 999px;
    box-shadow: 0 0 0 3px color-mix(in srgb, currentColor 14%, transparent);
  }

  .connection-status.connected .status-dot {
    background: #16a34a;
    color: #16a34a;
  }

  .connection-status.connecting .status-dot {
    background: #f59e0b;
    color: #f59e0b;
  }

  .connection-status.disconnected .status-dot {
    background: #dc2626;
    color: #dc2626;
  }

  @media (max-width: 640px) {
    .app-header {
      margin: 10px 10px 0;
    }

    .new-session-button {
      width: 40px;
      min-width: 40px;
      padding: 0;
      border-radius: 50%;
    }

    .new-session-button span {
      display: none;
    }
  }
</style>
