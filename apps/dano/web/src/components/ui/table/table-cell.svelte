<script lang="ts">
  import type { Snippet } from "svelte";
  import type { HTMLTdAttributes } from "svelte/elements";

  let {
    class: className,
    children,
    ...restProps
  }: HTMLTdAttributes & { children?: Snippet } = $props();
</script>

<td class={["ui-table-cell", className]} data-slot="table-cell" {...restProps}>
  {@render children?.()}
</td>
