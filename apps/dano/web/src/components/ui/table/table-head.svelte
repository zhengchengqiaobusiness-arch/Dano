<script lang="ts">
  import type { Snippet } from "svelte";
  import type { HTMLThAttributes } from "svelte/elements";

  let {
    class: className,
    children,
    ...restProps
  }: HTMLThAttributes & { children?: Snippet } = $props();
</script>

<th class={["ui-table-head", className]} data-slot="table-head" {...restProps}>
  {@render children?.()}
</th>
