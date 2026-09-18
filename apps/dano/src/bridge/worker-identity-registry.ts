import { createHash } from "node:crypto";
import { constants } from "node:fs";
import { mkdir, open, readdir, realpath, stat, type FileHandle } from "node:fs/promises";
import { join, resolve } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { flock } from "fs-ext";
import { writeFile as atomicWrite } from "atomically";

interface IdentityRange { firstUid: number; firstGid: number; count: number }
interface StoredIdentities { version: 1; range: IdentityRange; owners: string[] }
export interface WorkerIdentity { readonly uid: number; readonly gid: number }

const unavailable = () => new Error("WORKER_IDENTITIES_UNAVAILABLE");
const lock = (fd: number, operation: "exnb" | "un") => new Promise<void>((accept, reject) => {
  flock(fd, operation, error => error ? reject(error) : accept());
});

/** Launcher-owned metadata, outside all user/workspace trees. Allocations are
 * append-only: there is deliberately no release/reuse operation on user deletion. */
export class WorkerIdentityRegistry {
  readonly #directory: string;
  readonly #range: IdentityRange;
  readonly #lockTimeoutMs: number;

  constructor(options: IdentityRange & { directory: string; hostUid: number; hostGid: number; lockTimeoutMs: number }) {
    const validId = (id: number) => Number.isSafeInteger(id) && id > 0 && id < 2 ** 32 - 1;
    if (!validId(options.firstUid) || !validId(options.firstGid)
      || !validId(options.hostUid) || !validId(options.hostGid)
      || !Number.isSafeInteger(options.count) || options.count <= 0
      || !validId(options.firstUid + options.count - 1) || !validId(options.firstGid + options.count - 1)
      || (options.hostUid >= options.firstUid && options.hostUid < options.firstUid + options.count)
      || (options.hostGid >= options.firstGid && options.hostGid < options.firstGid + options.count)
      || !Number.isSafeInteger(options.lockTimeoutMs) || options.lockTimeoutMs <= 0) {
      throw new Error("INVALID_WORKER_IDENTITY_RANGE");
    }
    this.#directory = resolve(options.directory);
    this.#range = { firstUid: options.firstUid, firstGid: options.firstGid, count: options.count };
    this.#lockTimeoutMs = options.lockTimeoutMs;
  }

  async get(ownerId: string, signal?: AbortSignal): Promise<WorkerIdentity> {
    if (typeof ownerId !== "string" || !ownerId || Buffer.byteLength(ownerId) > 4096) throw unavailable();
    const owner = createHash("sha256").update(ownerId).digest("hex");
    return (await this.#update(owner, signal))!;
  }

  /** Before serving users, establish an empty pool only if all associated
   * persistent data roots are empty. Never recreate a lost pool over old data. */
  async initialize(dataRoots: readonly string[]): Promise<void> {
    if (!dataRoots.length) throw unavailable();
    await this.#update(undefined, undefined, dataRoots);
  }

  /** Provisioning must not implicitly create a new pool. */
  async assertInitialized(): Promise<void> {
    const handle = await open(join(this.#directory, "allocations.json"), constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    try { await this.#checkFile(handle); } finally { await handle.close(); }
  }

  async #update(owner?: string, signal?: AbortSignal, dataRoots?: readonly string[]): Promise<WorkerIdentity | undefined> {
    signal?.throwIfAborted();
    await mkdir(this.#directory, { mode: 0o700, recursive: true });
    if (await realpath(this.#directory) !== this.#directory) throw unavailable();
    const directory = await stat(this.#directory);
    if (!directory.isDirectory() || directory.uid !== process.getuid?.() || (directory.mode & 0o077)) throw unavailable();
    const handle = await open(join(this.#directory, "allocation.lock"),
      constants.O_RDWR | constants.O_CREAT | constants.O_NOFOLLOW | constants.O_NONBLOCK, 0o600);
    let locked = false;
    try {
      await this.#checkFile(handle);
      const deadline = Date.now() + this.#lockTimeoutMs;
      for (;;) {
        signal?.throwIfAborted();
        try { await lock(handle.fd, "exnb"); locked = true; break; }
        catch (error) {
          if (!["EAGAIN", "EWOULDBLOCK"].includes((error as NodeJS.ErrnoException).code ?? "")) throw unavailable();
          if (Date.now() >= deadline) throw new Error("WORKER_IDENTITY_LOCK_TIMEOUT");
          await delay(Math.min(10, Math.max(1, deadline - Date.now())), undefined, { signal });
        }
      }
      signal?.throwIfAborted();
      const marker = await handle.readFile("utf8");
      if (marker !== "" && marker !== "1") throw unavailable();
      const file = join(this.#directory, "allocations.json");
      let stored: StoredIdentities = { version: 1, range: this.#range, owners: [] };
      let input: FileHandle | undefined;
      try { input = await open(file, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK); }
      catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw unavailable(); }
      if (!input && (marker === "1" || owner !== undefined)) throw unavailable();
      if (!input && dataRoots) {
        for (const root of dataRoots) {
          if (await realpath(root) !== resolve(root) || (await readdir(root)).length) throw unavailable();
        }
      }
      if (input) {
        try {
          await this.#checkFile(input);
          stored = JSON.parse(await input.readFile("utf8"));
          if (stored.version !== 1 || !stored.range
            || Object.keys(this.#range).some(key => stored.range[key as keyof IdentityRange] !== this.#range[key as keyof IdentityRange])
            || !Array.isArray(stored.owners) || stored.owners.length > this.#range.count
            || stored.owners.some(value => typeof value !== "string" || !/^[a-f0-9]{64}$/.test(value))
            || new Set(stored.owners).size !== stored.owners.length) throw unavailable();
        } catch { throw unavailable(); }
        finally { await input.close(); }
      }
      let changed = !input;
      let offset = owner === undefined ? -1 : stored.owners.indexOf(owner);
      if (owner !== undefined && offset === -1) {
        if (stored.owners.length >= this.#range.count) throw new Error("WORKER_IDENTITY_RANGE_EXHAUSTED");
        offset = stored.owners.length;
        stored.owners.push(owner);
        changed = true;
      }
      if (changed) {
        signal?.throwIfAborted();
        // A cancelled caller may reserve an identity, but must never cause it
        // to be reused after persistence. The next call finds that allocation.
        const serialized = JSON.stringify(stored) + "\n";
        if (Buffer.byteLength(serialized) > 16 * 1024 * 1024) throw unavailable();
        await atomicWrite(file, serialized, { mode: 0o600, fsync: true });
        const parent = await open(this.#directory, constants.O_RDONLY);
        try { await parent.sync(); } finally { await parent.close(); }
      }
      if (marker === "") {
        await handle.write("1", 0, "utf8");
        await handle.sync();
      }
      signal?.throwIfAborted();
      return owner === undefined ? undefined : Object.freeze({ uid: this.#range.firstUid + offset, gid: this.#range.firstGid + offset });
    } finally {
      try { if (locked) await lock(handle.fd, "un"); }
      finally { await handle.close(); }
    }
  }

  async #checkFile(handle: FileHandle): Promise<void> {
    const metadata = await handle.stat();
    if (!metadata.isFile() || metadata.uid !== process.getuid?.() || (metadata.mode & 0o077)
      || metadata.nlink !== 1 || metadata.size > 16 * 1024 * 1024) throw unavailable();
  }
}
