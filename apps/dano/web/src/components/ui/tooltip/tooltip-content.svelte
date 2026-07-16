<script lang="ts">
  import { Tooltip as TooltipPrimitive } from "bits-ui";

  let {
    ref = $bindable(null),
    sideOffset = 8,
    collisionPadding = 12,
    class: className,
    children,
    ...restProps
  }: TooltipPrimitive.ContentProps = $props();
</script>

<TooltipPrimitive.Portal to=".app-shell">
  <TooltipPrimitive.Content
    bind:ref
    {sideOffset}
    {collisionPadding}
    class={["tooltip-content", className]}
    {...restProps}
  >
    {@render children?.()}
  </TooltipPrimitive.Content>
</TooltipPrimitive.Portal>

<style>
  :global(.tooltip-content) {
    z-index: 1000;
    max-width: min(420px, calc(100vw - 32px));
    padding: 8px 10px;
    border-radius: 8px;
    background: color-mix(in srgb, var(--panel-3) 88%, var(--accent));
    color: var(--text);
    font-size: 0.78rem;
    line-height: 1.45;
    overflow-wrap: anywhere;
    box-shadow: var(--shadow-raised);
  }
</style>
