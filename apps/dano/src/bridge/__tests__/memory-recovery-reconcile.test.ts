import { mkdtemp, mkdir, readFile, realpath, rm, writeFile } from "node:fs/promises";
import { randomBytes } from "node:crypto";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, expect, it, vi } from "vitest";
import { FileStateStore, MemoryGovernanceBarrier, OwnerMemoryClient } from "@josephyoung/pi-openviking/host";
import { MemoryCredentialStore } from "../memory-credential-store.js";
import { MemoryRecoveryJournal, RecoveryStateStore } from "../memory-recovery-journal.js";
import { checkpoint, reconcile } from "../../../runtime/reconcile-memory-recovery.mjs";

const roots: string[] = [];
afterEach(async () => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  await Promise.all(roots.splice(0).map(root => rm(root, { recursive: true, force: true })));
});

async function fixture() {
  const root = await realpath(await mkdtemp(join(tmpdir(), "dano-recovery-reconcile-"))); roots.push(root);
  const data = join(root, "data"), recovery = join(root, "recovery"), config = join(root, "config");
  for (const directory of [data, recovery, config, join(data, "host-state"), join(config, "memory")]) {
    await mkdir(directory, { mode: 0o700 });
  }
  const owner = { accountId: "account", userId: "alice" };
  const stateRoot = join(data, "host-state", "owner-a", "state", "memory");
  const store = new FileStateStore({ owner, directory: stateRoot, policyVersion: "v1" });
  await store.transact(state => { state.authorization.enabled = false; });
  await MemoryRecoveryJournal.bootstrap(recovery, owner, await store.read());
  const journal = await MemoryRecoveryJournal.open(recovery, owner, await store.read());
  const encryptionKey = randomBytes(32);
  const credentialStore = new MemoryCredentialStore({
    directory: join(data, "host-state", "memory-service", "credentials"),
    encryptionKey, keyVersion: "v1",
  });
  await credentialStore.write(owner, "synthetic-user-key");
  await writeFile(join(config, "memory", "memory-service.json"), JSON.stringify({ accountId: "account",
    baseUrl: "http://localhost:1", requestTimeoutMs: 3000, encryptionKey: encryptionKey.toString("hex"),
    encryptionKeyVersion: "v1" }), { mode: 0o600 });
  return { root, data, recovery, config, owner, store, journal, credentialStore,
    checkpointFile: join(root, "checkpoint.json") };
}

it("checkpoints a stopped owner and preflights only the post-snapshot deletion intent", async () => {
  const f = await fixture();
  await expect(checkpoint(f.data, f.recovery, f.checkpointFile)).resolves.toEqual({ owners: 1, journalBytes: 0 });
  const manifest = JSON.parse(await readFile(f.checkpointFile, "utf8"));
  expect(manifest.owners[0].statePath).toBe("host-state/owner-a/state/memory/state.json");
  const uri = "viking://user/alice/memories/synthetic.md";
  await f.journal.append({ kind: "removeMemory", uri });
  await expect(reconcile(f.config, f.data, f.recovery, f.checkpointFile, true))
    .resolves.toEqual({ owners: 1, events: 1 });
  const removed = vi.spyOn(OwnerMemoryClient.prototype, "removeMemory").mockResolvedValue(undefined);
  vi.spyOn(OwnerMemoryClient.prototype, "verifyIdentity").mockResolvedValue(undefined);
  vi.spyOn(OwnerMemoryClient.prototype, "listMemoryDocuments").mockResolvedValue([]);
  await expect(reconcile(f.config, f.data, f.recovery, f.checkpointFile))
    .resolves.toEqual({ owners: 1, events: 1 });
  expect(removed).toHaveBeenCalledExactlyOnceWith(uri);
});

it("rejects a changed checkpoint prefix before contacting the remote service", async () => {
  const f = await fixture();
  await f.journal.append({ kind: "removeMemory", uri: "viking://user/alice/memories/old.md" });
  await checkpoint(f.data, f.recovery, f.checkpointFile);
  const bytes = await readFile(join(f.recovery, "account", "alice", "events.jsonl"), "utf8");
  await writeFile(join(f.recovery, "account", "alice", "events.jsonl"), bytes.replace("old.md", "new.md"));
  const contacted = vi.spyOn(OwnerMemoryClient.prototype, "verifyIdentity");
  await expect(reconcile(f.config, f.data, f.recovery, f.checkpointFile))
    .rejects.toThrow("RECOVERY_JOURNAL_CHECKPOINT_MISMATCH");
  expect(contacted).not.toHaveBeenCalled();
});

it("rejects a mismatched checkpoint after a successful replay has overlaid the state", async () => {
  const f = await fixture();
  await checkpoint(f.data, f.recovery, f.checkpointFile);
  const statePath = join(f.data, "host-state", "owner-a", "state", "memory", "state.json");
  const oldBytes = await readFile(statePath);
  await new RecoveryStateStore(f.store, f.journal).transact(state => { state.authorization.enabled = true; });
  // Simulate restoring the old local state before the first replay.
  const checkpointBytes = await readFile(f.checkpointFile);
  const manifest = JSON.parse(checkpointBytes.toString("utf8"));
  const previous = await readFile(join(f.recovery, "account", "alice", "state.json"));
  await writeFile(statePath, oldBytes);
  vi.spyOn(OwnerMemoryClient.prototype, "verifyIdentity").mockResolvedValue(undefined);
  vi.spyOn(OwnerMemoryClient.prototype, "listMemoryDocuments").mockResolvedValue([]);
  await reconcile(f.config, f.data, f.recovery, f.checkpointFile);
  expect(await readFile(statePath)).toEqual(previous);
  manifest.owners[0].stateSha256 = "0".repeat(64);
  await writeFile(f.checkpointFile, JSON.stringify(manifest), { mode: 0o600 });
  const contacted = vi.spyOn(OwnerMemoryClient.prototype, "verifyIdentity");
  contacted.mockClear();
  await expect(reconcile(f.config, f.data, f.recovery, f.checkpointFile))
    .rejects.toThrow("POST_SNAPSHOT_STATE_MISMATCH");
  expect(contacted).not.toHaveBeenCalled();
  await writeFile(f.checkpointFile, checkpointBytes, { mode: 0o600 });
  await rm(`${statePath}.replay-receipt.json`);
  await expect(reconcile(f.config, f.data, f.recovery, f.checkpointFile, true))
    .rejects.toThrow("POST_SNAPSHOT_STATE_MISMATCH");
});

it("rejects owners created after the backup checkpoint", async () => {
  const f = await fixture();
  await checkpoint(f.data, f.recovery, f.checkpointFile);
  const newer = { accountId: "account", userId: "bob" };
  const store = new FileStateStore({ owner: newer,
    directory: join(f.data, "host-state", "owner-b", "state", "memory"), policyVersion: "v1" });
  await store.transact(state => { state.authorization.enabled = false; });
  await MemoryRecoveryJournal.bootstrap(f.recovery, newer, await store.read());
  await expect(reconcile(f.config, f.data, f.recovery, f.checkpointFile, true))
    .rejects.toThrow("RECOVERY_OWNER_SET_MISMATCH");
});

it("preflights both owners before replaying either owner's deletion", async () => {
  const f = await fixture();
  const bob = { accountId: "account", userId: "bob" };
  const bobStore = new FileStateStore({ owner: bob,
    directory: join(f.data, "host-state", "owner-b", "state", "memory"), policyVersion: "v1" });
  await bobStore.transact(state => { state.authorization.enabled = false; });
  await MemoryRecoveryJournal.bootstrap(f.recovery, bob, await bobStore.read());
  const bobJournal = await MemoryRecoveryJournal.open(f.recovery, bob, await bobStore.read());
  await f.credentialStore.write(bob, "synthetic-bob-key");
  await checkpoint(f.data, f.recovery, f.checkpointFile);
  const original = await readFile(f.checkpointFile);
  const aliceUri = "viking://user/alice/memories/to-delete.md";
  const bobUri = "viking://user/bob/memories/to-delete.md";
  await f.journal.append({ kind: "removeMemory", uri: aliceUri });
  await bobJournal.append({ kind: "removeMemory", uri: bobUri });
  const verify = vi.spyOn(OwnerMemoryClient.prototype, "verifyIdentity").mockResolvedValue(undefined);
  const remove = vi.spyOn(OwnerMemoryClient.prototype, "removeMemory").mockResolvedValue(undefined);
  vi.spyOn(OwnerMemoryClient.prototype, "listMemoryDocuments").mockResolvedValue([]);
  const altered = JSON.parse(original.toString("utf8"));
  altered.owners.find((item: { owner: { userId: string } }) => item.owner.userId === "bob").stateSha256 = "0".repeat(64);
  await writeFile(f.checkpointFile, JSON.stringify(altered), { mode: 0o600 });
  await expect(reconcile(f.config, f.data, f.recovery, f.checkpointFile))
    .rejects.toThrow("POST_SNAPSHOT_STATE_MISMATCH");
  expect(verify).not.toHaveBeenCalled();
  expect(remove).not.toHaveBeenCalled();
  await writeFile(f.checkpointFile, original, { mode: 0o600 });
  await expect(reconcile(f.config, f.data, f.recovery, f.checkpointFile))
    .resolves.toEqual({ owners: 2, events: 2 });
  expect(remove).toHaveBeenCalledTimes(2);
  expect(remove).toHaveBeenCalledWith(aliceUri);
  expect(remove).toHaveBeenCalledWith(bobUri);
});

it("keeps the service stopped while a later governance barrier is unfinished", async () => {
  const f = await fixture();
  await checkpoint(f.data, f.recovery, f.checkpointFile);
  await new MemoryGovernanceBarrier(new RecoveryStateStore(f.store, f.journal))
    .begin({ kind: "clear", scope: null, supersedePending: true });
  await expect(reconcile(f.config, f.data, f.recovery, f.checkpointFile, true))
    .rejects.toThrow("RECOVERY_PENDING_GOVERNANCE");
});

it("keeps the restored state untouched until replay and readback succeed", async () => {
  const f = await fixture();
  await checkpoint(f.data, f.recovery, f.checkpointFile);
  const statePath = join(f.data, "host-state", "owner-a", "state", "memory", "state.json");
  const oldBytes = await readFile(statePath);
  await new RecoveryStateStore(f.store, f.journal).transact(state => { state.authorization.enabled = true; });
  await f.journal.append({ kind: "removeMemory", uri: "viking://user/alice/memories/old.md" });
  await writeFile(statePath, oldBytes);
  vi.spyOn(OwnerMemoryClient.prototype, "verifyIdentity").mockResolvedValue(undefined);
  const remove = vi.spyOn(OwnerMemoryClient.prototype, "removeMemory").mockRejectedValueOnce(new Error("REMOTE_DOWN"));
  vi.spyOn(OwnerMemoryClient.prototype, "listMemoryDocuments").mockResolvedValue([]);
  await expect(reconcile(f.config, f.data, f.recovery, f.checkpointFile)).rejects.toThrow("REMOTE_DOWN");
  expect(await readFile(statePath)).toEqual(oldBytes);
  remove.mockResolvedValue(undefined);
  await expect(reconcile(f.config, f.data, f.recovery, f.checkpointFile))
    .resolves.toEqual({ owners: 1, events: 1 });
  expect((await f.store.read()).revision).toBe(2);
});

it("replays a post-checkpoint source and document deletion through the real HTTP client", async () => {
  const f = await fixture();
  const uri = "viking://user/alice/memories/synthetic.md", sessionId = "synthetic_session";
  const documents = new Map([[uri, "synthetic old fact"]]), sessions = new Set([sessionId]);
  const notFound = () => Response.json({ status: "error", error: { code: "NOT_FOUND", message: "not found" } }, { status: 404 });
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
    const url = new URL(String(input));
    const method = init?.method ?? "GET";
    if (url.pathname === "/health") return Response.json({ auth_mode: "api_key", role: "user",
      account_id: f.owner.accountId, user_id: f.owner.userId });
    if (url.pathname === "/api/v1/fs/tree") return Response.json({ status: "ok", result:
      [...documents.keys()].map(item => ({ uri: item, isDir: false })) });
    if (url.pathname === "/api/v1/fs" && method === "DELETE") {
      documents.delete(url.searchParams.get("uri") ?? "");
      return Response.json({ status: "ok", result: {} });
    }
    if (url.pathname === "/api/v1/content/read") {
      const content = documents.get(url.searchParams.get("uri") ?? "");
      return content === undefined ? notFound() : Response.json({ status: "ok", result: content });
    }
    if (url.pathname === `/api/v1/sessions/${sessionId}` && method === "DELETE") {
      sessions.delete(sessionId);
      return Response.json({ status: "ok", result: {} });
    }
    if (url.pathname === `/api/v1/sessions/${sessionId}`) {
      return sessions.has(sessionId)
        ? Response.json({ status: "ok", result: { session_id: sessionId } }) : notFound();
    }
    throw new Error(`Unexpected route: ${method} ${url.pathname}`);
  }));
  await checkpoint(f.data, f.recovery, f.checkpointFile);
  const statePath = join(f.data, "host-state", "owner-a", "state", "memory", "state.json");
  const snapshot = await readFile(statePath);
  await new RecoveryStateStore(f.store, f.journal).transact(state => { state.authorization.enabled = true; });
  await f.journal.append({ kind: "removeSource", remoteSessionId: sessionId });
  await f.journal.append({ kind: "removeMemory", uri });
  // Restore the old local state and remote content as a stopped-stack fixture.
  await writeFile(statePath, snapshot);
  await expect(reconcile(f.config, f.data, f.recovery, f.checkpointFile))
    .resolves.toEqual({ owners: 1, events: 2 });
  expect(sessions.has(sessionId)).toBe(false);
  expect(documents.has(uri)).toBe(false);
  expect((await f.store.read()).revision).toBe(2);
  await expect(reconcile(f.config, f.data, f.recovery, f.checkpointFile))
    .resolves.toEqual({ owners: 1, events: 2 });
});
