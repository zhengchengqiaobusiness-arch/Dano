<script lang="ts">
  import * as Tooltip from "./ui/tooltip";

  let { value }: { value: string } = $props();
  let triggerElement = $state<HTMLElement>();
  let overflowing = $state(false);

  $effect(() => {
    const element = triggerElement;
    value;
    if (!element || typeof ResizeObserver === "undefined") return;

    const updateOverflow = () => {
      overflowing = element.scrollWidth > element.clientWidth;
    };
    const observer = new ResizeObserver(updateOverflow);
    observer.observe(element);
    updateOverflow();
    return () => observer.disconnect();
  });
</script>

<Tooltip.Provider delayDuration={300}>
  <Tooltip.Root disabled={!overflowing}>
    <Tooltip.Trigger tabindex={overflowing ? 0 : -1}>
      {#snippet child({ props })}
        <div {...props} bind:this={triggerElement} class="submitted-field-value" aria-label={value}>
          {value}
        </div>
      {/snippet}
    </Tooltip.Trigger>
    {#if overflowing}
      <Tooltip.Content>{value}</Tooltip.Content>
    {/if}
  </Tooltip.Root>
</Tooltip.Provider>

<style>
  .submitted-field-value {
    box-sizing: border-box;
    width: 100%;
    min-height: 40px;
    padding: 9px 12px;
    overflow: hidden;
    border: 1px solid var(--border);
    border-radius: 10px;
    background: var(--control-bg);
    color: var(--text);
    font-size: 0.9rem;
    line-height: 20px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .submitted-field-value:focus-visible {
    outline: 2px solid var(--focus-ring);
    outline-offset: 2px;
  }
</style>
