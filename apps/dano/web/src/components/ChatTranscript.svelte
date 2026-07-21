<script lang="ts">
  import { onDestroy, tick } from "svelte";
  import type {
    FieldAssistCommandPayload,
    FieldAssistResult,
    RpcImageContent,
    RpcTranscriptContent,
    RpcTranscriptContentBlock,
  } from "@dano/types/protocol";
  import ArrowDown from "lucide-svelte/icons/arrow-down";
  import ChevronRight from "lucide-svelte/icons/chevron-right";
  import Copy from "lucide-svelte/icons/copy";
  import FileText from "lucide-svelte/icons/file-text";
  import Pencil from "lucide-svelte/icons/pencil";
  import Sparkle from "lucide-svelte/icons/sparkle";
  import X from "lucide-svelte/icons/x";
  import { slide } from "svelte/transition";
  import {
    abortGeneration,
    answerQuestion,
    cancelQuestionRevision,
    getBridgeClientId,
    presentQuestion,
    reviseQuestion,
    submitQuestionRevision,
    type TranscriptDelta,
    type TranscriptEntry,
    type TranscriptStream,
  } from "../composables/bridgeStore.svelte";
  import {
    askUserQuestionReturnedConfirmationFormIds,
    askUserQuestionRequest,
    hideAskUserQuestionToolBlock,
    isAskUserQuestionTerminalFailure,
    isAskUserQuestionToolError,
    isAskUserQuestionValidationTerminalFailure,
  } from "../utils/askUserQuestion";
  import {
    copyTextToClipboard,
    userMessageCopyText,
    userMessagePlainText,
  } from "../utils/messageCopy";
  import {
    assistantPendingState,
    buildTranscriptDisplayItems,
    buildTranscriptProcessGroups,
    contentBlocks,
    errorMessageText,
    formatTranscriptDuration,
    hasTerminalFormInteractionBlock,
    isAbortedMessage,
    isErrorMessage,
    isStreamingThinkingBlock,
    isToolResultMessage,
    latestThinkingLine,
    messageContent,
    type FileContentBlock,
    type ImageContentBlock,
    type PendingTranscriptSessionEvent,
    type ContentBlock,
    type ToolContentBlock,
    type TranscriptDisplayItem,
    type TranscriptMessageDisplayItem,
    type TranscriptProcessGroup,
  } from "../utils/transcript";
  import {
    createChatTranscriptBlockState,
    createChatTranscriptLightboxState,
  } from "./chatTranscriptBlockState.svelte";
  import {
    TRANSCRIPT_START_NOTICE_DURATION_MS,
    nextTopLoadArmed,
    restoredScrollTop,
    shouldAutoLoadOlderTranscript,
    shouldShowTranscriptStartNotice,
  } from "./chatTranscriptPagination";
  import {
    buildSkillActivity,
    buildToolActivities,
    type ToolActivity,
  } from "../utils/toolPresentation";
  import FilePreviewDialog from "./FilePreviewDialog.svelte";
  import ImageLightbox from "./ImageLightbox.svelte";
  import MarkdownRenderer from "./MarkdownRenderer.svelte";
  import QuestionToolCard from "./QuestionToolCard.svelte";
  import ToolActivityRow from "./ToolActivityRow.svelte";
  import type { QuestionFocusChange } from "./questionFocus";
  import {
    getRuntimeEmptyStateConfig,
    getRuntimeTranscriptProcessSummaryEnabled,
  } from "../utils/runtimeConfig";
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
    isPromptPending = false,
    isCompacting = false,
    scrollLocked = false,
    showMessageIds = false,
    allowRevision = false,
    onLoadOlder = () => false,
    onRevise = (_: { entryId: string; text: string; preview: string; hasImages: boolean; images: RpcImageContent[] }) => {},
    onOpenFileReference = (_: { path: string; lineNumber: number }) => {},
    readWorkspaceFile,
    onFieldAssist = undefined as
      | ((payload: FieldAssistCommandPayload) => Promise<FieldAssistResult>)
      | undefined,
    onQuestionFocusChange = undefined as
      | ((target: QuestionFocusChange) => void)
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
    isPromptPending?: boolean;
    isCompacting?: boolean;
    scrollLocked?: boolean;
    showMessageIds?: boolean;
    allowRevision?: boolean;
    onLoadOlder?: () => boolean | Promise<boolean>;
    onRevise?: (payload: { entryId: string; text: string; preview: string; hasImages: boolean; images: RpcImageContent[] }) => void;
    onOpenFileReference?: (payload: { path: string; lineNumber: number }) => void;
    readWorkspaceFile?: (path: string) => Promise<{ content: string }>;
    onFieldAssist?: (payload: FieldAssistCommandPayload) => Promise<FieldAssistResult>;
    onQuestionFocusChange?: (target: QuestionFocusChange) => void;
  } = $props();

  type RpcTranscriptToolCallBlock = Extract<
    RpcTranscriptContentBlock,
    { type: "toolCall" }
  >;

  const emptyStateConfig = getRuntimeEmptyStateConfig();
  const transcriptProcessSummaryEnabled =
    getRuntimeTranscriptProcessSummaryEnabled();
  const transcriptRevealTransition = { duration: 160 };
  const terminalFailuresAborted = new Set<string>();

  $effect(() => {
    if (!isStreaming) return;
    for (const message of messages) {
      for (const block of contentBlocks(message)) {
        if (
          block.kind !== "tool" ||
          !block.toolCallId ||
          !isAskUserQuestionTerminalFailure(block) ||
          terminalFailuresAborted.has(block.toolCallId)
        ) {
          continue;
        }
        terminalFailuresAborted.add(block.toolCallId);
        void abortGeneration();
        return;
      }
    }
  });

  // ---- DOM refs ----
  let container = $state<HTMLDivElement | null>(null);

  const BOTTOM_LOCK_THRESHOLD = 24;
  const TOP_LOAD_THRESHOLD = 80;
  let shouldStickToBottom = $state(true);
  let showTranscriptEndNotice = $state(false);
  let olderLoadRequestPending = $state(false);
  let topLoadArmed = $state(true);
  let transcriptEndNoticeTimer: number | undefined;
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
  let unfilteredDisplayItems = $derived(
    buildTranscriptDisplayItems(
      [...messages, ...streamDisplayMessages],
      { pendingSessionEvent: pendingTranscriptConfigEvent },
    ),
  );
  let returnedConfirmationFormIds = $derived.by(() => {
    const formIds = new Set<string>();
    for (const item of unfilteredDisplayItems) {
      if (item.kind !== "message") continue;
      for (const block of rawDisplayContentBlocks(item.message, item.messageIndex)) {
        if (block.kind !== "tool") continue;
        for (const formId of askUserQuestionReturnedConfirmationFormIds(block)) {
          formIds.add(formId);
        }
      }
    }
    return formIds;
  });
  let displayItems = $derived(
    unfilteredDisplayItems
      .filter((item): item is TranscriptMessageDisplayItem => item.kind === "message")
      .filter(item => !isSupersededFormOnlyItem(item)),
  );
  let processGroups = $derived(
    transcriptProcessSummaryEnabled
      ? buildTranscriptProcessGroups(displayItems, {
          blocksForMessage: displayContentBlocks,
          isMessageActive: shouldDeferMessageMarkdownErrors,
          messageKey: messageStableKey,
        })
      : [],
  );
  let hasVisibleStreaming = $derived(
    isStreaming || transcriptStreams.length > 0 || transcriptDeltas.length > 0,
  );
  let showBusyIndicator = $derived(hasVisibleStreaming || isCompacting);
  let pendingAssistantState = $derived(
    assistantPendingState(
      [
        ...messages.map((message, index) =>
          messageWithTranscriptDeltas(message, index),
        ),
        ...streamDisplayMessages,
      ],
      isPromptPending || isStreaming,
    ),
  );
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
  let streamingAssistantMessageIndex = $derived.by(() => {
    if (!hasVisibleStreaming) return -1;
    let lastUserIndex = -1;
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i]?.role !== "user") continue;
      lastUserIndex = i;
      break;
    }
    for (let i = messages.length - 1; i >= 0; i--) {
      if (i <= lastUserIndex) return -1;
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
      const isDifferentToolCall =
        (delta.toolCallId && block.id && delta.toolCallId !== block.id) ||
        (delta.toolName && block.name && delta.toolName !== block.name);
      if (isDifferentToolCall) {
        return {
          type: "toolCall",
          id: delta.toolCallId,
          name: delta.toolName ?? "tool",
          arguments: delta.delta,
        };
      }
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

  function isDifferentToolCall(
    block: RpcTranscriptToolCallBlock,
    delta: TranscriptDelta,
  ): boolean {
    return (
      Boolean(delta.toolCallId && block.id && delta.toolCallId !== block.id) ||
      Boolean(delta.toolName && block.name && delta.toolName !== block.name)
    );
  }

  function findMatchingToolCallIndex(
    content: (string | RpcTranscriptContentBlock)[],
    delta: TranscriptDelta,
  ): number | null {
    const index = content.findIndex(block =>
      Boolean(
        block &&
          typeof block === "object" &&
          block.type === "toolCall" &&
          (!delta.toolCallId || block.id === delta.toolCallId) &&
          (!delta.toolName || block.name === delta.toolName),
      ),
    );
    return index >= 0 ? index : null;
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

      const block = contentItems[delta.contentIndex];
      const targetIndex =
        delta.blockType === "toolCall" &&
        block &&
        typeof block === "object" &&
        block.type === "toolCall" &&
        isDifferentToolCall(block, delta)
          ? findMatchingToolCallIndex(contentItems, delta) ?? contentItems.length
          : delta.contentIndex;

      contentItems[targetIndex] = appendDeltaToContentBlock(
        contentItems[targetIndex],
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

  function rawDisplayContentBlocks(msg: TranscriptEntry, index: number) {
    // Stream display messages already have deltas applied before they enter
    // `displayItems`, so avoid replaying the same deltas a second time.
    return index >= messages.length
      ? contentBlocks(msg)
      : contentBlocks(messageWithTranscriptDeltas(msg, index));
  }

  function displayContentBlocks(msg: TranscriptEntry, index: number) {
    const blocks = rawDisplayContentBlocks(msg, index).filter(block =>
      block.kind !== "tool" ||
      !hideAskUserQuestionToolBlock(block, returnedConfirmationFormIds),
    );
    if (
      msg.role === "user" &&
      blocks.some(block => block.kind === "file" && isImageFile(block))
    ) {
      return blocks.filter(block => block.kind !== "image");
    }
    return blocks;
  }

  function isSupersededFormOnlyItem(item: TranscriptDisplayItem): boolean {
    if (item.kind !== "message") return false;
    const blocks = rawDisplayContentBlocks(item.message, item.messageIndex);
    return blocks.length > 0 && blocks.every(block =>
      block.kind === "tool" &&
      hideAskUserQuestionToolBlock(block, returnedConfirmationFormIds),
    );
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

  function askUserQuestionFailureActivity(
    key: string,
    block: ToolContentBlock,
  ): ToolActivity {
    const activity = buildToolActivities([{ key, block }])[0]!;
    const validationTerminal = isAskUserQuestionValidationTerminalFailure(block);
    const terminal = isAskUserQuestionTerminalFailure(block);
    return {
      ...activity,
      label: t(validationTerminal
        ? "chatTranscript.askUserQuestionValidationFailure"
        : terminal
          ? "chatTranscript.askUserQuestionTerminalFailure"
          : "chatTranscript.askUserQuestionRetryFailure"),
      details: [t(validationTerminal
        ? "chatTranscript.askUserQuestionValidationFailureDetail"
        : terminal
          ? "chatTranscript.askUserQuestionTerminalFailureDetail"
          : "chatTranscript.askUserQuestionRetryFailureDetail")],
      rawDetails: [],
    };
  }

  function hiddenAskUserQuestionFailureKeys(): Set<string> {
    const hidden = new Set<string>();
    let unresolvedRetryKeys: string[] = [];

    const keepOnlyLatestRetry = () => {
      for (const key of unresolvedRetryKeys.slice(0, -1)) hidden.add(key);
      unresolvedRetryKeys = [];
    };

    for (const item of displayItems) {
      if (item.message.role === "user") {
        keepOnlyLatestRetry();
        continue;
      }
      if (isToolResultMessage(item.message) || isErrorMessage(item.message)) continue;

      for (const [blockIndex, block] of displayContentBlocks(
        item.message,
        item.messageIndex,
      ).entries()) {
        if (block.kind !== "tool") continue;
        const key = toolBlockStateKey(
          item.message,
          item.messageIndex,
          block,
          blockIndex,
        );
        if (isAskUserQuestionToolError(block)) {
          if (isAskUserQuestionTerminalFailure(block)) {
            for (const retryKey of unresolvedRetryKeys) hidden.add(retryKey);
            unresolvedRetryKeys = [];
          } else {
            unresolvedRetryKeys.push(key);
          }
          continue;
        }
        if (askUserQuestionRequest(block)) {
          for (const retryKey of unresolvedRetryKeys) hidden.add(retryKey);
          unresolvedRetryKeys = [];
        }
      }
    }
    keepOnlyLatestRetry();
    return hidden;
  }

  let toolActivityProjection = $derived.by(() => {
    const bySourceKey = new Map<string, ToolActivity>();
    const firstSourceKeys = new Set<string>();
    let activeKey: string | undefined;
    let pendingSources: Array<{ key: string; block: ToolContentBlock }> = [];
    const hiddenQuestionFailureKeys = hiddenAskUserQuestionFailureKeys();

    const register = (activity: ToolActivity) => {
      const firstSourceKey = activity.sourceKeys[0];
      if (firstSourceKey) firstSourceKeys.add(firstSourceKey);
      for (const sourceKey of activity.sourceKeys) {
        bySourceKey.set(sourceKey, activity);
      }
      if (activity.status === "pending") activeKey = activity.key;
    };

    const flush = () => {
      for (const activity of buildToolActivities(pendingSources)) {
        register(activity);
      }
      pendingSources = [];
    };

    for (const item of displayItems) {
      if (item.message.role === "user" || isToolResultMessage(item.message) || isErrorMessage(item.message)) {
        flush();
        continue;
      }

      const blocks = displayContentBlocks(item.message, item.messageIndex);
      for (const [blockIndex, block] of blocks.entries()) {
        if (block.kind === "thinking") continue;
        if (block.kind !== "tool") {
          flush();
          continue;
        }
        const key = toolBlockStateKey(
          item.message,
          item.messageIndex,
          block,
          blockIndex,
        );
        if (isAskUserQuestionToolError(block)) {
          flush();
          if (!hiddenQuestionFailureKeys.has(key)) {
            register(askUserQuestionFailureActivity(key, block));
          }
          continue;
        }
        if (askUserQuestionRequest(block)) {
          flush();
          continue;
        }
        pendingSources.push({
          key,
          block,
        });
      }
    }
    flush();

    return { bySourceKey, firstSourceKeys, activeKey };
  });

  function processGroupForItemIndex(itemIndex: number): TranscriptProcessGroup | undefined {
    return processGroups.find(group =>
      itemIndex >= group.startItemIndex && itemIndex <= group.endItemIndex,
    );
  }

  function processGroupForUserItemIndex(itemIndex: number): TranscriptProcessGroup | undefined {
    return processGroups.find(group => group.startItemIndex === itemIndex);
  }

  function isProcessGroupExpanded(group: TranscriptProcessGroup | undefined): boolean {
    return Boolean(group && blockState.isProcessGroupExpanded(group.key));
  }

  function shouldRenderDisplayItemAt(index: number): boolean {
    const group = processGroupForItemIndex(index);
    if (!group || isProcessGroupExpanded(group)) return true;
    const item = displayItems[index];
    return index === group.startItemIndex ||
      index >= group.finalAnswerItemIndex ||
      Boolean(
        item?.kind === "message" &&
        hasTerminalFormInteractionBlock(
          displayContentBlocks(item.message, item.messageIndex),
        ),
      );
  }

  function visibleContentBlocks(
    item: TranscriptDisplayItem,
    itemIndex: number,
  ): ContentBlock[] {
    if (item.kind !== "message") return [];

    const blocks = displayContentBlocks(item.message, item.messageIndex);
    const group = processGroupForItemIndex(itemIndex);
    if (
      !group ||
      isProcessGroupExpanded(group) ||
      itemIndex !== group.finalAnswerItemIndex
    ) return blocks;

    return blocks.slice(group.finalAnswerBlockIndex);
  }

  function processSummaryLabel(group: TranscriptProcessGroup): string {
    const duration = formatTranscriptDuration(group.durationMs);
    return duration
      ? t("chatTranscript.processCompleteWithDuration", { duration })
      : t("chatTranscript.processComplete");
  }

  function processSummaryAriaLabel(group: TranscriptProcessGroup): string {
    const duration = formatTranscriptDuration(group.durationMs);
    const key = isProcessGroupExpanded(group)
      ? duration
        ? "chatTranscript.processCollapseLabel"
        : "chatTranscript.processCollapseLabelNoDuration"
      : duration
        ? "chatTranscript.processExpandLabel"
        : "chatTranscript.processExpandLabelNoDuration";
    return duration
      ? t(key, { duration })
      : t(key);
  }

  function toggleProcessGroup(group: TranscriptProcessGroup) {
    blockState.toggleProcessGroup(group.key);
  }

  function expandProcessGroupForEntry(entryId: string): boolean {
    const group = processGroups.find(candidate =>
      candidate.entryIds.includes(entryId),
    );
    if (!group || isProcessGroupExpanded(group)) return false;

    blockState.expandProcessGroup(group.key);
    tick().then(() => {
      const target = transcriptEntryElement(entryId);
      if (!target) return;
      target.scrollIntoView({ block: "center" });
      updateBottomLock();
    });
    return true;
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

  function toolResultText(msg: TranscriptEntry): string {
    if (msg.toolName === "read" && toolResultImages(msg).length > 0) return "";
    return contentBlocks(msg)
      .flatMap(block => (block.kind === "text" ? [block.text] : []))
      .join("\n");
  }

  function toolResultImages(msg: TranscriptEntry): ImageContentBlock[] {
    return contentBlocks(msg).filter(
      (block): block is ImageContentBlock => block.kind === "image",
    );
  }

  function orphanToolActivity(msg: TranscriptEntry, messageIndex: number): ToolActivity {
    const key = `${messageStableKey(msg, messageIndex)}:tool-result`;
    const block: ToolContentBlock = {
      kind: "tool",
      toolName: msg.toolName?.trim() || "unknown",
      toolArgs: {},
      argumentsText: "",
      toolStatus: msg.isError ? "error" : "success",
      resultText: toolResultText(msg),
      resultBlocks: toolResultImages(msg),
    };
    return buildToolActivities([{ key, block }])[0]!;
  }

  function openFileBlock(block: FileContentBlock) {
    const src = workspaceFilePreviewUrl(block);
    if (isImageFile(block)) {
      if (src) {
        filePreview = { block, src, loading: false, error: "" };
        return;
      }
    }
    const requestId = ++filePreviewRequestId;
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

  function isNearTop(el: HTMLElement): boolean {
    return el.scrollTop <= TOP_LOAD_THRESHOLD;
  }

  function showTranscriptEndNoticeBriefly() {
    showTranscriptEndNotice = true;
    if (transcriptEndNoticeTimer !== undefined) {
      window.clearTimeout(transcriptEndNoticeTimer);
    }
    transcriptEndNoticeTimer = window.setTimeout(() => {
      showTranscriptEndNotice = false;
      transcriptEndNoticeTimer = undefined;
    }, TRANSCRIPT_START_NOTICE_DURATION_MS);
  }

  async function requestOlderTranscript() {
    if (!container || scrollLocked) return;

    const nearTop = isNearTop(container);
    if (
      !shouldAutoLoadOlderTranscript({
        isNearTop: nearTop,
        topLoadArmed,
        hasOlder,
        initialLoading,
        pageLoading,
        requestPending: olderLoadRequestPending,
      })
    ) {
      return;
    }

    const target = container;
    const previousScrollHeight = target.scrollHeight;
    const previousScrollTop = target.scrollTop;

    topLoadArmed = false;
    olderLoadRequestPending = true;
    let reachedTranscriptStart = false;
    try {
      const loaded = await onLoadOlder();
      if (!loaded) {
        await tick();
        reachedTranscriptStart = !hasOlder;
      } else {
        await tick();
        if (container === target) {
          const nextScrollTop = restoredScrollTop({
            loaded,
            previousScrollTop,
            previousScrollHeight,
            nextScrollHeight: target.scrollHeight,
          });
          if (nextScrollTop !== null) target.scrollTop = nextScrollTop;
        }
      }
    } finally {
      olderLoadRequestPending = false;
      updateBottomLock();
    }
    if (reachedTranscriptStart) showTranscriptEndNoticeBriefly();
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

  export function scrollTranscriptToBottom(options: { smooth?: boolean } = {}) {
    if (!container || scrollLocked) return;
    container.scrollTo({
      top: container.scrollHeight,
      behavior: options.smooth ? "smooth" : "auto",
    });
    updateBottomLock();
  }

  function scheduleStickToBottom() {
    if (scrollLocked) return;
    if (stickToBottomFrame) cancelAnimationFrame(stickToBottomFrame);
    stickToBottomFrame = requestAnimationFrame(() => {
      stickToBottomFrame = 0;
      scrollTranscriptToBottom();
    });
  }

  function handleTranscriptScroll() {
    if (scrollLocked) return;
    updateBottomLock();
    if (container) {
      const nearTop = isNearTop(container);
      const topLoadTriggered = nearTop && topLoadArmed;
      topLoadArmed = nextTopLoadArmed({
        isNearTop: nearTop,
        current: topLoadArmed,
      });
      if (
        shouldShowTranscriptStartNotice({
          topLoadTriggered,
          hasOlder,
          messagesLength: messages.length,
          initialLoading,
          pageLoading,
          requestPending: olderLoadRequestPending,
        })
      ) {
        topLoadArmed = false;
        showTranscriptEndNoticeBriefly();
      }
    }
    if (!container || !isNearTop(container)) return;
    void requestOlderTranscript();
  }

  function shouldShowScrollToBottom(): boolean {
    return !initialLoading && messages.length > 0 && !shouldStickToBottom;
  }

  export function preserveBottomPosition(gracePx: number = 48): boolean {
    if (scrollLocked) return false;
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

  function transcriptEntryElement(entryId: string): HTMLElement | null {
    const el = container;
    if (!el) return null;

    const selector = `[data-tree-entry-id="${cssEscape(entryId)}"], [data-tree-entry-ids~="${cssEscape(entryId)}"]`;
    return el.querySelector<HTMLElement>(selector);
  }

  export function scrollToTranscriptEntry(entryId: string): boolean {
    if (scrollLocked) return false;
    const target = transcriptEntryElement(entryId);
    if (!target) return expandProcessGroupForEntry(entryId);

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
    const transcriptEl = el;

    let lastClientHeight = transcriptEl.clientHeight;
    let lastScrollHeight = transcriptEl.scrollHeight;
    const observedChildren = new Set<Element>();

    function handleTranscriptResize() {
      const keepBottomLocked = shouldStickToBottom;
      const nextClientHeight = transcriptEl.clientHeight;
      const nextScrollHeight = transcriptEl.scrollHeight;
      if (
        nextClientHeight === lastClientHeight &&
        nextScrollHeight === lastScrollHeight
      ) return;
      lastClientHeight = nextClientHeight;
      lastScrollHeight = nextScrollHeight;
      if (keepBottomLocked) scheduleStickToBottom();
      else updateBottomLock();
    }

    const observer = new ResizeObserver(handleTranscriptResize);

    function observeTranscriptChildren() {
      const currentChildren = new Set(Array.from(transcriptEl.children));
      for (const child of observedChildren) {
        if (currentChildren.has(child)) continue;
        observer.unobserve(child);
        observedChildren.delete(child);
      }
      for (const child of currentChildren) {
        if (observedChildren.has(child)) continue;
        observedChildren.add(child);
        observer.observe(child);
      }
    }

    observer.observe(transcriptEl);
    observeTranscriptChildren();
    const mutationObserver =
      typeof MutationObserver === "undefined"
        ? null
        : new MutationObserver(observeTranscriptChildren);
    mutationObserver?.observe(transcriptEl, { childList: true });

    return () => {
      mutationObserver?.disconnect();
      observer.disconnect();
    };
  });

  $effect(() => {
    const el = container;
    const path = sessionPath;
    if (!el || lastSessionPath === path) return;

    lastSessionPath = path;
    shouldStickToBottom = true;
    topLoadArmed = true;
    showTranscriptEndNotice = false;
    if (transcriptEndNoticeTimer !== undefined) {
      window.clearTimeout(transcriptEndNoticeTimer);
      transcriptEndNoticeTimer = undefined;
    }
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
    if (!el || !shouldStickToBottom || scrollLocked) return;

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
    if (transcriptEndNoticeTimer !== undefined) {
      window.clearTimeout(transcriptEndNoticeTimer);
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

<div
  bind:this={container}
  class="chat-transcript"
  data-center-focus-transcript
  onscroll={handleTranscriptScroll}
>
  {#if initialLoading}
    <div
    class="conversation-skeleton"
    role="status"
    aria-label={t("chatTranscript.loadingTitle")}
  >
      <div class="conversation-skeleton-row assistant" aria-hidden="true">
        <span class="conversation-skeleton-line wide"></span>
        <span class="conversation-skeleton-line medium"></span>
        <span class="conversation-skeleton-line short"></span>
      </div>
      <div class="conversation-skeleton-row user" aria-hidden="true">
        <span class="conversation-skeleton-bubble"></span>
      </div>
      <div class="conversation-skeleton-row assistant" aria-hidden="true">
        <span class="conversation-skeleton-line medium"></span>
        <span class="conversation-skeleton-line wide"></span>
      </div>
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

  {#if !initialLoading && (pageLoading || olderLoadRequestPending || showTranscriptEndNotice)}
    <div
      class="history-loader"
      class:history-end-notice={showTranscriptEndNotice && !pageLoading && !olderLoadRequestPending}
      role="status"
      aria-live="polite"
    >
      {#if pageLoading || olderLoadRequestPending}
        <span class="history-loader-spinner" aria-hidden="true"></span>
        <span>{t("chatTranscript.loadingEarlierMessages")}</span>
      {:else}
        <span>{t("chatTranscript.noEarlierMessages")}</span>
      {/if}
    </div>
  {/if}

  {#each displayItems as item, index (displayItemKey(item, index))}
    {#if shouldRenderDisplayItemAt(index) && isToolResultMessage(item.message)}
      {@const activity = orphanToolActivity(item.message, item.messageIndex)}
      <div
        class="message-row tool"
        data-message-id={item.message.id ?? undefined}
        data-tree-entry-id={item.message.id ?? undefined}
        transition:slide={transcriptRevealTransition}
      >
        <div class="message-content tool">
          <ToolActivityRow
            {activity}
            treeEntryId={item.message.id}
            expanded={blockState.isToolBlockExpanded(activity.key)}
            onToggle={() => blockState.toggleToolBlock(activity.key)}
            onOpenImage={(imageIndex) => lightbox.openImageLightbox(activity.images, imageIndex)}
          />
        </div>
      </div>
    {:else if shouldRenderDisplayItemAt(index) && isErrorMessage(item.message)}
      <div
        class="message-row {roleClass(item.message.role)}"
        data-message-id={item.message.id ?? undefined}
        data-tree-entry-id={item.message.id ?? undefined}
        transition:slide={transcriptRevealTransition}
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
              <div class="tool-inline-details" transition:slide={transcriptRevealTransition}>
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
    {:else if shouldRenderDisplayItemAt(index)}
      {@const blocks = visibleContentBlocks(item, index)}
      <div
        class="message-row {roleClass(item.message.role)}"
        data-message-id={item.message.id ?? undefined}
        data-tree-entry-id={item.message.id ?? undefined}
        transition:slide={transcriptRevealTransition}
      >
        <div class="message-stack {roleClass(item.message.role)}">
          <div
            class="message-content {roleClass(item.message.role)}"
            data-user-message-index={item.message.role === "user" ? item.messageIndex : undefined}
          >
            {#if showMessageIds}
              <div class="message-debug-id">{t("chatTranscript.messageId", { id: messageIdLabel(item.message) })}</div>
            {/if}

            {#each blocks as block, bIdx (contentBlockKey(item.message, item.messageIndex, block, bIdx))}
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
              {:else if block.kind === "thinking" && isStreamingThinkingBlock(isMessageThinkingActive(item.message, item.messageIndex), blocks, bIdx)}
                <div class="thinking-block">
                  <div class="thinking-stream-line">
                    <span class="thinking-stream-icon" aria-hidden="true">
                      <Sparkle size={14} />
                    </span>
                    <span class="thinking-stream-text">{latestThinkingLine(block.text)}...</span>
                  </div>
                </div>
              {:else if block.kind === "tool"}
                {#if askUserQuestionRequest(block) && !isAskUserQuestionToolError(block)}
                  <QuestionToolCard {block} active={isStreaming && !initialLoading && shouldDeferMessageMarkdownErrors(item.message, item.messageIndex)} onPresent={presentQuestion} onRespond={answerQuestion} onRevise={reviseQuestion} onCancelRevision={cancelQuestionRevision} onSubmitRevision={submitQuestionRevision} onFocusChange={onQuestionFocusChange} {onFieldAssist} />
                {:else}
                  {@const activityKey = toolBlockStateKey(item.message, item.messageIndex, block, bIdx)}
                  {@const activity = toolActivityProjection.bySourceKey.get(activityKey)}
                  {#if activity && toolActivityProjection.firstSourceKeys.has(activityKey)}
                    <ToolActivityRow
                      {activity}
                      treeEntryId={block.resultSourceMessageId}
                      expanded={blockState.isToolBlockExpanded(activity.key)}
                      active={activity.key === toolActivityProjection.activeKey && isStreaming && !initialLoading}
                      onToggle={() => blockState.toggleToolBlock(activity.key)}
                      onOpenImage={(imageIndex) => lightbox.openImageLightbox(activity.images, imageIndex)}
                    />
                  {/if}
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
                {@const skillActivityKey = contentBlockKey(item.message, item.messageIndex, block, bIdx)}
                {@const skillActivity = buildSkillActivity(skillActivityKey, block.skillName)}
                <ToolActivityRow activity={skillActivity} />
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
    {#if processGroupForUserItemIndex(index)}
      {@const group = processGroupForUserItemIndex(index)}
      {#if group}
        <div class="message-row assistant process-summary-row" transition:slide={transcriptRevealTransition}>
          <button
            type="button"
            class="process-summary-toggle"
            class:expanded={isProcessGroupExpanded(group)}
            aria-expanded={isProcessGroupExpanded(group)}
            aria-label={processSummaryAriaLabel(group)}
            onclick={() => toggleProcessGroup(group)}
          >
            <span>{processSummaryLabel(group)}</span>
            <span class="process-summary-icon" aria-hidden="true">
              <ChevronRight size={14} />
            </span>
          </button>
        </div>
      {/if}
    {/if}
  {/each}

  {#if pendingAssistantState}
    <div
      class="message-row assistant assistant-pending-row"
      class:assistant-pending-delayed={pendingAssistantState === "post-tool"}
      role="status"
      aria-label={t("chatTranscript.waitingForResponse")}
    >
      <div class="assistant-pending" aria-hidden="true">
        <span></span>
        <span></span>
        <span></span>
      </div>
    </div>
  {/if}

  {#if shouldShowScrollToBottom()}
    <div class="scroll-bottom-overlay">
      <button
        type="button"
        class="scroll-bottom-button"
        aria-label={t("chatTranscript.scrollToBottom")}
        title={t("chatTranscript.scrollToBottom")}
        onclick={() => scrollTranscriptToBottom({ smooth: true })}
      >
        <ArrowDown aria-hidden="true" size={18} />
      </button>
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

  <FilePreviewDialog
    preview={filePreview
      ? {
          name: filePreview.block.name,
          src: filePreview.src,
          content: filePreview.content,
          loading: filePreview.loading,
          error: filePreview.error,
        }
      : null}
    onClose={closeFilePreview}
  />
</div>

<style>
  .chat-transcript {
    position: relative;
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

  .scroll-bottom-overlay {
    position: sticky;
    bottom: 56px;
    z-index: 5;
    height: 0;
    display: flex;
    justify-content: center;
    pointer-events: none;
  }

  .scroll-bottom-button {
    appearance: none;
    width: 40px;
    height: 40px;
    border: 0;
    border-radius: 999px;
    background: color-mix(in srgb, var(--panel) 65%, transparent);
    -webkit-backdrop-filter: blur(2px);
    backdrop-filter: blur(2px);
    color: var(--text);
    box-shadow:
      0 0 0 1px color-mix(in srgb, var(--border) 70%, transparent),
      var(--shadow-raised);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    pointer-events: auto;
    touch-action: manipulation;
    -webkit-tap-highlight-color: transparent;
    transition:
      background 0.16s ease,
      box-shadow 0.16s ease,
      transform 0.12s ease;
  }

  .scroll-bottom-button:hover {
    background: color-mix(in srgb, var(--panel-2) 75%, transparent);
    box-shadow:
      0 0 0 1px color-mix(in srgb, var(--border-strong) 80%, transparent),
      var(--shadow-floating);
  }

  .scroll-bottom-button:active {
    transform: scale(0.96);
  }

  .scroll-bottom-button:focus-visible {
    outline: 2px solid var(--focus-ring);
    outline-offset: 2px;
  }

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

  .conversation-skeleton {
    display: flex;
    flex: 1;
    flex-direction: column;
    gap: 22px;
    width: 100%;
    max-width: 920px;
    min-height: 240px;
    margin: 0 auto;
    padding: 22px 10px;
  }

  .conversation-skeleton-row {
    display: flex;
    flex-direction: column;
    gap: 10px;
    width: min(70%, 620px);
  }

  .conversation-skeleton-row.user {
    align-self: flex-end;
    width: min(58%, 520px);
  }

  .conversation-skeleton-line,
  .conversation-skeleton-bubble {
    display: block;
    background: linear-gradient(
      100deg,
      color-mix(in srgb, var(--panel-2) 82%, transparent) 20%,
      color-mix(in srgb, var(--text-subtle) 18%, var(--panel)) 42%,
      color-mix(in srgb, var(--panel-2) 82%, transparent) 64%
    );
    background-size: 220% 100%;
    animation: conversation-skeleton-shimmer 1.4s ease-in-out infinite;
  }

  .conversation-skeleton-line {
    height: 12px;
    border-radius: 999px;
  }

  .conversation-skeleton-line.wide { width: 100%; }
  .conversation-skeleton-line.medium { width: 76%; }
  .conversation-skeleton-line.short { width: 44%; }

  .conversation-skeleton-bubble {
    width: 100%;
    height: 68px;
    border-radius: 18px;
    animation-delay: -0.35s;
  }

  @keyframes conversation-skeleton-shimmer {
    from { background-position: 100% 0; }
    to { background-position: -120% 0; }
  }

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
    align-items: center;
    justify-content: center;
    gap: 8px;
    width: 100%;
    min-height: 32px;
    color: var(--text-subtle);
    font-size: 0.74rem;
  }

  .history-loader.history-end-notice {
    position: absolute;
    top: 4px;
    right: 0;
    left: 0;
    width: auto;
  }

  .history-loader-spinner {
    width: 14px;
    height: 14px;
    border: 1px solid var(--border);
    border-radius: 999px;
    border-top-color: var(--text-subtle);
    animation: history-loader-spin 0.8s linear infinite;
  }

  @keyframes history-loader-spin {
    to {
      transform: rotate(360deg);
    }
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

  .assistant-pending-row {
    padding-left: 10px;
    overflow-anchor: none;
  }

  .assistant-pending-row.assistant-pending-delayed {
    visibility: hidden;
    animation: assistant-pending-reveal 0s linear 500ms forwards;
  }

  .assistant-pending {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    height: 24px;
  }

  .assistant-pending span {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: var(--text-subtle);
    animation: assistant-pending-dot 1.2s ease-in-out infinite;
  }

  .assistant-pending span:nth-child(2) { animation-delay: 0.15s; }
  .assistant-pending span:nth-child(3) { animation-delay: 0.3s; }

  @keyframes assistant-pending-dot {
    0%, 60%, 100% {
      opacity: 0.25;
      transform: translateY(0);
    }
    30% {
      opacity: 1;
      transform: translateY(-2px);
    }
  }

  @keyframes assistant-pending-reveal {
    to { visibility: visible; }
  }

  .process-summary-row {
    justify-content: flex-start;
  }

  .process-summary-toggle {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    margin-left: 10px;
    padding: 0;
    border: 0;
    background: transparent;
    color: var(--text-muted);
    font: inherit;
    font-size: 0.9rem;
    line-height: 1.4;
    cursor: pointer;
  }

  .process-summary-toggle:hover,
  .process-summary-toggle:focus-visible {
    color: var(--text);
    outline: none;
  }

  .process-summary-toggle:focus-visible {
    box-shadow: 0 2px 0 var(--focus-ring);
  }

  .process-summary-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex: 0 0 auto;
    width: 14px;
    height: 14px;
    color: var(--text-subtle);
    line-height: 0;
    opacity: 0;
    transition:
      opacity 0.16s ease,
      transform 0.16s ease;
  }

  .process-summary-icon :global(svg) {
    display: block;
  }

  .process-summary-toggle:hover .process-summary-icon,
  .process-summary-toggle:focus-visible .process-summary-icon,
  .process-summary-toggle.expanded .process-summary-icon {
    opacity: 1;
  }

  .process-summary-toggle.expanded .process-summary-icon {
    transform: rotate(90deg);
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

  .thinking-stream-line {
    display: flex;
    align-items: center;
    gap: 8px;
    max-width: min(720px, 100%);
    color: var(--text-muted);
    font-size: 0.9rem;
    min-height: 24px;
    line-height: 24px;
  }

  .thinking-stream-icon {
    display: flex;
    flex: 0 0 auto;
    color: var(--text-muted);
  }

  .thinking-stream-text {
    min-width: 0;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
    background:
      linear-gradient(
        90deg,
        var(--text-subtle) 0%,
        var(--text-muted) 42%,
        var(--text) 50%,
        var(--text-muted) 58%,
        var(--text-subtle) 100%
      );
    background-size: 240% 100%;
    background-clip: text;
    -webkit-background-clip: text;
    color: transparent;
    animation: thinking-stream-shimmer 2.4s linear infinite;
  }

  @keyframes thinking-stream-shimmer {
    from { background-position: 120% 0; }
    to { background-position: -120% 0; }
  }

  @media (prefers-reduced-motion: reduce) {
    .thinking-stream-text {
      animation: none;
      background: none;
      color: var(--text-muted);
    }
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

  .tool-inline-name.tool-inline-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 18px;
    height: 18px;
    line-height: 0;
  }

  .tool-inline-icon :global(svg) {
    display: block;
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

  .tool-inline-meta {
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

  @media (max-width: 900px) {
    .chat-transcript {
      padding: 42px 16px 48px;
      gap: 6px;
    }

    .scroll-bottom-overlay {
      bottom: 0;
    }
  }

  @media (max-width: 640px) {
    .chat-transcript {
      padding: 42px 12px 48px;
    }

    .message-content.user {
      max-width: min(580px, 100%);
      padding: 10px 14px;
      border-radius: 16px;
    }

    .conversation-skeleton {
      gap: 18px;
      padding-inline: 2px;
    }

    .conversation-skeleton-row { width: 82%; }
    .conversation-skeleton-row.user { width: 72%; }

  }

  @media (prefers-reduced-motion: reduce) {
    .conversation-skeleton-line,
    .conversation-skeleton-bubble {
      animation: none;
    }
  }
</style>
