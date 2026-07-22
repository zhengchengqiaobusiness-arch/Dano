<script lang="ts">
  import { Dialog as DialogPrimitive } from "bits-ui";

  let { children, ...restProps }: DialogPrimitive.TitleProps = $props();
</script>

<DialogPrimitive.Title {...restProps} {children} />
