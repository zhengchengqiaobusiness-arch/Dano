<script lang="ts">
  import X from "lucide-svelte/icons/x";

  let { visible = false }: { visible?: boolean } = $props();
  let dismissed = $state(false);
</script>

{#if visible && !dismissed}
  <div class="compat-warning" role="alert">
    <span class="compat-kicker">compat</span>
    <span class="compat-text">
      This extension uses a custom TUI interface that is not supported in the
      browser. Use the terminal for full functionality.
    </span>
    <button
      class="compat-dismiss"
      aria-label="Dismiss warning"
      onclick={() => (dismissed = true)}
    >
      <X size={14} aria-hidden="true" />
    </button>
  </div>
{/if}

<style>
  .compat-warning {
    display: flex;
    align-items: center;
    gap: 10px;
    margin: 12px 24px 0;
    padding: 10px 12px;
    border-radius: 12px;
    border: 1px solid var(--border);
    background: var(--panel);
    color: var(--text-muted);
    font-size: 0.8rem;
    line-height: 1.5;
    flex-shrink: 0;
  }

  .compat-kicker {
    flex-shrink: 0;
    font-size: 0.66rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-subtle);
  }

  .compat-text {
    flex: 1;
  }

  .compat-dismiss {
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

  .compat-dismiss:hover {
    color: var(--text);
  }

  @media (max-width: 900px) {
    .compat-warning {
      margin: 12px 16px 0;
    }
  }
</style>
