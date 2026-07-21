import { afterEach, describe, expect, it, vi } from "vitest";
import {
  BridgePromptAcknowledgementTimeoutError,
  BridgePromptDispatchHttpError,
  dispatchPromptForAcknowledgement,
} from "./bridgePromptDispatch";

const prompt = {
  type: "command" as const,
  payload: {
    type: "prompt" as const,
    message: "ordinary short prompt",
    streamingBehavior: "followUp" as const,
  },
};

describe("prompt acknowledgement dispatch", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("accepts only the Bridge 202 response", async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(null, { status: 202 }));

    await expect(
      dispatchPromptForAcknowledgement("/messages", prompt, { fetchImpl }),
    ).resolves.toBeUndefined();

    fetchImpl.mockResolvedValueOnce(new Response(null, { status: 200 }));
    await expect(
      dispatchPromptForAcknowledgement("/messages", prompt, { fetchImpl }),
    ).rejects.toBeInstanceOf(BridgePromptDispatchHttpError);
  });

  it("aborts a browser-queued prompt after ten seconds", async () => {
    vi.useFakeTimers();
    let requestSignal: AbortSignal | undefined;
    const fetchImpl = vi.fn<typeof fetch>((_input, init) => {
      requestSignal = init?.signal ?? undefined;
      return new Promise((_resolve, reject) => {
        requestSignal?.addEventListener("abort", () =>
          reject(new DOMException("Aborted", "AbortError")),
        );
      });
    });

    const dispatched = dispatchPromptForAcknowledgement("/messages", prompt, {
      fetchImpl,
    });
    const rejected = expect(dispatched).rejects.toBeInstanceOf(
      BridgePromptAcknowledgementTimeoutError,
    );
    await vi.advanceTimersByTimeAsync(10_000);

    await rejected;
    expect(requestSignal?.aborted).toBe(true);
  });
});
