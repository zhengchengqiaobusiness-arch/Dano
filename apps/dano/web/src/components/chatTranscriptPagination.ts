export const TRANSCRIPT_START_NOTICE_DURATION_MS = 1000;

export function shouldAutoLoadOlderTranscript(options: {
  isNearTop: boolean;
  topLoadArmed: boolean;
  hasOlder: boolean;
  initialLoading: boolean;
  pageLoading: boolean;
  requestPending: boolean;
}): boolean {
  return (
    options.isNearTop &&
    options.topLoadArmed &&
    options.hasOlder &&
    !options.initialLoading &&
    !options.pageLoading &&
    !options.requestPending
  );
}

export function nextTopLoadArmed(options: {
  isNearTop: boolean;
  current: boolean;
}): boolean {
  return options.isNearTop ? options.current : true;
}

export function restoredScrollTop(options: {
  loaded: boolean;
  previousScrollTop: number;
  previousScrollHeight: number;
  nextScrollHeight: number;
}): number | null {
  if (!options.loaded) return null;
  return (
    options.previousScrollTop +
    options.nextScrollHeight -
    options.previousScrollHeight
  );
}

export function shouldShowTranscriptStartNotice(options: {
  topLoadTriggered: boolean;
  hasOlder: boolean;
  messagesLength: number;
  initialLoading: boolean;
  pageLoading: boolean;
  requestPending: boolean;
}): boolean {
  return (
    options.topLoadTriggered &&
    !options.hasOlder &&
    options.messagesLength > 0 &&
    !options.initialLoading &&
    !options.pageLoading &&
    !options.requestPending
  );
}
