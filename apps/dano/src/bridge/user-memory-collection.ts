import { CollectionFactSelector, CollectionScheduler, CollectionSessionRegistry,
  type CollectionSchedulerOptions, type StateStore, type MemoryDelivery,
  type MemoryExtensionOptions } from "@josephyoung/pi-openviking/host";
import type { MemoryUserProvenance } from "./memory-user-provenance.js";
import type { MemoryTaskFactConfig } from "./memory-task-facts.js";

type SelectorOptions = ConstructorParameters<typeof CollectionFactSelector>[0];
export interface UserMemoryCollectionOptions {
  policyVersion: string;
  lifecycleTimeoutMs: number;
  selector: Pick<SelectorOptions, "maxInputBytes" | "maxFacts" | "timeoutMs" | "complete" | "sensitiveValues" | "taskFacts">;
  scheduler: Omit<CollectionSchedulerOptions, "store" | "delivery" | "selector" | "resolveSession" | "wakeDelivery" | "scope">;
  taskFacts?: { config: MemoryTaskFactConfig; key: Uint8Array };
}

/** One owner-level collector. Recovery reads only original protected pi files. */
export class UserMemoryCollection {
  readonly sessions: CollectionSessionRegistry;
  readonly extension: NonNullable<MemoryExtensionOptions["collection"]>;
  readonly #scheduler: CollectionScheduler;

  constructor(options: { store: StateStore; delivery: MemoryDelivery; sessionRoot: string;
    provenance: MemoryUserProvenance; configuration: UserMemoryCollectionOptions;
    wakeDelivery(): void }) {
    const configured = options.configuration;
    if (!configured.policyVersion.trim() || !Number.isSafeInteger(configured.lifecycleTimeoutMs)
      || configured.lifecycleTimeoutMs <= 0) throw new Error("INVALID_MEMORY_COLLECTION_CONFIG");
    this.sessions = new CollectionSessionRegistry({ store: options.store, sessionRoot: options.sessionRoot });
    const selector = new CollectionFactSelector({ ...configured.selector, store: options.store,
      projectUserText: async ({ source, signal }) => {
        const session = await this.sessions.resolveSession(source.sessionId, signal);
        return options.provenance.project(session, source);
      } });
    this.#scheduler = new CollectionScheduler({ ...configured.scheduler, store: options.store,
      delivery: options.delivery, selector,
      resolveSession: (id, signal) => this.sessions.resolveSession(id, signal),
      wakeDelivery: options.wakeDelivery });
    this.extension = { sessions: this.sessions, policyVersion: configured.policyVersion,
      lifecycleTimeoutMs: configured.lifecycleTimeoutMs, wake: () => this.#scheduler.wake() };
  }

  start(): void { this.#scheduler.start(); }
  stop(): Promise<void> { return this.#scheduler.stop(); }
}
