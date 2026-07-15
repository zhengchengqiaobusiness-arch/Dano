<script lang="ts">
  import SquarePen from "lucide-svelte/icons/square-pen";
  import type { ConnectionStatus } from "../composables/bridgeStore.svelte";
  import { t } from "../i18n";

  let {
    connectionStatus,
    disconnectReason = "",
    onReconnect,
    onNewSession,
    newSessionPending = false,
  }: {
    connectionStatus: ConnectionStatus;
    disconnectReason?: string;
    onReconnect?: () => void;
    onNewSession?: () => void;
    newSessionPending?: boolean;
  } = $props();

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
</script>

<header class="app-header">
  <div class="header-leading">
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
  <button
    class="new-session-button"
    type="button"
    aria-label={t("appHeader.newSession")}
    title={t("appHeader.newSession")}
    disabled={newSessionPending}
    onclick={() => onNewSession?.()}
  >
    <SquarePen size={18} aria-hidden="true" />
    <span>{t("appHeader.newSession")}</span>
  </button>
</header>

<style>
  .app-header {
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
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    height: 40px;
    padding: 0 14px;
    border: 1px solid #e4e5e2;
    border-radius: 999px;
    background: #fff;
    color: #5f6368;
    font: inherit;
    font-size: 0.82rem;
    font-weight: 600;
    line-height: 1;
    cursor: pointer;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
  }

  .new-session-button:hover:not(:disabled) {
    background: #f7f7f5;
  }

  .new-session-button:focus-visible {
    outline: 2px solid #5f6368;
    outline-offset: 2px;
  }

  .new-session-button:disabled {
    cursor: wait;
    opacity: 0.6;
  }

  .header-leading {
    display: flex;
    align-items: center;
    gap: 14px;
    height: 100%;
    min-width: 0;
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
    color: var(--text-muted);
    font-size: 0.78rem;
    font-weight: 650;
    line-height: 1;
    background: var(--panel);
    box-shadow:
      rgba(0, 0, 0, 0) 0px 0px 0px 0px,
      rgba(0, 0, 0, 0) 0px 0px 0px 0px,
      rgba(0, 0, 0, 0) 0px 0px 0px 0px,
      rgba(0, 0, 0, 0) 0px 0px 0px 0px,
      rgba(0, 0, 0, 0.04) 0px 0px 0px 1px,
      rgba(0, 0, 0, 0.04) 0px 2px 8px 0px;
    cursor: pointer;
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

    .header-leading {
      gap: 10px;
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
