import { fork, spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { afterEach, expect, it, vi } from "vitest";
import { WorkerBrokerClient } from "../worker-broker-client.js";

const clients: WorkerBrokerClient[] = [];
afterEach(async () => { await Promise.all(clients.splice(0).map(client => client.close())); });
function harness(mode = "normal", overrides: Partial<ConstructorParameters<typeof WorkerBrokerClient>[1]> = {}) {
  const child = fork(fileURLToPath(new URL("./fixtures/worker-broker-child.mjs", import.meta.url)), [mode], {
    execArgv: [], stdio: ["ignore", "ignore", "ignore", "ipc"], env: { PATH: process.env.PATH },
  });
  const assertBrokerIdentity = vi.fn(async () => {});
  const options = { workspace: "/fixture", startupTimeoutMs: 3000, operationTimeoutMs: 1000,
    shutdownTimeoutMs: 100, maxConcurrentOperations: 4, maxResultBytes: 4096, assertBrokerIdentity, ...overrides };
  const client = new WorkerBrokerClient(child, options);
  clients.push(client);
  return { child, client, assertBrokerIdentity };
}

it("uses real child IPC for isolation checks, updates and results, then waits for shutdown", async () => {
  const h = harness();
  await h.client.assertIsolated();
  const updates: unknown[] = [];
  expect(await h.client.execute("read", { value: "fixture" }, undefined, value => updates.push(value))).toEqual({ value: "fixture" });
  expect(updates).toEqual([{ progress: true }]);
  expect(h.assertBrokerIdentity).toHaveBeenCalledTimes(2);
  await h.client.close();
  expect(h.child.exitCode !== null || h.child.signalCode !== null).toBe(true);
  await expect(h.client.execute("read", {})).rejects.toThrow("UNAVAILABLE");
});

it("cancels and times out individual operations while preserving the broker for later calls", async () => {
  const h = harness("normal", { operationTimeoutMs: 80 });
  await h.client.assertIsolated();
  const abort = new AbortController();
  const cancelled = h.client.execute("bash", { wait: 1000 }, abort.signal, () => abort.abort());
  await expect(cancelled).rejects.toThrow("CANCELLED");
  await expect(h.client.execute("bash", { wait: 1000 })).rejects.toThrow("TIMEOUT");
  expect(await h.client.execute("read", { value: "still alive" })).toEqual({ value: "still alive" });
});

it("rejects in-flight operations when a child dies and never reconnects", async () => {
  const h = harness();
  await expect(h.client.execute("bash", { crash: true })).rejects.toThrow("UNAVAILABLE");
  await h.client.close();
  expect(h.child.exitCode).toBe(23);
  await expect(h.client.assertIsolated()).rejects.toThrow("UNAVAILABLE");
});

it("bounds startup and kills a broker that ignores shutdown", async () => {
  const h = harness("silent", { startupTimeoutMs: 100 });
  await expect(h.client.assertIsolated()).rejects.toThrow("UNAVAILABLE");
  await h.client.close();
  expect(h.child.signalCode).toBe("SIGKILL");
});

it("cancels a caller waiting for startup without waiting for the startup deadline", async () => {
  const h = harness("silent", { startupTimeoutMs: 5000 });
  const controller = new AbortController();
  const result = h.client.execute("read", {}, controller.signal);
  controller.abort(new Error("cancelled before ready"));
  await expect(result).rejects.toThrow("cancelled before ready");
}, 2000);

it("rejects oversized requests and failed kernel identity checks before dispatch", async () => {
  const h = harness();
  await expect(h.client.execute("write", { value: "x".repeat(5000) })).rejects.toThrow("REQUEST_LIMIT");
  h.assertBrokerIdentity.mockRejectedValueOnce(new Error("identity changed"));
  await expect(h.client.execute("read", {})).rejects.toThrow("identity changed");
});

it("does not hang shutdown when process creation fails without an exit event", async () => {
  const child = spawn("/nonexistent/dano-worker-broker", [], { stdio: ["ignore", "ignore", "ignore", "ipc"] });
  const client = new WorkerBrokerClient(child, { workspace: "/fixture", startupTimeoutMs: 100,
    operationTimeoutMs: 100, shutdownTimeoutMs: 100, maxConcurrentOperations: 1, maxResultBytes: 4096,
    assertBrokerIdentity: async () => {} });
  clients.push(client);
  await expect(client.assertIsolated()).rejects.toThrow("UNAVAILABLE");
  await client.close();
});
