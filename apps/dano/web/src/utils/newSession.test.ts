import { describe, expect, it, vi } from "vitest";
import type { RpcResponse } from "@dano/types/protocol";
import {
  ACTIVE_SESSION_CACHE_KEY,
  createExplicitNewSessionAction,
  readActiveSessionCache,
  transitionNewSessionView,
  writeActiveSessionCache,
} from "./newSession";

function createStorage(): Storage {
  const values = new Map<string, string>();
  return {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: key => values.get(key) ?? null,
    key: index => [...values.keys()][index] ?? null,
    removeItem: key => values.delete(key),
    setItem: (key, value) => values.set(key, value),
  };
}

const successfulResponse = {
  type: "response",
  command: "new_session",
  success: true,
  data: {
    cancelled: false,
    sessionId: "new-session-id",
    sessionName: "New session",
    sessionPath: "/sessions/new.jsonl",
    transcript: { messages: [], hasOlder: false, hasNewer: false },
    treeEntries: [],
    thinkingLevel: "medium",
    workspacePath: "/workspaces/new",
  },
} satisfies RpcResponse;

describe("active session cache", () => {
  it("stores the newly active session and clears stale paths", () => {
    const storage = createStorage();

    writeActiveSessionCache(storage, "/sessions/new.jsonl");
    expect(readActiveSessionCache(storage)).toBe("/sessions/new.jsonl");
    expect(storage.getItem(ACTIVE_SESSION_CACHE_KEY)).toBe(
      "/sessions/new.jsonl",
    );

    writeActiveSessionCache(storage, null);
    expect(readActiveSessionCache(storage)).toBeNull();
  });
});

describe("explicit new session action", () => {
  it("creates a new session through the existing bridge command", async () => {
    const createSession = vi.fn().mockResolvedValue(successfulResponse);
    const reportError = vi.fn();
    const newSession = createExplicitNewSessionAction(
      createSession,
      reportError,
    );

    await expect(newSession()).resolves.toMatchObject({ success: true });
    expect(createSession).toHaveBeenCalledOnce();
    expect(createSession).toHaveBeenCalledWith();
    expect(reportError).not.toHaveBeenCalled();
  });

  it("coalesces rapid activations into one creation request", async () => {
    let resolveRequest!: (result: RpcResponse) => void;
    const createSession = vi.fn(
      () =>
        new Promise<RpcResponse>(resolve => {
          resolveRequest = resolve;
        }),
    );
    const newSession = createExplicitNewSessionAction(
      createSession,
      vi.fn(),
    );

    const first = newSession();
    const second = newSession();

    expect(first).toBe(second);
    expect(createSession).toHaveBeenCalledOnce();
    resolveRequest(successfulResponse);
    await first;
  });

  it("reports failed responses and allows a later retry", async () => {
    const createSession = vi
      .fn()
      .mockResolvedValueOnce({
        type: "response",
        command: "new_session",
        success: false,
        error: "Runtime unavailable",
      } satisfies RpcResponse)
      .mockResolvedValueOnce(successfulResponse);
    const reportError = vi.fn();
    const newSession = createExplicitNewSessionAction(
      createSession,
      reportError,
    );

    await expect(newSession()).resolves.toMatchObject({ success: false });
    expect(reportError).toHaveBeenCalledWith("Runtime unavailable");

    await expect(newSession()).resolves.toMatchObject({ success: true });
    expect(createSession).toHaveBeenCalledTimes(2);
  });

  it("reports rejected requests without converting them into success", async () => {
    const reportError = vi.fn();
    const newSession = createExplicitNewSessionAction(
      vi.fn().mockRejectedValue(new Error("Connection lost")),
      reportError,
    );

    await expect(newSession()).rejects.toThrow("Connection lost");
    expect(reportError).toHaveBeenCalledWith(expect.any(Error));
  });
});

describe("new session response transition", () => {
  const previousMessage = {
    id: "previous-user-message",
    role: "user",
    content: "Keep this transcript",
  } as const;
  const previous = {
    activeSessionPath: "/sessions/previous.jsonl",
    transcript: [previousMessage],
  };

  it("switches a successful response to the returned blank session", () => {
    const next = transitionNewSessionView(successfulResponse, previous);

    expect(next).toEqual({
      activeSessionPath: "/sessions/new.jsonl",
      transcript: [],
    });

    const storage = createStorage();
    writeActiveSessionCache(storage, next.activeSessionPath);
    expect(readActiveSessionCache(storage)).toBe("/sessions/new.jsonl");
  });

  it("preserves the active session and transcript on failure", () => {
    const next = transitionNewSessionView(
      {
        id: "new-session",
        type: "response",
        command: "new_session",
        success: false,
        error: "Runtime unavailable",
      },
      previous,
    );

    expect(next).toBe(previous);
    expect(next.transcript).toEqual([previousMessage]);
  });
});
