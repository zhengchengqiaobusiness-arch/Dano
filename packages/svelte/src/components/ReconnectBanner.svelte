<script lang="ts">
  let {
    visible = false,
    reason = "",
    reconnectCount = 0,
  }: {
    visible?: boolean;
    reason?: string;
    reconnectCount?: number;
  } = $props();
</script>

{#if visible}
  <div class="reconnect-banner" role="alert" aria-live="polite">
    <span class="pulse-dot"></span>
    <span class="banner-text"
      >{reason || "Connection lost"}. Reconnecting...</span
    >
    {#if reconnectCount > 1}
      <span class="attempt-badge">Attempt {reconnectCount}</span>
    {/if}
  </div>
{/if}

<style>
  .reconnect-banner {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 6px 16px;
    background: var(--panel);
    border-bottom: 1px solid var(--border);
    color: var(--text-muted);
    font-size: 0.72rem;
    flex-shrink: 0;
    z-index: 19;
  }

  .pulse-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--text-muted);
    flex-shrink: 0;
    animation: pulse-glow 1.5s ease-in-out infinite;
  }

  .banner-text {
    flex: 1;
  }

  .attempt-badge {
    height: 22px;
    padding: 0 8px;
    border-radius: 999px;
    border: 1px solid var(--border-strong);
    background: var(--panel-2);
    color: var(--text-subtle);
    font-size: 0.66rem;
    line-height: 22px;
    white-space: nowrap;
  }

  @keyframes pulse-glow {
    0%,
    100% {
      opacity: 0.4;
      transform: scale(0.8);
    }
    50% {
      opacity: 1;
      transform: scale(1.05);
    }
  }
</style>
