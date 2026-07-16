import type {
  AskUserQuestionConfirmationCardRequest,
} from "@dano/types/protocol";

let editingConfirmationId = $state("");
let editingSourceToolCallId = $state("");
let requestOverrides = $state<Record<string, AskUserQuestionConfirmationCardRequest>>({});
let confirmationIds = $state<Record<string, string>>({});
let confirmedSources = $state<Record<string, boolean>>({});
let cancelledSources = $state<Record<string, boolean>>({});

export const questionConfirmationState = {
  sync(
    request: AskUserQuestionConfirmationCardRequest,
    confirmationToolCallId: string,
    confirmed = false,
    cancelled = false,
  ) {
    requestOverrides[request.confirmationOfToolCallId] = request;
    confirmationIds[request.confirmationOfToolCallId] = confirmationToolCallId;
    if (confirmed) confirmedSources[request.confirmationOfToolCallId] = true;
    if (cancelled) cancelledSources[request.confirmationOfToolCallId] = true;
  },
  request(request: AskUserQuestionConfirmationCardRequest) {
    return requestOverrides[request.confirmationOfToolCallId] ?? request;
  },
  sourceAnswer(toolCallId: string) {
    return requestOverrides[toolCallId]?.answer;
  },
  isEditing(toolCallId: string) {
    return editingSourceToolCallId === toolCallId;
  },
  confirmationId(toolCallId: string) {
    return editingSourceToolCallId === toolCallId ? editingConfirmationId : "";
  },
  linkedConfirmationId(toolCallId: string) {
    return confirmationIds[toolCallId] ?? "";
  },
  isConfirmed(toolCallId: string) {
    return Boolean(confirmedSources[toolCallId]);
  },
  isCancelled(toolCallId: string) {
    return Boolean(cancelledSources[toolCallId]);
  },
  startEditing(
    confirmationToolCallId: string,
    request: AskUserQuestionConfirmationCardRequest,
  ) {
    requestOverrides[request.confirmationOfToolCallId] = request;
    confirmationIds[request.confirmationOfToolCallId] = confirmationToolCallId;
    editingConfirmationId = confirmationToolCallId;
    editingSourceToolCallId = request.confirmationOfToolCallId;
  },
  finishEditing(request: AskUserQuestionConfirmationCardRequest) {
    requestOverrides[request.confirmationOfToolCallId] = request;
    editingConfirmationId = "";
    editingSourceToolCallId = "";
  },
};
