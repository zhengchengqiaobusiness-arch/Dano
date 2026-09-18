import { OwnerMemoryClient, type MemoryExtensionOptions, type Owner } from "@josephyoung/pi-openviking/host";
import type { MemoryUserConnection } from "./memory-identity-service.js";

type Client = MemoryExtensionOptions["client"];
interface Options {
  owner: Owner;
  baseUrl: string;
  timeoutMs: number;
  /** Bound by the host to one authenticated UserContext, never a tool input. */
  connect(): Promise<MemoryUserConnection>;
  assertToolIsolation(): Promise<void>;
}

/** Local construction does not contact OpenViking or provision a USER key.
 * Share one instance across an owner's sessions; retain no mutable current-user
 * state and never pass this object to the untrusted tool worker. */
export class LazyMemoryClient implements Client {
  readonly owner: Owner;
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
      await this.#options.assertToolIsolation();
      signal?.throwIfAborted();
      if (this.#closed) throw new Error("MEMORY_RUNTIME_CLOSED");
      const client = await this.#connection();
      await this.#options.assertToolIsolation();
      if (this.#closed) throw new Error("MEMORY_RUNTIME_CLOSED");
      signal?.throwIfAborted();
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
  recall(query: string, limit: number, signal?: AbortSignal): ReturnType<Client["recall"]> {
    return this.#call(client => client.recall(query, limit, signal), signal);
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
