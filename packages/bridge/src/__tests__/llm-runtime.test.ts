import { afterEach, describe, expect, it, vi } from "vitest";
import {
  DEFAULT_LLM_ACTIVITY_TIMEOUT_MS,
  PiCodingAgentRuntime,
  createActivityTimeout,
} from "../llm-runtime.js";
import type { RuntimeCallbacks } from "../types.js";

type SessionEvent = Record<string, unknown>;
type SessionSubscriber = (event: SessionEvent) => void;

function createCallbacks(): RuntimeCallbacks {
  return {
    onDelta: vi.fn(),
    onContentBlocks: vi.fn(),
    onComplete: vi.fn(),
    onFailure: vi.fn(),
  };
}

function createScriptedSession(
  steps: Array<{ atMs: number; event: SessionEvent }>,
) {
  const subscribers = new Set<SessionSubscriber>();
  return {
    subscribe(callback: SessionSubscriber) {
      subscribers.add(callback);
      return () => subscribers.delete(callback);
    },
    prompt: vi.fn(async () => {
      for (const step of steps) {
        setTimeout(() => {
          for (const subscriber of subscribers) {
            subscriber(step.event);
          }
        }, step.atMs);
      }
    }),
    sessionManager: {
      getBranch: () => [],
    },
  };
}

describe("LLM activity timeout", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("defaults to a five-minute inactivity window", () => {
    expect(DEFAULT_LLM_ACTIVITY_TIMEOUT_MS).toBe(300_000);
  });

  it("fires after the configured idle window", () => {
    vi.useFakeTimers();
    const onTimeout = vi.fn();
    const timeout = createActivityTimeout(100, onTimeout);

    timeout.refresh();
    vi.advanceTimersByTime(99);
    expect(onTimeout).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    expect(onTimeout).toHaveBeenCalledTimes(1);
  });

  it("extends the idle window when activity is refreshed", () => {
    vi.useFakeTimers();
    const onTimeout = vi.fn();
    const timeout = createActivityTimeout(100, onTimeout);

    timeout.refresh();
    vi.advanceTimersByTime(80);
    timeout.refresh();
    vi.advanceTimersByTime(80);
    expect(onTimeout).not.toHaveBeenCalled();

    vi.advanceTimersByTime(20);
    expect(onTimeout).toHaveBeenCalledTimes(1);
  });

  it("uses only the latest timer after repeated refreshes", () => {
    vi.useFakeTimers();
    const onTimeout = vi.fn();
    const timeout = createActivityTimeout(100, onTimeout);

    timeout.refresh();
    timeout.refresh();
    timeout.refresh();
    vi.advanceTimersByTime(100);

    expect(onTimeout).toHaveBeenCalledTimes(1);
  });

  it("can be cleared before or after refresh without firing", () => {
    vi.useFakeTimers();
    const onTimeout = vi.fn();
    const timeout = createActivityTimeout(100, onTimeout);

    timeout.clear();
    timeout.refresh();
    timeout.clear();
    vi.advanceTimersByTime(100);

    expect(onTimeout).not.toHaveBeenCalled();
  });

  it("keeps a long turn alive while runtime events continue arriving", async () => {
    vi.useFakeTimers();
    const callbacks = createCallbacks();
    const session = createScriptedSession([
      {
        atMs: 80,
        event: {
          type: "message_update",
          message: {
            role: "assistant",
            content: [{ type: "thinking", thinking: "Inspecting files" }],
          },
        },
      },
      {
        atMs: 160,
        event: {
          type: "tool_execution_update",
          toolCallId: "tool-1",
          toolName: "bash",
          partialResult: "still running",
        },
      },
      {
        atMs: 240,
        event: {
          type: "message_update",
          assistantMessageEvent: { type: "text_delta", delta: "Almost " },
          message: {
            role: "assistant",
            content: [{ type: "text", text: "Almost " }],
          },
        },
      },
      {
        atMs: 320,
        event: {
          type: "message_end",
          message: {
            role: "assistant",
            content: [{ type: "text", text: "Almost done." }],
          },
        },
      },
    ]);
    const runtime = new PiCodingAgentRuntime({
      cwd: "/tmp",
      timeoutMs: 100,
      sessionFactory: () => session as never,
    });

    const turn = runtime.sendUserMessage("test timeout refresh", callbacks);
    await vi.advanceTimersByTimeAsync(319);

    expect(callbacks.onFailure).not.toHaveBeenCalled();
    expect(callbacks.onComplete).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(1);
    await turn;

    expect(callbacks.onFailure).not.toHaveBeenCalled();
    expect(callbacks.onComplete).toHaveBeenCalledWith("Almost done.");
    expect(callbacks.onContentBlocks).toHaveBeenCalledWith([
      { kind: "thinking", text: "Inspecting files" },
    ]);
  });

  it("fails a runtime turn only after the activity window is idle", async () => {
    vi.useFakeTimers();
    const callbacks = createCallbacks();
    const session = createScriptedSession([]);
    const runtime = new PiCodingAgentRuntime({
      cwd: "/tmp",
      timeoutMs: 100,
      sessionFactory: () => session as never,
    });

    const turn = runtime.sendUserMessage("test idle timeout", callbacks);
    await vi.advanceTimersByTimeAsync(99);
    expect(callbacks.onFailure).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(1);
    await turn;

    expect(callbacks.onComplete).not.toHaveBeenCalled();
    expect(callbacks.onFailure).toHaveBeenCalledWith({
      code: "LLM_TIMEOUT",
      errorMessage: "The assistant stopped making progress in time.",
      retryable: true,
    });
  });
});
