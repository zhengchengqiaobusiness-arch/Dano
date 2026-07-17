<script lang="ts">
  import { Command as CommandPrimitive } from "bits-ui";

  let {
    ref = $bindable(null),
    class: className,
    ...restProps
  }: CommandPrimitive.InputProps = $props();
</script>

<CommandPrimitive.Input bind:ref class={className} {...restProps} />
