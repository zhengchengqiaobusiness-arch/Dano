export type TranscriptToolIconName =
  | "code-xml"
  | "book-open-text"
  | "file-pen-line"
  | "pen-line";

const TRANSCRIPT_TOOL_ICONS: Readonly<Record<string, TranscriptToolIconName>> = {
  bash: "code-xml",
  read: "book-open-text",
  write: "file-pen-line",
  edit: "pen-line",
};

export function transcriptToolIconName(
  toolName: string | undefined,
): TranscriptToolIconName | undefined {
  return toolName ? TRANSCRIPT_TOOL_ICONS[toolName.trim()] : undefined;
}
