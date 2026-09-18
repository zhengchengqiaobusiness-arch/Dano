import { EventEmitter } from "node:events";
import { expect, it, vi } from "vitest";
import { assertWorkerProviderApi, serveWorkerBroker } from "../worker-broker.js";

function harness() {
  const events = new EventEmitter();
  const channel = Object.assign(events, {
    connected: true,
    send: vi.fn((_value: object, callback: (error: Error | null) => void) => callback(null)),
    disconnect() { this.connected = false; events.emit("disconnect"); },
  });
  const worker = { workspace: "/fixture", assertIsolated: vi.fn(async () => {}), close: vi.fn(),
    execute: vi.fn(async (_name: string, _params: Record<string, unknown>, _signal?: AbortSignal, update?: (value: unknown) => void): Promise<unknown> => {
      update?.({ content: [{ type: "text", text: "progress" }] });
      return { content: [{ type: "text", text: "done" }] };
    }) };
  const close = serveWorkerBroker(worker, channel, { maxConcurrentOperations: 2, maxResultBytes: 4096 });
  return { channel, worker, close, messages: () => channel.send.mock.calls.map(([value]) => value) };
}
const settle = () => new Promise(resolve => setImmediate(resolve));

it("forwards fixed operations and updates only after checking worker isolation", async () => {
  const h = harness();
  h.channel.emit("message", { type: "execute", id: "call", name: "read", parameters: { path: "file" } });
  await settle();
  expect(h.worker.assertIsolated).toHaveBeenCalledOnce();
  expect(h.messages()).toEqual([{ type: "ready" },
    { type: "update", id: "call", value: { content: [{ type: "text", text: "progress" }] } },
    { type: "result", id: "call", value: { content: [{ type: "text", text: "done" }] } }]);
  h.close();
});

it("rejects arbitrary operations and never returns worker exception details", async () => {
  const h = harness();
  h.channel.emit("message", { type: "execute", id: "load", name: "import", parameters: { path: "malicious" } });
  expect(h.worker.execute).not.toHaveBeenCalled();
  h.worker.assertIsolated.mockRejectedValueOnce(new Error("synthetic-private-detail"));
  h.channel.emit("message", { type: "assert", id: "check" });
  await settle();
  expect(h.messages()).toContainEqual({ type: "error", id: "check", code: "WORKER_BROKER_OPERATION_FAILED" });
  expect(JSON.stringify(h.messages())).not.toContain("synthetic-private-detail");
  h.close();
});

it("aborts running work on cancellation and channel loss without publishing later results", async () => {
  const h = harness();
  const signals: AbortSignal[] = [];
  h.worker.execute.mockImplementation(async (_name, _params, signal) => {
    signals.push(signal!);
    await new Promise<void>(resolve => signal!.addEventListener("abort", () => resolve(), { once: true }));
    return "must not return";
  });
  h.channel.emit("message", { type: "execute", id: "a", name: "bash", parameters: {} });
  await settle();
  h.channel.emit("message", { type: "cancel", id: "a" });
  await settle();
  expect(signals[0].aborted).toBe(true);
  h.channel.emit("message", { type: "execute", id: "b", name: "bash", parameters: {} });
  await settle();
  h.channel.disconnect();
  await settle();
  expect(signals[1].aborted).toBe(true);
  expect(h.worker.close).toHaveBeenCalledOnce();
  expect(JSON.stringify(h.messages())).not.toContain("must not return");
});

it("closes the worker on oversized traffic and does not accept further calls", async () => {
  const h = harness();
  h.channel.emit("message", { type: "execute", id: "large", name: "read", parameters: { path: "x".repeat(5000) } });
  h.channel.emit("message", { type: "assert", id: "after" });
  await settle();
  expect(h.worker.close).toHaveBeenCalledOnce();
  expect(h.worker.assertIsolated).not.toHaveBeenCalled();
  expect(h.channel.connected).toBe(false);
});

it("requires an explicit supported provider capability instead of assuming unknown versions work", () => {
  for (const value of [null, {}, { protectedWorkerProviderApiVersion: 0 }, { protectedWorkerProviderApiVersion: "1" }]) {
    expect(() => assertWorkerProviderApi(value)).toThrow("PROTECTED_WORKER_PROVIDER_API_REQUIRED");
  }
  expect(() => assertWorkerProviderApi({ protectedWorkerProviderApiVersion: 1 })).not.toThrow();
});
