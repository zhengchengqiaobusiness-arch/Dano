<script lang="ts">
  import * as Tooltip from "./ui/tooltip";
  import "./questionToolControls.css";

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
        <div {...props} bind:this={triggerElement} class="question-input submitted-field-value" aria-label={value}>
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
    display: flex;
    align-items: center;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
</style>
