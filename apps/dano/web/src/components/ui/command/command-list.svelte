<script lang="ts">
  import { Command as CommandPrimitive } from "bits-ui";

  let { class: className, children, ...restProps }: CommandPrimitive.ListProps = $props();
</script>

<CommandPrimitive.List class={className} {...restProps} {children} />
