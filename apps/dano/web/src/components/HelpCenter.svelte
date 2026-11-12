<script lang="ts">
  import { onMount, tick } from "svelte";
  import BookOpen from "@lucide/svelte/icons/book-open";
  import ArrowLeft from "@lucide/svelte/icons/arrow-left";
  import Menu from "@lucide/svelte/icons/menu";
  import { Button } from "./ui/button";
  import { Input } from "./ui/input";
  import * as Alert from "./ui/alert";
  import MarkdownRenderer from "./MarkdownRenderer.svelte";
  import ImageLightbox from "./ImageLightbox.svelte";
  import { getRuntimeProductName } from "../utils/runtimeConfig";
  type Article = {
    id: string;
    title: string;
    file?: string;
    group?: string;
    summary?: string;
    keywords?: string[];
    video?: string;
    poster?: string;
    captions?: string;
    image?: string;
    imageAlt?: string;
  };
  let { onClose }: { onClose: () => void } = $props();
  let articles = $state<Article[]>([]),
    active = $state<Article | null>(null),
    content = $state(""),
    query = $state(""),
    error = $state(""),
    loading = $state(true),
    directoryOpen = $state(false),
    preview = $state<string | null>(null);
  let headings = $state<{ id: string; title: string }[]>([]);
  let body: HTMLElement;
  let mediaError = $state("");
  let generation = 0;
  const filtered = $derived(
    articles.filter((a) =>
      [a.title, a.summary, ...(a.keywords ?? [])]
        .join(" ")
        .toLowerCase()
        .includes(query.toLowerCase().trim()),
    ),
  );
  const groups = $derived([
    ...new Set(filtered.map((a) => a.group || "使用手册")),
  ]);
  const index = $derived(articles.findIndex((a) => a.id === active?.id));
  function asset(file: string) {
    return "/api/help/" + file.split("/").map(encodeURIComponent).join("/");
  }
  async function select(article: Article) {
    const token = ++generation;
    active = article;
    directoryOpen = false;
    content = "";
    error = "";
    mediaError = "";
    headings = [];
    loading = true;
    try {
      if (!article.file) return;
      const response = await fetch(asset(article.file), { cache: "no-store" });
      if (!response.ok) throw Error();
      const text = await response.text();
      if (!text.trim()) throw Error();
      if (token !== generation) return;
      content = text;
      await tick();
      body?.closest("main")?.scrollTo(0, 0);
    } catch {
      if (token === generation)
        error = "暂时无法读取这篇手册，请重试或选择其他章节。";
    } finally {
      if (token === generation) loading = false;
    }
  }
  async function load() {
    loading = true;
    error = "";
    try {
      const response = await fetch("/api/help/index.json", {
        cache: "no-store",
      });
      if (!response.ok) throw Error();
      const data: unknown = await response.json();
      if (
        !Array.isArray(data) ||
        !data.every(
          (a) =>
            a &&
            typeof a.id === "string" &&
            typeof a.title === "string" &&
            ((typeof a.file === "string" && /\.md$/i.test(a.file)) ||
              (a.file === undefined && typeof a.video === "string" && /\.(mp4|webm)$/i.test(a.video))) &&
            a.id.length > 0 &&
            a.title.length > 0 &&
            data.filter((b) => b?.id === a.id).length === 1 &&
            [
              "group",
              "summary",
              "video",
              "poster",
              "captions",
              "image",
              "imageAlt",
            ].every(
              (key) => a[key] === undefined || typeof a[key] === "string",
            ) &&
            (a.keywords === undefined ||
              (Array.isArray(a.keywords) &&
                a.keywords.every((k: unknown) => typeof k === "string"))),
        )
      )
        throw Error();
      articles = data;
      if (articles[0]) await select(articles[0]);
      else error = "暂时没有帮助手册，请联系内容维护者。";
    } catch {
      error = "帮助内容暂不可用，请联系维护者检查手册目录。";
    } finally {
      loading = false;
    }
  }
  onMount(() => {
    void load();
    const observer = new MutationObserver(() => {
      for (const img of body?.querySelectorAll("img") ?? []) {
        img.onerror = () => { mediaError = "图片暂不可用，请联系维护者。"; };
        if (img.complete && !img.naturalWidth) mediaError = "图片暂不可用，请联系维护者。";
        if (img.closest("button")) continue;
        img.tabIndex = 0;
        img.setAttribute("role", "button");
        img.setAttribute("aria-label", "放大图片：" + (img.alt || "手册图片"));
      }
      const next = Array.from(body?.querySelectorAll("h1,h2,h3") ?? []).map(
        (h, i) => {
          const id = "help-section-" + i;
          if (h.id !== id) h.id = id;
          return { id, title: h.textContent ?? "" };
        },
      );
      if (JSON.stringify(next) !== JSON.stringify(headings)) headings = next;
    });
    observer.observe(body, { childList: true, subtree: true });
    return () => {
      generation++;
      observer.disconnect();
    };
  });
  function imageClick(event: MouseEvent) {
    if (event.target instanceof HTMLImageElement) preview = event.target.src;
  }
</script>

<div class="help-center">
  <header>
    <div class="brand">
      <BookOpen size={22} /><strong>{getRuntimeProductName()} / 帮助中心</strong
      >
    </div>
    <Button variant="ghost" onclick={onClose}
      ><ArrowLeft data-icon="inline-start" />返回对话</Button
    >
  </header>
  <div class="help-layout">
    <aside class:expanded={directoryOpen}>
      <strong>使用手册</strong><Input
        type="search"
        aria-label="搜索帮助手册"
        placeholder="搜索帮助手册…"
        bind:value={query}
      />
      <nav aria-label="帮助手册章节">
        {#each groups as group}<h2>{group}</h2>
          {#each filtered.filter((a) => (a.group || "使用手册") === group) as article}<Button
              variant={active?.id === article.id ? "secondary" : "ghost"}
              class="w-full justify-start"
              aria-current={active?.id === article.id ? "page" : undefined}
              onclick={() => select(article)}>{article.title}</Button
            >{/each}{/each}{#if query && !filtered.length}<p>
            没有找到相关内容，请尝试其他关键词。
          </p>{/if}
      </nav>
    </aside>
    <main>
      <div class="mobile-directory">
        <Button
          variant="outline"
          onclick={() => (directoryOpen = !directoryOpen)}
          ><Menu data-icon="inline-start" />{directoryOpen
            ? "收起目录"
            : "手册目录"}</Button
        >
      </div>
      {#if active}<div class="intro">
          <small>{active.group || "使用手册"}</small>
          <h1>{active.title}</h1>
          {#if active.summary}<p>{active.summary}</p>{/if}
        </div>{/if}
      {#if loading}<p role="status">正在加载手册…</p>{/if}
      {#if error}<Alert.Root>
          <Alert.Description>{error}</Alert.Description>
          <Button
            variant="outline"
            onclick={() => (active ? select(active) : load())}>重试</Button
          >
        </Alert.Root>{/if}
      <div
        class="article"
        bind:this={body}
        onclick={imageClick}
        onkeydown={(event) => {
          if (event.key === "Enter" && event.target instanceof HTMLImageElement)
            preview = event.target.src;
        }}
        role="presentation"
      >
        {#if !loading && !error && active}
          {#if active.video}<figure>
              <video
                onerror={() =>
                  (mediaError = "视频暂不可用，请稍后重试或联系维护者。")}
                controls
                preload="metadata"
                poster={active.poster ? asset(active.poster) : undefined}
                src={asset(active.video)}
                aria-label={active.title + "操作演示"}
                ><track
                  kind="captions"
                  src={active.captions ? asset(active.captions) : undefined}
                  srclang="zh"
                  label="中文"
                /></video
              >
              <figcaption>操作演示</figcaption>
            </figure>{/if}
          {#if active.file}<MarkdownRenderer
            {content}
            resourceBase={new URL(asset(active.file), location.origin).href}
            interactiveRoot={false}
          />{/if}
          {#if active.image}<figure>
              <Button
                variant="ghost"
                class="h-auto w-full p-0"
                aria-label="放大操作示意图"
                onclick={() => (preview = asset(active!.image!))}
                ><img
                  onerror={() => (mediaError = "图片暂不可用，请联系维护者。")}
                  src={asset(active.image)}
                  alt={active.imageAlt || active.title}
                /></Button
              >
              <figcaption>{active.imageAlt || "点击图片放大"}</figcaption>
            </figure>{/if}
        {/if}
      </div>
      {#if mediaError}<Alert.Root><Alert.Description>{mediaError}</Alert.Description></Alert.Root>{/if}
      {#if active}<footer>
          {#if index > 0}<Button
              variant="outline"
              onclick={() => select(articles[index - 1])}
              >上一篇：{articles[index - 1].title}</Button
            >{/if}{#if index < articles.length - 1}<Button
              variant="outline"
              onclick={() => select(articles[index + 1])}
              >下一篇：{articles[index + 1].title}</Button
            >{/if}
        </footer>{/if}
    </main>
    <nav class="toc" aria-label="本页内容">
      <strong>本页内容</strong>{#each headings as heading}<Button
          variant="ghost"
          class="justify-start whitespace-normal text-left"
          onclick={() =>
            document
              .getElementById(heading.id)
              ?.scrollIntoView({ block: "start" })}>{heading.title}</Button
        >{/each}
    </nav>
  </div>
  <ImageLightbox
    open={preview !== null}
    images={preview
      ? [{ kind: "image", src: preview, alt: active?.title || "手册图片" }]
      : []}
    index={0}
    onClose={() => (preview = null)}
  />
</div>

<style>
  .help-center {
    position: fixed;
    inset: 0;
    z-index: 25;
    background: var(--bg);
    color: var(--text);
    display: flex;
    flex-direction: column;
  }
  header {
    height: 72px;
    padding: 0 32px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-shrink: 0;
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .brand :global(svg) {
    color: var(--accent);
  }
  .help-layout {
    display: grid;
    grid-template-columns: 244px minmax(0, 1fr) 190px;
    flex: 1;
    min-height: 0;
  }
  aside {
    background: var(--panel-2);
    border-right: 1px solid var(--border);
    padding: 28px 18px;
    overflow: auto;
  }
  aside > strong {
    display: block;
    margin-bottom: 22px;
  }
  aside :global(input) {
    margin-bottom: 18px;
  }
  nav h2 {
    font-size: 12px;
    color: var(--text-muted);
    margin: 24px 12px 8px;
  }
  nav :global(button) {
    min-height: 42px;
    max-width: 100%;
    white-space: normal;
  }
  main {
    overflow: auto;
    padding: 40px 48px;
    scroll-padding-top: 24px;
    min-width: 0;
  }
  .intro,
  .article,
  footer,
  main > :global([role="alert"]) {
    max-width: 760px;
    margin: 0 auto;
  }
  .intro small {
    color: var(--text-muted);
  }
  h1 {
    font-size: 30px;
    margin: 12px 0;
  }
  .intro p {
    color: var(--text-muted);
    line-height: 1.8;
  }
  .article {
    line-height: 1.85;
    overflow-wrap: anywhere;
  }
  .article :global(h1),
  .article :global(h2),
  .article :global(h3) {
    margin: 26px 0 14px;
  }
  .article :global(ol),
  .article :global(ul) {
    padding-left: 24px;
  }
  .article :global(img),
  video {
    max-width: 100%;
    border-radius: 10px;
  }
  .article :global(pre) {
    overflow: auto;
  }
  figure {
    margin: 24px 0;
  }
  video {
    width: 100%;
  }
  figcaption {
    text-align: center;
    font-size: 12px;
    color: var(--text-muted);
    margin-top: 10px;
  }
  footer {
    display: flex;
    gap: 12px;
    flex-wrap: wrap;
    padding-top: 28px;
    padding-bottom: 36px;
  }
  .toc {
    padding: 40px 12px;
    overflow: auto;
    display: flex;
    flex-direction: column;
    gap: 10px;
    font-size: 12px;
  }
  .toc strong {
    padding-left: 10px;
  }
  .mobile-directory {
    display: none;
  }
  @media (max-width: 1100px) {
    .help-layout {
      grid-template-columns: 224px minmax(0, 1fr);
    }
    .toc {
      display: none;
    }
    main {
      padding: 28px;
    }
  }
  @media (max-width: 640px) {
    header {
      height: 60px;
      padding: 0 14px;
    }
    .brand {
      font-size: 12px;
      gap: 6px;
    }
    .help-layout {
      display: block;
      position: relative;
      overflow: hidden;
    }
    aside {
      display: none;
    }
    aside.expanded {
      display: block;
      position: absolute;
      inset: 62px 0 0;
      z-index: 1;
    }
    main {
      height: 100%;
      padding: 16px 20px;
    }
    .mobile-directory {
      display: block;
      margin-bottom: 22px;
    }
    h1 {
      font-size: 26px;
    }
  }
</style>
