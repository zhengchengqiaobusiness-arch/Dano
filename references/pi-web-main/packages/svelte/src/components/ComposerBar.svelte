<script lang="ts">
  import type {
    RpcGitRepoState,
    RpcImageContent,
    RpcSlashCommand,
    RpcThinkingLevel,
    RpcWorkspaceEntry,
  } from "@pi-web/bridge/types";
  import Check from "lucide-svelte/icons/check";
  import CornerDownLeft from "lucide-svelte/icons/corner-down-left";
  import ImagePlus from "lucide-svelte/icons/image-plus";
  import Square from "lucide-svelte/icons/square";
  import X from "lucide-svelte/icons/x";
  import type { ConnectionStatus } from "../composables/bridgeStore.svelte";
  import { COMPOSER_ATTACHMENT_ACCEPT, formatAttachmentSize } from "../utils/attachments";
  import type { RpcModelInfo } from "../utils/models";
  import CommandPalette from "./CommandPalette.svelte";
  import GitBranchDropdown from "./GitBranchDropdown.svelte";
  import ImageLightbox from "./ImageLightbox.svelte";
  import ModelDropdown from "./ModelDropdown.svelte";
  import ThinkingLevelDropdown from "./ThinkingLevelDropdown.svelte";
  import WorkspaceMentionPalette from "./WorkspaceMentionPalette.svelte";
  import { createComposerBarState } from "./composerBarState.svelte";

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
    onSubmit = ((_: { message: string; images: RpcImageContent[]; revisionEntryId?: string; steer?: boolean }) => {}) as (payload: { message: string; images: RpcImageContent[]; revisionEntryId?: string; steer?: boolean }) => void,
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
    refreshGitRepoState = (_?: boolean) => Promise.resolve(null as RpcGitRepoState | null),
    switchGitBranch = (_: string) => Promise.resolve(null as RpcGitRepoState | null),
    createGitBranch = (_: string) => Promise.resolve(null as RpcGitRepoState | null),
  } = $props();

  let composerPlaceholder = $derived(
    isDebugMode && isDebugSession
      ? "Use /fixture, /tps, /json, or type synthetic markdown"
      : "Ask anything, or drop an image",
  );

  // ---- DOM refs (must stay in .svelte for bind:this) ----
  let composerRootRef = $state<HTMLDivElement | null>(null);
  let textareaRef = $state<HTMLTextAreaElement | null>(null);
  let fileInputRef = $state<HTMLInputElement | null>(null);
  let commandPaletteRef = $state<CommandPalette | null>(null);
  let mentionPaletteRef = $state<WorkspaceMentionPalette | null>(null);

  // ---- reactive primitives owned by the component (needed for bind:) ----
  let inputText = $state("");
  let cursorOffset = $state(0);

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
    }, isStreaming);
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

  // ---- effects that need DOM refs ----

  $effect(() => {
    // Resize textarea on input change
    void inputText;
    composer.resizeTextarea(textareaRef);
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
    composer.resizeTextarea(textareaRef);
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

    <div
      class="composer-dock"
      class:disabled={composer.isDisabled}
      class:drag-active={composer.isDragActive}
      role="region"
      aria-label="Message composer"
      ondragenter={composer.handleDragEnter}
      ondragover={composer.handleDragOver}
      ondragleave={composer.handleDragLeave}
      ondrop={composer.handleDrop}
    >
      {#if revision}
        <div class="revision-banner">
          <div class="revision-banner-copy">
            <p class="revision-preview">{revision.preview}</p>
          </div>
          <button
            type="button"
            class="revision-cancel-button"
            onclick={handleCancelRevision}
          >
            Cancel
          </button>
        </div>
      {/if}

      <input
        bind:this={fileInputRef}
        class="hidden-file-input"
        type="file"
        multiple
        accept={COMPOSER_ATTACHMENT_ACCEPT}
        onchange={handleFileInputChange}
      />

      {#if composer.attachments.length > 0}
        <div class="attachment-strip">
          {#each composer.attachments as attachment, index (attachment.id)}
            <div class="attachment-chip">
              <button
                type="button"
                class="attachment-chip-open"
                aria-label={`View ${attachment.name}`}
                onclick={() => composer.openAttachmentLightbox(index)}
              >
                <img
                  class="attachment-chip-preview"
                  src={attachment.previewUrl}
                  alt={attachment.name}
                />
                <div class="attachment-chip-body">
                  <span class="attachment-chip-name">{attachment.name}</span>
                  <span class="attachment-chip-meta">
                    {formatAttachmentSize(attachment.size)}
                  </span>
                </div>
              </button>
              <button
                type="button"
                class="attachment-chip-remove"
                aria-label={`Remove ${attachment.name}`}
                onclick={() => composer.removeAttachment(attachment.id)}
              >
                <X class="attachment-chip-remove-icon" aria-hidden="true" size={14} />
              </button>
            </div>
          {/each}
        </div>
      {/if}

      <div class="composer-main-row">
        <button
          type="button"
          class="attach-btn"
          title={composer.hasAttachments ? "Add more images" : "Attach images"}
          onclick={handleFilePickerOpen}
        >
          <ImagePlus class="attach-icon" aria-hidden="true" size={16} />
        </button>
        <textarea
          bind:this={textareaRef}
          bind:value={inputText}
          class="prompt-input"
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
      </div>

      <div class="composer-footer-row">
        <div class="composer-status-cluster">
          {#if !isDebugSession}
            <GitBranchDropdown
              label={gitBranch}
              repoState={gitRepoState}
              loading={gitRepoLoading}
              switching={gitBranchSwitching}
              disabled={gitActionsDisabled}
              refresh={refreshGitRepoState}
              switchBranch={switchGitBranch}
              createBranch={createGitBranch}
            />
          {/if}
          <ModelDropdown
            {models}
            {selectedModel}
            label={composer.currentModelText}
            disabled={composer.isDisabled}
            onSelect={(model: RpcModelInfo) => onSelectModel(model)}
          />
          <ThinkingLevelDropdown
            value={thinkingLevel}
            disabled={composer.isDisabled}
            onSelect={(level: RpcThinkingLevel) => onSelectThinkingLevel(level)}
          />
          <button
            type="button"
            class="toggle-chip"
            class:disabled={composer.isDisabled}
            class:checked={autoCompactionEnabled}
            disabled={composer.isDisabled}
            aria-pressed={autoCompactionEnabled}
            onclick={composer.handleAutoCompactionToggle}
          >
            <span class="toggle-chip-icon" aria-hidden="true">
              {#if autoCompactionEnabled}
                <Check size={11} strokeWidth={2.5} />
              {/if}
            </span>
            <span class="toggle-chip-label">Auto compact</span>
          </button>
        </div>
        <div class="composer-action-cluster">
          {#if composer.attachmentSummary}
            <span class="attachment-summary">{composer.attachmentSummary}</span>
          {/if}
          {#if composer.hasPendingMessages}
            <div
              class="pending-queue-indicator"
              title={`${pendingMessageCount} message${pendingMessageCount > 1 ? "s" : ""} queued`}
            >
              <span class="pending-pulse"></span>
              <span class="pending-label">{pendingMessageCount}</span>
            </div>
          {/if}
          <button
            class="send-btn"
            class:stop={composer.showStopButton}
            disabled={composer.showStopButton ? !composer.canAbort : !composer.canSubmit}
            aria-label={composer.showStopButton ? "Stop response" : "Send message"}
            onclick={handlePrimaryAction}
          >
            {#if composer.showStopButton}
              <Square class="send-icon stop-icon" aria-hidden="true" size={13} />
            {:else}
              <CornerDownLeft class="send-icon" aria-hidden="true" size={15} />
            {/if}
          </button>
        </div>
      </div>
    </div>
  </div>
</div>

<ImageLightbox
  open={composer.lightboxOpen}
  images={composer.lightboxImages}
  index={composer.lightboxAttachmentIndex}
  onClose={composer.closeAttachmentLightbox}
  onPrevious={composer.showPreviousAttachmentLightboxImage}
  onNext={composer.showNextAttachmentLightboxImage}
/>

<style>
  .composer-bar {
    flex-shrink: 0;
    padding: 6px 24px 12px;
    padding-bottom: max(12px, env(safe-area-inset-bottom));
    background: var(--bg);
  }

  .composer-inner-wrap {
    position: relative;
    width: min(960px, 100%);
    margin: 0 auto;
  }

  .composer-dock {
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding: 6px;
    border-radius: 18px;
    border: 1px solid var(--border);
    background: var(--bg);
    transition:
      border-color 0.15s ease,
      background 0.15s ease;
  }

  .revision-banner {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 10px 12px;
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
  }

  .revision-cancel-button {
    flex-shrink: 0;
    height: 28px;
    padding: 0 10px;
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

  .composer-dock:focus-within {
    border-color: var(--border-strong);
    background: var(--bg);
  }

  .composer-dock.drag-active {
    border-color: color-mix(in srgb, var(--accent) 36%, var(--border-strong));
    background: var(--bg);
  }

  .composer-dock.disabled { opacity: 0.74; }

  .hidden-file-input { display: none; }

  .attachment-strip {
    display: flex;
    gap: 8px;
    overflow-x: auto;
    padding: 2px 2px 0;
    scrollbar-width: thin;
  }

  .attachment-chip {
    display: flex;
    align-items: center;
    gap: 10px;
    min-width: 0;
    padding: 8px 10px;
    border: 1px solid color-mix(in srgb, var(--border) 82%, transparent);
    border-radius: 14px;
    background: color-mix(in srgb, var(--panel) 74%, transparent);
  }

  .attachment-chip-open {
    display: flex;
    align-items: center;
    gap: 10px;
    min-width: 0;
    padding: 0;
    border: none;
    background: transparent;
    color: inherit;
    cursor: zoom-in;
    text-align: left;
  }

  .attachment-chip-preview {
    width: 36px;
    height: 36px;
    border-radius: 10px;
    object-fit: cover;
    background: var(--panel);
    border: 1px solid color-mix(in srgb, var(--border) 68%, transparent);
  }

  .attachment-chip-body {
    display: flex;
    flex-direction: column;
    min-width: 0;
  }

  .attachment-chip-name,
  .attachment-chip-meta,
  .attachment-summary { font-family: var(--pi-font-mono); }

  .pending-queue-indicator {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    height: 22px;
    padding: 0 8px;
    border-radius: 999px;
    border: 1px solid color-mix(in srgb, var(--border-strong) 60%, transparent);
    background: color-mix(in srgb, var(--panel-2) 80%, transparent);
    color: var(--text-subtle);
    font-size: 0.68rem;
    user-select: none;
  }

  .pending-pulse {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--warning);
    animation: pending-pulse 1.4s ease-in-out infinite;
  }

  .pending-label {
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    line-height: 1;
  }

  @keyframes pending-pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.5; transform: scale(0.85); }
  }

  .attachment-chip-name {
    max-width: 180px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 0.72rem;
    color: var(--text);
  }

  .attachment-chip-meta,
  .attachment-summary {
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

  .attach-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    width: 24px;
    height: 24px;
    margin-top: 8px;
    border-radius: 10px;
    border: none;
    background: var(--bg);
    color: var(--text-subtle);
    cursor: pointer;
    transition:
      background 0.15s ease,
      border-color 0.15s ease,
      color 0.15s ease,
      opacity 0.15s ease;
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
    background: var(--bg);
  }

  .attachment-chip-open:focus-visible,
  .attachment-chip-remove:focus-visible,
  .attach-btn:focus-visible {
    outline: 2px solid color-mix(in srgb, var(--accent) 54%, white 12%);
    outline-offset: 2px;
  }

  .composer-main-row {
    display: flex;
    align-items: flex-start;
    gap: 6px;
    min-width: 0;
  }

  .prompt-input {
    display: block;
    box-sizing: border-box;
    flex: 1;
    min-width: 0;
    max-height: 160px;
    padding: 9px 0 10px;
    border: none;
    background: transparent;
    color: var(--text);
    font-family: var(--pi-font-sans);
    font-size: 0.94rem;
    font-weight: 400;
    line-height: 1.55;
    outline: none;
    resize: none;
    overflow-y: hidden;
    scrollbar-gutter: stable;
  }

  .prompt-input:disabled,
  .attach-btn:disabled { cursor: not-allowed; }

  .prompt-input::placeholder {
    color: var(--text-subtle);
    line-height: inherit;
  }

  .send-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 25px;
    height: 25px;
    border-radius: 12px;
    border: none;
    background: var(--bg);
    color: var(--text);
    cursor: pointer;
    transition:
      background 0.15s ease,
      border-color 0.15s ease,
      opacity 0.15s ease,
      transform 0.15s ease;
  }

  .send-btn:hover:not(:disabled) {
    background: var(--bg);
    transform: translateY(-1px);
  }

  .send-btn.stop {
    background: var(--bg);
    color: var(--error-text);
  }

  .send-btn.stop:hover:not(:disabled) {
    background: var(--bg);
  }

  .send-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .composer-footer-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding-top: 10px;
    border-top: 1px solid color-mix(in srgb, var(--border) 84%, transparent);
    min-width: 0;
  }

  .composer-status-cluster,
  .composer-action-cluster {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
    flex-wrap: wrap;
  }

  .composer-action-cluster { justify-content: flex-end; }

  .toggle-chip {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    height: 26px;
    padding: 0 10px;
    border-radius: 999px;
    border: none;
    background: var(--bg);
    color: var(--text-subtle);
    cursor: pointer;
    user-select: none;
    font: inherit;
    transition:
      background 0.15s ease,
      border-color 0.15s ease,
      color 0.15s ease,
      transform 0.15s ease;
  }

  .toggle-chip:hover:not(.disabled) {
    background: var(--bg);
    color: var(--text);
  }

  .toggle-chip:focus-visible {
    background: var(--bg);
    color: var(--text);
    outline: none;
  }

  .toggle-chip.disabled { opacity: 0.45; cursor: not-allowed; }

  .toggle-chip-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 14px;
    height: 14px;
    border-radius: 4px;
    border: 1px solid color-mix(in srgb, var(--border-strong) 72%, transparent);
    background: transparent;
    color: var(--bg);
    transition:
      border-color 0.15s ease,
      background 0.15s ease,
      color 0.15s ease;
  }

  .toggle-chip.checked {
    color: var(--text);
  }

  .toggle-chip.checked .toggle-chip-icon {
    border-color: color-mix(in srgb, var(--text) 72%, transparent);
    background: var(--text);
    color: var(--bg);
  }

  .toggle-chip-label {
    font-family: var(--pi-font-sans);
    font-size: 0.66rem;
    white-space: nowrap;
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

    .composer-footer-row {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: center;
      gap: 10px;
    }

    .composer-status-cluster {
      min-width: 0;
      padding-bottom: 2px;
      scrollbar-width: none;
    }

    .composer-status-cluster::-webkit-scrollbar { display: none; }

    .composer-action-cluster {
      flex-shrink: 0;
      justify-content: flex-end;
    }

    .attachment-summary { display: none; }
  }

  @media (max-width: 640px) {
    .composer-bar {
      padding: 8px 12px 10px;
      padding-bottom: max(10px, env(safe-area-inset-bottom));
    }

    .revision-banner { flex-direction: column; }
    .revision-cancel-button { align-self: flex-start; }

    .composer-dock { gap: 8px; padding: 8px 10px; border-radius: 16px; }
    .attachment-chip { min-width: 200px; }

    .composer-main-row { gap: 8px; align-items: flex-end; }

    .attach-btn {
      width: 26px;
      height: 39px;
      margin-top: 0;
      border-radius: 10px;
    }

    .prompt-input { padding: 5px 0 6px; line-height: 1.5; }
    .composer-footer-row { gap: 8px; padding-top: 8px; }

    .send-btn { width: 32px; height: 32px; border-radius: 10px; }
  }
</style>
