import { afterEach, expect, it, vi } from "vitest";
import { LazyMemoryClient } from "../lazy-memory-client.js";

const owner = { accountId: "account", userId: "alice" };
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}
afterEach(() => vi.unstubAllGlobals());
function harness() {
  const connect = vi.fn(async () => ({ owner, apiKey: "synthetic-user-key" }));
  const isolation = vi.fn(async () => {});
  const fetch = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    if (String(input).endsWith("/health")) return Response.json({ auth_mode: "api_key", role: "user", account_id: owner.accountId, user_id: owner.userId });
    const body = JSON.parse(String(init?.body));
    return Response.json({ status: "ok", result: { session_id: body.session_id } });
  });
  vi.stubGlobal("fetch", fetch);
  const client = new LazyMemoryClient({ owner, baseUrl: "https://memory.example.test", timeoutMs: 1000,
    connect, assertToolIsolation: isolation });
  return { client, connect, isolation, fetch };
}

it("does no network work until used and shares one owner connection between callers", async () => {
  const h = harness();
  expect(h.connect).not.toHaveBeenCalled();
  expect(h.fetch).not.toHaveBeenCalled();
  await Promise.all([h.client.createSession("first"), h.client.createSession("second")]);
  expect(h.connect).toHaveBeenCalledTimes(1);
  expect(h.fetch).toHaveBeenCalledTimes(3); // One shared health verification plus two requests.
  expect(h.isolation).toHaveBeenCalledTimes(4);
  await h.client.close();
});

it("rejects a mismatched credential owner before making a request", async () => {
  const h = harness();
  h.connect.mockResolvedValue({ owner: { ...owner, userId: "bob" }, apiKey: "synthetic-other-key" });
  await expect(h.client.createSession("first")).rejects.toThrow("OWNER_MISMATCH");
  expect(h.fetch).not.toHaveBeenCalled();
  await h.client.close();
});

it("checks live isolation before connecting and again before dispatch", async () => {
  const h = harness();
  h.isolation.mockRejectedValueOnce(new Error("NOT_ISOLATED"));
  await expect(h.client.createSession("first")).rejects.toThrow("NOT_ISOLATED");
  expect(h.connect).not.toHaveBeenCalled();
  h.isolation.mockResolvedValueOnce().mockRejectedValueOnce(new Error("WORKER_DIED"));
  await expect(h.client.createSession("second")).rejects.toThrow("WORKER_DIED");
  expect(h.fetch).not.toHaveBeenCalled();
  await h.client.close();
});

it("allows a later request to retry unavailable identity initialization", async () => {
  const h = harness();
  h.connect.mockRejectedValueOnce(new Error("UNAVAILABLE"));
  await expect(h.client.createSession("first")).rejects.toThrow("UNAVAILABLE");
  await h.client.createSession("second");
  expect(h.connect).toHaveBeenCalledTimes(2);
  await h.client.close();
});

it("waits for a pending identity connection on close without sending a mutation", async () => {
  const h = harness();
  const pending = deferred<{ owner: typeof owner; apiKey: string }>();
  const entered = deferred<void>();
  h.connect.mockImplementation(() => { entered.resolve(); return pending.promise; });
  const request = expect(h.client.createSession("first")).rejects.toThrow("CLOSED");
  await entered.promise;
  let done = false;
  const closing = h.client.close().then(() => { done = true; });
  await Promise.resolve();
  expect(done).toBe(false);
  pending.resolve({ owner, apiKey: "synthetic-user-key" });
  await Promise.all([request, closing]);
  expect(h.fetch).not.toHaveBeenCalled();
  await expect(h.client.createSession("second")).rejects.toThrow("CLOSED");
});

it("waits for an already sent mutation and preserves its successful receipt", async () => {
  const h = harness();
  const result = deferred<Response>();
  const sent = deferred<void>();
  h.fetch.mockImplementation(async input => {
    if (String(input).endsWith("/health")) return Response.json({ auth_mode: "api_key", role: "user", account_id: owner.accountId, user_id: owner.userId });
    sent.resolve(); return result.promise;
  });
  const request = h.client.createSession("first");
  await sent.promise;
  let closed = false;
  const closing = h.client.close().then(() => { closed = true; });
  await Promise.resolve();
  expect(closed).toBe(false);
  result.resolve(Response.json({ status: "ok", result: { session_id: "first" } }));
  await request;
  await closing;
  expect(closed).toBe(true);
});

it("does not initialize credentials for an already cancelled recall", async () => {
  const h = harness();
  const cancelled = new AbortController();
  cancelled.abort(new Error("CANCELLED"));
  await expect(h.client.recall("private query", 3, cancelled.signal)).rejects.toThrow("CANCELLED");
  expect(h.connect).not.toHaveBeenCalled();
  expect(h.fetch).not.toHaveBeenCalled();
  await h.client.close();
});
