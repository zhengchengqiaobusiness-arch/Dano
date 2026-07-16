<script lang="ts">
  import { Tooltip as TooltipPrimitive } from "bits-ui";

  let { children, ...restProps }: TooltipPrimitive.RootProps = $props();
</script>

<TooltipPrimitive.Root {...restProps} {children} />
