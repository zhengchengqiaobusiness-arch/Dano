<script lang="ts">
  import type { Snippet } from "svelte";
  import type { HTMLAttributes } from "svelte/elements";

  let {
    class: className,
    children,
    ...restProps
  }: HTMLAttributes<HTMLTableSectionElement> & { children?: Snippet } = $props();
</script>

<thead class={["ui-table-header", className]} data-slot="table-header" {...restProps}>
  {@render children?.()}
</thead>
