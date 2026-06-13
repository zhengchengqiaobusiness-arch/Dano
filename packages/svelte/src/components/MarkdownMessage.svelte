<script lang="ts">
  import { onDestroy, tick } from "svelte";
  import DOMPurify from "dompurify";
  import hljs from "highlight.js/lib/core";
  import bash from "highlight.js/lib/languages/bash";
  import css from "highlight.js/lib/languages/css";
  import go from "highlight.js/lib/languages/go";
  import java from "highlight.js/lib/languages/java";
  import javascript from "highlight.js/lib/languages/javascript";
  import json from "highlight.js/lib/languages/json";
  import markdown from "highlight.js/lib/languages/markdown";
  import python from "highlight.js/lib/languages/python";
  import rust from "highlight.js/lib/languages/rust";
  import shell from "highlight.js/lib/languages/shell";
  import sql from "highlight.js/lib/languages/sql";
  import typescript from "highlight.js/lib/languages/typescript";
  import xml from "highlight.js/lib/languages/xml";
  import yaml from "highlight.js/lib/languages/yaml";
  import "highlight.js/styles/github.css";
  import { marked, Renderer, type Tokens } from "marked";

  type MessageStatus = "pending" | "streaming" | "completed" | "failed";

  const MERMAID_LANGUAGES = new Set(["mermaid", "mmd"]);
  const LANGUAGE_CLASS_PATTERN = /^[a-z0-9_+.#-]+$/i;

  const highlightRegistry = globalThis as typeof globalThis & {
    __danoHighlightLanguagesRegistered?: boolean;
  };

  if (!highlightRegistry.__danoHighlightLanguagesRegistered) {
    hljs.registerLanguage("bash", bash);
    hljs.registerLanguage("css", css);
    hljs.registerLanguage("go", go);
    hljs.registerLanguage("java", java);
    hljs.registerLanguage("javascript", javascript);
    hljs.registerLanguage("js", javascript);
    hljs.registerLanguage("json", json);
    hljs.registerLanguage("markdown", markdown);
    hljs.registerLanguage("md", markdown);
    hljs.registerLanguage("python", python);
    hljs.registerLanguage("py", python);
    hljs.registerLanguage("rust", rust);
    hljs.registerLanguage("rs", rust);
    hljs.registerLanguage("shell", shell);
    hljs.registerLanguage("sh", shell);
    hljs.registerLanguage("sql", sql);
    hljs.registerLanguage("typescript", typescript);
    hljs.registerLanguage("ts", typescript);
    hljs.registerLanguage("html", xml);
    hljs.registerLanguage("xml", xml);
    hljs.registerLanguage("yaml", yaml);
    hljs.registerLanguage("yml", yaml);
    highlightRegistry.__danoHighlightLanguagesRegistered = true;
  }

  let {
    content,
    status,
    onrendered,
  }: {
    content: string;
    status: MessageStatus;
    onrendered?: () => void;
  } = $props();
  let root = $state<HTMLDivElement>();
  let mermaidConfigured = false;
  let mermaidLoader: Promise<typeof import("mermaid")> | null = null;
  const copyResetTimers = new Set<ReturnType<typeof setTimeout>>();

  const html = $derived(renderMarkdown(content, status === "streaming"));

  onDestroy(() => {
    for (const timer of copyResetTimers) {
      clearTimeout(timer);
    }
    copyResetTimers.clear();
  });

  $effect(() => {
    html;
    status;

    const currentRoot = root;
    if (!currentRoot) {
      return;
    }

    void tick().then(async () => {
      if (status !== "streaming") {
        await renderMermaid(currentRoot);
      }
      onrendered?.();
    });
  });

  $effect(() => {
    const currentRoot = root;
    if (!currentRoot) {
      return;
    }

    currentRoot.addEventListener("click", handleMarkdownClick);
    return () => currentRoot.removeEventListener("click", handleMarkdownClick);
  });

  function renderMarkdown(markdown: string, deferMermaid: boolean): string {
    const renderer = new Renderer();

    renderer.code = (token: Tokens.Code) => {
      const language = normalizeLanguage(token.lang);
      if (MERMAID_LANGUAGES.has(language) && !deferMermaid) {
        return `<div class="mermaid">${escapeHtml(token.text)}</div>`;
      }

      const languageClass = language && LANGUAGE_CLASS_PATTERN.test(language)
        ? ` language-${language}`
        : "";
      const languageLabel = language
        ? `<span class="code-language">${escapeHtml(language)}</span>`
        : "<span></span>";

      return `<div class="code-block"><div class="code-block-header">${languageLabel}<button type="button" class="code-copy-button" aria-label="Copy code" title="Copy code"><span class="code-copy-icon" aria-hidden="true"></span></button></div><pre><code class="hljs${languageClass}">${highlightCode(
        token.text,
        language,
      )}</code></pre></div>`;
    };

    const rawHtml = marked(markdown, {
      async: false,
      breaks: false,
      gfm: true,
      renderer,
    });

    return DOMPurify.sanitize(rawHtml, {
      ADD_ATTR: [
        "class",
        "target",
        "rel",
        "type",
        "title",
        "aria-label",
        "aria-hidden",
      ],
    });
  }

  async function handleMarkdownClick(event: MouseEvent) {
    const target = event.target;
    if (!(target instanceof Element)) {
      return;
    }

    const button = target.closest<HTMLButtonElement>(".code-copy-button");
    if (!button || !root?.contains(button)) {
      return;
    }

    const code = button
      .closest<HTMLElement>(".code-block")
      ?.querySelector<HTMLElement>("pre code");
    const text = code?.textContent ?? "";
    if (!text) {
      return;
    }

    try {
      await copyText(text);
      showCopiedState(button);
    } catch {
      showCopyFailedState(button);
    }
  }

  async function copyText(text: string): Promise<void> {
    try {
      copyTextWithTextarea(text);
      return;
    } catch {
      // Fall back to the modern Clipboard API when the legacy copy command is unavailable.
    }

    const clipboard = globalThis.navigator?.clipboard;
    if (clipboard?.writeText) {
      await clipboard.writeText(text);
      return;
    }

    throw new Error("Clipboard is unavailable");
  }

  function copyTextWithTextarea(text: string): void {
    const activeElement = document.activeElement;
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.top = "-9999px";
    textarea.style.left = "-9999px";
    textarea.style.pointerEvents = "none";
    document.body.append(textarea);
    textarea.focus();
    textarea.select();
    textarea.setSelectionRange(0, text.length);

    try {
      if (
        typeof document.execCommand !== "function" ||
        !document.execCommand("copy")
      ) {
        throw new Error("Copy command failed");
      }
    } finally {
      textarea.remove();
      if (activeElement instanceof HTMLElement) {
        activeElement.focus({ preventScroll: true });
      }
    }
  }

  function showCopyFailedState(button: HTMLButtonElement) {
    button.classList.remove("copied");
    button.title = "Copy failed";
    button.setAttribute("aria-label", "Copy failed");

    const timer = setTimeout(() => {
      button.title = "Copy code";
      button.setAttribute("aria-label", "Copy code");
      copyResetTimers.delete(timer);
    }, 1200);
    copyResetTimers.add(timer);
  }

  function showCopiedState(button: HTMLButtonElement) {
    button.classList.add("copied");
    button.title = "Copied";
    button.setAttribute("aria-label", "Copied");

    const timer = setTimeout(() => {
      button.classList.remove("copied");
      button.title = "Copy code";
      button.setAttribute("aria-label", "Copy code");
      copyResetTimers.delete(timer);
    }, 1200);
    copyResetTimers.add(timer);
  }

  function normalizeLanguage(language: string | undefined): string {
    return language?.trim().split(/\s+/)[0]?.toLowerCase() ?? "";
  }

  function highlightCode(code: string, language: string): string {
    try {
      if (language && hljs.getLanguage(language)) {
        return hljs.highlight(code, {
          language,
          ignoreIllegals: true,
        }).value;
      }

      return hljs.highlightAuto(code).value;
    } catch {
      return escapeHtml(code);
    }
  }

  function escapeHtml(value: string): string {
    return value
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  async function renderMermaid(container: HTMLDivElement): Promise<void> {
    const nodes = Array.from(container.querySelectorAll<HTMLElement>(".mermaid"));
    if (nodes.length === 0) {
      return;
    }

    mermaidLoader ??= import("mermaid");
    const mermaid = (await mermaidLoader).default;

    if (!mermaidConfigured) {
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        theme: "default",
      });
      mermaidConfigured = true;
    }

    try {
      await mermaid.run({ nodes });
    } catch (error) {
      for (const node of nodes) {
        node.classList.add("mermaid-error");
        node.setAttribute(
          "aria-label",
          error instanceof Error ? error.message : "Mermaid diagram failed to render.",
        );
      }
    }
  }
</script>

<div class="markdown-body" bind:this={root}>
  {@html html}
</div>

<style>
  .markdown-body {
    line-height: 1.58;
    overflow-wrap: anywhere;
  }

  .markdown-body :global(:first-child) {
    margin-top: 0;
  }

  .markdown-body :global(:last-child) {
    margin-bottom: 0;
  }

  .markdown-body :global(p),
  .markdown-body :global(ul),
  .markdown-body :global(ol),
  .markdown-body :global(blockquote),
  .markdown-body :global(.code-block),
  .markdown-body :global(pre),
  .markdown-body :global(table),
  .markdown-body :global(.mermaid) {
    margin: 0 0 12px;
  }

  .markdown-body :global(ul),
  .markdown-body :global(ol) {
    padding-left: 22px;
  }

  .markdown-body :global(li + li) {
    margin-top: 4px;
  }

  .markdown-body :global(blockquote) {
    border-left: 3px solid #94a3b8;
    padding: 2px 0 2px 12px;
    color: #475569;
  }

  .markdown-body :global(pre) {
    max-width: 100%;
    overflow-x: auto;
    border: 1px solid #d8dee8;
    border-radius: 7px;
    background: #f8fafc;
  }

  .markdown-body :global(.code-block) {
    max-width: 100%;
    overflow: hidden;
    border: 1px solid #d8dee8;
    border-radius: 10px;
    background: #f1f4f8;
  }

  .markdown-body :global(.code-block-header) {
    min-height: 34px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 0 10px 0 14px;
    color: #8a9099;
    font-family:
      ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono",
      monospace;
    font-size: 13px;
    line-height: 1;
  }

  .markdown-body :global(.code-language) {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .markdown-body :global(.code-copy-button) {
    width: 28px;
    height: 28px;
    flex: 0 0 auto;
    display: grid;
    place-items: center;
    border: 0;
    border-radius: 7px;
    background: transparent;
    color: #8a9099;
    cursor: pointer;
  }

  .markdown-body :global(.code-copy-button:hover),
  .markdown-body :global(.code-copy-button:focus-visible) {
    background: #e3e8f0;
    color: #5f6673;
  }

  .markdown-body :global(.code-copy-button:focus-visible) {
    outline: 2px solid rgba(47, 102, 255, 0.26);
    outline-offset: 2px;
  }

  .markdown-body :global(.code-copy-button.copied) {
    color: #047857;
  }

  .markdown-body :global(.code-copy-icon) {
    width: 17px;
    height: 17px;
    display: block;
    position: relative;
  }

  .markdown-body :global(.code-copy-icon::before),
  .markdown-body :global(.code-copy-icon::after) {
    content: "";
    position: absolute;
    width: 9px;
    height: 11px;
    border: 2px solid currentColor;
    border-radius: 4px;
    background: transparent;
  }

  .markdown-body :global(.code-copy-icon::before) {
    top: 1px;
    left: 6px;
  }

  .markdown-body :global(.code-copy-icon::after) {
    top: 5px;
    left: 2px;
    background: #f1f4f8;
  }

  .markdown-body :global(.code-copy-button:hover .code-copy-icon::after),
  .markdown-body :global(.code-copy-button:focus-visible .code-copy-icon::after) {
    background: #e3e8f0;
  }

  .markdown-body :global(.code-copy-button.copied .code-copy-icon::after) {
    background: #f1f4f8;
  }

  .markdown-body :global(.code-block pre) {
    margin: 0;
    border: 0;
    border-top: 1px solid #e0e5ee;
    border-radius: 0;
    background: transparent;
  }

  .markdown-body :global(pre code) {
    display: block;
    padding: 12px;
    white-space: pre;
    overflow-wrap: normal;
    font-size: 13px;
    line-height: 1.55;
  }

  .markdown-body :global(:not(pre) > code) {
    border-radius: 4px;
    background: #e8eef7;
    padding: 1px 4px;
    font-size: 0.92em;
  }

  .markdown-body :global(a) {
    color: #1d4ed8;
    text-decoration: underline;
    text-underline-offset: 2px;
  }

  .markdown-body :global(table) {
    display: block;
    max-width: 100%;
    overflow-x: auto;
    border-collapse: collapse;
    font-size: 14px;
  }

  .markdown-body :global(th),
  .markdown-body :global(td) {
    border: 1px solid #d8dee8;
    padding: 7px 9px;
    text-align: left;
    vertical-align: top;
  }

  .markdown-body :global(th) {
    background: #eef2f7;
    font-weight: 700;
  }

  .markdown-body :global(.mermaid) {
    max-width: 100%;
    overflow-x: auto;
    border: 1px solid #d8dee8;
    border-radius: 7px;
    background: #ffffff;
    padding: 12px;
  }

  .markdown-body :global(.mermaid svg) {
    display: block;
    max-width: 100%;
    height: auto;
    margin: 0 auto;
  }

  .markdown-body :global(.mermaid-error) {
    white-space: pre-wrap;
    color: #9f1239;
  }
</style>
