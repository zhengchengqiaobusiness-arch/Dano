<script lang="ts">
  import { Command as CommandPrimitive } from "bits-ui";

  let { children, ...restProps }: CommandPrimitive.RootProps = $props();
</script>

<CommandPrimitive.Root {...restProps} {children} />
