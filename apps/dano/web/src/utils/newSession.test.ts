import { describe, expect, it, vi } from "vitest";
import {
  ACTIVE_SESSION_CACHE_KEY,
  createSingleFlightNewSession,
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
  it("creates a new session through the supplied existing path", async () => {
    const createSession = vi.fn().mockResolvedValue({
      success: true,
      data: { sessionPath: "/sessions/new.jsonl" },
    });
    const reportError = vi.fn();
    const newSession = createSingleFlightNewSession(
      createSession,
      reportError,
      () => "Failed to create a new session",
    );

    await expect(newSession()).resolves.toMatchObject({ success: true });
    expect(createSession).toHaveBeenCalledOnce();
    expect(createSession).toHaveBeenCalledWith(undefined);
    expect(reportError).not.toHaveBeenCalled();
  });

  it("coalesces rapid activations into one creation request", async () => {
    let resolveRequest!: (result: { success: true }) => void;
    const createSession = vi.fn(
      () =>
        new Promise<{ success: true }>(resolve => {
          resolveRequest = resolve;
        }),
    );
    const newSession = createSingleFlightNewSession(
      createSession,
      vi.fn(),
      () => "Failed to create a new session",
    );

    const first = newSession();
    const second = newSession();

    expect(first).toBe(second);
    expect(createSession).toHaveBeenCalledOnce();
    resolveRequest({ success: true });
    await first;
  });

  it("reports failed responses and allows a later retry", async () => {
    const createSession = vi
      .fn()
      .mockResolvedValueOnce({ success: false, error: "Runtime unavailable" })
      .mockResolvedValueOnce({ success: true });
    const reportError = vi.fn();
    const newSession = createSingleFlightNewSession(
      createSession,
      reportError,
      () => "Failed to create a new session",
    );

    await expect(newSession()).resolves.toMatchObject({ success: false });
    expect(reportError).toHaveBeenCalledWith("Runtime unavailable");

    await expect(newSession()).resolves.toMatchObject({ success: true });
    expect(createSession).toHaveBeenCalledTimes(2);
  });

  it("reports rejected requests without converting them into success", async () => {
    const reportError = vi.fn();
    const newSession = createSingleFlightNewSession(
      vi.fn().mockRejectedValue(new Error("Connection lost")),
      reportError,
      () => "Failed to create a new session",
    );

    await expect(newSession()).rejects.toThrow("Connection lost");
    expect(reportError).toHaveBeenCalledWith("Connection lost");
  });
});
