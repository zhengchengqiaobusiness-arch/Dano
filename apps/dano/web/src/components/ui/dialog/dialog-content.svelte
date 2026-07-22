<script lang="ts">
  import { Dialog as DialogPrimitive } from "bits-ui";

  let { children, ...restProps }: DialogPrimitive.ContentProps = $props();
</script>

<DialogPrimitive.Content {...restProps} {children} />
