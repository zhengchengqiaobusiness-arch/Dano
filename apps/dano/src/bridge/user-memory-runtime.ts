import { join, resolve } from "node:path";
import { createOpenVikingExtension, FileStateStore, MemoryDelivery, DeliveryScheduler,
  type MemoryExtensionOptions } from "@josephyoung/pi-openviking/host";
import type { IsolatedToolExecutor } from "@josephyoung/pi-openviking/worker-tools";
import type { MemoryOwnerRegistry } from "./memory-owner-registry.js";
import type { MemoryCredentialStore } from "./memory-credential-store.js";
import type { MemoryProvisioner } from "./memory-provisioner.js";
import { MemoryIdentityService } from "./memory-identity-service.js";
import { LazyMemoryClient } from "./lazy-memory-client.js";
import type { ProtectedSessionTools } from "./protected-session-tools.js";
import type { UserMemoryControls, UserMemoryStatus } from "./user-memory-controls.js";
import type { UserContext } from "./user-context.js";
import type { UserMemoryOperation, UserMemoryOperationPage, UserMemoryContent } from "../../types/memory.js";
import { memoryOperationPage, projectMemoryOperation } from "./memory-operation-page.js";

type SchedulerPolicy = Omit<ConstructorParameters<typeof DeliveryScheduler>[0], "store" | "delivery">;
export interface UserMemoryServices {
  owners: Pick<MemoryOwnerRegistry, "get">;
  credentials: Pick<MemoryCredentialStore, "read" | "write">;
  provisioner: Pick<MemoryProvisioner, "provision" | "verifyUserKey">;
  baseUrl: string;
  requestTimeoutMs: number;
  maxContentBytes: number;
  policyVersion: string;
  policy: MemoryExtensionOptions["policy"];
  scheduler: SchedulerPolicy;
}

/** One authenticated user's shared authorization/outbox, with per-session
 * extension factories. Construct only after supervisor isolation is verified. */
export class UserMemoryRuntime implements UserMemoryControls {
  readonly #store: FileStateStore;
  readonly #delivery: MemoryDelivery;
  readonly #scheduler: DeliveryScheduler;
  readonly #client: LazyMemoryClient;
  readonly #options: UserMemoryServices;
  #settings: Promise<void> = Promise.resolve();
  #closed = false;
  #closing?: Promise<void>;

  private constructor(store: FileStateStore, client: LazyMemoryClient, options: UserMemoryServices) {
    this.#store = store; this.#client = client; this.#options = options;
    this.#delivery = new MemoryDelivery({ store, transport: client, maxPayloadBytes: options.policy.maxPayloadBytes });
    this.#scheduler = new DeliveryScheduler({ ...options.scheduler, store, delivery: this.#delivery });
    this.#scheduler.start();
  }

  static async create(context: UserContext, stateDirectory: string, worker: IsolatedToolExecutor,
    options: UserMemoryServices): Promise<UserMemoryRuntime> {
    if (!("username" in context.user)) throw new Error("MEMORY_LOGIN_REQUIRED");
    if (!Number.isSafeInteger(options.maxContentBytes) || options.maxContentBytes <= 0) throw new Error("INVALID_MEMORY_CONTENT_LIMIT");
    await worker.assertIsolated();
    const owner = await options.owners.get(context);
    const identity = new MemoryIdentityService({ ...options, assertToolIsolation: () => worker.assertIsolated() });
    const client = new LazyMemoryClient({ owner, baseUrl: options.baseUrl, timeoutMs: options.requestTimeoutMs,
      connect: () => identity.connect(context), assertToolIsolation: () => worker.assertIsolated() });
    const store = new FileStateStore({ owner, directory: join(stateDirectory, "memory"), policyVersion: options.policyVersion });
    await store.read();
    return new UserMemoryRuntime(store, client, options);
  }

  extension(worker: IsolatedToolExecutor) {
    this.#assertOpen();
    return createOpenVikingExtension({ owner: this.#store.owner, client: this.#client, stateStore: this.#store,
      policy: this.#options.policy, assertToolIsolation: async () => { this.#assertOpen(); await worker.assertIsolated(); },
      wakeDelivery: () => { if (!this.#closed) this.#scheduler.wake(); } });
  }

  /** Server-authenticated settings calls only; never exposed as model tools. */
  setEnabled(enabled: boolean): Promise<void> {
    if (typeof enabled !== "boolean") return Promise.reject(new Error("INVALID_MEMORY_SETTING"));
    const pending = this.#settings.then(async () => {
      this.#assertOpen();
      if (enabled) {
        const state = await this.#store.read();
        this.#assertOpen();
        if (!state.authorization.enabled) await this.#delivery.enable(this.#options.policyVersion);
      } else await this.#delivery.pause();
    });
    this.#settings = pending.catch(() => {});
    return pending;
  }

  async status(): Promise<UserMemoryStatus> {
    this.#assertOpen();
    const state = await this.#store.read();
    // Explicit projection excludes owners, API keys, remote task/session IDs,
    // memory payloads and filesystem paths from the future browser surface.
    return { enabled: state.authorization.enabled, automaticCollection: state.authorization.automaticCollection,
      effectiveAt: state.authorization.effectiveAt, policyVersion: state.authorization.policyVersion,
      revision: state.revision };
  }

  async operation(id: string): Promise<UserMemoryOperation | undefined> {
    this.#assertOpen();
    const state = await this.#store.read();
    this.#assertOpen();
    const operation = Object.hasOwn(state.operations, id) ? state.operations[id] : undefined;
    if (!operation) return undefined;
    return projectMemoryOperation(operation);
  }

  async operations(cursor?: string): Promise<UserMemoryOperationPage> {
    this.#assertOpen();
    const state = await this.#store.read();
    this.#assertOpen();
    return memoryOperationPage(Object.values(state.operations), cursor);
  }

  /** Accept only a local receipt and ordinal; never accept a browser-supplied URI. */
  async content(id: string, index: number): Promise<UserMemoryContent | undefined> {
    this.#assertOpen();
    if (!Number.isSafeInteger(index) || index < 0) return undefined;
    const state = await this.#store.read();
    const operation = Object.hasOwn(state.operations, id) ? state.operations[id] : undefined;
    if (!operation || operation.phase !== "ready" || !operation.memoryUris?.[index]) return undefined;
    const text = await this.#client.readMemory(operation.memoryUris[index]);
    this.#assertOpen();
    if (Buffer.byteLength(text, "utf8") > this.#options.maxContentBytes) throw new Error("MEMORY_CONTENT_TOO_LARGE");
    // Governance or authorization changes while reading invalidate the response.
    // Do not cache memory bodies in the host or return stale deletion results.
    const latest = await this.#store.read();
    this.#assertOpen();
    if (latest.revision !== state.revision) throw new Error("MEMORY_CONTENT_CHANGED");
    return { operationId: id, index, total: operation.memoryUris.length, text };
  }

  #assertOpen(): void { if (this.#closed) throw new Error("MEMORY_RUNTIME_CLOSED"); }

  close(): Promise<void> {
    if (this.#closing) return this.#closing;
    this.#closed = true;
    this.#closing = (async () => {
      // Stop new scheduler claims, settle network requests, then let the active
      // tick persist its receipts before the host releases the user's workers.
      await this.#scheduler.stop(0);
      await this.#settings;
      await this.#client.close();
      // A timeout is not a persistence fence. Once sent network requests have
      // settled, retain the user's resources until the local tick also ends.
      await this.#scheduler.stop();
    })();
    return this.#closing;
  }
}

/** Composes with the supervisor's factory. Anonymous users keep ordinary
 * isolated tools; they receive no memory client, state or model tool. */
export function withUserMemory(
  protectedToolsForUser: (context: UserContext) => Promise<ProtectedSessionTools>,
  options: UserMemoryServices,
  onRuntime: (context: UserContext, runtime: UserMemoryRuntime | undefined) => void = () => {},
): (context: UserContext) => Promise<ProtectedSessionTools> {
  return async context => {
    const profile = await protectedToolsForUser(context);
    if (!("username" in context.user)) return profile;
    let runtime: UserMemoryRuntime | undefined;
    try {
      if (!profile.memoryStateDirectory) throw new Error("PROTECTED_MEMORY_STATE_REQUIRED");
      const workspace = join(context.folderPath, "workspaces", "default");
      const worker = await profile.resolveWorker(workspace);
      if (resolve(worker.workspace) !== resolve(workspace)) throw new Error("MEMORY_WORKER_WORKSPACE_MISMATCH");
      runtime = await UserMemoryRuntime.create(context, profile.memoryStateDirectory, worker, options);
      onRuntime(context, runtime);
      const bound = runtime;
      let disposing: Promise<void> | undefined;
      return { ...profile, memory: bound,
        createMemoryExtension: (_workspace, sessionWorker) => bound.extension(sessionWorker),
        dispose: () => disposing ??= (async () => {
          try { await bound.close(); }
          finally {
            try { onRuntime(context, undefined); }
            finally { await profile.dispose?.(); }
          }
        })(),
      };
    } catch (error) {
      try { await runtime?.close(); }
      finally {
        try { if (runtime) onRuntime(context, undefined); }
        finally { await profile.dispose?.(); }
      }
      throw error;
    }
  };
}
