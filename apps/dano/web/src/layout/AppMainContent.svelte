<script lang="ts">
  import type {
    RpcGitRepoState,
    RpcImageContent,
    RpcQueuedMessage,
    RpcSessionState,
    RpcSlashCommand,
    RpcThinkingLevel,
    RpcUploadedFileRef,
    RpcWorkspaceEntry,
    RpcWorkspaceFile,
    FieldAssistCommandPayload,
    FieldAssistResult,
  } from "@dano/types/protocol";
  import { onDestroy } from "svelte";
  import ChatTranscript from "../components/ChatTranscript.svelte";
  import CompatWarning from "../components/CompatWarning.svelte";
  import ComposerBar from "../components/ComposerBar.svelte";
  import type { QuestionFocusChange } from "../components/questionFocus";
  import type { ConnectionStatus, TranscriptDelta, TranscriptEntry, TranscriptStream } from "../composables/bridgeStore.svelte";
  import { t } from "../i18n";
  import { isDebugSessionPath } from "../utils/debugSession";
  import type { RpcModelInfo } from "../utils/models";
  import { getRuntimeQuickActions } from "../utils/runtimeConfig";
  import type { PendingTranscriptSessionEvent } from "../utils/transcript";
  import {
    createCenterFocusStage,
    type CenterFocusStage,
  } from "./centerFocusStage";

  let transcriptRef: ChatTranscript | null = $state(null);
  let centerColumn = $state<HTMLElement | null>(null);
  let centerFocusStage: CenterFocusStage | null = null;
  let centerFocusActive = $state(false);

  let {
    compatWarningVisible = false,
    statusEntries = {} as Record<string, string>,
    activeSessionPath = null as string | null,
    transcript = [] as readonly TranscriptEntry[],
    transcriptDeltas = [] as readonly TranscriptDelta[],
    transcriptStreams = [] as readonly TranscriptStream[],
    transcriptHasOlder = false,
    transcriptInitialLoading = false,
    isEmptyConversation = false,
    transcriptPageLoading = false,
    pendingTranscriptConfigEvent = null as PendingTranscriptSessionEvent | null,
    isStreaming = false,
    isPromptPending = false,
    isCompacting = false,
    isDebugMode = false,
    slashCommandsAndMentionsEnabled = false,
    connectionStatus = "disconnected" as ConnectionStatus,
    commands = [] as readonly RpcSlashCommand[],
    workspaceEntries = [] as readonly RpcWorkspaceEntry[],
    workspaceEntriesLoading = false,
    workspaceContextKey = null as string | null,
    ensureWorkspaceEntries = (_?: boolean) => Promise.resolve([] as RpcWorkspaceEntry[]),
    availableModels = [] as readonly RpcModelInfo[],
    currentModel = null as RpcModelInfo | null,
    currentThinkingLevel = null as RpcThinkingLevel | null,
    autoCompactionEnabled = false,
    sessionState = null as RpcSessionState | null,
    gitRepoState = null as RpcGitRepoState | null,
    gitRepoLoading = false,
    gitBranchSwitching = false,
    refreshGitRepoState = (_?: boolean) => Promise.resolve(null as RpcGitRepoState | null),
    switchGitBranch = (_: string) => Promise.resolve(null as RpcGitRepoState | null),
    createGitBranch = (_: string) => Promise.resolve(null as RpcGitRepoState | null),
    prefillText = null as string | null,
    pendingRevision = null as {
      entryId: string;
      text: string;
      preview: string;
      hasImages: boolean;
      images: RpcImageContent[];
    } | null,
    allowRevision = false,
    pendingMessageCount = 0,
    queuedUserMessages = [] as readonly RpcQueuedMessage[],
    editQueuedPayload = null as {
      text: string;
      images: RpcImageContent[];
    } | null,
    onSubmit = (_: {
      message: string;
      images: RpcImageContent[];
      files: RpcUploadedFileRef[];
      revisionEntryId?: string;
      steer?: boolean;
    }) => true,
    onAbort = () => {},
    onLoadOlderTranscript = () => false,
    onSelectModel = (_: RpcModelInfo) => {},
    onSelectThinkingLevel = (_: RpcThinkingLevel) => {},
    onToggleAutoCompaction = (_: boolean) => {},
    onReviseMessage = (_: {
      entryId: string;
      text: string;
      preview: string;
      hasImages: boolean;
      images: RpcImageContent[];
    }) => {},
    onCancelRevision = () => {},
    onCancelQueued = (_: number) => {},
    onEditQueued = (_: number) => {},
    onOpenFileReference = (_: { path: string; lineNumber: number }) => {},
    readWorkspaceFile = (_: string) => Promise.reject(new Error("Workspace file reader unavailable")),
    onFieldAssist = undefined as
      | ((payload: FieldAssistCommandPayload) => Promise<FieldAssistResult>)
      | undefined,
  }: {
    compatWarningVisible?: boolean;
    statusEntries?: Record<string, string>;
    activeSessionPath?: string | null;
    transcript?: readonly TranscriptEntry[];
    transcriptDeltas?: readonly TranscriptDelta[];
    transcriptStreams?: readonly TranscriptStream[];
    transcriptHasOlder?: boolean;
    transcriptInitialLoading?: boolean;
    isEmptyConversation?: boolean;
    transcriptPageLoading?: boolean;
    pendingTranscriptConfigEvent?: PendingTranscriptSessionEvent | null;
    isStreaming?: boolean;
    isPromptPending?: boolean;
    isCompacting?: boolean;
    isDebugMode?: boolean;
    slashCommandsAndMentionsEnabled?: boolean;
    connectionStatus?: ConnectionStatus;
    commands?: readonly RpcSlashCommand[];
    workspaceEntries?: readonly RpcWorkspaceEntry[];
    workspaceEntriesLoading?: boolean;
    workspaceContextKey?: string | null;
    ensureWorkspaceEntries?: (force?: boolean) => Promise<RpcWorkspaceEntry[]>;
    availableModels?: readonly RpcModelInfo[];
    currentModel?: RpcModelInfo | null;
    currentThinkingLevel?: RpcThinkingLevel | null;
    autoCompactionEnabled?: boolean;
    sessionState?: RpcSessionState | null;
    gitRepoState?: RpcGitRepoState | null;
    gitRepoLoading?: boolean;
    gitBranchSwitching?: boolean;
    refreshGitRepoState?: (force?: boolean) => Promise<RpcGitRepoState | null>;
    switchGitBranch?: (branchName: string) => Promise<RpcGitRepoState | null>;
    createGitBranch?: (branchName: string) => Promise<RpcGitRepoState | null>;
    prefillText?: string | null;
    pendingRevision?: {
      entryId: string;
      text: string;
      preview: string;
      hasImages: boolean;
      images: RpcImageContent[];
    } | null;
    allowRevision?: boolean;
    pendingMessageCount?: number;
    queuedUserMessages?: readonly RpcQueuedMessage[];
    editQueuedPayload?: {
      text: string;
      images: RpcImageContent[];
    } | null;
    onSubmit?: (payload: {
      message: string;
      images: RpcImageContent[];
      files: RpcUploadedFileRef[];
      revisionEntryId?: string;
      steer?: boolean;
    }) => boolean | Promise<boolean>;
    onAbort?: () => void;
    onLoadOlderTranscript?: () => boolean | Promise<boolean>;
    onSelectModel?: (model: RpcModelInfo) => void;
    onSelectThinkingLevel?: (level: RpcThinkingLevel) => void;
    onToggleAutoCompaction?: (enabled: boolean) => void;
    onReviseMessage?: (payload: {
      entryId: string;
      text: string;
      preview: string;
      hasImages: boolean;
      images: RpcImageContent[];
    }) => void;
    onCancelRevision?: () => void;
    onCancelQueued?: (index: number) => void;
    onEditQueued?: (index: number) => void;
    onOpenFileReference?: (payload: { path: string; lineNumber: number }) => void;
    readWorkspaceFile?: (path: string) => Promise<RpcWorkspaceFile>;
    onFieldAssist?: (payload: FieldAssistCommandPayload) => Promise<FieldAssistResult>;
  } = $props();

  let isDebugSession = $derived(isDebugSessionPath(activeSessionPath));
  const quickActions = getRuntimeQuickActions();

  export function scrollToTranscriptEntry(entryId: string): boolean {
    return transcriptRef?.scrollToTranscriptEntry(entryId) ?? false;
  }

  function handleQuestionFocusChange(target: QuestionFocusChange): void {
    if (!centerColumn) return;
    centerFocusStage ??= createCenterFocusStage(
      centerColumn,
      active => centerFocusActive = active,
    );
    if (!target.element) {
      centerFocusStage.hide(target.toolCallId);
      return;
    }
    centerFocusStage.show({
      sessionKey: activeSessionPath ?? "",
      toolCallId: target.toolCallId,
      element: target.element,
    });
  }

  $effect(() => {
    const sessionKey = activeSessionPath;
    centerFocusStage?.setSession(sessionKey);
  });

  onDestroy(() => centerFocusStage?.destroy());
</script>

<main
  bind:this={centerColumn}
  class="center-column"
  class:empty-conversation={isEmptyConversation}
  class:center-focus-active={centerFocusActive}
>
  <CompatWarning visible={compatWarningVisible} />

  <ChatTranscript
    bind:this={transcriptRef}
    sessionPath={activeSessionPath}
    messages={transcript}
    {transcriptDeltas}
    {transcriptStreams}
    hasOlder={transcriptHasOlder}
    initialLoading={transcriptInitialLoading}
    pageLoading={transcriptPageLoading}
    {pendingTranscriptConfigEvent}
    {isStreaming}
    {isPromptPending}
    {isCompacting}
    scrollLocked={centerFocusActive}
    showMessageIds={isDebugMode}
    {allowRevision}
    onLoadOlder={onLoadOlderTranscript}
    onRevise={onReviseMessage}
    onOpenFileReference={onOpenFileReference}
    {readWorkspaceFile}
    {onFieldAssist}
    onQuestionFocusChange={handleQuestionFocusChange}
  />

  <div class="center-focus-overlay" aria-hidden="true"></div>

  {#if queuedUserMessages.length > 0}
    <div class="queued-messages-strip">
      {#each queuedUserMessages as queued, qIdx (`${queued.queueType ?? "followUp"}:${queued.timestamp}:${qIdx}`)}
        <div class="queued-message-card">
          <div class="queued-message-body">
            <span class="queued-badge">
              {queued.queueType === "steering"
                ? t("queuedMessages.steering")
                : t("queuedMessages.queued")}
            </span>
            <span class="queued-text">{queued.text}</span>
          </div>
          {#if queued.queueType !== "steering"}
            <div class="queued-message-actions">
              <button
                type="button"
                class="queued-action-btn edit"
                title={t("common.edit")}
                onclick={() => onEditQueued(qIdx)}
              >
                {t("common.edit")}
              </button>
              <button
                type="button"
                class="queued-action-btn cancel"
                title={t("common.cancel")}
                onclick={() => onCancelQueued(qIdx)}
              >
                {t("common.cancel")}
              </button>
            </div>
          {/if}
        </div>
      {/each}
    </div>
  {/if}

  <ComposerBar
    {connectionStatus}
    {isStreaming}
    {isDebugMode}
    {isDebugSession}
    {slashCommandsAndMentionsEnabled}
    {commands}
    {workspaceEntries}
    {workspaceEntriesLoading}
    {workspaceContextKey}
    {ensureWorkspaceEntries}
    models={availableModels}
    selectedModel={currentModel}
    thinkingLevel={currentThinkingLevel}
    {autoCompactionEnabled}
    {prefillText}
    revision={pendingRevision}
    {pendingMessageCount}
    {editQueuedPayload}
    onInteraction={() => transcriptRef?.preserveBottomPosition()}
    {onSubmit}
    onAbort={onAbort}
    onCancelRevision={onCancelRevision}
    onSelectModel={onSelectModel}
    onSelectThinkingLevel={onSelectThinkingLevel}
    onToggleAutoCompaction={onToggleAutoCompaction}
    gitBranch={sessionState?.gitBranch ?? null}
    {gitRepoState}
    {gitRepoLoading}
    {gitBranchSwitching}
    gitActionsDisabled={isDebugSession || connectionStatus !== "connected" || isStreaming || isCompacting}
    {refreshGitRepoState}
    {switchGitBranch}
    {createGitBranch}
    quickActions={isEmptyConversation ? quickActions : []}
  />
</main>

<style>
  .center-column {
    grid-column: 1;
    display: flex;
    flex-direction: column;
    min-width: 0;
    min-height: 0;
    overflow: hidden;
    border-bottom-left-radius: 14px;
    position: relative;
    isolation: isolate;
  }

  .center-focus-overlay {
    position: absolute;
    inset: 0;
    z-index: 20;
    opacity: 0;
    pointer-events: none;
    background: color-mix(in srgb, var(--panel) 58%, transparent);
    backdrop-filter: blur(3px);
    transition: opacity 220ms ease;
  }

  .center-column.center-focus-active .center-focus-overlay {
    opacity: 1;
    pointer-events: auto;
  }

  .center-column.center-focus-active :global(.composer-bar) {
    opacity: 0;
    transform: translateY(calc(100% + 24px));
    pointer-events: none;
  }

  .center-column :global(.composer-bar) {
    transition: opacity 220ms ease, transform 220ms cubic-bezier(0.22, 1, 0.36, 1);
  }

  .center-column :global(.chat-transcript[data-center-focus-locked="true"]) {
    overflow: hidden;
  }

  .center-column :global(.center-focused-card) {
    position: fixed;
    z-index: 22;
    overflow-y: auto;
    overscroll-behavior: contain;
    box-shadow: var(--shadow-overlay, var(--shadow-raised));
  }

  .center-column :global(.center-focus-transition-card) {
    view-transition-name: center-focus-card;
  }

  :global(::view-transition-group(center-focus-card)) {
    animation-duration: 260ms;
    animation-timing-function: cubic-bezier(0.22, 1, 0.36, 1);
  }

  @media (prefers-reduced-motion: reduce) {
    .center-focus-overlay {
      transition: opacity 80ms linear;
    }

    .center-column.center-focus-active :global(.composer-bar) {
      transform: none;
    }

    .center-column :global(.composer-bar) {
      transition: opacity 80ms linear;
    }
  }

  @media (max-width: 900px) {
    .center-column.center-focus-active :global(.composer-bar) {
      transform: translateY(
        calc(100% + max(24px, env(safe-area-inset-bottom, 0px)))
      );
    }

    .center-column :global(.center-focused-card) {
      scroll-padding-bottom: calc(64px + env(safe-area-inset-bottom, 0px));
      scrollbar-gutter: stable;
    }

    .center-column :global(.center-focused-card.center-focus-mobile-card) {
      left: var(--center-focus-left);
      top: var(--center-focus-top);
      width: var(--center-focus-width);
      max-height: var(--center-focus-max-height);
    }

    .center-column :global(.center-focused-card .question-actions) {
      position: sticky;
      bottom: 0;
      z-index: 1;
      margin-top: auto;
      padding-top: 12px;
      padding-bottom: env(safe-area-inset-bottom, 0px);
      background: var(--panel);
    }
  }

  .center-column.empty-conversation {
    display: grid;
    grid-template-rows: minmax(0, 1fr) auto minmax(0, 1fr);
  }

  .empty-conversation :global(.chat-transcript) {
    grid-row: 1;
    align-self: end;
    flex: 0 0 auto;
    margin-bottom: 1.5rem;
    overflow: visible;
    padding: 0;
  }

  .empty-conversation :global(.chat-transcript .empty-state) {
    flex: 0 0 auto;
  }

  .center-column.empty-conversation :global(.composer-bar) {
    grid-row: 2;
    margin-top: 0;
    margin-bottom: 0;
  }

  .center-column.empty-conversation :global(.composer-bar::before) {
    display: none;
  }

  .queued-messages-strip {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 0 24px;
    flex-shrink: 0;
  }

  .queued-message-card {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 10px 14px;
    border-radius: 14px;
    border: 1px dashed color-mix(in srgb, var(--border-strong) 60%, transparent);
    background: color-mix(in srgb, var(--panel-2) 60%, transparent);
    animation: queued-slide-in 0.25s ease;
    width: min(960px, 100%);
    margin: 0 auto;
    box-sizing: border-box;
  }

  @keyframes queued-slide-in {
    from {
      opacity: 0;
      transform: translateY(4px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }

  .queued-message-body {
    display: flex;
    align-items: baseline;
    gap: 10px;
    min-width: 0;
  }

  .queued-badge {
    flex-shrink: 0;
    font-size: 0.6rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-subtle);
    padding: 2px 8px;
    border-radius: 999px;
    border: 1px solid color-mix(in srgb, var(--border) 60%, transparent);
    background: color-mix(in srgb, var(--panel) 60%, transparent);
  }

  .queued-text {
    font-size: 0.86rem;
    line-height: 1.5;
    color: var(--text-muted);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .queued-message-actions {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-shrink: 0;
  }

  .queued-action-btn {
    height: 24px;
    padding: 0 10px;
    border-radius: 999px;
    border: 1px solid color-mix(in srgb, var(--border) 70%, transparent);
    background: color-mix(in srgb, var(--panel) 70%, transparent);
    color: var(--text-subtle);
    font-size: 0.68rem;
    cursor: pointer;
    transition:
      border-color 0.12s ease,
      color 0.12s ease,
      background 0.12s ease;
  }

  .queued-action-btn:hover {
    border-color: var(--border-strong);
    background: var(--panel-2);
    color: var(--text);
  }

  .queued-action-btn.edit:hover {
    border-color: color-mix(in srgb, var(--accent) 60%, var(--border-strong));
    color: var(--accent-hover);
  }

  .queued-action-btn.cancel:hover {
    border-color: color-mix(in srgb, var(--danger) 60%, var(--border-strong));
    color: var(--danger);
  }

  @media (max-width: 900px) {
    .center-column {
      grid-column: 1;
      border-bottom-left-radius: 0;
    }
  }
</style>
