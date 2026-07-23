const ENTER_INSERTS_NEWLINE_QUERY =
  "(hover: none) and (pointer: coarse)";
const TOUCH_ONLY_INPUT_QUERY =
  "(any-hover: none) and (any-pointer: coarse)";

type ComposerKeyboardEnvironment = {
  matchMedia: (query: string) => Pick<MediaQueryList, "matches">;
  navigator: Pick<Navigator, "maxTouchPoints">;
};

export function shouldEnterInsertNewline(
  environment: ComposerKeyboardEnvironment | undefined =
    typeof window === "undefined" ? undefined : window,
): boolean {
  if (
    typeof environment?.matchMedia !== "function" ||
    environment.navigator.maxTouchPoints <= 0
  ) {
    return false;
  }
  return (
    environment.matchMedia(ENTER_INSERTS_NEWLINE_QUERY).matches &&
    environment.matchMedia(TOUCH_ONLY_INPUT_QUERY).matches
  );
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
