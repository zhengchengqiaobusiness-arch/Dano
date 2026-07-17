<script lang="ts">
  import { Popover as PopoverPrimitive } from "bits-ui";

  let { children, child: childSnippet, ...restProps }: PopoverPrimitive.TriggerProps = $props();
</script>

<PopoverPrimitive.Trigger {...restProps} {children} child={childSnippet} />
