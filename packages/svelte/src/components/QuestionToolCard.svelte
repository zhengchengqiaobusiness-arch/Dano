<script lang="ts">
  import type { RpcResponse } from "@dano/bridge/types";
  import { t } from "../i18n";
  import {
    askUserQuestionRequest,
    askUserQuestionResult,
  } from "../utils/askUserQuestion";
  import type { ToolContentBlock } from "../utils/transcript";
  import MarkdownRenderer from "./MarkdownRenderer.svelte";

  let {
    block,
    active = true,
    onRespond,
  }: {
    block: ToolContentBlock;
    active?: boolean;
    onRespond: (
      toolCallId: string,
      response:
        | { cancelled: true }
        | {
            cancelled: false;
            answer: string | string[] | boolean;
          },
    ) => Promise<RpcResponse>;
  } = $props();

  const request = $derived(askUserQuestionRequest(block));
  const result = $derived(askUserQuestionResult(block.resultDetails));
  const pending = $derived(block.toolStatus === "pending" && !result && active);
  const interrupted = $derived(block.toolStatus === "pending" && !result && !active);
  let selectedOption = $state("");
  let selectedOptions = $state<string[]>([]);
  let textAnswer = $state("");
  let customAnswer = $state("");
  let submitting = $state(false);
  let error = $state("");

  async function respond(
    response:
      | { cancelled: true }
      | {
          cancelled: false;
          answer: string | string[] | boolean;
        },
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
    if (!request) return;
    if (request.kind === "single" && selectedOption) {
      void respond({
        cancelled: false,
        answer: isOtherOption(selectedOption)
          ? customAnswer.trim()
          : selectedOption,
      });
    } else if (request.kind === "multiple" && selectedOptions.length > 0) {
      void respond({
        cancelled: false,
        answer: selectedOptions.map(option =>
          isOtherOption(option) ? customAnswer.trim() : option,
        ),
      });
    } else if (request.kind === "text" && textAnswer.trim()) {
      void respond({ cancelled: false, answer: textAnswer.trim() });
    }
  }

  function canSubmit(): boolean {
    if (!request) return false;
    if (request.kind === "single") {
      return Boolean(selectedOption) &&
        (!isOtherOption(selectedOption) || Boolean(customAnswer.trim()));
    }
    if (request.kind === "multiple") {
      return selectedOptions.length > 0 &&
        (!selectedOptions.some(isOtherOption) || Boolean(customAnswer.trim()));
    }
    if (request.kind === "text") return Boolean(textAnswer.trim());
    return false;
  }

  function isOtherOption(option: string): boolean {
    const normalized = option.trim().toLocaleLowerCase();
    return normalized === "其他" || normalized === "other";
  }

  function customAnswerSelected(): boolean {
    if (request?.kind === "single") return isOtherOption(selectedOption);
    if (request?.kind === "multiple") return selectedOptions.some(isOtherOption);
    return false;
  }

  function answerText(answer: string | string[] | boolean): string {
    if (Array.isArray(answer)) return answer.join(", ");
    if (typeof answer === "boolean") {
      return t(answer ? "questionTool.confirm" : "questionTool.cancel");
    }
    return answer;
  }
</script>

{#if request}
  <article class="question-card" data-status={result?.status ?? "pending"}>
    <div class="question-label">{t("questionTool.label")}</div>
    <div class="question-text">
      <MarkdownRenderer content={request.question.replaceAll("\\n", "\n")} />
    </div>

    {#if result?.status === "answered"}
      <div class="question-result">{t("questionTool.answered", { answer: answerText(result.answer) })}</div>
    {:else if result?.status === "cancelled"}
      <div class="question-result muted">{t("questionTool.cancelled")}</div>
    {:else if interrupted}
      <div class="question-result muted">{t("questionTool.interrupted")}</div>
    {:else if !pending}
      <div class="question-error" role="alert">{block.resultText}</div>
    {:else if request.kind === "confirm"}
      <div class="question-actions">
        <button type="button" class="secondary" disabled={submitting} onclick={() => void respond({ cancelled: false, answer: false })}>
          {t("questionTool.cancel")}
        </button>
        <button type="button" disabled={submitting} onclick={() => void respond({ cancelled: false, answer: true })}>
          {t("questionTool.confirm")}
        </button>
      </div>
    {:else}
      <form onsubmit={submit}>
        {#if request.kind === "single"}
          <fieldset disabled={!pending || submitting}>
            <legend class="sr-only">{request.question}</legend>
            {#each request.options as option}
              <label class="question-option">
                <input type="radio" name={`question-${block.toolCallId}`} value={option} bind:group={selectedOption} />
                <span>{option}</span>
              </label>
            {/each}
          </fieldset>
        {:else if request.kind === "multiple"}
          <fieldset disabled={!pending || submitting}>
            <legend class="sr-only">{request.question}</legend>
            {#each request.options as option}
              <label class="question-option">
                <input type="checkbox" value={option} bind:group={selectedOptions} />
                <span>{option}</span>
              </label>
            {/each}
          </fieldset>
        {:else if request.kind === "text"}
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

        {#if customAnswerSelected()}
          <label class="sr-only" for={`question-other-${block.toolCallId}`}>
            {t("questionTool.otherPlaceholder")}
          </label>
          <input
            id={`question-other-${block.toolCallId}`}
            class="question-input"
            type="text"
            bind:value={customAnswer}
            disabled={!pending || submitting}
            placeholder={t("questionTool.otherPlaceholder")}
          />
        {/if}

        <div class="question-actions">
          <button type="button" class="secondary" disabled={!pending || submitting} onclick={() => void respond({ cancelled: true })}>
            {t("questionTool.cancel")}
          </button>
          <button type="submit" disabled={!pending || submitting || !canSubmit()}>
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
