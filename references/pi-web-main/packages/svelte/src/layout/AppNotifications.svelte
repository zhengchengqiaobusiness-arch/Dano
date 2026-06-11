<script lang="ts">
  import X from "lucide-svelte/icons/x";

  let {
    connectionError = "",
    notifications = [] as ReadonlyArray<{
      message: string;
      notifyType?: string;
      id: string;
    }>,
    onDismiss = (_: string) => {},
  } = $props();
</script>

{#if connectionError || notifications.length > 0}
  <div class="toast-container">
    {#if connectionError}
      <div class="toast-item error" role="alert">
        <div class="toast-copy">
          <span class="toast-type">error</span>
          <span class="toast-message">{connectionError}</span>
        </div>
      </div>
    {/if}
    {#each notifications as notif (notif.id)}
      <div
        class="toast-item"
        class:info={notif.notifyType === "info"}
        class:error={notif.notifyType === "error"}
        class:warn={notif.notifyType === "warn"}
      >
        <div class="toast-copy">
          <span class="toast-type">{notif.notifyType ?? "info"}</span>
          <span class="toast-message">{notif.message}</span>
        </div>
        <button
          class="toast-dismiss"
          aria-label="Dismiss notification"
          onclick={() => onDismiss(notif.id)}
        >
          <X size={14} aria-hidden="true" />
        </button>
      </div>
    {/each}
  </div>
{/if}

<style>
  .toast-container {
    position: fixed;
    top: 56px;
    right: 16px;
    z-index: 900;
    display: flex;
    flex-direction: column;
    gap: 8px;
    max-width: 340px;
  }

  .toast-item {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 12px 14px;
    border-radius: 12px;
    background: var(--panel);
    border: 1px solid var(--border-strong);
    box-shadow: var(--shadow);
    animation: toast-in 0.16s ease;
  }

  .toast-item.error {
    background: var(--error-bg);
    border-color: var(--error-border);
  }

  .toast-item.error .toast-type,
  .toast-item.error .toast-message {
    color: var(--error-text);
  }

  .toast-copy {
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 0;
    flex: 1;
  }

  .toast-type {
    font-size: 0.66rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-subtle);
  }

  .toast-message {
    font-size: 0.82rem;
    line-height: 1.45;
    color: var(--text-muted);
  }

  .toast-dismiss {
    flex-shrink: 0;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    background: none;
    border: none;
    color: var(--text-subtle);
    cursor: pointer;
    padding: 0;
    line-height: 1;
  }

  .toast-dismiss:hover {
    color: var(--text);
  }

  @keyframes toast-in {
    from {
      opacity: 0;
      transform: translateY(-4px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }

  @media (max-width: 900px) {
    .toast-container {
      left: 16px;
      right: 16px;
      max-width: none;
    }
  }
</style>
