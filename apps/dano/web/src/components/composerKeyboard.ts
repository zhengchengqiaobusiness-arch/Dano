const ENTER_INSERTS_NEWLINE_QUERY =
  "(hover: none) and (pointer: coarse)";

type ComposerKeyboardEnvironment = {
  matchMedia: (query: string) => Pick<MediaQueryList, "matches">;
  navigator: Pick<Navigator, "userAgent"> & {
    userAgentData?: { mobile?: boolean };
  };
};

const MOBILE_BROWSER_PATTERN =
  /Android|iPhone|iPad|iPod|IEMobile|Opera Mini|Mobile/i;

function isMobileBrowser(navigator: ComposerKeyboardEnvironment["navigator"]): boolean {
  return (
    navigator.userAgentData?.mobile ??
    MOBILE_BROWSER_PATTERN.test(navigator.userAgent)
  );
}

export function shouldEnterInsertNewline(
  environment: ComposerKeyboardEnvironment | undefined =
    typeof window === "undefined" ? undefined : window,
): boolean {
  if (
    typeof environment?.matchMedia !== "function" ||
    !isMobileBrowser(environment.navigator)
  ) {
    return false;
  }
  return environment.matchMedia(ENTER_INSERTS_NEWLINE_QUERY).matches;
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
