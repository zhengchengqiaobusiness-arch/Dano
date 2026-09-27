import { appendFile, chmod, copyFile, link, mkdtemp, mkdir, readdir, readFile, realpath, rm, symlink, writeFile } from "node:fs/promises";
import { createHash, randomBytes, randomUUID } from "node:crypto";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, expect, it, vi } from "vitest";
import { FileStateStore, MemoryGovernanceBarrier, OwnerMemoryClient } from "@josephyoung/pi-openviking/host";
import { MemoryCredentialStore } from "../memory-credential-store.js";
import { MemoryRecoveryJournal, RecoveryStateStore } from "../memory-recovery-journal.js";
import { auditRetention, checkpoint, migrateLegacyRecovery, reconcile } from "../../../runtime/reconcile-memory-recovery.mjs";
import { privateDirectory, privateFile } from "../../../runtime/private-recovery-path.mjs";

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

it("rejects linked, exposed and oversized recovery inputs before reading them", async () => {
  const root = await realpath(await mkdtemp(join(tmpdir(), "dano-private-recovery-"))); roots.push(root);
  const secret = join(root, "secret.json");
  await writeFile(secret, "synthetic", { mode: 0o600 });
  await expect(privateFile(secret, 9)).resolves.toEqual(Buffer.from("synthetic"));
  await expect(privateFile(secret, 2)).rejects.toThrow("UNPROTECTED_RECOVERY_PATH");
  await symlink(secret, join(root, "symlink.json"));
  await expect(privateFile(join(root, "symlink.json"))).rejects.toThrow();
  await link(secret, join(root, "hardlink.json"));
  await expect(privateFile(secret)).rejects.toThrow("UNPROTECTED_RECOVERY_PATH");
  await rm(join(root, "hardlink.json"));
  await chmod(secret, 0o644);
  await expect(privateFile(secret)).rejects.toThrow("UNPROTECTED_RECOVERY_PATH");
  await symlink(root, join(root, "directory-link"));
  await expect(privateDirectory(join(root, "directory-link"))).rejects.toThrow("UNPROTECTED_RECOVERY_PATH");
});

it("rejects noncanonical ledger document URIs before any remote replay", async () => {
  const f = await fixture();
  const statePath = "host-state/owner-a/state/memory/state.json";
  const stateBytes = await readFile(join(f.data, statePath));
  const ledgerFile = join(f.root, "ledger.json");
  const appRoot = fileURLToPath(new URL("../../../", import.meta.url));
  const install = join(f.root, "installed");
  await mkdir(install, { mode: 0o700 });
  const script = join(install, "replay-memory-deletions.mjs");
  await copyFile(join(appRoot, "runtime", "replay-memory-deletions.mjs"), script);
  await copyFile(join(appRoot, "runtime", "private-recovery-path.mjs"), join(install, "private-recovery-path.mjs"));
  await symlink(join(appRoot, "dist"), join(install, "dist"));
  await symlink(join(appRoot, "node_modules"), join(install, "node_modules"));
  const base = { version: 1, owner: f.owner, statePath,
    postStateSha256: createHash("sha256").update(stateBytes).digest("hex"),
    deleteUris: [] as string[], sourceSessionIds: [] as string[],
    expectedDocumentUris: [] as string[], retainedDocuments: {} as Record<string, string> };
  const run = async (ledger: typeof base) => {
    await writeFile(ledgerFile, JSON.stringify(ledger), { mode: 0o600 });
    const result = spawnSync(process.execPath, [script, f.config, f.data, ledgerFile], { encoding: "utf8" });
    expect(result.status).toBe(1);
    return JSON.parse(result.stderr.trim()) as { stage: string; code: string };
  };
  expect((await run(base)).stage).toBe("replay");
  for (const uri of [
    "viking://user/alice/memories/../private.md",
    "viking://user/alice/memories/%2e%2e/private.md",
    "viking://user/alice/memories/.hidden.md",
    "viking://user/alice/memories/valid.md?other=1",
  ]) {
    expect(await run({ ...base, deleteUris: [uri] })).toEqual({ result: "failed", stage: "load", code: "INVALID_LEDGER" });
  }
  const escaped = "viking://user/alice/memories/../private.md";
  expect(await run({ ...base, expectedDocumentUris: [escaped] }))
    .toEqual({ result: "failed", stage: "load", code: "INVALID_LEDGER" });
  expect(await run({ ...base, expectedDocumentUris: [escaped], retainedDocuments: { [escaped]: "synthetic" } }))
    .toEqual({ result: "failed", stage: "load", code: "INVALID_LEDGER" });
});

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

it("preflights a checkpoint containing more than 256 owners", async () => {
  const f = await fixture();
  for (let index = 0; index < 256; index++) {
    const owner = { accountId: "account", userId: `user${index}` };
    const directory = join(f.data, "host-state", `owner-${index}`, "state", "memory");
    const store = new FileStateStore({ owner, directory, policyVersion: "v1" });
    await store.transact(state => { state.authorization.enabled = false; });
    await MemoryRecoveryJournal.bootstrap(f.recovery, owner, await store.read());
    await f.credentialStore.write(owner, "synthetic-user-key");
  }
  await expect(checkpoint(f.data, f.recovery, f.checkpointFile))
    .resolves.toEqual({ owners: 257, journalBytes: 0 });
  await expect(reconcile(f.config, f.data, f.recovery, f.checkpointFile, true))
    .resolves.toEqual({ owners: 257, events: 0 });
}, 30_000);

it("replays a deletion after pruning an obsolete correction body from an older checkpoint", async () => {
  const f = await fixture();
  await checkpoint(f.data, f.recovery, f.checkpointFile);
  const uri = "viking://user/alice/memories/forgotten.md";
  await f.journal.append({ kind: "replaceMemory", uri, content: "forgotten private fact" });
  await f.journal.append({ kind: "removeMemory", uri });
  const payloads = join(f.recovery, "account", "alice", "payloads");
  expect(await readFile(join(f.recovery, "account", "alice", "events.jsonl"), "utf8"))
    .not.toContain("forgotten private fact");
  expect(await readdir(payloads)).toEqual([]);
  await expect(reconcile(f.config, f.data, f.recovery, f.checkpointFile, true))
    .resolves.toEqual({ owners: 1, events: 2 });
  vi.spyOn(OwnerMemoryClient.prototype, "verifyIdentity").mockResolvedValue(undefined);
  const replace = vi.spyOn(OwnerMemoryClient.prototype, "replaceMemory");
  const remove = vi.spyOn(OwnerMemoryClient.prototype, "removeMemory").mockResolvedValue(undefined);
  vi.spyOn(OwnerMemoryClient.prototype, "listMemoryDocuments").mockResolvedValue([]);
  await expect(reconcile(f.config, f.data, f.recovery, f.checkpointFile))
    .resolves.toEqual({ owners: 1, events: 2 });
  expect(replace).not.toHaveBeenCalled();
  expect(remove).toHaveBeenCalledExactlyOnceWith(uri);
});

it("does not briefly restore a legacy inline correction before its later deletion", async () => {
  const f = await fixture();
  await checkpoint(f.data, f.recovery, f.checkpointFile);
  const uri = "viking://user/alice/memories/forgotten.md";
  const entry = { version: 1, id: randomUUID(), owner: f.owner,
    occurredAt: new Date().toISOString(),
    mutation: { kind: "replaceMemory", uri, content: "legacy forgotten fact" } };
  await writeFile(join(f.recovery, "account", "alice", "events.jsonl"), JSON.stringify(entry) + "\n", { mode: 0o600 });
  await f.journal.append({ kind: "removeMemory", uri });
  const replace = vi.spyOn(OwnerMemoryClient.prototype, "replaceMemory");
  const remove = vi.spyOn(OwnerMemoryClient.prototype, "removeMemory").mockResolvedValue(undefined);
  vi.spyOn(OwnerMemoryClient.prototype, "verifyIdentity").mockResolvedValue(undefined);
  vi.spyOn(OwnerMemoryClient.prototype, "listMemoryDocuments").mockResolvedValue([]);
  await expect(reconcile(f.config, f.data, f.recovery, f.checkpointFile))
    .resolves.toEqual({ owners: 1, events: 2 });
  expect(replace).not.toHaveBeenCalled();
  expect(remove).toHaveBeenCalledExactlyOnceWith(uri);
});

it("replays the current correction from its private payload after an older checkpoint", async () => {
  const f = await fixture();
  await checkpoint(f.data, f.recovery, f.checkpointFile);
  const uri = "viking://user/alice/memories/current.md";
  await f.journal.append({ kind: "replaceMemory", uri, content: "current private fact" });
  await expect(reconcile(f.config, f.data, f.recovery, f.checkpointFile, true))
    .resolves.toEqual({ owners: 1, events: 1 });
  vi.spyOn(OwnerMemoryClient.prototype, "verifyIdentity").mockResolvedValue(undefined);
  const replace = vi.spyOn(OwnerMemoryClient.prototype, "replaceMemory").mockResolvedValue(undefined);
  vi.spyOn(OwnerMemoryClient.prototype, "listMemoryDocuments").mockResolvedValue([uri]);
  vi.spyOn(OwnerMemoryClient.prototype, "readMemory").mockResolvedValue("current private fact");
  await expect(reconcile(f.config, f.data, f.recovery, f.checkpointFile))
    .resolves.toEqual({ owners: 1, events: 1 });
  expect(replace).toHaveBeenCalledExactlyOnceWith(uri, "current private fact");
});

it("audits legacy inline bodies and live payloads without returning private content", async () => {
  const f = await fixture();
  const uri = "viking://user/alice/memories/retention.md";
  await f.journal.append({ kind: "replaceMemory", uri, content: "synthetic private fact" });
  expect(await auditRetention(f.recovery)).toEqual({ owners: 1, legacyInlineBodies: 0,
    activePayloads: 1, prunedPayloadReferences: 0, needsMigration: false });
  await f.journal.append({ kind: "removeMemory", uri });
  const entry = { version: 1, id: randomUUID(), owner: f.owner,
    occurredAt: new Date().toISOString(), mutation: { kind: "replaceMemory", uri, content: "legacy private fact" } };
  await appendFile(join(f.recovery, "account", "alice", "events.jsonl"), JSON.stringify(entry) + "\n");
  const result = await auditRetention(f.recovery);
  expect(result).toEqual({ owners: 1, legacyInlineBodies: 1,
    activePayloads: 0, prunedPayloadReferences: 1, needsMigration: true });
  expect(JSON.stringify(result)).not.toMatch(/alice|legacy private fact|retention\.md/);
});

it("moves legacy inline bodies into a new checkpoint generation without retaining forgotten text", async () => {
  const f = await fixture();
  const oldFile = join(f.recovery, "account", "alice", "events.jsonl");
  const forgotten = "viking://user/alice/memories/forgotten.md";
  const retained = "viking://user/alice/memories/retained.md";
  const current = "viking://user/alice/memories/current.md";
  const legacy = (uri: string, content: string) => ({ version: 1, id: randomUUID(), owner: f.owner,
    occurredAt: new Date().toISOString(), mutation: { kind: "replaceMemory", uri, content } });
  await writeFile(oldFile, [legacy(forgotten, "deleted private body"),
    legacy(retained, "current private body")].map(entry => JSON.stringify(entry) + "\n").join(""), { mode: 0o600 });
  await f.journal.append({ kind: "removeMemory", uri: forgotten });
  await f.journal.append({ kind: "replaceMemory", uri: current, content: "new format body" });
  const oldBytes = await readFile(oldFile);
  await checkpoint(f.data, f.recovery, f.checkpointFile);
  const target = join(f.root, "fresh-recovery"), nextCheckpoint = join(f.root, "fresh-checkpoint.json");
  await mkdir(target, { mode: 0o700 });
  vi.spyOn(OwnerMemoryClient.prototype, "verifyIdentity").mockResolvedValue(undefined);
  vi.spyOn(OwnerMemoryClient.prototype, "listMemoryDocuments").mockResolvedValue([retained, current]);
  vi.spyOn(OwnerMemoryClient.prototype, "readMemory").mockImplementation(async uri => {
    if (uri === retained) return "current private body";
    if (uri === current) return "new format body";
    throw new Error("UNEXPECTED_READ");
  });
  await expect(migrateLegacyRecovery(f.config, f.data, f.recovery, target, nextCheckpoint))
    .resolves.toMatchObject({ owners: 1, legacyInlineBodies: 2, retiredBodies: 1, activePayloads: 2 });
  expect(await readFile(oldFile)).toEqual(oldBytes);
  const newFile = join(target, "account", "alice", "events.jsonl");
  expect(await readFile(newFile, "utf8")).not.toMatch(/deleted private body|current private body|new format body/);
  expect(await readFile(newFile, "utf8")).not.toContain(
    createHash("sha256").update("deleted private body").digest("hex"));
  expect(await readdir(join(target, "account", "alice", "payloads"))).toHaveLength(2);
  expect((await MemoryRecoveryJournal.inspect(target, f.owner)).events.map(event => event.mutation))
    .toEqual([expect.objectContaining({ kind: "replaceMemory", uri: forgotten }),
      expect.objectContaining({ kind: "replaceMemory", uri: retained, content: "current private body" }),
      { kind: "removeMemory", uri: forgotten },
      expect.objectContaining({ kind: "replaceMemory", uri: current, content: "new format body" })]);
  expect(await auditRetention(target)).toEqual({ owners: 1, legacyInlineBodies: 0,
    activePayloads: 2, prunedPayloadReferences: 1, needsMigration: false });
  await expect(reconcile(f.config, f.data, target, nextCheckpoint, true))
    .resolves.toEqual({ owners: 1, events: 0 });
  await expect(reconcile(f.config, f.data, target, f.checkpointFile, true))
    .rejects.toThrow("RECOVERY_JOURNAL_CHECKPOINT_MISMATCH");
  await expect(migrateLegacyRecovery(f.config, f.data, f.recovery, target, join(f.root, "another-checkpoint.json")))
    .rejects.toThrow("RECOVERY_MIGRATION_TARGET_NOT_EMPTY");
});

it("does not re-anchor a deletion that is still visible on the real-service boundary", async () => {
  const f = await fixture();
  const uri = "viking://user/alice/memories/forgotten.md";
  const entry = { version: 1, id: randomUUID(), owner: f.owner,
    occurredAt: new Date().toISOString(), mutation: { kind: "replaceMemory", uri, content: "deleted private body" } };
  await writeFile(join(f.recovery, "account", "alice", "events.jsonl"), JSON.stringify(entry) + "\n", { mode: 0o600 });
  await f.journal.append({ kind: "removeMemory", uri });
  const target = join(f.root, "fresh-recovery");
  await mkdir(target, { mode: 0o700 });
  vi.spyOn(OwnerMemoryClient.prototype, "verifyIdentity").mockResolvedValue(undefined);
  vi.spyOn(OwnerMemoryClient.prototype, "listMemoryDocuments").mockResolvedValue([uri]);
  await expect(migrateLegacyRecovery(f.config, f.data, f.recovery, target,
    join(f.root, "fresh-checkpoint.json"))).rejects.toThrow("MEMORY_DELETION_UNCONFIRMED");
  expect(await readdir(target)).toEqual([]);
});

it("preflights every source owner before creating migrated owner files", async () => {
  const f = await fixture();
  const bob = { accountId: "account", userId: "bob" };
  const bobStore = new FileStateStore({ owner: bob,
    directory: join(f.data, "host-state", "owner-b", "state", "memory"), policyVersion: "v1" });
  await bobStore.transact(state => { state.authorization.enabled = false; });
  await MemoryRecoveryJournal.bootstrap(f.recovery, bob, await bobStore.read());
  const aliceFile = join(f.recovery, "account", "alice", "events.jsonl");
  const entry = { version: 1, id: randomUUID(), owner: f.owner,
    occurredAt: new Date().toISOString(), mutation: { kind: "replaceMemory",
      uri: "viking://user/alice/memories/a.md", content: "legacy body" } };
  await writeFile(aliceFile, JSON.stringify(entry) + "\n", { mode: 0o600 });
  const target = join(f.root, "fresh-recovery");
  await mkdir(target, { mode: 0o700 });
  await writeFile(join(f.recovery, "account", "bob", "events.jsonl"), "{invalid}\n", { mode: 0o600 });
  await expect(migrateLegacyRecovery(f.config, f.data, f.recovery, target,
    join(f.root, "fresh-checkpoint.json")))
    .rejects.toThrow();
  expect(await readdir(target)).toEqual([]);
  expect(await readFile(aliceFile, "utf8")).toContain("legacy body");
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

it("rejects a changed old writer source before contacting the remote service", async () => {
  const f = await fixture();
  const id = "a".repeat(64);
  await new RecoveryStateStore(f.store, f.journal).transact(state => {
    state.operations[id] = { id, owner: f.owner, scope: null, kind: "explicit",
      authorizationEpoch: state.authorization.epoch,
      source: { sessionId: "chat", entryId: "original", branchId: "root", contentVersion: "1" },
      createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(),
      phase: "queued", remoteSessionId: "remote-session", payload: "synthetic fact" };
  });
  await checkpoint(f.data, f.recovery, f.checkpointFile);
  const statePath = join(f.data, "host-state", "owner-a", "state", "memory", "state.json");
  const oldBytes = await readFile(statePath);
  await new RecoveryStateStore(f.store, f.journal).transact(state => {
    state.operations[id]!.source.entryId = "different";
  });
  await writeFile(statePath, oldBytes);
  const contacted = vi.spyOn(OwnerMemoryClient.prototype, "verifyIdentity");
  await expect(reconcile(f.config, f.data, f.recovery, f.checkpointFile))
    .rejects.toThrow("RECOVERY_WRITER_RECONCILIATION_REQUIRED");
  expect(contacted).not.toHaveBeenCalled();
});

it("accepts an old queued writer that was explicitly blocked after the checkpoint", async () => {
  const f = await fixture();
  const id = "b".repeat(64);
  await new RecoveryStateStore(f.store, f.journal).transact(state => {
    state.operations[id] = { id, owner: f.owner, scope: null, kind: "explicit",
      authorizationEpoch: state.authorization.epoch,
      source: { sessionId: "chat", entryId: "original", branchId: "root", contentVersion: "1" },
      createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(),
      phase: "queued", remoteSessionId: "remote-session", payload: "synthetic fact" };
  });
  await checkpoint(f.data, f.recovery, f.checkpointFile);
  const statePath = join(f.data, "host-state", "owner-a", "state", "memory", "state.json");
  const oldBytes = await readFile(statePath);
  await new RecoveryStateStore(f.store, f.journal).transact(state => {
    state.operations[id]!.phase = "blocked";
    state.operations[id]!.errorCode = "MEMORY_DISABLED";
    state.operations[id]!.updatedAt = new Date().toISOString();
    delete state.operations[id]!.payload;
  });
  await writeFile(statePath, oldBytes);
  await expect(reconcile(f.config, f.data, f.recovery, f.checkpointFile, true))
    .resolves.toEqual({ owners: 1, events: 0 });
});

it("restores a new queued writer and an absent session_unknown writer after the checkpoint", async () => {
  const stage = async (phase: "queued" | "session_unknown" | "blocked", payload?: string) => {
    const f = await fixture();
    await checkpoint(f.data, f.recovery, f.checkpointFile);
    const statePath = join(f.data, "host-state", "owner-a", "state", "memory", "state.json");
    const oldBytes = await readFile(statePath);
    await new RecoveryStateStore(f.store, f.journal).transact(state => {
      state.operations[id] = { id, owner: f.owner, scope: null, kind: "explicit",
        authorizationEpoch: state.authorization.epoch,
        source: { sessionId: "chat", entryId: "new", branchId: "root", contentVersion: "1" },
        createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(),
        phase, remoteSessionId: "new-remote-session", payload };
    });
    await writeFile(statePath, oldBytes);
    return { ...f, statePath, oldBytes };
  };
  const id = "c".repeat(64);
  const f = await stage("queued", "new synthetic fact");
  await expect(reconcile(f.config, f.data, f.recovery, f.checkpointFile, true))
    .resolves.toEqual({ owners: 1, events: 0 });
  vi.spyOn(OwnerMemoryClient.prototype, "verifyIdentity").mockResolvedValue(undefined);
  vi.spyOn(OwnerMemoryClient.prototype, "listMemoryDocuments").mockResolvedValue([]);
  await reconcile(f.config, f.data, f.recovery, f.checkpointFile);
  expect((await f.store.read()).operations[id]).toMatchObject({ phase: "queued", payload: "new synthetic fact" });

  vi.restoreAllMocks();
  const second = await stage("session_unknown", "new synthetic fact");
  await expect(reconcile(second.config, second.data, second.recovery, second.checkpointFile, true))
    .resolves.toEqual({ owners: 1, events: 0, remoteWriterChecks: 1 });
  vi.spyOn(OwnerMemoryClient.prototype, "verifyIdentity").mockResolvedValue(undefined);
  const absent = vi.spyOn(OwnerMemoryClient.prototype, "sessionExists").mockResolvedValue(false);
  vi.spyOn(OwnerMemoryClient.prototype, "listMemoryDocuments").mockResolvedValue([]);
  await reconcile(second.config, second.data, second.recovery, second.checkpointFile);
  expect(absent).toHaveBeenCalledExactlyOnceWith("new-remote-session");
  expect((await second.store.read()).operations[id]).toMatchObject({
    phase: "session_unknown", payload: "new synthetic fact" });

  vi.restoreAllMocks();
  const present = await stage("session_unknown", "new synthetic fact");
  await present.journal.append({ kind: "removeMemory", uri: "viking://user/alice/memories/old.md" });
  vi.spyOn(OwnerMemoryClient.prototype, "verifyIdentity").mockResolvedValue(undefined);
  const exists = vi.spyOn(OwnerMemoryClient.prototype, "sessionExists").mockResolvedValue(true);
  const remove = vi.spyOn(OwnerMemoryClient.prototype, "removeMemory");
  const readback = vi.spyOn(OwnerMemoryClient.prototype, "listMemoryDocuments");
  await expect(reconcile(present.config, present.data, present.recovery, present.checkpointFile))
    .rejects.toThrow("RECOVERY_WRITER_RECONCILIATION_REQUIRED");
  expect(exists).toHaveBeenCalledExactlyOnceWith("new-remote-session");
  expect(remove).not.toHaveBeenCalled();
  expect(readback).not.toHaveBeenCalled();
  expect(await readFile(present.statePath)).toEqual(present.oldBytes);

  vi.restoreAllMocks();
  const missingPayload = await stage("queued");
  const contacted = vi.spyOn(OwnerMemoryClient.prototype, "verifyIdentity");
  await expect(reconcile(missingPayload.config, missingPayload.data, missingPayload.recovery,
    missingPayload.checkpointFile, true)).rejects.toThrow("RECOVERY_WRITER_RECONCILIATION_REQUIRED");
  expect(contacted).not.toHaveBeenCalled();
  const unsupportedPhase = await stage("blocked", "new synthetic fact");
  await expect(reconcile(unsupportedPhase.config, unsupportedPhase.data, unsupportedPhase.recovery,
    unsupportedPhase.checkpointFile, true)).rejects.toThrow("RECOVERY_WRITER_RECONCILIATION_REQUIRED");
  expect(contacted).not.toHaveBeenCalled();
});

const laterWriterPhases = ["session_created", "message_unknown", "message_delivered",
  "commit_unknown", "processing", "ready"] as const;
type LaterWriterPhase = typeof laterWriterPhases[number];

async function stagedLaterWriter(phase: LaterWriterPhase | "session_unknown") {
  const f = await fixture();
  await checkpoint(f.data, f.recovery, f.checkpointFile);
  const statePath = join(f.data, "host-state", "owner-a", "state", "memory", "state.json");
  const oldBytes = await readFile(statePath);
  const id = "d".repeat(64);
  const memoryUri = "viking://user/alice/memories/current.md";
  await new RecoveryStateStore(f.store, f.journal).transact(state => {
    state.operations[id] = { id, owner: f.owner, scope: null, kind: "explicit",
      authorizationEpoch: state.authorization.epoch,
      source: { sessionId: "chat", entryId: "new", branchId: "root", contentVersion: "1" },
      createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(),
      phase, remoteSessionId: "new-remote-session",
      ...(phase === "ready" ? { taskId: "task", archiveId: "archive", memoryUris: [memoryUri] }
        : { payload: "new synthetic fact", ...(phase === "processing" ? { taskId: "task" } : {}) }) };
  });
  await writeFile(statePath, oldBytes);
  await f.journal.append({ kind: "removeMemory", uri: "viking://user/alice/memories/old.md" });
  return { ...f, id, statePath, oldBytes, memoryUri };
}

it("rejects a clear followed by a new ready writer before any remote mutation", async () => {
  const f = await fixture();
  await checkpoint(f.data, f.recovery, f.checkpointFile);
  const statePath = join(f.data, "host-state", "owner-a", "state", "memory", "state.json");
  const snapshot = await readFile(statePath);
  await f.journal.append({ kind: "clearMemoryScope" });
  const id = "f".repeat(64);
  const uri = "viking://user/alice/memories/after-clear.md";
  await new RecoveryStateStore(f.store, f.journal).transact(state => {
    state.operations[id] = { id, owner: f.owner, scope: null, kind: "explicit",
      authorizationEpoch: state.authorization.epoch,
      source: { sessionId: "chat", entryId: "after-clear", branchId: "root", contentVersion: "1" },
      createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(),
      phase: "ready", remoteSessionId: "after-clear-session", taskId: "task",
      archiveId: "archive", memoryUris: [uri] };
  });
  await writeFile(statePath, snapshot);
  const verify = vi.spyOn(OwnerMemoryClient.prototype, "verifyIdentity");
  const clear = vi.spyOn(OwnerMemoryClient.prototype, "clearMemoryScope");
  await expect(reconcile(f.config, f.data, f.recovery, f.checkpointFile, true))
    .rejects.toThrow("RECOVERY_WRITER_RECONCILIATION_REQUIRED");
  await expect(reconcile(f.config, f.data, f.recovery, f.checkpointFile))
    .rejects.toThrow("RECOVERY_WRITER_RECONCILIATION_REQUIRED");
  expect(verify).not.toHaveBeenCalled();
  expect(clear).not.toHaveBeenCalled();
  expect(await readFile(statePath)).toEqual(snapshot);
});

for (const phase of ["session_unknown", "session_created", "message_unknown", "message_delivered"] as const) {
  it(`preserves a new ${phase} session across a post-checkpoint memory clear only without a commit`, async () => {
    const f = await stagedLaterWriter(phase);
    await f.journal.append({ kind: "clearMemoryScope" });
    vi.spyOn(OwnerMemoryClient.prototype, "verifyIdentity").mockResolvedValue(undefined);
    vi.spyOn(OwnerMemoryClient.prototype, "sessionExists").mockResolvedValue(phase !== "session_unknown");
    vi.spyOn(OwnerMemoryClient.prototype, "hasSource")
      .mockResolvedValue(phase !== "session_created");
    const commit = vi.spyOn(OwnerMemoryClient.prototype, "findCommit").mockResolvedValue(null);
    vi.spyOn(OwnerMemoryClient.prototype, "removeMemory").mockResolvedValue(undefined);
    const clear = vi.spyOn(OwnerMemoryClient.prototype, "clearMemoryScope").mockResolvedValue(undefined);
    vi.spyOn(OwnerMemoryClient.prototype, "listMemoryDocuments").mockResolvedValue([]);
    await expect(reconcile(f.config, f.data, f.recovery, f.checkpointFile))
      .resolves.toEqual({ owners: 1, events: 2, remoteWriterChecks: 1 });
    expect(commit).toHaveBeenCalledTimes(phase === "session_unknown" ? 0 : 1);
    if (phase !== "session_unknown") expect(commit).toHaveBeenCalledWith("new-remote-session");
    expect(clear).toHaveBeenCalledOnce();
    expect((await f.store.read()).operations[f.id]?.phase).toBe(phase);
  });
}

it("does not clear a new message writer if an unrecorded remote commit exists", async () => {
  const f = await stagedLaterWriter("message_delivered");
  await f.journal.append({ kind: "clearMemoryScope" });
  vi.spyOn(OwnerMemoryClient.prototype, "verifyIdentity").mockResolvedValue(undefined);
  vi.spyOn(OwnerMemoryClient.prototype, "hasSource").mockResolvedValue(true);
  vi.spyOn(OwnerMemoryClient.prototype, "findCommit").mockResolvedValue({ taskId: "remote-task" });
  const remove = vi.spyOn(OwnerMemoryClient.prototype, "removeMemory");
  const clear = vi.spyOn(OwnerMemoryClient.prototype, "clearMemoryScope");
  await expect(reconcile(f.config, f.data, f.recovery, f.checkpointFile))
    .rejects.toThrow("RECOVERY_WRITER_RECONCILIATION_REQUIRED");
  expect(remove).not.toHaveBeenCalled();
  expect(clear).not.toHaveBeenCalled();
  expect(await readFile(f.statePath)).toEqual(f.oldBytes);
});

for (const phase of laterWriterPhases) {
  it(`overlays a new ${phase} writer only after its remote receipt is verified`, async () => {
    const f = await stagedLaterWriter(phase);
    const contacted = vi.spyOn(OwnerMemoryClient.prototype, "verifyIdentity").mockResolvedValue(undefined);
    await expect(reconcile(f.config, f.data, f.recovery, f.checkpointFile, true))
      .resolves.toEqual({ owners: 1, events: 1, remoteWriterChecks: 1 });
    expect(contacted).not.toHaveBeenCalled();
    vi.spyOn(OwnerMemoryClient.prototype, "sessionExists").mockResolvedValue(true);
    const source = vi.spyOn(OwnerMemoryClient.prototype, "hasSource")
      .mockResolvedValue(phase !== "session_created");
    vi.spyOn(OwnerMemoryClient.prototype, "findCommit").mockResolvedValue({ taskId: "task" });
    const settled = vi.spyOn(OwnerMemoryClient.prototype, "writerSettled").mockResolvedValue(true);
    const inspect = vi.spyOn(OwnerMemoryClient.prototype, "inspect")
      .mockResolvedValue({ status: "ready", archiveId: "archive", memoryUris: [f.memoryUri] });
    const remove = vi.spyOn(OwnerMemoryClient.prototype, "removeMemory").mockResolvedValue(undefined);
    vi.spyOn(OwnerMemoryClient.prototype, "listMemoryDocuments").mockResolvedValue([]);
    await reconcile(f.config, f.data, f.recovery, f.checkpointFile);
    expect(source).toHaveBeenCalledOnce();
    expect(settled).toHaveBeenCalledTimes(["commit_unknown", "processing", "ready"].includes(phase) ? 1 : 0);
    expect(inspect).toHaveBeenCalledTimes(phase === "ready" ? 1 : 0);
    expect(remove).toHaveBeenCalledExactlyOnceWith("viking://user/alice/memories/old.md");
    expect((await f.store.read()).operations[f.id]!.phase).toBe(phase);
  });

  it(`rejects a new ${phase} writer without its remote proof before deletion replay`, async () => {
    const f = await stagedLaterWriter(phase);
    vi.spyOn(OwnerMemoryClient.prototype, "verifyIdentity").mockResolvedValue(undefined);
    vi.spyOn(OwnerMemoryClient.prototype, "sessionExists").mockResolvedValue(phase !== "session_created");
    vi.spyOn(OwnerMemoryClient.prototype, "hasSource")
      .mockResolvedValue(!["message_unknown", "message_delivered"].includes(phase));
    const receipt = vi.spyOn(OwnerMemoryClient.prototype, "findCommit").mockResolvedValue(null);
    const settled = vi.spyOn(OwnerMemoryClient.prototype, "writerSettled")
      .mockResolvedValue(phase === "ready");
    const inspect = vi.spyOn(OwnerMemoryClient.prototype, "inspect")
      .mockResolvedValue({ status: "ready", archiveId: "archive", memoryUris: [] });
    const remove = vi.spyOn(OwnerMemoryClient.prototype, "removeMemory");
    await expect(reconcile(f.config, f.data, f.recovery, f.checkpointFile))
      .rejects.toThrow("RECOVERY_WRITER_RECONCILIATION_REQUIRED");
    expect(receipt).toHaveBeenCalledTimes(phase === "commit_unknown" ? 1 : 0);
    expect(settled).toHaveBeenCalledTimes(["processing", "ready"].includes(phase) ? 1 : 0);
    expect(inspect).toHaveBeenCalledTimes(phase === "ready" ? 1 : 0);
    expect(remove).not.toHaveBeenCalled();
    expect(await readFile(f.statePath)).toEqual(f.oldBytes);
  });
}

it("checks Bob's post-checkpoint source before replaying Alice's deletion", async () => {
  const f = await fixture();
  const bob = { accountId: "account", userId: "bob" };
  const bobStore = new FileStateStore({ owner: bob,
    directory: join(f.data, "host-state", "owner-b", "state", "memory"), policyVersion: "v1" });
  await bobStore.transact(state => { state.authorization.enabled = false; });
  await MemoryRecoveryJournal.bootstrap(f.recovery, bob, await bobStore.read());
  const bobJournal = await MemoryRecoveryJournal.open(f.recovery, bob, await bobStore.read());
  await f.credentialStore.write(bob, "synthetic-bob-key");
  await checkpoint(f.data, f.recovery, f.checkpointFile);
  const bobStatePath = join(f.data, "host-state", "owner-b", "state", "memory", "state.json");
  const oldBob = await readFile(bobStatePath);
  const id = "e".repeat(64);
  await new RecoveryStateStore(bobStore, bobJournal).transact(state => {
    state.operations[id] = { id, owner: bob, scope: null, kind: "explicit",
      authorizationEpoch: state.authorization.epoch,
      source: { sessionId: "chat", entryId: "new", branchId: "root", contentVersion: "1" },
      createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(),
      phase: "message_unknown", remoteSessionId: "bob-session", payload: "Bob fact" };
  });
  await writeFile(bobStatePath, oldBob);
  const aliceUri = "viking://user/alice/memories/forgotten.md";
  await f.journal.append({ kind: "removeMemory", uri: aliceUri });
  vi.spyOn(OwnerMemoryClient.prototype, "verifyIdentity").mockResolvedValue(undefined);
  const source = vi.spyOn(OwnerMemoryClient.prototype, "hasSource").mockResolvedValue(false);
  const remove = vi.spyOn(OwnerMemoryClient.prototype, "removeMemory").mockResolvedValue(undefined);
  vi.spyOn(OwnerMemoryClient.prototype, "listMemoryDocuments").mockResolvedValue([]);
  await expect(reconcile(f.config, f.data, f.recovery, f.checkpointFile))
    .rejects.toThrow("RECOVERY_WRITER_RECONCILIATION_REQUIRED");
  expect(source).toHaveBeenCalledOnce();
  expect(remove).not.toHaveBeenCalled();
  expect(await readFile(bobStatePath)).toEqual(oldBob);
  source.mockResolvedValue(true);
  await expect(reconcile(f.config, f.data, f.recovery, f.checkpointFile))
    .resolves.toEqual({ owners: 2, events: 1, remoteWriterChecks: 1 });
  expect(remove).toHaveBeenCalledExactlyOnceWith(aliceUri);
  expect((await bobStore.read()).operations[id]?.phase).toBe("message_unknown");
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

it("retries both owners after a later remote deletion fails without advancing restored states", async () => {
  const f = await fixture();
  const bob = { accountId: "account", userId: "bob" };
  const bobStore = new FileStateStore({ owner: bob,
    directory: join(f.data, "host-state", "owner-b", "state", "memory"), policyVersion: "v1" });
  await bobStore.transact(state => { state.authorization.enabled = false; });
  await MemoryRecoveryJournal.bootstrap(f.recovery, bob, await bobStore.read());
  const bobJournal = await MemoryRecoveryJournal.open(f.recovery, bob, await bobStore.read());
  await f.credentialStore.write(bob, "synthetic-bob-key");
  await checkpoint(f.data, f.recovery, f.checkpointFile);

  const aliceStatePath = join(f.data, "host-state", "owner-a", "state", "memory", "state.json");
  const bobStatePath = join(f.data, "host-state", "owner-b", "state", "memory", "state.json");
  const [aliceSnapshot, bobSnapshot] = await Promise.all([readFile(aliceStatePath), readFile(bobStatePath)]);
  await new RecoveryStateStore(f.store, f.journal).transact(state => { state.authorization.enabled = true; });
  await new RecoveryStateStore(bobStore, bobJournal).transact(state => { state.authorization.enabled = true; });
  const aliceUri = "viking://user/alice/memories/forgotten.md";
  const bobUri = "viking://user/bob/memories/forgotten.md";
  await f.journal.append({ kind: "removeMemory", uri: aliceUri });
  await bobJournal.append({ kind: "removeMemory", uri: bobUri });
  await Promise.all([writeFile(aliceStatePath, aliceSnapshot), writeFile(bobStatePath, bobSnapshot)]);

  const documents = new Set([aliceUri, bobUri]);
  const calls: string[] = [];
  vi.spyOn(OwnerMemoryClient.prototype, "verifyIdentity").mockImplementation(async () => { calls.push("verify"); });
  let failBob = true;
  vi.spyOn(OwnerMemoryClient.prototype, "removeMemory").mockImplementation(async uri => {
    calls.push(uri);
    if (uri === bobUri && failBob) { failBob = false; throw new Error("BOB_REMOTE_DOWN"); }
    documents.delete(uri);
  });
  vi.spyOn(OwnerMemoryClient.prototype, "listMemoryDocuments").mockImplementation(async () => [...documents]);

  await expect(reconcile(f.config, f.data, f.recovery, f.checkpointFile))
    .rejects.toThrow("BOB_REMOTE_DOWN");
  expect(calls.slice(0, 4)).toEqual(["verify", "verify", aliceUri, bobUri]);
  expect(documents).toEqual(new Set([bobUri]));
  expect(await readFile(aliceStatePath)).toEqual(aliceSnapshot);
  expect(await readFile(bobStatePath)).toEqual(bobSnapshot);

  await expect(reconcile(f.config, f.data, f.recovery, f.checkpointFile))
    .resolves.toEqual({ owners: 2, events: 2 });
  expect(documents.size).toBe(0);
  expect((await f.store.read()).revision).toBe(2);
  expect((await bobStore.read()).revision).toBe(2);
  await expect(reconcile(f.config, f.data, f.recovery, f.checkpointFile))
    .resolves.toEqual({ owners: 2, events: 2 });
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
