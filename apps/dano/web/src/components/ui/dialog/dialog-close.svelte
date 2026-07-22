<script lang="ts">
  import { Dialog as DialogPrimitive } from "bits-ui";

  let { children, ...restProps }: DialogPrimitive.CloseProps = $props();
</script>

<DialogPrimitive.Close {...restProps} {children} />
