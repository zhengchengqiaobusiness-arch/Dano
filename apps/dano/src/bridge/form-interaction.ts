import type { SessionManager } from "@earendil-works/pi-coding-agent";
import type {
  AskUserQuestionAnswer,
  AskUserQuestionConfirmationForm,
  FormInteractionProjection,
  FormInteractionState,
  RpcTranscriptMessage,
} from "./types.js";

export const FORM_INTERACTION_CUSTOM_TYPE = "dano.form-interaction.v1";

export type FormInteractionForm = AskUserQuestionConfirmationForm & {
  revision: number;
};

export type FormInteractionSnapshot = {
  schemaVersion: 1;
  interactionId: string;
  assistantTurnId: string;
  state: FormInteractionState;
  revision: number;
  forms: FormInteractionForm[];
};

export type FormInteractionTransition =
  | { type: "confirm" }
  | { type: "cancel" }
  | { type: "interrupt" };

export type FormInteractionTransitionResult =
  | { kind: "transitioned"; snapshot: FormInteractionSnapshot }
  | { kind: "already_terminal"; snapshot: FormInteractionSnapshot };

export function createFormInteraction(
  sessionManager: SessionManager,
  input: {
    interactionId: string;
    assistantTurnId: string;
    forms: AskUserQuestionConfirmationForm[];
  },
): FormInteractionSnapshot {
  const existing = readFormInteractions(sessionManager.getBranch()).get(
    input.interactionId,
  );
  if (existing) return existing;

  const snapshot: FormInteractionSnapshot = {
    schemaVersion: 1,
    interactionId: input.interactionId,
    assistantTurnId: input.assistantTurnId,
    state: "awaiting_confirmation",
    revision: 1,
    forms: input.forms.map(form => ({
      ...form,
      questions: [...form.questions],
      answer: { ...form.answer },
      revision: 1,
    })),
  };
  appendSnapshot(sessionManager, snapshot);
  return snapshot;
}

export function transitionFormInteraction(
  sessionManager: SessionManager,
  interactionId: string,
  transition: FormInteractionTransition,
): FormInteractionTransitionResult {
  const current = readFormInteractions(sessionManager.getBranch()).get(
    interactionId,
  );
  if (!current) {
    throw new Error(`Form Interaction not found: ${interactionId}`);
  }
  const reduced = reduceFormInteraction(current, transition);
  if (reduced.kind === "transitioned") {
    appendSnapshot(sessionManager, reduced.snapshot);
  }
  return reduced;
}

export function reduceFormInteraction(
  current: FormInteractionSnapshot,
  transition: FormInteractionTransition,
): FormInteractionTransitionResult {
  if (current.state !== "awaiting_confirmation") {
    return { kind: "already_terminal", snapshot: current };
  }
  const state: FormInteractionState =
    transition.type === "confirm"
      ? "confirmed"
      : transition.type === "cancel"
        ? "cancelled"
        : "interrupted";
  return {
    kind: "transitioned",
    snapshot: {
      ...current,
      state,
      revision: current.revision + 1,
    },
  };
}

export function interruptAwaitingFormInteractions(
  sessionManager: SessionManager,
): FormInteractionSnapshot[] {
  const interrupted: FormInteractionSnapshot[] = [];
  for (const interaction of readFormInteractions(
    sessionManager.getBranch(),
  ).values()) {
    if (interaction.state !== "awaiting_confirmation") continue;
    const result = transitionFormInteraction(
      sessionManager,
      interaction.interactionId,
      { type: "interrupt" },
    );
    if (result.kind === "transitioned") {
      interrupted.push(result.snapshot);
    }
  }
  return interrupted;
}

export function readFormInteractions(
  entries: readonly unknown[],
): Map<string, FormInteractionSnapshot> {
  const interactions = new Map<string, FormInteractionSnapshot>();
  for (const entry of entries) {
    if (!isFormInteractionEntry(entry)) continue;
    const current = interactions.get(entry.data.interactionId);
    if (!current || entry.data.revision > current.revision) {
      interactions.set(entry.data.interactionId, entry.data);
    }
  }
  return interactions;
}

export function projectFormInteraction(
  snapshot: FormInteractionSnapshot,
): FormInteractionProjection {
  return {
    interactionId: snapshot.interactionId,
    state: snapshot.state,
    revision: snapshot.revision,
    allowedActions:
      snapshot.state === "awaiting_confirmation"
        ? ["cancel", "confirm"]
        : [],
  };
}

export function projectFormInteractionsInMessage(
  message: RpcTranscriptMessage,
  interactions: ReadonlyMap<string, FormInteractionSnapshot>,
): RpcTranscriptMessage {
  if (!Array.isArray(message.content) || interactions.size === 0) return message;
  const byFormId = new Map<string, FormInteractionSnapshot>();
  for (const interaction of interactions.values()) {
    for (const form of interaction.forms) {
      byFormId.set(form.formId, interaction);
    }
  }
  let changed = false;
  const content = message.content.map(block => {
    if (
      typeof block === "string" ||
      block.type !== "toolCall" ||
      !block.id
    ) {
      return block;
    }
    const interaction = interactions.get(block.id) ?? byFormId.get(block.id);
    if (!interaction) return block;
    changed = true;
    return {
      ...block,
      formInteraction: projectFormInteraction(interaction),
    };
  });
  return changed ? { ...message, content } : message;
}

export function createFormInteractionForQuestion(
  sessionManager: SessionManager,
  interactionId: string,
  request: {
    title: string;
    confirmationOfToolCallId: string;
    questions: AskUserQuestionConfirmationForm["questions"];
    answer: AskUserQuestionConfirmationForm["answer"];
    forms?: AskUserQuestionConfirmationForm[];
  },
): FormInteractionSnapshot {
  const forms = request.forms?.length
    ? request.forms
    : [
        {
          formId: request.confirmationOfToolCallId,
          title: request.title,
          questions: request.questions,
          answer: request.answer,
        },
      ];
  return createFormInteraction(sessionManager, {
    interactionId,
    assistantTurnId:
      assistantTurnIdForToolCall(sessionManager.getBranch(), interactionId) ??
      interactionId,
    forms,
  });
}

function assistantTurnIdForToolCall(
  entries: readonly unknown[],
  toolCallId: string,
): string | undefined {
  for (let index = entries.length - 1; index >= 0; index -= 1) {
    const entry = entries[index];
    if (!isRecord(entry) || entry.type !== "message") continue;
    if (typeof entry.id !== "string" || !isRecord(entry.message)) continue;
    if (entry.message.role !== "assistant" || !Array.isArray(entry.message.content)) {
      continue;
    }
    const ownsToolCall = entry.message.content.some(block =>
      isRecord(block) && block.type === "toolCall" && block.id === toolCallId,
    );
    if (ownsToolCall) return entry.id;
  }
  return undefined;
}

function appendSnapshot(
  sessionManager: SessionManager,
  snapshot: FormInteractionSnapshot,
): void {
  sessionManager.appendCustomEntry(FORM_INTERACTION_CUSTOM_TYPE, snapshot);
}

function isFormInteractionEntry(
  value: unknown,
): value is { data: FormInteractionSnapshot } {
  if (!isRecord(value) || value.type !== "custom") return false;
  if (value.customType !== FORM_INTERACTION_CUSTOM_TYPE) return false;
  return isFormInteractionSnapshot(value.data);
}

function isFormInteractionSnapshot(
  value: unknown,
): value is FormInteractionSnapshot {
  if (!isRecord(value)) return false;
  if (
    value.schemaVersion !== 1 ||
    typeof value.interactionId !== "string" ||
    typeof value.assistantTurnId !== "string" ||
    !isFormInteractionState(value.state) ||
    !Number.isInteger(value.revision) ||
    (value.revision as number) < 1 ||
    !Array.isArray(value.forms)
  ) {
    return false;
  }
  return value.forms.every(isFormInteractionForm);
}

function isFormInteractionForm(value: unknown): value is FormInteractionForm {
  if (!isRecord(value)) return false;
  return (
    typeof value.formId === "string" &&
    typeof value.title === "string" &&
    Array.isArray(value.questions) &&
    isAnswerRecord(value.answer) &&
    Number.isInteger(value.revision) &&
    (value.revision as number) >= 1
  );
}

function isFormInteractionState(value: unknown): value is FormInteractionState {
  return (
    value === "awaiting_confirmation" ||
    value === "confirmed" ||
    value === "cancelled" ||
    value === "interrupted"
  );
}

function isAnswerRecord(
  value: unknown,
): value is Record<string, AskUserQuestionAnswer> {
  return isRecord(value) && !Array.isArray(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
