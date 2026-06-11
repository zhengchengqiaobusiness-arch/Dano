<script lang="ts">
  import type { FileDiffMetadata } from "@pierre/diffs";
  import { onMount } from "svelte";
  import {
    readThemeModeFromDom,
    readThemePairFromDom,
    resolveShikiTheme,
  } from "../themes";

  type DiffEdit = { oldText: string; newText: string };
  type ReadWorkspaceFile = (path: string) => Promise<{ content: string }>;
  type DiffsModule = typeof import("@pierre/diffs");
  type FileDiffRenderer = InstanceType<DiffsModule["FileDiff"]>;

  const REGISTERED_DIFF_THEMES = new Set<string>();
  let diffsModulePromise: Promise<DiffsModule> | undefined;

  let {
    diff = "",
    path,
    edits = [],
    readWorkspaceFile,
  }: {
    diff?: string;
    path?: string;
    edits?: DiffEdit[];
    readWorkspaceFile?: ReadWorkspaceFile;
  } = $props();

  let host = $state<HTMLElement | null>(null);
  let hasRenderedDiff = $state(false);
  let renderError = $state("");
  let loading = $state(false);
  let currentFileContent = $state<string | null>(null);
  let currentFilePath = $state<string | null>(null);
  let diffRenderer: FileDiffRenderer | undefined;
  let themeObserver: MutationObserver | undefined;
  let currentFileRequestId = 0;
  let renderRequestId = 0;

  let normalizedDiff = $derived(diff.replace(/\r/g, "").trim());
  let syntheticPatch = $derived(
    synthesizePatchFromEdits(edits, currentFileContent),
  );
  let fallbackText = $derived(
    looksLikePatch(normalizedDiff) || looksLikeNumberedEditDiff(normalizedDiff)
      ? normalizedDiff
      : syntheticPatch || normalizedDiff,
  );
  let fallbackLines = $derived(
    fallbackText.split("\n").map(line => ({
      text: line,
      kind: classifyLine(line),
    })),
  );

  function themedUnsafeCss() {
    return `
      :host {
        --diffs-bg: var(--tool-output-bg);
        --diffs-fg: var(--text);
        --diffs-fg-number-override: var(--text-subtle);
        --diffs-fg-conflict-marker-override: var(--text-subtle);
        --diffs-bg-context-override: color-mix(in srgb, var(--tool-output-bg) 92%, var(--panel));
        --diffs-bg-separator-override: var(--diff-header-bg);
        --diffs-font-family: var(--pi-font-mono);
        --diffs-header-font-family: var(--pi-font-sans);
        --diffs-font-size: 0.72rem;
        --diffs-line-height: 1.65;
        --diffs-addition-color-override: var(--diff-added-accent);
        --diffs-deletion-color-override: var(--diff-removed-accent);
        --diffs-modified-color-override: var(--accent);
        --diffs-bg-addition-override: color-mix(in srgb, var(--diff-added-bg) 78%, var(--tool-output-bg));
        --diffs-bg-deletion-override: color-mix(in srgb, var(--diff-removed-bg) 78%, var(--tool-output-bg));
        --diffs-bg-addition-emphasis-override: color-mix(in srgb, var(--diff-added-bg) 92%, var(--diff-added-accent));
        --diffs-bg-deletion-emphasis-override: color-mix(in srgb, var(--diff-removed-bg) 92%, var(--diff-removed-accent));
        --diffs-bg-selection-override: var(--selection-bg);
        --diffs-bg-selection-number-override: var(--selection-bg);
        --diffs-gap-style: 1px solid color-mix(in srgb, var(--tool-output-border) 82%, transparent);
        --diffs-min-number-column-width-default: 2ch;
      }

      [data-diff],
      [data-file],
      pre,
      code {
        background: var(--tool-output-bg);
        color: var(--text);
      }

      [data-column-number],
      [data-gutter-buffer] {
        border-right-color: color-mix(in srgb, var(--tool-output-border) 82%, transparent);
      }

      [data-line-type="change-addition"],
      [data-line-type="change-addition"] + [data-no-newline],
      [data-column-number][data-line-type="change-addition"] {
        background: var(--diffs-bg-addition);
      }

      [data-line-type="change-addition"] {
        color: var(--diff-added-text);
      }

      [data-line-type="change-deletion"],
      [data-line-type="change-deletion"] + [data-no-newline],
      [data-column-number][data-line-type="change-deletion"] {
        background: var(--diffs-bg-deletion);
      }

      [data-line-type="change-deletion"] {
        color: var(--diff-removed-text);
      }

      [data-separator="line-info"],
      [data-separator="line-info-basic"],
      [data-separator="metadata"],
      [data-separator="simple"],
      [data-diffs-header="default"] {
        color: var(--text-subtle);
        background: var(--diff-header-bg);
      }

      [data-separator="simple"] {
        min-height: 1px;
      }
    `;
  }

  function loadDiffsModule() {
    diffsModulePromise ??= import("@pierre/diffs");
    return diffsModulePromise;
  }

  function ensureDiffThemesRegistered(
    registerCustomTheme: DiffsModule["registerCustomTheme"],
  ) {
    const pair = readThemePairFromDom();
    const darkName = `pi-web-diff-${pair.dark.id}`;
    const lightName = `pi-web-diff-${pair.light.id}`;

    if (!REGISTERED_DIFF_THEMES.has(darkName)) {
      registerCustomTheme(darkName, async () => ({
        ...resolveShikiTheme(pair.dark),
        name: darkName,
      }));
      REGISTERED_DIFF_THEMES.add(darkName);
    }
    if (!REGISTERED_DIFF_THEMES.has(lightName)) {
      registerCustomTheme(lightName, async () => ({
        ...resolveShikiTheme(pair.light),
        name: lightName,
      }));
      REGISTERED_DIFF_THEMES.add(lightName);
    }

    return { dark: darkName, light: lightName };
  }

  function diffOptions(registerCustomTheme: DiffsModule["registerCustomTheme"]) {
    return {
      diffStyle: "unified" as const,
      diffIndicators: "none" as const,
      disableFileHeader: true,
      hunkSeparators: "simple" as const,
      overflow: "scroll" as const,
      theme: ensureDiffThemesRegistered(registerCustomTheme),
      themeType: readThemeModeFromDom(),
      unsafeCSS: themedUnsafeCss(),
    };
  }

  function classifyLine(
    line: string,
  ): "header" | "hunk" | "added" | "removed" | "context" {
    if (line.startsWith("+++") || line.startsWith("---")) return "header";
    if (line.startsWith("@@")) return "hunk";
    if (line.startsWith("+")) return "added";
    if (line.startsWith("-")) return "removed";
    return "context";
  }

  function hasRenderableDiff(
    fileDiff: FileDiffMetadata | undefined,
  ): fileDiff is FileDiffMetadata {
    return Array.isArray(fileDiff?.hunks) && fileDiff.hunks.length > 0;
  }

  function safeDisplayPath() {
    return (path || "file.txt").replace(/^\.?\//, "").replace(/\\/g, "/");
  }

  function wrapPatchWithFileHeaders(patchText: string) {
    const safePath = safeDisplayPath();
    return `--- a/${safePath}\n+++ b/${safePath}\n${patchText}`;
  }

  function looksLikePatch(patchText: string) {
    if (!patchText) return false;
    return (
      patchText.startsWith("--- ") ||
      patchText.startsWith("+++ ") ||
      patchText.includes("\n@@ ") ||
      patchText.startsWith("@@ ")
    );
  }

  type NumberedDiffLine = {
    kind: "context" | "removed" | "added";
    lineNumber: number;
    text: string;
  };

  function parseNumberedEditLine(line: string): NumberedDiffLine | "gap" | undefined {
    const indicator = line[0];
    if (indicator !== " " && indicator !== "+" && indicator !== "-") return undefined;

    const content = line.slice(1);
    if (/^\s*\.\.\.\s*$/.test(content)) return "gap";

    const match = content.match(/^\s*(\d+)\s(.*)$/);
    if (!match) return undefined;

    return {
      kind:
        indicator === "+"
          ? "added"
          : indicator === "-"
            ? "removed"
            : "context",
      lineNumber: Number(match[1]),
      text: match[2] ?? "",
    };
  }

  function looksLikeNumberedEditDiff(diffText: string) {
    if (!diffText || looksLikePatch(diffText)) return false;
    const lines = diffText.split("\n").filter(line => line.length > 0);
    if (lines.length === 0) return false;

    let parsed = 0;
    let changed = 0;
    for (const line of lines) {
      const parsedLine = parseNumberedEditLine(line);
      if (!parsedLine) return false;
      if (parsedLine === "gap") continue;
      parsed += 1;
      if (parsedLine.kind !== "context") changed += 1;
    }

    return parsed > 0 && changed > 0;
  }

  function hunkHeaderCount(count: number) {
    return count === 1 ? "" : `,${count}`;
  }

  function appendNumberedEditHunk(
    patchLines: string[],
    hunkLines: NumberedDiffLine[],
    lineDelta: number,
  ) {
    if (hunkLines.length === 0) return 0;

    const firstLine = hunkLines[0]!;
    const oldStart =
      firstLine.kind === "added"
        ? Math.max(1, firstLine.lineNumber - lineDelta)
        : firstLine.lineNumber;
    const newStart =
      firstLine.kind === "removed"
        ? Math.max(1, firstLine.lineNumber + lineDelta)
        : firstLine.lineNumber;
    let oldCount = 0;
    let newCount = 0;

    for (const line of hunkLines) {
      if (line.kind !== "added") oldCount += 1;
      if (line.kind !== "removed") newCount += 1;
    }

    patchLines.push(
      `@@ -${oldStart}${hunkHeaderCount(oldCount)} +${newStart}${hunkHeaderCount(newCount)} @@`,
    );

    for (const line of hunkLines) {
      const indicator =
        line.kind === "added" ? "+" : line.kind === "removed" ? "-" : " ";
      patchLines.push(`${indicator}${line.text}`);
    }

    return newCount - oldCount;
  }

  function numberedEditDiffToPatch(diffText: string) {
    const patchLines = [`--- a/${safeDisplayPath()}`, `+++ b/${safeDisplayPath()}`];
    let hunkLines: NumberedDiffLine[] = [];
    let lineDelta = 0;

    for (const line of diffText.split("\n")) {
      if (!line) continue;

      const parsedLine = parseNumberedEditLine(line);
      if (!parsedLine) throw new Error("Unsupported numbered edit diff format");
      if (parsedLine === "gap") {
        lineDelta += appendNumberedEditHunk(patchLines, hunkLines, lineDelta);
        hunkLines = [];
        continue;
      }

      hunkLines.push(parsedLine);
    }

    appendNumberedEditHunk(patchLines, hunkLines, lineDelta);
    return patchLines.join("\n");
  }

  function ensureTrailingNewline(text: string) {
    if (!text) return "";
    return text.endsWith("\n") ? text : `${text}\n`;
  }

  function normalizeLineEndings(text: string) {
    return text.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  }

  function splitLines(text: string) {
    if (!text) return [];
    return ensureTrailingNewline(normalizeLineEndings(text)).split("\n").slice(0, -1);
  }

  function commonPrefixCount(left: string[], right: string[]) {
    let count = 0;
    while (count < left.length && count < right.length && left[count] === right[count]) {
      count += 1;
    }
    return count;
  }

  function commonSuffixCount(left: string[], right: string[], prefixCount: number) {
    let count = 0;
    const leftLimit = left.length - prefixCount;
    const rightLimit = right.length - prefixCount;
    while (
      count < leftLimit &&
      count < rightLimit &&
      left[left.length - 1 - count] === right[right.length - 1 - count]
    ) {
      count += 1;
    }
    return count;
  }

  function locateTextLine(
    content: string | null,
    text: string,
    fromIndex: number,
  ): { lineNumber: number; endIndex: number } | undefined {
    if (!content || !text) return undefined;
    const normalizedText = normalizeLineEndings(text);
    if (!normalizedText) return undefined;

    const index = content.indexOf(normalizedText, fromIndex);
    if (index === -1) return undefined;

    return {
      lineNumber: content.slice(0, index).split("\n").length,
      endIndex: index + normalizedText.length,
    };
  }

  function synthesizePatchFromEdits(
    editList: DiffEdit[],
    fileContent: string | null,
  ) {
    if (editList.length === 0 || fileContent === null) return "";

    const lines = [`--- a/${safeDisplayPath()}`, `+++ b/${safeDisplayPath()}`];
    const normalizedFileContent = normalizeLineEndings(fileContent);
    let searchIndex = 0;
    let oldLine = 1;
    let newLine = 1;

    for (const edit of editList) {
      const oldLines = splitLines(edit.oldText);
      const newLines = splitLines(edit.newText);
      const prefixCount = commonPrefixCount(oldLines, newLines);
      const suffixCount = commonSuffixCount(oldLines, newLines, prefixCount);

      const contextBefore = oldLines.slice(0, prefixCount);
      const removedLines = oldLines.slice(prefixCount, oldLines.length - suffixCount);
      const addedLines = newLines.slice(prefixCount, newLines.length - suffixCount);
      const contextAfter = oldLines.slice(oldLines.length - suffixCount);

      const hunkOldCount = contextBefore.length + removedLines.length + contextAfter.length;
      const hunkNewCount = contextBefore.length + addedLines.length + contextAfter.length;
      const located =
        locateTextLine(normalizedFileContent, edit.newText, searchIndex) ??
        locateTextLine(normalizedFileContent, edit.oldText, searchIndex);
      const hunkOldStart = located?.lineNumber ?? oldLine;
      const hunkNewStart = located?.lineNumber ?? newLine;

      lines.push(`@@ -${hunkOldStart},${hunkOldCount} +${hunkNewStart},${hunkNewCount} @@`);
      for (const line of contextBefore) lines.push(` ${line}`);
      for (const line of removedLines) lines.push(`-${line}`);
      for (const line of addedLines) lines.push(`+${line}`);
      for (const line of contextAfter) lines.push(` ${line}`);

      if (located) searchIndex = located.endIndex;
      oldLine = hunkOldStart + Math.max(hunkOldCount, 1);
      newLine = hunkNewStart + Math.max(hunkNewCount, 1);
    }

    return lines.join("\n");
  }

  function parseDiffText(
    patchText: string,
    processFile: DiffsModule["processFile"],
  ): FileDiffMetadata {
    const candidates: string[] = [];

    // Prefer file-anchored edit hunks once we have source content.
    if (syntheticPatch) candidates.push(syntheticPatch);
    if (looksLikeNumberedEditDiff(patchText)) {
      candidates.push(numberedEditDiffToPatch(patchText));
    }
    if (looksLikePatch(patchText)) {
      candidates.push(patchText);
      if (!patchText.startsWith("--- ") && !patchText.startsWith("+++ ")) {
        candidates.push(wrapPatchWithFileHeaders(patchText));
      }
    }

    if (patchText && !looksLikePatch(patchText)) candidates.push(patchText);

    for (const candidate of candidates) {
      const fileDiff = processFile(candidate, {
        cacheKey: path,
        throwOnError: true,
      });
      if (hasRenderableDiff(fileDiff)) return fileDiff;
    }

    throw new Error("Unsupported diff format for @pierre/diffs");
  }

  function clearRenderedDiff() {
    diffRenderer?.cleanUp();
    diffRenderer = undefined;
    hasRenderedDiff = false;
  }

  function createDiffRenderer(
    FileDiff: DiffsModule["FileDiff"],
    registerCustomTheme: DiffsModule["registerCustomTheme"],
  ) {
    clearRenderedDiff();
    diffRenderer = new FileDiff(diffOptions(registerCustomTheme), undefined, true);
    return diffRenderer;
  }

  async function renderDiff() {
    if (!host) return;

    const requestId = ++renderRequestId;

    if (!fallbackText) {
      clearRenderedDiff();
      renderError = "";
      loading = false;
      return;
    }

    loading = true;

    try {
      const diffs = await loadDiffsModule();
      if (requestId !== renderRequestId || !host) return;

      const fileDiff = parseDiffText(normalizedDiff, diffs.processFile);
      const renderer = createDiffRenderer(diffs.FileDiff, diffs.registerCustomTheme);
      if (requestId !== renderRequestId || !host) return;

      renderer.render({
        fileDiff,
        fileContainer: host,
        forceRender: true,
      });
      hasRenderedDiff = true;
      renderError = "";
    } catch (error) {
      clearRenderedDiff();
      renderError =
        error instanceof Error ? error.message : "Failed to render diff";
    } finally {
      if (requestId === renderRequestId) loading = false;
    }
  }

  $effect(() => {
    void [path, readWorkspaceFile];

    const currentPath = path;
    if (!currentPath || edits.length === 0 || !readWorkspaceFile) {
      currentFileContent = null;
      currentFilePath = null;
      return;
    }
    if (currentFilePath === currentPath && currentFileContent !== null) {
      return;
    }

    const requestId = ++currentFileRequestId;
    currentFilePath = currentPath;
    currentFileContent = null;
    readWorkspaceFile(currentPath)
      .then(file => {
        if (requestId === currentFileRequestId && currentFilePath === currentPath) {
          currentFileContent = file.content;
        }
      })
      .catch(() => {
        if (requestId === currentFileRequestId && currentFilePath === currentPath) {
          currentFileContent = null;
        }
      });

    return () => {
      currentFileRequestId += 1;
    };
  });

  $effect(() => {
    void [host, normalizedDiff, path, syntheticPatch];
    renderDiff();
  });

  onMount(() => {
    const shell = document.querySelector(".app-shell");
    if (shell) {
      themeObserver = new MutationObserver(() => {
        void renderDiff();
      });
      themeObserver.observe(shell, {
        attributes: true,
        attributeFilter: [
          "data-theme-mode",
          "data-theme",
          "data-dark-theme",
          "data-light-theme",
        ],
      });
    }

    return () => {
      themeObserver?.disconnect();
      renderRequestId += 1;
      clearRenderedDiff();
    };
  });
</script>

<div class="diff-view-shell">
  <diffs-container bind:this={host} class="diff-view-host"></diffs-container>

  {#if loading && !hasRenderedDiff && !renderError}
    <div class="diff-view-status">Loading diff...</div>
  {:else if !hasRenderedDiff && fallbackText}
    <div class="diff-view-fallback" role="note">
      {#if renderError}
        <div class="diff-view-fallback-title">{renderError}</div>
      {/if}
      <table class="diff-table" role="presentation">
        <tbody>
          {#each fallbackLines as line, index (`${index}:${line.text}`)}
            <tr class="diff-line" data-kind={line.kind}>
              <td>
                <pre>{line.text}</pre>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>

<style>
  .diff-view-shell {
    margin: 0;
    border: 1px solid var(--tool-output-border);
    border-radius: 10px;
    background: var(--tool-output-bg);
    overflow: auto;
    max-height: 360px;
  }

  .diff-view-host {
    display: block;
    min-width: 0;
  }

  .diff-view-status,
  .diff-view-fallback {
    color: var(--text-muted);
  }

  .diff-view-status,
  .diff-view-fallback-title {
    padding: 10px 12px;
    font-size: 0.72rem;
    line-height: 1.65;
  }

  .diff-view-fallback-title {
    color: var(--warning-text, var(--text));
  }

  .diff-table {
    width: max-content;
    min-width: 100%;
    border-collapse: collapse;
    border-spacing: 0;
  }

  .diff-line td {
    padding: 0;
    color: var(--text);
  }

  .diff-line pre {
    margin: 0;
    padding: 0 12px;
    font-family: var(--pi-font-mono);
    font-size: 0.72rem;
    line-height: 1.65;
    white-space: pre;
    color: inherit;
    font-weight: 500;
  }

  .diff-line[data-kind="header"] td {
    background: color-mix(in srgb, var(--tool-output-bg) 92%, var(--border));
    color: var(--text-subtle);
  }

  .diff-line[data-kind="hunk"] td {
    border-top: 1px solid
      color-mix(in srgb, var(--tool-output-border) 82%, transparent);
    border-bottom: 1px solid
      color-mix(in srgb, var(--tool-output-border) 82%, transparent);
    background: color-mix(in srgb, var(--tool-output-bg) 84%, var(--border));
    color: var(--text);
  }

  .diff-line[data-kind="added"] td {
    background: color-mix(
      in srgb,
      var(--diff-added-bg) 72%,
      var(--tool-output-bg)
    );
    box-shadow: inset 3px 0 0 var(--diff-added-accent);
    color: var(--diff-added-text);
  }

  .diff-line[data-kind="removed"] td {
    background: color-mix(
      in srgb,
      var(--diff-removed-bg) 72%,
      var(--tool-output-bg)
    );
    box-shadow: inset 3px 0 0 var(--diff-removed-accent);
    color: var(--diff-removed-text);
  }
</style>
