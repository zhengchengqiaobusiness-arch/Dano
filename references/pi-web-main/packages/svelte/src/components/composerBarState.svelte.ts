import type {
  RpcImageContent,
  RpcSlashCommand,
  RpcThinkingLevel,
  RpcWorkspaceEntry,
} from "@pi-web/bridge/types";
import type { ConnectionStatus } from "../composables/bridgeStore.svelte";
import {
  createComposerAttachments,
  extractSupportedImageFiles,
  toRpcImageContent,
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
import type { ImageContentBlock } from "../utils/transcript";
import {
  applyWorkspaceMentionCompletion,
  getWorkspaceMentionContext,
  getWorkspaceMentionSuggestions,
  type WorkspaceMentionSuggestion,
} from "../utils/workspaceMentions";

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
    revisionEntryId?: string;
    steer?: boolean;
  }) => void;
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

const MAX_TEXTAREA_HEIGHT = 160;
const TEXTAREA_HEIGHT_BUFFER = 4;

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

  // ---- lightbox ----

  let lightboxAttachmentIndex = $state(-1);

  // ---- revision backup ----

  let revisionBackup = $state<{
    text: string;
    attachments: ComposerAttachment[];
  } | null>(null);

  // ---- derived state (uses $rx for inputText/cursorOffset) ----

  let isDisabled = $derived(props.connectionStatus !== "connected");
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
      return props.models.length > 0 ? "choose model" : "no models";
    return props.selectedModel.name ?? props.selectedModel.id;
  });

  let normalizedInputText = $derived(normalizeSubmittedText($rx.inputText));
  let hasAttachments = $derived(attachments.length > 0);
  let canSubmit = $derived(
    !isDisabled && (normalizedInputText.length > 0 || hasAttachments),
  );
  let canAbort = $derived(!isDisabled && props.isStreaming);
  let showStopButton = $derived(props.isStreaming && !canSubmit);
  let hasPendingMessages = $derived(props.pendingMessageCount > 0);
  let attachmentSummary = $derived(attachmentNotice ?? "");

  let lightboxImages = $derived.by(() =>
    attachments.map<ImageContentBlock>(a => ({
      kind: "image",
      src: a.previewUrl,
      alt: a.name,
      mimeType: a.mimeType,
    })),
  );

  let lightboxOpen = $derived(
    lightboxAttachmentIndex >= 0 &&
      lightboxAttachmentIndex < attachments.length,
  );

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
    attachments = [];
    lightboxAttachmentIndex = -1;
    if (fileInputEl) fileInputEl.value = "";
  }

  async function addAttachmentsFromFiles(
    files: Iterable<File> | ArrayLike<File> | null | undefined,
  ) {
    if (!files) return;
    const incomingFiles = Array.from(files);
    if (!incomingFiles.length) return;
    const { attachments: nextAttachments, rejectedNames } =
      await createComposerAttachments(incomingFiles);
    if (nextAttachments.length > 0) {
      attachments = [...attachments, ...nextAttachments];
      setAttachmentNotice(null);
    }
    if (rejectedNames.length > 0) {
      setAttachmentNotice(
        `Skipped unsupported files: ${rejectedNames.join(", ")}`,
      );
    }
  }

  function removeAttachment(id: string) {
    const index = attachments.findIndex(a => a.id === id);
    if (index === -1) return;
    const nextAttachments = attachments.filter(a => a.id !== id);
    if (lightboxAttachmentIndex === index) {
      lightboxAttachmentIndex =
        nextAttachments.length > 0
          ? Math.min(index, nextAttachments.length - 1)
          : -1;
    } else if (lightboxAttachmentIndex > index) {
      lightboxAttachmentIndex -= 1;
    }
    attachments = nextAttachments;
    if (attachments.length === 0) clearAttachmentNotice();
  }

  // ---- lightbox ----

  function openAttachmentLightbox(index: number) {
    if (index < 0 || index >= attachments.length) return;
    lightboxAttachmentIndex = index;
  }

  function closeAttachmentLightbox() {
    lightboxAttachmentIndex = -1;
  }

  function showPreviousAttachmentLightboxImage() {
    if (attachments.length <= 1 || lightboxAttachmentIndex < 0) return;
    lightboxAttachmentIndex =
      (lightboxAttachmentIndex + attachments.length - 1) % attachments.length;
  }

  function showNextAttachmentLightboxImage() {
    if (attachments.length <= 1 || lightboxAttachmentIndex < 0) return;
    lightboxAttachmentIndex =
      (lightboxAttachmentIndex + 1) % attachments.length;
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
      const pt = Number.parseFloat(styles.paddingTop) || 0;
      const pb = Number.parseFloat(styles.paddingBottom) || 0;
      const minHeight = Math.ceil(
        lineHeight + pt + pb + TEXTAREA_HEIGHT_BUFFER,
      );
      const nextHeight = Math.min(
        Math.max(el.scrollHeight + TEXTAREA_HEIGHT_BUFFER, minHeight),
        MAX_TEXTAREA_HEIGHT,
      );
      el.style.height = `${nextHeight}px`;
      el.style.overflowY =
        el.scrollHeight > MAX_TEXTAREA_HEIGHT ? "auto" : "hidden";
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

  function submitMessage(
    message: string,
    steer: boolean,
    fileInputEl?: HTMLInputElement | null,
    textareaEl?: HTMLTextAreaElement | null,
  ) {
    callbacks.onSubmit({
      message,
      images: toRpcImageContent(attachments),
      revisionEntryId: props.revision?.entryId,
      steer,
    });
    resetComposerState(fileInputEl);
    resizeTextarea(textareaEl);
  }

  function handleSubmit(
    steer: boolean,
    fileInputEl?: HTMLInputElement | null,
    textareaEl?: HTMLTextAreaElement | null,
  ): boolean {
    const text = normalizedInputText;
    if ((!text && !hasAttachments) || isDisabled) return false;
    if (parseCompactSlashCommand(text) && hasAttachments) {
      setAttachmentNotice("/compact does not accept image attachments");
      return false;
    }
    submitMessage(text, steer, fileInputEl, textareaEl);
    return true;
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

  function extractPastedFiles(event: ClipboardEvent): File[] {
    const directFiles = extractSupportedImageFiles(event.clipboardData?.files);
    if (directFiles.length > 0) return directFiles;
    const pastedFiles = Array.from(event.clipboardData?.items ?? [])
      .filter(i => i.kind === "file")
      .map(i => i.getAsFile())
      .filter((f): f is File => f !== null);
    return extractSupportedImageFiles(pastedFiles);
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
    dragDepth += 1;
    isDragActive = true;
  }

  function handleDragOver(event: DragEvent) {
    if (!hasFilePayload(event.dataTransfer)) return;
    if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
    isDragActive = true;
  }

  function handleDragLeave(event: DragEvent) {
    if (!hasFilePayload(event.dataTransfer)) return;
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) isDragActive = false;
  }

  async function handleDrop(event: DragEvent) {
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
          ((!e.shiftKey && e.key === "Enter") || e.key === "Tab")))
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
          ((!e.shiftKey && e.key === "Enter") || e.key === "Tab")))
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
      if (composing || e.shiftKey) return;
      e.preventDefault();
      const isSteer = typeof steer === "function" ? steer() : steer;
      handleSubmit(isSteer);
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
    get lightboxAttachmentIndex() {
      return lightboxAttachmentIndex;
    },
    get lightboxImages() {
      return lightboxImages;
    },
    get lightboxOpen() {
      return lightboxOpen;
    },

    // derived
    get isDisabled() {
      return isDisabled;
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
    openAttachmentLightbox,
    closeAttachmentLightbox,
    showPreviousAttachmentLightboxImage,
    showNextAttachmentLightboxImage,
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
