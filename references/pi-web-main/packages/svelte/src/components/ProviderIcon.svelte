<script lang="ts">
  import { getProviderSvg } from "../utils/providerIcons";

  let { provider, size = 14 }: { provider: string; size?: number } = $props();

  let svgContent = $derived(getProviderSvg(provider));
</script>

{#if svgContent}
  <span
    class="provider-icon"
    style="width: {size}px; height: {size}px;"
    aria-label={provider}
  >
    {@html svgContent}
  </span>
{/if}

<style>
  .provider-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    color: inherit;
  }

  .provider-icon :global(svg) {
    width: 100%;
    height: 100%;
    display: block;
  }
</style>
