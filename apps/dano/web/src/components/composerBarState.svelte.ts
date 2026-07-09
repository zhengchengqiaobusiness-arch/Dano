import type {
  RpcImageContent,
  RpcSlashCommand,
  RpcThinkingLevel,
  RpcUploadedFileRef,
  RpcWorkspaceEntry,
} from "@dano/types/protocol";
import type { ConnectionStatus } from "../composables/bridgeStore.svelte";
import { t } from "../i18n";
import {
  MAX_COMPOSER_ATTACHMENT_BYTES,
  MAX_COMPOSER_ATTACHMENTS,
  createUploadingComposerAttachment,
  toRpcImageContent,
  toRpcUploadedFileRefs,
  markComposerAttachmentOrphaned,
  uploadComposerAttachment,
  type ComposerAttachment,
} from "../utils/attachments";
import type { RpcModelInfo } from "../utils/models";
import {
  applySlashCommandCompletion,
  debugSlashCommandOptions,
  getSlashCommandContext,
  mergeSlashCommandOptions,
  parseCompactSlashCommand,
  slashCommandOptionsFromRpc,
} from "../utils/slashCommands";
import { getNextThinkingLevel } from "../utils/thinkingLevels";
import {
  applyWorkspaceMentionCompletion,
  getWorkspaceMentionContext,
  getWorkspaceMentionSuggestions,
  type WorkspaceMentionSuggestion,
} from "../utils/workspaceMentions";
import {
  shouldEnterInsertNewline,
  shouldSubmitComposerEnter,
} from "./composerKeyboard";
import { canSubmitComposerMessage } from "./composerSubmit";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ComposerBarProps {
  readonly connectionStatus: ConnectionStatus;
  readonly isStreaming: boolean;
  readonly isDebugMode: boolean;
  readonly commands: readonly RpcSlashCommand[];
  readonly workspaceEntries: readonly RpcWorkspaceEntry[];
  readonly workspaceEntriesLoading: boolean;
  readonly workspaceContextKey: string | null;
  readonly ensureWorkspaceEntries: (
    force?: boolean,
  ) => Promise<RpcWorkspaceEntry[]>;
  readonly models: readonly RpcModelInfo[];
  readonly selectedModel: RpcModelInfo | null;
  readonly thinkingLevel: RpcThinkingLevel | null;
  readonly autoCompactionEnabled: boolean;
  readonly prefillText: string | null;
  readonly revision: RevisionPayload | null;
  readonly pendingMessageCount: number;
  readonly editQueuedPayload: EditQueuedPayload | null;
}

export interface ComposerBarCallbacks {
  readonly onSubmit: (payload: {
    message: string;
    images: RpcImageContent[];
    files: RpcUploadedFileRef[];
    revisionEntryId?: string;
    steer?: boolean;
  }) => boolean | Promise<boolean>;
  readonly onAbort: () => void;
  readonly onCancelRevision: () => void;
  readonly onSelectModel: (model: RpcModelInfo) => void;
  readonly onSelectThinkingLevel: (level: RpcThinkingLevel) => void;
  readonly onToggleAutoCompaction: (enabled: boolean) => void;
}

/** Externally-owned reactive variables that the state module reads/writes. */
export interface ComposerBarReactive {
  inputText: string;
  cursorOffset: number;
}

export interface RevisionPayload {
  entryId: string;
  text: string;
  preview: string;
  hasImages: boolean;
  images: RpcImageContent[];
}

export interface EditQueuedPayload {
  text: string;
  images: RpcImageContent[];
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEFAULT_MAX_TEXTAREA_VISIBLE_LINES = 5;

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

function restoredAttachmentExtension(mimeType: string): string {
  switch (mimeType) {
    case "image/png":
      return "png";
    case "image/jpeg":
      return "jpg";
    case "image/gif":
      return "gif";
    case "image/webp":
      return "webp";
    default:
      return "img";
  }
}

function restoredAttachmentSize(base64Data: string): number {
  const n = base64Data.replace(/\s+/g, "");
  if (!n) return 0;
  const p = n.endsWith("==") ? 2 : n.endsWith("=") ? 1 : 0;
  return Math.max(0, Math.floor((n.length * 3) / 4) - p);
}

function restoredAttachmentId(index: number): string {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `restored_attachment_${Date.now().toString(36)}_${index.toString(36)}_${Math.random().toString(36).slice(2)}`;
}

function attachmentsFromRpcImages(
  images: readonly RpcImageContent[] | undefined,
): ComposerAttachment[] {
  if (!images?.length) return [];
  return images.map((image, idx) => {
    const ext = restoredAttachmentExtension(image.mimeType);
    return {
      id: restoredAttachmentId(idx),
      type: "image",
      data: image.data,
      mimeType: image.mimeType,
      name: `image-${idx + 1}.${ext}`,
      size: restoredAttachmentSize(image.data),
      previewUrl: `data:${image.mimeType};base64,${image.data}`,
      status: "uploaded",
    };
  });
}

export function normalizeSubmittedText(value: string): string {
  const normalized = value.replace(/\r\n/g, "\n");
  const lines = normalized.split("\n");
  while (lines.length > 0 && lines[0]?.trim() === "") lines.shift();
  while (lines.length > 0 && lines[lines.length - 1]?.trim() === "")
    lines.pop();
  if (lines.length === 0) return "";
  lines[0] = lines[0]!.trimStart();
  lines[lines.length - 1] = lines[lines.length - 1]!.trimEnd();
  return lines.join("\n");
}

function getCommandKey(
  ctx: ReturnType<typeof getSlashCommandContext> | null,
): string | null {
  if (!ctx) return null;
  return `${ctx.start}:${ctx.query}`;
}

function getMentionKey(
  ctx: ReturnType<typeof getWorkspaceMentionContext> | null,
): string | null {
  if (!ctx) return null;
  return `${ctx.start}:${ctx.prefix}`;
}

// ---------------------------------------------------------------------------
// Composable
// ---------------------------------------------------------------------------

export function createComposerBarState(
  props: ComposerBarProps,
  callbacks: ComposerBarCallbacks,
  $rx: ComposerBarReactive,
) {
  // ---- core mutable state owned by this module ----

  let isComposing = $state(false);
  let attachments = $state<ComposerAttachment[]>([]);
  let isDragActive = $state(false);
  let dragDepth = 0;

  // ---- palette dismissal state ----

  let dismissedCommandKey = $state<string | null>(null);
  let dismissedMentionKey = $state<string | null>(null);
  let mentionInteractionWorkspaceKey = $state<string | null>(null);

  // ---- attachment notice ----

  let attachmentNotice = $state<string | null>(null);
  let attachmentNoticeTimer: ReturnType<typeof setTimeout> | null = null;

  // ---- revision backup ----

  let revisionBackup = $state<{
    text: string;
    attachments: ComposerAttachment[];
  } | null>(null);

  // ---- derived state (uses $rx for inputText/cursorOffset) ----

  let isDisabled = $derived(props.connectionStatus !== "connected");
  let canEditPrompt = $derived(props.connectionStatus !== "connecting");
  let availableSlashCommands = $derived.by(() => {
    const baseCommands = props.isDebugMode
      ? debugSlashCommandOptions()
      : slashCommandOptionsFromRpc(props.commands);
    return mergeSlashCommandOptions(
      baseCommands,
      props.isDebugMode ? [] : undefined,
    );
  });

  let commandContext = $derived(
    getSlashCommandContext($rx.inputText, $rx.cursorOffset),
  );

  let filteredSlashCommands = $derived.by(() => {
    if (!commandContext) return [];
    const query = commandContext.query.toLowerCase();
    if (!query) return availableSlashCommands;
    return availableSlashCommands.filter(
      c =>
        c.name.toLowerCase().includes(query) ||
        (c.description ?? "").toLowerCase().includes(query),
    );
  });

  let mentionContext = $derived(
    getWorkspaceMentionContext($rx.inputText, $rx.cursorOffset),
  );

  let mentionSuggestions = $derived.by(() => {
    if (!mentionContext) return [];
    return getWorkspaceMentionSuggestions(
      props.workspaceEntries,
      mentionContext,
    );
  });

  let showCommandPalette = $derived.by(() => {
    if (isDisabled || !commandContext) return false;
    return dismissedCommandKey !== getCommandKey(commandContext);
  });

  let showMentionPalette = $derived.by(() => {
    if (showCommandPalette || !mentionContext) return false;
    if (dismissedMentionKey === getMentionKey(mentionContext)) return false;
    if (props.workspaceEntriesLoading) return true;
    return true;
  });

  let currentModelText = $derived.by(() => {
    if (!props.selectedModel)
      return props.models.length > 0
        ? t("composer.state.chooseModel")
        : t("composer.state.noModels");
    return props.selectedModel.name ?? props.selectedModel.id;
  });

  let normalizedInputText = $derived(normalizeSubmittedText($rx.inputText));
  let hasAttachments = $derived(attachments.length > 0);
  let hasUploadingAttachments = $derived(
    attachments.some(attachment => attachment.status === "uploading"),
  );
  let hasFailedAttachments = $derived(
    attachments.some(attachment => attachment.status === "failed"),
  );
  let hasSubmittableAttachments = $derived(
    attachments.some(
      attachment =>
        attachment.status === "uploaded" && (attachment.file || attachment.data),
    ),
  );
  let canAddAttachments = $derived(
    !isDisabled && attachments.length < MAX_COMPOSER_ATTACHMENTS,
  );
  let canSubmit = $derived(
    canSubmitComposerMessage({
      connectionStatus: props.connectionStatus,
      hasUploadingAttachments,
      hasFailedAttachments,
      hasText: normalizedInputText.length > 0,
      hasSubmittableAttachments,
    }),
  );
  let canAbort = $derived(!isDisabled && props.isStreaming);
  let showStopButton = $derived(props.isStreaming && !canSubmit);
  let hasPendingMessages = $derived(props.pendingMessageCount > 0);
  let attachmentSummary = $derived(attachmentNotice ?? "");

  // ---- attachment notice helpers ----

  function clearAttachmentNotice() {
    if (attachmentNoticeTimer) {
      clearTimeout(attachmentNoticeTimer);
      attachmentNoticeTimer = null;
    }
    attachmentNotice = null;
  }

  function setAttachmentNotice(message: string | null) {
    clearAttachmentNotice();
    attachmentNotice = message;
    if (!message) return;
    attachmentNoticeTimer = setTimeout(() => {
      attachmentNotice = null;
      attachmentNoticeTimer = null;
    }, 4000);
  }

  // ---- attachment management ----

  function clearAttachments(fileInputEl?: HTMLInputElement | null) {
    for (const attachment of attachments) disposeAttachment(attachment);
    attachments = [];
    if (fileInputEl) fileInputEl.value = "";
  }

  async function addAttachmentsFromFiles(
    files: Iterable<File> | ArrayLike<File> | null | undefined,
  ) {
    if (!canAddAttachments) return;
    if (!files) return;
    const incomingFiles = Array.from(files);
    if (!incomingFiles.length) return;

    const rejectedNames: string[] = [];
    const nextAttachments: Array<{ attachment: ComposerAttachment; file: File }> = [];
    const remainingSlots = Math.max(0, MAX_COMPOSER_ATTACHMENTS - attachments.length);

    for (const file of incomingFiles) {
      if (nextAttachments.length >= remainingSlots) {
        rejectedNames.push(file.name);
        continue;
      }
      if (file.size > MAX_COMPOSER_ATTACHMENT_BYTES) {
        rejectedNames.push(file.name);
        continue;
      }
      const abortController = new AbortController();
      nextAttachments.push({
        file,
        attachment: createUploadingComposerAttachment(file, abortController),
      });
    }

    if (nextAttachments.length > 0) {
      attachments = [...attachments, ...nextAttachments.map(item => item.attachment)];
      setAttachmentNotice(null);
      for (const item of nextAttachments) {
        void finishAttachmentUpload(item.attachment.id, item.file);
      }
    }
    if (rejectedNames.length > 0) {
      setAttachmentNotice(
        t("composerBar.skippedUnsupportedFiles", {
          count: rejectedNames.length,
        }),
      );
    }
  }

  async function finishAttachmentUpload(id: string, file: File) {
    const attachment = attachments.find(a => a.id === id);
    if (!attachment?.abortController) return;
    try {
      const uploaded = await uploadComposerAttachment(
        file,
        attachment.abortController.signal,
      );
      if (uploaded.previewUrl && attachment.previewUrl?.startsWith("blob:")) {
        URL.revokeObjectURL(attachment.previewUrl);
      }
      attachments = attachments.map(item =>
        item.id === id
          ? {
              ...item,
              status: "uploaded",
              file: { ...uploaded, name: item.name },
              previewUrl:
                item.type === "image"
                  ? (uploaded.previewUrl ?? item.previewUrl)
                  : undefined,
              abortController: undefined,
            }
          : item,
      );
    } catch (error) {
      if ((error as { name?: unknown }).name === "AbortError") return;
      attachments = attachments.map(item =>
        item.id === id
          ? {
              ...item,
              status: "failed",
              error: error instanceof Error ? error.message : String(error),
              abortController: undefined,
            }
          : item,
      );
    }
  }

  function disposeAttachment(attachment: ComposerAttachment) {
    attachment.abortController?.abort();
    if (attachment.previewUrl?.startsWith("blob:")) {
      URL.revokeObjectURL(attachment.previewUrl);
    }
  }

  function removeAttachment(id: string) {
    const index = attachments.findIndex(a => a.id === id);
    if (index === -1) return;
    const attachment = attachments[index]!;
    disposeAttachment(attachment);
    if (attachment.status === "uploaded" && attachment.file) {
      void markComposerAttachmentOrphaned(attachment.file);
    }
    attachments = attachments.filter(a => a.id !== id);
    if (attachments.length === 0) clearAttachmentNotice();
  }

  // ---- textarea helpers (need DOM refs) ----

  function syncCursorFromTextarea(
    textareaEl: HTMLTextAreaElement | null | undefined,
  ) {
    $rx.cursorOffset = textareaEl?.selectionStart ?? $rx.inputText.length;
  }

  function resizeTextarea(textareaEl: HTMLTextAreaElement | null | undefined) {
    queueMicrotask(() => {
      const el = textareaEl;
      if (!el) return;

      el.style.height = "auto";
      const styles = window.getComputedStyle(el);
      const lineHeight = Number.parseFloat(styles.lineHeight) || 0;
      const paddingTop = Number.parseFloat(styles.paddingTop) || 0;
      const paddingBottom = Number.parseFloat(styles.paddingBottom) || 0;
      const maxVisibleLines =
        Number.parseInt(
          styles.getPropertyValue("--composer-max-visible-lines"),
          10,
        ) || DEFAULT_MAX_TEXTAREA_VISIBLE_LINES;
      const contentPadding = paddingTop + paddingBottom;
      const singleLineHeight = lineHeight + contentPadding;
      const maxHeight = lineHeight * maxVisibleLines + contentPadding;
      const nextHeight = Math.min(
        Math.max(el.scrollHeight, singleLineHeight),
        maxHeight,
      );

      el.style.height = `${Math.ceil(nextHeight)}px`;
      el.style.overflowY = el.scrollHeight > maxHeight ? "auto" : "hidden";
    });
  }

  function shouldRevealComposer(
    rootEl: HTMLDivElement | null | undefined,
  ): boolean {
    if (typeof window === "undefined") return false;
    const el = rootEl;
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const vh = window.innerHeight || 0;
    const margin = 24;
    return rect.top < margin || rect.bottom > vh - margin;
  }

  function focusComposer(opts?: {
    textareaEl?: HTMLTextAreaElement | null;
    rootEl?: HTMLDivElement | null;
    reveal?: boolean;
  }) {
    queueMicrotask(() => {
      if (opts?.reveal && shouldRevealComposer(opts.rootEl)) {
        opts.rootEl?.scrollIntoView({ behavior: "smooth", block: "end" });
      }
      const el = opts?.textareaEl;
      if (!el) return;
      el.focus();
      const cursor = $rx.inputText.length;
      el.setSelectionRange(cursor, cursor);
      $rx.cursorOffset = cursor;
      resizeTextarea(el);
    });
  }

  function applyExternalText(
    text: string,
    opts?: {
      clearAttachments?: boolean;
      fileInputEl?: HTMLInputElement | null;
      textareaEl?: HTMLTextAreaElement | null;
      rootEl?: HTMLDivElement | null;
    },
  ) {
    $rx.inputText = text;
    if (opts?.clearAttachments) clearAttachments(opts.fileInputEl);
    clearAttachmentNotice();
    dismissedCommandKey = null;
    dismissedMentionKey = null;
    focusComposer({
      textareaEl: opts?.textareaEl,
      rootEl: opts?.rootEl,
      reveal: true,
    });
  }

  // ---- submission ----

  function resetComposerState(fileInputEl?: HTMLInputElement | null) {
    $rx.inputText = "";
    $rx.cursorOffset = 0;
    dismissedCommandKey = null;
    dismissedMentionKey = null;
    revisionBackup = null;
    clearAttachments(fileInputEl);
    clearAttachmentNotice();
  }

  async function submitMessage(
    message: string,
    steer: boolean,
    fileInputEl?: HTMLInputElement | null,
    textareaEl?: HTMLTextAreaElement | null,
  ): Promise<boolean> {
    const accepted = await callbacks.onSubmit({
      message,
      images: toRpcImageContent(attachments),
      files: toRpcUploadedFileRefs(attachments),
      revisionEntryId: props.revision?.entryId,
      steer,
    });
    if (!accepted) return false;
    resetComposerState(fileInputEl);
    resizeTextarea(textareaEl);
    return true;
  }

  async function handleSubmit(
    steer: boolean,
    fileInputEl?: HTMLInputElement | null,
    textareaEl?: HTMLTextAreaElement | null,
  ): Promise<boolean> {
    const text = normalizedInputText;
    if (!canSubmit) return false;
    if (parseCompactSlashCommand(text) && hasAttachments) {
      setAttachmentNotice(t("composer.warning.compactNoImages"));
      return false;
    }
    return submitMessage(text, steer, fileInputEl, textareaEl);
  }

  function handleAbortAction(): boolean {
    if (!canAbort) return false;
    callbacks.onAbort();
    return true;
  }

  // ---- slash commands ----

  function handleCommandSelect(
    commandName: string,
    textareaEl?: HTMLTextAreaElement | null,
  ) {
    const cmd = availableSlashCommands.find(c => c.name === commandName);
    const ctx = commandContext;
    if (!cmd || !ctx) return;
    const ns = applySlashCommandCompletion($rx.inputText, ctx, cmd);
    $rx.inputText = ns.text;
    dismissedCommandKey = null;
    queueMicrotask(() => {
      const el = textareaEl;
      if (!el) return;
      el.focus();
      el.setSelectionRange(ns.cursor, ns.cursor);
      $rx.cursorOffset = ns.cursor;
      resizeTextarea(el);
    });
  }

  function handleCommandClose() {
    dismissedCommandKey = getCommandKey(commandContext);
  }

  // ---- workspace mentions ----

  function handleMentionSelect(
    item: WorkspaceMentionSuggestion,
    textareaEl?: HTMLTextAreaElement | null,
  ) {
    const mention = mentionContext;
    if (!mention) return;
    const ns = applyWorkspaceMentionCompletion(
      $rx.inputText,
      $rx.cursorOffset,
      mention,
      item,
    );
    $rx.inputText = ns.text;
    dismissedMentionKey = null;
    queueMicrotask(() => {
      const el = textareaEl;
      if (!el) return;
      el.focus();
      el.setSelectionRange(ns.cursor, ns.cursor);
      $rx.cursorOffset = ns.cursor;
      resizeTextarea(el);
    });
  }

  function handleMentionClose() {
    dismissedMentionKey = getMentionKey(mentionContext);
  }

  // ---- thinking / compaction toggles ----

  function handleCycleThinkingLevel() {
    if (isDisabled) return;
    callbacks.onSelectThinkingLevel(getNextThinkingLevel(props.thinkingLevel));
  }

  function handleAutoCompactionToggle() {
    if (isDisabled) return;
    callbacks.onToggleAutoCompaction(!props.autoCompactionEnabled);
  }

  // ---- revision ----

  function handleCancelRevision(
    fileInputEl?: HTMLInputElement | null,
    textareaEl?: HTMLTextAreaElement | null,
    rootEl?: HTMLDivElement | null,
  ) {
    const backup = revisionBackup;
    $rx.inputText = backup?.text ?? "";
    attachments = backup ? [...backup.attachments] : [];
    if (fileInputEl) fileInputEl.value = "";
    revisionBackup = null;
    clearAttachmentNotice();
    dismissedCommandKey = null;
    dismissedMentionKey = null;
    callbacks.onCancelRevision();
    focusComposer({ textareaEl, rootEl });
  }

  // ---- file input / drag-drop / paste ----

  function handleFilePickerOpen(fileInputEl?: HTMLInputElement | null) {
    fileInputEl?.click();
  }

  async function handleFileInputChange(
    event: Event,
    fileInputEl?: HTMLInputElement | null,
  ) {
    const files = (event.target as HTMLInputElement | null)?.files;
    await addAttachmentsFromFiles(files);
    if (fileInputEl) fileInputEl.value = "";
  }

  function hasFilePayload(dataTransfer: DataTransfer | null): boolean {
    return Array.from(dataTransfer?.types ?? []).includes("Files");
  }

  function preventFileDropNavigation(event: DragEvent) {
    event.preventDefault();
    event.stopPropagation();
  }

  function extractPastedFiles(event: ClipboardEvent): File[] {
    const directFiles = Array.from(event.clipboardData?.files ?? []);
    if (directFiles.length > 0) return directFiles;
    return Array.from(event.clipboardData?.items ?? [])
      .filter(i => i.kind === "file")
      .map(i => i.getAsFile())
      .filter((f): f is File => f !== null);
  }

  async function handleInputPaste(event: ClipboardEvent) {
    const pastedFiles = extractPastedFiles(event);
    if (pastedFiles.length === 0) return;
    event.preventDefault();
    await addAttachmentsFromFiles(pastedFiles);
  }

  // ---- drag / drop ----

  function handleDragEnter(event: DragEvent) {
    if (!hasFilePayload(event.dataTransfer)) return;
    preventFileDropNavigation(event);
    dragDepth += 1;
    isDragActive = true;
  }

  function handleDragOver(event: DragEvent) {
    if (!hasFilePayload(event.dataTransfer)) return;
    preventFileDropNavigation(event);
    if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
    isDragActive = true;
  }

  function handleDragLeave(event: DragEvent) {
    if (!hasFilePayload(event.dataTransfer)) return;
    event.stopPropagation();
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) isDragActive = false;
  }

  async function handleDrop(event: DragEvent) {
    if (!hasFilePayload(event.dataTransfer)) return;
    preventFileDropNavigation(event);
    dragDepth = 0;
    isDragActive = false;
    await addAttachmentsFromFiles(event.dataTransfer?.files);
  }

  // ---- keyboard / composition ----

  function isInputComposing(event: KeyboardEvent): boolean {
    return event.isComposing || isComposing || event.keyCode === 229;
  }

  function handleInputCompositionStart() {
    isComposing = true;
  }

  function handleInputCompositionEnd(textareaEl?: HTMLTextAreaElement | null) {
    isComposing = false;
    handleInputInteraction(textareaEl);
  }

  function handleInputInteraction(textareaEl?: HTMLTextAreaElement | null) {
    syncCursorFromTextarea(textareaEl);
  }

  function handleInputKeydown(
    e: KeyboardEvent,
    refs: {
      textareaEl?: HTMLTextAreaElement | null;
      commandPaletteEl?: { handleKeydown: (e: KeyboardEvent) => void } | null;
      mentionPaletteEl?: { handleKeydown: (e: KeyboardEvent) => void } | null;
    },
    steer: boolean | (() => boolean),
  ) {
    const composing = isInputComposing(e);
    const enterInsertsNewline = shouldEnterInsertNewline();
    const plainEnterSelectsPalette =
      !enterInsertsNewline && !e.shiftKey && e.key === "Enter";

    // Shift+Tab → cycle thinking
    if (
      e.key === "Tab" &&
      e.shiftKey &&
      !e.altKey &&
      !e.ctrlKey &&
      !e.metaKey &&
      !composing
    ) {
      e.preventDefault();
      handleCycleThinkingLevel();
      return;
    }

    // Palette navigation
    if (
      showCommandPalette &&
      refs.commandPaletteEl &&
      (e.key === "ArrowDown" ||
        e.key === "ArrowUp" ||
        e.key === "Escape" ||
        (filteredSlashCommands.length > 0 &&
          !composing &&
          (plainEnterSelectsPalette || e.key === "Tab")))
    ) {
      refs.commandPaletteEl.handleKeydown(e);
      return;
    }

    if (
      showMentionPalette &&
      refs.mentionPaletteEl &&
      (e.key === "ArrowDown" ||
        e.key === "ArrowUp" ||
        e.key === "Escape" ||
        ((props.workspaceEntriesLoading || mentionSuggestions.length > 0) &&
          !composing &&
          (plainEnterSelectsPalette || e.key === "Tab")))
    ) {
      refs.mentionPaletteEl.handleKeydown(e);
      return;
    }

    // Escape → abort
    if (e.key === "Escape" && props.isStreaming) {
      e.preventDefault();
      handleAbortAction();
      return;
    }

    // Enter → submit / steer
    if (e.key === "Enter") {
      if (!shouldSubmitComposerEnter(e, composing, enterInsertsNewline)) return;
      e.preventDefault();
      const isSteer = typeof steer === "function" ? steer() : steer;
      void handleSubmit(isSteer);
    }
  }

  // ---- effects ----

  $effect(() => {
    const cmdKey = getCommandKey(commandContext);
    if (cmdKey && cmdKey !== (dismissedCommandKey ?? undefined)) {
      dismissedCommandKey = null;
    }
  });

  $effect(() => {
    void [mentionContext, props.workspaceContextKey];
    const mk = getMentionKey(mentionContext);
    if (mk && mk !== (dismissedMentionKey ?? undefined)) {
      dismissedMentionKey = null;
    }

    if (!mentionContext) {
      mentionInteractionWorkspaceKey = null;
      return;
    }
    const nik = `${props.workspaceContextKey ?? ""}:${mentionContext.start}`;
    if (mentionInteractionWorkspaceKey === nik) return;
    mentionInteractionWorkspaceKey = nik;
    void props.ensureWorkspaceEntries();
  });

  let previousRevision: RevisionPayload | null = null;
  $effect(() => {
    const rev = props.revision;
    if (!rev) {
      revisionBackup = null;
      previousRevision = null;
      return;
    }
    if (!previousRevision && !revisionBackup) {
      revisionBackup = {
        text: $rx.inputText,
        attachments: [...attachments],
      };
    }
    $rx.inputText = rev.text;
    attachments = attachmentsFromRpcImages(rev.images);
    previousRevision = rev;
  });

  $effect(() => {
    const payload = props.editQueuedPayload;
    if (!payload) return;
    $rx.inputText = payload.text;
    attachments = attachmentsFromRpcImages(payload.images);
    clearAttachmentNotice();
    dismissedCommandKey = null;
    dismissedMentionKey = null;
    revisionBackup = null;
  });

  // ---- return public API ----

  return {
    // state owned by module
    get attachments() {
      return attachments;
    },
    get isDragActive() {
      return isDragActive;
    },
    // get isComposing() { return isComposing; }, // not read from template
    get attachmentNotice() {
      return attachmentNotice;
    },
    // derived
    get isDisabled() {
      return isDisabled;
    },
    get canEditPrompt() {
      return canEditPrompt;
    },
    get availableSlashCommands() {
      return availableSlashCommands;
    },
    get commandContext() {
      return commandContext;
    },
    get filteredSlashCommands() {
      return filteredSlashCommands;
    },
    get mentionContext() {
      return mentionContext;
    },
    get mentionSuggestions() {
      return mentionSuggestions;
    },
    get showCommandPalette() {
      return showCommandPalette;
    },
    get showMentionPalette() {
      return showMentionPalette;
    },
    get currentModelText() {
      return currentModelText;
    },
    get normalizedInputText() {
      return normalizedInputText;
    },
    get hasAttachments() {
      return hasAttachments;
    },
    get canSubmit() {
      return canSubmit;
    },
    get canAddAttachments() {
      return canAddAttachments;
    },
    get canAbort() {
      return canAbort;
    },
    get showStopButton() {
      return showStopButton;
    },
    get hasPendingMessages() {
      return hasPendingMessages;
    },
    get attachmentSummary() {
      return attachmentSummary;
    },

    // methods
    addAttachmentsFromFiles,
    removeAttachment,
    clearAttachments,
    clearAttachmentNotice,
    setAttachmentNotice,

    handleSubmit,
    handleAbortAction,
    handleCommandSelect,
    handleCommandClose,
    handleMentionSelect,
    handleMentionClose,
    handleCycleThinkingLevel,
    handleAutoCompactionToggle,
    handleCancelRevision,
    handleFilePickerOpen,
    handleFileInputChange,
    handleInputPaste,
    handleDragEnter,
    handleDragOver,
    handleDragLeave,
    handleDrop,
    handleInputCompositionStart,
    handleInputCompositionEnd,
    handleInputInteraction,
    handleInputKeydown,
    applyExternalText,
    focusComposer,
    resizeTextarea,
  };
}
