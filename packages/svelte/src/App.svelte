<script lang="ts">
  import { onMount } from "svelte";
  import {
    applyServerEvent,
    canSend,
    createClientMessageId,
    createInitialChatState,
    type ChatMessage,
    type ChatState,
  } from "./composables/bridgeStore.svelte";

  const EVENT_NAMES = [
    "conversation.ready",
    "message.accepted",
    "assistant.started",
    "assistant.delta",
    "assistant.completed",
    "message.failed",
    "heartbeat",
  ];

  let chat = $state<ChatState>(createInitialChatState());
  let draft = $state("");
  let eventSource: EventSource | null = null;

  onMount(() => {
    void startConversation();
    return () => {
      eventSource?.close();
    };
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

    <div class="transcript" aria-live="polite">
      {#if chat.messages.length === 0}
        <div class="empty-state">
          <img src="/dano-assistant.svg" alt="" />
          <p>Start a conversation with the server-side assistant.</p>
        </div>
      {:else}
        {#each chat.messages as message (message.id)}
          <article class:assistant={message.role === "assistant"} class="message">
            <div class="message-meta">
              <span>{message.role === "user" ? "You" : "Dano"}</span>
              <span>{message.status}</span>
            </div>
            <p>{message.content || (message.status === "streaming" ? "Thinking..." : "")}</p>
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

    {#if chat.lastError}
      <div class="error-banner" role="alert">{chat.lastError}</div>
    {/if}

    <form
      class="composer"
      onsubmit={submitComposer}
      aria-label="Send chat message"
    >
      <textarea
        bind:value={draft}
        placeholder="Message Dano"
        rows="3"
        aria-invalid={Boolean(chat.inputError)}
        onkeydown={handleComposerKeydown}
      ></textarea>
      <button type="submit" disabled={!canSend(draft, chat)}>
        {chat.sending ? "Sending" : "Send"}
      </button>
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

  .transcript {
    overflow-y: auto;
    padding: 22px;
    display: flex;
    flex-direction: column;
    gap: 14px;
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

  .message-meta {
    display: flex;
    justify-content: space-between;
    gap: 14px;
    margin-bottom: 8px;
  }

  .message p {
    white-space: pre-wrap;
    line-height: 1.55;
    overflow-wrap: anywhere;
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

  .failure-row button,
  .composer button {
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
    grid-template-columns: 1fr auto;
    gap: 12px;
    padding: 16px 18px 18px;
    border-top: 1px solid #e4e8ef;
    background: #fbfcfe;
  }

  textarea {
    width: 100%;
    min-height: 72px;
    max-height: 180px;
    resize: vertical;
    border: 1px solid #cfd6e2;
    border-radius: 7px;
    padding: 10px 12px;
    color: #172033;
  }

  textarea:focus {
    outline: 2px solid #93c5fd;
    outline-offset: 1px;
  }

  .composer button {
    width: 96px;
    min-height: 44px;
    align-self: end;
  }

  .composer button:disabled {
    cursor: not-allowed;
    background: #98a2b3;
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
      grid-template-columns: 1fr;
    }

    .composer button {
      width: 100%;
    }
  }
</style>
