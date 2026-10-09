import { isAbsolute, resolve } from "node:path";
import type { IsolatedToolExecutor } from "@josephyoung/pi-openviking/worker-tools";
import type { WorkerIdentityRegistry } from "./worker-identity-registry.js";
import { provisionWorkerWorkspace } from "./worker-workspace.js";
import type { WorkerBrokerProfile } from "./start-worker-broker.js";

export interface SupervisedWorker {
  readonly workspace: string;
  readonly agentDir: string;
  readonly stateDir: string;
  readonly worker: IsolatedToolExecutor & { close(): Promise<void> };
}
export interface WorkerSupervisorOptions {
  usersRoot: string;
  hostStateRoot: string;
  identities: WorkerIdentityRegistry;
  maxWorkers: number;
  /** Root-validated read-only resource roots, never supplied over host RPC. */
  trustedReadPaths?: readonly string[];
  broker: Omit<WorkerBrokerProfile, "workspace" | "agentDir" | "stateDir" | "workerUid" | "workerGid">;
}
type Factory = (ownerId: string, workspace: string) => Promise<SupervisedWorker>;
interface Slot { ownerId: string; creating: Promise<SupervisedWorker>; value?: SupervisedWorker;
  active: number; used: number; cleanup?: Promise<void>; cleanupFailed?: boolean }

/** Root supervisor lifecycle, separate from the non-root HTTP host. Retiring
 * an owner blocks new acquisitions for this supervisor lifetime; persistent
 * authentication/memory revocation is the caller's separate responsibility. */
export class WorkerSupervisor {
  readonly #slots = new Map<string, Slot>();
  readonly #retired = new Set<string>();
  readonly #factory: Factory;
  readonly #maxWorkers: number;
  #closed = false;
  #closing?: Promise<void>;
  readonly #retirements = new Map<string, Promise<void>>();
  readonly #releases = new Map<string, Promise<void>>();
  readonly #releasingSlots = new Set<Slot>();
  readonly #preparations = new Map<string, { ownerId: string; promise: Promise<void> }>();
  #clock = 0;

  constructor(private readonly options: WorkerSupervisorOptions, factory?: Factory) {
    if (!Number.isSafeInteger(options.maxWorkers) || options.maxWorkers <= 0) throw new Error("INVALID_WORKER_SUPERVISOR_LIMIT");
    this.#maxWorkers = options.maxWorkers;
    this.#factory = factory ?? (async (ownerId, workspace) => {
      const provisioned = await provisionWorkerWorkspace({ usersRoot: options.usersRoot,
        hostStateRoot: options.hostStateRoot, identities: options.identities,
        hostUid: options.broker.hostUid, hostGid: options.broker.hostGid, userId: ownerId, workspace,
        trustedReadPaths: options.trustedReadPaths });
      const { startWorkerBroker } = await import("./start-worker-broker.js");
      const worker = await startWorkerBroker({ ...options.broker,
        workspace: provisioned.workspace, agentDir: provisioned.agentDir, stateDir: provisioned.stateDir,
        workerUid: provisioned.identity.uid, workerGid: provisioned.identity.gid });
      return { ...provisioned, worker };
    });
  }

  #assertOwner(ownerId: string, workspace: string): void {
    if (typeof ownerId !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(ownerId)
      || typeof workspace !== "string" || !isAbsolute(workspace)) throw new Error("INVALID_WORKER_OWNER_REQUEST");
    if (this.#closed || this.#retired.has(ownerId)) throw new Error("WORKER_SUPERVISOR_CLOSED");
    if (this.#releases.has(ownerId)) throw new Error("WORKER_SUPERVISOR_RELEASING");
  }

  /** Prepare managed permissions through the same root authority as startup,
   * without consuming a worker slot or launching a model/backend. */
  prepare(ownerId: string, workspace: string): Promise<void> {
    this.#assertOwner(ownerId, workspace);
    const canonical = resolve(workspace), key = JSON.stringify([ownerId, canonical]);
    const previous = this.#preparations.get(key);
    if (previous) return previous.promise;
    const pending = { ownerId, promise: Promise.resolve().then(async () => {
      const slot = this.#slots.get(key);
      if (slot) await slot.creating;
      this.#assertOwner(ownerId, canonical);
      if (this.#slots.get(key)?.active) throw new Error("WORKER_SUPERVISOR_BUSY");
      await provisionWorkerWorkspace({ usersRoot: this.options.usersRoot,
        hostStateRoot: this.options.hostStateRoot, identities: this.options.identities,
        hostUid: this.options.broker.hostUid, hostGid: this.options.broker.hostGid,
        userId: ownerId, workspace: canonical, trustedReadPaths: this.options.trustedReadPaths });
    }).finally(() => { if (this.#preparations.get(key) === pending) this.#preparations.delete(key); }) };
    this.#preparations.set(key, pending);
    return pending.promise;
  }

  async acquire(ownerId: string, workspace: string): Promise<SupervisedWorker> {
    this.#assertOwner(ownerId, workspace);
    const canonical = resolve(workspace);
    const key = JSON.stringify([ownerId, canonical]);
    const preparation = this.#preparations.get(key);
    if (preparation) { await preparation.promise; this.#assertOwner(ownerId, workspace); }
    const existing = this.#slots.get(key);
    if (existing && !existing.cleanup) { existing.used = ++this.#clock; return existing.creating; }
    if (existing?.cleanup) {
      await existing.cleanup;
      if (this.#slots.get(key) === existing) this.#slots.delete(key);
      return this.acquire(ownerId, canonical);
    }
    if (this.#slots.size + this.#releasingSlots.size >= this.#maxWorkers) {
      const victim = [...this.#slots.entries()].filter(([, slot]) => slot.value && !slot.active && !slot.cleanup)
        .sort((a, b) => a[1].used - b[1].used)[0];
      if (!victim) throw new Error("WORKER_SUPERVISOR_LIMIT");
      await this.#closeLease(victim[1], victim[1].value!);
      if (this.#slots.get(victim[0]) === victim[1]) this.#slots.delete(victim[0]);
      return this.acquire(ownerId, canonical);
    }
    const slot: Slot = { ownerId, active: 0, used: ++this.#clock, creating: Promise.resolve().then(async () => {
      const lease = await this.#factory(ownerId, canonical);
      if (this.#closed || this.#retired.has(ownerId) || this.#slots.get(key) !== slot
        || lease.workspace !== canonical || lease.worker.workspace !== canonical) {
        await this.#closeLease(slot, lease);
        throw new Error("WORKER_SUPERVISOR_CLOSED");
      }
      await lease.worker.assertIsolated().catch(async error => { await this.#closeLease(slot, lease); throw error; });
      if (this.#closed || this.#retired.has(ownerId) || this.#slots.get(key) !== slot) {
        await this.#closeLease(slot, lease);
        throw new Error("WORKER_SUPERVISOR_CLOSED");
      }
      slot.value = Object.freeze(lease);
      return slot.value;
    }).catch(error => {
      if (!slot.cleanupFailed && this.#slots.get(key) === slot) this.#slots.delete(key);
      throw error;
    }) };
    this.#slots.set(key, slot);
    return slot.creating;
  }

  /** Pin the current process only for an operation. Logical IPC leases and
   * host-owned memory queues survive eviction of an idle worker process. */
  async use<T>(ownerId: string, workspace: string, operation: (lease: SupervisedWorker) => Promise<T>): Promise<T> {
    while (true) {
      const lease = await this.acquire(ownerId, workspace);
      const slot = this.#slots.get(JSON.stringify([ownerId, resolve(workspace)]));
      // Another acquisition may have started eviction before our await resumed.
      if (!slot || slot.value !== lease || slot.cleanup || this.#preparations.has(JSON.stringify([ownerId, resolve(workspace)]))) continue;
      slot.active++;
      try { return await operation(lease); }
      finally { slot.active--; slot.used = ++this.#clock; }
    }
  }

  retire(ownerId: string): Promise<void> {
    const previous = this.#retirements.get(ownerId);
    if (previous) return previous;
    this.#retired.add(ownerId);
    const closing = this.release(ownerId);
    this.#retirements.set(ownerId, closing);
    return closing;
  }

  /** Release a failed/finished runtime without permanently retiring its owner. */
  release(ownerId: string): Promise<void> {
    const previous = this.#releases.get(ownerId);
    if (previous) return previous;
    const slots = [...this.#slots.entries()].filter(([, slot]) => slot.ownerId === ownerId);
    for (const [key, slot] of slots) { this.#releasingSlots.add(slot); this.#slots.delete(key); }
    const preparations = [...this.#preparations.values()].filter(item => item.ownerId === ownerId);
    const closing = Promise.all([this.#closeSlots(slots.map(([, slot]) => slot)),
      ...preparations.map(item => item.promise.catch(() => {}))]).then(() => {
      for (const [, slot] of slots) this.#releasingSlots.delete(slot);
      this.#releases.delete(ownerId);
    });
    // Keep failures registered: no replacement worker until cleanup succeeds.
    this.#releases.set(ownerId, closing);
    return closing;
  }

  close(): Promise<void> {
    if (this.#closing) return this.#closing;
    this.#closed = true;
    const slots = [...this.#slots.values()];
    this.#slots.clear();
    this.#closing = this.#waitForCleanup([this.#closeSlots(slots), ...this.#retirements.values(), ...this.#releases.values(),
      ...[...this.#preparations.values()].map(item => item.promise.catch(() => {}))]);
    return this.#closing;
  }

  #closeLease(slot: Slot, lease: SupervisedWorker): Promise<void> {
    slot.cleanup ??= Promise.resolve().then(() => lease.worker.close()).catch(error => {
      slot.cleanupFailed = true;
      throw error;
    });
    return slot.cleanup;
  }

  async #waitForCleanup(pending: Promise<void>[]): Promise<void> {
    const results = await Promise.allSettled(pending);
    const errors = results.flatMap(result => result.status === "rejected" ? [result.reason] : []);
    if (errors.length) throw new AggregateError(errors, "WORKER_SUPERVISOR_CLEANUP_FAILED");
  }

  async #closeSlots(slots: Slot[]): Promise<void> {
    const results = await Promise.allSettled(slots.map(slot => slot.creating));
    await this.#waitForCleanup(results.flatMap((result, index) => {
      const slot = slots[index]!;
      if (result.status === "fulfilled") return [this.#closeLease(slot, result.value)];
      return slot.cleanup ? [slot.cleanup] : [];
    }));
  }
}
