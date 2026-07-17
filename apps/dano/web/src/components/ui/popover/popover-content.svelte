<script lang="ts">
  import { Popover as PopoverPrimitive } from "bits-ui";

  let {
    ref = $bindable(null),
    sideOffset = 4,
    collisionPadding = 12,
    class: className,
    children,
    ...restProps
  }: PopoverPrimitive.ContentProps = $props();
</script>

<PopoverPrimitive.Portal to=".app-shell">
  <PopoverPrimitive.Content
    bind:ref
    {sideOffset}
    {collisionPadding}
    class={className}
    {...restProps}
  >
    {@render children?.()}
  </PopoverPrimitive.Content>
</PopoverPrimitive.Portal>
