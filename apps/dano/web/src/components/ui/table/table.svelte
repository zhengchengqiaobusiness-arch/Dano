<script lang="ts">
  import type { Snippet } from "svelte";
  import type { HTMLTableAttributes } from "svelte/elements";

  let {
    class: className,
    children,
    ...restProps
  }: HTMLTableAttributes & { children?: Snippet } = $props();
</script>

<div class="ui-table-scroll" data-slot="table-container">
  <table class={["ui-table", className]} data-slot="table" {...restProps}>
    {@render children?.()}
  </table>
</div>

<style>
  .ui-table-scroll {
    max-width: 100%;
    margin: 0.6em 0;
    overflow-x: auto;
    border-radius: 8px;
    overscroll-behavior-inline: contain;
    scrollbar-width: thin;
  }

  .ui-table {
    width: 100%;
    border-collapse: separate;
    border-spacing: 0;
    color: var(--text);
    font-size: 0.85em;
    line-height: 1.5;
  }

  .ui-table :global(.ui-table-head),
  .ui-table :global(.ui-table-cell) {
    min-width: 8rem;
    padding: 10px 12px;
    border-right: 1px solid var(--border);
    border-bottom: 1px solid var(--border);
    text-align: left;
    vertical-align: top;
    white-space: normal;
    overflow-wrap: anywhere;
  }

  .ui-table :global(.ui-table-head:first-child),
  .ui-table :global(.ui-table-cell:first-child) {
    border-left: 1px solid var(--border);
  }

  .ui-table :global(.ui-table-header .ui-table-head) {
    border-top: 1px solid var(--border);
    background: color-mix(in srgb, var(--accent) 10%, var(--panel));
    color: var(--text);
    font-weight: 600;
  }

  .ui-table :global(.ui-table-header:first-child .ui-table-row:first-child .ui-table-head:first-child) {
    border-top-left-radius: 8px;
  }

  .ui-table :global(.ui-table-header:first-child .ui-table-row:first-child .ui-table-head:last-child) {
    border-top-right-radius: 8px;
  }

  .ui-table :global(.ui-table-body:last-child .ui-table-row:last-child .ui-table-cell:first-child) {
    border-bottom-left-radius: 8px;
  }

  .ui-table :global(.ui-table-body:last-child .ui-table-row:last-child .ui-table-cell:last-child) {
    border-bottom-right-radius: 8px;
  }

  .ui-table :global(.ui-table-body .ui-table-row) {
    transition-property: background-color;
    transition-duration: 120ms;
    transition-timing-function: ease-out;
  }

  .ui-table :global(.ui-table-body .ui-table-row:hover) {
    background: color-mix(in srgb, var(--panel-2) 72%, transparent);
  }
</style>
