import { AskUserQuestionCoordinator } from "../ask-user-question.js";

export async function submitTestForms(
  coordinator: AskUserQuestionCoordinator,
  signal: AbortSignal,
  formIds: readonly string[],
): Promise<void> {
  for (const formId of formIds) {
    const form = coordinator.wait(
      formId,
      {
        title: formId,
        questions: [{ id: "value", question: "值？", default: formId }],
      },
      signal,
    );
    coordinator.present(formId);
    coordinator.answer(formId, {
      cancelled: false,
      answer: { value: formId },
    });
    await form;
  }
}
