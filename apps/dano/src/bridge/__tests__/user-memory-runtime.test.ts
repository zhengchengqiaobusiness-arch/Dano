import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, expect, it, vi } from "vitest";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { withUserMemory, type UserMemoryRuntime, type UserMemoryServices } from "../user-memory-runtime.js";
import type { ProtectedSessionTools } from "../protected-session-tools.js";
import { FileStateStore } from "@josephyoung/pi-openviking/host";
import { LazyMemoryClient } from "../lazy-memory-client.js";

const roots: string[] = [];
const profiles: ProtectedSessionTools[] = [];
afterEach(async () => {
  for (const profile of profiles.splice(0)) await profile.dispose?.();
  await Promise.all(roots.splice(0).map(root => rm(root, { recursive: true, force: true })));
  vi.restoreAllMocks();
});
async function harness() {
  const root = await mkdtemp(join(tmpdir(), "dano-user-memory-")); roots.push(root);
  const context = { user: { id: "alice", username: "Alice" }, folderPath: join(root, "users/alice") };
  const workspace = join(context.folderPath, "workspaces/default");
  const worker = { workspace, assertIsolated: vi.fn(async () => {}), execute: vi.fn(async () => ({})) };
  const release = vi.fn(async () => {});
  const profile: ProtectedSessionTools = { agentDir: join(root, "agent"), memoryStateDirectory: join(root, "state"),
    trustedSkillPaths: [], resolveWorker: async () => worker, dispose: release };
  const services: UserMemoryServices = {
    owners: { get: vi.fn(async () => ({ accountId: "account", userId: "alice" })) },
    credentials: { read: vi.fn(async () => undefined), write: vi.fn(async () => {}) },
    provisioner: { provision: vi.fn(async () => { throw new Error("REMOTE_OFFLINE"); }), verifyUserKey: vi.fn(async () => {}) },
    baseUrl: "https://memory.example.test", requestTimeoutMs: 100, maxContentBytes: 1024, policyVersion: "v1",
    // Fixture-only counter. Production must supply the active model's tokenizer.
    policy: { maxPayloadBytes: 8192, recallTimeoutMs: 50, recallTokenBudget: 1000, recallLimit: 5,
      minimumScore: 0.5, countTokens: text => text.length },
    scheduler: { pollIntervalMs: 1000, initialBackoffMs: 1000, maxBackoffMs: 2000, maxAttemptsPerPhase: 2, maxOperationsPerTick: 2 },
  };
  let runtime: UserMemoryRuntime | undefined;
  const registered = vi.fn((_context, value: UserMemoryRuntime | undefined) => { runtime = value; });
  const create = withUserMemory(async () => profile, services, registered);
  async function start() { const bound = await create(context); profiles.push(bound); return bound; }
  return { context, workspace, worker, services, release, registered, create, start, runtime: () => runtime! };
}
function bind(profile: ProtectedSessionTools, worker: Awaited<ReturnType<ProtectedSessionTools["resolveWorker"]>>) {
  const handlers = new Map<string, (...args: any[]) => any>();
  const tools = new Map<string, any>();
  const pi = { on: (name: string, handler: (...args: any[]) => any) => handlers.set(name,
    (event, context = { model: { provider: "fixture", api: "openai-completions", id: "fixture-model" } }) => handler(event, context)),
    registerTool: (tool: any) => tools.set(tool.name, tool) } as unknown as ExtensionAPI;
  profile.createMemoryExtension!(worker.workspace, worker)(pi);
  return { handlers, tools };
}

it("keeps default-disabled sessions offline and does not expose private fields in status", async () => {
  const h = await harness();
  const profile = await h.start();
  const session = bind(profile, h.worker);
  const messages = [{ role: "user", content: "ordinary chat", timestamp: Date.now() }];
  session.handlers.get("before_agent_start")!({ prompt: "ordinary chat" });
  expect(await session.handlers.get("context")!({ messages })).toEqual({ messages });
  const result = await session.tools.get("memory_save").execute("call", { content: "fact" }, undefined, undefined, {
    sessionManager: { getBranch: () => [{ type: "message", id: "entry", timestamp: new Date().toISOString(), message: { role: "user" } }] },
  });
  expect(result.details).toEqual({ status: "blocked", errorCode: "MEMORY_DISABLED" });
  expect(h.services.credentials.read).not.toHaveBeenCalled();
  expect(h.services.provisioner.provision).not.toHaveBeenCalled();
  expect(await h.runtime().status()).toEqual({ enabled: false, automaticCollection: false,
    effectiveAt: expect.any(String), policyVersion: "v1", revision: 0 });
});

it("shares authorization between sessions and preserves ordinary context when the service is offline", async () => {
  const h = await harness();
  const profile = await h.start();
  const first = bind(profile, h.worker), second = bind(profile, h.worker);
  const messages = [{ role: "user", content: "query", timestamp: Date.now() }];
  await h.runtime().setEnabled(true);
  expect((await h.runtime().status()).automaticCollection).toBe(false);
  first.handlers.get("before_agent_start")!({ prompt: "query" });
  expect(await first.handlers.get("context")!({ messages })).toEqual({ messages });
  expect(h.services.provisioner.provision).toHaveBeenCalledTimes(1);
  await h.runtime().setEnabled(false);
  second.handlers.get("before_agent_start")!({ prompt: "query" });
  expect(await second.handlers.get("context")!({ messages })).toEqual({ messages });
  expect(h.services.provisioner.provision).toHaveBeenCalledTimes(1);
});

it("restores local authorization without needing management access", async () => {
  const h = await harness();
  const first = await h.start();
  await h.runtime().setEnabled(true);
  await first.dispose!();
  await h.start();
  expect((await h.runtime().status()).enabled).toBe(true);
  expect(h.services.provisioner.provision).not.toHaveBeenCalled();
  expect(h.services.credentials.read).not.toHaveBeenCalled();
});

it("gives anonymous users only their existing isolated tool profile", async () => {
  const h = await harness();
  const anonymous = await h.create({ ...h.context, user: { id: "guest" } });
  profiles.push(anonymous);
  expect(anonymous.createMemoryExtension).toBeUndefined();
  expect(h.services.owners.get).not.toHaveBeenCalled();
  expect(h.registered).not.toHaveBeenCalled();
});

it("reports persisted delivery phases and local provenance without exposing payloads or remote identifiers", async () => {
  const h = await harness();
  const profile = await h.start();
  const owner = await h.services.owners.get(h.context);
  const store = new FileStateStore({ owner, directory: join(profile.memoryStateDirectory!, "memory"), policyVersion: "v1" });
  const source = { sessionId: "local-session", entryId: "local-entry", branchId: "local-branch", contentVersion: "private-hash" };
  const createdAt = new Date().toISOString();
  for (const phase of ["queued", "session_unknown", "session_created", "message_unknown", "message_delivered",
    "commit_unknown", "processing", "ready", "failed", "blocked_by_pause", "blocked"] as const) {
    await store.transact(state => {
      state.operations.receipt = { id: "receipt", owner, source, scope: null, kind: "explicit", authorizationEpoch: 0,
        createdAt, updatedAt: createdAt, phase, remoteSessionId: "private-remote", taskId: "private-task",
        payload: "private-payload", nextAttemptAt: Number.MAX_SAFE_INTEGER };
    });
    expect(await h.runtime().operation("receipt")).toEqual({ id: "receipt", phase, createdAt, updatedAt: createdAt,
      source: { sessionId: "local-session", entryId: "local-entry", branchId: "local-branch" } });
  }
  expect(await h.runtime().operation("missing")).toBeUndefined();
  expect(await h.runtime().operation("__proto__")).toBeUndefined();
  expect(h.services.provisioner.provision).not.toHaveBeenCalled();
  const runtime = h.runtime();
  await profile.dispose!();
  await expect(runtime.operation("receipt")).rejects.toThrow("MEMORY_RUNTIME_CLOSED");
});

it("clears the host registration and releases the worker exactly once", async () => {
  const h = await harness();
  const profile = await h.start();
  const runtime = h.runtime();
  await Promise.all([profile.dispose!(), profile.dispose!()]);
  expect(h.registered).toHaveBeenLastCalledWith(h.context, undefined);
  expect(h.release).toHaveBeenCalledTimes(1);
  await expect(runtime.setEnabled(true)).rejects.toThrow("CLOSED");
  expect(() => profile.createMemoryExtension!(h.workspace, h.worker)).toThrow("CLOSED");
});

it("retains the user's worker until the remote response receipt has been persisted", async () => {
  const h = await harness();
  h.services.scheduler.pollIntervalMs = 5;
  const profile = await h.start();
  await h.runtime().setEnabled(true);
  const owner = await h.services.owners.get(h.context);
  const store = new FileStateStore({ owner, directory: join(profile.memoryStateDirectory!, "memory"), policyVersion: "v1" });
  let remoteResponded = false;
  let writingReceipt = false;
  let releaseWrite!: () => void;
  const heldWrite = new Promise<void>(resolve => { releaseWrite = resolve; });
  const transact = FileStateStore.prototype.transact;
  vi.spyOn(FileStateStore.prototype, "transact").mockImplementation(async function (this: FileStateStore, mutation) {
    if (remoteResponded) { writingReceipt = true; await heldWrite; }
    return transact.call(this, mutation);
  });
  vi.spyOn(LazyMemoryClient.prototype, "createSession").mockImplementation(async () => { remoteResponded = true; });
  const append = vi.spyOn(LazyMemoryClient.prototype, "append");
  const id = "a".repeat(64);
  let closing: Promise<void> | undefined;
  try {
    await store.transact(state => {
      state.operations[id] = { id, owner, scope: null, kind: "explicit", authorizationEpoch: state.authorization.epoch,
        source: { sessionId: "chat", entryId: "entry", branchId: "root", contentVersion: "1" },
        createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(), phase: "queued",
        remoteSessionId: "remote-session", payload: "fact" };
    });
    await vi.waitFor(() => expect(writingReceipt).toBe(true));
    closing = profile.dispose!();
    await new Promise(resolve => setTimeout(resolve, 20));
    expect(h.release).not.toHaveBeenCalled();
    expect((await store.read()).operations[id]!.phase).toBe("session_unknown");
    releaseWrite();
    await closing;
    expect((await store.read()).operations[id]!.phase).toBe("session_created");
    expect(h.release).toHaveBeenCalledTimes(1);
    expect(append).not.toHaveBeenCalled();
  } finally {
    releaseWrite();
    await closing;
  }
});

it("serializes repeated enables without creating multiple consent boundaries", async () => {
  const h = await harness();
  await h.start();
  await Promise.all([h.runtime().setEnabled(true), h.runtime().setEnabled(true)]);
  expect((await h.runtime().status()).revision).toBe(1);
});

it("rejects malformed settings without enabling memory", async () => {
  const h = await harness();
  await h.start();
  await expect(h.runtime().setEnabled("false" as unknown as boolean)).rejects.toThrow("INVALID_MEMORY_SETTING");
  expect((await h.runtime().status()).enabled).toBe(false);
});

async function contentHarness() {
  const h = await harness();
  const profile = await h.start();
  const owner = await h.services.owners.get(h.context);
  const store = new FileStateStore({ owner, directory: join(profile.memoryStateDirectory!, "memory"), policyVersion: "v1" });
  await store.transact(state => {
    state.operations.receipt = { id: "receipt", owner, scope: null, kind: "explicit", authorizationEpoch: 0,
      source: { sessionId: "session", entryId: "entry", branchId: "branch", contentVersion: "hash" },
      createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(), phase: "ready",
      remoteSessionId: "private-remote", memoryUris: ["viking://user/alice/memories/fact.md"] };
  });
  return { ...h, store };
}

it("reads only ready receipt references and projects content without remote identifiers", async () => {
  const h = await contentHarness();
  const read = vi.spyOn(LazyMemoryClient.prototype, "readMemory").mockResolvedValue("<script>quoted memory</script>");
  expect(await h.runtime().content("receipt", 0)).toEqual({ operationId: "receipt", index: 0, total: 1,
    text: "<script>quoted memory</script>" });
  expect(read).toHaveBeenCalledWith("viking://user/alice/memories/fact.md");
  for (const [id, index] of [["missing", 0], ["__proto__", 0], ["receipt", -1], ["receipt", 1]] as const) {
    expect(await h.runtime().content(id, index)).toBeUndefined();
  }
  await h.store.transact(state => { state.operations.receipt!.phase = "blocked"; });
  expect(await h.runtime().content("receipt", 0)).toBeUndefined();
  expect(read).toHaveBeenCalledTimes(1);
});

it("rejects content whose receipt is deleted during the remote read", async () => {
  const h = await contentHarness();
  vi.spyOn(LazyMemoryClient.prototype, "readMemory").mockImplementation(async () => {
    await h.store.transact(state => { delete state.operations.receipt; });
    return "stale deleted fact";
  });
  await expect(h.runtime().content("receipt", 0)).rejects.toThrow("MEMORY_CONTENT_CHANGED");
});

it("rejects oversized content instead of sending an unbounded body to the browser", async () => {
  const h = await contentHarness();
  vi.spyOn(LazyMemoryClient.prototype, "readMemory").mockResolvedValue("中".repeat(400));
  await expect(h.runtime().content("receipt", 0)).rejects.toThrow("MEMORY_CONTENT_TOO_LARGE");
});
