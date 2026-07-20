<script lang="ts">
  import type {
    AskUserQuestionAnswer,
    AskUserQuestionConfirmationCardRequest,
    AskUserQuestionConfirmationForm,
    AskUserQuestionDataSource,
    AskUserQuestionOptionId,
    AskUserQuestionResult,
    FieldAssistAction,
    FieldAssistCommandPayload,
    FieldAssistResult,
    FormInteractionProjection,
    RpcResponse,
  } from "@dano/types/protocol";
  import { onDestroy, tick } from "svelte";
  import { t } from "../i18n";
  import {
    type AskUserQuestionItem,
    type NormalizedAskUserQuestionOption,
    askUserQuestionAnswerItems,
    askUserQuestionAnswerMarkdown,
    askUserQuestionConfirmationForms,
    askUserQuestionMarkdown,
    askUserQuestionRequest,
    askUserQuestionResult,
  } from "../utils/askUserQuestion";
  import {
    getFieldAssistWarning,
    invalidateFieldAssistRuns,
    isCurrentFieldAssistRun,
    nextFieldAssistRunId,
    toFieldAssistErrorMessage,
  } from "../utils/fieldAssist";
  import type { ToolContentBlock } from "../utils/transcript";
  import MarkdownRenderer from "./MarkdownRenderer.svelte";
  import QuestionDateField from "./QuestionDateField.svelte";
  import QuestionFieldLabel from "./QuestionFieldLabel.svelte";
  import QuestionRemoteCombobox from "./QuestionRemoteCombobox.svelte";
  import SubmittedAnswerValue from "./SubmittedAnswerValue.svelte";
  import ChevronDown from "lucide-svelte/icons/chevron-down";
  import Check from "lucide-svelte/icons/check";
  import RefreshCw from "lucide-svelte/icons/refresh-cw";
  import Sparkle from "lucide-svelte/icons/sparkle";
  import "./questionToolControls.css";
  import type { QuestionFocusChange } from "./questionFocus";

  const PENDING_RENDER_DELAY_MS = 400;

  let {
    block,
    active = true,
    onPresent,
    onRespond,
    onRevise,
    onCancelRevision = undefined as
      | ((toolCallId: string, expectedRevision: number) => Promise<RpcResponse>)
      | undefined,
    onSubmitRevision,
    onFocusChange = undefined as
      | ((target: QuestionFocusChange) => void)
      | undefined,
    onFieldAssist = undefined as
      | ((payload: FieldAssistCommandPayload) => Promise<FieldAssistResult>)
      | undefined,
  }: {
    block: ToolContentBlock;
    active?: boolean;
    onPresent: (toolCallId: string) => Promise<RpcResponse>;
    onRespond: (
      toolCallId: string,
      response:
        | { cancelled: true; expectedRevision?: number }
        | {
            cancelled: false;
            expectedRevision?: number;
            answer: AskUserQuestionAnswer | Record<string, AskUserQuestionAnswer>;
        },
    ) => Promise<RpcResponse>;
    onRevise: (toolCallId: string, expectedRevision: number) => Promise<RpcResponse>;
    onCancelRevision?: (toolCallId: string, expectedRevision: number) => Promise<RpcResponse>;
    onSubmitRevision: (
      toolCallId: string,
      expectedRevision: number,
      answers: Record<string, Record<string, AskUserQuestionAnswer>>,
    ) => Promise<RpcResponse>;
    onFocusChange?: (target: QuestionFocusChange) => void;
    onFieldAssist?: (payload: FieldAssistCommandPayload) => Promise<FieldAssistResult>;
  } = $props();

  const projectedRequest = $derived(askUserQuestionRequest(block));
  const request = $derived(projectedRequest);
  let interactionOverride = $state<FormInteractionProjection>();
  const interaction = $derived(
    interactionOverride &&
      (!block.formInteraction ||
        interactionOverride.revision >= block.formInteraction.revision)
      ? interactionOverride
      : block.formInteraction,
  );
  const isConfirmation = $derived(
    Boolean(request && !request.batch && request.kind === "confirm"),
  );
  type RevisionQuestionItem = AskUserQuestionItem & {
    revisionFormId?: string;
    originalId?: string;
    revisionTitle?: string;
  };
  const revisionItems = $derived<RevisionQuestionItem[]>(
    interaction?.state === "revising"
      ? interaction.forms.flatMap(form =>
          form.questions.map((item, index) => ({
            ...item,
            id: `${form.formId}:${item.id}`,
            revisionFormId: form.formId,
            originalId: item.id,
            revisionTitle: index === 0 ? form.title : undefined,
          })),
        )
      : [],
  );
  const questionItems = $derived<RevisionQuestionItem[]>(
    revisionItems.length > 0
      ? revisionItems
      : request
      ? request.batch || request.kind === "confirm"
        ? request.questions
        : [request]
      : [],
  );
  let submittedResult = $state<AskUserQuestionResult | null>(null);
  let presentationToolCallId = $state("");
  let focusedToolCallId = $state("");
  const result = $derived(
    askUserQuestionResult(block.resultDetails) ?? submittedResult,
  );
  const pending = $derived(
    isConfirmation
      ? interaction?.state === "awaiting_confirmation" ||
          (!interaction &&
            (block.questionState === "awaiting_presentation" ||
              (block.questionState === undefined &&
                block.toolStatus === "pending" &&
                active)))
      : block.toolStatus === "pending" && !result && active,
  );
  const interrupted = $derived(
    !isConfirmation && block.toolStatus === "pending" && !result && !active,
  );
  const isFocusableGroupedForm = $derived(
    Boolean(
      request?.batch &&
      request.title?.trim() &&
      request.questions.length > 1
    ),
  );
  const isActionableConfirmation = $derived(
    Boolean(
      isConfirmation &&
      interaction &&
      (interaction.state === "awaiting_confirmation" ||
        interaction.state === "revising") &&
      interaction.allowedActions.length > 0
    ),
  );
  const focusActionable = $derived(
    active &&
    block.toolStatus === "pending" &&
    ((isFocusableGroupedForm && !askUserQuestionResult(block.resultDetails)) ||
      isActionableConfirmation),
  );
  const requestKey = $derived(
    request ? JSON.stringify([request, interaction]) : "",
  );
  const revising = $derived(isConfirmation && interaction?.state === "revising");
  const formEnabled = $derived(pending || revising);
  const formAnswer = $derived(
    result?.status === "answered" && typeof result.answer === "object" && !Array.isArray(result.answer)
      ? result.answer
      : undefined,
  );
  const interactionFormAnswer = $derived(
    result?.status === "answered"
      ? interaction?.forms.find(form => form.formId === result.formId)?.answer
      : undefined,
  );
  let initializedRequestKey = $state("");
  let selectedOption = $state<Record<string, string>>({});
  let selectedOptions = $state<Record<string, string[]>>({});
  let textAnswer = $state<Record<string, string>>({});
  let dateAnswer = $state<Record<string, string | undefined>>({});
  let customAnswer = $state<Record<string, string>>({});
  let remoteOptions = $state<Record<string, NormalizedAskUserQuestionOption[]>>({});
  let remoteSearch = $state<Record<string, string>>({});
  let remotePage = $state<Record<string, number>>({});
  let remoteHasMore = $state<Record<string, boolean>>({});
  let remoteLoading = $state<Record<string, boolean>>({});
  let remoteError = $state<Record<string, string>>({});
  let remoteRequestSeq = $state<Record<string, number>>({});
  let submitting = $state(false);
  let error = $state("");
  let pendingReady = $state(false);
  let cardElement = $state<HTMLElement | null>(null);
  let componentAlive = true;
  let aiAssistLoading = $state<Record<string, FieldAssistAction | undefined>>({});
  let aiAssistError = $state<Record<string, string>>({});
  let aiAssistWarning = $state<Record<string, string>>({});
  let aiAssistSeq = $state<Record<string, number>>({});
  const showCard = $derived(
    Boolean(request) &&
      (!pending || pendingReady || Boolean(interaction)) &&
      !(request?.batch && result?.status === "answered" && interaction?.state === "revising"),
  );

  $effect(() => {
    if (!request || initializedRequestKey === requestKey) return;
    selectedOption = {};
    selectedOptions = {};
    textAnswer = {};
    dateAnswer = {};
    customAnswer = {};
    remoteOptions = {};
    remoteSearch = {};
    remotePage = {};
    remoteHasMore = {};
    remoteLoading = {};
    remoteError = {};
    remoteRequestSeq = {};
    submitting = false;
    error = "";
    aiAssistLoading = {};
    aiAssistError = {};
    aiAssistWarning = {};
    aiAssistSeq = invalidateFieldAssistRuns(aiAssistSeq);
    submittedResult = null;

    for (const item of questionItems) {
      const revisionForm = item.revisionFormId
        ? interaction?.forms.find(form => form.formId === item.revisionFormId)
        : undefined;
      const savedAnswer = revisionForm
        ? revisionForm.answer[item.originalId ?? item.id]
        : interactionFormAnswer !== undefined
          ? interactionFormAnswer[item.id]
          : formAnswer?.[item.id];
      const authoritativeAnswer =
        revisionForm !== undefined || interactionFormAnswer !== undefined;
      const fallbackDefault = authoritativeAnswer ? undefined : item.default;
      if (item.kind === "text") {
        textAnswer[item.id] = typeof savedAnswer === "string"
          ? savedAnswer
          : typeof fallbackDefault === "string"
            ? fallbackDefault
            : "";
      } else if (item.kind === "date") {
        dateAnswer[item.id] = typeof savedAnswer === "string"
          ? savedAnswer
          : typeof fallbackDefault === "string"
            ? fallbackDefault
            : undefined;
      } else if (item.kind === "single" || item.kind === "select" || item.kind === "treeSelect") {
        const answer = typeof savedAnswer === "string" || typeof savedAnswer === "number"
          ? savedAnswer
          : typeof fallbackDefault === "string" || typeof fallbackDefault === "number"
            ? fallbackDefault
            : undefined;
        selectedOption[item.id] = selectedOptionForDefault(item, answer);
        customAnswer[item.id] = customAnswerForDefault(item, answer);
        if ((item.kind === "select" || item.kind === "treeSelect") && item.dataSource) {
          void loadRemoteOptions(item, 1, "", false);
        }
      } else if (item.kind === "multiple") {
        const answers = Array.isArray(savedAnswer)
          ? savedAnswer
          : Array.isArray(fallbackDefault)
            ? fallbackDefault
            : undefined;
        selectedOptions[item.id] = selectedOptionsForDefault(item, answers);
        customAnswer[item.id] = customAnswerForDefault(item, answers?.find(
          answer => !itemOptions(item).some(option => option.id === answer),
        ));
        if (item.dataSource) void loadRemoteOptions(item, 1, "", false);
      }
    }

    initializedRequestKey = requestKey;
  });

  $effect(() => {
    const toolCallId = block.toolCallId;
    if (!showCard || !pending || !toolCallId || presentationToolCallId === toolCallId) {
      return;
    }
    presentationToolCallId = toolCallId;
    void tick()
      .then(() => onPresent(toolCallId))
      .then(response => {
        if (!componentAlive || presentationToolCallId !== toolCallId) return;
        applyAuthoritativeInteraction(response);
        if (!response.success) throw new Error(response.error);
        requestFocus(toolCallId);
      })
      .catch(cause => {
        if (presentationToolCallId === toolCallId) {
          presentationToolCallId = "";
          error = cause instanceof Error ? cause.message : String(cause);
        }
      });
  });

  $effect(() => {
    const toolCallId = block.toolCallId;
    if (
      !isConfirmation ||
      !focusActionable ||
      !toolCallId ||
      focusedToolCallId ||
      presentationToolCallId
    ) {
      return;
    }
    requestFocus(toolCallId);
  });

  $effect(() => {
    if (!focusedToolCallId || focusActionable) return;
    const toolCallId = focusedToolCallId;
    focusedToolCallId = "";
    onFocusChange?.({ toolCallId, element: null });
  });

  onDestroy(() => {
    componentAlive = false;
    if (focusedToolCallId) {
      onFocusChange?.({ toolCallId: focusedToolCallId, element: null });
    }
  });

  function requestFocus(toolCallId: string): void {
    if (!componentAlive || !focusActionable || !cardElement) return;
    focusedToolCallId = toolCallId;
    onFocusChange?.({ toolCallId, element: cardElement });
  }

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
      const rpc = await onRespond(block.toolCallId, {
        ...response,
        ...(interaction ? { expectedRevision: interaction.revision } : {}),
      });
      applyAuthoritativeInteraction(rpc);
      if (!rpc.success) throw new Error(rpc.error);
      submittedResult = response.cancelled
        ? { status: "cancelled" }
        : askUserQuestionResult(rpc.data) ?? {
            status: "answered",
            answer: response.answer,
          };
    } catch (cause) {
      error = cause instanceof Error ? cause.message : String(cause);
    } finally {
      submitting = false;
    }
  }

  function submit(event: SubmitEvent) {
    event.preventDefault();
    if (!request) return;
    if (revising && interaction) {
      const answers = Object.fromEntries(
        interaction.forms.map(form => [form.formId, { ...form.answer }]),
      );
      for (const item of questionItems) {
        if (!item.revisionFormId) continue;
        const answer = answerForItem(item);
        if (answer === null) return;
        if (answer === undefined) delete answers[item.revisionFormId][item.originalId ?? item.id];
        else answers[item.revisionFormId][item.originalId ?? item.id] = answer;
      }
      void submitRevision(answers);
      return;
    }
    if (request.batch) {
      const answers: Record<string, AskUserQuestionAnswer> = {};
      for (const item of questionItems) {
        const answer = answerForItem(item);
        if (answer === null) return;
        if (answer !== undefined) answers[item.id] = answer;
      }
      void respond({ cancelled: false, answer: answers });
      return;
    }

    if (request.kind === "confirm") return;
    const answer = answerForItem(request);
    if (answer !== null) {
      void respond({ cancelled: false, answer: answer ?? "" });
    }
  }

  async function requestInteraction(
    request: (toolCallId: string, expectedRevision: number) => Promise<RpcResponse>,
  ) {
    if (!block.toolCallId || !interaction || submitting) return;
    submitting = true;
    error = "";
    try {
      const rpc = await request(block.toolCallId, interaction.revision);
      applyAuthoritativeInteraction(rpc);
      if (!rpc.success) throw new Error(rpc.error);
    } catch (cause) {
      error = cause instanceof Error ? cause.message : String(cause);
    } finally {
      submitting = false;
    }
  }

  async function startRevision() {
    await requestInteraction(onRevise);
  }

  async function submitRevision(
    answers: Record<string, Record<string, AskUserQuestionAnswer>>,
  ) {
    await requestInteraction((toolCallId, expectedRevision) =>
      onSubmitRevision(toolCallId, expectedRevision, answers)
    );
  }

  async function cancelRevision() {
    if (!onCancelRevision) return;
    await requestInteraction(onCancelRevision);
  }

  function applyAuthoritativeInteraction(response: RpcResponse): void {
    const data = response.data;
    const candidate = isFormInteractionProjection(data)
      ? data
      : isRecord(data) && isFormInteractionProjection(data.interaction)
        ? data.interaction
        : undefined;
    if (
      candidate &&
      (!interactionOverride || candidate.revision >= interactionOverride.revision)
    ) {
      interactionOverride = candidate;
    }
  }

  function isFormInteractionProjection(
    value: unknown,
  ): value is FormInteractionProjection {
    return isRecord(value) &&
      typeof value.interactionId === "string" &&
      typeof value.state === "string" &&
      typeof value.revision === "number" &&
      Array.isArray(value.allowedActions) &&
      Array.isArray(value.forms);
  }


  function canSubmit(): boolean {
    return questionItems.length > 0 &&
      questionItems.every(item => answerForItem(item) !== null);
  }

  function answerForItem(item: AskUserQuestionItem): AskUserQuestionAnswer | null | undefined {
    if (item.kind === "single" || item.kind === "select" || item.kind === "treeSelect") {
      const selected = selectedOption[item.id] ?? "";
      if (!selected) return item.required ? null : undefined;
      return selectedOptionIsOther(item, selected)
        ? customAnswer[item.id]?.trim() || null
        : selectedOptionValue(item, selected);
    }
    if (item.kind === "multiple") {
      const selected = selectedOptions[item.id] ?? [];
      if (selected.length === 0) return item.required ? null : [];
      if (selected.some(option => selectedOptionIsOther(item, option)) && !customAnswer[item.id]?.trim()) return null;
      return selected.map(option =>
        selectedOptionIsOther(item, option)
          ? customAnswer[item.id].trim()
          : selectedOptionValue(item, option),
      );
    }
    if (item.kind === "date") {
      const answer = dateAnswer[item.id];
      return item.required && !answer ? null : answer;
    }
    if (item.kind === "text") {
      const answer = textAnswer[item.id]?.trim() ?? "";
      return item.required && !answer ? null : answer;
    }
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
    const seq = nextFieldAssistRunId(aiAssistSeq, item.id);
    aiAssistSeq[item.id] = seq;
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
      if (!isCurrentFieldAssistRun(aiAssistSeq, item.id, seq)) return;
      aiAssistWarning[item.id] =
        result.metadata.warnings?.[0]?.message ?? aiAssistWarning[item.id] ?? "";
      textAnswer[item.id] = result.value;
    } catch (cause) {
      if (!isCurrentFieldAssistRun(aiAssistSeq, item.id, seq)) return;
      textAnswer[item.id] = previousValue;
      aiAssistError[item.id] = toFieldAssistErrorMessage(cause);
    } finally {
      if (isCurrentFieldAssistRun(aiAssistSeq, item.id, seq)) {
        aiAssistLoading[item.id] = undefined;
      }
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
    if (!item.dataSource || (append && remoteLoading[item.id])) return;
    const requestSeq = (remoteRequestSeq[item.id] ?? 0) + 1;
    remoteRequestSeq[item.id] = requestSeq;
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
      if (remoteRequestSeq[item.id] !== requestSeq) return;
      const selected = (remoteOptions[item.id] ?? []).find(
        option => optionKey(option.id) === selectedOption[item.id],
      );
      remoteOptions[item.id] = mergeRemoteOptions(
        append ? [...(remoteOptions[item.id] ?? []), ...options] : selected ? [selected, ...options] : options,
      );
      remotePage[item.id] = page;
      remoteHasMore[item.id] =
        typeof response.total === "number"
          ? remoteOptions[item.id].length < response.total
          : options.length >= pageSize;
    } catch (cause) {
      if (remoteRequestSeq[item.id] === requestSeq) {
        remoteError[item.id] = cause instanceof Error ? cause.message : String(cause);
      }
    } finally {
      if (remoteRequestSeq[item.id] === requestSeq) remoteLoading[item.id] = false;
    }
  }

  function mergeRemoteOptions(
    options: NormalizedAskUserQuestionOption[],
  ): NormalizedAskUserQuestionOption[] {
    const byId = new Map<string, NormalizedAskUserQuestionOption>();
    for (const option of options) byId.set(optionKey(option.id), option);
    return [...byId.values()];
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

  const displayedConfirmationForms = $derived(
    request && !request.batch && request.kind === "confirm"
      ? confirmationFormsForDisplay(request, result)
      : [],
  );
  const sourceAnsweredMarkdown = $derived(
    request && request.batch && result?.status === "answered"
      ? t("questionTool.answered", {
          answer: askUserQuestionAnswerMarkdown(
            request,
            result.answer,
            {
              confirm: t("questionTool.confirm"),
              cancel: t("questionTool.cancel"),
            },
          ),
        })
      : "",
  );

  function confirmationFormsForDisplay(
    confirmationRequest: AskUserQuestionConfirmationCardRequest,
    confirmationResult: AskUserQuestionResult | null,
  ): Array<
    AskUserQuestionConfirmationForm & {
      items: ReturnType<typeof askUserQuestionAnswerItems>;
    }
  > {
    const forms = interaction?.forms.length
      ? interaction.forms
      : askUserQuestionConfirmationForms(confirmationRequest);
    return forms.map(form => {
      const answer =
        confirmationResult?.status === "confirmed"
          ? confirmationResult.forms.find(
              confirmedForm => confirmedForm.formId === form.formId,
            )?.answer ?? form.answer
          : form.answer;
      return {
        ...form,
        answer,
        items: askUserQuestionAnswerItems(
          { batch: true, title: form.title, questions: form.questions },
          answer,
          {
            confirm: t("questionTool.confirm"),
            cancel: t("questionTool.cancel"),
          },
        ),
      };
    });
  }

  function interactionStatusLabel(): string {
    if (interaction?.state === "confirmed" || result?.status === "confirmed") {
      return t("questionTool.confirmed");
    }
    if (interaction?.state === "cancelled" || result?.status === "cancelled") {
      return t("questionTool.interactionCancelled");
    }
    if (interaction?.state === "interrupted") {
      return t("questionTool.interactionInterrupted");
    }
    return t("questionTool.awaitingConfirmation");
  }
</script>

{#if request && showCard}
  <div class="question-card-anchor">
    <article
      bind:this={cardElement}
      class="question-card"
      class:inline-readonly-card={result?.status === "answered" ||
        result?.status === "confirmed" ||
        interaction?.state === "confirmed" ||
        interaction?.state === "cancelled" ||
        interaction?.state === "interrupted"}
      data-status={result?.status ?? "pending"}
      data-form-id={result?.status === "answered" ? result.formId : undefined}
      aria-live="polite"
      aria-label={t("questionTool.label")}
      aria-busy={pending && submitting}
    >
    {#if !request.batch && request.kind !== "confirm"}
      <div class="question-label">{t("questionTool.label")}</div>
    {/if}
    {#if request.batch && request.title}
      <h2 class="question-form-title">{request.title}</h2>
    {:else if !request.batch && request.kind !== "text" && request.kind !== "confirm"}
      <div class="question-text">
        <MarkdownRenderer content={askUserQuestionMarkdown(request.question)} />
      </div>
    {/if}

    {#if !request.batch && request.kind === "confirm" && !revising}
      <section class="desktop-question-result" aria-label={request.title}>
        <header class="submitted-header">
          <span class="submitted-status-icon" aria-hidden="true">
            <Check size={22} />
          </span>
          <div>
            <h3>{request.title}</h3>
            <p>{t("questionTool.confirmDescription")}</p>
          </div>
        </header>
        <div class="question-form-scroll-region">
          <div class="question-form-content confirmation-form-list">
            {#each displayedConfirmationForms as form (form.formId)}
              <div class="confirmation-form" data-form-id={form.formId}>
                <h4>{form.title}</h4>
                <div class="submitted-fields">
                  {#each form.items as item (item.id)}
                    <div class="submitted-field">
                      <div class="submitted-field-label">
                        <QuestionFieldLabel kind={item.kind} label={item.label} />
                      </div>
                      <SubmittedAnswerValue value={item.value} />
                    </div>
                  {/each}
                </div>
              </div>
            {/each}
          </div>
        </div>
      </section>
      <div class="question-actions">
        {#if interaction?.allowedActions.includes("cancel")}
          <button type="button" class="question-button secondary" disabled={submitting} onclick={() => void respond({ cancelled: true })}>
            {t("questionTool.cancel")}
          </button>
        {/if}
        {#if interaction?.allowedActions.includes("return_modify")}
          <button type="button" class="question-button secondary" disabled={submitting} onclick={() => void startRevision()}>
            {t("questionTool.returnModify")}
          </button>
        {/if}
        {#if interaction?.allowedActions.includes("confirm")}
          <button type="button" class="question-button" disabled={submitting} onclick={() => void respond({ cancelled: false, answer: true })}>
            {t("questionTool.confirm")}
          </button>
        {/if}
        {#if interaction && interaction.allowedActions.length === 0}
          <button type="button" class="question-button" disabled>
            {interactionStatusLabel()}
          </button>
        {/if}
      </div>
    {:else if result?.status === "cancelled"}
      <div class="question-result muted">{t("questionTool.cancelled")}</div>
    {:else if interrupted}
      <div class="question-result muted">{t("questionTool.interrupted")}</div>
    {:else if !pending && !revising && result?.status !== "answered"}
      <div class="question-error" role="alert">{block.resultText}</div>
    {:else}
      {#if result?.status === "answered"}
        <div class="mobile-answered-result question-result question-form-scroll-region">
          <div class="question-form-content">
            <MarkdownRenderer content={sourceAnsweredMarkdown} />
          </div>
        </div>
      {/if}
      <form onsubmit={submit} class:answered-source-form={result?.status === "answered"}>
        {#if revising}
          <h2 class="question-form-title">{t("questionTool.modify")}</h2>
        {/if}
        <div class="question-form-scroll-region">
          <div class="question-form-content">
            {#each questionItems as item}
              <div
                class:question-group={request.batch}
                class:single-line-text-field={item.kind === "text" && item.inputType !== "textarea"}
              >
                {#if item.revisionTitle}
                  <h3 class="revision-form-title">{item.revisionTitle}</h3>
                {/if}
                {#if (request.batch || revising) && item.kind !== "text"}
                  <div class="question-text">
                    <QuestionFieldLabel kind={item.kind} label={askUserQuestionMarkdown(item.question)} />
                  </div>
                {/if}

            {#if item.kind === "single"}
              <fieldset class="single-options" disabled={!formEnabled || submitting}>
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
                <QuestionRemoteCombobox
                  id={`question-${block.toolCallId}-${item.id}`}
                  label={item.question}
                  value={selectedOption[item.id] ?? ""}
                  options={itemOptions(item).map(option => ({
                    key: optionKey(option.id),
                    label: option.label,
                  }))}
                  disabled={!formEnabled || submitting}
                  loading={Boolean(remoteLoading[item.id])}
                  error={Boolean(remoteError[item.id])}
                  hasMore={Boolean(remoteHasMore[item.id])}
                  placeholder={t("questionTool.selectPlaceholder")}
                  searchPlaceholder={t("questionTool.searchPlaceholder")}
                  loadingLabel={t("questionTool.loading")}
                  emptyLabel={t("questionTool.remoteEmpty")}
                  errorLabel={t("questionTool.remoteError")}
                  retryLabel={t("questionTool.retry")}
                  clearLabel={t("questionTool.clearSelection")}
                  loadMoreLabel={t("questionTool.loadMore")}
                  onValueChange={(value) => selectedOption[item.id] = value}
                  onSearch={(search) => {
                    remoteSearch[item.id] = search;
                    void loadRemoteOptions(item, 1, search, false);
                  }}
                  onLoadMore={() => void loadRemoteOptions(
                    item,
                    (remotePage[item.id] ?? 1) + 1,
                    remoteSearch[item.id] ?? "",
                    true,
                  )}
                />
              {:else}
                <label class="sr-only" for={`question-${block.toolCallId}-${item.id}`}>{item.question}</label>
                <div class="question-select-control">
                  <select
                    id={`question-${block.toolCallId}-${item.id}`}
                    class="question-input"
                    bind:value={selectedOption[item.id]}
                    disabled={!formEnabled || submitting}
                  >
                    <option value="">{t("questionTool.selectPlaceholder")}</option>
                    {#each itemOptions(item) as option}
                      <option value={optionKey(option.id)}>{option.label}</option>
                    {/each}
                  </select>
                  <ChevronDown size={16} aria-hidden="true" />
                </div>
              {/if}
            {:else if item.kind === "multiple"}
              {#if item.dataSource}
                <div class="question-remote-row">
                  <input
                    class="question-input"
                    type="search"
                    bind:value={remoteSearch[item.id]}
                    disabled={!formEnabled || submitting || remoteLoading[item.id]}
                    placeholder={t("questionTool.searchPlaceholder")}
                    onkeydown={preventEnterSubmit}
                  />
                  <button
                    type="button"
                    class="question-button secondary"
                    disabled={!formEnabled || submitting || remoteLoading[item.id]}
                    onclick={() => void loadRemoteOptions(item, 1, remoteSearch[item.id] ?? "", false)}
                  >
                    {t("questionTool.search")}
                  </button>
                </div>
              {/if}
              <fieldset disabled={!formEnabled || submitting}>
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
                  class="question-button secondary load-more"
                  disabled={!formEnabled || submitting || remoteLoading[item.id]}
                  onclick={() => void loadRemoteOptions(item, (remotePage[item.id] ?? 1) + 1, remoteSearch[item.id] ?? "", true)}
                >
                  {remoteLoading[item.id] ? t("questionTool.loading") : t("questionTool.loadMore")}
                </button>
              {/if}
            {:else if item.kind === "text"}
              <div class="question-field-header">
                <div class="question-text">
                  {#if request.batch || revising}
                    <QuestionFieldLabel kind={item.kind} label={askUserQuestionMarkdown(item.question)} />
                  {:else}
                    <MarkdownRenderer content={askUserQuestionMarkdown(item.question)} />
                  {/if}
                </div>
                {#if item.fieldAssist}
                  <div class="question-ai-actions" aria-label={`${t("questionTool.aiAssistRegenerate")} / ${t("questionTool.aiAssistPolish")}`}>
                    <button
                      type="button"
                      class="question-button secondary icon-button"
                      disabled={!formEnabled || submitting || Boolean(aiAssistLoading[item.id])}
                      onclick={() => void runFieldAssist(item, "regenerate")}
                      aria-label={aiAssistLoading[item.id] === "regenerate" ? t("questionTool.aiAssistGenerating") : t("questionTool.aiAssistRegenerate")}
                      title={aiAssistLoading[item.id] === "regenerate" ? t("questionTool.aiAssistGenerating") : t("questionTool.aiAssistRegenerate")}
                      data-tooltip={aiAssistLoading[item.id] === "regenerate" ? t("questionTool.aiAssistGenerating") : t("questionTool.aiAssistRegenerate")}
                    >
                      <RefreshCw size={16} aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      class="question-button secondary icon-button"
                      disabled={!formEnabled || submitting || Boolean(aiAssistLoading[item.id]) || !textAnswer[item.id]?.trim()}
                      onclick={() => void runFieldAssist(item, "polish")}
                      aria-label={aiAssistLoading[item.id] === "polish" ? t("questionTool.aiAssistPolishing") : t("questionTool.aiAssistPolish")}
                      title={aiAssistLoading[item.id] === "polish" ? t("questionTool.aiAssistPolishing") : t("questionTool.aiAssistPolish")}
                      data-tooltip={aiAssistLoading[item.id] === "polish" ? t("questionTool.aiAssistPolishing") : t("questionTool.aiAssistPolish")}
                    >
                      <Sparkle size={16} aria-hidden="true" />
                    </button>
                  </div>
                {/if}
              </div>
              <label class="sr-only" for={`question-${block.toolCallId}-${item.id}`}>{item.question}</label>
              <div
                class="question-input-wrap"
                class:single-line={item.inputType !== "textarea"}
                class:loading={Boolean(aiAssistLoading[item.id])}
              >
                {#if item.inputType === "textarea"}
                  <textarea
                    id={`question-${block.toolCallId}-${item.id}`}
                    class="question-input question-textarea"
                    rows="4"
                    bind:value={textAnswer[item.id]}
                    disabled={!formEnabled || submitting}
                    readonly={Boolean(aiAssistLoading[item.id])}
                    placeholder={t("questionTool.inputPlaceholder")}
                  ></textarea>
                {:else}
                  <input
                    id={`question-${block.toolCallId}-${item.id}`}
                    class="question-input"
                    type="text"
                    bind:value={textAnswer[item.id]}
                    disabled={!formEnabled || submitting}
                    readonly={Boolean(aiAssistLoading[item.id])}
                    placeholder={t("questionTool.inputPlaceholder")}
                    onkeydown={preventEnterSubmit}
                  />
                {/if}
                {#if aiAssistLoading[item.id]}
                  <span class="question-input-spinner" aria-hidden="true"></span>
                {/if}
              </div>
              {#if aiAssistWarning[item.id]}
                <div class="question-warning" role="status">{aiAssistWarning[item.id]}</div>
              {/if}
              {#if aiAssistError[item.id]}
                <div class="question-error" role="alert">{aiAssistError[item.id]}</div>
              {/if}
            {:else if item.kind === "date"}
              <label class="sr-only" for={`question-${block.toolCallId}-${item.id}-trigger`}>{item.question}</label>
              <QuestionDateField
                id={`question-${block.toolCallId}-${item.id}`}
                value={dateAnswer[item.id]}
                dateFormat={item.dateFormat}
                required={item.required}
                disabled={!formEnabled || submitting}
                placeholder={item.dateFormat}
                onValueChange={(value) => {
                  dateAnswer[item.id] = value;
                }}
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
                disabled={!formEnabled || submitting}
                placeholder={t("questionTool.otherPlaceholder")}
                onkeydown={preventEnterSubmit}
              />
            {/if}
              </div>
            {/each}
          </div>
        </div>

        <div class="question-actions">
          {#if revising && interaction}
            {#if onCancelRevision && interaction.allowedActions.includes("cancel_revision")}
              <button type="button" class="question-button secondary" disabled={submitting} onclick={() => void cancelRevision()}>
                {t("questionTool.cancel")}
              </button>
            {/if}
            {#if interaction.allowedActions.includes("submit_revision")}
              <button type="submit" class="question-button" disabled={submitting || !canSubmit()}>
                {t("questionTool.saveAndReturn")}
              </button>
            {/if}
          {:else if pending}
            <button type="button" class="question-button secondary" disabled={submitting} onclick={() => void respond({ cancelled: true })}>
              {t("questionTool.cancel")}
            </button>
            <button type="submit" class="question-button" disabled={submitting || !canSubmit()}>
              {t("questionTool.submit")}
            </button>
          {:else if result?.status === "answered"}
            <button type="button" class="question-button" disabled>
              {interaction ? interactionStatusLabel() : t("questionTool.submitted")}
            </button>
          {/if}
        </div>
      </form>
    {/if}

    {#if error}<div class="question-error" role="alert">{error}</div>{/if}
    </article>
  </div>
{/if}

<style>
  .question-card-anchor {
    box-sizing: border-box;
    width: 100%;
    margin: 1rem 0;
  }

  .question-card {
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    gap: 12px;
    width: 100%;
    margin: 0;
    padding: 16px;
    border-radius: 14px;
    background: var(--panel);
    box-shadow: var(--shadow-raised);
  }

  .question-card:global(.center-focused-card) {
    overflow: hidden;
  }

  .question-label {
    color: var(--text-subtle);
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.08em;
  }

  .question-form-title {
    margin: 0;
    color: var(--text);
    font-size: 1rem;
    font-weight: 700;
    line-height: 1.4;
  }

  .revision-form-title {
    margin: 0 0 2px;
    color: var(--text);
    font-size: 0.92rem;
    font-weight: 700;
  }

  .question-text {
    min-width: 0;
    color: var(--text);
    font-size: 0.92rem;
    font-weight: normal;
    line-height: 1.5;
  }

  form { display: flex; flex-direction: column; gap: 12px; min-height: 0; }
  fieldset { display: grid; gap: 8px; margin: 0; padding: 0; border: 0; }

  .question-form-scroll-region {
    box-sizing: border-box;
    width: 100%;
    min-height: 0;
  }

  .question-form-content {
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    gap: 12px;
    width: 100%;
    max-width: 900px;
    margin-inline: auto;
  }

  .question-card:global(.center-focused-card) form,
  .question-card:global(.center-focused-card) .desktop-question-result {
    flex: 1 1 auto;
  }

  .question-card:global(.center-focused-card) .question-form-scroll-region {
    flex: 1 1 auto;
    width: calc(100% + 32px);
    max-height: none;
    margin-inline: -16px;
    padding-inline: 16px;
    overflow-y: auto;
    overscroll-behavior: contain;
    scrollbar-gutter: stable;
  }

  .question-card.inline-readonly-card .question-form-scroll-region {
    max-height: 420px;
    overflow-y: auto;
    scrollbar-gutter: stable;
  }

  .single-options {
    display: flex;
    flex-wrap: wrap;
    column-gap: 24px;
  }

  .question-group {
    display: grid;
    gap: 10px;
    min-width: 0;
    margin-bottom: 1rem;
  }

  .single-line-text-field {
    width: 100%;
    max-width: 600px;
  }

  .question-option {
    display: flex;
    align-items: center;
    gap: 10px;
    color: var(--text);
    cursor: pointer;
    transition: color 0.12s ease;
  }

  .question-option:hover,
  .question-option:has(input:checked) {
    color: var(--accent);
  }

  .question-option input { accent-color: var(--accent); }

  .question-option input[type="checkbox"] {
    appearance: none;
    -webkit-appearance: none;
    flex: 0 0 auto;
    display: grid;
    place-content: center;
    width: 16px;
    height: 16px;
    margin: 0;
    border: 1.5px solid var(--border-strong);
    border-radius: 3px;
    background: transparent;
    transition:
      border-color 0.12s ease,
      background-color 0.12s ease;
  }

  .question-option input[type="checkbox"]::before {
    content: "";
    width: 8px;
    height: 4px;
    border-bottom: 2px solid var(--on-accent);
    border-left: 2px solid var(--on-accent);
    opacity: 0;
    transform: translateY(-1px) rotate(-45deg) scale(0.75);
    transition:
      opacity 0.12s ease,
      transform 0.12s ease;
  }

  .question-option input[type="checkbox"]:checked {
    border-color: var(--accent);
    background: var(--accent);
  }

  .question-option input[type="checkbox"]:checked::before {
    opacity: 1;
    transform: translateY(-1px) rotate(-45deg) scale(1);
  }

  .question-option input[type="radio"] {
    appearance: none;
    -webkit-appearance: none;
    flex: 0 0 auto;
    width: 16px;
    height: 16px;
    margin: 0;
    border: 2px solid var(--border-strong);
    border-radius: 999px;
    background: var(--control-bg);
    transition:
      border-color 0.12s ease,
      background 0.12s ease;
  }

  .question-option input[type="radio"]:checked {
    border-color: var(--accent);
    background:
      radial-gradient(circle, var(--accent) 0 38%, transparent 42%),
      var(--control-bg);
  }

  .question-remote-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 8px;
  }

  .question-input-wrap {
    position: relative;
  }

  .question-input-wrap.single-line {
    width: 100%;
  }

  .question-input-wrap.loading .question-input {
    padding-right: calc(1em + 28px);
  }

  .question-input-spinner {
    position: absolute;
    top: 50%;
    right: 1em;
    width: 16px;
    height: 16px;
    margin-top: -8px;
    border: 2px solid color-mix(in srgb, var(--accent) 24%, transparent);
    border-top-color: var(--accent);
    border-radius: 999px;
    animation: question-input-spin 0.75s linear infinite;
    pointer-events: none;
  }

  .question-textarea + .question-input-spinner {
    top: 13px;
    margin-top: 0;
  }

  @keyframes question-input-spin {
    to { transform: rotate(360deg); }
  }

  .question-textarea {
    min-height: 96px;
    resize: vertical;
  }

  .question-actions {
    display: flex;
    flex: 0 0 auto;
    justify-content: flex-end;
    gap: 8px;
  }

  .question-actions .question-button {
    padding: 6px 26px;
    font-weight: normal;
  }

  .question-field-header {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    gap: 12px;
  }

  .question-field-header .question-text {
    min-width: 0;
  }

  .question-ai-actions {
    display: flex;
    justify-content: flex-end;
    gap: 6px;
  }

  .question-button.icon-button {
    position: relative;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 40px;
    height: 40px;
    padding: 0;
    border: 0;
    border-radius: 10px;
    background: transparent;
    color: var(--text-subtle);
    transition:
      background-color 0.12s ease,
      color 0.12s ease,
      transform 0.12s ease;
  }

  .question-button.icon-button::after {
    position: absolute;
    right: 0;
    bottom: calc(100% + 6px);
    z-index: 2;
    display: none;
    padding: 5px 7px;
    border-radius: 6px;
    background: color-mix(in srgb, var(--panel-3) 84%, var(--accent));
    color: var(--text);
    content: attr(data-tooltip);
    font-size: 0.72rem;
    font-weight: 600;
    line-height: 1;
    box-shadow: 0 8px 18px color-mix(in srgb, var(--accent) 18%, transparent);
    white-space: nowrap;
    pointer-events: none;
  }

  .question-button.icon-button:hover::after,
  .question-button.icon-button:focus-visible::after {
    display: block;
  }

  .load-more { justify-self: start; }

  .question-button.secondary.icon-button {
    border: 0;
    background: transparent;
    color: var(--text-subtle);
  }

  .question-button.secondary.icon-button:hover,
  .question-button.secondary.icon-button:focus-visible {
    background: transparent;
    color: var(--accent);
  }

  .question-button.secondary.icon-button:active:not(:disabled) {
    background: transparent;
    color: var(--accent-hover);
    transform: scale(0.96);
  }

  .question-result { color: var(--text); }
  .mobile-answered-result { display: none; }
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

  .desktop-question-result {
    display: flex;
    flex-direction: column;
    gap: 24px;
    min-height: 0;
  }

  .confirmation-form-list { gap: 24px; }

  .submitted-header {
    display: flex;
    align-items: flex-start;
    gap: 14px;
  }

  .submitted-status-icon {
    display: inline-flex;
    flex: 0 0 auto;
    align-items: center;
    justify-content: center;
    width: 40px;
    height: 40px;
    border-radius: 999px;
    background: var(--accent);
    color: var(--on-accent);
  }

  .submitted-header h3,
  .submitted-header p {
    margin: 0;
  }

  .submitted-header h3 {
    color: var(--text);
    font-size: 1.1rem;
    line-height: 1.4;
    text-wrap: balance;
  }

  .submitted-header p {
    margin-top: 4px;
    color: var(--text-muted);
    font-size: 0.84rem;
    line-height: 1.5;
    text-wrap: pretty;
  }

  .confirmation-form {
    display: grid;
    gap: 12px;
  }

  .confirmation-form h4 {
    margin: 0;
    color: var(--text);
    font-size: 0.94rem;
    line-height: 1.4;
  }

  .submitted-fields {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 18px 28px;
  }

  .submitted-field {
    min-width: 0;
  }

  .submitted-field-label {
    min-width: 0;
    margin-bottom: 7px;
    color: var(--text-subtle);
    font-size: 0.84rem;
    font-weight: normal;
  }

  @media (min-width: 641px) {
    .submitted-fields {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }

  @media (max-width: 640px) {
    .answered-source-form { display: none; }
    .mobile-answered-result { display: block; }
  }
</style>
