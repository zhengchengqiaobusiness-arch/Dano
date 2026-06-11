<script lang="ts">
  import { Comark } from "@comark/svelte";
  import type { RenderOptions } from "beautiful-mermaid";
  import DOMPurify from "dompurify";
  import { onMount, tick } from "svelte";
  import { highlightCodeHtml, readThemeMode } from "../utils/codeHighlight";
  import { parseInlineFileReference } from "../utils/fileReferences";

  let {
    class: className = "",
    content = "",
    streaming = false,
    deferMermaidErrors = false,
    onOpenFileReference = (_: { path: string; lineNumber: number }) => {},
  }: {
    class?: string;
    content?: string;
    streaming?: boolean;
    deferMermaidErrors?: boolean;
    onOpenFileReference?: (payload: { path: string; lineNumber: number }) => void;
  } = $props();

  type MermaidRenderer = typeof import("beautiful-mermaid").renderMermaidSVG;

  const COMARK_OPTIONS = { html: false };
  const MERMAID_MIN_WIDTH = 420;
  const MERMAID_MAX_WIDTH = 900;
  const MERMAID_MIN_ZOOM = 0.5;
  const MERMAID_MAX_ZOOM = 2.5;
  const MERMAID_ZOOM_STEP = 0.25;

  let mermaidPromise: Promise<MermaidRenderer> | null = null;
  let renderVersion = 0;
  let codeRenderVersion = 0;
  let themeObserver: MutationObserver | undefined;
  let contentObserver: MutationObserver | undefined;
  let container = $state<HTMLDivElement | null>(null);
  let postProcessScheduled = false;
  let forceCodeRender = false;
  let forceMermaidRender = false;

  function markdownBody(): HTMLElement | null {
    return container?.querySelector<HTMLElement>(".markdown-body") ?? null;
  }

  function languageName(value?: string | null): string {
    for (const token of (value ?? "").split(/\s+/)) {
      if (token.startsWith("language-")) return token.slice("language-".length).toLowerCase();
    }
    return "";
  }

  function loadMermaid(): Promise<MermaidRenderer> {
    mermaidPromise ??= import("beautiful-mermaid").then(m => m.renderMermaidSVG);
    return mermaidPromise;
  }

  function cssVar(styles: CSSStyleDeclaration, name: string, fallback: string) {
    return styles.getPropertyValue(name).trim() || fallback;
  }

  function getMermaidOptions(): RenderOptions {
    const shell = document.querySelector<HTMLElement>(".app-shell");
    const styles = getComputedStyle(shell ?? document.documentElement);
    const isDark = readThemeMode() !== "light";
    return {
      bg: cssVar(styles, "--panel", isDark ? "#161b22" : "#ffffff"),
      fg: cssVar(styles, "--text", isDark ? "#e6edf3" : "#1f2328"),
      line: cssVar(styles, "--text-subtle", isDark ? "#7d8590" : "#6e7781"),
      surface: cssVar(styles, "--panel-2", isDark ? "#21262d" : "#f6f8fa"),
      border: cssVar(styles, "--border-strong", isDark ? "#484f58" : "#afb8c1"),
      font: cssVar(styles, "--pi-font-sans", "system-ui, sans-serif"),
    };
  }

  function sanitizeMermaidSvg(svg: string): string {
    return DOMPurify.sanitize(svg, {
      USE_PROFILES: { svg: true, svgFilters: true },
      ADD_TAGS: ["style"],
      ADD_ATTR: ["class", "style"],
    });
  }

  function numericSvgLength(value: string | null): number | null {
    const match = value?.trim().match(/^[0-9.]+/);
    if (!match) return null;
    const parsed = Number(match[0]);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  }

  function svgViewBoxWidth(svg: SVGSVGElement): number | null {
    const values = svg.getAttribute("viewBox")?.trim().split(/[\s,]+/).map(Number);
    const width = values?.[2];
    return typeof width === "number" && Number.isFinite(width) && width > 0 ? width : null;
  }

  function clampMermaidZoom(value: number): number {
    return Math.min(MERMAID_MAX_ZOOM, Math.max(MERMAID_MIN_ZOOM, value));
  }

  function readMermaidZoom(block: HTMLElement): number {
    const zoom = Number(block.dataset.mermaidZoom);
    return Number.isFinite(zoom) ? clampMermaidZoom(zoom) : 1;
  }

  function mermaidZoomLabel(zoom: number): string {
    return `${Math.round(zoom * 100)}%`;
  }

  function updateMermaidZoom(block: HTMLElement, zoom: number) {
    const nextZoom = clampMermaidZoom(zoom);
    const baseWidth = Number(block.dataset.mermaidBaseWidth);
    const svg = block.querySelector<SVGSVGElement>("svg");
    block.dataset.mermaidZoom = String(nextZoom);
    if (svg && Number.isFinite(baseWidth) && baseWidth > 0) {
      svg.style.width = `${Math.round(baseWidth * nextZoom)}px`;
      svg.style.maxWidth = nextZoom > 1 ? "none" : "100%";
    }
    const label = block.querySelector<HTMLElement>("[data-mermaid-zoom-label]");
    if (label) label.textContent = mermaidZoomLabel(nextZoom);
    const zoomOut = block.querySelector<HTMLButtonElement>('[data-mermaid-zoom-action="out"]');
    const zoomIn = block.querySelector<HTMLButtonElement>('[data-mermaid-zoom-action="in"]');
    const reset = block.querySelector<HTMLButtonElement>('[data-mermaid-zoom-action="reset"]');
    if (zoomOut) zoomOut.disabled = nextZoom <= MERMAID_MIN_ZOOM;
    if (zoomIn) zoomIn.disabled = nextZoom >= MERMAID_MAX_ZOOM;
    if (reset) reset.disabled = nextZoom === 1;
  }

  function mermaidZoomButton(action: string, label: string, title: string) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "mermaid-zoom-button";
    button.dataset.mermaidZoomAction = action;
    button.title = title;
    button.setAttribute("aria-label", title);
    button.textContent = label;
    return button;
  }

  function addMermaidZoomControls(block: HTMLElement) {
    const toolbar = document.createElement("div");
    toolbar.className = "mermaid-block-toolbar";
    const zoomLabel = document.createElement("span");
    zoomLabel.className = "mermaid-zoom-label";
    zoomLabel.dataset.mermaidZoomLabel = "true";
    toolbar.append(
      mermaidZoomButton("out", "-", "Zoom out"),
      zoomLabel,
      mermaidZoomButton("in", "+", "Zoom in"),
      mermaidZoomButton("reset", "Reset zoom", "Reset zoom"),
    );
    toolbar.addEventListener("click", event => {
      const target = event.target instanceof Element
        ? event.target.closest<HTMLButtonElement>("button[data-mermaid-zoom-action]")
        : null;
      if (!target) return;
      const currentZoom = readMermaidZoom(block);
      if (target.dataset.mermaidZoomAction === "out") {
        updateMermaidZoom(block, currentZoom - MERMAID_ZOOM_STEP);
      } else if (target.dataset.mermaidZoomAction === "in") {
        updateMermaidZoom(block, currentZoom + MERMAID_ZOOM_STEP);
      } else {
        updateMermaidZoom(block, 1);
      }
    });
    block.prepend(toolbar);
    updateMermaidZoom(block, readMermaidZoom(block));
  }

  function wrapMermaidDiagram(block: HTMLElement) {
    const scrollContainer = document.createElement("div");
    scrollContainer.className = "mermaid-diagram-scroll";
    scrollContainer.append(...block.childNodes);
    block.replaceChildren(scrollContainer);
  }

  function fitMermaidSvg(block: HTMLElement) {
    const svg = block.querySelector<SVGSVGElement>("svg");
    const intrinsicWidth = svg
      ? (svgViewBoxWidth(svg) ?? numericSvgLength(svg.getAttribute("width")))
      : null;
    if (!intrinsicWidth) return;
    const displayWidth = Math.min(MERMAID_MAX_WIDTH, Math.max(MERMAID_MIN_WIDTH, intrinsicWidth));
    block.dataset.mermaidBaseWidth = String(displayWidth);
    updateMermaidZoom(block, readMermaidZoom(block));
  }

  function errorText(error: unknown): string {
    return error instanceof Error ? error.message : String(error);
  }

  function replaceWithMermaidSource(block: HTMLElement, source: string, statusText: string) {
    const currentStatus = block.querySelector<HTMLElement>(".mermaid-block-status")?.textContent ?? "";
    const currentSource = block.querySelector<HTMLElement>(".mermaid-source code")?.textContent ?? "";
    if (currentStatus === statusText && currentSource === source) return;

    const status = document.createElement("div");
    status.className = "mermaid-block-status";
    status.setAttribute("aria-live", "polite");
    status.textContent = statusText;
    const code = document.createElement("code");
    code.textContent = source;
    const pre = document.createElement("pre");
    pre.className = "mermaid-source";
    pre.append(code);
    delete block.dataset.mermaidBaseWidth;
    delete block.dataset.mermaidZoom;
    delete block.dataset.mermaidRendered;
    delete block.dataset.mermaidThemeMode;
    block.classList.remove("mermaid-block-rendered");
    block.replaceChildren(status, pre);
  }

  function showMermaidDeferred(block: HTMLElement) {
    const source = block.dataset.mermaidSource ?? "";
    replaceWithMermaidSource(block, source, "Waiting for complete Mermaid diagram...");
    block.classList.remove("mermaid-block-error", "mermaid-block-rendered");
  }

  function showMermaidError(block: HTMLElement, source: string, error: unknown) {
    block.classList.add("mermaid-block-error");
    replaceWithMermaidSource(block, source, `Could not render Mermaid diagram: ${errorText(error)}`);
  }

  function createHighlightedPreElement(html: string, source: string, lang: string): HTMLPreElement | null {
    const template = document.createElement("template");
    template.innerHTML = html.trim();
    const pre = template.content.firstElementChild;
    if (!(pre instanceof HTMLPreElement)) return null;
    pre.classList.add("markdown-code-block-rendered");
    pre.dataset.codeSource = source;
    pre.dataset.codeLang = lang;
    return pre;
  }

  function enhanceInlineFileReferences() {
    const root = markdownBody();
    if (!root) return;
    const nodes = root.querySelectorAll<HTMLElement>("code:not(pre code)");
    for (const code of nodes) {
      if (code.closest("a")) continue;
      const text = code.textContent?.trim() ?? "";
      const fileReference = parseInlineFileReference(text);
      if (!fileReference) continue;
      const anchor = document.createElement("a");
      anchor.className = "markdown-file-ref";
      anchor.href = "#";
      anchor.dataset.filePath = fileReference.path;
      anchor.dataset.fileLine = String(fileReference.lineNumber);
      anchor.title = `Open ${fileReference.path} at line ${fileReference.lineNumber}`;
      anchor.append(code.cloneNode(true));
      code.replaceWith(anchor);
    }
  }

  function replaceMermaidCodeBlocks(root: HTMLElement) {
    const codeBlocks = root.querySelectorAll<HTMLElement>("pre > code");
    for (const code of codeBlocks) {
      const pre = code.parentElement;
      if (!(pre instanceof HTMLPreElement)) continue;
      if (pre.closest(".mermaid-block")) continue;
      if (languageName(code.className) !== "mermaid") continue;
      const source = code.textContent ?? "";
      const block = document.createElement("div");
      block.className = "mermaid-block";
      block.dataset.mermaidSource = source;
      replaceWithMermaidSource(block, source, "Rendering diagram...");
      pre.replaceWith(block);
    }
  }

  async function renderCodeBlocks(force = false) {
    const version = ++codeRenderVersion;
    await tick();

    if (streaming) return;

    const root = markdownBody();
    if (!root) return;

    if (force) {
      const highlightedBlocks = root.querySelectorAll<HTMLPreElement>(
        "pre.markdown-code-block-rendered[data-code-source]",
      );
      for (const pre of highlightedBlocks) {
        const source = pre.dataset.codeSource ?? pre.textContent ?? "";
        const lang = pre.dataset.codeLang ?? "";
        try {
          const html = await highlightCodeHtml(source, lang);
          if (version !== codeRenderVersion) return;
          const replacement = createHighlightedPreElement(html, source, lang);
          if (!replacement) continue;
          pre.replaceWith(replacement);
        } catch {
          if (version !== codeRenderVersion) return;
        }
      }
    }

    const codeBlocks = root.querySelectorAll<HTMLElement>("pre > code");
    for (const code of codeBlocks) {
      const pre = code.parentElement;
      if (!(pre instanceof HTMLPreElement)) continue;
      if (pre.closest(".mermaid-block") || pre.classList.contains("markdown-code-block-rendered")) continue;
      const lang = languageName(code.className);
      if (lang === "mermaid") continue;
      const source = code.textContent ?? "";
      try {
        const html = await highlightCodeHtml(source, lang);
        if (version !== codeRenderVersion) return;
        const replacement = createHighlightedPreElement(html, source, lang);
        if (!replacement) continue;
        pre.replaceWith(replacement);
      } catch {
        if (version !== codeRenderVersion) return;
        pre.classList.add("markdown-code-block-error");
      }
    }
  }

  async function renderMermaidBlocks(force = false) {
    const version = ++renderVersion;
    await tick();

    const root = markdownBody();
    if (!root) return;

    replaceMermaidCodeBlocks(root);

    const blocks = root.querySelectorAll<HTMLElement>(".mermaid-block[data-mermaid-source]");
    if (blocks.length === 0) return;

    if (deferMermaidErrors) {
      for (const block of blocks) showMermaidDeferred(block);
      return;
    }

    const renderMermaid = await loadMermaid();
    if (version !== renderVersion) return;

    const themeMode = readThemeMode();
    const options = getMermaidOptions();
    let index = 0;
    for (const block of blocks) {
      const source = block.dataset.mermaidSource ?? "";
      if (!source) {
        index += 1;
        continue;
      }
      if (!force && block.dataset.mermaidRendered === "true" && block.dataset.mermaidThemeMode === themeMode) {
        index += 1;
        continue;
      }
      try {
        const svg = renderMermaid(source, options);
        if (version !== renderVersion) return;
        block.innerHTML = sanitizeMermaidSvg(svg);
        wrapMermaidDiagram(block);
        fitMermaidSvg(block);
        addMermaidZoomControls(block);
        block.dataset.mermaidRendered = "true";
        block.dataset.mermaidThemeMode = themeMode;
        block.classList.add("mermaid-block-rendered");
        block.classList.remove("mermaid-block-error");
      } catch (error) {
        if (version !== renderVersion) return;
        showMermaidError(block, source, error);
      }
      index += 1;
    }
  }

  function schedulePostProcess(options?: { forceCode?: boolean; forceMermaid?: boolean }) {
    forceCodeRender ||= options?.forceCode ?? false;
    forceMermaidRender ||= options?.forceMermaid ?? false;
    if (postProcessScheduled) return;
    postProcessScheduled = true;
    queueMicrotask(async () => {
      postProcessScheduled = false;
      const shouldForceCode = forceCodeRender;
      const shouldForceMermaid = forceMermaidRender;
      forceCodeRender = false;
      forceMermaidRender = false;
      await tick();
      enhanceInlineFileReferences();
      await renderCodeBlocks(shouldForceCode);
      await renderMermaidBlocks(shouldForceMermaid);
    });
  }

  function handleClick(event: MouseEvent) {
    handleClickTarget(event.target);
    event.preventDefault();
  }

  function handleClickTarget(target: EventTarget | null) {
    const el = target instanceof Element
      ? target.closest<HTMLAnchorElement>("a[data-file-path][data-file-line]")
      : null;
    if (!el) return;
    const path = el.dataset.filePath?.trim();
    const lineNumber = Number.parseInt(el.dataset.fileLine ?? "", 10);
    if (!path || !Number.isInteger(lineNumber) || lineNumber < 1) return;
    onOpenFileReference({ path, lineNumber });
  }

  onMount(() => {
    schedulePostProcess();

    if (container) {
      contentObserver = new MutationObserver(() => {
        schedulePostProcess();
      });
      contentObserver.observe(container, {
        childList: true,
        subtree: true,
      });
    }

    const shell = document.querySelector(".app-shell");
    if (shell) {
      themeObserver = new MutationObserver(records => {
        const rerenderMermaid = records.some(r => r.attributeName === "data-theme-mode");
        const rerenderCode = records.some(
          r => r.attributeName === "data-dark-theme" || r.attributeName === "data-light-theme",
        );
        if (rerenderMermaid || rerenderCode) {
          schedulePostProcess({
            forceCode: rerenderCode,
            forceMermaid: rerenderMermaid,
          });
        }
      });
      themeObserver.observe(shell, {
        attributes: true,
        attributeFilter: ["data-theme-mode", "data-dark-theme", "data-light-theme"],
      });
    }

    return () => {
      renderVersion += 1;
      codeRenderVersion += 1;
      themeObserver?.disconnect();
      contentObserver?.disconnect();
    };
  });

  $effect(() => {
    void [content, streaming, deferMermaidErrors];
    schedulePostProcess();
  });

</script>

<div bind:this={container} role="button" tabindex="0" onclick={handleClick} onkeydown={(e) => (e.key === "Enter" || e.key === " ") && handleClickTarget(e.target)}>
  <Comark
    markdown={content}
    options={COMARK_OPTIONS}
    streaming={streaming}
    class={`markdown-body ${className}`.trim()}
  />
</div>

<style>
  :global(.markdown-body) {
    font-size: 0.9rem;
    line-height: 1.7;
    color: var(--text);
    word-break: break-word;
  }

  /* Markdown children are rendered by Comark, so descendant selectors must stay global. */
  :global(.markdown-body > *:first-child) {
    margin-top: 0;
  }

  :global(.markdown-body > *:last-child) {
    margin-bottom: 0;
  }

  :global(.markdown-body p) {
    margin: 0.4em 0;
    white-space: pre-wrap;
  }

  :global(.markdown-body h1),
  :global(.markdown-body h2),
  :global(.markdown-body h3),
  :global(.markdown-body h4),
  :global(.markdown-body h5),
  :global(.markdown-body h6) {
    margin: 1.2em 0 0.4em;
    font-weight: 600;
    line-height: 1.3;
    color: var(--text);
  }

  :global(.markdown-body h1) { font-size: 1.4em; }
  :global(.markdown-body h2) { font-size: 1.25em; }
  :global(.markdown-body h3) { font-size: 1.1em; }
  :global(.markdown-body h4) { font-size: 1em; }

  :global(.markdown-body ul),
  :global(.markdown-body ol) {
    margin: 0.5em 0;
    padding-left: 1.6em;
  }

  :global(.markdown-body ul) { list-style: disc; }
  :global(.markdown-body ol) { list-style: decimal; }

  :global(.markdown-body li) {
    margin: 0.2em 0;
    white-space: pre-wrap;
  }

  :global(.markdown-body li > p) { margin: 0.3em 0; }

  :global(.markdown-body blockquote) {
    margin: 0.6em 0;
    padding: 0.4em 1em;
    border-left: 3px solid var(--border-strong);
    color: var(--text-muted);
    background: var(--panel);
    border-radius: 0 6px 6px 0;
  }

  :global(.markdown-body blockquote p) {
    margin: 0.2em 0;
    white-space: pre-wrap;
  }

  :global(.markdown-body code) {
    font-family: var(--pi-font-mono);
    font-size: 0.85em;
    padding: 0.15em 0.4em;
    border-radius: 4px;
    background: var(--panel);
    color: var(--text);
  }

  :global(.markdown-body a.markdown-file-ref) {
    color: inherit;
    text-decoration: none;
    white-space: normal;
    overflow-wrap: anywhere;
  }

  :global(.markdown-body a.markdown-file-ref code) {
    color: color-mix(in srgb, var(--accent) 82%, var(--text));
    border: 1px solid color-mix(in srgb, var(--accent) 28%, transparent);
    background: color-mix(in srgb, var(--surface-active) 64%, var(--panel-2));
    white-space: normal;
    overflow-wrap: anywhere;
    word-break: break-word;
    box-decoration-break: clone;
    -webkit-box-decoration-break: clone;
    cursor: pointer;
    transition:
      border-color 0.12s ease,
      color 0.12s ease,
      background 0.12s ease;
  }

  :global(.markdown-body a.markdown-file-ref:hover code),
  :global(.markdown-body a.markdown-file-ref:focus-visible code) {
    color: var(--accent-hover);
    border-color: color-mix(in srgb, var(--accent) 52%, var(--border-strong));
    background: color-mix(in srgb, var(--surface-active) 88%, var(--panel-2));
  }

  :global(.markdown-body a.markdown-file-ref:focus-visible) {
    outline: 2px solid color-mix(in srgb, var(--focus-ring) 72%, transparent);
    outline-offset: 2px;
    border-radius: 6px;
  }

  :global(.markdown-body pre) {
    margin: 0.6em 0;
    padding: 14px 16px;
    border-radius: 8px;
    background: var(--panel);
    border: 1px solid var(--border);
    overflow-x: auto;
    line-height: 1.5;
  }

  :global(.markdown-body pre code) {
    display: block;
    padding: 0;
    border-radius: 0;
    background: none;
    font-size: 0.82rem;
    white-space: pre;
    word-break: normal;
    overflow-wrap: normal;
  }

  :global(.markdown-body .mermaid-block) {
    margin: 0.7em 0;
    padding: 14px 16px;
    border: 1px solid var(--border);
    border-radius: 10px;
    background: var(--panel);
    overflow: hidden;
  }

  :global(.markdown-body .mermaid-block-rendered) { text-align: center; }

  :global(.markdown-body .mermaid-block-toolbar) {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 6px;
    margin: 0 0 10px;
  }

  :global(.markdown-body .mermaid-zoom-button) {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 28px;
    height: 24px;
    padding: 0 8px;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: color-mix(in srgb, var(--panel-2) 82%, transparent);
    color: var(--text-muted);
    font: inherit;
    font-size: 0.68rem;
    line-height: 1;
    cursor: pointer;
  }

  :global(.markdown-body .mermaid-zoom-button:hover:not(:disabled)) {
    border-color: var(--border-strong);
    color: var(--text);
  }

  :global(.markdown-body .mermaid-zoom-button:disabled) {
    opacity: 0.45;
    cursor: not-allowed;
  }

  :global(.markdown-body .mermaid-zoom-label) {
    min-width: 42px;
    color: var(--text-subtle);
    font-size: 0.68rem;
    line-height: 1;
    text-align: center;
  }

  :global(.markdown-body .mermaid-diagram-scroll) {
    overflow-x: auto;
    overflow-y: hidden;
  }

  :global(.markdown-body .mermaid-block svg) {
    display: block;
    width: min(100%, var(--mermaid-svg-width, 760px));
    max-width: 100%;
    height: auto;
    margin: 0 auto;
  }

  :global(.markdown-body .mermaid-block-status) {
    margin-bottom: 10px;
    color: var(--text-subtle);
    font-size: 0.76rem;
  }

  :global(.markdown-body .mermaid-source) {
    margin: 0;
    padding: 0;
    border: none;
    background: transparent;
  }

  :global(.markdown-body .mermaid-block-rendered .mermaid-block-status),
  :global(.markdown-body .mermaid-block-rendered .mermaid-source) { display: none; }

  :global(.markdown-body .mermaid-block-error) {
    border-color: color-mix(in srgb, var(--error-border) 72%, var(--border));
  }

  :global(.markdown-body a) {
    color: var(--text-muted);
    text-decoration: underline;
    text-underline-offset: 2px;
  }

  :global(.markdown-body a:hover) { color: var(--text); }

  :global(.markdown-body hr) {
    margin: 1.2em 0;
    border: none;
    border-top: 1px solid var(--border);
  }

  :global(.markdown-body table) {
    margin: 0.6em 0;
    border-collapse: collapse;
    width: 100%;
    font-size: 0.85em;
  }

  :global(.markdown-body th),
  :global(.markdown-body td) {
    padding: 8px 12px;
    border: 1px solid var(--border);
    text-align: left;
  }

  :global(.markdown-body th) {
    background: var(--panel);
    font-weight: 600;
    color: var(--text);
  }

  :global(.markdown-body img) {
    max-width: 100%;
    border-radius: 6px;
  }

  :global(.markdown-body input[type="checkbox"]) {
    margin-right: 0.5em;
    pointer-events: none;
  }

  :global(.markdown-body strong) { font-weight: 600; color: var(--text); }
  :global(.markdown-body em) { font-style: italic; }

  :global(.markdown-body del) {
    text-decoration: line-through;
    color: var(--text-subtle);
  }

  :global(.markdown-body details) { margin: 0.5em 0; }
  :global(.markdown-body summary) {
    cursor: pointer;
    font-size: 0.85em;
    color: var(--text-muted);
  }
</style>
