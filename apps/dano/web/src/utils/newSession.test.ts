import { afterEach, describe, expect, it, vi } from "vitest";
import type { RpcResponse } from "@dano/types/protocol";
import {
  ACTIVE_SESSION_CACHE_KEY,
  createExplicitNewSessionAction,
  readActiveSessionCache,
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

describe("bridge new session response", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("updates the real store and cache while preserving state on failure", async () => {
    const storage = createStorage();
    vi.stubGlobal("window", { sessionStorage: storage });
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>(() => {})),
    );

    const store = await import("../composables/bridgeStore.svelte");
    const bridge = store.initBridge();
    const previousResponse = {
      ...successfulResponse,
      data: {
        ...successfulResponse.data,
        sessionId: "previous-session-id",
        sessionName: "Previous session",
        sessionPath: "/sessions/previous.jsonl",
        transcript: {
          messages: [
            {
              id: "previous-user-message",
              role: "user",
              content: "Keep this transcript",
            },
          ],
          hasOlder: false,
          hasNewer: false,
        },
      },
    } satisfies RpcResponse;

    expect(store.applyNewSessionResponse(previousResponse)).toMatchObject({
      success: true,
    });
    expect(bridge.transcript).toHaveLength(1);
    expect(bridge.activeSessionPath).toBe("/sessions/previous.jsonl");
    expect(readActiveSessionCache(storage)).toBe("/sessions/previous.jsonl");

    expect(
      store.applyNewSessionResponse({
        type: "response",
        command: "new_session",
        success: false,
        error: "Runtime unavailable",
      }),
    ).toEqual({ success: false });
    expect(bridge.transcript).toHaveLength(1);
    expect(bridge.activeSessionPath).toBe("/sessions/previous.jsonl");
    expect(readActiveSessionCache(storage)).toBe("/sessions/previous.jsonl");

    expect(store.applyNewSessionResponse(successfulResponse)).toMatchObject({
      success: true,
    });
    expect(bridge.transcript).toEqual([]);
    expect(bridge.activeSessionPath).toBe("/sessions/new.jsonl");
    expect(readActiveSessionCache(storage)).toBe("/sessions/new.jsonl");
  });
});
