<script lang="ts">
  import BookOpenText from "lucide-svelte/icons/book-open-text";
  import ChevronRight from "lucide-svelte/icons/chevron-right";
  import FilePenLine from "lucide-svelte/icons/file-pen-line";
  import ListChecks from "lucide-svelte/icons/list-checks";
  import Search from "lucide-svelte/icons/search";
  import Sparkle from "lucide-svelte/icons/sparkle";
  import SquareTerminal from "lucide-svelte/icons/square-terminal";
  import WandSparkles from "lucide-svelte/icons/wand-sparkles";
  import { slide } from "svelte/transition";
  import { t } from "../i18n";
  import type { ToolActivity } from "../utils/toolPresentation";

  let {
    activity,
    expanded = false,
    active = false,
    treeEntryId,
    onToggle = () => {},
    onOpenImage = (_: number) => {},
  }: {
    activity: ToolActivity;
    expanded?: boolean;
    active?: boolean;
    treeEntryId?: string;
    onToggle?: () => void;
    onOpenImage?: (index: number) => void;
  } = $props();

  let expandable = $derived(
    activity.details.length > 0 ||
    activity.rawDetails.length > 0 ||
    activity.overflowCount > 0 ||
    activity.images.length > 0,
  );
</script>

{#snippet activityIcon()}
  {#if activity.kind === "read"}
    <BookOpenText size={15} strokeWidth={1.8} aria-hidden="true" />
  {:else if activity.kind === "update"}
    <FilePenLine size={15} strokeWidth={1.8} aria-hidden="true" />
  {:else if activity.kind === "external"}
    <Search size={15} strokeWidth={1.8} aria-hidden="true" />
  {:else if activity.kind === "process"}
    <SquareTerminal size={15} strokeWidth={1.8} aria-hidden="true" />
  {:else if activity.kind === "question"}
    <ListChecks size={15} strokeWidth={1.8} aria-hidden="true" />
  {:else if activity.kind === "skill"}
    <WandSparkles size={15} strokeWidth={1.8} aria-hidden="true" />
  {:else}
    <Sparkle size={15} strokeWidth={1.8} aria-hidden="true" />
  {/if}
{/snippet}

<div
  class="tool-activity"
  class:active
  class:failed={activity.status === "error"}
  data-status={activity.status}
  data-tree-entry-id={treeEntryId}
>
  {#if expandable}
    <button
      type="button"
      class="tool-activity-trigger"
      tabindex="-1"
      aria-expanded={expanded}
      onclick={onToggle}
    >
      <span class="tool-activity-icon">{@render activityIcon()}</span>
      <span class="tool-activity-label">{activity.label}</span>
      <span class="tool-activity-chevron" class:expanded aria-hidden="true">
        <ChevronRight size={14} strokeWidth={1.8} />
      </span>
    </button>
  {:else}
    <div class="tool-activity-trigger static-row">
      <span class="tool-activity-icon">{@render activityIcon()}</span>
      <span class="tool-activity-label">{activity.label}</span>
    </div>
  {/if}

  {#if expanded && expandable}
    <div class="tool-activity-details" transition:slide={{ duration: 160 }}>
      {#each activity.details as detail}
        <div>{detail}</div>
      {/each}
      {#each activity.rawDetails as detail}
        <pre class="tool-activity-raw-detail">{detail}</pre>
      {/each}
      {#if activity.overflowCount > 0}
        <div class="tool-activity-overflow">
          {t("chatTranscript.activity.moreItems", { count: activity.overflowCount })}
        </div>
      {/if}
      {#if activity.images.length > 0}
        <div class="tool-activity-images">
          {#each activity.images as image, imageIndex (`${image.src}-${imageIndex}`)}
            <button
              type="button"
              class="tool-activity-image-button"
              aria-label={t("chatTranscript.openImageNumber", { number: imageIndex + 1 })}
              onclick={() => onOpenImage(imageIndex)}
            >
              <img src={image.src} alt={image.alt} loading="lazy" />
            </button>
          {/each}
        </div>
      {/if}
    </div>
  {/if}
</div>

<style>
  .tool-activity {
    width: min(100%, 760px);
    margin-left: -8px;
    color: var(--text-muted);
  }

  .tool-activity-trigger {
    display: flex;
    align-items: center;
    width: fit-content;
    max-width: 100%;
    min-width: 0;
    min-height: 36px;
    margin: 0;
    padding: 5px 8px;
    border: 0;
    border-radius: 9px;
    background: transparent;
    color: inherit;
    font: inherit;
    font-size: 0.82rem;
    line-height: 1.45;
    text-align: left;
    cursor: pointer;
    transition-property: color;
    transition-duration: 150ms;
    transition-timing-function: ease-out;
  }

  .tool-activity-trigger:focus {
    outline: none;
  }

  .tool-activity-trigger.static-row {
    cursor: default;
  }

  button.tool-activity-trigger:hover {
    color: var(--text);
  }

  .tool-activity-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex: 0 0 auto;
    width: 22px;
    height: 22px;
    margin-right: 7px;
    border-radius: 7px;
    background: color-mix(in srgb, var(--panel-2) 70%, transparent);
    color: var(--text-subtle);
  }

  .tool-activity-label {
    min-width: 0;
    flex: 0 1 auto;
    font-variant-numeric: tabular-nums;
    text-wrap: pretty;
  }

  .tool-activity-chevron {
    display: inline-flex;
    flex: 0 0 auto;
    margin-left: 8px;
    opacity: 0;
    scale: 0.25;
    filter: blur(4px);
    color: var(--text-subtle);
    transition-property: opacity, scale, filter, rotate;
    transition-duration: 180ms;
    transition-timing-function: cubic-bezier(0.2, 0, 0, 1);
  }

  .tool-activity-trigger:hover .tool-activity-chevron {
    opacity: 1;
    scale: 1;
    filter: blur(0);
  }

  .tool-activity-chevron.expanded {
    opacity: 1;
    scale: 1;
    filter: blur(0);
    rotate: 90deg;
  }

  .tool-activity-details {
    display: flex;
    flex-direction: column;
    gap: 3px;
    margin: 1px 10px 8px 37px;
    padding: 7px 10px;
    border-radius: 9px;
    background: color-mix(in srgb, var(--panel-2) 58%, transparent);
    color: var(--text-subtle);
    font-size: 0.75rem;
    line-height: 1.55;
    text-wrap: pretty;
  }

  .tool-activity-overflow {
    color: var(--text-muted);
  }

  .tool-activity-raw-detail {
    margin: 0;
    overflow-wrap: anywhere;
    white-space: pre-wrap;
    font: inherit;
  }

  .tool-activity-images {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 5px;
  }

  .tool-activity-image-button {
    display: block;
    max-width: min(100%, 320px);
    padding: 0;
    border: 0;
    border-radius: 10px;
    overflow: hidden;
    background: transparent;
    cursor: pointer;
  }

  .tool-activity-image-button img {
    display: block;
    max-width: 100%;
    max-height: 240px;
    border-radius: 10px;
    outline: 1px solid rgba(0, 0, 0, 0.1);
    outline-offset: -1px;
  }

  :global(.app-shell[data-theme-mode="dark"]) .tool-activity-image-button img {
    outline-color: rgba(255, 255, 255, 0.1);
  }

  .tool-activity.active {
    color: var(--text);
  }

  .tool-activity.active .tool-activity-icon {
    color: var(--accent, var(--text));
    animation: tool-activity-breathe 1.8s ease-in-out infinite;
  }

  .tool-activity.failed {
    color: var(--text-subtle);
    opacity: 0.55;
  }

  @keyframes tool-activity-breathe {
    0%, 100% { opacity: 0.45; transform: scale(0.92); }
    50% { opacity: 1; transform: scale(1); }
  }

  @media (hover: none) {
    .tool-activity-chevron { display: none; }
  }

  @media (prefers-reduced-motion: reduce) {
    .tool-activity.active .tool-activity-icon { animation: none; }
  }
</style>
