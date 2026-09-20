import { EventEmitter } from "node:events";
import { afterEach, expect, it, vi } from "vitest";
import { serveWorkerSupervisor } from "../worker-supervisor-rpc.js";
import { WorkerSupervisor, type SupervisedWorker, type WorkerSupervisorOptions } from "../worker-supervisor.js";

class Channel extends EventEmitter {
  connected = true;
  readonly output = new EventEmitter();
  send(value: object, callback: (error: Error | null) => void) { this.output.emit("message", value); callback(null); }
  disconnect() { this.connected = false; this.emit("disconnect"); }
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}
function worker(workspace: string): SupervisedWorker {
  let closed = false;
  return { workspace, agentDir: "/private/agent", stateDir: "/private/state", worker: {
    workspace,
    async assertIsolated() { if (closed) throw new Error("private diagnostic"); },
    execute: vi.fn(async (_name, parameters, signal, update) => {
      update?.({ workspace });
      if (parameters.wait) await new Promise<void>(resolve => signal!.addEventListener("abort", () => resolve(), { once: true }));
      signal?.throwIfAborted();
      return { workspace, value: parameters.value };
    }),
    close: vi.fn(async () => { closed = true; }),
  } };
}
const servers: Array<{ close(): Promise<void> }> = [];
afterEach(async () => { await Promise.all(servers.splice(0).map(server => server.close())); });
function harness(factory = async (_owner: string, workspace: string) => worker(workspace)) {
  const channel = new Channel();
  const pool = new WorkerSupervisor({ maxWorkers: 4 } as WorkerSupervisorOptions, factory);
  const server = serveWorkerSupervisor(pool, channel, { maxConcurrentOperations: 4, maxMessageBytes: 4096 });
  servers.push(server);
  let sequence = 0;
  function request(body: Record<string, unknown>, update?: (value: unknown) => void) {
    const id = `request-${++sequence}`;
    const result = new Promise<Record<string, any>>(resolve => {
      const receive = (message: Record<string, any>) => {
        if (message.id !== id) return;
        if (message.type === "update") update?.(message.value);
        else { channel.output.off("message", receive); resolve(message); }
      };
      channel.output.on("message", receive);
    });
    channel.emit("message", { ...body, id });
    return { id, result };
  }
  return { channel, pool, server, request };
}

it("keeps an old user token usable after idle worker eviction without replaying prior commands", async () => {
  const created: SupervisedWorker[] = [];
  const h = harness(async (_owner, workspace) => {
    const value = worker(workspace); created.push(value); return value;
  });
  const first = (await h.request({ type: "acquire", owner: "alice", workspace: "/alice" }).result).value;
  expect((await h.request({ type: "execute", token: first.token, name: "read", parameters: { value: "first" } }).result).type)
    .toBe("result");
  for (let i = 0; i < 6; i++) {
    expect((await h.request({ type: "acquire", owner: `user${i}`, workspace: `/user${i}` }).result).type).toBe("result");
  }
  expect(created[0]!.worker.close).toHaveBeenCalledTimes(1);
  const response = await h.request({ type: "execute", token: first.token, name: "read", parameters: { value: "second" } }).result;
  expect(response).toMatchObject({ type: "result", value: { workspace: "/alice", value: "second" } });
  expect(created[0]!.worker.execute).toHaveBeenCalledTimes(1);
  expect(created.at(-1)!.worker.execute).toHaveBeenCalledTimes(1);
});

it("routes opaque leases to their own workspace and invalidates released tokens", async () => {
  const h = harness();
  const alice = (await h.request({ type: "acquire", owner: "alice", workspace: "/alice" }).result).value;
  const bob = (await h.request({ type: "acquire", owner: "bob", workspace: "/bob" }).result).value;
  expect(alice.token).not.toBe(bob.token);
  const updates: unknown[] = [];
  expect((await h.request({ type: "execute", token: alice.token, name: "read", parameters: { value: "own" } }, v => updates.push(v)).result).value)
    .toEqual({ workspace: "/alice", value: "own" });
  expect(updates).toEqual([{ workspace: "/alice" }]);
  await h.request({ type: "release", owner: "alice" }).result;
  expect((await h.request({ type: "assert", token: alice.token }).result).type).toBe("error");
  expect((await h.request({ type: "assert", token: bob.token }).result).type).toBe("result");
  const renewed = (await h.request({ type: "acquire", owner: "alice", workspace: "/alice" }).result).value;
  expect(renewed.token).not.toBe(alice.token);
});

it("cancels a streaming operation without closing another lease", async () => {
  const h = harness();
  const lease = (await h.request({ type: "acquire", owner: "alice", workspace: "/alice" }).result).value;
  const started = deferred<void>();
  const call = h.request({ type: "execute", token: lease.token, name: "bash", parameters: { wait: true } }, () => started.resolve());
  await started.promise;
  h.channel.emit("message", { type: "cancel", id: call.id });
  expect((await call.result).type).toBe("error");
  expect((await h.request({ type: "assert", token: lease.token }).result).type).toBe("result");
});

it("rejects arbitrary operations and emits no internal diagnostics", async () => {
  const item = worker("/alice");
  const h = harness(async () => item);
  const lease = (await h.request({ type: "acquire", owner: "alice", workspace: "/alice" }).result).value;
  for (const body of [
    { type: "execute", token: lease.token, name: "loadModule", parameters: { path: "/etc/private" } },
    { type: "execute", token: lease.token, name: "read", parameters: [] },
    { type: "execute", token: "unknown", name: "read", parameters: {} },
    { type: "provision", uid: 0 },
  ]) expect(await h.request(body).result).toMatchObject({ type: "error", code: "SUPERVISOR_OPERATION_FAILED" });
  expect(item.worker.execute).not.toHaveBeenCalled();
});

it("waits for a late worker after host disconnect", async () => {
  const start = deferred<SupervisedWorker>();
  const entered = deferred<void>();
  const item = worker("/alice");
  const h = harness(async () => { entered.resolve(); return start.promise; });
  h.request({ type: "acquire", owner: "alice", workspace: "/alice" });
  await entered.promise;
  h.channel.disconnect();
  let done = false;
  const closing = h.server.close().then(() => { done = true; });
  await Promise.resolve();
  expect(done).toBe(false);
  start.resolve(item);
  await closing;
  expect(item.worker.close).toHaveBeenCalledTimes(1);
});

it("permanently rejects new acquisitions after owner retirement", async () => {
  const h = harness();
  await h.request({ type: "acquire", owner: "alice", workspace: "/alice" }).result;
  await h.request({ type: "retire", owner: "alice" }).result;
  expect((await h.request({ type: "acquire", owner: "alice", workspace: "/alice" }).result).type).toBe("error");
});

it("bounds pending startup requests and closes on duplicate active request IDs", async () => {
  const start = deferred<SupervisedWorker>();
  const entered = deferred<void>();
  const item = worker("/alice");
  const h = harness(async () => { entered.resolve(); return start.promise; });
  const first = h.request({ type: "acquire", owner: "alice", workspace: "/alice" });
  await entered.promise;
  for (let i = 0; i < 3; i++) h.request({ type: "acquire", owner: "alice", workspace: "/alice" });
  expect(await h.request({ type: "acquire", owner: "bob", workspace: "/bob" }).result)
    .toMatchObject({ type: "error", code: "SUPERVISOR_REQUEST_LIMIT" });
  h.channel.emit("message", { type: "acquire", id: first.id, owner: "alice", workspace: "/alice" });
  start.resolve(item);
  await h.server.close();
  expect(h.channel.connected).toBe(false);
  expect(item.worker.close).toHaveBeenCalledTimes(1);
});
