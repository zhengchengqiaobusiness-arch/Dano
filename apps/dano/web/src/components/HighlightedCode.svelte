<script lang="ts">
  import { onMount } from "svelte";
  import { highlightCodeHtml } from "../utils/codeHighlight";

  let {
    code = "",
    path,
  }: {
    code: string;
    path?: string;
  } = $props();

  let renderedHtml = $state("");
  let renderVersion = 0;
  let observer: MutationObserver | undefined;

  async function renderCode() {
    const version = ++renderVersion;
    if (!code.trim()) {
      renderedHtml = "";
      return;
    }

    const html = await highlightCodeHtml(code, path);
    if (version !== renderVersion) return;
    renderedHtml = html;
  }

  $effect(() => {
    void [code, path];
    void renderCode();
  });

  onMount(() => {
    const shell = document.querySelector(".app-shell");
    if (shell) {
      observer = new MutationObserver(() => {
        void renderCode();
      });
      observer.observe(shell, {
        attributes: true,
        attributeFilter: ["data-dark-theme", "data-light-theme"],
      });
    }

    return () => {
      observer?.disconnect();
    };
  });
</script>

{#if renderedHtml}
  <div class="highlighted-code">{@html renderedHtml}</div>
{:else}
  <pre class="highlighted-code-fallback">{code}</pre>
{/if}

<style>
  .highlighted-code,
  .highlighted-code-fallback {
    margin: 0;
    font-family: var(--pi-font-mono);
    font-size: 0.72rem;
    line-height: 1.6;
    color: var(--text-muted);
  }

  .highlighted-code :global(pre) {
    margin: 0;
    padding: 0;
    background: transparent !important;
    overflow-x: auto;
    white-space: pre;
  }

  .highlighted-code :global(code) {
    font-family: inherit;
    font-size: inherit;
    line-height: inherit;
  }

  .highlighted-code-fallback {
    white-space: pre-wrap;
    word-break: break-word;
  }
</style>
