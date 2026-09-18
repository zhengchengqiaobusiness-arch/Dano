import { EventEmitter } from "node:events";
import { afterEach, expect, it, vi } from "vitest";
import { WorkerSupervisorClient } from "../worker-supervisor-client.js";
import { serveWorkerSupervisor } from "../worker-supervisor-rpc.js";
import { WorkerSupervisor, type SupervisedWorker, type WorkerSupervisorOptions } from "../worker-supervisor.js";

class Channel extends EventEmitter {
  connected = true;
  peer!: Channel;
  send(value: object, callback: (error: Error | null) => void) {
    queueMicrotask(() => { if (this.connected) this.peer.emit("message", value); callback(this.connected ? null : new Error("disconnected")); });
  }
  disconnect() {
    if (!this.connected) return;
    this.connected = false;
    this.emit("disconnect");
    this.peer.connected = false;
    this.peer.emit("disconnect");
  }
}
const cleanup: Array<() => Promise<void>> = [];
afterEach(async () => { await Promise.all(cleanup.splice(0).map(close => close())); });
function harness() {
  const host = new Channel(); const root = new Channel(); host.peer = root; root.peer = host;
  const factory = vi.fn(async (_owner: string, workspace: string): Promise<SupervisedWorker> => {
    let closed = false;
    return { workspace, agentDir: "/private/agent", stateDir: "/private/state", worker: {
      workspace,
      async assertIsolated() { if (closed) throw new Error("CLOSED"); },
      async execute(_name, parameters, signal, update) {
        update?.({ progress: true });
        if (parameters.wait) await new Promise<void>(resolve => signal!.addEventListener("abort", () => resolve(), { once: true }));
        signal?.throwIfAborted();
        return { workspace, value: parameters.value };
      },
      close: vi.fn(async () => { closed = true; }),
    } };
  });
  const pool = new WorkerSupervisor({ maxWorkers: 8 } as WorkerSupervisorOptions, factory);
  const client = new WorkerSupervisorClient(host, { maxConcurrentOperations: 8, maxMessageBytes: 4096,
    startupTimeoutMs: 1000, operationTimeoutMs: 1000 });
  const server = serveWorkerSupervisor(pool, root, { maxConcurrentOperations: 8, maxMessageBytes: 4096 });
  cleanup.push(async () => { client.close(); await server.close(); });
  return { client, factory, host };
}
const user = (id: string) => ({ user: { id }, folderPath: `/users/${id}` });

it("binds profiles to server users, shares startup, and refuses foreign workspaces", async () => {
  const { client, factory } = harness();
  const alice = await client.profile(user("alice"), { trustedSkillPaths: [] });
  const bob = await client.profile(user("bob"), { trustedSkillPaths: [] });
  const [a, repeated] = await Promise.all([
    alice.resolveWorker("/users/alice/workspaces/default"), alice.resolveWorker("/users/alice/workspaces/default"),
  ]);
  expect(factory).toHaveBeenCalledTimes(2);
  await repeated.assertIsolated();
  await expect(alice.resolveWorker("/users/bob/workspaces/default")).rejects.toThrow("OWNER_MISMATCH");
  const updates: unknown[] = [];
  expect(await a.execute("read", { value: "own" }, undefined, value => updates.push(value)))
    .toEqual({ workspace: "/users/alice/workspaces/default", value: "own" });
  expect(updates).toEqual([{ progress: true }]);
  await alice.dispose!();
  await expect(a.execute("read", {})).rejects.toThrow("UNAVAILABLE");
  const b = await bob.resolveWorker("/users/bob/workspaces/default");
  await b.assertIsolated();
  await bob.dispose!();
});

it("propagates cancellation across both IPC directions", async () => {
  const { client } = harness();
  const profile = await client.profile(user("alice"), { trustedSkillPaths: [] });
  const worker = await profile.resolveWorker("/users/alice/workspaces/default");
  const abort = new AbortController();
  await expect(worker.execute("bash", { wait: true }, abort.signal, () => abort.abort())).rejects.toThrow("CANCELLED");
  await worker.assertIsolated();
  await profile.dispose!();
});

it("invalidates profiles after parent disconnect and never falls back to local execution", async () => {
  const { client, host } = harness();
  const profile = await client.profile(user("alice"), { trustedSkillPaths: [] });
  const worker = await profile.resolveWorker("/users/alice/workspaces/default");
  host.disconnect();
  await expect(worker.assertIsolated()).rejects.toThrow("UNAVAILABLE");
  await expect(profile.resolveWorker("/users/alice/workspaces/another")).rejects.toThrow("UNAVAILABLE");
});
