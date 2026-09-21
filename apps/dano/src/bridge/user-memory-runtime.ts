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
import { MemoryUserProvenance } from "./memory-user-provenance.js";
import { UserMemoryCollection, type UserMemoryCollectionOptions } from "./user-memory-collection.js";
import { MemoryTaskFacts, type ProviderTaskFactInput } from "./memory-task-facts.js";

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
  collection?: UserMemoryCollectionOptions;
}

/** One authenticated user's shared authorization/outbox, with per-session
 * extension factories. Construct only after supervisor isolation is verified. */
export class UserMemoryRuntime implements UserMemoryControls {
  readonly #store: FileStateStore;
  readonly #delivery: MemoryDelivery;
  readonly #scheduler: DeliveryScheduler;
  readonly #client: LazyMemoryClient;
  readonly #options: UserMemoryServices;
  readonly provenance: MemoryUserProvenance;
  readonly #collection?: UserMemoryCollection;
  #taskFacts?: MemoryTaskFacts;
  #settings: Promise<void> = Promise.resolve();
  #closed = false;
  #closing?: Promise<void>;
  #captureAuthorizationRead?: ReturnType<FileStateStore["read"]>;

  private constructor(store: FileStateStore, client: LazyMemoryClient, options: UserMemoryServices, sessionRoot?: string) {
    this.#store = store; this.#client = client; this.#options = options;
    this.provenance = new MemoryUserProvenance(store.owner);
    this.#delivery = new MemoryDelivery({ store, transport: client, maxPayloadBytes: options.policy.maxPayloadBytes });
    this.#scheduler = new DeliveryScheduler({ ...options.scheduler, store, delivery: this.#delivery });
    if (options.collection) {
      if (!sessionRoot) throw new Error("PROTECTED_MEMORY_SESSION_ROOT_REQUIRED");
      this.#collection = new UserMemoryCollection({ store, delivery: this.#delivery, sessionRoot,
        provenance: this.provenance, configuration: options.collection,
        wakeDelivery: () => { if (!this.#closed) this.#scheduler.wake(); } });
    }
  }

  static async create(context: UserContext, stateDirectory: string, worker: IsolatedToolExecutor,
    options: UserMemoryServices, sessionRoot?: string): Promise<UserMemoryRuntime> {
    if (!("username" in context.user)) throw new Error("MEMORY_LOGIN_REQUIRED");
    if (!Number.isSafeInteger(options.maxContentBytes) || options.maxContentBytes <= 0) throw new Error("INVALID_MEMORY_CONTENT_LIMIT");
    await worker.assertIsolated();
    const owner = await options.owners.get(context);
    const identity = new MemoryIdentityService({ ...options, assertToolIsolation: () => worker.assertIsolated() });
    const client = new LazyMemoryClient({ owner, baseUrl: options.baseUrl, timeoutMs: options.requestTimeoutMs,
      connect: () => identity.connect(context), assertToolIsolation: () => worker.assertIsolated() });
    const store = new FileStateStore({ owner, directory: join(stateDirectory, "memory"), policyVersion: options.policyVersion });
    const configured = options.collection;
    const taskFacts = configured?.taskFacts ? new MemoryTaskFacts({ ...configured.taskFacts, store,
      userId: context.user.id, policyVersion: configured.policyVersion, timeoutMs: configured.lifecycleTimeoutMs }) : undefined;
    const collection = configured ? { ...configured, selector: { ...configured.selector,
      ...(taskFacts ? { taskFacts: taskFacts.policy() } : {}),
      async sensitiveValues(signal?: AbortSignal) {
        await worker.assertIsolated();
        signal?.throwIfAborted();
        const [values, key] = await Promise.all([
          configured.selector.sensitiveValues?.(signal) ?? [], options.credentials.read(owner),
        ]);
        signal?.throwIfAborted();
        return key ? [...values, key] : values;
      },
      async complete(input: Parameters<typeof configured.selector.complete>[0]) {
        await worker.assertIsolated();
        input.signal.throwIfAborted();
        return configured.selector.complete(input);
      },
    } } : undefined;
    const runtime = new UserMemoryRuntime(store, client, { ...options, collection }, sessionRoot);
    runtime.#taskFacts = taskFacts;
    try {
      // Runtime creation completes before this user's authenticated controls or
      // sessions are published by UserRuntimeRegistry. Invalidate an obsolete
      // grant before either scheduler can claim work under the new host policy.
      const state = await store.read();
      if (state.authorization.automaticCollection && (!collection
        || state.authorization.collectionConsent?.policyVersion !== collection.policyVersion)) {
        await runtime.#delivery.revokeCollection();
      }
      runtime.#scheduler.start();
      runtime.#collection?.start();
      return runtime;
    } catch (error) {
      await runtime.close();
      throw error;
    }
  }

  extension(worker: IsolatedToolExecutor) {
    this.#assertOpen();
    const memory = createOpenVikingExtension({ owner: this.#store.owner, client: this.#client, stateStore: this.#store,
      policy: this.#options.policy, collection: this.#collection?.extension,
      assertToolIsolation: async () => { this.#assertOpen(); await worker.assertIsolated(); },
      wakeDelivery: () => { if (!this.#closed) this.#scheduler.wake(); } });
    return ((pi) => {
      pi.on("agent_settled", (_event, ctx) => {
        this.provenance.settle({
          getSessionId: () => ctx.sessionManager.getSessionId(),
          getEntry: id => ctx.sessionManager.getEntry(id),
          getEntries: () => ctx.sessionManager.getEntries(),
          getBranch: id => ctx.sessionManager.getBranch(id),
          appendCustomEntry: (type, data) => { pi.appendEntry(type, data); return ""; },
        });
      });
      return memory(pi);
    }) satisfies ReturnType<typeof createOpenVikingExtension>;
  }

  async captureInput(...input: Parameters<MemoryUserProvenance["capture"]>): Promise<() => void> {
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      this.#assertOpen();
      // A locked optional-memory store must not stall ordinary chat. Share one
      // pending read so repeated prompts cannot accumulate background lock waits.
      this.#captureAuthorizationRead ??= this.#store.read(AbortSignal.timeout(this.#options.policy.recallTimeoutMs))
        .finally(() => { this.#captureAuthorizationRead = undefined; });
      const state = await Promise.race([this.#captureAuthorizationRead,
        new Promise<undefined>(resolve => { timer = setTimeout(() => resolve(undefined), this.#options.policy.recallTimeoutMs); })]);
      this.#assertOpen();
      if (state?.authorization.enabled && state.authorization.automaticCollection) return this.provenance.capture(...input);
    } catch { /* Missing memory metadata must not prevent ordinary chat. */ }
    finally { clearTimeout(timer); }
    return () => {};
  }

  async captureTaskFact(input: ProviderTaskFactInput) {
    if (this.#closed) return undefined;
    return this.#taskFacts?.capture(input);
  }

  /** Server-authenticated settings calls only; never exposed as model tools. */
  setEnabled(enabled: boolean): Promise<void> {
    if (typeof enabled !== "boolean") return Promise.reject(new Error("INVALID_MEMORY_SETTING"));
    const pending = this.#settings.then(async () => {
      this.#assertOpen();
      if (enabled) {
        const state = await this.#store.read();
        this.#assertOpen();
        if (!state.authorization.enabled) await this.#delivery.enable(this.#options.policyVersion,
          await this.#collection?.sessions.boundaries(AbortSignal.timeout(this.#options.collection!.lifecycleTimeoutMs)) ?? []);
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
      revision: state.revision,
      ...(this.#options.collection || state.authorization.collectionConsent ? { collection: {
        availablePolicyVersion: this.#options.collection?.policyVersion ?? null,
        consent: state.authorization.collectionConsent ? {
          policyVersion: state.authorization.collectionConsent.policyVersion,
          scope: state.authorization.collectionConsent.scope,
          effectiveAt: state.authorization.collectionConsent.effectiveAt,
          revision: state.authorization.collectionConsent.revision,
        } : null,
      } } : {}) };
  }

  /** Separate consent, bound to the policy actually shown by the authenticated UI. */
  setAutomaticCollection(enabled: boolean, policyVersion?: string): Promise<void> {
    if (typeof enabled !== "boolean" || (enabled && (!policyVersion || typeof policyVersion !== "string"))
      || (!enabled && policyVersion !== undefined)) return Promise.reject(new Error("INVALID_MEMORY_SETTING"));
    const pending = this.#settings.then(async () => {
      this.#assertOpen();
      if (enabled) {
        const configuration = this.#options.collection;
        if (!this.#collection || !configuration || policyVersion !== configuration.policyVersion) {
          throw new Error("MEMORY_COLLECTION_POLICY_CHANGED");
        }
        const boundaries = await this.#collection.sessions.boundaries(
          AbortSignal.timeout(configuration.lifecycleTimeoutMs));
        this.#assertOpen();
        await this.#delivery.authorizeCollection({ policyVersion: configuration.policyVersion, scope: null, boundaries });
      } else {
        await this.#delivery.revokeCollection();
        this.provenance.clear();
      }
    });
    this.#settings = pending.catch(() => {});
    return pending;
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
    this.provenance.clear();
    this.#taskFacts?.close();
    this.#closing = (async () => {
      try { await this.#collection?.stop(); }
      finally {
        // Stop new scheduler claims, settle network requests, then let the active
        // tick persist its receipts before the host releases the user's workers.
        await this.#scheduler.stop(0);
        await this.#settings;
        await this.#client.close();
        // A timeout is not a persistence fence. Once sent network requests have
        // settled, retain the user's resources until the local tick also ends.
        await this.#scheduler.stop();
      }
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
): (context: UserContext, paths?: { sessionsRootPath: string }) => Promise<ProtectedSessionTools> {
  return async (context, paths) => {
    const profile = await protectedToolsForUser(context);
    if (!("username" in context.user)) return profile;
    let runtime: UserMemoryRuntime | undefined;
    try {
      if (!profile.memoryStateDirectory) throw new Error("PROTECTED_MEMORY_STATE_REQUIRED");
      const workspace = join(context.folderPath, "workspaces", "default");
      const worker = await profile.resolveWorker(workspace);
      if (resolve(worker.workspace) !== resolve(workspace)) throw new Error("MEMORY_WORKER_WORKSPACE_MISMATCH");
      await worker.assertIsolated();
      try {
        runtime = await UserMemoryRuntime.create(context, profile.memoryStateDirectory, worker, options, paths?.sessionsRootPath);
      } catch {
        // Memory persistence is optional for chat. Never repair or overwrite
        // failed owner/state data here, and never fall back to unisolated tools.
        // Recheck the boundary in case initialization failed on isolation itself.
        await worker.assertIsolated();
        return profile;
      }
      onRuntime(context, runtime);
      const bound = runtime;
      let disposing: Promise<void> | undefined;
      return { ...profile, memory: bound,
        captureMemoryInput: bound.captureInput.bind(bound),
        captureTaskFact: bound.captureTaskFact.bind(bound),
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
