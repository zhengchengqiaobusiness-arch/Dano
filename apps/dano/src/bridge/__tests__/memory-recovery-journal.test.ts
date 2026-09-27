import { mkdtemp, mkdir, readdir, readFile, realpath, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { randomUUID } from "node:crypto";
import { join } from "node:path";
import { afterEach, expect, it, vi } from "vitest";
import { FileStateStore } from "@josephyoung/pi-openviking/host";
import { MemoryRecoveryJournal, RecoveryStateStore } from "../memory-recovery-journal.js";

const roots: string[] = [];
afterEach(async () => {
  await Promise.all(roots.splice(0).map(root => rm(root, { recursive: true, force: true })));
  vi.restoreAllMocks();
});

async function fixture() {
  const root = await realpath(await mkdtemp(join(tmpdir(), "dano-recovery-journal-"))); roots.push(root);
  const recovery = join(root, "recovery"), state = join(root, "state");
  await mkdir(recovery, { mode: 0o700 });
  const owner = { accountId: "account", userId: "alice" };
  const base = new FileStateStore({ owner, directory: state, policyVersion: "v1" });
  return { root, recovery, state, owner, base };
}

it("mirrors each acknowledged state change and durably queues owner-bound deletion intents", async () => {
  const f = await fixture();
  const journal = await MemoryRecoveryJournal.open(f.recovery, f.owner, await f.base.read());
  await expect(MemoryRecoveryJournal.open(f.recovery, f.owner, await f.base.read())).resolves.toBeDefined();
  const store = new RecoveryStateStore(f.base, journal);
  await Promise.all([store.transact(state => { state.authorization.enabled = true; }),
    store.transact(state => { state.authorization.automaticCollection = false; })]);
  await journal.append({ kind: "removeSource", remoteSessionId: "session_a" });
  await journal.append({ kind: "removeMemory", uri: "viking://user/alice/memories/a.md" });
  const directory = join(f.recovery, "account", "alice");
  expect(JSON.parse(await readFile(join(directory, "state.json"), "utf8"))).toEqual(await f.base.read());
  const events = (await readFile(join(directory, "events.jsonl"), "utf8")).trim().split("\n").map(line => JSON.parse(line));
  expect(events.map(event => event.mutation.kind)).toEqual(["removeSource", "removeMemory"]);
  expect(events.every(event => event.owner.accountId === "account" && event.owner.userId === "alice")).toBe(true);
  const inspected = await MemoryRecoveryJournal.inspect(f.recovery, f.owner);
  expect(inspected.state).toEqual(await f.base.read());
  expect(inspected.eventBytes.toString("utf8")).toBe(await readFile(join(directory, "events.jsonl"), "utf8"));
  expect(inspected.events.map(event => event.mutation.kind)).toEqual(["removeSource", "removeMemory"]);
  await expect(MemoryRecoveryJournal.open(f.recovery, f.owner, await f.base.read())).resolves.toBeDefined();
});

it("prunes superseded correction bodies without changing earlier journal bytes", async () => {
  const f = await fixture();
  const journal = await MemoryRecoveryJournal.open(f.recovery, f.owner, await f.base.read());
  const directory = join(f.recovery, "account", "alice");
  const payloads = join(directory, "payloads");
  const uri = "viking://user/alice/memories/a.md";
  await journal.append({ kind: "replaceMemory", uri, content: "private old fact" });
  const prefix = await readFile(join(directory, "events.jsonl"));
  expect(prefix.toString("utf8")).not.toContain("private old fact");
  expect(await readdir(payloads)).toHaveLength(1);
  expect((await MemoryRecoveryJournal.inspect(f.recovery, f.owner)).events[0].mutation)
    .toMatchObject({ kind: "replaceMemory", content: "private old fact" });
  await journal.append({ kind: "replaceMemory", uri, content: "private corrected fact" });
  expect(await readdir(payloads)).toHaveLength(1);
  const corrected = await MemoryRecoveryJournal.inspect(f.recovery, f.owner);
  expect(corrected.events[0].mutation).not.toHaveProperty("content");
  expect(corrected.events[1].mutation).toMatchObject({ content: "private corrected fact" });
  await journal.append({ kind: "removeMemory", uri });
  expect(await readdir(payloads)).toEqual([]);
  const inspected = await MemoryRecoveryJournal.inspect(f.recovery, f.owner);
  expect(inspected.eventBytes.subarray(0, prefix.length)).toEqual(prefix);
  expect(inspected.events[0].mutation).not.toHaveProperty("content");
  await journal.append({ kind: "replaceMemory", uri, content: "new authorized fact" });
  expect(await readdir(payloads)).toHaveLength(1);
  await journal.append({ kind: "clearMemoryScope" });
  expect(await readdir(payloads)).toEqual([]);
  await journal.append({ kind: "replaceMemory", uri, content: "last private fact" });
  await journal.append({ kind: "clearOwnerData" });
  expect(await readdir(payloads)).toEqual([]);
  await expect(MemoryRecoveryJournal.open(f.recovery, f.owner, await f.base.read())).resolves.toBeDefined();
});

it("fails closed on a missing live correction body and removes orphan payloads at startup", async () => {
  const f = await fixture();
  const journal = await MemoryRecoveryJournal.open(f.recovery, f.owner, await f.base.read());
  const payloads = join(f.recovery, "account", "alice", "payloads");
  const uri = "viking://user/alice/memories/a.md";
  await journal.append({ kind: "replaceMemory", uri, content: "current fact" });
  const [file] = await readdir(payloads);
  await rm(join(payloads, file));
  await expect(MemoryRecoveryJournal.open(f.recovery, f.owner, await f.base.read()))
    .rejects.toThrow("MEMORY_RECOVERY_JOURNAL_UNAVAILABLE");
  await writeFile(join(payloads, file), "changed fact", { mode: 0o600 });
  await expect(MemoryRecoveryJournal.inspect(f.recovery, f.owner))
    .rejects.toThrow("MEMORY_RECOVERY_JOURNAL_UNAVAILABLE");
  await writeFile(join(payloads, file), "current fact", { mode: 0o600 });
  const orphan = join(payloads, "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa.txt");
  await writeFile(orphan, "orphaned private fact", { mode: 0o600 });
  await MemoryRecoveryJournal.open(f.recovery, f.owner, await f.base.read());
  expect(await readdir(payloads)).toEqual([file]);
});

it("can read a prior candidate's inline correction intent for old backup replay", async () => {
  const f = await fixture();
  await MemoryRecoveryJournal.open(f.recovery, f.owner, await f.base.read());
  const uri = "viking://user/alice/memories/legacy.md";
  const entry = { version: 1, id: randomUUID(), owner: f.owner,
    occurredAt: new Date().toISOString(), mutation: { kind: "replaceMemory", uri, content: "legacy fact" } };
  await writeFile(join(f.recovery, "account", "alice", "events.jsonl"), JSON.stringify(entry) + "\n", { mode: 0o600 });
  const journal = await MemoryRecoveryJournal.inspect(f.recovery, f.owner);
  expect(journal.events[0].mutation).toEqual(entry.mutation);
});

it("refuses an existing owner with no external mirror or a mismatched restored state", async () => {
  const f = await fixture();
  await f.base.transact(state => { state.authorization.enabled = true; });
  await expect(MemoryRecoveryJournal.open(f.recovery, f.owner, await f.base.read()))
    .rejects.toThrow("MEMORY_RECOVERY_BOOTSTRAP_REQUIRED");
  await MemoryRecoveryJournal.bootstrap(f.recovery, f.owner, await f.base.read());
  await MemoryRecoveryJournal.bootstrap(f.recovery, f.owner, await f.base.read());
  await expect(MemoryRecoveryJournal.open(f.recovery, f.owner, await f.base.read())).resolves.toBeDefined();
  await f.base.transact(state => { state.authorization.enabled = false; });
  await expect(MemoryRecoveryJournal.open(f.recovery, f.owner, await f.base.read()))
    .rejects.toThrow("MEMORY_RECOVERY_STATE_MISMATCH");
});

it("poisons memory access after an external mirror failure", async () => {
  const f = await fixture();
  const journal = await MemoryRecoveryJournal.open(f.recovery, f.owner, await f.base.read());
  const store = new RecoveryStateStore(f.base, journal);
  vi.spyOn(journal, "mirror").mockRejectedValueOnce(new Error("disk full"));
  await expect(store.transact(state => { state.authorization.enabled = true; }))
    .rejects.toThrow("MEMORY_RECOVERY_JOURNAL_UNAVAILABLE");
  await expect(store.read()).rejects.toThrow("MEMORY_RECOVERY_JOURNAL_UNAVAILABLE");
  await expect(journal.append({ kind: "clearMemoryScope" }))
    .rejects.toThrow("MEMORY_RECOVERY_JOURNAL_UNAVAILABLE");
});

it("mirrors a committed state even when the caller cancels just after commit", async () => {
  const f = await fixture();
  const journal = await MemoryRecoveryJournal.open(f.recovery, f.owner, await f.base.read());
  const store = new RecoveryStateStore(f.base, journal);
  const controller = new AbortController();
  const transact = f.base.transact.bind(f.base);
  vi.spyOn(f.base, "transact").mockImplementation(async (mutation, signal) => {
    const result = await transact(mutation, signal);
    controller.abort();
    return result;
  });
  await expect(store.transact(state => { state.authorization.enabled = true; }, controller.signal))
    .resolves.toBeUndefined();
  expect(await MemoryRecoveryJournal.inspect(f.recovery, f.owner)).toMatchObject({ state: await f.base.read() });
  await expect(MemoryRecoveryJournal.open(f.recovery, f.owner, await f.base.read())).resolves.toBeDefined();
});

it("rejects a partial event tail before publishing an owner runtime", async () => {
  const f = await fixture();
  await MemoryRecoveryJournal.open(f.recovery, f.owner, await f.base.read());
  await writeFile(join(f.recovery, "account", "alice", "events.jsonl"), '{"partial":', { mode: 0o600 });
  await expect(MemoryRecoveryJournal.open(f.recovery, f.owner, await f.base.read()))
    .rejects.toThrow("MEMORY_RECOVERY_JOURNAL_UNAVAILABLE");
});

it("rejects malformed and duplicate deletion events before publishing an owner runtime", async () => {
  const f = await fixture();
  const journal = await MemoryRecoveryJournal.open(f.recovery, f.owner, await f.base.read());
  await journal.append({ kind: "removeMemory", uri: "viking://user/alice/memories/a.md" });
  const path = join(f.recovery, "account", "alice", "events.jsonl");
  const original = await readFile(path, "utf8");
  const entry = JSON.parse(original.trim());
  await writeFile(path, original + original, { mode: 0o600 });
  await expect(MemoryRecoveryJournal.open(f.recovery, f.owner, await f.base.read()))
    .rejects.toThrow("MEMORY_RECOVERY_JOURNAL_UNAVAILABLE");
  entry.mutation = { kind: "removeMemory", uri: "viking://user/bob/memories/a.md" };
  await writeFile(path, JSON.stringify(entry) + "\n", { mode: 0o600 });
  await expect(MemoryRecoveryJournal.open(f.recovery, f.owner, await f.base.read()))
    .rejects.toThrow("MEMORY_RECOVERY_JOURNAL_UNAVAILABLE");
});

it("poisons later memory operations if an event append fails", async () => {
  const f = await fixture();
  const journal = await MemoryRecoveryJournal.open(f.recovery, f.owner, await f.base.read());
  const path = join(f.recovery, "account", "alice", "events.jsonl");
  await symlink(join(f.root, "outside"), path);
  await expect(journal.append({ kind: "removeMemory", uri: "viking://user/alice/memories/a.md" }))
    .rejects.toThrow("MEMORY_RECOVERY_JOURNAL_UNAVAILABLE");
  await expect(journal.append({ kind: "clearMemoryScope" }))
    .rejects.toThrow("MEMORY_RECOVERY_JOURNAL_UNAVAILABLE");
  await expect(new RecoveryStateStore(f.base, journal).read())
    .rejects.toThrow("MEMORY_RECOVERY_JOURNAL_UNAVAILABLE");
});
