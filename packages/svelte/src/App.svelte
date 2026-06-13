<script lang="ts">
  import { onMount, tick } from "svelte";
  import { cubicOut } from "svelte/easing";
  import { fly } from "svelte/transition";
  import MarkdownMessage from "./components/MarkdownMessage.svelte";
  import ToolBlock from "./components/ToolBlock.svelte";
  import {
    applyServerEvent,
    canSend,
    createClientMessageId,
    createInitialChatState,
    type ChatContentBlock,
    type ChatMessage,
    type ChatState,
  } from "./composables/bridgeStore.svelte";

  const EVENT_NAMES = [
    "conversation.ready",
    "message.accepted",
    "assistant.started",
    "assistant.delta",
    "assistant.blocks",
    "assistant.completed",
    "message.failed",
    "heartbeat",
  ];
  const BOTTOM_LOCK_THRESHOLD_PX = 96;
  const SCROLL_BOTTOM_ANIMATION_MS = 420;

  let chat = $state<ChatState>(createInitialChatState());
  let draft = $state("");
  let transcriptElement = $state<HTMLDivElement>();
  let composerInputElement = $state<HTMLTextAreaElement>();
  let isComposerMultiline = $state(false);
  let isPinnedToBottom = $state(true);
  let showScrollToBottom = $state(false);
  let isScrollBottomAnimating = $state(false);
  let eventSource: EventSource | null = null;
  let scrollFrame: number | null = null;
  let scrollBottomAnimationTimeout: ReturnType<typeof setTimeout> | null = null;

  const transcriptScrollKey = $derived(
    chat.messages
      .map(message => messageScrollKey(message))
      .join("|"),
  );

  onMount(() => {
    void startConversation();
    return () => {
      eventSource?.close();
      if (scrollFrame !== null) {
        cancelAnimationFrame(scrollFrame);
      }
      if (scrollBottomAnimationTimeout !== null) {
        clearTimeout(scrollBottomAnimationTimeout);
      }
    };
  });

  $effect(() => {
    transcriptScrollKey;
    if (isPinnedToBottom) {
      void scrollTranscriptToBottom("auto");
    }
  });

  $effect(() => {
    draft;
    void tick().then(resizeComposerInput);
  });

  async function startConversation() {
    chat = { ...chat, connectionStatus: "connecting", lastError: "" };

    try {
      const response = await fetch("/api/conversations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });

      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }

      const body = (await response.json()) as {
        conversationId: string;
        eventsUrl: string;
      };
      chat = {
        ...chat,
        conversationId: body.conversationId,
        eventsUrl: body.eventsUrl,
      };
      openEventStream(body.eventsUrl);
    } catch (error) {
      chat = {
        ...chat,
        connectionStatus: "disconnected",
        lastError: error instanceof Error ? error.message : String(error),
      };
    }
  }

  function openEventStream(eventsUrl: string) {
    eventSource?.close();
    eventSource = new EventSource(eventsUrl);

    for (const eventName of EVENT_NAMES) {
      eventSource.addEventListener(eventName, event => {
        const data = JSON.parse((event as MessageEvent).data || "{}") as Record<
          string,
          unknown
        >;
        chat = applyServerEvent(chat, eventName, data);
      });
    }

    eventSource.onerror = () => {
      chat = {
        ...chat,
        connectionStatus: "disconnected",
        lastError: "Event stream interrupted.",
      };
    };
  }

  async function sendMessage() {
    const text = draft.trim();
    if (!text) {
      chat = { ...chat, inputError: "Enter a message before sending." };
      return;
    }
    if (!chat.conversationId || chat.sending) {
      return;
    }

    chat = { ...chat, sending: true, inputError: "", lastError: "" };
    draft = "";
    void scrollTranscriptToBottom("smooth");

    try {
      const response = await fetch(
        `/api/conversations/${chat.conversationId}/messages`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            clientMessageId: createClientMessageId(),
            text,
          }),
        },
      );

      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }
    } catch (error) {
      chat = {
        ...chat,
        sending: false,
        lastError: error instanceof Error ? error.message : String(error),
      };
    }
  }

  async function retryMessage(message: ChatMessage) {
    if (!chat.conversationId || chat.sending) {
      return;
    }

    chat = { ...chat, sending: true, lastError: "" };
    void scrollTranscriptToBottom("smooth");
    try {
      const response = await fetch(
        `/api/conversations/${chat.conversationId}/messages/${message.id}/retry`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        },
      );

      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }
    } catch (error) {
      chat = {
        ...chat,
        sending: false,
        lastError: error instanceof Error ? error.message : String(error),
      };
    }
  }

  function submitComposer(event: SubmitEvent) {
    event.preventDefault();
    void sendMessage();
  }

  function handleComposerKeydown(
    event: KeyboardEvent & { currentTarget: HTMLTextAreaElement },
  ) {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing) {
      return;
    }

    event.preventDefault();
    event.currentTarget.form?.requestSubmit();
  }

  function resizeComposerInput() {
    if (!composerInputElement) {
      return;
    }

    const maxHeight = 180;
    composerInputElement.style.height = "auto";
    const measuredHeight = composerInputElement.scrollHeight;
    const computedStyle = getComputedStyle(composerInputElement);
    const lineHeight = Number.parseFloat(computedStyle.lineHeight);
    const paddingTop = Number.parseFloat(computedStyle.paddingTop) || 0;
    const paddingBottom = Number.parseFloat(computedStyle.paddingBottom) || 0;
    const singleLineHeight =
      (Number.isFinite(lineHeight) ? lineHeight : composerInputElement.clientHeight) +
      paddingTop +
      paddingBottom;
    isComposerMultiline = measuredHeight > Math.ceil(singleLineHeight) + 2;
    const nextHeight = Math.min(measuredHeight, maxHeight);
    composerInputElement.style.height = `${nextHeight}px`;
    composerInputElement.style.overflowY =
      composerInputElement.scrollHeight > maxHeight ? "auto" : "hidden";
  }

  function messageScrollKey(message: ChatMessage) {
    const blocksKey = message.contentBlocks
      ?.map(block =>
        block.kind === "text"
          ? `text:${block.text.length}`
          : [
              "tool",
              block.toolCallId ?? "",
              block.toolName,
              block.toolStatus,
              block.resultText?.length ?? 0,
              block.argumentsText.length,
            ].join(":"),
      )
      .join("|");

    return [
      message.id,
      message.content.length,
      message.status,
      blocksKey ?? "",
    ].join(":");
  }

  function trailingContentForMessage(message: ChatMessage): string {
    if (!message.contentBlocks?.length || !message.content) {
      return "";
    }

    return contentTextFromBlocks(message.contentBlocks) === message.content
      ? ""
      : message.content;
  }

  function contentTextFromBlocks(blocks: ChatContentBlock[]): string {
    return blocks
      .flatMap(block => (block.kind === "text" ? [block.text] : []))
      .join("");
  }

  function handleTranscriptScroll() {
    const isPinned = isTranscriptNearBottom();
    isPinnedToBottom = isPinned;
    if (isScrollBottomAnimating && isPinned) {
      showScrollToBottom = true;
      return;
    }

    showScrollToBottom = !isPinned && chat.messages.length > 0;
  }

  async function handleScrollBottomClick() {
    isScrollBottomAnimating = true;
    await scrollTranscriptToBottom("smooth", false);
    scheduleScrollBottomAnimationEnd();
  }

  function handleMessageRendered() {
    if (isPinnedToBottom) {
      void scrollTranscriptToBottom("auto");
    }
  }

  function isTranscriptNearBottom() {
    if (!transcriptElement) {
      return true;
    }

    return getDistanceFromBottom(transcriptElement) <= BOTTOM_LOCK_THRESHOLD_PX;
  }

  function getDistanceFromBottom(element: HTMLDivElement) {
    return element.scrollHeight - element.scrollTop - element.clientHeight;
  }

  async function scrollTranscriptToBottom(
    behavior: ScrollBehavior,
    hideButton = true,
  ) {
    isPinnedToBottom = true;
    if (hideButton) {
      showScrollToBottom = false;
      isScrollBottomAnimating = false;
    }
    await tick();

    if (scrollFrame !== null) {
      cancelAnimationFrame(scrollFrame);
    }

    scrollFrame = requestAnimationFrame(() => {
      scrollFrame = null;
      transcriptElement?.scrollTo({
        top: transcriptElement.scrollHeight,
        behavior,
      });
      if (hideButton) {
        showScrollToBottom = false;
      }
    });
  }

  function scheduleScrollBottomAnimationEnd(startedAt = performance.now()) {
    if (scrollBottomAnimationTimeout !== null) {
      clearTimeout(scrollBottomAnimationTimeout);
    }

    scrollBottomAnimationTimeout = setTimeout(() => {
      scrollBottomAnimationTimeout = null;
      if (isTranscriptNearBottom() || performance.now() - startedAt > 1200) {
        showScrollToBottom = false;
        isScrollBottomAnimating = false;
        return;
      }

      scheduleScrollBottomAnimationEnd(startedAt);
    }, SCROLL_BOTTOM_ANIMATION_MS);
  }

  async function readErrorMessage(response: Response): Promise<string> {
    try {
      const data = (await response.json()) as { errorMessage?: string };
      return data.errorMessage ?? `Request failed with ${response.status}`;
    } catch {
      return `Request failed with ${response.status}`;
    }
  }
</script>

<svelte:options runes={true} />

<svelte:head>
  <title>Dano</title>
</svelte:head>

<main class="shell">
  <section class="chat-surface" aria-label="Dano chat">
    <header class="topbar">
      <div class="brand">
        <img src="/dano-assistant.svg" alt="Dano Assistant" />
        <div>
          <h1>Dano</h1>
          <p>{chat.connectionStatus}</p>
        </div>
      </div>
      <div class:online={chat.connectionStatus === "connected"} class="status-dot"></div>
    </header>

    <div class="transcript-shell">
      <div
        bind:this={transcriptElement}
        class="transcript"
        aria-live="polite"
        onscroll={handleTranscriptScroll}
      >
        {#if chat.messages.length === 0}
          <div class="empty-state">
            <img src="/dano-assistant.svg" alt="" />
            <p>Start a conversation with the server-side assistant.</p>
          </div>
        {:else}
          {#each chat.messages as message (message.id)}
            <article class:assistant={message.role === "assistant"} class="message">
              {#if message.status !== "completed"}
                <div class="message-meta">
                  <span>{message.status}</span>
                </div>
              {/if}
              {#if message.contentBlocks?.length}
                {#each message.contentBlocks as block, blockIndex (`${message.id}-${blockIndex}`)}
                  {#if block.kind === "text"}
                    {#if block.text}
                      <MarkdownMessage
                        content={block.text}
                        status={message.status}
                        onrendered={handleMessageRendered}
                      />
                    {/if}
                  {:else}
                    <ToolBlock block={block} onrendered={handleMessageRendered} />
                  {/if}
                {/each}
                {#if trailingContentForMessage(message)}
                  <MarkdownMessage
                    content={trailingContentForMessage(message)}
                    status={message.status}
                    onrendered={handleMessageRendered}
                  />
                {/if}
              {:else}
                <MarkdownMessage
                  content={message.content ||
                    (message.status === "streaming" ? "Thinking..." : "")}
                  status={message.status}
                  onrendered={handleMessageRendered}
                />
              {/if}
              {#if message.status === "failed"}
                <div class="failure-row">
                  <span>{message.errorMessage}</span>
                  {#if message.retryable}
                    <button type="button" onclick={() => void retryMessage(message)}>
                      Retry
                    </button>
                  {/if}
                </div>
              {/if}
            </article>
          {/each}
        {/if}
      </div>
      {#if showScrollToBottom}
        <button
          type="button"
          class="scroll-bottom"
          class:scrolling={isScrollBottomAnimating}
          aria-label="Scroll to latest message"
          transition:fly={{ y: 10, duration: 160, easing: cubicOut }}
          onclick={handleScrollBottomClick}
        >
          <span aria-hidden="true"></span>
        </button>
      {/if}
    </div>

    {#if chat.lastError}
      <div class="error-banner" role="alert">{chat.lastError}</div>
    {/if}

    <form
      class="composer"
      class:multiline={isComposerMultiline}
      class:invalid={Boolean(chat.inputError)}
      onsubmit={submitComposer}
      aria-label="Send chat message"
    >
      <label class="sr-only" for="chat-composer-input">Message Dano</label>
      <textarea
        id="chat-composer-input"
        class="composer-input"
        bind:this={composerInputElement}
        bind:value={draft}
        placeholder="Message Dano"
        rows="1"
        aria-invalid={Boolean(chat.inputError)}
        onkeydown={handleComposerKeydown}
        oninput={resizeComposerInput}
      ></textarea>
      <div class="composer-toolbar">
        <div class="composer-actions-left">
          <button
            type="button"
            class="composer-icon-button"
            aria-label="Add attachment"
            title="Attachment upload is not wired yet"
          >
            <span class="plus-icon" aria-hidden="true"></span>
          </button>
        </div>
        <div class="composer-actions-right">
          <button
            type="submit"
            class="composer-send-button"
            disabled={!canSend(draft, chat)}
            aria-label={chat.sending ? "Sending message" : "Send message"}
          >
            <span class="send-arrow-icon" aria-hidden="true"></span>
          </button>
        </div>
      </div>
    </form>
    {#if chat.inputError}
      <p class="input-error">{chat.inputError}</p>
    {/if}
  </section>
</main>

<style>
  :global(*) {
    box-sizing: border-box;
  }

  :global(body) {
    margin: 0;
    font-family:
      Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
      "Segoe UI", sans-serif;
    background: #f6f7f9;
    color: #172033;
  }

  button,
  textarea {
    font: inherit;
  }

  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }

  .shell {
    min-height: 100vh;
    display: grid;
    place-items: stretch;
    padding: 24px;
  }

  .chat-surface {
    width: min(980px, 100%);
    height: calc(100vh - 48px);
    min-height: 620px;
    margin: 0 auto;
    display: grid;
    grid-template-rows: auto 1fr auto auto;
    background: #ffffff;
    border: 1px solid #dce2ea;
    border-radius: 8px;
    box-shadow: 0 18px 48px rgba(23, 32, 51, 0.12);
    overflow: hidden;
  }

  .topbar {
    min-height: 72px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 18px;
    border-bottom: 1px solid #e4e8ef;
    background: #fbfcfe;
  }

  .brand {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .brand img {
    width: 44px;
    height: 44px;
  }

  h1,
  p {
    margin: 0;
  }

  h1 {
    font-size: 19px;
    line-height: 1.2;
    font-weight: 720;
  }

  .brand p,
  .message-meta,
  .input-error {
    font-size: 13px;
    color: #667085;
  }

  .status-dot {
    width: 11px;
    height: 11px;
    border-radius: 999px;
    background: #c04d4d;
  }

  .status-dot.online {
    background: #0d9488;
  }

  .transcript-shell {
    position: relative;
    min-height: 0;
    overflow: hidden;
  }

  .transcript {
    height: 100%;
    overflow-y: auto;
    padding: 22px;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }

  .scroll-bottom {
    position: absolute;
    right: 22px;
    bottom: 18px;
    width: 40px;
    height: 40px;
    display: grid;
    place-items: center;
    border: 1px solid #d8dee8;
    border-radius: 999px;
    background: #ffffff;
    color: #1d4ed8;
    box-shadow: 0 10px 24px rgba(23, 32, 51, 0.18);
    cursor: pointer;
    transform: translateY(0) scale(1);
    transition:
      transform 150ms ease,
      box-shadow 150ms ease,
      border-color 150ms ease,
      background 150ms ease;
    will-change: transform, opacity;
  }

  .scroll-bottom:hover {
    border-color: #b8c4d6;
    background: #f8fbff;
    box-shadow: 0 14px 28px rgba(23, 32, 51, 0.2);
    transform: translateY(-2px) scale(1);
  }

  .scroll-bottom:active,
  .scroll-bottom.scrolling {
    transform: translateY(1px) scale(0.96);
  }

  .scroll-bottom span {
    width: 11px;
    height: 11px;
    border-right: 2px solid currentColor;
    border-bottom: 2px solid currentColor;
    transform: rotate(45deg) translate(-1px, -2px);
  }

  .scroll-bottom.scrolling span {
    animation: scroll-bottom-arrow 420ms cubic-bezier(0.2, 0.8, 0.2, 1);
  }

  @keyframes scroll-bottom-arrow {
    0% {
      transform: rotate(45deg) translate(-1px, -2px);
    }

    42% {
      transform: rotate(45deg) translate(3px, 2px);
    }

    100% {
      transform: rotate(45deg) translate(-1px, -2px);
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .scroll-bottom {
      transition: none;
    }

    .scroll-bottom.scrolling span {
      animation: none;
    }
  }

  .empty-state {
    height: 100%;
    display: grid;
    place-items: center;
    align-content: center;
    gap: 14px;
    color: #667085;
    text-align: center;
  }

  .empty-state img {
    width: 68px;
    height: 68px;
  }

  .message {
    width: min(72%, 680px);
    align-self: flex-end;
    padding: 12px 14px;
    border: 1px solid #d8dee8;
    border-radius: 8px;
    background: #edf5ff;
  }

  .message.assistant {
    align-self: flex-start;
    background: #f7f8fa;
  }

  .message :global(.markdown-body + .tool-inline-block),
  .message :global(.tool-inline-block + .markdown-body),
  .message :global(.tool-inline-block + .tool-inline-block) {
    margin-top: 12px;
  }

  .message-meta {
    display: flex;
    justify-content: space-between;
    gap: 14px;
    margin-bottom: 8px;
  }

  .failure-row {
    margin-top: 10px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    color: #9f1239;
    font-size: 14px;
  }

  .failure-row button {
    border: 0;
    border-radius: 7px;
    background: #1d4ed8;
    color: white;
    cursor: pointer;
  }

  .failure-row button {
    min-height: 32px;
    padding: 0 12px;
    flex: 0 0 auto;
  }

  .error-banner {
    margin: 0 18px 12px;
    padding: 10px 12px;
    border: 1px solid #fecdd3;
    border-radius: 7px;
    background: #fff1f2;
    color: #9f1239;
  }

  .composer {
    display: grid;
    grid-template-columns: 40px minmax(0, 1fr) 44px;
    grid-template-areas: "left input right";
    align-items: center;
    gap: 10px;
    margin: 16px 18px 18px;
    padding: 12px 18px;
    border: 1px solid #d6d9df;
    border-radius: 30px;
    background: #ffffff;
    box-shadow:
      0 20px 52px rgba(15, 23, 42, 0.12),
      0 1px 2px rgba(15, 23, 42, 0.08);
    transition:
      border-color 140ms ease,
      box-shadow 140ms ease;
  }

  .composer.multiline {
    grid-template-columns: 1fr;
    grid-template-areas:
      "input"
      "toolbar";
    align-items: stretch;
    gap: 18px;
    padding: 18px 18px 16px;
  }

  .composer:focus-within {
    border-color: #c9cdd4;
    box-shadow:
      0 22px 58px rgba(15, 23, 42, 0.14),
      0 0 0 3px rgba(148, 163, 184, 0.16);
  }

  .composer.invalid {
    border-color: #fda4af;
    box-shadow:
      0 20px 52px rgba(15, 23, 42, 0.1),
      0 0 0 3px rgba(253, 164, 175, 0.22);
  }

  .composer-input {
    grid-area: input;
    width: 100%;
    min-height: 1.55em;
    max-height: 180px;
    resize: none;
    border: 0;
    padding: 0 22px;
    background: transparent;
    color: #111827;
    font-size: 18px;
    line-height: 1.55;
    outline: none;
  }

  .composer:not(.multiline) .composer-input {
    padding: 0 4px;
  }

  .composer-input::placeholder {
    color: #8a9099;
  }

  .composer-toolbar {
    grid-area: toolbar;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 14px;
  }

  .composer:not(.multiline) .composer-toolbar {
    display: contents;
  }

  .composer-actions-left,
  .composer-actions-right {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .composer-actions-left {
    grid-area: left;
  }

  .composer-actions-right {
    grid-area: right;
    justify-content: flex-end;
  }

  .composer-icon-button,
  .composer-send-button {
    border: 0;
    cursor: pointer;
    transition:
      background 140ms ease,
      color 140ms ease,
      opacity 140ms ease,
      transform 140ms ease;
  }

  .composer-icon-button {
    width: 40px;
    height: 40px;
    display: grid;
    place-items: center;
    border-radius: 999px;
    background: transparent;
    color: #111827;
  }

  .composer-icon-button:hover {
    background: #f2f3f5;
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

  .composer-send-button {
    width: 44px;
    height: 44px;
    display: grid;
    place-items: center;
    border-radius: 999px;
    background: #8b5cf6;
    color: #ffffff;
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

  .composer-send-button:not(:disabled):hover {
    background: #7c3aed;
    transform: translateY(-1px);
  }

  .composer-send-button:not(:disabled):active {
    transform: translateY(0) scale(0.96);
  }

  .composer-send-button:disabled {
    cursor: not-allowed;
    background: #c7cbd1;
    color: #ffffff;
    opacity: 0.75;
  }

  .input-error {
    padding: 0 18px 14px;
    color: #9f1239;
  }

  @media (max-width: 720px) {
    .shell {
      padding: 0;
    }

    .chat-surface {
      height: 100vh;
      min-height: 100vh;
      border: 0;
      border-radius: 0;
    }

    .message {
      width: 90%;
    }

    .composer {
      margin: 12px;
      padding: 10px 14px;
      border-radius: 24px;
    }

    .composer.multiline {
      padding: 14px 14px 12px;
    }

    .composer-input {
      min-height: 1.55em;
      padding: 0 8px;
      font-size: 16px;
    }

    .composer:not(.multiline) .composer-input {
      padding: 0 4px;
    }

    .composer-actions-right {
      gap: 6px;
    }

  }
</style>
