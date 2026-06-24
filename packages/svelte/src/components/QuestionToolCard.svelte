<script lang="ts">
  import type { AskUserQuestionAnswer, RpcResponse } from "@dano/bridge/types";
  import { t } from "../i18n";
  import {
    type AskUserQuestionItem,
    askUserQuestionMarkdown,
    askUserQuestionRequest,
    askUserQuestionResult,
  } from "../utils/askUserQuestion";
  import type { ToolContentBlock } from "../utils/transcript";
  import MarkdownRenderer from "./MarkdownRenderer.svelte";

  const PENDING_RENDER_DELAY_MS = 400;

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
            answer: AskUserQuestionAnswer | Record<string, AskUserQuestionAnswer>;
          },
    ) => Promise<RpcResponse>;
  } = $props();

  const request = $derived(askUserQuestionRequest(block));
  const questionItems = $derived(
    request ? (request.batch ? request.questions : [request]) : [],
  );
  const result = $derived(askUserQuestionResult(block.resultDetails));
  const pending = $derived(block.toolStatus === "pending" && !result && active);
  const interrupted = $derived(block.toolStatus === "pending" && !result && !active);
  const requestKey = $derived(request ? JSON.stringify(request) : "");
  let initializedRequestKey = $state("");
  let selectedOption = $state<Record<string, string>>({});
  let selectedOptions = $state<Record<string, string[]>>({});
  let textAnswer = $state<Record<string, string>>({});
  let customAnswer = $state<Record<string, string>>({});
  let submitting = $state(false);
  let error = $state("");
  let pendingReady = $state(false);
  const showCard = $derived(Boolean(request) && (!pending || pendingReady));

  $effect(() => {
    if (!request || initializedRequestKey === requestKey) return;
    selectedOption = {};
    selectedOptions = {};
    textAnswer = {};
    customAnswer = {};

    for (const item of questionItems) {
      if (item.kind === "text") {
        textAnswer[item.id] = item.default ?? "";
      } else if (item.kind === "single") {
        selectedOption[item.id] = selectedOptionForDefault(item, item.default);
        customAnswer[item.id] = customAnswerForDefault(item, item.default);
      } else if (item.kind === "multiple") {
        selectedOptions[item.id] = selectedOptionsForDefault(item, item.default);
        customAnswer[item.id] = customAnswerForDefault(item, item.default?.find(
          answer => !item.options.includes(answer),
        ));
      }
    }

    initializedRequestKey = requestKey;
  });

  $effect(() => {
    if (!pending) {
      pendingReady = false;
      return;
    }
    pendingReady = false;
    // ponytail: hide transient invalid-tool retries; real pending questions show after this delay.
    const timer = setTimeout(() => {
      pendingReady = true;
    }, PENDING_RENDER_DELAY_MS);
    return () => clearTimeout(timer);
  });

  async function respond(
    response:
      | { cancelled: true }
      | {
          cancelled: false;
          answer: AskUserQuestionAnswer | Record<string, AskUserQuestionAnswer>;
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
    if (request.batch) {
      const answers: Record<string, AskUserQuestionAnswer> = {};
      for (const item of questionItems) {
        const answer = answerForItem(item);
        if (answer === null) return;
        answers[item.id] = answer;
      }
      void respond({ cancelled: false, answer: answers });
      return;
    }

    const answer = answerForItem(request);
    if (answer !== null) {
      void respond({ cancelled: false, answer });
    }
  }

  function canSubmit(): boolean {
    return questionItems.length > 0 &&
      questionItems.every(item => answerForItem(item) !== null);
  }

  function answerForItem(item: AskUserQuestionItem): AskUserQuestionAnswer | null {
    if (item.kind === "single") {
      const selected = selectedOption[item.id] ?? "";
      if (!selected) return null;
      return isOtherOption(selected) ? customAnswer[item.id]?.trim() || null : selected;
    }
    if (item.kind === "multiple") {
      const selected = selectedOptions[item.id] ?? [];
      if (selected.length === 0) return null;
      if (selected.some(isOtherOption) && !customAnswer[item.id]?.trim()) return null;
      return selected.map(option =>
        isOtherOption(option) ? customAnswer[item.id].trim() : option,
      );
    }
    if (item.kind === "text") return textAnswer[item.id]?.trim() || null;
    return null;
  }

  function isOtherOption(option: string): boolean {
    const normalized = option.trim().toLocaleLowerCase();
    return normalized === "其他" || normalized === "other";
  }

  function customAnswerSelected(item: AskUserQuestionItem): boolean {
    if (item.kind === "single") return isOtherOption(selectedOption[item.id] ?? "");
    if (item.kind === "multiple") {
      return (selectedOptions[item.id] ?? []).some(isOtherOption);
    }
    return false;
  }

  function selectedOptionForDefault(
    item: Extract<AskUserQuestionItem, { kind: "single" | "multiple" }>,
    answer: string | undefined,
  ): string {
    if (!answer) return "";
    if (item.options.includes(answer)) return answer;
    return item.options.find(isOtherOption) ?? "";
  }

  function selectedOptionsForDefault(
    item: Extract<AskUserQuestionItem, { kind: "multiple" }>,
    answers: string[] | undefined,
  ): string[] {
    if (!answers) return [];
    const selected = new Set<string>();
    const other = item.options.find(isOtherOption);
    for (const answer of answers) {
      if (item.options.includes(answer)) selected.add(answer);
      else if (other) selected.add(other);
    }
    return [...selected];
  }

  function customAnswerForDefault(
    item: Extract<AskUserQuestionItem, { kind: "single" | "multiple" }>,
    answer: string | undefined,
  ): string {
    return answer && !item.options.includes(answer) && item.options.some(isOtherOption)
      ? answer
      : "";
  }

  function answerText(
    answer: AskUserQuestionAnswer | Record<string, AskUserQuestionAnswer>,
  ): string {
    if (Array.isArray(answer)) return answer.join(", ");
    if (typeof answer === "boolean") {
      return t(answer ? "questionTool.confirm" : "questionTool.cancel");
    }
    if (typeof answer === "object") {
      return Object.entries(answer)
        .map(([key, value]) => `${key}: ${answerText(value)}`)
        .join("; ");
    }
    return answer;
  }
</script>

{#if request && showCard}
  <article class="question-card" data-status={result?.status ?? "pending"}>
    <div class="question-label">{t("questionTool.label")}</div>
    {#if !request.batch}
      <div class="question-text">
        <MarkdownRenderer content={askUserQuestionMarkdown(request.question)} />
      </div>
    {/if}

    {#if result?.status === "answered"}
      <div class="question-result">{t("questionTool.answered", { answer: answerText(result.answer) })}</div>
    {:else if result?.status === "cancelled"}
      <div class="question-result muted">{t("questionTool.cancelled")}</div>
    {:else if interrupted}
      <div class="question-result muted">{t("questionTool.interrupted")}</div>
    {:else if !pending}
      <div class="question-error" role="alert">{block.resultText}</div>
    {:else if !request.batch && request.kind === "confirm"}
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
        {#each questionItems as item}
          <div class:question-group={request.batch}>
            {#if request.batch}
              <div class="question-text">
                <MarkdownRenderer content={askUserQuestionMarkdown(item.question)} />
              </div>
            {/if}

            {#if item.kind === "single"}
              <fieldset disabled={!pending || submitting}>
                <legend class="sr-only">{item.question}</legend>
                {#each item.options as option}
                  <label class="question-option">
                    <input type="radio" name={`question-${block.toolCallId}-${item.id}`} value={option} bind:group={selectedOption[item.id]} />
                    <span>{option}</span>
                  </label>
                {/each}
              </fieldset>
            {:else if item.kind === "multiple"}
              <fieldset disabled={!pending || submitting}>
                <legend class="sr-only">{item.question}</legend>
                {#each item.options as option}
                  <label class="question-option">
                    <input type="checkbox" value={option} bind:group={selectedOptions[item.id]} />
                    <span>{option}</span>
                  </label>
                {/each}
              </fieldset>
            {:else if item.kind === "text"}
              <label class="sr-only" for={`question-${block.toolCallId}-${item.id}`}>{item.question}</label>
              <input
                id={`question-${block.toolCallId}-${item.id}`}
                class="question-input"
                type="text"
                bind:value={textAnswer[item.id]}
                disabled={!pending || submitting}
                placeholder={t("questionTool.inputPlaceholder")}
              />
            {/if}

            {#if customAnswerSelected(item)}
              <label class="sr-only" for={`question-other-${block.toolCallId}-${item.id}`}>
                {t("questionTool.otherPlaceholder")}
              </label>
              <input
                id={`question-other-${block.toolCallId}-${item.id}`}
                class="question-input"
                type="text"
                bind:value={customAnswer[item.id]}
                disabled={!pending || submitting}
                placeholder={t("questionTool.otherPlaceholder")}
              />
            {/if}
          </div>
        {/each}

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

  .question-group {
    display: grid;
    gap: 10px;
    padding-top: 12px;
    border-top: 1px solid var(--border);
  }

  .question-group:first-child {
    padding-top: 0;
    border-top: 0;
  }

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
