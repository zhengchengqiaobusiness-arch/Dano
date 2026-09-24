import { OwnerMemoryClient, type MemoryExtensionOptions, type MemoryGovernanceClient,
  type Owner } from "@josephyoung/pi-openviking/host";
import type { MemoryUserConnection } from "./memory-identity-service.js";
import type { MemoryReranker } from "./memory-reranker.js";
import type { MemoryRecoveryJournal } from "./memory-recovery-journal.js";

type Client = MemoryExtensionOptions["client"];
interface Options {
  owner: Owner;
  baseUrl: string;
  timeoutMs: number;
  /** Bound by the host to one authenticated UserContext, never a tool input. */
  connect(): Promise<MemoryUserConnection>;
  assertToolIsolation(): Promise<void>;
  reranker?: MemoryReranker;
  journal?: MemoryRecoveryJournal;
}

/** Local construction does not contact OpenViking or provision a USER key.
 * Share one instance across an owner's sessions; retain no mutable current-user
 * state and never pass this object to the untrusted tool worker. */
export class LazyMemoryClient implements Client, MemoryGovernanceClient {
  readonly owner: Owner;
  readonly scope: string | null = null;
  readonly #options: Options;
  readonly #active = new Set<Promise<unknown>>();
  #connecting?: Promise<OwnerMemoryClient>;
  #closed = false;
  #closing?: Promise<void>;

  constructor(options: Options) {
    if (![options.owner.accountId, options.owner.userId].every(id => typeof id === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(id))) {
      throw new Error("MEMORY_OWNER_MISMATCH");
    }
    const url = new URL(options.baseUrl);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash
      || url.pathname !== "/" || !Number.isSafeInteger(options.timeoutMs) || options.timeoutMs <= 0) {
      throw new Error("INVALID_MEMORY_CONNECTION");
    }
    this.owner = Object.freeze({ ...options.owner });
    this.#options = { ...options, owner: this.owner, baseUrl: url.origin };
  }

  async #connection(): Promise<OwnerMemoryClient> {
    this.#connecting ??= this.#options.connect().then(connection => {
      if (this.#closed) throw new Error("MEMORY_RUNTIME_CLOSED");
      if (connection.owner.accountId !== this.owner.accountId || connection.owner.userId !== this.owner.userId) {
        throw new Error("MEMORY_OWNER_MISMATCH");
      }
      return new OwnerMemoryClient({ owner: this.owner, baseUrl: this.#options.baseUrl,
        apiKey: connection.apiKey, timeoutMs: this.#options.timeoutMs });
    }).catch(error => { this.#connecting = undefined; throw error; });
    return this.#connecting;
  }

  #call<T>(operation: (client: OwnerMemoryClient) => Promise<T>, signal?: AbortSignal): Promise<T> {
    if (signal?.aborted) return Promise.reject(signal.reason);
    if (this.#closed) return Promise.reject(new Error("MEMORY_RUNTIME_CLOSED"));
    const pending = (async () => {
      this.#options.journal?.assertHealthy();
      await this.#options.assertToolIsolation();
      signal?.throwIfAborted();
      if (this.#closed) throw new Error("MEMORY_RUNTIME_CLOSED");
      const client = await this.#connection();
      await this.#options.assertToolIsolation();
      if (this.#closed) throw new Error("MEMORY_RUNTIME_CLOSED");
      signal?.throwIfAborted();
      this.#options.journal?.assertHealthy();
      // Preserve receipts of already-sent mutations even when shutdown starts.
      return operation(client);
    })();
    this.#active.add(pending);
    void pending.finally(() => this.#active.delete(pending)).catch(() => {});
    return pending;
  }

  createSession(id: string) { return this.#call(client => client.createSession(id)); }
  sessionExists(id: string) { return this.#call(client => client.sessionExists(id)); }
  append(operation: Parameters<Client["append"]>[0]) { return this.#call(client => client.append(operation)); }
  hasSource(operation: Parameters<Client["hasSource"]>[0]) { return this.#call(client => client.hasSource(operation)); }
  commit(id: string) { return this.#call(client => client.commit(id)); }
  findCommit(id: string) { return this.#call(client => client.findCommit(id)); }
  inspect(operation: Parameters<Client["inspect"]>[0]) { return this.#call(client => client.inspect(operation)); }
  readMemory(uri: string) { return this.#call(client => client.readMemory(uri)); }
  readMemoryLimited(uri: string, maxBytes: number) { return this.#call(client => client.readMemoryLimited(uri, maxBytes)); }
  memoryDocumentSize(uri: string) { return this.#call(client => client.memoryDocumentSize(uri)); }
  listMemoryDocuments() { return this.#call(client => client.listMemoryDocuments()); }
  writerSettled(operation: Parameters<MemoryGovernanceClient["writerSettled"]>[0]) {
    return this.#call(client => client.writerSettled(operation));
  }
  writerSettledAny(operation: Parameters<MemoryGovernanceClient["writerSettledAny"]>[0]) {
    return this.#call(client => client.writerSettledAny(operation));
  }
  clearOwnerData() { return this.#call(async client => {
    await this.#options.journal?.append({ kind: "clearOwnerData" });
    return client.clearOwnerData();
  }); }
  removeSource(operation: Parameters<MemoryGovernanceClient["removeSource"]>[0]) {
    return this.#call(async client => {
      await this.#options.journal?.append({ kind: "removeSource", remoteSessionId: operation.remoteSessionId });
      return client.removeSource(operation);
    });
  }
  replaceMemory(uri: string, content: string) { return this.#call(async client => {
    await this.#options.journal?.append({ kind: "replaceMemory", uri, content });
    return client.replaceMemory(uri, content);
  }); }
  removeMemory(uri: string) { return this.#call(async client => {
    await this.#options.journal?.append({ kind: "removeMemory", uri });
    return client.removeMemory(uri);
  }); }
  clearMemoryScope() { return this.#call(async client => {
    await this.#options.journal?.append({ kind: "clearMemoryScope" });
    return client.clearMemoryScope();
  }); }
  recall(query: string, limit: number, signal?: AbortSignal): ReturnType<Client["recall"]> {
    return this.#call(async client => {
      const found = await client.recall(query,
        this.#options.reranker ? Math.min(limit, this.#options.reranker.maxCandidates) : limit, signal);
      return this.#options.reranker ? this.#options.reranker.filter(query, found, signal) : found;
    }, signal);
  }

  /** Reject new work and wait for identity checks and sent requests to settle.
   * Caller must stop its scheduler first, then release workers after this fence. */
  close(): Promise<void> {
    if (this.#closing) return this.#closing;
    this.#closed = true;
    this.#closing = Promise.allSettled([...this.#active]).then(() => { this.#connecting = undefined; });
    return this.#closing;
  }
}
