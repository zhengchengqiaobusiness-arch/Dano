<script lang="ts">
  import { Command as CommandPrimitive } from "bits-ui";

  let { class: className, children, ...restProps }: CommandPrimitive.ItemProps = $props();
</script>

<CommandPrimitive.Item class={className} {...restProps} {children} />
