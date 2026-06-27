<script lang="ts">
  import type { ConnectionStatus } from "../composables/bridgeStore.svelte";
  import { t } from "../i18n";

  let {
    connectionStatus,
  }: {
    connectionStatus: ConnectionStatus;
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
</script>

<header class="app-header">
  <div class="header-leading">
    <div class={`connection-status ${statusMeta.className}`}>
      <span class="status-dot" aria-hidden="true"></span>
      <span>{statusMeta.label}</span>
    </div>
  </div>
</header>

<style>
  .app-header {
    position: fixed;
    top: 0;
    left: 0;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    height: auto;
    margin: 10px 0 0 10px;
    border-radius: 999px;
    flex-shrink: 0;
    z-index: 20;
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
    border: 1px solid var(--border);
    border-radius: 999px;
    color: var(--text-muted);
    font-size: 0.78rem;
    font-weight: 650;
    line-height: 1;
    background: var(--surface);
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
      margin: 10px 0 0 10px;
    }

    .header-leading {
      gap: 10px;
    }
  }
</style>
