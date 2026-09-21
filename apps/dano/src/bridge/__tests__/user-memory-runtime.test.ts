import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, expect, it, vi } from "vitest";
import { SessionManager, type ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { withUserMemory, type UserMemoryRuntime, type UserMemoryServices } from "../user-memory-runtime.js";
import type { ProtectedSessionTools } from "../protected-session-tools.js";
import { FileStateStore, DeliveryScheduler, CollectionSessionRegistry, CollectionLifecycle, CollectionScheduler, MemoryDelivery } from "@josephyoung/pi-openviking/host";
import { LazyMemoryClient } from "../lazy-memory-client.js";
import { oauthUserId } from "../oauth-user-id.js";

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
  const sessionRoot = join(context.folderPath, "sessions");
  await mkdir(sessionRoot, { recursive: true, mode: 0o700 });
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
  async function start() { const bound = await create(context, { sessionsRootPath: sessionRoot }); profiles.push(bound); return bound; }
  return { context, workspace, sessionRoot, worker, profile, services, release, registered, create, start, runtime: () => runtime! };
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

it("does not record user provenance without a separate automatic-collection grant", async () => {
  const h = await harness();
  const profile = await h.start();
  const session = SessionManager.inMemory();
  for (const enabled of [false, true]) {
    await profile.memory!.setEnabled(enabled);
    await profile.captureMemoryInput!(session, "I prefer concise reports.", "I prefer concise reports.");
    session.appendMessage({ role: "user", content: "I prefer concise reports.", timestamp: Date.now() });
    h.runtime().provenance.settle(session);
    expect(session.getEntries().filter(entry => entry.type === "custom")).toHaveLength(0);
  }
});

it("bounds optional provenance authorization checks and shares stalled reads across prompts", async () => {
  // Isolate foreground capture reads from the independent delivery poller.
  vi.spyOn(DeliveryScheduler.prototype, "start").mockImplementation(() => {});
  const h = await harness();
  const profile = await h.start();
  const session = SessionManager.inMemory();
  const read = vi.spyOn(FileStateStore.prototype, "read").mockReturnValue(new Promise(() => {}));
  try {
    const cancellations = await Promise.all(["first", "second", "third"].map(text => profile.captureMemoryInput!(session, text, text)));
    expect(cancellations).toHaveLength(3);
    expect(read).toHaveBeenCalledOnce();
    session.appendMessage({ role: "user", content: "first", timestamp: Date.now() });
    h.runtime().provenance.settle(session);
    expect(session.getEntries().filter(entry => entry.type === "custom")).toHaveLength(0);
  } finally { read.mockRestore(); }
});

it("preserves isolated tools and damaged memory data when memory state cannot load", async () => {
  const h = await harness();
  const directory = join(h.profile.memoryStateDirectory!, "memory");
  await mkdir(directory, { recursive: true });
  const path = join(directory, "state.json");
  await writeFile(path, "{damaged", { mode: 0o600 });
  const profile = await h.start();
  expect(profile.memory).toBeUndefined();
  expect(profile.createMemoryExtension).toBeUndefined();
  expect(await profile.resolveWorker(h.workspace)).toBe(h.worker);
  expect(h.release).not.toHaveBeenCalled();
  expect(h.registered).not.toHaveBeenCalled();
  expect(await readFile(path, "utf8")).toBe("{damaged");
  expect(h.services.provisioner.provision).not.toHaveBeenCalled();
});

it("retains ordinary tools when the memory owner registry is unavailable", async () => {
  const h = await harness();
  vi.mocked(h.services.owners.get).mockRejectedValue(new Error("OWNER_STORE_UNREADABLE"));
  const profile = await h.start();
  expect(profile.memory).toBeUndefined();
  expect(await profile.resolveWorker(h.workspace)).toBe(h.worker);
  expect(h.release).not.toHaveBeenCalled();
});

it("still rejects and releases a profile when tool isolation cannot be verified", async () => {
  const h = await harness();
  h.worker.assertIsolated.mockRejectedValue(new Error("ISOLATION_FAILED"));
  await expect(h.start()).rejects.toThrow("ISOLATION_FAILED");
  expect(h.release).toHaveBeenCalledTimes(1);
  expect(h.services.owners.get).not.toHaveBeenCalled();
});

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

function configureCollection(services: UserMemoryServices) {
  services.collection = { policyVersion: "collection-v1", lifecycleTimeoutMs: 1000,
    selector: { timeoutMs: 1000, maxInputBytes: 8192, maxFacts: 5, complete: vi.fn(async () => '{"facts":[]}') },
    scheduler: { pollIntervalMs: 1000, mergeWindowMs: 20, maxWaitMs: 50, workTimeoutMs: 2000,
      leaseMs: 5000, initialBackoffMs: 50, maxBackoffMs: 100, maxAttempts: 2, maxRequestsPerBatch: 5 } };
}

it("binds provider fact capture to the authenticated runtime grant and disposal", async () => {
  const h = await harness(); configureCollection(h.services); h.context.user.id = oauthUserId("oa-alice");
  h.services.collection!.taskFacts = { key: Buffer.alloc(32, 9), config: { maxResponseBytes: 8192, maxFactBytes: 1024,
    contracts: [{ id: "report", method: "GET", path: "/report", success: { path: ["code"], equals: 0 },
      actorPath: ["data", "owner"], fields: [{ label: "reference", path: ["data", "reference"], type: "string" }] }] } };
  const profile = await h.start();
  const input = { toolName: "provider_request" as const, toolCallId: "call", loginSessionBound: true,
    request: { method: "GET", path: "/report" }, response: { ok: true as const, status: 200, headers: {},
      body: JSON.stringify({ code: 0, data: { owner: "oa-alice", reference: "REPORT-42" } }) } };
  expect(await profile.captureTaskFact!(input)).toBeUndefined();
  await profile.memory!.setEnabled(true);
  expect(await profile.captureTaskFact!(input)).toBeUndefined();
  await profile.memory!.setAutomaticCollection(true, "collection-v1");
  expect(await profile.captureTaskFact!(input)).toMatchObject({ data: expect.stringContaining("REPORT-42"), signature: expect.any(String) });
  await profile.memory!.setAutomaticCollection(false);
  expect(await profile.captureTaskFact!(input)).toBeUndefined();
  await profile.dispose!(); expect(await profile.captureTaskFact!(input)).toBeUndefined();
});

it("requires a separate current-policy grant and preserves its scope across pause/resume and revocation", async () => {
  const h = await harness(); configureCollection(h.services);
  const profile = await h.start();
  const owner = await h.services.owners.get(h.context);
  const store = new FileStateStore({ owner, directory: join(profile.memoryStateDirectory!, "memory"), policyVersion: "v1" });
  const session = SessionManager.create(h.workspace, h.sessionRoot);
  const sessions = new CollectionSessionRegistry({ store, sessionRoot: h.sessionRoot });
  await sessions.register(session);
  session.appendMessage({ role: "user", content: "Historical preference", timestamp: Date.now() });
  session.appendMessage({ role: "assistant", content: [{ type: "text", text: "Done." }], api: "openai-completions",
    provider: "fixture", model: "fixture", stopReason: "stop", timestamp: Date.now(),
    usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } } });
  await expect(h.runtime().setAutomaticCollection(true, "collection-v1")).rejects.toThrow("MEMORY_DISABLED");
  await h.runtime().setEnabled(true);
  await expect(h.runtime().setAutomaticCollection(true, "stale-policy")).rejects.toThrow("POLICY_CHANGED");
  expect((await h.runtime().status()).automaticCollection).toBe(false);
  await h.runtime().setAutomaticCollection(true, "collection-v1");
  const grant = (await store.read()).authorization.collectionConsent!;
  expect(grant).toMatchObject({ policyVersion: "collection-v1", scope: null,
    boundaries: [{ sessionId: session.getSessionId(), entryId: session.getLeafId(), branchId: session.getLeafId() }] });
  const status = await h.runtime().status();
  expect(status.collection).toEqual({ availablePolicyVersion: "collection-v1", consent: {
    policyVersion: grant.policyVersion, scope: null, effectiveAt: grant.effectiveAt, revision: grant.revision,
  } });
  expect(JSON.stringify(status)).not.toContain(session.getSessionId());
  await h.runtime().setEnabled(false);
  session.appendMessage({ role: "user", content: "Paused preference", timestamp: Date.now() });
  await h.runtime().setEnabled(true);
  const resumed = (await store.read()).authorization.collectionConsent!;
  expect(resumed.revision).toBe(grant.revision + 1);
  expect(resumed.boundaries[0]?.entryId).toBe(session.getLeafId());
  expect((await h.runtime().status()).automaticCollection).toBe(true);
  await h.runtime().setAutomaticCollection(false);
  expect(await h.runtime().status()).toMatchObject({ enabled: true, automaticCollection: false });
  await profile.dispose!();
  await h.start();
  expect(await h.runtime().status()).toMatchObject({ enabled: true, automaticCollection: false });
  expect(h.services.provisioner.provision).not.toHaveBeenCalled();
});

it("does not enable an unconfigured collector, but still allows withdrawal of old consent", async () => {
  const h = await harness();
  await h.start();
  await h.runtime().setEnabled(true);
  await expect(h.runtime().setAutomaticCollection(true, "v1")).rejects.toThrow("POLICY_CHANGED");
  await h.runtime().setAutomaticCollection(false);
  expect(await h.runtime().status()).toMatchObject({ enabled: true, automaticCollection: false });
  await expect(h.runtime().setAutomaticCollection("true" as unknown as boolean)).rejects.toThrow("INVALID_MEMORY_SETTING");
  await expect(h.runtime().setAutomaticCollection(true)).rejects.toThrow("INVALID_MEMORY_SETTING");
});

for (const boundary of ["credential", "isolation"] as const) it(`rechecks ${boundary} before background selection`, async () => {
  const h = await harness(); configureCollection(h.services);
  h.services.collection!.scheduler.pollIntervalMs = 10;
  h.services.collection!.scheduler.maxAttempts = 1;
  const profile = await h.start();
  const owner = await h.services.owners.get(h.context);
  const store = new FileStateStore({ owner, directory: join(profile.memoryStateDirectory!, "memory"), policyVersion: "v1" });
  const session = SessionManager.create(h.workspace, h.sessionRoot);
  await new CollectionSessionRegistry({ store, sessionRoot: h.sessionRoot }).register(session);
  await h.runtime().setEnabled(true);
  await h.runtime().setAutomaticCollection(true, "collection-v1");
  const lifecycle = new CollectionLifecycle(store);
  const request = await lifecycle.begin(session);
  const text = "My report title is synthetic-private-owner-value.";
  if (boundary === "credential") vi.mocked(h.services.credentials.read).mockResolvedValue("synthetic-private-owner-value");
  else h.worker.assertIsolated.mockRejectedValue(new Error("PRIVATE_ISOLATION_LOST"));
  h.runtime().provenance.capture(session, text, text);
  session.appendMessage({ role: "user", content: text, timestamp: Date.now() });
  session.appendMessage({ role: "assistant", content: [{ type: "text", text: "Done." }], api: "openai-completions",
    provider: "fixture", model: "fixture", stopReason: "stop", timestamp: Date.now(),
    usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } } });
  h.runtime().provenance.settle(session);
  await lifecycle.settle(request!, session);
  await expect.poll(async () => (await store.read()).collectionRequests![request!]!.phase,
    { timeout: 5000 }).toBe(boundary === "credential" ? "processed" : "selection_failed");
  expect(h.services.collection!.selector.complete).not.toHaveBeenCalled();
  expect(Object.values((await store.read()).operations)).toHaveLength(0);
  expect(h.services.provisioner.provision).not.toHaveBeenCalled();
});

for (const change of ["policy", "unconfigured"] as const) it(`invalidates obsolete collection consent before schedulers start: ${change}`, async () => {
  const deliveryStart = vi.spyOn(DeliveryScheduler.prototype, "start").mockImplementation(() => {});
  const collectionStart = vi.spyOn(CollectionScheduler.prototype, "start").mockImplementation(() => {});
  const h = await harness(); configureCollection(h.services);
  const first = await h.start();
  await h.runtime().setEnabled(true);
  await h.runtime().setAutomaticCollection(true, "collection-v1");
  const owner = await h.services.owners.get(h.context);
  const store = new FileStateStore({ owner, directory: join(first.memoryStateDirectory!, "memory"), policyVersion: "v1" });
  const delivery = new MemoryDelivery({ store, transport: { owner } as ConstructorParameters<typeof MemoryDelivery>[0]["transport"], maxPayloadBytes: 8192 });
  const auth = (await store.read()).authorization;
  const source = { sessionId: "chat", branchId: "branch", entryId: "entry", contentVersion: "v1", entryTimestamp: new Date().toISOString() };
  const automatic = await delivery.collect(source, "automatic fact", { epoch: auth.epoch, collectionRevision: auth.collectionConsent!.revision });
  const explicit = await delivery.save({ ...source, entryId: "explicit" }, "explicit fact");
  const inflight = await delivery.collect({ ...source, entryId: "inflight" }, "inflight fact", { epoch: auth.epoch, collectionRevision: auth.collectionConsent!.revision });
  if (!("id" in automatic) || !("id" in explicit) || !("id" in inflight)) throw new Error("FIXTURE_NOT_QUEUED");
  await store.transact(state => { state.operations[inflight.id]!.phase = "message_unknown"; });
  await first.dispose!();
  if (change === "policy") h.services.collection!.policyVersion = "collection-v2";
  else h.services.collection = undefined;
  deliveryStart.mockClear(); collectionStart.mockClear();
  let entered = false, release!: () => void;
  const fence = new Promise<void>(resolve => { release = resolve; });
  const revoke = MemoryDelivery.prototype.revokeCollection;
  vi.spyOn(MemoryDelivery.prototype, "revokeCollection").mockImplementationOnce(async function (this: MemoryDelivery) {
    entered = true; await fence; await revoke.call(this);
  });
  const creating = h.start();
  try {
    await vi.waitFor(() => expect(entered).toBe(true));
    expect(deliveryStart).not.toHaveBeenCalled();
    expect(collectionStart).not.toHaveBeenCalled();
  } finally { release(); }
  await creating;
  const state = await store.read();
  expect(state.authorization).toMatchObject({ enabled: true, automaticCollection: false });
  expect(state.operations[automatic.id]).toMatchObject({ phase: "blocked_by_pause" });
  expect(state.operations[automatic.id]!.payload).toBeUndefined();
  expect(state.operations[explicit.id]).toMatchObject({ phase: "queued", payload: "explicit fact" });
  expect(state.operations[inflight.id]).toMatchObject({ phase: "message_unknown", payload: "inflight fact" });
  expect(deliveryStart).toHaveBeenCalledOnce();
  if (change === "policy") {
    await h.runtime().setAutomaticCollection(true, "collection-v2");
    expect((await store.read()).authorization.collectionConsent!.revision).toBeGreaterThan(auth.collectionConsent!.revision);
    expect((await store.read()).operations[automatic.id]!.phase).toBe("blocked_by_pause");
  }
});
