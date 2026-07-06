<script lang="ts">
  import { tick } from "svelte";
  import type {
    BridgeQuickActionConfig,
    RpcGitRepoState,
    RpcImageContent,
    RpcSlashCommand,
    RpcThinkingLevel,
    RpcUploadedFileRef,
    RpcWorkspaceEntry,
  } from "@dano/types/protocol";
  import FileIcon from "lucide-svelte/icons/file";
  import X from "lucide-svelte/icons/x";
  import type { ConnectionStatus } from "../composables/bridgeStore.svelte";
  import { t } from "../i18n";
  import { COMPOSER_ATTACHMENT_ACCEPT, MAX_COMPOSER_ATTACHMENT_BYTES, MAX_COMPOSER_ATTACHMENTS, formatAttachmentSize } from "../utils/attachments";
  import type { ComposerAttachment } from "../utils/attachments";
  import type { RpcModelInfo } from "../utils/models";
  import CommandPalette from "./CommandPalette.svelte";
  import FilePreviewDialog from "./FilePreviewDialog.svelte";
  import WorkspaceMentionPalette from "./WorkspaceMentionPalette.svelte";
  import { shouldComposerBeMultiline } from "./composerLayout";
  import { createComposerBarState } from "./composerBarState.svelte";

  const COMPOSER_LAYOUT_ANIMATION_MS = 180;

  let {
    connectionStatus = "disconnected" as ConnectionStatus,
    isStreaming = false,
    isDebugMode = false,
    isDebugSession = false,
    commands = [] as readonly RpcSlashCommand[],
    workspaceEntries = [] as readonly RpcWorkspaceEntry[],
    workspaceEntriesLoading = false,
    workspaceContextKey = null as string | null,
    ensureWorkspaceEntries = ((_force?: boolean) => Promise.resolve([] as RpcWorkspaceEntry[])) as (force?: boolean) => Promise<RpcWorkspaceEntry[]>,
    models = [] as readonly RpcModelInfo[],
    selectedModel = null as RpcModelInfo | null,
    thinkingLevel = null as RpcThinkingLevel | null,
    autoCompactionEnabled = false,
    prefillText = null as string | null,
    revision = null as { entryId: string; text: string; preview: string; hasImages: boolean; images: RpcImageContent[] } | null,
    pendingMessageCount = 0,
    editQueuedPayload = null as { text: string; images: RpcImageContent[] } | null,
    onInteraction = (() => {}) as () => void,
    onSubmit = ((_: { message: string; images: RpcImageContent[]; files: RpcUploadedFileRef[]; revisionEntryId?: string; steer?: boolean }) => {}) as (payload: { message: string; images: RpcImageContent[]; files: RpcUploadedFileRef[]; revisionEntryId?: string; steer?: boolean }) => void,
    onAbort = (() => {}) as () => void,
    onCancelRevision = (() => {}) as () => void,
    onSelectModel = ((_: RpcModelInfo) => {}) as (model: RpcModelInfo) => void,
    onSelectThinkingLevel = ((_: RpcThinkingLevel) => {}) as (level: RpcThinkingLevel) => void,
    onToggleAutoCompaction = ((_: boolean) => {}) as (enabled: boolean) => void,
    gitBranch = null as string | null,
    gitRepoState = null as RpcGitRepoState | null,
    gitRepoLoading = false,
    gitBranchSwitching = false,
    gitActionsDisabled = false,
    quickActions = [] as readonly BridgeQuickActionConfig[],
    refreshGitRepoState = (_?: boolean) => Promise.resolve(null as RpcGitRepoState | null),
    switchGitBranch = (_: string) => Promise.resolve(null as RpcGitRepoState | null),
    createGitBranch = (_: string) => Promise.resolve(null as RpcGitRepoState | null),
  } = $props();

  let composerPlaceholder = $derived(
    isDebugMode && isDebugSession
      ? "Use /fixture, /tps, /json, or type synthetic markdown"
      : t("composer.placeholder"),
  );

  // ---- DOM refs (must stay in .svelte for bind:this) ----
  let composerRootRef = $state<HTMLDivElement | null>(null);
  let composerDockRef = $state<HTMLDivElement | null>(null);
  let textareaRef = $state<HTMLTextAreaElement | null>(null);
  let fileInputRef = $state<HTMLInputElement | null>(null);
  let commandPaletteRef = $state<CommandPalette | null>(null);
  let mentionPaletteRef = $state<WorkspaceMentionPalette | null>(null);

  // ---- reactive primitives owned by the component (needed for bind:) ----
  let inputText = $state("");
  let cursorOffset = $state(0);
  let isComposerMultiline = $state(false);
  let attachmentPreview = $state<{
    name: string;
    src?: string;
    content?: string;
    loading: boolean;
    error: string;
  } | null>(null);
  let attachmentPreviewRequestId = 0;

  // ---- state module (reads/writes inputText & cursorOffset through $rx) ----
  const composer = createComposerBarState(
    {
      get connectionStatus() { return connectionStatus; },
      get isStreaming() { return isStreaming; },
      get isDebugMode() { return isDebugMode; },
      get commands() { return commands; },
      get workspaceEntries() { return workspaceEntries; },
      get workspaceEntriesLoading() { return workspaceEntriesLoading; },
      get workspaceContextKey() { return workspaceContextKey; },
      get ensureWorkspaceEntries() { return ensureWorkspaceEntries; },
      get models() { return models; },
      get selectedModel() { return selectedModel; },
      get thinkingLevel() { return thinkingLevel; },
      get autoCompactionEnabled() { return autoCompactionEnabled; },
      get prefillText() { return prefillText; },
      get revision() { return revision; },
      get pendingMessageCount() { return pendingMessageCount; },
      get editQueuedPayload() { return editQueuedPayload; },
    },
    {
      get onSubmit() { return onSubmit; },
      get onAbort() { return onAbort; },
      get onCancelRevision() { return onCancelRevision; },
      get onSelectModel() { return onSelectModel; },
      get onSelectThinkingLevel() { return onSelectThinkingLevel; },
      get onToggleAutoCompaction() { return onToggleAutoCompaction; },
    },
    {
      get inputText() { return inputText; },
      set inputText(v: string) { inputText = v; },
      get cursorOffset() { return cursorOffset; },
      set cursorOffset(v: number) { cursorOffset = v; },
    },
  );

  // ---- event handler glue (wires state methods to DOM refs) ----

  function handleFilePickerOpen() {
    if (!composer.canAddAttachments) return;
    composer.handleFilePickerOpen(fileInputRef);
  }

  async function handleFileInputChange(event: Event) {
    await composer.handleFileInputChange(event, fileInputRef);
  }

  function handleInputInteraction() {
    composer.handleInputInteraction(textareaRef);
    onInteraction();
  }

  function handleInputCompositionStart() {
    composer.handleInputCompositionStart();
  }

  function handleInputCompositionEnd() {
    composer.handleInputCompositionEnd(textareaRef);
  }

  async function handleInputPaste(event: ClipboardEvent) {
    await composer.handleInputPaste(event);
  }

  function handleInputKeydown(e: KeyboardEvent) {
    composer.handleInputKeydown(e, {
      textareaEl: textareaRef,
      commandPaletteEl: commandPaletteRef,
      mentionPaletteEl: mentionPaletteRef,
    }, false);
  }

  function handleCommandSelect(commandName: string) {
    composer.handleCommandSelect(commandName, textareaRef);
  }

  function handleMentionSelect(item: Parameters<typeof composer.handleMentionSelect>[0]) {
    composer.handleMentionSelect(item, textareaRef);
  }

  function handleCancelRevision() {
    composer.handleCancelRevision(fileInputRef, textareaRef, composerRootRef);
  }

  function handlePrimaryAction() {
    if (composer.showStopButton) {
      composer.handleAbortAction();
      return;
    }
    composer.handleSubmit(false, fileInputRef, textareaRef);
  }

  function handleQuickAction(prompt: string) {
    if (composer.isDisabled) return;
    inputText = prompt;
    composer.handleSubmit(false, fileInputRef, textareaRef);
  }

  function openAttachmentPreview(attachment: ComposerAttachment) {
    if (attachment.status !== "uploaded") return;
    if (attachment.type === "image" && attachment.previewUrl) {
      attachmentPreview = {
        name: attachment.name,
        src: attachment.previewUrl,
        loading: false,
        error: "",
      };
      return;
    }
    const previewUrl = attachment.file?.previewUrl;
    if (!previewUrl) return;
    const requestId = ++attachmentPreviewRequestId;
    attachmentPreview = { name: attachment.name, loading: true, error: "" };
    fetch(previewUrl)
      .then(async response => {
        if (!response.ok) throw new Error(await response.text());
        return response.text();
      })
      .then(content => {
        if (requestId !== attachmentPreviewRequestId) return;
        attachmentPreview = {
          name: attachment.name,
          content,
          loading: false,
          error: "",
        };
      })
      .catch(error => {
        if (requestId !== attachmentPreviewRequestId) return;
        attachmentPreview = {
          name: attachment.name,
          loading: false,
          error: error instanceof Error ? error.message : t("fileViewer.loadFailed"),
        };
      });
  }

  function closeAttachmentPreview() {
    attachmentPreviewRequestId += 1;
    attachmentPreview = null;
  }

  function captureComposerLayout() {
    if (!composerDockRef) return null;

    const targets = [
      textareaRef,
      composerDockRef.querySelector<HTMLElement>(".composer-actions-left"),
      composerDockRef.querySelector<HTMLElement>(".composer-actions-right"),
    ].filter((element): element is HTMLElement => Boolean(element));

    return new Map(targets.map(element => [element, element.getBoundingClientRect()]));
  }

  function animateComposerLayout(layoutBefore: Map<HTMLElement, DOMRect> | null) {
    if (
      !layoutBefore ||
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      return;
    }

    void tick().then(() => {
      requestAnimationFrame(() => {
        for (const [element, before] of layoutBefore) {
          if (!document.contains(element)) continue;

          const after = element.getBoundingClientRect();
          const deltaX = before.left - after.left;
          const deltaY = before.top - after.top;
          if (Math.abs(deltaX) < 0.5 && Math.abs(deltaY) < 0.5) continue;

          element.animate(
            [
              { transform: `translate(${deltaX}px, ${deltaY}px)` },
              { transform: "translate(0, 0)" },
            ],
            {
              duration: COMPOSER_LAYOUT_ANIMATION_MS,
              easing: "cubic-bezier(0.2, 0.8, 0.2, 1)",
            },
          );
        }
      });
    });
  }

  function updateComposerMultiline() {
    const el = textareaRef;
    if (!el) return;

    const wasMultiline = isComposerMultiline;
    const layoutBefore = captureComposerLayout();
    const computedStyle = getComputedStyle(el);
    const lineHeight = Number.parseFloat(computedStyle.lineHeight);
    const paddingTop = Number.parseFloat(computedStyle.paddingTop) || 0;
    const paddingBottom = Number.parseFloat(computedStyle.paddingBottom) || 0;
    const singleLineHeight =
      (Number.isFinite(lineHeight) ? lineHeight : el.clientHeight) +
      paddingTop +
      paddingBottom;
    const hasText = el.value.length > 0;
    const hasExplicitNewline = el.value.includes("\n");
    const wrapsAtCurrentWidth =
      el.scrollHeight > Math.ceil(singleLineHeight * 1.5);
    const nextIsMultiline = shouldComposerBeMultiline({
      hasText,
      wasMultiline,
      hasExplicitNewline,
      wrapsAtCurrentWidth,
    });
    const switchedToMultilineBySoftWrap =
      !wasMultiline &&
      nextIsMultiline &&
      !hasExplicitNewline &&
      wrapsAtCurrentWidth;
    isComposerMultiline = nextIsMultiline;

    if (nextIsMultiline !== wasMultiline) {
      if (!nextIsMultiline) {
        el.style.height = `${Math.ceil(singleLineHeight)}px`;
        el.style.overflowY = "hidden";
      } else if (switchedToMultilineBySoftWrap) {
        void tick().then(() => composer.resizeTextarea(textareaRef));
      }
      animateComposerLayout(layoutBefore);
    }
  }

  function resizeComposerInput() {
    composer.resizeTextarea(textareaRef);
    queueMicrotask(updateComposerMultiline);
  }

  // ---- effects that need DOM refs ----

  $effect(() => {
    // Resize textarea on input change
    void inputText;
    resizeComposerInput();
  });

  $effect(() => {
    if (typeof prefillText === "string") {
      composer.applyExternalText(prefillText, {
        fileInputEl: fileInputRef,
        textareaEl: textareaRef,
        rootEl: composerRootRef,
      });
    }
  });

  $effect(() => {
    void editQueuedPayload;
    if (editQueuedPayload) {
      composer.focusComposer({
        textareaEl: textareaRef,
        rootEl: composerRootRef,
        reveal: true,
      });
    }
  });

  // Initial resize
  $effect(() => {
    resizeComposerInput();
  });
</script>

<div bind:this={composerRootRef} class="composer-bar">
  <div class="composer-inner-wrap">
    {#if composer.showCommandPalette}
      <CommandPalette
        bind:this={commandPaletteRef}
        commands={composer.availableSlashCommands}
        filter={composer.commandContext?.query ?? ""}
        isDebugMode={isDebugMode}
        onSelect={handleCommandSelect}
        onClose={composer.handleCommandClose}
      />
    {:else if composer.showMentionPalette}
      <WorkspaceMentionPalette
        bind:this={mentionPaletteRef}
        items={composer.mentionSuggestions}
        loading={workspaceEntriesLoading}
        onSelect={handleMentionSelect}
        onClose={composer.handleMentionClose}
      />
    {/if}

    {#if revision}
      <div class="revision-banner">
        <div class="revision-banner-copy">
          <p class="revision-preview">{revision.preview}</p>
        </div>
        <button
          type="button"
          class="revision-cancel-button"
          aria-label={t("composer.revision.cancel")}
          title={t("composer.revision.cancel")}
          onclick={handleCancelRevision}
        >
          <X aria-hidden="true" size={14} />
        </button>
      </div>
    {/if}

    <div
      bind:this={composerDockRef}
      class="composer-dock composer"
      class:multiline={isComposerMultiline}
      class:has-attachments={composer.hasAttachments}
      class:disabled={composer.isDisabled}
      class:drag-active={composer.isDragActive}
      role="region"
      aria-label={t("composer.regionLabel")}
      ondragenter={composer.handleDragEnter}
      ondragover={composer.handleDragOver}
      ondragleave={composer.handleDragLeave}
      ondrop={composer.handleDrop}
    >
      <input
        bind:this={fileInputRef}
        class="hidden-file-input"
        type="file"
        multiple
        accept={COMPOSER_ATTACHMENT_ACCEPT}
        disabled={!composer.canAddAttachments}
        onchange={handleFileInputChange}
      />

      {#if composer.attachments.length > 0}
        <div class="attachment-strip">
          {#each composer.attachments as attachment (attachment.id)}
            <div
              class="attachment-chip"
              class:uploading={attachment.status === "uploading"}
              class:failed={attachment.status === "failed"}
            >
              {#if attachment.type === "image" && attachment.previewUrl}
                <button
                  type="button"
                  class="attachment-chip-open"
                  aria-label={t("composer.attachments.view", { name: attachment.name })}
                  disabled={attachment.status !== "uploaded"}
                  onclick={() => openAttachmentPreview(attachment)}
                >
                  <img
                    class="attachment-chip-preview"
                    src={attachment.previewUrl}
                    alt={attachment.name}
                  />
                  <div class="attachment-chip-body">
                    <span class="attachment-chip-name">{attachment.name}</span>
                    <span class="attachment-chip-meta">
                      {#if attachment.status === "uploading"}
                        {t("composer.attachments.uploading")}
                      {:else if attachment.status === "failed"}
                        {t("composer.attachments.uploadFailed")}
                      {:else}
                        {formatAttachmentSize(attachment.size)}
                      {/if}
                    </span>
                  </div>
                </button>
              {:else}
                <button
                  type="button"
                  class="attachment-chip-open"
                  aria-label={t("composer.attachments.view", { name: attachment.name })}
                  disabled={attachment.status !== "uploaded" || !attachment.file?.previewUrl}
                  onclick={() => openAttachmentPreview(attachment)}
                >
                  <span class="attachment-chip-preview attachment-chip-file-icon" aria-hidden="true">
                    <FileIcon size={18} />
                  </span>
                  <div class="attachment-chip-body">
                    <span class="attachment-chip-name">{attachment.name}</span>
                    <span class="attachment-chip-meta">
                      {#if attachment.status === "uploading"}
                        {t("composer.attachments.uploading")}
                      {:else if attachment.status === "failed"}
                        {t("composer.attachments.uploadFailed")}
                      {:else}
                        {formatAttachmentSize(attachment.size)}
                      {/if}
                    </span>
                  </div>
                </button>
              {/if}
              <button
                type="button"
                class="attachment-chip-remove"
                aria-label={t("composer.attachments.remove", { name: attachment.name })}
                onclick={() => composer.removeAttachment(attachment.id)}
              >
                <X class="attachment-chip-remove-icon" aria-hidden="true" size={14} />
              </button>
            </div>
          {/each}
        </div>
      {/if}

      <textarea
        bind:this={textareaRef}
        bind:value={inputText}
        class="prompt-input composer-input"
        rows="1"
        disabled={composer.isDisabled}
        placeholder={composerPlaceholder}
        onkeydown={handleInputKeydown}
        oninput={handleInputInteraction}
        onkeyup={handleInputInteraction}
        onclick={handleInputInteraction}
        oncompositionstart={handleInputCompositionStart}
        oncompositionend={handleInputCompositionEnd}
        onselect={handleInputInteraction}
        onfocus={handleInputInteraction}
        onpaste={handleInputPaste}
      ></textarea>

      <div class="composer-toolbar">
        <div class="composer-actions-left">
          <button
            type="button"
            class="attach-btn composer-icon-button"
            aria-label={t("composer.attachments.add")}
            title={t("composer.attachments.limitHint", {
              count: MAX_COMPOSER_ATTACHMENTS,
              size: formatAttachmentSize(MAX_COMPOSER_ATTACHMENT_BYTES),
            })}
            disabled={!composer.canAddAttachments}
            onclick={handleFilePickerOpen}
          >
            <span class="plus-icon" aria-hidden="true"></span>
          </button>
        </div>

        <div class="composer-actions-right">
          <button
            class="send-btn composer-send-button"
            class:stop={composer.showStopButton}
            disabled={composer.showStopButton ? !composer.canAbort : !composer.canSubmit}
            aria-label={composer.showStopButton ? t("composer.actions.stopResponse") : t("composer.actions.sendMessage")}
            onclick={handlePrimaryAction}
          >
            {#if composer.showStopButton}
              <span class="stop-square-icon" aria-hidden="true"></span>
            {:else}
              <span class="send-arrow-icon" aria-hidden="true"></span>
            {/if}
          </button>
        </div>
      </div>
    </div>

    {#if quickActions.length > 0}
      <div class="quick-actions" aria-label="常用 OA 功能">
        {#each quickActions as action (action.label)}
          <button
            type="button"
            class="quick-action-button"
            disabled={composer.isDisabled}
            onclick={() => handleQuickAction(action.prompt)}
          >
            {action.label}
          </button>
        {/each}
      </div>
    {/if}
  </div>
</div>

<FilePreviewDialog preview={attachmentPreview} onClose={closeAttachmentPreview} />

<style>
  .composer-bar {
    flex-shrink: 0;
    padding: 6px 24px 36px;
    padding-bottom: max(36px, env(safe-area-inset-bottom));
  }

  .composer-inner-wrap {
    position: relative;
    width: min(960px, 100%);
    margin: 0 auto;
  }

  .quick-actions {
    display: flex;
    justify-content: center;
    flex-wrap: wrap;
    gap: 10px;
    margin-top: 14px;
  }

  .quick-action-button {
    min-height: 40px;
    padding: 8px 20px;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: color-mix(in srgb, var(--panel) 90%, transparent);
    color: var(--text-muted);
    font: inherit;
    cursor: pointer;
    transition:
      border-color 0.15s ease,
      color 0.15s ease,
      background 0.15s ease;
  }

  .quick-action-button:hover:not(:disabled),
  .quick-action-button:focus-visible {
    border-color: var(--border-strong);
    background: var(--panel);
    color: var(--text);
  }

  .quick-action-button:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }

  .quick-action-button:disabled {
    cursor: not-allowed;
    opacity: 0.45;
  }

  .composer-dock {
    --composer-control-size: 36px;
    --composer-input-line-height: var(--composer-control-size);
    --composer-max-visible-lines: 5;
    --composer-single-line-gap: 10px;
    display: flex;
    align-items: center;
    gap: var(--composer-single-line-gap);
    padding: 12px 18px;
    border-radius: 30px;
    border: none;
    background: var(--panel);
    box-shadow:
      rgba(0, 0, 0, 0) 0px 0px 0px 0px,
      rgba(0, 0, 0, 0) 0px 0px 0px 0px,
      rgba(0, 0, 0, 0) 0px 0px 0px 0px,
      rgba(0, 0, 0, 0) 0px 0px 0px 0px,
      rgba(0, 0, 0, 0.04) 0px 0px 0px 1px,
      rgba(0, 0, 0, 0.04) 0px 2px 8px 0px;
    transition:
      border-radius 0.18s cubic-bezier(0.2, 0.8, 0.2, 1),
      background 0.15s ease,
      box-shadow 0.15s ease,
      gap 0.18s cubic-bezier(0.2, 0.8, 0.2, 1),
      padding 0.18s cubic-bezier(0.2, 0.8, 0.2, 1);
  }

  .composer-dock.multiline {
    flex-direction: column;
    align-items: stretch;
    gap: 16px;
    padding: 18px 18px 16px;
  }

  .composer-dock.has-attachments:not(.multiline) {
    flex-wrap: wrap;
    row-gap: 10px;
    border-radius: 24px;
  }

  .composer-dock:has(.attachment-strip):not(.multiline) {
    flex-wrap: wrap;
    row-gap: 10px;
    border-radius: 24px;
  }

  .revision-banner {
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
    margin: 0 0 8px;
    min-height: 48px;
    padding: 10px 46px 10px 12px;
    border-radius: 14px;
    border: 1px solid color-mix(in srgb, var(--border-strong) 82%, transparent);
    background: color-mix(in srgb, var(--panel-2) 88%, transparent);
  }

  .revision-banner-copy { min-width: 0; }

  .revision-preview {
    margin: 0;
    font-size: 0.82rem;
    line-height: 1.45;
    color: var(--text);
    text-align: center;
  }

  .revision-cancel-button {
    position: absolute;
    top: 8px;
    right: 8px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    padding: 0;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: var(--bg);
    color: var(--text-muted);
    font-size: 0.7rem;
    cursor: pointer;
    transition:
      border-color 0.12s ease,
      color 0.12s ease,
      background 0.12s ease;
  }

  .revision-cancel-button:hover {
    border-color: var(--border-strong);
    background: var(--bg);
    color: var(--text);
  }

  .composer-dock.drag-active {
    border-color: color-mix(in srgb, var(--accent) 36%, var(--border-strong));
    background: color-mix(in srgb, var(--surface-active) 64%, var(--panel));
  }

  .composer-dock.disabled { opacity: 0.74; }

  .hidden-file-input { display: none; }

  .attachment-strip {
    flex: 0 0 100%;
    order: -1;
    display: flex;
    gap: 8px;
    width: 100%;
    overflow-x: auto;
    padding: 2px 2px 0;
    scrollbar-width: thin;
  }

  .attachment-chip {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    width: 200px;
    max-width: 100%;
    min-width: 0;
    padding: 8px 10px;
    border: 1px solid color-mix(in srgb, var(--border) 82%, transparent);
    border-radius: 14px;
    background: color-mix(in srgb, var(--panel) 74%, transparent);
  }

  .attachment-chip.uploading {
    opacity: 0.72;
  }

  .attachment-chip.failed {
    border-color: color-mix(in srgb, var(--danger) 52%, var(--border));
  }

  .attachment-chip-open {
    display: flex;
    align-items: center;
    gap: 10px;
    min-width: 0;
    flex: 1 1 auto;
    padding: 0;
    border: none;
    background: transparent;
    color: inherit;
    cursor: zoom-in;
    text-align: left;
  }

  .attachment-chip-open:disabled {
    cursor: default;
  }

  .attachment-chip-preview {
    width: 36px;
    height: 36px;
    border-radius: 10px;
    object-fit: cover;
    background: var(--panel);
    border: 1px solid color-mix(in srgb, var(--border) 68%, transparent);
  }

  .attachment-chip-file-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    color: var(--text-subtle);
  }

  .attachment-chip-body {
    display: flex;
    flex-direction: column;
    min-width: 0;
  }

  .attachment-chip-name,
  .attachment-chip-meta { font-family: var(--pi-font-mono); }

  .attachment-chip-name {
    max-width: 180px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 0.72rem;
    color: var(--text);
  }

  .attachment-chip-meta {
    font-size: 0.64rem;
    color: var(--text-subtle);
  }

  .attachment-chip-remove {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    width: 24px;
    height: 24px;
    border-radius: 999px;
    border: 1px solid color-mix(in srgb, var(--border) 78%, transparent);
    background: transparent;
    color: var(--text-subtle);
    cursor: pointer;
    transition:
      background 0.15s ease,
      border-color 0.15s ease,
      color 0.15s ease;
  }

  .composer-icon-button,
  .composer-send-button {
    border: 0;
    cursor: pointer;
    transition:
      background 0.14s ease,
      color 0.14s ease,
      opacity 0.14s ease,
      transform 0.14s ease;
  }

  .attach-btn,
  .composer-icon-button {
    width: var(--composer-control-size);
    height: var(--composer-control-size);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    border-radius: 999px;
    background: transparent;
    color: var(--text);
  }

  .attachment-chip-open:hover .attachment-chip-name,
  .attachment-chip-open:focus-visible .attachment-chip-name,
  .attachment-chip-remove:hover,
  .attach-btn:hover:not(:disabled) {
    color: var(--text);
  }

  .attachment-chip-open:hover .attachment-chip-preview,
  .attachment-chip-open:focus-visible .attachment-chip-preview,
  .attachment-chip-remove:hover,
  .attach-btn:hover:not(:disabled) {
    background: var(--surface-hover);
  }

  .attachment-chip-open:focus-visible,
  .attachment-chip-remove:focus-visible,
  .attach-btn:focus-visible {
    outline: 2px solid var(--focus-ring);
    outline-offset: 2px;
  }

  .composer-toolbar {
    order: 2;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 14px;
    width: 100%;
  }

  .composer-dock:not(.multiline) .composer-toolbar {
    display: contents;
  }

  .composer-actions-left,
  .composer-actions-right {
    display: flex;
    align-items: center;
    gap: 10px;
    will-change: transform;
  }

  .composer-actions-left {
    order: 0;
  }

  .composer-actions-right {
    order: 2;
    margin-left: auto;
    justify-content: flex-end;
  }

  .prompt-input {
    display: block;
    box-sizing: border-box;
    order: 1;
    flex: 1 1 auto;
    width: auto;
    min-width: 0;
    padding: 0;
    border: none;
    background: transparent;
    color: var(--text);
    font-family: var(--pi-font-sans);
    font-size: 1.04rem;
    font-weight: 400;
    line-height: var(--composer-input-line-height);
    outline: none;
    resize: none;
    overflow-y: hidden;
    scrollbar-gutter: stable;
    transition: padding 0.18s cubic-bezier(0.2, 0.8, 0.2, 1);
  }

  .composer-dock.multiline .prompt-input {
    min-height: var(--composer-input-line-height);
    padding: 0;
  }

  .composer-dock:not(.multiline) .prompt-input {
    padding: 0;
  }

  .prompt-input:disabled,
  .attach-btn:disabled { cursor: not-allowed; }

  .prompt-input::placeholder {
    color: var(--text-subtle);
    color: color-mix(in srgb, var(--text-subtle) 68%, var(--panel));
    line-height: inherit;
    opacity: 1;
  }

  .send-btn,
  .composer-send-button {
    width: var(--composer-control-size);
    height: var(--composer-control-size);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border-radius: 999px;
    border: none;
    background: var(--accent);
    color: var(--bg);
    cursor: pointer;
    transition:
      background 0.15s ease,
      opacity 0.15s ease,
      transform 0.15s ease;
  }

  .send-btn:hover:not(:disabled) {
    background: color-mix(in srgb, var(--accent) 88%, black);
    transform: translateY(-1px);
  }

  .send-btn:active:not(:disabled) {
    transform: translateY(0) scale(0.96);
  }

  .send-btn.stop {
    background: color-mix(in srgb, var(--accent) 72%, black);
    color: var(--bg);
  }

  .send-btn.stop:hover:not(:disabled) {
    background: color-mix(in srgb, var(--accent) 62%, black);
  }

  .send-btn:disabled {
    opacity: 0.45;
    cursor: not-allowed;
  }

  .plus-icon {
    position: relative;
    width: 22px;
    height: 22px;
    display: inline-block;
  }

  .plus-icon::before,
  .plus-icon::after {
    content: "";
    position: absolute;
    inset: 50% auto auto 50%;
    width: 22px;
    height: 2px;
    border-radius: 999px;
    background: currentColor;
    transform: translate(-50%, -50%);
  }

  .plus-icon::after {
    transform: translate(-50%, -50%) rotate(90deg);
  }

  .send-arrow-icon {
    position: relative;
    width: 22px;
    height: 22px;
    display: block;
  }

  .send-arrow-icon::before {
    content: "";
    position: absolute;
    left: 50%;
    top: 4px;
    width: 3px;
    height: 15px;
    border-radius: 999px;
    background: currentColor;
    transform: translateX(-50%);
  }

  .send-arrow-icon::after {
    content: "";
    position: absolute;
    left: 50%;
    top: 4px;
    width: 11px;
    height: 11px;
    border-top: 3px solid currentColor;
    border-left: 3px solid currentColor;
    border-radius: 2px 0 0 0;
    transform: translateX(-50%) rotate(45deg);
    transform-origin: 50% 50%;
  }

  .stop-square-icon {
    width: 13px;
    height: 13px;
    display: block;
    border-radius: 2px;
    background: currentColor;
  }

  @media (max-width: 900px) {
    .composer-bar {
      position: sticky;
      bottom: 0;
      z-index: 10;
      padding: 10px 16px 12px;
      padding-bottom: max(12px, env(safe-area-inset-bottom));
    }

    .composer-inner-wrap { width: 100%; }
    .prompt-input { font-size: 16px; }

    .composer-dock { padding: 10px 14px; border-radius: 24px; }
  }

  @media (max-width: 640px) {
    .composer-bar {
      padding: 8px 12px 10px;
      padding-bottom: max(10px, env(safe-area-inset-bottom));
    }

    .composer-dock { gap: 8px; padding: 10px 14px; border-radius: 24px; }
    .composer-dock.multiline { padding: 14px 14px 12px; }

    .prompt-input { font-size: 16px; }
    .composer-dock.multiline .prompt-input { padding: 0; }
    .composer-dock:not(.multiline) .prompt-input { padding: 0; }
  }
</style>
