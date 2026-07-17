import type {
  AskUserQuestionConfirmationCardRequest,
  AskUserQuestionConfirmationForm,
} from "@dano/types/protocol";
import { askUserQuestionConfirmationForms } from "../utils/askUserQuestion";

let editingConfirmationId = $state("");
let editingSourceToolCallId = $state("");
type ConfirmationRecord = {
  request: AskUserQuestionConfirmationCardRequest;
  form: AskUserQuestionConfirmationForm;
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
    for (const form of askUserQuestionConfirmationForms(request)) {
      confirmations[form.formId] = {
        request,
        form,
        confirmationId: confirmationToolCallId,
        status: confirmed ? "confirmed" : cancelled ? "cancelled" : "pending",
      };
    }
  },
  request(request: AskUserQuestionConfirmationCardRequest) {
    const firstForm = askUserQuestionConfirmationForms(request)[0];
    return (firstForm ? confirmations[firstForm.formId]?.request : undefined) ?? request;
  },
  sourceAnswer(toolCallId: string) {
    return confirmations[toolCallId]?.form.answer;
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
    const form = askUserQuestionConfirmationForms(request)[0];
    if (!form) return;
    confirmations[form.formId] = {
      request,
      form,
      confirmationId: confirmationToolCallId,
      status: "pending",
    };
    editingConfirmationId = confirmationToolCallId;
    editingSourceToolCallId = form.formId;
  },
  finishEditing(request: AskUserQuestionConfirmationCardRequest) {
    const form = askUserQuestionConfirmationForms(request)[0];
    if (!form) return;
    const current = confirmations[form.formId];
    confirmations[form.formId] = {
      request,
      form,
      confirmationId: current?.confirmationId ?? editingConfirmationId,
      status: "pending",
    };
    editingConfirmationId = "";
    editingSourceToolCallId = "";
  },
};
