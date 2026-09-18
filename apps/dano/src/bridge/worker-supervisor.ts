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
  broker: Omit<WorkerBrokerProfile, "workspace" | "agentDir" | "stateDir" | "workerUid" | "workerGid">;
}
type Factory = (ownerId: string, workspace: string) => Promise<SupervisedWorker>;
interface Slot { ownerId: string; creating: Promise<SupervisedWorker>; cleanup?: Promise<void>; cleanupFailed?: boolean }

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

  constructor(options: WorkerSupervisorOptions, factory?: Factory) {
    if (!Number.isSafeInteger(options.maxWorkers) || options.maxWorkers <= 0) throw new Error("INVALID_WORKER_SUPERVISOR_LIMIT");
    this.#maxWorkers = options.maxWorkers;
    this.#factory = factory ?? (async (ownerId, workspace) => {
      const provisioned = await provisionWorkerWorkspace({ usersRoot: options.usersRoot,
        hostStateRoot: options.hostStateRoot, identities: options.identities,
        hostUid: options.broker.hostUid, hostGid: options.broker.hostGid, userId: ownerId, workspace });
      const { startWorkerBroker } = await import("./start-worker-broker.js");
      const worker = await startWorkerBroker({ ...options.broker,
        workspace: provisioned.workspace, agentDir: provisioned.agentDir, stateDir: provisioned.stateDir,
        workerUid: provisioned.identity.uid, workerGid: provisioned.identity.gid });
      return { ...provisioned, worker };
    });
  }

  async acquire(ownerId: string, workspace: string): Promise<SupervisedWorker> {
    if (typeof ownerId !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(ownerId)
      || typeof workspace !== "string" || !isAbsolute(workspace)) throw new Error("INVALID_WORKER_OWNER_REQUEST");
    if (this.#closed || this.#retired.has(ownerId)) throw new Error("WORKER_SUPERVISOR_CLOSED");
    if (this.#releases.has(ownerId)) throw new Error("WORKER_SUPERVISOR_RELEASING");
    const canonical = resolve(workspace);
    const key = JSON.stringify([ownerId, canonical]);
    const existing = this.#slots.get(key);
    if (existing) return existing.creating;
    if (this.#slots.size >= this.#maxWorkers) throw new Error("WORKER_SUPERVISOR_LIMIT");
    const slot: Slot = { ownerId, creating: Promise.resolve().then(async () => {
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
      return Object.freeze(lease);
    }).catch(error => {
      if (!slot.cleanupFailed && this.#slots.get(key) === slot) this.#slots.delete(key);
      throw error;
    }) };
    this.#slots.set(key, slot);
    return slot.creating;
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
    for (const [key] of slots) this.#slots.delete(key);
    const closing = this.#closeSlots(slots.map(([, slot]) => slot)).then(() => {
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
    this.#closing = this.#waitForCleanup([this.#closeSlots(slots), ...this.#retirements.values(), ...this.#releases.values()]);
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
