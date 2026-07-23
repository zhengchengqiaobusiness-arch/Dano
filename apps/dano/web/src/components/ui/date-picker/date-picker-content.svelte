<script lang="ts">
  import { DatePicker as DatePickerPrimitive } from "bits-ui";

  let {
    ref = $bindable(null),
    class: className,
    children,
    ...restProps
  }: DatePickerPrimitive.ContentProps = $props();
</script>

<DatePickerPrimitive.Portal to=".app-shell">
  <DatePickerPrimitive.Content
    bind:ref
    class={["date-picker-content", className]}
    {...restProps}
  >
    {@render children?.()}
  </DatePickerPrimitive.Content>
</DatePickerPrimitive.Portal>

<style>
  :global(.date-picker-content) {
    z-index: var(--layer-popover);
  }
</style>
