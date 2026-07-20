<script lang="ts">
  import type { Snippet } from "svelte";
  import type { HTMLAttributes } from "svelte/elements";

  let {
    class: className,
    children,
    ...restProps
  }: HTMLAttributes<HTMLTableRowElement> & { children?: Snippet } = $props();
</script>

<tr class={["ui-table-row", className]} data-slot="table-row" {...restProps}>
  {@render children?.()}
</tr>
