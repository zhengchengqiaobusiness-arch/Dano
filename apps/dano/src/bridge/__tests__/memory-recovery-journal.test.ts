import { mkdtemp, mkdir, readFile, realpath, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
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
