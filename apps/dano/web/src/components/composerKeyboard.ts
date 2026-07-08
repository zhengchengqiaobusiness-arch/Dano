const ENTER_INSERTS_NEWLINE_QUERY =
  "(hover: none) and (pointer: coarse), (max-width: 768px)";

export function shouldEnterInsertNewline(
  win: Pick<Window, "matchMedia"> | undefined =
    typeof window === "undefined" ? undefined : window,
): boolean {
  if (typeof win?.matchMedia !== "function") return false;
  return win.matchMedia(ENTER_INSERTS_NEWLINE_QUERY).matches;
}

export function shouldSubmitComposerEnter(
  event: Pick<KeyboardEvent, "key" | "shiftKey" | "isComposing" | "keyCode">,
  isComposingState: boolean,
  enterInsertsNewline: boolean,
): boolean {
  if (event.key !== "Enter") return false;
  if (event.isComposing || isComposingState || event.keyCode === 229) return false;
  if (event.shiftKey || enterInsertsNewline) return false;
  return true;
}
