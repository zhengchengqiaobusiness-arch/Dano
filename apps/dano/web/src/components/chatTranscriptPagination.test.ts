import { describe, expect, it } from "vitest";
import {
  nextTopLoadArmed,
  restoredScrollTop,
  shouldAutoLoadOlderTranscript,
  shouldShowTranscriptStartNotice,
} from "./chatTranscriptPagination";

describe("chat transcript older pagination", () => {
  it("restores scroll position only after older messages were added", () => {
    expect(
      restoredScrollTop({
        loaded: true,
        previousScrollTop: 24,
        previousScrollHeight: 500,
        nextScrollHeight: 820,
      }),
    ).toBe(344);

    expect(
      restoredScrollTop({
        loaded: false,
        previousScrollTop: 24,
        previousScrollHeight: 500,
        nextScrollHeight: 560,
      }),
    ).toBeNull();
  });

  it("does not restore scroll position after load failure or duplicate empty loads", () => {
    expect(
      restoredScrollTop({
        loaded: false,
        previousScrollTop: 100,
        previousScrollHeight: 900,
        nextScrollHeight: 900,
      }),
    ).toBeNull();
  });

  it("loads once per top-zone entry and rearms only after leaving the threshold", () => {
    expect(
      shouldAutoLoadOlderTranscript({
        isNearTop: true,
        topLoadArmed: true,
        hasOlder: true,
        initialLoading: false,
        pageLoading: false,
        requestPending: false,
      }),
    ).toBe(true);

    expect(
      shouldAutoLoadOlderTranscript({
        isNearTop: true,
        topLoadArmed: false,
        hasOlder: true,
        initialLoading: false,
        pageLoading: false,
        requestPending: false,
      }),
    ).toBe(false);

    expect(nextTopLoadArmed({ isNearTop: true, current: false })).toBe(false);
    expect(nextTopLoadArmed({ isNearTop: false, current: false })).toBe(true);
  });

  it("shows no-more history only after transcript start was confirmed", () => {
    expect(
      shouldShowTranscriptStartNotice({
        hasReachedTranscriptStart: false,
        isNearTop: true,
        messagesLength: 20,
        initialLoading: false,
      }),
    ).toBe(false);

    expect(
      shouldShowTranscriptStartNotice({
        hasReachedTranscriptStart: true,
        isNearTop: true,
        messagesLength: 20,
        initialLoading: false,
      }),
    ).toBe(true);

    expect(
      shouldShowTranscriptStartNotice({
        hasReachedTranscriptStart: true,
        isNearTop: false,
        messagesLength: 20,
        initialLoading: false,
      }),
    ).toBe(false);
  });
});
