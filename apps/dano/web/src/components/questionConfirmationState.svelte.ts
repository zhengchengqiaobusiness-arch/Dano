import type {
  AskUserQuestionConfirmationCardRequest,
} from "@dano/types/protocol";

let editingConfirmationId = $state("");
let editingSourceToolCallId = $state("");
type ConfirmationRecord = {
  request: AskUserQuestionConfirmationCardRequest;
  confirmationId: string;
  status: "pending" | "confirmed" | "cancelled";
};

let confirmations = $state<Record<string, ConfirmationRecord>>({});

export const questionConfirmationState = {
  sync(
    request: AskUserQuestionConfirmationCardRequest,
    confirmationToolCallId: string,
    confirmed = false,
    cancelled = false,
  ) {
    confirmations[request.confirmationOfToolCallId] = {
      request,
      confirmationId: confirmationToolCallId,
      status: confirmed ? "confirmed" : cancelled ? "cancelled" : "pending",
    };
  },
  request(request: AskUserQuestionConfirmationCardRequest) {
    return confirmations[request.confirmationOfToolCallId]?.request ?? request;
  },
  sourceAnswer(toolCallId: string) {
    return confirmations[toolCallId]?.request.answer;
  },
  isEditing(toolCallId: string) {
    return editingSourceToolCallId === toolCallId;
  },
  confirmationId(toolCallId: string) {
    return editingSourceToolCallId === toolCallId ? editingConfirmationId : "";
  },
  linkedConfirmationId(toolCallId: string) {
    return confirmations[toolCallId]?.confirmationId ?? "";
  },
  isConfirmed(toolCallId: string) {
    return confirmations[toolCallId]?.status === "confirmed";
  },
  isCancelled(toolCallId: string) {
    return confirmations[toolCallId]?.status === "cancelled";
  },
  startEditing(
    confirmationToolCallId: string,
    request: AskUserQuestionConfirmationCardRequest,
  ) {
    confirmations[request.confirmationOfToolCallId] = {
      request,
      confirmationId: confirmationToolCallId,
      status: "pending",
    };
    editingConfirmationId = confirmationToolCallId;
    editingSourceToolCallId = request.confirmationOfToolCallId;
  },
  finishEditing(request: AskUserQuestionConfirmationCardRequest) {
    const current = confirmations[request.confirmationOfToolCallId];
    confirmations[request.confirmationOfToolCallId] = {
      request,
      confirmationId: current?.confirmationId ?? editingConfirmationId,
      status: "pending",
    };
    editingConfirmationId = "";
    editingSourceToolCallId = "";
  },
};
