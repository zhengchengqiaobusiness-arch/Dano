<script lang="ts">
  import type {
    RpcExtensionUIRequest,
    RpcExtensionUIResponse,
  } from "@pi-web/bridge/types";
  import X from "lucide-svelte/icons/x";

  type DialogExtensionUIRequest = Extract<
    RpcExtensionUIRequest,
    { method: "select" | "confirm" | "input" | "editor" }
  >;

  let {
    request = null as DialogExtensionUIRequest | null,
    onRespond = (_: RpcExtensionUIResponse) => {},
  } = $props();

  let inputValue = $state("");
  let editorValue = $state("");
  let selectedIndex = $state(-1);

  function handleSelect(option: string) {
    if (!request) return;
    onRespond({
      type: "extension_ui_response",
      id: request.id,
      value: option,
    });
  }

  function handleConfirm(confirmed: boolean) {
    if (!request) return;
    onRespond({
      type: "extension_ui_response",
      id: request.id,
      confirmed,
    });
  }

  function handleInputSubmit() {
    if (!request) return;
    onRespond({
      type: "extension_ui_response",
      id: request.id,
      value: inputValue,
    });
    inputValue = "";
  }

  function handleEditorSubmit() {
    if (!request) return;
    onRespond({
      type: "extension_ui_response",
      id: request.id,
      value: editorValue,
    });
    editorValue = "";
  }

  function handleCancel() {
    if (!request) return;
    onRespond({
      type: "extension_ui_response",
      id: request.id,
      cancelled: true,
    });
    inputValue = "";
    editorValue = "";
  }

  function initFromRequest() {
    if (!request) return;
    if (request.method === "input") inputValue = "";
    if (request.method === "editor" && request.prefill) {
      editorValue = request.prefill;
    } else {
      editorValue = "";
    }
    selectedIndex = -1;
  }

  $effect(() => {
    initFromRequest();
  });
</script>

{#if request}
  <div class="dialog-overlay" role="button" tabindex="0" onclick={handleCancel} onkeydown={(e) => (e.key === "Enter" || e.key === " ") && handleCancel()}>
    <div class="dialog-panel" role="dialog" aria-modal="true" aria-label={request.title} tabindex="-1" onclick={(e) => e.stopPropagation()} onkeydown={(e) => e.stopPropagation()}>
      <div class="dialog-header">
        <div>
          <div class="dialog-kicker">Extension request</div>
          <h3 class="dialog-title">{request.title}</h3>
        </div>
        <button class="dialog-close" aria-label="Cancel" onclick={handleCancel}>
          <X aria-hidden="true" size={16} />
        </button>
      </div>

      {#if request.method === "select"}
        <div class="dialog-body">
          <ul class="select-list">
            {#each request.options as option, i}
              <li
                class="select-item"
                class:selected={selectedIndex === i}
              >
                <button
                  class="select-item-btn"
                  type="button"
                  onclick={() => handleSelect(option)}
                  onmouseenter={() => (selectedIndex = i)}
                  onmouseleave={() => (selectedIndex = -1)}
                >
                  {option}
                </button>
              </li>
            {/each}
          </ul>
        </div>
      {:else if request.method === "confirm"}
        <div class="dialog-body">
          <p class="confirm-message">{request.message}</p>
          <div class="dialog-actions">
            <button class="btn btn-cancel" onclick={() => handleConfirm(false)}>
              Cancel
            </button>
            <button class="btn btn-primary" onclick={() => handleConfirm(true)}>
              Confirm
            </button>
          </div>
        </div>
      {:else if request.method === "input"}
        <div class="dialog-body">
          <input
            bind:value={inputValue}
            class="dialog-input"
            placeholder={request.placeholder ?? "Enter a value..."}
            onkeydown={(e) => e.key === "Enter" && handleInputSubmit()}
          />
          <div class="dialog-actions">
            <button class="btn btn-cancel" onclick={handleCancel}>Cancel</button>
            <button class="btn btn-primary" onclick={handleInputSubmit}>
              Submit
            </button>
          </div>
        </div>
      {:else if request.method === "editor"}
        <div class="dialog-body">
          <textarea
            bind:value={editorValue}
            class="dialog-textarea"
            rows="10"
            onkeydown={(e) =>
              (e.ctrlKey || e.metaKey) && e.key === "Enter" && handleEditorSubmit()}
          ></textarea>
          <div class="dialog-hint">
            <kbd class="dialog-kbd">Ctrl+Enter</kbd> to submit
          </div>
          <div class="dialog-actions">
            <button class="btn btn-cancel" onclick={handleCancel}>Cancel</button>
            <button class="btn btn-primary" onclick={handleEditorSubmit}>
              Submit
            </button>
          </div>
        </div>
      {/if}

      {#if request.method === "select"}
        <div class="dialog-actions select-actions">
          <button class="btn btn-cancel" onclick={handleCancel}>Cancel</button>
        </div>
      {/if}
    </div>
  </div>
{/if}

<style>
  .dialog-overlay {
    position: fixed;
    inset: 0;
    z-index: 1000;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--overlay);
    backdrop-filter: blur(6px);
  }

  .dialog-panel {
    width: min(92vw, 520px);
    max-height: 80vh;
    max-height: 80dvh;
    overflow-y: auto;
    background: var(--panel);
    border: 1px solid var(--border-strong);
    border-radius: 16px;
    box-shadow: var(--shadow);
    display: flex;
    flex-direction: column;
  }

  .dialog-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
    padding: 18px 20px 16px;
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }

  .dialog-kicker {
    margin-bottom: 6px;
    font-size: 0.66rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-subtle);
  }

  .dialog-title {
    margin: 0;
    font-size: 1rem;
    font-weight: 600;
    color: var(--text);
  }

  .dialog-close {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    background: none;
    border: none;
    color: var(--text-subtle);
    cursor: pointer;
    line-height: 1;
    padding: 4px;
  }

  .dialog-close:hover {
    color: var(--text);
  }

  .dialog-body {
    padding: 16px 20px;
    flex: 1;
    overflow-y: auto;
  }

  .select-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .select-item {
    padding: 0;
    border-radius: 10px;
    transition:
      background 0.1s ease,
      border-color 0.1s ease;
    border: 1px solid var(--border);
    background: var(--panel-2);
    list-style: none;
  }

  .select-item-btn {
    display: block;
    width: 100%;
    padding: 12px 14px;
    border: none;
    border-radius: 10px;
    background: transparent;
    color: var(--text);
    font-size: 0.9rem;
    cursor: pointer;
    text-align: left;
    font: inherit;
  }

  .select-item:hover,
  .select-item.selected {
    background: var(--panel-3);
    border-color: var(--border-strong);
  }

  .confirm-message {
    margin: 0 0 16px;
    color: var(--text-muted);
    font-size: 0.9rem;
    line-height: 1.6;
  }

  .dialog-input,
  .dialog-textarea {
    width: 100%;
    padding: 12px 14px;
    border-radius: 12px;
    border: 1px solid var(--border);
    background: var(--bg-elevated);
    color: var(--text);
    font-size: 0.92rem;
    outline: none;
    box-sizing: border-box;
  }

  .dialog-input:focus,
  .dialog-textarea:focus {
    border-color: var(--border-strong);
  }

  .dialog-input::placeholder {
    color: var(--text-subtle);
  }

  .dialog-textarea {
    font-family: var(--pi-font-mono);
    resize: vertical;
    margin-bottom: 6px;
  }

  .dialog-hint {
    margin-bottom: 14px;
    font-family: var(--pi-font-sans);
    font-size: 0.68rem;
    color: var(--text-subtle);
  }

  .dialog-kbd {
    display: inline-flex;
    align-items: center;
    padding: 0 0.36em;
    border: 1px solid color-mix(in srgb, var(--border) 86%, transparent);
    border-radius: 999px;
    background: color-mix(in srgb, var(--panel-2) 78%, transparent);
    font-family: var(--pi-font-mono);
    font-size: 0.95em;
    line-height: 1.5;
  }

  .dialog-actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    padding: 0 20px 18px;
  }

  .select-actions {
    padding-top: 0;
  }

  .btn {
    height: 38px;
    padding: 0 16px;
    border-radius: 10px;
    border: 1px solid var(--border);
    font-size: 0.84rem;
    font-weight: 600;
    cursor: pointer;
    transition:
      background 0.15s ease,
      color 0.15s ease,
      border-color 0.15s ease;
  }

  .btn-primary {
    background: var(--button-bg);
    color: var(--text);
  }

  .btn-primary:hover {
    background: var(--button-hover);
    border-color: var(--border-strong);
  }

  .btn-cancel {
    background: transparent;
    color: var(--text-muted);
  }

  .btn-cancel:hover {
    background: var(--panel-2);
    color: var(--text);
  }

  @media (max-width: 900px) {
    .dialog-panel {
      width: min(95vw, 520px);
      max-height: 90vh;
      max-height: 90dvh;
    }

    .select-item,
    .btn {
      min-height: 44px;
    }

    .dialog-input,
    .dialog-textarea {
      font-size: 16px;
    }
  }
</style>
