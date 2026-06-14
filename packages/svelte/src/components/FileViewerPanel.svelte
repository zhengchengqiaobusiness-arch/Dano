<script lang="ts">
  import type { RpcWorkspaceFile } from "@pi-web/bridge/types";
  import { onMount } from "svelte";
  import { highlightCodeLinesHtml } from "../utils/codeHighlight";

  let {
    filePath = "",
    lineNumber = 1,
    readWorkspaceFile = (_: string) =>
      Promise.resolve({} as RpcWorkspaceFile),
  }: {
    filePath: string;
    lineNumber: number;
    readWorkspaceFile: (path: string) => Promise<RpcWorkspaceFile>;
  } = $props();

  let container = $state<HTMLDivElement | null>(null);
  let file = $state<RpcWorkspaceFile | null>(null);
  let renderedHtml = $state("");
  let loading = $state(false);
  let errorMessage = $state("");
  let loadVersion = 0;
  let renderVersion = 0;
  let themeObserver: MutationObserver | undefined;

  let activeLineNumber = $derived(
    Number.isInteger(lineNumber) && lineNumber > 0 ? lineNumber : 1,
  );

  async function loadFile() {
    const version = ++loadVersion;
    loading = true;
    errorMessage = "";
    file = null;
    renderedHtml = "";

    try {
      const nextFile = await readWorkspaceFile(filePath);
      if (version !== loadVersion) return;
      file = nextFile;
    } catch (error) {
      if (version !== loadVersion) return;
      file = null;
      errorMessage =
        error instanceof Error ? error.message : "Failed to load file preview";
      renderedHtml = "";
    } finally {
      if (version === loadVersion) loading = false;
    }
  }

  async function scrollToActiveLine() {
    await tick();

    const root = container;
    if (!root) return;

    const target = root.querySelector<HTMLElement>(
      `[data-line="${activeLineNumber}"]`,
    );
    if (!target) return;

    target.scrollIntoView({ block: "center" });
  }

  async function renderCode() {
    const version = ++renderVersion;
    if (!file) {
      renderedHtml = "";
      return;
    }

    const html = await highlightCodeLinesHtml(
      file.content,
      file.path,
      undefined,
      activeLineNumber,
    );
    if (version !== renderVersion) return;

    renderedHtml = html;
    await scrollToActiveLine();
  }

  function tick(): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, 0));
  }

  onMount(() => {
    const shell = document.querySelector(".app-shell");
    if (!shell) return;

    themeObserver = new MutationObserver(() => {
      void renderCode();
    });
    themeObserver.observe(shell, {
      attributes: true,
      attributeFilter: ["data-dark-theme", "data-light-theme"],
    });

    return () => {
      loadVersion += 1;
      renderVersion += 1;
      themeObserver?.disconnect();
    };
  });

  $effect(() => {
    void loadFile();
  });

  $effect(() => {
    void [file?.content, file?.path, activeLineNumber];
    void renderCode();
  });
</script>

<section class="file-viewer-panel">
  {#if errorMessage}
    <div class="file-viewer-state error">
      {errorMessage}
    </div>
  {:else if loading && !file}
    <div class="file-viewer-state">
      Loading file...
    </div>
  {:else}
    {#if file?.truncated}
      <div class="file-viewer-notice">
        Showing the first {file.lineCount} lines. The full file is
        {file.totalBytes} bytes.
      </div>
    {/if}
    <div bind:this={container} class="file-viewer-code-shell">
      {#if renderedHtml}
        <div class="file-viewer-code">{@html renderedHtml}</div>
      {:else}
        <div class="file-viewer-empty">This file is empty.</div>
      {/if}
    </div>
  {/if}
</section>

<style>
  .file-viewer-panel {
    --file-viewer-code-bg: var(--bg);

    display: flex;
    flex-direction: column;
    min-width: 0;
    min-height: 0;
    height: 100%;
    background: var(--rail-bg);
  }

  .file-viewer-state,
  .file-viewer-notice,
  .file-viewer-empty {
    margin: 10px 14px 0;
    padding: 10px 12px;
    border-radius: 12px;
    font-size: 0.74rem;
    line-height: 1.5;
  }

  .file-viewer-state,
  .file-viewer-empty {
    border: 1px solid color-mix(in srgb, var(--border) 82%, transparent);
    background: color-mix(in srgb, var(--panel) 84%, transparent);
    color: var(--text-muted);
  }

  .file-viewer-state.error {
    border-color: color-mix(in srgb, var(--danger) 38%, var(--border));
    background: color-mix(in srgb, var(--error-bg) 72%, transparent);
    color: var(--error-text);
  }

  .file-viewer-notice {
    border: 1px solid color-mix(in srgb, var(--warning) 32%, var(--border));
    background: color-mix(in srgb, var(--panel) 82%, transparent);
    color: var(--text-muted);
  }

  .file-viewer-code-shell {
    flex: 1;
    min-height: 0;
    overflow: auto;
    border-top: 1px solid var(--border);
    background: var(--file-viewer-code-bg);
    scrollbar-width: none;
  }

  .file-viewer-code-shell::-webkit-scrollbar {
    display: none;
  }

  .file-viewer-code {
    min-width: max-content;
    min-height: 100%;
    padding-bottom: 4px;
    background: var(--file-viewer-code-bg);
  }

  .file-viewer-code :global(pre) {
    min-height: 100%;
    margin: 0;
    padding: 2px 0 6px;
    overflow: visible;
    background: transparent !important;
  }

  .file-viewer-code :global(code) {
    display: block;
    min-width: max-content;
    min-height: 100%;
    font-family: var(--pi-font-mono);
    font-size: 0.72rem;
    line-height: 1.35;
    white-space: normal;
  }

  .file-viewer-code :global(.code-line) {
    display: block;
    position: relative;
    padding: 0 14px 0 62px;
    white-space: pre;
    line-height: 1.35;
    background: transparent;
  }

  .file-viewer-code :global(.code-line:empty)::after {
    content: " ";
    visibility: hidden;
  }

  .file-viewer-code :global(.code-line)::before {
    content: attr(data-line);
    position: absolute;
    top: 0;
    left: 0;
    width: 50px;
    padding-right: 12px;
    border-right: 1px solid var(--border);
    color: var(--text-subtle);
    text-align: right;
    line-height: 1.35;
    user-select: none;
  }

  .file-viewer-code :global(.code-line-target) {
    background: var(--surface-active);
  }

  .file-viewer-code :global(.code-line-target)::before {
    color: var(--accent-hover);
    background: var(--surface-active);
  }

  @media (max-width: 900px) {
    .file-viewer-state,
    .file-viewer-notice,
    .file-viewer-empty {
      margin-inline: 12px;
    }
  }
</style>
