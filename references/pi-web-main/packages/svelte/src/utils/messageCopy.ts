import { messageContent, type TranscriptEntryLike } from "./transcript";

function normalizeSelectionText(text: string): string {
  return text.replace(/\r\n?/g, "\n").trim();
}

export function userMessageCopyText(
  msg: TranscriptEntryLike,
  selectedText: string,
  renderedText: string,
): string | null {
  if (msg.role !== "user") return null;

  const plainText = messageContent(msg);
  if (!plainText) return null;

  const normalizedSelected = normalizeSelectionText(selectedText);
  if (!normalizedSelected) return null;

  const normalizedPlain = normalizeSelectionText(plainText);
  const normalizedRendered = normalizeSelectionText(renderedText);

  if (
    normalizedSelected !== normalizedPlain &&
    normalizedSelected !== normalizedRendered
  ) {
    return null;
  }

  return plainText;
}
