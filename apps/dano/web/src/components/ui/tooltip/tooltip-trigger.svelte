<script lang="ts">
  import { Tooltip as TooltipPrimitive } from "bits-ui";

  let { children, child: childSnippet, ...restProps }: TooltipPrimitive.TriggerProps = $props();
</script>

<TooltipPrimitive.Trigger {...restProps} {children} child={childSnippet} />
