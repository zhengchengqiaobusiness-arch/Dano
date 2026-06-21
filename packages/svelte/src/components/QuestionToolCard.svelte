<script lang="ts">
  import type { RpcResponse } from "@dano/bridge/types";
  import { t } from "../i18n";
  import {
    askUserQuestionRequest,
    askUserQuestionResult,
  } from "../utils/askUserQuestion";
  import type { ToolContentBlock } from "../utils/transcript";

  let {
    block,
    onRespond,
  }: {
    block: ToolContentBlock;
    onRespond: (
      toolCallId: string,
      response:
        | { cancelled: true }
        | { cancelled: false; answer: string },
    ) => Promise<RpcResponse>;
  } = $props();

  const request = $derived(askUserQuestionRequest(block));
  const result = $derived(askUserQuestionResult(block.resultDetails));
  const pending = $derived(block.toolStatus === "pending" && !result);
  let selectedOption = $state("");
  let textAnswer = $state("");
  let submitting = $state(false);
  let error = $state("");

  async function respond(
    response:
      | { cancelled: true }
      | { cancelled: false; answer: string },
  ) {
    if (!block.toolCallId || submitting) return;
    submitting = true;
    error = "";
    try {
      const rpc = await onRespond(block.toolCallId, response);
      if (!rpc.success) throw new Error(rpc.error);
    } catch (cause) {
      error = cause instanceof Error ? cause.message : String(cause);
      submitting = false;
    }
  }

  function submit(event: SubmitEvent) {
    event.preventDefault();
    const answer = request?.options ? selectedOption : textAnswer.trim();
    if (answer) void respond({ cancelled: false, answer });
  }
</script>

{#if request}
  <article class="question-card" data-status={result?.status ?? "pending"}>
    <div class="question-label">{t("questionTool.label")}</div>
    <div class="question-text">{request.question}</div>

    {#if result?.status === "answered"}
      <div class="question-result">{t("questionTool.answered", { answer: result.answer })}</div>
    {:else if result?.status === "cancelled"}
      <div class="question-result muted">{t("questionTool.cancelled")}</div>
    {:else if !pending}
      <div class="question-error" role="alert">{block.resultText}</div>
    {:else}
      <form onsubmit={submit}>
        {#if request.options}
          <fieldset disabled={!pending || submitting}>
            <legend class="sr-only">{request.question}</legend>
            {#each request.options as option}
              <label class="question-option">
                <input type="radio" name={`question-${block.toolCallId}`} value={option} bind:group={selectedOption} />
                <span>{option}</span>
              </label>
            {/each}
          </fieldset>
        {:else}
          <label class="sr-only" for={`question-${block.toolCallId}`}>{request.question}</label>
          <input
            id={`question-${block.toolCallId}`}
            class="question-input"
            type="text"
            bind:value={textAnswer}
            disabled={!pending || submitting}
            placeholder={t("questionTool.inputPlaceholder")}
          />
        {/if}

        <div class="question-actions">
          <button type="button" class="secondary" disabled={!pending || submitting} onclick={() => void respond({ cancelled: true })}>
            {t("questionTool.cancel")}
          </button>
          <button type="submit" disabled={!pending || submitting || !(request.options ? selectedOption : textAnswer.trim())}>
            {t("questionTool.submit")}
          </button>
        </div>
      </form>
    {/if}

    {#if error}<div class="question-error" role="alert">{error}</div>{/if}
  </article>
{/if}

<style>
  .question-card {
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    gap: 12px;
    max-width: 640px;
    width: 100%;
    padding: 16px;
    border: 1px solid var(--border);
    border-radius: 14px;
    background: var(--panel);
    box-shadow: var(--shadow-raised);
  }

  .question-label {
    color: var(--text-subtle);
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .question-text {
    color: var(--text);
    font-size: 0.92rem;
    font-weight: 600;
    line-height: 1.5;
  }

  form { display: flex; flex-direction: column; gap: 12px; }
  fieldset { display: grid; gap: 8px; margin: 0; padding: 0; border: 0; }

  .question-option {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 12px;
    border: 1px solid var(--border);
    border-radius: 10px;
    background: var(--bg);
    cursor: pointer;
  }

  .question-option:has(input:checked) {
    border-color: var(--accent);
    background: color-mix(in srgb, var(--accent) 10%, var(--bg));
  }

  .question-option input { accent-color: var(--accent); }

  .question-input {
    box-sizing: border-box;
    width: 100%;
    padding: 10px 12px;
    border: 1px solid var(--border);
    border-radius: 10px;
    background: var(--bg);
    color: var(--text);
    font: inherit;
  }

  .question-input:focus-visible,
  button:focus-visible,
  .question-option:focus-within {
    outline: 2px solid var(--focus-ring);
    outline-offset: 2px;
  }

  .question-actions { display: flex; justify-content: flex-end; gap: 8px; }

  button {
    padding: 8px 14px;
    border: 1px solid var(--accent);
    border-radius: 9px;
    background: var(--accent);
    color: var(--bg);
    font: inherit;
    font-weight: 600;
    cursor: pointer;
  }

  button.secondary {
    border-color: var(--border);
    background: transparent;
    color: var(--text-muted);
  }

  button:disabled { cursor: not-allowed; opacity: 0.5; }
  .question-result { color: var(--text); }
  .question-result.muted { color: var(--text-muted); }
  .question-error { color: var(--error-text); font-size: 0.76rem; }

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
</style>
