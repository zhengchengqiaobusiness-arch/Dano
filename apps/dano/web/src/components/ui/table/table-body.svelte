<script lang="ts">
  import type { Snippet } from "svelte";
  import type { HTMLAttributes } from "svelte/elements";

  let {
    class: className,
    children,
    ...restProps
  }: HTMLAttributes<HTMLTableSectionElement> & { children?: Snippet } = $props();
</script>

<tbody class={["ui-table-body", className]} data-slot="table-body" {...restProps}>
  {@render children?.()}
</tbody>
