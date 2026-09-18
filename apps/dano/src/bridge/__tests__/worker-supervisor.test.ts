import { expect, it, vi } from "vitest";
import { WorkerSupervisor, type SupervisedWorker, type WorkerSupervisorOptions } from "../worker-supervisor.js";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}
function lease(workspace = "/users/alice/workspaces/default"): SupervisedWorker {
  let closed = false;
  const assertIsolated = vi.fn(async () => { if (closed) throw new Error("CLOSED"); });
  return { workspace, agentDir: "/private/agent", stateDir: "/private/state", worker: {
    workspace, assertIsolated, execute: async () => { await assertIsolated(); return {}; },
    close: vi.fn(async () => { closed = true; }),
  } };
}
function supervisor(factory: (owner: string, workspace: string) => Promise<SupervisedWorker>, maxWorkers = 2) {
  // Injected factory replaces privileged Linux provisioning in lifecycle tests.
  return new WorkerSupervisor({ maxWorkers } as WorkerSupervisorOptions, factory);
}

it("shares pending startup per owner/workspace and counts starts toward capacity", async () => {
  const start = deferred<SupervisedWorker>();
  const factory = vi.fn(() => start.promise);
  const pool = supervisor(factory, 1);
  const item = lease();
  const first = pool.acquire("alice", item.workspace);
  const second = pool.acquire("alice", item.workspace + "/.");
  await expect(pool.acquire("bob", "/users/bob/workspaces/default")).rejects.toThrow("LIMIT");
  start.resolve(item);
  expect(await first).toBe(await second);
  expect(factory).toHaveBeenCalledTimes(1);
  await pool.close();
  await expect(item.worker.execute("read", {})).rejects.toThrow("CLOSED");
});

it("waits for pending startup during shutdown and closes the late worker exactly once", async () => {
  const start = deferred<SupervisedWorker>();
  const pool = supervisor(() => start.promise);
  const item = lease();
  const request = expect(pool.acquire("alice", item.workspace)).rejects.toThrow("CLOSED");
  let done = false;
  const closing = pool.close().then(() => { done = true; });
  await Promise.resolve();
  expect(done).toBe(false);
  start.resolve(item);
  await Promise.all([request, closing]);
  expect(item.worker.close).toHaveBeenCalledTimes(1);
  await expect(pool.acquire("bob", "/bob")).rejects.toThrow("CLOSED");
});

it("retires one owner without closing a peer and shutdown waits for retirement", async () => {
  const blocked = deferred<void>();
  const alice = lease();
  const bob = lease("/users/bob/workspaces/default");
  vi.mocked(alice.worker.close).mockImplementation(() => blocked.promise);
  const pool = supervisor(async owner => owner === "alice" ? alice : bob);
  await pool.acquire("alice", alice.workspace);
  await pool.acquire("bob", bob.workspace);
  const retirement = pool.retire("alice");
  await expect(pool.acquire("alice", alice.workspace)).rejects.toThrow("CLOSED");
  await bob.worker.assertIsolated();
  let done = false;
  const closing = pool.close().then(() => { done = true; });
  await Promise.resolve();
  expect(done).toBe(false);
  blocked.resolve();
  await Promise.all([retirement, closing]);
  expect(alice.worker.close).toHaveBeenCalledTimes(1);
  expect(bob.worker.close).toHaveBeenCalledTimes(1);
});

it("allows a new attempt after failed startup without replaying any operation", async () => {
  const item = lease();
  const factory = vi.fn().mockRejectedValueOnce(new Error("START_FAILED")).mockResolvedValue(item);
  const pool = supervisor(factory, 1);
  await expect(pool.acquire("alice", item.workspace)).rejects.toThrow("START_FAILED");
  expect(await pool.acquire("alice", item.workspace)).toBe(item);
  await pool.close();
});

it("does not hide a late worker cleanup failure during shutdown", async () => {
  const start = deferred<SupervisedWorker>();
  const item = lease();
  vi.mocked(item.worker.close).mockRejectedValue(new Error("CLOSE_FAILED"));
  const pool = supervisor(() => start.promise);
  const request = expect(pool.acquire("alice", item.workspace)).rejects.toThrow("CLOSE_FAILED");
  const closing = expect(pool.close()).rejects.toThrow("CLEANUP_FAILED");
  start.resolve(item);
  await Promise.all([request, closing]);
});

it("closes a worker that fails the isolation handshake", async () => {
  const item = lease();
  vi.mocked(item.worker.assertIsolated).mockRejectedValue(new Error("ISOLATION_FAILED"));
  const pool = supervisor(async () => item);
  await expect(pool.acquire("alice", item.workspace)).rejects.toThrow("ISOLATION_FAILED");
  expect(item.worker.close).toHaveBeenCalledTimes(1);
  await pool.close();
});

it("rejects acquisition when retirement races the isolation handshake", async () => {
  const handshake = deferred<void>();
  const entered = deferred<void>();
  const item = lease();
  vi.mocked(item.worker.assertIsolated).mockImplementation(async () => {
    entered.resolve();
    await handshake.promise;
  });
  const pool = supervisor(async () => item);
  const request = expect(pool.acquire("alice", item.workspace)).rejects.toThrow("CLOSED");
  await entered.promise;
  const retiring = pool.retire("alice");
  handshake.resolve();
  await Promise.all([request, retiring]);
  expect(item.worker.close).toHaveBeenCalledTimes(1);
  await pool.close();
});

it("waits for other workers even when one cleanup fails", async () => {
  const pending = deferred<void>();
  const started = deferred<void>();
  const alice = lease();
  const bob = lease("/users/bob/workspaces/default");
  vi.mocked(alice.worker.close).mockRejectedValue(new Error("CLOSE_FAILED"));
  vi.mocked(bob.worker.close).mockImplementation(async () => { started.resolve(); await pending.promise; });
  const pool = supervisor(async owner => owner === "alice" ? alice : bob);
  await pool.acquire("alice", alice.workspace);
  await pool.acquire("bob", bob.workspace);
  let settled = false;
  const result = pool.close().catch(error => { settled = true; throw error; });
  const check = expect(result).rejects.toThrow("CLEANUP_FAILED");
  await started.promise;
  await Promise.resolve();
  expect(settled).toBe(false);
  pending.resolve();
  await check;
});
