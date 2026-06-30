<script lang="ts">
  import { onDestroy, tick } from "svelte";
  import type {
    FieldAssistCommandPayload,
    FieldAssistResult,
    RpcImageContent,
    RpcTranscriptContent,
    RpcTranscriptContentBlock,
  } from "@dano/types/protocol";
  import Copy from "lucide-svelte/icons/copy";
  import FileText from "lucide-svelte/icons/file-text";
  import Maximize from "lucide-svelte/icons/maximize";
  import Maximize2 from "lucide-svelte/icons/maximize-2";
  import Minimize2 from "lucide-svelte/icons/minimize-2";
  import Pencil from "lucide-svelte/icons/pencil";
  import Sparkle from "lucide-svelte/icons/sparkle";
  import X from "lucide-svelte/icons/x";
  import ZoomIn from "lucide-svelte/icons/zoom-in";
  import ZoomOut from "lucide-svelte/icons/zoom-out";
  import {
    answerQuestion,
    getBridgeClientId,
    type TranscriptDelta,
    type TranscriptEntry,
    type TranscriptStream,
  } from "../composables/bridgeStore.svelte";
  import {
    askUserQuestionRequest,
    isAskUserQuestionToolError,
  } from "../utils/askUserQuestion";
  import {
    copyTextToClipboard,
    userMessageCopyText,
    userMessagePlainText,
  } from "../utils/messageCopy";
  import {
    buildTranscriptDisplayItems,
    contentBlocks,
    errorMessageText,
    isAbortedMessage,
    isErrorMessage,
    isToolResultMessage,
    messageContent,
    type FileContentBlock,
    type ImageContentBlock,
    type PendingTranscriptSessionEvent,
    type ToolContentBlock,
    type TranscriptDisplayItem,
  } from "../utils/transcript";
  import {
    createChatTranscriptBlockState,
    createChatTranscriptLightboxState,
  } from "./chatTranscriptBlockState.svelte";
  import DiffView from "./DiffView.svelte";
  import HighlightedCode from "./HighlightedCode.svelte";
  import ImageLightbox from "./ImageLightbox.svelte";
  import MarkdownRenderer from "./MarkdownRenderer.svelte";
  import QuestionToolCard from "./QuestionToolCard.svelte";
  import SkillInvocationCard from "./SkillInvocationCard.svelte";
  import { getRuntimeEmptyStateConfig } from "../utils/runtimeConfig";
  import { t } from "../i18n";

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
    onFieldAssist = undefined as
      | ((payload: FieldAssistCommandPayload) => Promise<FieldAssistResult>)
      | undefined,
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
    onFieldAssist?: (payload: FieldAssistCommandPayload) => Promise<FieldAssistResult>;
  } = $props();

  const emptyStateConfig = getRuntimeEmptyStateConfig();

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
    ).filter(item => item.kind !== "session_event"),
  );
  let hasVisibleStreaming = $derived(
    isStreaming || transcriptStreams.length > 0 || transcriptDeltas.length > 0,
  );
  let showBusyIndicator = $derived(hasVisibleStreaming || isCompacting);
  let copiedMessageKey = $state<string | null>(null);
  let copiedMessageResetTimer: number | undefined;
  let filePreview = $state<{
    block: FileContentBlock;
    src?: string;
    content?: string;
    loading: boolean;
    error: string;
  } | null>(null);
  let filePreviewRequestId = 0;
  let imagePreviewFit = $state(true);
  let imagePreviewScale = $state(1);
  let imagePreviewNaturalWidth = $state(0);
  let imagePreviewNaturalHeight = $state(0);
  let filePreviewMaximized = $state(false);
  let filePreviewBody = $state<HTMLDivElement | undefined>();
  let filePreviewDragging = $state(false);
  let filePreviewDragStart:
    | { x: number; y: number; scrollLeft: number; scrollTop: number }
    | null = null;
  let streamingAssistantMessageIndex = $derived.by(() => {
    if (!hasVisibleStreaming) return -1;
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i]?.role === "assistant") return i;
    }
    return -1;
  });
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
    const blocks = index >= messages.length
      ? contentBlocks(msg)
      : contentBlocks(messageWithTranscriptDeltas(msg, index));
    if (
      msg.role === "user" &&
      blocks.some(block => block.kind === "file" && isImageFile(block))
    ) {
      return blocks.filter(block => block.kind !== "image");
    }
    return blocks;
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
      case "file":
        return `${messageKey}:file:${block.path}:${blockIndex}`;
      case "system":
        return `${messageKey}:system:${block.systemType}:${block.title}:${blockIndex}`;
      case "thinking":
        return `${messageKey}:thinking:${blockIndex}`;
      case "text":
        return `${messageKey}:text:${blockIndex}`;
      case "skill":
        return `${messageKey}:skill:${block.skillName}:${blockIndex}`;
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

  function isMessageThinkingActive(msg: TranscriptEntry, mi: number): boolean {
    return shouldDeferMessageMarkdownErrors(msg, mi);
  }

  function thinkingBlockLabel(msg: TranscriptEntry, mi: number): string {
    return isMessageThinkingActive(msg, mi)
      ? t("chatTranscript.thinkingActive")
      : t("chatTranscript.thinkingComplete");
  }

  function previewText(text: string, maxLines: number = 8): string {
    const normalized = text.replace(/\r/g, "").trim();
    if (!normalized) return "";
    const lines = normalized.split("\n");
    if (lines.length <= maxLines) return normalized;
    const remaining = lines.length - maxLines;
    return `${lines.slice(0, maxLines).join("\n")}\n${t("chatTranscript.moreLines", { count: remaining })}`;
  }

  function compactInlineText(text: string | undefined, maxLength: number = 96): string | undefined {
    if (!text) return undefined;
    const singleLine = text.replace(/\s+/g, " ").trim();
    if (!singleLine) return undefined;
    if (singleLine.length <= maxLength) return singleLine;
    return `${singleLine.slice(0, maxLength - 3).trimEnd()}...`;
  }

  function toolBlockDescriptor(block: ToolContentBlock) {
    if (isAskUserQuestionToolError(block)) {
      return {
        name: t("chatTranscript.askUserQuestionRetryFailure"),
        params: undefined,
        meta: t("chatTranscript.askUserQuestionRetryFailureMeta"),
        status: block.toolStatus,
      };
    }
    const model = blockState.toolBlockModel(block);
    return {
      name: block.toolName || t("chatTranscript.toolFallback"),
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
    if (status === "pending") return t("chatTranscript.toolRunning");
    if (status === "error") return t("chatTranscript.error");
    return undefined;
  }

  function toolBlockImages(block: ToolContentBlock): ImageContentBlock[] {
    return (block.resultBlocks ?? []).filter(
      (item): item is ImageContentBlock => item.kind === "image",
    );
  }

  type ReadClassification = { kind: string; label: string };

  function getReadClassification(block: ToolContentBlock): ReadClassification | null {
    if (block.toolName !== "read") return null;
    const args = block.toolArgs;
    if (!args || typeof args !== "object") return null;
    const rawPath = (args as Record<string, unknown>).file_path ?? (args as Record<string, unknown>).path;
    if (typeof rawPath !== "string" || !rawPath) return null;
    const normalized = rawPath.replace(/\\/g, "/");
    const fileName = normalized.split("/").pop() ?? "";
    if (fileName === "SKILL.md") {
      const segments = normalized.split("/");
      const idx = segments.lastIndexOf("SKILL.md");
      return { kind: "skill", label: idx > 0 ? segments[idx - 1] : fileName };
    }
    return null;
  }

  function toolBlockEmptyState(block: ToolContentBlock): string {
    if (block.toolStatus === "pending") return t("chatTranscript.waitingForToolResult");
    if (block.toolName === "write" && blockState.toolBlockDetail(block).kind === "empty")
      return t("chatTranscript.fileEmpty");
    return t("chatTranscript.noTextResult");
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

  function openFileBlock(block: FileContentBlock) {
    const src = workspaceFilePreviewUrl(block);
    if (isImageFile(block)) {
      if (src) {
        resetImagePreviewZoom();
        filePreviewMaximized = false;
        filePreview = { block, src, loading: false, error: "" };
        return;
      }
    }
    const requestId = ++filePreviewRequestId;
    filePreviewMaximized = false;
    filePreview = { block, loading: true, error: "" };
    if (src) {
      fetch(src)
        .then(async response => {
          if (!response.ok) throw new Error(await response.text());
          return response.text();
        })
        .then(content => {
          if (requestId !== filePreviewRequestId) return;
          filePreview = { block, content, loading: false, error: "" };
        })
        .catch(error => {
          if (requestId !== filePreviewRequestId) return;
          filePreview = {
            block,
            loading: false,
            error: error instanceof Error ? error.message : t("fileViewer.loadFailed"),
          };
        });
      return;
    }

    if (!readWorkspaceFile) {
      filePreview = {
        block,
        loading: false,
        error: t("fileViewer.loadFailed"),
      };
      return;
    }
    readWorkspaceFile(block.path)
      .then(file => {
        if (requestId !== filePreviewRequestId) return;
        filePreview = {
          block,
          content: file.content,
          loading: false,
          error: "",
        };
      })
      .catch(error => {
        if (requestId !== filePreviewRequestId) return;
        filePreview = {
          block,
          loading: false,
          error: error instanceof Error ? error.message : t("fileViewer.loadFailed"),
        };
      });
  }

  function openImageBlock(block: ImageContentBlock) {
    resetImagePreviewZoom();
    filePreviewMaximized = false;
    filePreview = {
      block: { kind: "file", name: block.alt || t("transcript.imageAttachmentAlt"), path: "" },
      src: block.src,
      loading: false,
      error: "",
    };
  }

  function closeFilePreview() {
    filePreviewRequestId += 1;
    filePreview = null;
    filePreviewMaximized = false;
    endFilePreviewPan();
  }

  function resetImagePreviewZoom() {
    imagePreviewFit = true;
    imagePreviewScale = 1;
    imagePreviewNaturalWidth = 0;
    imagePreviewNaturalHeight = 0;
  }

  function setImagePreviewFit() {
    imagePreviewFit = true;
    imagePreviewScale = 1;
  }

  function setImagePreviewOriginalSize() {
    imagePreviewFit = false;
    imagePreviewScale = 1;
  }

  function zoomImagePreview(multiplier: number) {
    imagePreviewFit = false;
    imagePreviewScale = Math.min(8, Math.max(0.1, imagePreviewScale * multiplier));
  }

  function handleImagePreviewLoad(event: Event) {
    const image = event.currentTarget as HTMLImageElement;
    imagePreviewNaturalWidth = image.naturalWidth;
    imagePreviewNaturalHeight = image.naturalHeight;
  }

  function handleFilePreviewWheel(event: WheelEvent) {
    if (!filePreview?.src) return;
    event.preventDefault();
    zoomImagePreview(event.deltaY < 0 ? 1.1 : 1 / 1.1);
  }

  function startFilePreviewPan(event: MouseEvent) {
    if (!filePreview?.src || !filePreviewBody || event.button !== 0) return;
    filePreviewDragging = true;
    filePreviewDragStart = {
      x: event.clientX,
      y: event.clientY,
      scrollLeft: filePreviewBody.scrollLeft,
      scrollTop: filePreviewBody.scrollTop,
    };
  }

  function moveFilePreviewPan(event: MouseEvent) {
    if (!filePreviewDragging || !filePreviewDragStart || !filePreviewBody) return;
    event.preventDefault();
    filePreviewBody.scrollLeft =
      filePreviewDragStart.scrollLeft - (event.clientX - filePreviewDragStart.x);
    filePreviewBody.scrollTop =
      filePreviewDragStart.scrollTop - (event.clientY - filePreviewDragStart.y);
  }

  function endFilePreviewPan() {
    filePreviewDragging = false;
    filePreviewDragStart = null;
  }

  function imagePreviewStyle(): string {
    if (imagePreviewFit || !imagePreviewNaturalWidth || !imagePreviewNaturalHeight)
      return "";
    return `width: ${Math.round(imagePreviewNaturalWidth * imagePreviewScale)}px; height: ${Math.round(imagePreviewNaturalHeight * imagePreviewScale)}px;`;
  }

  function isImageFile(block: FileContentBlock): boolean {
    return /\.(png|jpe?g|gif|webp|svg)$/i.test(block.name || block.path);
  }

  function workspaceFilePreviewUrl(block: FileContentBlock): string | null {
    const clientId = getBridgeClientId();
    if (!clientId) return null;
    const query = new URLSearchParams({ clientId, path: block.path });
    return `/api/workspace-files/preview?${query.toString()}`;
  }

  function handleFilePreviewKeydown(event: KeyboardEvent) {
    if (!filePreview || event.key !== "Escape") return;
    event.preventDefault();
    closeFilePreview();
  }

  $effect(() => {
    if (typeof document === "undefined" || !filePreview) return;
    document.addEventListener("keydown", handleFilePreviewKeydown);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", handleFilePreviewKeydown);
      document.body.style.removeProperty("overflow");
    };
  });

  function toolResultName(msg: TranscriptEntry): string {
    return msg.toolName?.trim() || t("chatTranscript.toolFallback");
  }

  function toolResultMeta(msg: TranscriptEntry): string | undefined {
    const preview = compactInlineText(toolResultPreview(msg));
    if (preview) return preview;
    const images = toolResultImages(msg);
    if (images.length > 0)
      return t("chatTranscript.imageCount", { count: images.length });
    return toolStatusMeta(msg.isError ? "error" : "success");
  }

  function errorSummaryLabel(msg: TranscriptEntry): string {
    return isAbortedMessage(msg)
      ? t("chatTranscript.cancelled")
      : t("chatTranscript.error");
  }

  function errorSummaryMeta(msg: TranscriptEntry): string | undefined {
    if (isAbortedMessage(msg)) return undefined;
    return compactInlineText(errorMessageText(msg), 120);
  }

  function errorDetailText(msg: TranscriptEntry): string {
    return isAbortedMessage(msg)
      ? t("chatTranscript.cancelled")
      : errorMessageText(msg);
  }

  function messageIdLabel(msg: TranscriptEntry): string {
    return msg.id ?? t("chatTranscript.missingMessageId");
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

  function canCopyMessage(msg: TranscriptEntry): boolean {
    return Boolean(userMessagePlainText(msg));
  }

  function messageCopyLabel(key: string): string {
    return copiedMessageKey === key ? t("common.copied") : t("chatTranscript.copyMessage");
  }

  function showCopiedMessageState(key: string) {
    copiedMessageKey = key;
    if (copiedMessageResetTimer !== undefined) {
      window.clearTimeout(copiedMessageResetTimer);
    }
    copiedMessageResetTimer = window.setTimeout(() => {
      if (copiedMessageKey === key) copiedMessageKey = null;
      copiedMessageResetTimer = undefined;
    }, 1200);
  }

  async function handleCopyMessage(msg: TranscriptEntry, key: string) {
    const text = userMessagePlainText(msg);
    if (!text) return;
    const copied = await copyTextToClipboard(text);
    if (copied) showCopiedMessageState(key);
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

  onDestroy(() => {
    if (copiedMessageResetTimer !== undefined) {
      window.clearTimeout(copiedMessageResetTimer);
    }
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
      <p class="empty-title">{t("chatTranscript.loadingTitle")}</p>
      <p class="empty-subtitle">{t("chatTranscript.loadingSubtitle")}</p>
    </div>
  {:else if messages.length === 0}
    <div class="empty-state">
      {#if emptyStateConfig.mode === "html"}
        <div class="empty-html">{@html emptyStateConfig.content}</div>
      {:else}
        <p class="empty-text">{emptyStateConfig.content}</p>
      {/if}
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
        {pageLoading
          ? t("chatTranscript.loadingEarlierMessages")
          : t("chatTranscript.loadEarlierMessages")}
      </button>
    </div>
  {/if}

  {#each displayItems as item, index (displayItemKey(item, index))}
    {#if isToolResultMessage(item.message)}
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
                  <span class="message-debug-id">{t("chatTranscript.messageId", { id: messageIdLabel(item.message) })}</span>
                {/if}

                {#if toolResultImages(item.message).length > 0}
                  <div class="tool-inline-images">
                    {#each toolResultImages(item.message) as image, imgIdx (`${image.src}-${imgIdx}`)}
                      <figure class="message-image-block">
                        <button
                          type="button"
                          class="message-image-button"
                          aria-label={t("chatTranscript.openImageNumber", { number: imgIdx + 1 })}
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
                  <div class="tool-inline-empty">{t("chatTranscript.noTextResult")}</div>
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
                  <span class="message-debug-id">{t("chatTranscript.messageId", { id: messageIdLabel(item.message) })}</span>
                {/if}
                {#if errorDetailText(item.message)}
                  <section class="tool-inline-section">
                    <pre class="tool-inline-pre">{errorDetailText(item.message)}</pre>
                  </section>
                {:else}
                  <div class="tool-inline-empty">{t("chatTranscript.noErrorMessage")}</div>
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
              <div class="message-debug-id">{t("chatTranscript.messageId", { id: messageIdLabel(item.message) })}</div>
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
                <div
                  class={`thinking-block ${blockState.isThinkingExpanded(thinkingBlockStateKey(item.message, item.messageIndex, bIdx)) ? "expanded" : ""}`}
                >
                  <button
                    class="thinking-toggle"
                    onclick={() => blockState.toggleThinking(thinkingBlockStateKey(item.message, item.messageIndex, bIdx))}
                  >
                    <Sparkle class="toggle-icon" aria-hidden="true" size={14} />
                    {thinkingBlockLabel(item.message, item.messageIndex)}
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
                {#if askUserQuestionRequest(block) && !isAskUserQuestionToolError(block)}
                  <QuestionToolCard {block} active={isStreaming && !initialLoading} onRespond={answerQuestion} {onFieldAssist} />
                {:else if getReadClassification(block)?.kind === "skill"}
                  <SkillInvocationCard skillName={getReadClassification(block)!.label} />
                {:else}
                  {@const descriptor = toolBlockDescriptor(block)}
                  {@const diffStats = toolBlockDiffStats(block)}
                  {@const trailingKind = toolBlockTrailingKind(block)}
                  <div class="tool-inline-block" data-tree-entry-id={block.resultSourceMessageId}>
                    <div
                      class="tool-inline"
                      data-status={descriptor.status}
                      data-question-error={isAskUserQuestionToolError(block) ? true : undefined}
                    >
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
                          aria-label={t("chatTranscript.diffStats", {
                            additions: diffStats?.added ?? 0,
                            deletions: diffStats?.removed ?? 0,
                          })}
                        >
                          <span class="tool-inline-diff-added">+{diffStats?.added ?? 0}</span>
                          <span class="tool-inline-diff-removed">-{diffStats?.removed ?? 0}</span>
                        </span>
                      </span>
                      </button>

                    {#if blockState.isToolBlockExpanded(toolBlockStateKey(item.message, item.messageIndex, block, bIdx))}
                      <div class="tool-inline-details">
                        {#if showMessageIds && block.resultSourceMessageId}
                          <span class="message-debug-id">{t("chatTranscript.messageId", { id: block.resultSourceMessageId })}</span>
                        {/if}

                        {#if toolBlockImages(block).length > 0}
                          <div class="tool-inline-images">
                            {#each toolBlockImages(block) as image, imgIdx (`${image.src}-${imgIdx}`)}
                              <figure class="message-image-block">
                                <button
                                  type="button"
                                  class="message-image-button"
                                  aria-label={t("chatTranscript.openImageNumber", { number: imgIdx + 1 })}
                                  onclick={() => lightbox.openImageLightbox(toolBlockImages(block), imgIdx)}
                                >
                                  <img class="message-image" src={image.src} alt={image.alt} loading="lazy" />
                                </button>
                              </figure>
                            {/each}
                          </div>
                        {/if}

                        {#if isAskUserQuestionToolError(block)}
                          <section class="tool-inline-section">
                            <pre class="tool-inline-pre">{t("chatTranscript.askUserQuestionRetryFailureDetail")}</pre>
                          </section>
                        {:else if blockState.toolBlockDetail(block).kind !== "empty"}
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
                {/if}
              {:else if block.kind === "image"}
                <figure class="message-image-block">
                  <button
                    type="button"
                    class="message-image-button"
                    aria-label={t("chatTranscript.openImage")}
                    onclick={() => openImageBlock(block)}
                  >
                    <img class="message-image" src={block.src} alt={block.alt} loading="lazy" />
                  </button>
                </figure>
              {:else if block.kind === "file"}
                {@const previewSrc = isImageFile(block) ? workspaceFilePreviewUrl(block) : null}
                <div class="message-file-attachment">
                  {#if previewSrc}
                    <button
                      type="button"
                      class="message-file-preview-button"
                      aria-label={block.name}
                      title={block.name}
                      onclick={() => openFileBlock(block)}
                    >
                      <img
                        class="message-file-preview-image"
                        src={previewSrc}
                        alt={block.name}
                        loading="lazy"
                      />
                    </button>
                  {:else}
                    <button
                      type="button"
                      class="message-file-card"
                      title={block.path}
                      onclick={() => openFileBlock(block)}
                    >
                      <FileText class="message-file-icon" aria-hidden="true" size={18} />
                      <span class="message-file-name">{block.name}</span>
                    </button>
                  {/if}
                </div>
              {:else if block.kind === "skill"}
                <SkillInvocationCard skillName={block.skillName} />
              {:else if block.kind === "text" && block.text}
                <MarkdownRenderer
                  content={block.text}
                  fallbackText={item.message.role === "user" ? block.text : ""}
                  streaming={shouldDeferMessageMarkdownErrors(item.message, item.messageIndex)}
                  deferMermaidErrors={shouldDeferMessageMarkdownErrors(item.message, item.messageIndex)}
                  onOpenFileReference={onOpenFileReference}
                />
              {/if}
            {/each}
          </div>

          {#if canCopyMessage(item.message) || canReviseMessage(item.message)}
            <div class="message-actions">
              {#if canReviseMessage(item.message)}
                <button
                  type="button"
                  class="message-action-button"
                  aria-label={t("chatTranscript.editMessage")}
                  title={t("chatTranscript.editMessage")}
                  onclick={() => handleRevise(item.message)}
                >
                  <Pencil class="message-action-icon" aria-hidden="true" size={14} />
                </button>
              {/if}

              {#if canCopyMessage(item.message)}
                {@const copyKey = messageStableKey(item.message, item.messageIndex)}
                <button
                  type="button"
                  class="message-action-button"
                  data-copy-state={copiedMessageKey === copyKey ? "copied" : undefined}
                  data-tooltip={messageCopyLabel(copyKey)}
                  aria-label={messageCopyLabel(copyKey)}
                  title={messageCopyLabel(copyKey)}
                  onclick={() => handleCopyMessage(item.message, copyKey)}
                >
                  <Copy class="message-action-icon copy-base-icon" aria-hidden="true" size={14} />
                </button>
              {/if}
            </div>
          {/if}
        </div>
      </div>
    {/if}
  {/each}

  <ImageLightbox
    open={lightbox.lightboxImages.length > 0}
    images={lightbox.lightboxImages}
    index={lightbox.lightboxIndex}
    onClose={lightbox.closeImageLightbox}
    onPrevious={lightbox.showPreviousLightboxImage}
    onNext={lightbox.showNextLightboxImage}
  />

  {#if filePreview}
    <div class="file-preview-shell">
      <button
        type="button"
        class="file-preview-backdrop"
        aria-label={t("common.cancel")}
        onclick={closeFilePreview}
      ></button>
      <div
        class="file-preview-dialog"
        class:maximized={filePreviewMaximized}
        role="dialog"
        aria-modal="true"
        aria-label={filePreview.block.name}
        tabindex="-1"
      >
        <header class="file-preview-header">
          <div class="file-preview-title">{filePreview.block.name}</div>
          {#if filePreview.src}
            <div class="file-preview-controls">
              <button
                type="button"
                class="file-preview-control"
                aria-label="Zoom out"
                title="Zoom out"
                onclick={() => zoomImagePreview(1 / 1.25)}
              >
                <ZoomOut aria-hidden="true" size={16} />
              </button>
              <button
                type="button"
                class="file-preview-control"
                aria-label="Original size"
                title="Original size"
                onclick={setImagePreviewOriginalSize}
              >
                1:1
              </button>
              <button
                type="button"
                class="file-preview-control"
                aria-label="Fit to view"
                title="Fit to view"
                onclick={setImagePreviewFit}
              >
                <Maximize2 aria-hidden="true" size={16} />
              </button>
              <button
                type="button"
                class="file-preview-control"
                aria-label="Zoom in"
                title="Zoom in"
                onclick={() => zoomImagePreview(1.25)}
              >
                <ZoomIn aria-hidden="true" size={16} />
              </button>
            </div>
          {/if}
          <button
            type="button"
            class="file-preview-control"
            aria-label={filePreviewMaximized ? "Restore dialog" : "Maximize dialog"}
            title={filePreviewMaximized ? "Restore dialog" : "Maximize dialog"}
            onclick={() => (filePreviewMaximized = !filePreviewMaximized)}
          >
            {#if filePreviewMaximized}
              <Minimize2 aria-hidden="true" size={16} />
            {:else}
              <Maximize aria-hidden="true" size={16} />
            {/if}
          </button>
          <button
            type="button"
            class="file-preview-close"
            aria-label={t("common.cancel")}
            onclick={closeFilePreview}
          >
            <X aria-hidden="true" size={18} />
          </button>
        </header>
        <!-- svelte-ignore a11y_no_static_element_interactions, a11y_no_noninteractive_element_interactions: drag-to-pan is mouse-only sugar; native scrolling still works -->
        <div
          bind:this={filePreviewBody}
          class="file-preview-body"
          class:pannable={Boolean(filePreview.src)}
          class:panning={filePreviewDragging}
          onwheel={handleFilePreviewWheel}
          onmousedown={startFilePreviewPan}
          onmousemove={moveFilePreviewPan}
          onmouseup={endFilePreviewPan}
          onmouseleave={endFilePreviewPan}
        >
          {#if filePreview.src}
            <img
              class="file-preview-image"
              class:fit={imagePreviewFit}
              src={filePreview.src}
              alt={filePreview.block.name}
              style={imagePreviewStyle()}
              onload={handleImagePreviewLoad}
            />
          {:else if filePreview.loading}
            <div class="file-preview-state">{t("fileViewer.loading")}</div>
          {:else if filePreview.error}
            <div class="file-preview-state error">{filePreview.error}</div>
          {:else if !(filePreview.content ?? "")}
            <div class="file-preview-state">{t("fileViewer.empty")}</div>
          {:else}
            <pre class="file-preview-text">{filePreview.content ?? ""}</pre>
          {/if}
        </div>
      </div>
    </div>
  {/if}
</div>

<style>
  .chat-transcript {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    padding: 42px 32px 12px;
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

  .empty-title,
  .empty-text {
    margin: 0;
    font-size: 1.1rem;
    font-weight: 500;
    color: var(--text);
  }

  .empty-html {
    max-width: min(620px, 100%);
    color: var(--text);
    font-size: 1.1rem;
    line-height: 1.5;
  }

  .empty-html :global(*) {
    max-width: 100%;
  }

  .empty-html :global(:first-child) {
    margin-top: 0;
  }

  .empty-html :global(:last-child) {
    margin-bottom: 0;
  }

  .empty-subtitle {
    margin: 0;
    max-width: 420px;
    font-size: 0.85rem;
    line-height: 1.6;
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
    gap: 2px;
    justify-content: flex-end;
    width: fit-content;
    max-width: min(720px, 100%);
    margin: 2px 0px 0 0;
  }

  .message-action-button {
    position: relative;
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
    transform: translateY(-2px);
    transition:
      opacity 0.14s ease,
      color 0.14s ease,
      background 0.14s ease,
      transform 0.14s ease;
  }

  .message-stack.user:hover .message-action-button,
  .message-stack.user:focus-within .message-action-button,
  .message-action-button[data-copy-state="copied"] {
    opacity: 1;
    transform: translateY(0);
  }

  .message-action-button:hover,
  .message-action-button:focus-visible {
    background: var(--surface-hover);
    color: var(--text);
  }

  .message-action-button[data-copy-state="copied"] {
    color: var(--success);
  }

  .message-action-button[data-copy-state="copied"]::after {
    content: attr(data-tooltip);
    position: absolute;
    left: 50%;
    bottom: calc(100% + 7px);
    z-index: 2;
    padding: 5px 8px;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--panel);
    box-shadow: var(--shadow-raised);
    color: var(--text);
    font-size: 0.68rem;
    line-height: 1;
    white-space: nowrap;
    pointer-events: none;
    opacity: 1;
    transform: translateX(-50%);
  }

  .message-content.user {
    width: fit-content;
    max-width: min(720px, 100%);
    margin-left: auto;
    padding: 12px 16px;
    border: none;
    border-radius: 18px 18px 18px 18px;
    background: color-mix(in srgb, var(--accent) 16%, var(--bg));
  }

  :global(.app-shell[data-theme-mode="dark"]) .message-content.user {
    background: color-mix(in srgb, var(--accent) 55%, var(--bg));
  }

  :global(.markdown-renderer) + :global(.markdown-renderer),
  :global(.markdown-renderer) + .thinking-block,
  :global(.markdown-renderer) + .tool-inline-block,
  :global(.markdown-renderer) + .message-image-block,
  :global(.markdown-renderer) + .message-file-attachment,
  :global(.markdown-renderer) + .message-file-card,
  :global(.markdown-renderer) + .system-block,
  .thinking-block + :global(.markdown-renderer),
  .thinking-block + .thinking-block,
  .thinking-block + .tool-inline-block,
  .thinking-block + .message-image-block,
  .thinking-block + .message-file-attachment,
  .thinking-block + .message-file-card,
  .thinking-block + .system-block,
  .tool-inline-block + :global(.markdown-renderer),
  .tool-inline-block + .thinking-block,
  .tool-inline-block + .message-image-block,
  .tool-inline-block + .message-file-attachment,
  .tool-inline-block + .message-file-card,
  .tool-inline-block + .system-block,
  .message-image-block + :global(.markdown-renderer),
  .message-image-block + .thinking-block,
  .message-image-block + .tool-inline-block,
  .message-image-block + .message-image-block,
  .message-image-block + .message-file-attachment,
  .message-image-block + .message-file-card,
  .message-image-block + .system-block,
  .message-file-attachment + :global(.markdown-renderer),
  .message-file-attachment + .thinking-block,
  .message-file-attachment + .tool-inline-block,
  .message-file-attachment + .message-image-block,
  .message-file-attachment + .message-file-attachment,
  .message-file-attachment + .system-block,
  .message-file-card + :global(.markdown-renderer),
  .message-file-card + .thinking-block,
  .message-file-card + .tool-inline-block,
  .message-file-card + .message-image-block,
  .message-file-card + .message-file-card,
  .message-file-card + .system-block,
  .system-block + :global(.markdown-renderer),
  .system-block + .thinking-block,
  .system-block + .tool-inline-block,
  .system-block + .message-image-block,
  .system-block + .system-block {
    margin-top: 4px;
  }

  .tool-inline-block + .tool-inline-block {
    margin-top: 6px;
  }

  .thinking-block.expanded + :global(.markdown-renderer) {
    margin-top: 10px;
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

  .system-block :global(.system-block-body) {
    margin: 0;
    color: var(--text-muted);
    font-size: 0.76rem;
    line-height: 1.6;
  }

  .message-image-block { margin: 0; }

  .message-file-attachment {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
    max-width: 100%;
  }

  .message-file-card {
    display: inline-grid;
    grid-template-columns: auto minmax(0, 1fr);
    align-items: center;
    gap: 8px;
    max-width: min(100%, 360px);
    padding: 9px 11px;
    border: 1px solid color-mix(in srgb, var(--border) 72%, transparent);
    border-radius: 10px;
    background: color-mix(in srgb, var(--panel) 64%, transparent);
    color: var(--text);
    font: inherit;
    line-height: 1.25;
    text-align: left;
    cursor: pointer;
  }

  .message-file-card:hover,
  .message-file-card:focus-visible {
    border-color: var(--accent);
    box-shadow: 0 0 0 3px var(--focus-ring);
  }

  .message-file-icon {
    color: var(--text-muted);
    flex: 0 0 auto;
  }

  .message-file-name {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-weight: 600;
  }

  .message-file-preview-button {
    display: block;
    width: min(100%, 420px);
    max-height: 420px;
    padding: 0;
    border: 1px solid color-mix(in srgb, var(--border) 72%, transparent);
    border-radius: 14px;
    background: color-mix(in srgb, var(--panel) 64%, transparent);
    overflow: hidden;
    cursor: zoom-in;
  }

  .message-file-preview-button:hover,
  .message-file-preview-button:focus-visible {
    border-color: var(--accent);
    box-shadow: 0 0 0 3px var(--focus-ring);
  }

  .message-file-preview-image {
    display: block;
    width: 100%;
    max-height: 420px;
    object-fit: contain;
  }

  .file-preview-shell {
    position: fixed;
    inset: 0;
    z-index: 80;
    display: grid;
    place-items: center;
    padding: 24px;
  }

  .file-preview-backdrop {
    position: absolute;
    inset: 0;
    border: 0;
    background: color-mix(in srgb, #000 42%, transparent);
    cursor: default;
  }

  .file-preview-dialog {
    position: relative;
    z-index: 1;
    display: flex;
    flex-direction: column;
    width: min(860px, 100%);
    height: min(720px, calc(100dvh - 48px));
    border: 1px solid color-mix(in srgb, var(--border) 78%, transparent);
    border-radius: 14px;
    background: var(--panel);
    box-shadow: var(--shadow-floating);
    overflow: hidden;
  }

  .file-preview-dialog.maximized {
    width: calc(100dvw - 48px);
    height: calc(100dvh - 48px);
  }

  .file-preview-header {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto auto auto;
    align-items: center;
    gap: 10px;
    padding: 12px 14px;
    border-bottom: 1px solid color-mix(in srgb, var(--border) 72%, transparent);
  }

  .file-preview-title {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 0.85rem;
    font-weight: 700;
  }

  .file-preview-controls {
    display: inline-flex;
    align-items: center;
    gap: 2px;
  }

  .file-preview-control,
  .file-preview-close {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    border: 0;
    border-radius: 999px;
    background: transparent;
    color: var(--text-muted);
    cursor: pointer;
  }

  .file-preview-control {
    font-size: 0.72rem;
    font-weight: 700;
  }

  .file-preview-control:hover,
  .file-preview-control:focus-visible,
  .file-preview-close:hover,
  .file-preview-close:focus-visible {
    background: var(--surface-hover);
    color: var(--text);
  }

  .file-preview-body {
    display: grid;
    place-items: center;
    flex: 1;
    min-height: 0;
    overflow: auto;
    padding: 14px;
  }

  .file-preview-body.pannable {
    cursor: grab;
  }

  .file-preview-body.panning {
    cursor: grabbing;
    user-select: none;
  }

  .file-preview-image {
    display: block;
    margin: 0 auto;
    object-fit: contain;
    max-width: none;
    max-height: none;
    user-select: none;
    -webkit-user-drag: none;
  }

  .file-preview-image.fit {
    max-width: 100%;
    max-height: 100%;
  }

  .file-preview-text {
    align-self: start;
    justify-self: stretch;
    margin: 0;
    font-family: var(--pi-font-mono);
    font-size: 0.78rem;
    line-height: 1.65;
    color: var(--text);
    white-space: pre-wrap;
    word-break: break-word;
  }

  .file-preview-state {
    padding: 16px;
    border: 1px solid color-mix(in srgb, var(--border) 82%, transparent);
    border-radius: 10px;
    color: var(--text-muted);
    background: color-mix(in srgb, var(--panel) 84%, transparent);
  }

  .file-preview-state.error {
    border-color: color-mix(in srgb, var(--danger) 38%, var(--border));
    color: var(--error-text);
    background: color-mix(in srgb, var(--error-bg) 72%, transparent);
  }

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

  .thinking-block :global(.thinking-content) {
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
    align-items: center;
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
    align-items: center;
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
    display: inline-flex;
    align-items: center;
    justify-content: flex-end;
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

  .tool-inline[data-question-error="true"] .tool-inline-name,
  .tool-inline[data-question-error="true"] .tool-inline-meta {
    color: var(--text-muted);
  }

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

  @media (max-width: 900px) {
    .chat-transcript {
      padding: 42px 16px 8px;
      gap: 6px;
    }
  }

  @media (max-width: 640px) {
    .chat-transcript {
      padding: 42px 12px 8px;
    }

    .message-content.user {
      max-width: min(580px, 100%);
      padding: 10px 14px;
      border-radius: 16px;
    }

    .file-preview-shell {
      place-items: end stretch;
      padding: 0;
    }

    .file-preview-dialog {
      width: 100%;
      height: 82dvh;
      border-right: 0;
      border-bottom: 0;
      border-left: 0;
      border-radius: 16px 16px 0 0;
    }

    .file-preview-dialog.maximized {
      width: 100%;
      height: 100dvh;
      border-radius: 0;
    }

    .file-preview-header {
      grid-template-columns: minmax(0, 1fr) auto auto;
    }

    .file-preview-controls {
      grid-column: 1 / -1;
      justify-content: center;
      order: 3;
    }
  }
</style>
