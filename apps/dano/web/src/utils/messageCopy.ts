import { messageContent, type TranscriptEntryLike } from "./transcript";

function normalizeSelectionText(text: string): string {
  return text.replace(/\r\n?/g, "\n").trim();
}

export function userMessagePlainText(msg: TranscriptEntryLike): string | null {
  const text = messageContent(msg);
  return msg.role === "user" && text ? text : null;
}

export function userMessageCopyText(
  msg: TranscriptEntryLike,
  selectedText: string,
  renderedText: string,
): string | null {
  const plainText = userMessagePlainText(msg);
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

export async function copyTextToClipboard(text: string): Promise<boolean> {
  if (!text) return false;

  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Fall back to the older DOM copy path below.
    }
  }

  if (typeof document === "undefined") return false;

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.append(textarea);
  textarea.select();
  try {
    return document.execCommand("copy");
  } finally {
    textarea.remove();
  }
}
