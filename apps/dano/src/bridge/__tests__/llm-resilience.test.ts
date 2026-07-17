import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  configureDanoLlmResilience,
  DANO_LLM_RETRY_WINDOW_MS,
} from "../llm-resilience.js";

describe("configureDanoLlmResilience", () => {
  let handleEvent: ((event: any) => void) | undefined;
  let applyOverrides: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));
    applyOverrides = vi.fn();
    handleEvent = undefined;

    configureDanoLlmResilience(
      { applyOverrides } as never,
      {
        subscribe: vi.fn(handler => {
          handleEvent = handler;
          return vi.fn();
        }),
      } as never,
      { DANO_LLM_TIMEOUT_MS: "300000" },
    );
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("stops scheduling retries when the total retry window is exhausted", () => {
    handleEvent?.({
      type: "message_start",
      message: { role: "user", content: "hello" },
    });
    vi.advanceTimersByTime(DANO_LLM_RETRY_WINDOW_MS - 1_000);

    handleEvent?.({
      type: "message_end",
      message: {
        role: "assistant",
        stopReason: "error",
        content: [],
        errorMessage: "Request timed out",
      },
    });

    expect(applyOverrides).toHaveBeenLastCalledWith({
      retry: { enabled: false },
    });
  });

  it("limits the next provider request to the remaining retry window", () => {
    handleEvent?.({
      type: "message_start",
      message: { role: "user", content: "hello" },
    });
    vi.advanceTimersByTime(DANO_LLM_RETRY_WINDOW_MS - 10_000);

    handleEvent?.({
      type: "auto_retry_start",
      attempt: 1,
      maxAttempts: 10,
      delayMs: 2_000,
      errorMessage: "Request timed out",
    });

    expect(applyOverrides).toHaveBeenLastCalledWith({
      retry: { provider: { timeoutMs: 8_000 } },
    });
  });
});
