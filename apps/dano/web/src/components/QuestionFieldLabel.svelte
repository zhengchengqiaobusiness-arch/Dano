<script lang="ts">
  import { tick } from "svelte";
  import Calendar from "lucide-svelte/icons/calendar";
  import CircleCheck from "lucide-svelte/icons/circle-check";
  import ListChecks from "lucide-svelte/icons/list-checks";
  import MessageSquareText from "lucide-svelte/icons/message-square-text";
  import type { AskUserQuestionItem } from "../utils/askUserQuestion";
  import MarkdownRenderer from "./MarkdownRenderer.svelte";
  import * as Tooltip from "./ui/tooltip";

  let {
    kind,
    label,
  }: {
    kind: AskUserQuestionItem["kind"];
    label: string;
  } = $props();

  let textElement = $state<HTMLElement>();
  let overflowing = $state(false);
  let tooltipText = $state("");

  $effect(() => {
    const element = textElement;
    label;
    if (!element || typeof ResizeObserver === "undefined") return;

    let disposed = false;
    const updateOverflow = () => {
      if (disposed) return;
      for (const link of element.querySelectorAll("a")) link.tabIndex = -1;
      tooltipText = (element.textContent ?? "").replace(/\s+/g, " ").trim();
      overflowing = element.scrollWidth > element.clientWidth;
    };
    const resizeObserver = new ResizeObserver(updateOverflow);
    const mutationObserver = typeof MutationObserver === "undefined"
      ? undefined
      : new MutationObserver(updateOverflow);
    resizeObserver.observe(element);
    mutationObserver?.observe(element, { childList: true, characterData: true, subtree: true });
    void tick().then(updateOverflow);
    return () => {
      disposed = true;
      resizeObserver.disconnect();
      mutationObserver?.disconnect();
    };
  });
</script>

<Tooltip.Provider delayDuration={300}>
  <Tooltip.Root disabled={!overflowing} ignoreNonKeyboardFocus={false}>
    <Tooltip.Trigger tabindex={overflowing ? 0 : -1}>
      {#snippet child({ props })}
        <span
          {...props}
          class="question-field-label"
          aria-label={overflowing ? tooltipText : undefined}
        >
          <span
            class="question-field-icon"
            data-question-kind={kind}
            aria-hidden="true"
          >
            {#if kind === "date"}
              <Calendar size={18} />
            {:else if kind === "single" || kind === "multiple" || kind === "select" || kind === "treeSelect"}
              <ListChecks size={18} />
            {:else if kind === "confirm"}
              <CircleCheck size={18} />
            {:else}
              <MessageSquareText size={18} />
            {/if}
          </span>
          <span bind:this={textElement} class="question-field-label-content">
            <MarkdownRenderer content={label} interactiveRoot={false} />
          </span>
        </span>
      {/snippet}
    </Tooltip.Trigger>
    {#if overflowing}
      <Tooltip.Content>{tooltipText}</Tooltip.Content>
    {/if}
  </Tooltip.Root>
</Tooltip.Provider>

<style>
  .question-field-label {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
    width: 100%;
  }

  .question-field-icon {
    display: inline-flex;
    flex: 0 0 18px;
    align-items: center;
    justify-content: center;
    width: 18px;
    height: 18px;
    color: var(--accent);
  }

  .question-field-label-content {
    flex: 1 1 auto;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .question-field-label-content :global(.markdown-renderer),
  .question-field-label-content :global(.markdown-body),
  .question-field-label-content :global(.markdown-body > *) {
    display: inline;
    margin: 0;
    color: inherit;
    font-size: inherit;
    line-height: inherit;
    white-space: nowrap;
  }
</style>
