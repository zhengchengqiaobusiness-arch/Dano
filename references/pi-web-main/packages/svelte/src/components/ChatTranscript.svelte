<script lang="ts">
  import { tick } from "svelte";
  import type {
    RpcImageContent,
    RpcTranscriptContent,
    RpcTranscriptContentBlock,
  } from "@pi-web/bridge/types";
  import Pencil from "lucide-svelte/icons/pencil";
  import Sparkle from "lucide-svelte/icons/sparkle";
  import type {
    TranscriptDelta,
    TranscriptEntry,
    TranscriptStream,
  } from "../composables/bridgeStore.svelte";
  import { userMessageCopyText } from "../utils/messageCopy";
  import {
    buildTranscriptDisplayItems,
    contentBlocks,
    errorMessageText,
    isAbortedMessage,
    isErrorMessage,
    isToolResultMessage,
    messageContent,
    type ImageContentBlock,
    type PendingTranscriptSessionEvent,
    type ToolContentBlock,
    type TranscriptDisplayItem,
    type TranscriptSessionEventDisplayItem,
  } from "../utils/transcript";
  import {
    createChatTranscriptBlockState,
    createChatTranscriptLightboxState,
  } from "./chatTranscriptBlockState.svelte";
  import DiffView from "./DiffView.svelte";
  import HighlightedCode from "./HighlightedCode.svelte";
  import ImageLightbox from "./ImageLightbox.svelte";
  import MarkdownRenderer from "./MarkdownRenderer.svelte";

  let {
    sessionPath = null as string | null,
    messages = [] as readonly TranscriptEntry[],
    transcriptDeltas = [] as readonly TranscriptDelta[],
    transcriptStreams = [] as readonly TranscriptStream[],
    hasOlder = false,
    initialLoading = false,
    pageLoading = false,
    pendingTranscriptConfigEvent = null as PendingTranscriptSessionEvent | null,
    isStreaming = false,
    isCompacting = false,
    showMessageIds = false,
    allowRevision = false,
    onLoadOlder = () => {},
    onRevise = (_: { entryId: string; text: string; preview: string; hasImages: boolean; images: RpcImageContent[] }) => {},
    onOpenFileReference = (_: { path: string; lineNumber: number }) => {},
    readWorkspaceFile,
  }: {
    sessionPath?: string | null;
    messages?: readonly TranscriptEntry[];
    transcriptDeltas?: readonly TranscriptDelta[];
    transcriptStreams?: readonly TranscriptStream[];
    hasOlder?: boolean;
    initialLoading?: boolean;
    pageLoading?: boolean;
    pendingTranscriptConfigEvent?: PendingTranscriptSessionEvent | null;
    isStreaming?: boolean;
    isCompacting?: boolean;
    showMessageIds?: boolean;
    allowRevision?: boolean;
    onLoadOlder?: () => void;
    onRevise?: (payload: { entryId: string; text: string; preview: string; hasImages: boolean; images: RpcImageContent[] }) => void;
    onOpenFileReference?: (payload: { path: string; lineNumber: number }) => void;
    readWorkspaceFile?: (path: string) => Promise<{ content: string }>;
  } = $props();

  // ---- DOM refs ----
  let container = $state<HTMLDivElement | null>(null);

  const BOTTOM_LOCK_THRESHOLD = 24;
  let shouldStickToBottom = true;
  let stickToBottomFrame = 0;
  let lastSessionPath: string | null | undefined = undefined;

  // ---- state modules ----
  const blockState = createChatTranscriptBlockState();
  const lightbox = createChatTranscriptLightboxState();

  // ---- derived ----
  let streamDisplayMessages = $derived.by(() => {
    const messageKeys = new Set(
      messages.map((message, index) => messageStableKey(message, index)),
    );

    return transcriptStreams
      .filter(stream => !messageKeys.has(messageStableKey(stream.message, -1)))
      .map(stream => messageWithTranscriptDeltas(stream.message, -1));
  });
  let displayItems = $derived(
    buildTranscriptDisplayItems(
      [...messages, ...streamDisplayMessages],
      { pendingSessionEvent: pendingTranscriptConfigEvent },
    ),
  );
  let hasVisibleStreaming = $derived(
    isStreaming || transcriptStreams.length > 0 || transcriptDeltas.length > 0,
  );
  let showBusyIndicator = $derived(hasVisibleStreaming || isCompacting);
  let streamingAssistantMessageIndex = $derived.by(() => {
    if (!hasVisibleStreaming) return -1;
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i]?.role === "assistant") return i;
    }
    return -1;
  });
  let busyIndicatorLabel = $derived(
    isCompacting && !hasVisibleStreaming ? "Compacting context" : "Responding",
  );

  // ---- display helpers ----
  function messageStableKey(msg: TranscriptEntry, index: number): string {
    return msg.transcriptKey ?? msg.id ?? `message:${index}`;
  }

  function deltasForMessage(msg: TranscriptEntry, index: number): readonly TranscriptDelta[] {
    const key = index >= 0 ? messageStableKey(msg, index) : msg.transcriptKey ?? msg.id ?? "";
    return transcriptDeltas.filter(delta => {
      if (delta.transcriptKey === key) return true;
      return Boolean(msg.id && delta.messageId === msg.id);
    });
  }

  function streamMatchesMessage(msg: TranscriptEntry, index: number): boolean {
    const key = index >= 0 ? messageStableKey(msg, index) : msg.transcriptKey ?? msg.id ?? "";
    return transcriptStreams.some(stream => {
      if (stream.message.transcriptKey === key) return true;
      return Boolean(msg.id && stream.message.id === msg.id);
    });
  }

  function cloneTranscriptContent(
    content: RpcTranscriptContent | undefined,
  ): RpcTranscriptContent | undefined {
    if (!Array.isArray(content)) return content;
    return content.map(item =>
      item && typeof item === "object" ? { ...item } : item,
    );
  }

  function defaultDeltaContentBlock(): RpcTranscriptContentBlock {
    // Fill index gaps with an invisible text placeholder so out-of-order
    // streamed blocks do not render phantom tool calls or thinking rows.
    return { type: "text", text: "" };
  }

  function appendDeltaToContentBlock(
    block: string | RpcTranscriptContentBlock | undefined,
    delta: TranscriptDelta,
  ): string | RpcTranscriptContentBlock {
    if (delta.blockType === "text") {
      if (typeof block === "string") return block + delta.delta;
      if (block?.type === "text") {
        return { ...block, text: `${block.text}${delta.delta}` };
      }
      return { type: "text", text: delta.delta };
    }

    if (delta.blockType === "thinking") {
      if (block && typeof block === "object" && block.type === "thinking") {
        return { ...block, thinking: `${block.thinking}${delta.delta}` };
      }
      return { type: "thinking", thinking: delta.delta };
    }

    if (block && typeof block === "object" && block.type === "toolCall") {
      const currentArguments =
        typeof block.arguments === "string"
          ? block.arguments
          : block.arguments
            ? JSON.stringify(block.arguments)
            : "";
      return {
        ...block,
        id: delta.toolCallId ?? block.id,
        name: delta.toolName ?? block.name,
        arguments: `${currentArguments}${delta.delta}`,
      };
    }

    return {
      type: "toolCall",
      id: delta.toolCallId,
      name: delta.toolName ?? "tool",
      arguments: delta.delta,
    };
  }

  function messageWithTranscriptDeltas(
    msg: TranscriptEntry,
    index: number,
  ): TranscriptEntry {
    const deltas = deltasForMessage(msg, index);
    if (deltas.length === 0) return msg;

    let content = cloneTranscriptContent(msg.content);
    for (const delta of deltas) {
      if (
        delta.blockType === "text" &&
        delta.contentIndex === 0 &&
        typeof content === "string"
      ) {
        content += delta.delta;
        continue;
      }

      const contentItems = Array.isArray(content)
        ? content.slice()
        : typeof content === "string"
          ? [{ type: "text" as const, text: content }]
          : [];

      while (contentItems.length <= delta.contentIndex) {
        contentItems.push(defaultDeltaContentBlock());
      }

      contentItems[delta.contentIndex] = appendDeltaToContentBlock(
        contentItems[delta.contentIndex],
        delta,
      );
      content = contentItems;
    }

    return {
      ...msg,
      id: msg.id ?? deltas.at(-1)?.messageId,
      role: deltas.at(-1)?.role ?? msg.role,
      content,
    };
  }

  function displayContentBlocks(msg: TranscriptEntry, index: number) {
    // Stream display messages already have deltas applied before they enter
    // `displayItems`, so avoid replaying the same deltas a second time.
    if (index >= messages.length) return contentBlocks(msg);
    return contentBlocks(messageWithTranscriptDeltas(msg, index));
  }

  function toolBlockIdentity(block: ToolContentBlock, blockIndex: number): string {
    // Keep tool detail state stable even when streamed tool calls later gain ids
    // or finish filling in arguments during the final transcript upsert.
    return `tool-call:${block.toolName}:${blockIndex}`;
  }

  function contentBlockKey(
    msg: TranscriptEntry,
    messageIndex: number,
    block: ReturnType<typeof contentBlocks>[number],
    blockIndex: number,
  ): string {
    const messageKey = messageStableKey(msg, messageIndex);
    switch (block.kind) {
      case "tool":
        return `${messageKey}:${toolBlockIdentity(block, blockIndex)}`;
      case "image":
        return `${messageKey}:image:${block.src}:${blockIndex}`;
      case "system":
        return `${messageKey}:system:${block.systemType}:${block.title}:${blockIndex}`;
      case "thinking":
        return `${messageKey}:thinking:${blockIndex}`;
      case "text":
        return `${messageKey}:text:${blockIndex}`;
    }
  }

  function toolBlockStateKey(
    msg: TranscriptEntry,
    messageIndex: number,
    block: ToolContentBlock,
    blockIndex: number,
  ): string {
    return contentBlockKey(msg, messageIndex, block, blockIndex);
  }

  function thinkingBlockStateKey(
    msg: TranscriptEntry,
    messageIndex: number,
    blockIndex: number,
  ): string {
    return `${messageStableKey(msg, messageIndex)}:thinking:${blockIndex}`;
  }

  function displayItemKey(item: TranscriptDisplayItem, index: number): string {
    return item.kind === "message"
      ? messageStableKey(item.message, item.messageIndex)
      : item.key || `session-event:${index}`;
  }

  function sessionEventModelText(item: TranscriptSessionEventDisplayItem): string {
    if (!item.model) return "";
    return item.model.provider
      ? `${item.model.provider} / ${item.model.id}`
      : item.model.id;
  }

  function roleClass(role: string): "user" | "assistant" | "tool" | "system" {
    if (role === "user") return "user";
    if (role === "assistant") return "assistant";
    if (role === "system") return "system";
    return "tool";
  }

  function shouldDeferMessageMarkdownErrors(msg: TranscriptEntry, mi: number): boolean {
    return (
      msg.role === "assistant" &&
      (mi === streamingAssistantMessageIndex ||
        streamMatchesMessage(msg, mi) ||
        deltasForMessage(msg, mi).length > 0)
    );
  }

  function previewText(text: string, maxLines: number = 8): string {
    const normalized = text.replace(/\r/g, "").trim();
    if (!normalized) return "";
    const lines = normalized.split("\n");
    if (lines.length <= maxLines) return normalized;
    const remaining = lines.length - maxLines;
    return `${lines.slice(0, maxLines).join("\n")}\n... ${remaining} more line${remaining === 1 ? "" : "s"}`;
  }

  function compactInlineText(text: string | undefined, maxLength: number = 96): string | undefined {
    if (!text) return undefined;
    const singleLine = text.replace(/\s+/g, " ").trim();
    if (!singleLine) return undefined;
    if (singleLine.length <= maxLength) return singleLine;
    return `${singleLine.slice(0, maxLength - 3).trimEnd()}...`;
  }

  function toolBlockDescriptor(block: ToolContentBlock) {
    const model = blockState.toolBlockModel(block);
    return {
      name: block.toolName || "tool",
      params: model.title !== model.label ? model.title : undefined,
      meta: model.meta ?? toolStatusMeta(block.toolStatus),
      status: block.toolStatus,
    };
  }

  function toolBlockDiffStats(block: ToolContentBlock) {
    return blockState.toolBlockModel(block).diffStats;
  }

  function toolBlockTrailingKind(block: ToolContentBlock): "diff" | "meta" | "empty" {
    if (toolBlockDiffStats(block)) return "diff";
    if (toolBlockDescriptor(block).meta) return "meta";
    return "empty";
  }

  function toolBlockTrailingHidden(kind: "diff" | "meta" | "empty", target: "diff" | "meta") {
    return kind === "empty" || kind !== target;
  }

  function toolStatusMeta(status: ToolContentBlock["toolStatus"] | "success" | "error"): string | undefined {
    if (status === "pending") return "running";
    if (status === "error") return "error";
    return undefined;
  }

  function toolBlockImages(block: ToolContentBlock): ImageContentBlock[] {
    return (block.resultBlocks ?? []).filter(
      (item): item is ImageContentBlock => item.kind === "image",
    );
  }

  function toolBlockEmptyState(block: ToolContentBlock): string {
    if (block.toolStatus === "pending") return "Waiting for tool result.";
    if (block.toolName === "write" && blockState.toolBlockDetail(block).kind === "empty")
      return "File is empty.";
    return "No text result.";
  }

  function toolResultText(msg: TranscriptEntry): string {
    if (msg.toolName === "read" && toolResultImages(msg).length > 0) return "";
    return contentBlocks(msg)
      .flatMap(block => (block.kind === "text" ? [block.text] : []))
      .join("\n");
  }

  function toolResultPreview(msg: TranscriptEntry): string {
    return previewText(toolResultText(msg), 6);
  }

  function toolResultImages(msg: TranscriptEntry): ImageContentBlock[] {
    return contentBlocks(msg).filter(
      (block): block is ImageContentBlock => block.kind === "image",
    );
  }

  function toolResultName(msg: TranscriptEntry): string {
    return msg.toolName?.trim() || "tool";
  }

  function toolResultMeta(msg: TranscriptEntry): string | undefined {
    const preview = compactInlineText(toolResultPreview(msg));
    if (preview) return preview;
    const images = toolResultImages(msg);
    if (images.length > 0) return `${images.length} image${images.length === 1 ? "" : "s"}`;
    return toolStatusMeta(msg.isError ? "error" : "success");
  }

  function errorSummaryLabel(msg: TranscriptEntry): string {
    return isAbortedMessage(msg) ? "cancelled" : "error";
  }

  function errorSummaryMeta(msg: TranscriptEntry): string | undefined {
    return compactInlineText(errorMessageText(msg), 120);
  }

  function messageIdLabel(msg: TranscriptEntry): string {
    return msg.id ?? "missing";
  }

  // ---- user message helpers ----
  function userMessageText(msg: TranscriptEntry): string {
    return messageContent(msg).trim();
  }

  function messageImages(msg: TranscriptEntry): RpcImageContent[] {
    if (!Array.isArray(msg.content)) return [];
    return msg.content.flatMap(item => {
      if (typeof item !== "object" || item === null) return [];
      const block = item as { type?: unknown; data?: unknown; mimeType?: unknown };
      if (
        block.type !== "image" ||
        typeof block.data !== "string" ||
        typeof block.mimeType !== "string"
      )
        return [];
      return [{ type: "image" as const, data: block.data, mimeType: block.mimeType }];
    });
  }

  function revisionPreview(text: string, maxLength: number = 96): string {
    const collapsed = text.replace(/\s+/g, " ").trim();
    if (collapsed.length <= maxLength) return collapsed;
    return `${collapsed.slice(0, maxLength - 1).trimEnd()}…`;
  }

  function canReviseMessage(msg: TranscriptEntry): msg is TranscriptEntry & { id: string } {
    return Boolean(
      allowRevision &&
        !showBusyIndicator &&
        msg.role === "user" &&
        typeof msg.id === "string" &&
        userMessageText(msg),
    );
  }

  function handleRevise(msg: TranscriptEntry) {
    if (!canReviseMessage(msg)) return;
    const text = userMessageText(msg);
    const images = messageImages(msg);
    onRevise({
      entryId: msg.id,
      text,
      preview: revisionPreview(text),
      hasImages: images.length > 0,
      images,
    });
  }

  function requestOlderTranscript() {
    if (!hasOlder || initialLoading || pageLoading) return;
    onLoadOlder();
  }

  function distanceFromBottom(el: HTMLElement): number {
    return el.scrollHeight - el.clientHeight - el.scrollTop;
  }

  function isNearBottom(el: HTMLElement): boolean {
    return distanceFromBottom(el) <= BOTTOM_LOCK_THRESHOLD;
  }

  function updateBottomLock() {
    if (!container) return;
    shouldStickToBottom = isNearBottom(container);
  }

  function scrollTranscriptToBottom() {
    if (!container) return;
    container.scrollTop = container.scrollHeight;
    updateBottomLock();
  }

  function scheduleStickToBottom() {
    if (stickToBottomFrame) cancelAnimationFrame(stickToBottomFrame);
    stickToBottomFrame = requestAnimationFrame(() => {
      stickToBottomFrame = 0;
      scrollTranscriptToBottom();
    });
  }

  function handleTranscriptScroll() {
    updateBottomLock();
  }

  export function preserveBottomPosition(gracePx: number = 48): boolean {
    const el = container;
    if (!el) return false;
    if (!shouldStickToBottom && distanceFromBottom(el) > BOTTOM_LOCK_THRESHOLD + gracePx) {
      updateBottomLock();
      return false;
    }
    shouldStickToBottom = true;
    tick().then(() => {
      if (container !== el || !shouldStickToBottom) return;
      scheduleStickToBottom();
    });
    return true;
  }

  function cssEscape(value: string): string {
    if (typeof CSS !== "undefined" && typeof CSS.escape === "function") {
      return CSS.escape(value);
    }
    return value.replace(/["\\]/g, "\\$&");
  }

  export function scrollToTranscriptEntry(entryId: string): boolean {
    const el = container;
    if (!el) return false;

    const selector = `[data-tree-entry-id="${cssEscape(entryId)}"], [data-tree-entry-ids~="${cssEscape(entryId)}"]`;
    const target = el.querySelector<HTMLElement>(selector);
    if (!target) return false;

    target.scrollIntoView({ block: "center" });
    updateBottomLock();
    return true;
  }

  $effect(() => {
    const el = container;
    if (!el) return;
    updateBottomLock();
  });

  $effect(() => {
    const el = container;
    if (!el || typeof ResizeObserver === "undefined") return;

    let lastHeight = el.clientHeight;
    const observer = new ResizeObserver(() => {
      const keepBottomLocked = shouldStickToBottom;
      const nextHeight = el.clientHeight;
      if (nextHeight === lastHeight) return;
      lastHeight = nextHeight;
      if (keepBottomLocked) scheduleStickToBottom();
      else updateBottomLock();
    });

    observer.observe(el);
    return () => observer.disconnect();
  });

  $effect(() => {
    const el = container;
    const path = sessionPath;
    if (!el || lastSessionPath === path) return;

    lastSessionPath = path;
    shouldStickToBottom = true;
    tick().then(() => {
      if (container !== el || sessionPath !== path) return;
      scheduleStickToBottom();
      updateBottomLock();
    });
  });

  $effect(() => {
    const el = container;
    void messages;
    void transcriptDeltas;
    void transcriptStreams;
    void pendingTranscriptConfigEvent;
    void showBusyIndicator;
    if (!el || !shouldStickToBottom) return;

    tick().then(() => {
      if (container !== el || !shouldStickToBottom) return;
      scheduleStickToBottom();
    });
  });

  $effect(() => {
    return () => {
      if (!stickToBottomFrame) return;
      cancelAnimationFrame(stickToBottomFrame);
      stickToBottomFrame = 0;
    };
  });

  // ---- copy handling ----
  const userCopySelector = "[data-user-message-index]";

  function userMessageElementForNode(node: Node | null): HTMLElement | null {
    const root = container;
    if (!root || !node) return null;
    const el = node instanceof Element ? node : node.parentElement;
    const candidate = el?.closest<HTMLElement>(userCopySelector) ?? null;
    if (!candidate || !root.contains(candidate)) return null;
    return candidate;
  }

  function selectedUserMessageElements(selection: Selection): HTMLElement[] {
    const root = container;
    if (!root || selection.rangeCount === 0 || selection.isCollapsed) return [];
    const elements = new Set<HTMLElement>();
    const userElements = root.querySelectorAll<HTMLElement>(userCopySelector);
    for (let i = 0; i < selection.rangeCount; i++) {
      const range = selection.getRangeAt(i);
      if (!range.intersectsNode(root)) continue;
      for (const el of userElements) {
        if (range.intersectsNode(el)) elements.add(el);
      }
      const startEl = userMessageElementForNode(range.startContainer);
      const endEl = userMessageElementForNode(range.endContainer);
      if (startEl) elements.add(startEl);
      if (endEl) elements.add(endEl);
    }
    return [...elements];
  }

  function selectedUserCopyText(selection: Selection | null): string | null {
    if (!selection || selection.rangeCount === 0 || selection.isCollapsed) return null;
    const [el, extra] = selectedUserMessageElements(selection);
    if (!el || extra) return null;
    const mi = Number(el.dataset.userMessageIndex);
    const msg = Number.isInteger(mi) ? messages[mi] : undefined;
    if (!msg) return null;
    return userMessageCopyText(msg, selection.toString(), el.innerText);
  }

  function handleCopy(event: ClipboardEvent) {
    const text = selectedUserCopyText(window.getSelection());
    if (!text || !event.clipboardData) return;
    event.clipboardData.setData("text/plain", text);
    event.preventDefault();
  }

</script>

<svelte:document oncopy={handleCopy} />

<div bind:this={container} class="chat-transcript" onscroll={handleTranscriptScroll}>
  {#if initialLoading}
    <div class="empty-state loading-state">
      <p class="empty-title">Loading conversation</p>
      <p class="empty-subtitle">Fetching the latest transcript window.</p>
    </div>
  {:else if messages.length === 0}
    <div class="empty-state">
      <p class="empty-title">Start a conversation</p>
      <p class="empty-subtitle">Start typing to keep the session moving.</p>
      <div class="empty-hints">
        <span class="hint-chip">Enter send / steer</span>
        <span class="hint-chip">Drop or paste images</span>
      </div>
    </div>
  {/if}

  {#if !initialLoading && hasOlder}
    <div class="history-loader">
      <button
        type="button"
        class="history-loader-button"
        disabled={pageLoading}
        onclick={requestOlderTranscript}
      >
        {pageLoading ? "Loading earlier messages..." : "Load earlier messages"}
      </button>
    </div>
  {/if}

  {#each displayItems as item, index (displayItemKey(item, index))}
    {#if item.kind === "session_event"}
      <div
        class="session-event-row"
        data-tree-entry-ids={item.sourceMessageIds.join(" ") || undefined}
      >
        <div class="session-event-line" aria-hidden="true"></div>
        <div class="session-event-body">
          <span class="session-event-label">{item.label}</span>
          {#if item.model}
            <span class="session-event-chip">
              <span class="session-event-chip-label">Model</span>
              <span class="session-event-chip-value">{sessionEventModelText(item)}</span>
            </span>
          {/if}
          {#if item.thinkingLevel}
            <span class="session-event-chip">
              <span class="session-event-chip-label">Thinking</span>
              <span class="session-event-chip-value">{item.thinkingLevel}</span>
            </span>
          {/if}
          {#if showMessageIds && item.sourceMessageIds.length > 0}
            <span class="session-event-debug">IDs {item.sourceMessageIds.join(", ")}</span>
          {/if}
        </div>
        <div class="session-event-line" aria-hidden="true"></div>
      </div>
    {:else if isToolResultMessage(item.message)}
      <div
        class="message-row tool"
        data-message-id={item.message.id ?? undefined}
        data-tree-entry-id={item.message.id ?? undefined}
      >
        <div class="message-content tool">
          <div class="tool-inline" data-status={item.message.isError ? "error" : "success"}>
            <button
              type="button"
              class="tool-inline-toggle"
              onclick={() => blockState.toggleToolBlock(`${messageStableKey(item.message, item.messageIndex)}:tool-result`)}
              aria-expanded={blockState.isToolBlockExpanded(`${messageStableKey(item.message, item.messageIndex)}:tool-result`)}
            >
              <span class="tool-inline-summary">
                <span class="tool-inline-name">{toolResultName(item.message)}</span>
              </span>
              {#if toolResultMeta(item.message)}
                <span class="tool-inline-meta">{toolResultMeta(item.message)}</span>
              {/if}
            </button>

            {#if blockState.isToolBlockExpanded(`${messageStableKey(item.message, item.messageIndex)}:tool-result`)}
              <div class="tool-inline-details">
                {#if showMessageIds}
                  <span class="message-debug-id">ID {messageIdLabel(item.message)}</span>
                {/if}

                {#if toolResultImages(item.message).length > 0}
                  <div class="tool-inline-images">
                    {#each toolResultImages(item.message) as image, imgIdx (`${image.src}-${imgIdx}`)}
                      <figure class="message-image-block">
                        <button
                          type="button"
                          class="message-image-button"
                          aria-label={`Open image ${imgIdx + 1}`}
                          onclick={() => lightbox.openImageLightbox(toolResultImages(item.message), imgIdx)}
                        >
                          <img
                            class="message-image"
                            src={image.src}
                            alt={image.alt}
                            loading="lazy"
                          />
                        </button>
                      </figure>
                    {/each}
                  </div>
                {/if}

                {#if toolResultText(item.message).trim()}
                  <section class="tool-inline-section">
                    {#if toolResultName(item.message) === "bash"}
                      <div class="tool-inline-code-panel">
                        <pre class="tool-inline-code-output">{toolResultText(item.message)}</pre>
                      </div>
                    {:else}
                      <pre class="tool-inline-pre">{toolResultText(item.message)}</pre>
                    {/if}
                  </section>
                {:else if toolResultImages(item.message).length === 0}
                  <div class="tool-inline-empty">No text result.</div>
                {/if}
              </div>
            {/if}
          </div>
        </div>
      </div>
    {:else if isErrorMessage(item.message)}
      <div
        class="message-row {roleClass(item.message.role)}"
        data-message-id={item.message.id ?? undefined}
        data-tree-entry-id={item.message.id ?? undefined}
      >
        <div class="message-content {roleClass(item.message.role)}">
          <div class="tool-inline" data-status="error">
            <button
              type="button"
              class="tool-inline-toggle"
              onclick={() => blockState.toggleToolBlock(`${messageStableKey(item.message, item.messageIndex)}:error`)}
              aria-expanded={blockState.isToolBlockExpanded(`${messageStableKey(item.message, item.messageIndex)}:error`)}
            >
              <span class="tool-inline-summary">
                <span class="tool-inline-name">{errorSummaryLabel(item.message)}</span>
              </span>
              {#if errorSummaryMeta(item.message)}
                <span class="tool-inline-meta">{errorSummaryMeta(item.message)}</span>
              {/if}
            </button>

            {#if blockState.isToolBlockExpanded(`${messageStableKey(item.message, item.messageIndex)}:error`)}
              <div class="tool-inline-details">
                {#if showMessageIds}
                  <span class="message-debug-id">ID {messageIdLabel(item.message)}</span>
                {/if}
                {#if errorMessageText(item.message)}
                  <section class="tool-inline-section">
                    <pre class="tool-inline-pre">{errorMessageText(item.message)}</pre>
                  </section>
                {:else}
                  <div class="tool-inline-empty">No error message.</div>
                {/if}
              </div>
            {/if}
          </div>
        </div>
      </div>
    {:else}
      <div
        class="message-row {roleClass(item.message.role)}"
        data-message-id={item.message.id ?? undefined}
        data-tree-entry-id={item.message.id ?? undefined}
      >
        <div class="message-stack {roleClass(item.message.role)}">
          <div
            class="message-content {roleClass(item.message.role)}"
            data-user-message-index={item.message.role === "user" ? item.messageIndex : undefined}
          >
            {#if showMessageIds}
              <div class="message-debug-id">ID {messageIdLabel(item.message)}</div>
            {/if}

            {#each displayContentBlocks(item.message, item.messageIndex) as block, bIdx (contentBlockKey(item.message, item.messageIndex, block, bIdx))}
              {#if block.kind === "system"}
                <article class="system-block" data-system-type={block.systemType}>
                  <div class="system-block-header">
                    <span class="system-block-label">{block.label}</span>
                    {#if block.meta}
                      <span class="system-block-meta">{block.meta}</span>
                    {/if}
                  </div>
                  <div class="system-block-title">{block.title}</div>
                  {#if block.body}
                    <MarkdownRenderer
                      class="system-block-body"
                      content={block.body}
                      onOpenFileReference={onOpenFileReference}
                    />
                  {/if}
                </article>
              {:else if block.kind === "thinking"}
                <div class="thinking-block">
                  <button
                    class="thinking-toggle"
                    onclick={() => blockState.toggleThinking(thinkingBlockStateKey(item.message, item.messageIndex, bIdx))}
                  >
                    <Sparkle class="toggle-icon" aria-hidden="true" size={14} />
                    Thinking
                  </button>
                  {#if blockState.isThinkingExpanded(thinkingBlockStateKey(item.message, item.messageIndex, bIdx))}
                    <MarkdownRenderer
                      class="thinking-content"
                      content={block.text}
                      streaming={shouldDeferMessageMarkdownErrors(item.message, item.messageIndex)}
                      deferMermaidErrors={shouldDeferMessageMarkdownErrors(item.message, item.messageIndex)}
                      onOpenFileReference={onOpenFileReference}
                    />
                  {/if}
                </div>
              {:else if block.kind === "tool"}
                {@const descriptor = toolBlockDescriptor(block)}
                {@const diffStats = toolBlockDiffStats(block)}
                {@const trailingKind = toolBlockTrailingKind(block)}
                <div class="tool-inline-block" data-tree-entry-id={block.resultSourceMessageId}>
                  <div class="tool-inline" data-status={descriptor.status}>
                    <button
                      type="button"
                      class="tool-inline-toggle"
                      onclick={() => blockState.toggleToolBlock(toolBlockStateKey(item.message, item.messageIndex, block, bIdx))}
                      aria-expanded={blockState.isToolBlockExpanded(toolBlockStateKey(item.message, item.messageIndex, block, bIdx))}
                    >
                      <span class="tool-inline-summary">
                        <span class="tool-inline-name">{descriptor.name}</span>
                        {#if descriptor.params}
                          <span class="tool-inline-params">{descriptor.params}</span>
                        {/if}
                      </span>
                      <span class="tool-inline-trailing" hidden={trailingKind === "empty"}>
                        <span
                          class="tool-inline-meta"
                          hidden={toolBlockTrailingHidden(trailingKind, "meta")}
                          aria-hidden={trailingKind !== "meta"}
                        >{descriptor.meta ?? ""}</span>
                        <span
                          class="tool-inline-diff"
                          hidden={toolBlockTrailingHidden(trailingKind, "diff")}
                          aria-hidden={trailingKind !== "diff"}
                          aria-label={`${diffStats?.added ?? 0} additions, ${diffStats?.removed ?? 0} deletions`}
                        >
                          <span class="tool-inline-diff-added">+{diffStats?.added ?? 0}</span>
                          <span class="tool-inline-diff-removed">-{diffStats?.removed ?? 0}</span>
                        </span>
                      </span>
                    </button>

                    {#if blockState.isToolBlockExpanded(toolBlockStateKey(item.message, item.messageIndex, block, bIdx))}
                      <div class="tool-inline-details">
                        {#if showMessageIds && block.resultSourceMessageId}
                          <span class="message-debug-id">ID {block.resultSourceMessageId}</span>
                        {/if}

                        {#if toolBlockImages(block).length > 0}
                          <div class="tool-inline-images">
                            {#each toolBlockImages(block) as image, imgIdx (`${image.src}-${imgIdx}`)}
                              <figure class="message-image-block">
                                <button
                                  type="button"
                                  class="message-image-button"
                                  aria-label={`Open image ${imgIdx + 1}`}
                                  onclick={() => lightbox.openImageLightbox(toolBlockImages(block), imgIdx)}
                                >
                                  <img class="message-image" src={image.src} alt={image.alt} loading="lazy" />
                                </button>
                              </figure>
                            {/each}
                          </div>
                        {/if}

                        {#if blockState.toolBlockDetail(block).kind !== "empty"}
                          <section class="tool-inline-section">
                            {#if blockState.toolBlockDetail(block).kind === "diff"}
                              <DiffView
                                diff={blockState.toolBlockDetail(block).text || ""}
                                path={blockState.toolBlockDetail(block).path}
                                edits={blockState.toolBlockDetail(block).edits || []}
                                {readWorkspaceFile}
                              />
                            {:else if blockState.toolBlockDetail(block).kind === "code"}
                              <div class="tool-inline-code-panel">
                                <HighlightedCode
                                  code={blockState.toolBlockDetail(block).text || ""}
                                  path={blockState.toolBlockDetail(block).path}
                                />
                              </div>
                            {:else if blockState.toolBlockDetail(block).kind === "bash"}
                              <div class="tool-inline-code-panel">
                                {#if blockState.toolBlockDetail(block).command}
                                  <pre class="tool-inline-code-output tool-inline-command-output">{blockState.toolBlockDetail(block).command}</pre>
                                {/if}
                                {#if blockState.toolBlockDetail(block).text}
                                  <pre class="tool-inline-code-output">{blockState.toolBlockDetail(block).text}</pre>
                                {/if}
                              </div>
                            {:else}
                              <pre class="tool-inline-pre">{blockState.toolBlockDetail(block).text}</pre>
                            {/if}
                          </section>
                        {:else if toolBlockImages(block).length === 0}
                          <div class="tool-inline-empty">{toolBlockEmptyState(block)}</div>
                        {/if}
                      </div>
                    {/if}
                  </div>
                </div>
              {:else if block.kind === "image"}
                <figure class="message-image-block">
                  <button
                    type="button"
                    class="message-image-button"
                    aria-label="Open image"
                    onclick={() => lightbox.openImageLightbox([block])}
                  >
                    <img class="message-image" src={block.src} alt={block.alt} loading="lazy" />
                  </button>
                </figure>
              {:else if block.kind === "text" && block.text}
                <MarkdownRenderer
                  content={block.text}
                  streaming={shouldDeferMessageMarkdownErrors(item.message, item.messageIndex)}
                  deferMermaidErrors={shouldDeferMessageMarkdownErrors(item.message, item.messageIndex)}
                  onOpenFileReference={onOpenFileReference}
                />
              {/if}
            {/each}
          </div>

          {#if canReviseMessage(item.message)}
            <div class="message-actions">
              <button
                type="button"
                class="message-action-button"
                aria-label="Edit message"
                title="Edit message"
                onclick={() => handleRevise(item.message)}
              >
                <Pencil class="message-action-icon" aria-hidden="true" size={14} />
              </button>
            </div>
          {/if}
        </div>
      </div>
    {/if}
  {/each}

  {#if showBusyIndicator}
    <div class="message-row assistant streaming-indicator-row">
      <div class="message-content assistant">
        <div class="streaming-indicator">
          <span class="busy-label">{busyIndicatorLabel}</span>
          <span class="dot"></span>
          <span class="dot"></span>
          <span class="dot"></span>
        </div>
      </div>
    </div>
  {/if}

  <ImageLightbox
    open={lightbox.lightboxImages.length > 0}
    images={lightbox.lightboxImages}
    index={lightbox.lightboxIndex}
    onClose={lightbox.closeImageLightbox}
    onPrevious={lightbox.showPreviousLightboxImage}
    onNext={lightbox.showNextLightboxImage}
  />
</div>

<style>
  .chat-transcript {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    padding: 24px 32px 12px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    background: transparent;
    scrollbar-width: none;
    /* Use explicit scroll restoration instead of browser anchoring. */
    overflow-anchor: none;
  }

  .chat-transcript::-webkit-scrollbar { display: none; }

  .empty-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 10px;
    flex: 1;
    text-align: center;
    color: var(--text-muted);
  }

  .loading-state { min-height: 240px; }

  .empty-title {
    margin: 0;
    font-size: 1.1rem;
    font-weight: 500;
    color: var(--text);
  }

  .empty-subtitle {
    margin: 0;
    max-width: 420px;
    font-size: 0.85rem;
    line-height: 1.6;
    color: var(--text-subtle);
  }

  .empty-hints {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    justify-content: center;
  }

  .hint-chip {
    display: inline-flex;
    align-items: center;
    height: 24px;
    padding: 0 10px;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: var(--panel);
    font-size: 0.68rem;
    color: var(--text-subtle);
  }

  .history-loader {
    display: flex;
    justify-content: center;
    width: 100%;
  }

  .history-loader-button {
    border: 1px solid var(--border);
    border-radius: 999px;
    background: var(--panel);
    color: var(--text-subtle);
    padding: 8px 14px;
    font-size: 0.74rem;
    cursor: pointer;
  }

  .history-loader-button:hover:not(:disabled) {
    border-color: var(--border-strong);
    color: var(--text);
  }

  .history-loader-button:disabled {
    opacity: 0.7;
    cursor: progress;
  }

  .session-event-row {
    width: 100%;
    max-width: 920px;
    margin: 0 auto;
    display: grid;
    grid-template-columns: minmax(24px, 1fr) auto minmax(24px, 1fr);
    align-items: center;
    gap: 12px;
  }

  .session-event-line {
    height: 1px;
    background: color-mix(in srgb, var(--border) 88%, transparent);
  }

  .session-event-body {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex-wrap: wrap;
    gap: 10px;
    min-width: 0;
  }

  .session-event-label {
    font-size: 0.66rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-subtle);
  }

  .session-event-chip {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 4px 10px;
    border: 1px solid color-mix(in srgb, var(--border) 72%, transparent);
    border-radius: 10px;
    background: color-mix(in srgb, var(--panel) 76%, transparent);
    box-shadow: none;
    font-size: 0.72rem;
    line-height: 1.2;
    color: var(--text-muted);
  }

  .session-event-chip-label {
    display: inline-flex;
    align-items: center;
    padding-right: 8px;
    border-right: 1px solid color-mix(in srgb, var(--border) 68%, transparent);
    font-size: 0.58rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    line-height: 1;
    color: var(--text-subtle);
  }

  .session-event-chip-value {
    display: inline-flex;
    align-items: center;
    font-weight: 500;
    line-height: 1.1;
    color: var(--text);
  }

  .session-event-debug {
    font-family: var(--pi-font-mono);
    font-size: 0.64rem;
    color: var(--text-subtle);
  }

  .message-row {
    width: 100%;
    max-width: 920px;
    margin: 0 auto;
  }

  .message-row.assistant,
  .message-row.user,
  .message-row.system { display: flex; }

  .message-row.user { justify-content: flex-end; }

  .message-row.system { justify-content: center; }

  .message-row.tool {
    display: flex;
    overflow-anchor: none;
  }

  .message-stack {
    min-width: 0;
    width: 100%;
  }

  .message-stack.user {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
  }

  .message-content {
    min-width: 0;
    font-size: 0.9rem;
    line-height: 1.7;
    color: var(--text);
    word-break: break-word;
  }

  .message-content.assistant,
  .message-content.tool,
  .message-content.system {
    width: 100%;
    padding-left: 10px;
  }

  .message-debug-id {
    display: inline-flex;
    align-items: center;
    margin: 0 0 10px;
    padding: 4px 8px;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: color-mix(in srgb, var(--panel) 88%, transparent);
    font-family: var(--pi-font-mono);
    font-size: 0.66rem;
    line-height: 1;
    color: var(--text-subtle);
  }

  .message-actions {
    display: flex;
    justify-content: flex-end;
    width: fit-content;
    max-width: min(720px, 100%);
    margin: 2px 0px 0 0;
  }

  .message-action-button {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    border-radius: 999px;
    border: none;
    background: transparent;
    color: var(--text-subtle);
    cursor: pointer;
    opacity: 0;
    pointer-events: none;
    transform: translateY(-2px);
    transition:
      opacity 0.14s ease,
      color 0.14s ease,
      background 0.14s ease,
      transform 0.14s ease;
  }

  .message-stack.user:hover .message-action-button,
  .message-stack.user:focus-within .message-action-button {
    opacity: 1;
    pointer-events: auto;
    transform: translateY(0);
  }

  .message-action-button:hover,
  .message-action-button:focus-visible {
    background: var(--surface-hover);
    color: var(--text);
  }

  .message-content.user {
    width: fit-content;
    max-width: min(720px, 100%);
    margin-left: auto;
    padding: 12px 16px;
    border: 1px solid var(--border);
    border-radius: 18px 18px 18px 18px;
    background: var(--panel);
  }

  :global(.markdown-body) + :global(.markdown-body),
  :global(.markdown-body) + .thinking-block,
  :global(.markdown-body) + .tool-inline-block,
  :global(.markdown-body) + .message-image-block,
  :global(.markdown-body) + .system-block,
  .thinking-block + :global(.markdown-body),
  .thinking-block + .thinking-block,
  .thinking-block + .tool-inline-block,
  .thinking-block + .message-image-block,
  .thinking-block + .system-block,
  .tool-inline-block + :global(.markdown-body),
  .tool-inline-block + .thinking-block,
  .tool-inline-block + .message-image-block,
  .tool-inline-block + .system-block,
  .message-image-block + :global(.markdown-body),
  .message-image-block + .thinking-block,
  .message-image-block + .tool-inline-block,
  .message-image-block + .message-image-block,
  .message-image-block + .system-block,
  .system-block + :global(.markdown-body),
  .system-block + .thinking-block,
  .system-block + .tool-inline-block,
  .system-block + .message-image-block,
  .system-block + .system-block {
    margin-top: 4px;
  }

  .tool-inline-block + .tool-inline-block {
    margin-top: 6px;
  }

  .tool-inline-block,
  .tool-inline-code-panel {
    overflow-anchor: none;
  }

  .thinking-block { padding-left: 0; }

  .system-block {
    display: flex;
    flex-direction: column;
    gap: 8px;
    max-width: min(720px, 100%);
    margin: 0 auto;
    padding: 12px 14px;
    border: 1px solid color-mix(in srgb, var(--border) 88%, transparent);
    border-radius: 14px;
    background: color-mix(in srgb, var(--panel) 86%, transparent);
  }

  .system-block-header {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 10px;
  }

  .system-block-label,
  .system-block-meta {
    font-size: 0.66rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-subtle);
  }

  .system-block-title {
    font-size: 0.8rem;
    line-height: 1.5;
    color: var(--text);
  }

  .system-block-body {
    margin: 0;
    color: var(--text-muted);
    font-size: 0.76rem;
    line-height: 1.6;
  }

  .message-image-block { margin: 0; }

  .message-image-button {
    display: block;
    padding: 0;
    border: none;
    background: transparent;
    cursor: zoom-in;
  }

  .message-image {
    display: block;
    max-width: min(100%, 420px);
    max-height: 320px;
    border: 1px solid var(--border);
    border-radius: 14px;
    background: color-mix(in srgb, var(--panel) 88%, transparent);
    box-shadow: var(--shadow-raised);
    object-fit: contain;
    transition:
      transform 0.16s ease,
      box-shadow 0.16s ease,
      border-color 0.16s ease;
  }

  .message-image-button:hover .message-image,
  .message-image-button:focus-visible .message-image {
    transform: translateY(-1px) scale(1.01);
    border-color: var(--accent);
    box-shadow:
      0 0 0 3px var(--focus-ring),
      var(--shadow-floating);
  }

  .thinking-toggle {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 0;
    background: none;
    border: none;
    color: var(--text-muted);
    font-size: 0.7rem;
    line-height: 1.3;
    cursor: pointer;
  }

  .thinking-toggle:hover { color: var(--text); }

  .thinking-content {
    margin: 0;
    padding: 0;
    font-size: 0.74rem;
    line-height: 1.55;
    color: var(--text-muted);
    max-height: 400px;
    overflow-y: auto;
    word-break: break-word;
  }

  .tool-inline {
    display: flex;
    flex-direction: column;
    gap: 4px;
    /* Avoid browser scroll anchoring jumps when streamed tool output re-renders. */
    overflow-anchor: none;
  }

  .tool-inline-toggle {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: baseline;
    column-gap: 8px;
    width: 100%;
    padding: 0;
    border: none;
    background: none;
    color: inherit;
    text-align: left;
    cursor: pointer;
  }

  .tool-inline-toggle:hover .tool-inline-name,
  .tool-inline-toggle:hover .tool-inline-params,
  .tool-inline-toggle:hover .tool-inline-meta { color: var(--text); }

  .tool-inline-summary {
    display: inline-flex;
    align-items: baseline;
    gap: 8px;
    min-width: 0;
  }

  .tool-inline-name {
    flex: none;
    font-family: var(--pi-font-mono);
    font-size: 0.72rem;
    font-weight: 600;
    line-height: 1.3;
    color: var(--text-muted);
  }

  .tool-inline-params {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 0.72rem;
    line-height: 1.3;
    color: var(--text-subtle);
  }

  .tool-inline-trailing {
    flex: none;
    min-width: 0;
    max-width: 180px;
  }

  .tool-inline-meta,
  .tool-inline-diff {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 0.66rem;
    line-height: 1.3;
  }

  .tool-inline-meta {
    color: var(--text-subtle);
    display: inline-block;
  }

  .tool-inline-diff {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    font-family: var(--pi-font-mono);
    font-weight: 600;
  }

  .tool-inline-trailing[hidden],
  .tool-inline-meta[hidden],
  .tool-inline-diff[hidden] {
    display: none !important;
  }

  .tool-inline-diff-added { color: var(--diff-added-accent); }
  .tool-inline-diff-removed { color: var(--diff-removed-accent); }

  .tool-inline[data-status="error"] .tool-inline-name,
  .tool-inline[data-status="error"] .tool-inline-meta { color: var(--error-text); }

  .tool-inline-details {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding-top: 1px;
  }

  .tool-inline-images {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }

  .tool-inline-code-panel {
    margin: 0;
    padding: 12px 14px;
    border: 1px solid var(--tool-output-border);
    border-radius: 10px;
    background: var(--tool-output-bg);
    overflow: auto;
    max-height: 360px;
  }

  :global(.app-shell[data-theme-mode="dark"]) .tool-inline-code-panel :global(pre.shiki) {
    background-color: var(--tool-output-bg) !important;
  }

  .tool-inline-command-output {
    padding-bottom: 6px;
    margin-bottom: 8px;
    border-bottom: 1px solid var(--tool-output-border);
    color: var(--text-subtle);
  }

  .tool-inline-code-output,
  .tool-inline-pre {
    margin: 0;
    font-family: var(--pi-font-mono);
    font-size: 0.72rem;
    line-height: 1.65;
    color: var(--text-muted);
    white-space: pre-wrap;
    word-break: break-word;
  }

  .tool-inline-empty {
    padding: 8px 0;
    font-size: 0.72rem;
    line-height: 1.45;
    color: var(--text-subtle);
  }

  .streaming-indicator-row {
    overflow-anchor: none;
  }

  .streaming-indicator {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    color: var(--text-subtle);
    font-size: 0.72rem;
    line-height: 1.3;
  }

  .busy-label { font-size: 0.7rem; }

  .dot {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: var(--text-subtle);
    display: inline-block;
    animation: typing-dot 1.2s ease-in-out infinite;
  }

  .dot:nth-child(2) { animation-delay: 0.2s; }
  .dot:nth-child(3) { animation-delay: 0.4s; }

  @keyframes typing-dot {
    0%,
    60%,
    100% {
      opacity: 0.2;
      transform: scale(0.7);
    }
    30% {
      opacity: 1;
      transform: scale(1);
    }
  }

  @media (max-width: 900px) {
    .chat-transcript {
      padding: 16px 16px 8px;
      gap: 6px;
    }
  }

  @media (max-width: 640px) {
    .chat-transcript {
      padding: 12px 12px 8px;
    }

    .message-content.user {
      max-width: min(580px, 100%);
      padding: 10px 14px;
      border-radius: 16px 16px 6px 16px;
    }
  }
</style>
