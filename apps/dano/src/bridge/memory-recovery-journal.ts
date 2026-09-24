import { constants } from "node:fs";
import { open, lstat, mkdir, realpath, rename, rm } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { join, resolve, sep } from "node:path";
import type { GovernanceStateStore, Owner, StateStore } from "@josephyoung/pi-openviking/host";

type OwnerState = Awaited<ReturnType<StateStore["read"]>>;

export type RecoveryMutation =
  | { kind: "removeSource"; remoteSessionId: string }
  | { kind: "removeMemory"; uri: string }
  | { kind: "replaceMemory"; uri: string; content: string }
  | { kind: "clearMemoryScope" }
  | { kind: "clearOwnerData" };

export interface RecoveryEvent {
  version: 1;
  id: string;
  owner: Owner;
  occurredAt: string;
  mutation: RecoveryMutation;
}

const unavailable = () => new Error("MEMORY_RECOVERY_JOURNAL_UNAVAILABLE");
const identifier = (value: unknown): value is string => typeof value === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(value);
const documentUri = (owner: Owner, value: unknown): value is string => {
  if (typeof value !== "string" || !value.startsWith(`viking://user/${owner.userId}/memories/`) || !value.endsWith(".md")) return false;
  const relative = value.slice(`viking://user/${owner.userId}/memories/`.length);
  return !/[%?#\\\x00-\x1f]/.test(value)
    && relative.split("/").every(part => part.length > 0 && !part.startsWith("."));
};

function checkedMutation(owner: Owner, value: unknown): asserts value is RecoveryMutation {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw unavailable();
  const mutation = value as Record<string, unknown>;
  const fields = Object.keys(mutation).sort().join(",");
  switch (mutation.kind) {
    case "removeSource": if (fields === "kind,remoteSessionId" && identifier(mutation.remoteSessionId)) return; break;
    case "removeMemory": if (fields === "kind,uri" && documentUri(owner, mutation.uri)) return; break;
    case "replaceMemory":
      if (fields === "content,kind,uri" && documentUri(owner, mutation.uri)
        && typeof mutation.content === "string" && mutation.content.trim()
        && Buffer.byteLength(mutation.content, "utf8") <= 1024 * 1024) return;
      break;
    case "clearMemoryScope":
    case "clearOwnerData": if (fields === "kind") return; break;
  }
  throw unavailable();
}

async function privateDirectory(path: string): Promise<void> {
  const stat = await lstat(path);
  if (!stat.isDirectory() || stat.isSymbolicLink() || stat.uid !== process.getuid?.()
    || (stat.mode & 0o077) !== 0 || await realpath(path) !== path) throw unavailable();
}

async function privateFile(path: string): Promise<Buffer | undefined> {
  let file;
  try { file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW); }
  catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined;
    throw unavailable();
  }
  try {
    const stat = await file.stat();
    if (!stat.isFile() || stat.nlink !== 1 || stat.uid !== process.getuid?.()
      || (stat.mode & 0o077) !== 0 || stat.size > 64 * 1024 * 1024) throw unavailable();
    return await file.readFile();
  } finally { await file.close(); }
}

/** A separate, host-owned volume must survive restoration of older data volumes.
 * Intent is fsynced before any corresponding remote deletion is sent. */
export class MemoryRecoveryJournal {
  readonly owner: Owner;
  readonly #directory: string;
  #appends: Promise<void> = Promise.resolve();
  #mirrors: Promise<void> = Promise.resolve();
  #poisoned = false;

  private constructor(owner: Owner, directory: string) {
    this.owner = Object.freeze({ ...owner });
    this.#directory = directory;
  }

  static async #prepare(root: string, owner: Owner): Promise<MemoryRecoveryJournal> {
    if (!root.startsWith(`${sep}`) || resolve(root) !== root
      || ![owner.accountId, owner.userId].every(id => typeof id === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(id))) {
      throw unavailable();
    }
    await privateDirectory(root);
    const account = join(root, owner.accountId);
    const directory = join(account, owner.userId);
    for (const path of [account, directory]) {
      await mkdir(path, { mode: 0o700 }).catch(error => {
        if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
      });
      await privateDirectory(path);
    }
    return new MemoryRecoveryJournal(owner, directory);
  }

  /** One-time stopped-service migration of an existing owner into an empty
   * recovery volume. Never replace an existing mirror with an older snapshot. */
  static async bootstrap(root: string, owner: Owner, current: OwnerState): Promise<void> {
    const journal = await this.#prepare(root, owner);
    const saved = await journal.#state();
    if (saved || await privateFile(join(journal.#directory, "events.jsonl"))) {
      if (saved && saved.revision === current.revision && JSON.stringify(saved) === JSON.stringify(current)
        && !(await privateFile(join(journal.#directory, "events.jsonl")))) return;
      throw new Error("MEMORY_RECOVERY_STATE_MISMATCH");
    }
    await journal.mirror(current);
  }

  static async open(root: string, owner: Owner, current: OwnerState): Promise<MemoryRecoveryJournal> {
    const journal = await this.#prepare(root, owner);
    const saved = await journal.#state();
    if (!saved) {
      if (await privateFile(join(journal.#directory, "events.jsonl"))) throw new Error("MEMORY_RECOVERY_STATE_MISMATCH");
      // An existing owner must be migrated while the service is stopped. An
      // empty journal cannot certify operations accepted before it existed.
      if (current.revision !== 0) throw new Error("MEMORY_RECOVERY_BOOTSTRAP_REQUIRED");
      await journal.mirror(current);
    } else if (saved.revision !== current.revision
      || current.revision > 0 && JSON.stringify(saved) !== JSON.stringify(current)) {
      throw new Error("MEMORY_RECOVERY_STATE_MISMATCH");
    }
    await journal.#validateEvents();
    return journal;
  }

  /** Read a stopped service's validated mirror and event stream for checkpoint
   * or rollback tooling. Never infer a deleted fact from an older snapshot. */
  static async inspect(root: string, owner: Owner): Promise<{ state: OwnerState; eventBytes: Buffer; events: RecoveryEvent[] }> {
    const journal = await this.#prepare(root, owner);
    const state = await journal.#state();
    if (!state) throw unavailable();
    const { bytes, events } = await journal.#validateEvents();
    return { state, eventBytes: bytes, events };
  }

  assertHealthy(): void { if (this.#poisoned) throw unavailable(); }
  poison(): void { this.#poisoned = true; }

  async #state(): Promise<OwnerState | undefined> {
    const bytes = await privateFile(join(this.#directory, "state.json"));
    if (!bytes) return undefined;
    try {
      const state = JSON.parse(bytes.toString("utf8")) as OwnerState;
      if (state.owner?.accountId !== this.owner.accountId || state.owner?.userId !== this.owner.userId
        || !Number.isSafeInteger(state.revision) || state.revision < 0) throw unavailable();
      return state;
    } catch { throw unavailable(); }
  }

  async #validateEvents(): Promise<{ bytes: Buffer; events: RecoveryEvent[] }> {
    const bytes = await privateFile(join(this.#directory, "events.jsonl")) ?? Buffer.alloc(0);
    if (bytes.length && bytes.at(-1) !== 10) throw unavailable();
    const ids = new Set<string>();
    const events: RecoveryEvent[] = [];
    for (const line of bytes.toString("utf8").split("\n")) {
      if (!line) continue;
      try {
        if (Buffer.byteLength(line, "utf8") > 1024 * 1024) throw unavailable();
        const entry = JSON.parse(line) as Record<string, unknown>;
        const owner = entry.owner as Owner | undefined;
        if (Object.keys(entry).sort().join(",") !== "id,mutation,occurredAt,owner,version"
          || entry.version !== 1 || !owner || Object.keys(owner).sort().join(",") !== "accountId,userId"
          || owner.accountId !== this.owner.accountId || owner.userId !== this.owner.userId
          || typeof entry.id !== "string" || !/^[a-f0-9-]{36}$/.test(entry.id) || ids.has(entry.id)
          || typeof entry.occurredAt !== "string" || !Number.isFinite(Date.parse(entry.occurredAt))
          || new Date(entry.occurredAt).toISOString() !== entry.occurredAt) throw unavailable();
        checkedMutation(this.owner, entry.mutation);
        ids.add(entry.id);
        events.push(entry as unknown as RecoveryEvent);
      } catch { throw unavailable(); }
    }
    return { bytes, events };
  }

  mirror(state: OwnerState): Promise<void> {
    this.assertHealthy();
    const pending = this.#mirrors.then(() => this.#mirror(state));
    this.#mirrors = pending.catch(() => {});
    return pending;
  }

  async #mirror(state: OwnerState): Promise<void> {
    if (state.owner.accountId !== this.owner.accountId || state.owner.userId !== this.owner.userId
      || !Number.isSafeInteger(state.revision) || state.revision < 0) throw unavailable();
    const saved = await this.#state();
    if (saved && saved.revision > state.revision) return;
    if (saved && saved.revision === state.revision && state.revision > 0
      && JSON.stringify(saved) !== JSON.stringify(state)) throw unavailable();
    const temporary = join(this.#directory, `.state-${randomUUID()}`);
    const file = await open(temporary, "wx", 0o600);
    try {
      await file.writeFile(JSON.stringify(state));
      await file.sync();
    } finally { await file.close(); }
    try {
      await rename(temporary, join(this.#directory, "state.json"));
      const directory = await open(this.#directory, constants.O_RDONLY);
      try { await directory.sync(); }
      finally { await directory.close(); }
    } finally { await rm(temporary, { force: true }); }
  }

  async append(mutation: RecoveryMutation): Promise<void> {
    this.assertHealthy();
    const pending = this.#appends.then(async () => {
      this.assertHealthy();
      checkedMutation(this.owner, mutation);
      const line = JSON.stringify({ version: 1, id: randomUUID(), owner: this.owner,
        occurredAt: new Date().toISOString(), mutation }) + "\n";
      if (Buffer.byteLength(line) > 1024 * 1024) throw unavailable();
      const path = join(this.#directory, "events.jsonl");
      const file = await open(path, constants.O_WRONLY | constants.O_APPEND | constants.O_CREAT | constants.O_NOFOLLOW, 0o600);
      try {
        const stat = await file.stat();
        if (!stat.isFile() || stat.nlink !== 1 || stat.uid !== process.getuid?.() || (stat.mode & 0o077) !== 0) throw unavailable();
        await file.writeFile(line);
        await file.sync();
      } finally { await file.close(); }
      const directory = await open(this.#directory, constants.O_RDONLY);
      try { await directory.sync(); }
      finally { await directory.close(); }
    });
    const guarded = pending.catch(() => { this.poison(); throw unavailable(); });
    this.#appends = guarded.catch(() => {});
    return guarded;
  }
}

/** Keep every acknowledged owner-state revision on the separate recovery
 * volume. If the mirror fails, poison this owner's memory runtime: a later
 * remote action must never continue from a state absent from recovery. */
export class RecoveryStateStore implements GovernanceStateStore {
  readonly owner: Owner;
  readonly #base: GovernanceStateStore;
  readonly #journal: MemoryRecoveryJournal;

  constructor(base: GovernanceStateStore, journal: MemoryRecoveryJournal) {
    if (base.owner.accountId !== journal.owner.accountId || base.owner.userId !== journal.owner.userId) throw unavailable();
    this.owner = base.owner;
    this.#base = base;
    this.#journal = journal;
  }

  #assertAvailable(): void { this.#journal.assertHealthy(); }
  async read(signal?: AbortSignal): Promise<OwnerState> { this.#assertAvailable(); return this.#base.read(signal); }
  async withGovernanceLock<T>(action: () => Promise<T>, signal?: AbortSignal): Promise<T> {
    this.#assertAvailable();
    return this.#base.withGovernanceLock(async () => { this.#assertAvailable(); return action(); }, signal);
  }
  async transact<T>(mutation: (state: OwnerState) => T, signal?: AbortSignal): Promise<T> {
    this.#assertAvailable();
    const result = await this.#base.transact(mutation, signal);
    try { await this.#journal.mirror(await this.#base.read(signal)); }
    catch { this.#journal.poison(); throw unavailable(); }
    return result;
  }
}
