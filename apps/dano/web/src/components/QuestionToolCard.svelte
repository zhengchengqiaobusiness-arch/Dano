<script lang="ts">
  import type {
    AskUserQuestionAnswer,
    AskUserQuestionDataSource,
    AskUserQuestionOptionId,
    FieldAssistAction,
    FieldAssistCommandPayload,
    FieldAssistResult,
    RpcResponse,
  } from "@dano/types/protocol";
  import { t } from "../i18n";
  import {
    type AskUserQuestionItem,
    type NormalizedAskUserQuestionOption,
    askUserQuestionAnswerMarkdown,
    askUserQuestionMarkdown,
    askUserQuestionRequest,
    askUserQuestionResult,
  } from "../utils/askUserQuestion";
  import {
    getFieldAssistWarning,
    toFieldAssistErrorMessage,
  } from "../utils/fieldAssist";
  import type { ToolContentBlock } from "../utils/transcript";
  import MarkdownRenderer from "./MarkdownRenderer.svelte";

  const PENDING_RENDER_DELAY_MS = 400;

  let {
    block,
    active = true,
    onRespond,
    onFieldAssist = undefined as
      | ((payload: FieldAssistCommandPayload) => Promise<FieldAssistResult>)
      | undefined,
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
    onFieldAssist?: (payload: FieldAssistCommandPayload) => Promise<FieldAssistResult>;
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
  let remoteOptions = $state<Record<string, NormalizedAskUserQuestionOption[]>>({});
  let remoteSearch = $state<Record<string, string>>({});
  let remotePage = $state<Record<string, number>>({});
  let remoteHasMore = $state<Record<string, boolean>>({});
  let remoteLoading = $state<Record<string, boolean>>({});
  let remoteError = $state<Record<string, string>>({});
  let submitting = $state(false);
  let error = $state("");
  let pendingReady = $state(false);
  let aiAssistLoading = $state<Record<string, FieldAssistAction | undefined>>({});
  let aiAssistError = $state<Record<string, string>>({});
  let aiAssistWarning = $state<Record<string, string>>({});
  let aiAssistSeq = $state(0);
  const showCard = $derived(Boolean(request) && (!pending || pendingReady));

  $effect(() => {
    if (!request || initializedRequestKey === requestKey) return;
    selectedOption = {};
    selectedOptions = {};
    textAnswer = {};
    customAnswer = {};
    remoteOptions = {};
    remoteSearch = {};
    remotePage = {};
    remoteHasMore = {};
    remoteLoading = {};
    remoteError = {};
    submitting = false;
    error = "";
    aiAssistLoading = {};
    aiAssistError = {};
    aiAssistWarning = {};
    aiAssistSeq += 1;

    for (const item of questionItems) {
      if (item.kind === "text") {
        textAnswer[item.id] = item.default ?? "";
      } else if (item.kind === "single" || item.kind === "select" || item.kind === "treeSelect") {
        selectedOption[item.id] = selectedOptionForDefault(item, item.default);
        customAnswer[item.id] = customAnswerForDefault(item, item.default);
        if ((item.kind === "select" || item.kind === "treeSelect") && item.dataSource) {
          void loadRemoteOptions(item, 1, "", false);
        }
      } else if (item.kind === "multiple") {
        selectedOptions[item.id] = selectedOptionsForDefault(item, item.default);
        customAnswer[item.id] = customAnswerForDefault(item, item.default?.find(
          answer => !itemOptions(item).some(option => option.id === answer),
        ));
        if (item.dataSource) void loadRemoteOptions(item, 1, "", false);
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
    if (item.kind === "single" || item.kind === "select" || item.kind === "treeSelect") {
      const selected = selectedOption[item.id] ?? "";
      if (!selected) return null;
      return selectedOptionIsOther(item, selected)
        ? customAnswer[item.id]?.trim() || null
        : selectedOptionValue(item, selected);
    }
    if (item.kind === "multiple") {
      const selected = selectedOptions[item.id] ?? [];
      if (selected.length === 0) return null;
      if (selected.some(option => selectedOptionIsOther(item, option)) && !customAnswer[item.id]?.trim()) return null;
      return selected.map(option =>
        selectedOptionIsOther(item, option)
          ? customAnswer[item.id].trim()
          : selectedOptionValue(item, option),
      );
    }
    if (item.kind === "text") return textAnswer[item.id]?.trim() || null;
    return null;
  }

  function preventEnterSubmit(event: KeyboardEvent) {
    if (event.key === "Enter") {
      event.preventDefault();
    }
  }

  function textItemFieldType(item: AskUserQuestionItem): "input" | "textarea" {
    return item.kind === "text" && item.inputType === "textarea"
      ? "textarea"
      : "input";
  }

  async function runFieldAssist(item: AskUserQuestionItem, action: FieldAssistAction) {
    if (item.kind !== "text") return;
    if (!onFieldAssist) {
      aiAssistError[item.id] = t("questionTool.aiAssistFailed");
      return;
    }

    const currentValue = textAnswer[item.id] ?? "";
    aiAssistWarning[item.id] = getFieldAssistWarning({
      title: item.question,
      placeholder: t("questionTool.inputPlaceholder"),
      prefill: item.default,
    });

    if (action === "polish" && !currentValue.trim()) {
      aiAssistError[item.id] = t("questionTool.aiAssistEmptyPolish");
      return;
    }

    const fieldType = textItemFieldType(item);
    const seq = ++aiAssistSeq;
    const previousValue = currentValue;
    aiAssistLoading[item.id] = action;
    aiAssistError[item.id] = "";

    try {
      const result = await onFieldAssist({
        requestId: `${block.toolCallId ?? "question"}:${item.id}`,
        action,
        fieldType,
        requestMethod: fieldType === "textarea" ? "editor" : "input",
        title: item.question,
        placeholder: t("questionTool.inputPlaceholder"),
        currentValue,
        prefill: item.default,
      });
      if (seq !== aiAssistSeq) return;
      aiAssistWarning[item.id] =
        result.metadata.warnings?.[0]?.message ?? aiAssistWarning[item.id] ?? "";
      textAnswer[item.id] = result.value;
    } catch (cause) {
      textAnswer[item.id] = previousValue;
      aiAssistError[item.id] = toFieldAssistErrorMessage(cause);
    } finally {
      if (seq === aiAssistSeq) aiAssistLoading[item.id] = undefined;
    }
  }

  function isOtherOption(option: string | NormalizedAskUserQuestionOption): boolean {
    const normalized = (typeof option === "string" ? option : option.label)
      .trim()
      .toLocaleLowerCase();
    return normalized === "其他" || normalized === "other";
  }

  function customAnswerSelected(item: AskUserQuestionItem): boolean {
    if (item.kind === "single" || item.kind === "select" || item.kind === "treeSelect") {
      return selectedOptionIsOther(item, selectedOption[item.id] ?? "");
    }
    if (item.kind === "multiple") {
      return (selectedOptions[item.id] ?? []).some(option => selectedOptionIsOther(item, option));
    }
    return false;
  }

  function selectedOptionForDefault(
    item: ChoiceQuestionItem,
    answer: AskUserQuestionOptionId | undefined,
  ): string {
    if (answer === undefined) return "";
    if (itemOptions(item).some(option => option.id === answer)) return optionKey(answer);
    const other = itemOptions(item).find(isOtherOption);
    return other ? optionKey(other.id) : "";
  }

  function selectedOptionsForDefault(
    item: Extract<AskUserQuestionItem, { kind: "multiple" }>,
    answers: AskUserQuestionOptionId[] | undefined,
  ): string[] {
    if (!answers) return [];
    const selected = new Set<string>();
    const other = itemOptions(item).find(isOtherOption);
    for (const answer of answers) {
      if (itemOptions(item).some(option => option.id === answer)) selected.add(optionKey(answer));
      else if (other) selected.add(optionKey(other.id));
    }
    return [...selected];
  }

  function customAnswerForDefault(
    item: ChoiceQuestionItem,
    answer: AskUserQuestionOptionId | undefined,
  ): string {
    return typeof answer === "string" &&
      !itemOptions(item).some(option => option.id === answer) &&
      itemOptions(item).some(isOtherOption)
      ? answer
      : "";
  }

  type ChoiceQuestionItem = Extract<
    AskUserQuestionItem,
    { kind: "single" | "multiple" | "select" | "treeSelect" }
  >;

  function itemOptions(item: ChoiceQuestionItem): NormalizedAskUserQuestionOption[] {
    const byId = new Map<string, NormalizedAskUserQuestionOption>();
    for (const option of item.options) byId.set(optionKey(option.id), option);
    for (const option of remoteOptions[item.id] ?? []) byId.set(optionKey(option.id), option);
    return [...byId.values()];
  }

  function selectedOptionIsOther(item: ChoiceQuestionItem, selected: string): boolean {
    const option = itemOptions(item).find(candidate => optionKey(candidate.id) === selected);
    return option ? isOtherOption(option) : isOtherOption(selected);
  }

  function selectedOptionValue(
    item: ChoiceQuestionItem,
    selected: string,
  ): AskUserQuestionOptionId {
    return itemOptions(item).find(option => optionKey(option.id) === selected)?.id ?? selected;
  }

  function optionKey(id: AskUserQuestionOptionId): string {
    return `${typeof id}:${String(id)}`;
  }

  async function loadRemoteOptions(
    item: RemoteQuestionItem,
    page: number,
    search: string,
    append: boolean,
  ): Promise<void> {
    if (!item.dataSource || remoteLoading[item.id]) return;
    remoteLoading[item.id] = true;
    remoteError[item.id] = "";
    try {
      const pageSize = item.dataSource.pageSize ?? 20;
      const response = await fetchDataSource(item.dataSource, search, page, pageSize);
      const options = normalizeRemoteOptions(
        response.data,
        item.dataSource,
        item.kind === "treeSelect" || item.inputType === "treeSelect",
      );
      remoteOptions[item.id] = append ? [...(remoteOptions[item.id] ?? []), ...options] : options;
      remotePage[item.id] = page;
      remoteHasMore[item.id] =
        typeof response.total === "number"
          ? remoteOptions[item.id].length < response.total
          : options.length >= pageSize;
    } catch (cause) {
      remoteError[item.id] = cause instanceof Error ? cause.message : String(cause);
    } finally {
      remoteLoading[item.id] = false;
    }
  }

  async function fetchDataSource(
    dataSource: NonNullable<RemoteQuestionItem["dataSource"]>,
    search: string,
    page: number,
    pageSize: number,
  ): Promise<{ data: unknown; total?: number }> {
    const method = dataSource.method ?? "GET";
    const params = { ...(dataSource.params ?? {}) } as Record<string, unknown>;
    if (dataSource.searchParam && search) params[dataSource.searchParam] = search;
    if (dataSource.pageParam) params[dataSource.pageParam] = page;
    if (dataSource.pageSizeParam) params[dataSource.pageSizeParam] = pageSize;

    const url = new URL(dataSource.endpoint, window.location.origin);
    if (url.origin !== window.location.origin) {
      throw new Error("Remote question dataSource must be same-origin");
    }
    const init: RequestInit = { method };
    if (method === "GET") {
      for (const [key, value] of Object.entries(params)) {
        if (value !== undefined && value !== null) url.searchParams.set(key, String(value));
      }
    } else {
      init.headers = { "content-type": "application/json" };
      init.body = JSON.stringify(params);
    }

    const response = await fetch(url, init);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const json = await response.json() as unknown;
    const data = dataSource.resultPath ? valueAtPath(json, dataSource.resultPath) : json;
    const totalValue = dataSource.totalPath ? valueAtPath(json, dataSource.totalPath) : undefined;
    return { data, total: typeof totalValue === "number" ? totalValue : undefined };
  }

  function normalizeRemoteOptions(
    data: unknown,
    dataSource: NonNullable<RemoteQuestionItem["dataSource"]>,
    flattenChildren: boolean,
  ): NormalizedAskUserQuestionOption[] {
    const rows = Array.isArray(data) ? data : [];
    const idField = dataSource.idField ?? "id";
    const labelField = dataSource.labelField ?? "label";
    const childrenField = dataSource.childrenField ?? "children";
    const extraFields = dataSource.extraFields ?? [];
    const options: NormalizedAskUserQuestionOption[] = [];

    const visit = (row: unknown, depth: number) => {
      if (!isRecord(row)) return;
      const id = row[idField];
      const label = row[labelField];
      if (typeof id !== "string" && typeof id !== "number") return;
      if (typeof label !== "string" && typeof label !== "number") return;
      const extra: Record<string, unknown> = {};
      for (const field of extraFields) extra[field] = row[field];
      options.push({
        id,
        label: `${"  ".repeat(depth)}${String(label)}`,
        ...(extraFields.length > 0 ? { extra } : {}),
      });
      if (flattenChildren && Array.isArray(row[childrenField])) {
        for (const child of row[childrenField]) visit(child, depth + 1);
      }
    };

    for (const row of rows) visit(row, 0);
    return options;
  }

  function valueAtPath(value: unknown, path: string): unknown {
    return path.split(".").reduce<unknown>((current, part) => {
      return isRecord(current) ? current[part] : undefined;
    }, value);
  }

  function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === "object" && value !== null && !Array.isArray(value);
  }

  type RemoteQuestionItem = Extract<
    AskUserQuestionItem,
    { kind: "select" | "treeSelect" | "multiple" }
  > & { dataSource?: AskUserQuestionDataSource; inputType?: "treeSelect" };

  const answeredMarkdown = $derived(
    request && result?.status === "answered"
      ? t("questionTool.answered", {
          answer: askUserQuestionAnswerMarkdown(request, result.answer, {
            confirm: t("questionTool.confirm"),
            cancel: t("questionTool.cancel"),
          }),
        })
      : "",
  );
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
      <div class="question-result">
        <MarkdownRenderer content={answeredMarkdown} />
      </div>
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
                {#each itemOptions(item) as option}
                  <label class="question-option">
                    <input type="radio" name={`question-${block.toolCallId}-${item.id}`} value={optionKey(option.id)} bind:group={selectedOption[item.id]} />
                    <span>{option.label}</span>
                  </label>
                {/each}
              </fieldset>
            {:else if item.kind === "select" || item.kind === "treeSelect"}
              {#if item.dataSource}
                <div class="question-remote-row">
                  <input
                    class="question-input"
                    type="search"
                    bind:value={remoteSearch[item.id]}
                    disabled={!pending || submitting || remoteLoading[item.id]}
                    placeholder={t("questionTool.searchPlaceholder")}
                    onkeydown={preventEnterSubmit}
                  />
                  <button
                    type="button"
                    class="secondary"
                    disabled={!pending || submitting || remoteLoading[item.id]}
                    onclick={() => void loadRemoteOptions(item, 1, remoteSearch[item.id] ?? "", false)}
                  >
                    {t("questionTool.search")}
                  </button>
                </div>
              {/if}
              <label class="sr-only" for={`question-${block.toolCallId}-${item.id}`}>{item.question}</label>
              <select
                id={`question-${block.toolCallId}-${item.id}`}
                class="question-input"
                bind:value={selectedOption[item.id]}
                disabled={!pending || submitting || remoteLoading[item.id]}
              >
                <option value="">{t("questionTool.selectPlaceholder")}</option>
                {#each itemOptions(item) as option}
                  <option value={optionKey(option.id)}>{option.label}</option>
                {/each}
              </select>
              {#if remoteError[item.id]}
                <div class="question-error" role="alert">{remoteError[item.id]}</div>
              {/if}
              {#if item.dataSource && remoteHasMore[item.id]}
                <button
                  type="button"
                  class="secondary load-more"
                  disabled={!pending || submitting || remoteLoading[item.id]}
                  onclick={() => void loadRemoteOptions(item, (remotePage[item.id] ?? 1) + 1, remoteSearch[item.id] ?? "", true)}
                >
                  {remoteLoading[item.id] ? t("questionTool.loading") : t("questionTool.loadMore")}
                </button>
              {/if}
            {:else if item.kind === "multiple"}
              {#if item.dataSource}
                <div class="question-remote-row">
                  <input
                    class="question-input"
                    type="search"
                    bind:value={remoteSearch[item.id]}
                    disabled={!pending || submitting || remoteLoading[item.id]}
                    placeholder={t("questionTool.searchPlaceholder")}
                    onkeydown={preventEnterSubmit}
                  />
                  <button
                    type="button"
                    class="secondary"
                    disabled={!pending || submitting || remoteLoading[item.id]}
                    onclick={() => void loadRemoteOptions(item, 1, remoteSearch[item.id] ?? "", false)}
                  >
                    {t("questionTool.search")}
                  </button>
                </div>
              {/if}
              <fieldset disabled={!pending || submitting}>
                <legend class="sr-only">{item.question}</legend>
                {#each itemOptions(item) as option}
                  <label class="question-option">
                    <input type="checkbox" value={optionKey(option.id)} bind:group={selectedOptions[item.id]} />
                    <span>{option.label}</span>
                  </label>
                {/each}
              </fieldset>
              {#if remoteError[item.id]}
                <div class="question-error" role="alert">{remoteError[item.id]}</div>
              {/if}
              {#if item.dataSource && remoteHasMore[item.id]}
                <button
                  type="button"
                  class="secondary load-more"
                  disabled={!pending || submitting || remoteLoading[item.id]}
                  onclick={() => void loadRemoteOptions(item, (remotePage[item.id] ?? 1) + 1, remoteSearch[item.id] ?? "", true)}
                >
                  {remoteLoading[item.id] ? t("questionTool.loading") : t("questionTool.loadMore")}
                </button>
              {/if}
            {:else if item.kind === "text"}
              <label class="sr-only" for={`question-${block.toolCallId}-${item.id}`}>{item.question}</label>
              {#if item.inputType === "textarea"}
                <textarea
                  id={`question-${block.toolCallId}-${item.id}`}
                  class="question-input question-textarea"
                  rows="4"
                  bind:value={textAnswer[item.id]}
                  disabled={!pending || submitting}
                  placeholder={t("questionTool.inputPlaceholder")}
                ></textarea>
              {:else}
                <input
                  id={`question-${block.toolCallId}-${item.id}`}
                  class="question-input"
                  type="text"
                  bind:value={textAnswer[item.id]}
                  disabled={!pending || submitting}
                  placeholder={t("questionTool.inputPlaceholder")}
                  onkeydown={preventEnterSubmit}
                />
              {/if}
              <div class="question-ai-actions">
                <button
                  type="button"
                  class="secondary"
                  disabled={!pending || submitting || Boolean(aiAssistLoading[item.id])}
                  onclick={() => void runFieldAssist(item, "regenerate")}
                >
                  {aiAssistLoading[item.id] === "regenerate" ? t("questionTool.aiAssistGenerating") : t("questionTool.aiAssistRegenerate")}
                </button>
                <button
                  type="button"
                  class="secondary"
                  disabled={!pending || submitting || Boolean(aiAssistLoading[item.id]) || !textAnswer[item.id]?.trim()}
                  onclick={() => void runFieldAssist(item, "polish")}
                >
                  {aiAssistLoading[item.id] === "polish" ? t("questionTool.aiAssistPolishing") : t("questionTool.aiAssistPolish")}
                </button>
              </div>
              {#if aiAssistWarning[item.id]}
                <div class="question-warning" role="status">{aiAssistWarning[item.id]}</div>
              {/if}
              {#if aiAssistError[item.id]}
                <div class="question-error" role="alert">{aiAssistError[item.id]}</div>
              {/if}
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
                onkeydown={preventEnterSubmit}
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
    border-radius: 14px;
    background: color-mix(in srgb, var(--panel) 84%, var(--panel-2));
    box-shadow:
      0 1px 0 color-mix(in srgb, var(--bg) 64%, transparent) inset,
      var(--shadow-raised);
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

  .question-option input[type="radio"] {
    appearance: none;
    -webkit-appearance: none;
    flex: 0 0 auto;
    width: 16px;
    height: 16px;
    margin: 0;
    border: 2px solid var(--border-strong);
    border-radius: 999px;
    background: var(--bg);
    transition:
      border-color 0.12s ease,
      background 0.12s ease;
  }

  .question-option input[type="radio"]:checked {
    border-color: var(--accent);
    background:
      radial-gradient(circle, var(--accent) 0 38%, transparent 42%),
      var(--bg);
  }

  .question-remote-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 8px;
  }

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

  .question-textarea {
    min-height: 96px;
    resize: vertical;
  }

  select.question-input {
    appearance: none;
    -webkit-appearance: none;
    padding-right: 36px;
    background:
      linear-gradient(45deg, transparent 50%, var(--text-muted) 50%) right 17px center / 6px 6px no-repeat,
      linear-gradient(135deg, var(--text-muted) 50%, transparent 50%) right 12px center / 6px 6px no-repeat,
      var(--bg);
  }

  .question-input:focus-visible,
  button:focus-visible,
  .question-option:focus-within {
    outline: 2px solid var(--focus-ring);
    outline-offset: 2px;
  }

  .question-actions { display: flex; justify-content: flex-end; gap: 8px; }
  .question-ai-actions { display: flex; flex-wrap: wrap; gap: 8px; }

  .load-more { justify-self: start; }

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
  .question-warning { color: var(--text-muted); font-size: 0.76rem; }
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
