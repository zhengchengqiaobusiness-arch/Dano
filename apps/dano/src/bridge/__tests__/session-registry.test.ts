import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { createAgentSessionMock } = vi.hoisted(() => ({
  createAgentSessionMock: vi.fn(),
}));

vi.mock("../detached-session.js", () => ({
  createDetachedAgentSession: createAgentSessionMock,
}));

import { DetachedSessionRegistry } from "../session-registry.js";

const roots: string[] = [];

function createRegistry() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "dano-session-registry-"));
  roots.push(root);
  return { registry: new DetachedSessionRegistry(root), root };
}

function createRunningSession(registry: DetachedSessionRegistry, root: string) {
  const handle = registry.createSession({ cwd: root, sessionDir: root });
  const calls: string[] = [];
  const operationController = new AbortController();
  const retryController = new AbortController();
  const abortRetry = vi.fn(() => {
    calls.push("abortRetry");
    retryController.abort();
  });
  const abort = vi.fn(async () => {
    calls.push("abort");
    operationController.abort();
  });
  const session = {
    sessionFile: handle.sessionPath,
    sessionId: "detached-session",
    isStreaming: true,
    abort,
    abortRetry,
    bindExtensions: vi.fn().mockResolvedValue(undefined),
    subscribe: vi.fn().mockReturnValue(() => {}),
    dispose: vi.fn(),
    sessionManager: handle.getSessionManager(),
  };
  createAgentSessionMock.mockResolvedValueOnce({ session });
  return {
    handle,
    abort,
    abortRetry,
    calls,
    operationSignal: operationController.signal,
    retrySignal: retryController.signal,
  };
}

beforeEach(() => {
  createAgentSessionMock.mockReset();
});

afterEach(() => {
  for (const root of roots.splice(0)) {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

describe("DetachedSessionRegistry terminal viewer teardown", () => {
  it.each([
    { state: "provider request", signal: "operationSignal" as const },
    { state: "tool execution", signal: "operationSignal" as const },
    { state: "retry delay", signal: "retrySignal" as const },
  ])(
    "cancels an orphaned $state when its final viewer is destroyed",
    async ({ signal }) => {
      const { registry, root } = createRegistry();
      const running = createRunningSession(registry, root);

      await registry.bindViewer(running.handle.sessionPath, {
        clientId: "client-a",
        uiContext: {} as never,
      });
      await registry.ensureSession(running.handle.sessionPath);
      await registry.destroyViewer(running.handle.sessionPath, "client-a");

      expect(running[signal].aborted).toBe(true);
      expect(running.abortRetry).toHaveBeenCalledTimes(1);
      expect(running.abort).toHaveBeenCalledTimes(1);
      expect(running.calls).toEqual(["abortRetry", "abort"]);
    },
  );

  it("keeps a running session alive while another viewer still owns it", async () => {
    const { registry, root } = createRegistry();
    const { handle, abort, abortRetry } = createRunningSession(registry, root);

    await registry.bindViewer(handle.sessionPath, {
      clientId: "client-a",
      uiContext: {} as never,
    });
    await registry.bindViewer(handle.sessionPath, {
      clientId: "client-b",
      uiContext: {} as never,
    });
    await registry.ensureSession(handle.sessionPath);

    await registry.destroyViewer(handle.sessionPath, "client-a");
    expect(abortRetry).not.toHaveBeenCalled();
    expect(abort).not.toHaveBeenCalled();

    await registry.destroyViewer(handle.sessionPath, "client-b");
    expect(abortRetry).toHaveBeenCalledTimes(1);
    expect(abort).toHaveBeenCalledTimes(1);
  });

  it("is idempotent when a viewer is destroyed repeatedly", async () => {
    const { registry, root } = createRegistry();
    const { handle, abort, abortRetry } = createRunningSession(registry, root);

    await registry.bindViewer(handle.sessionPath, {
      clientId: "client-a",
      uiContext: {} as never,
    });
    await registry.ensureSession(handle.sessionPath);

    await registry.destroyViewer(handle.sessionPath, "client-a");
    await registry.destroyViewer(handle.sessionPath, "client-a");

    expect(abortRetry).toHaveBeenCalledTimes(1);
    expect(abort).toHaveBeenCalledTimes(1);
  });

  it("does not abort when a viewer only switches away", async () => {
    const { registry, root } = createRegistry();
    const { handle, abort, abortRetry } = createRunningSession(registry, root);

    await registry.bindViewer(handle.sessionPath, {
      clientId: "client-a",
      uiContext: {} as never,
    });
    await registry.ensureSession(handle.sessionPath);
    await registry.releaseViewer(handle.sessionPath, "client-a");

    expect(abortRetry).not.toHaveBeenCalled();
    expect(abort).not.toHaveBeenCalled();
  });
});
