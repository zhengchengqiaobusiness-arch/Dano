import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, expect, it, vi } from "vitest";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { withUserMemory, type UserMemoryRuntime, type UserMemoryServices } from "../user-memory-runtime.js";
import type { ProtectedSessionTools } from "../protected-session-tools.js";

const roots: string[] = [];
const profiles: ProtectedSessionTools[] = [];
afterEach(async () => {
  for (const profile of profiles.splice(0)) await profile.dispose?.();
  await Promise.all(roots.splice(0).map(root => rm(root, { recursive: true, force: true })));
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
    baseUrl: "https://memory.example.test", requestTimeoutMs: 100, shutdownTimeoutMs: 1000, policyVersion: "v1",
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
  const pi = { on: (name: string, handler: (...args: any[]) => any) => handlers.set(name, handler),
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
