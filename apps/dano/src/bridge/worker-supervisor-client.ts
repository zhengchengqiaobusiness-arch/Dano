import { randomUUID } from "node:crypto";
import { isAbsolute, join, resolve } from "node:path";
import type { IsolatedToolExecutor } from "@josephyoung/pi-openviking/worker-tools";
import type { WorkerBrokerChannel } from "./worker-broker.js";
import type { ProtectedSessionTools } from "./protected-session-tools.js";
import type { UserContext } from "./user-context.js";
import type { SupervisorRpcLimits } from "./worker-supervisor-rpc.js";

interface Options extends SupervisorRpcLimits { startupTimeoutMs: number; operationTimeoutMs: number }
interface Pending {
  resolve(value: unknown): void;
  reject(error: Error): void;
  cleanup(): void;
  update?(value: unknown): void;
}
interface Descriptor { token: string; workspace: string; agentDir: string; stateDir: string }
const unavailable = () => new Error("SUPERVISOR_UNAVAILABLE");

/** Only the trusted non-root HTTP entrypoint owns this parent IPC channel. */
export class WorkerSupervisorClient {
  readonly #pending = new Map<string, Pending>();
  readonly #ready: Promise<void>;
  #closed = false;
  #initialized = false;
  #resolveReady!: () => void;
  #rejectReady!: (error: Error) => void;
  readonly #startupTimer: ReturnType<typeof setTimeout>;

  constructor(readonly channel: WorkerBrokerChannel, readonly options: Options) {
    if (![options.startupTimeoutMs, options.operationTimeoutMs, options.maxConcurrentOperations, options.maxMessageBytes]
      .every(value => Number.isSafeInteger(value) && value > 0)) throw new Error("INVALID_SUPERVISOR_RPC_LIMITS");
    this.#ready = new Promise((resolve, reject) => { this.#resolveReady = resolve; this.#rejectReady = reject; });
    void this.#ready.catch(() => {});
    this.#startupTimer = setTimeout(this.close, options.startupTimeoutMs);
    channel.on("message", this.#receive);
    channel.on("disconnect", this.close);
    channel.on("error", this.close);
    if (!channel.connected) this.close();
  }

  #receive = (message: unknown): void => {
    if (this.#closed || !message || typeof message !== "object" || Array.isArray(message)) return;
    try { if (Buffer.byteLength(JSON.stringify(message)) > this.options.maxMessageBytes) { this.close(); return; } }
    catch { this.close(); return; }
    const result = message as Record<string, unknown>;
    if (result.type === "ready") {
      if (this.#initialized) { this.close(); return; }
      this.#initialized = true;
      clearTimeout(this.#startupTimer);
      this.#resolveReady();
      return;
    }
    if (!this.#initialized || typeof result.id !== "string") { this.close(); return; }
    const pending = this.#pending.get(result.id);
    if (!pending) return;
    if (result.type === "update") {
      try { pending.update?.(result.value); }
      catch { this.#cancel(result.id, new Error("SUPERVISOR_UPDATE_FAILED")); }
      return;
    }
    this.#pending.delete(result.id);
    pending.cleanup();
    if (result.type === "result") pending.resolve(result.value);
    else pending.reject(new Error("SUPERVISOR_OPERATION_FAILED"));
  };

  #send(message: object): void {
    try { this.channel.send(message, error => { if (error) this.close(); }); }
    catch { this.close(); }
  }

  async #request(request: Record<string, unknown>, signal?: AbortSignal, update?: (value: unknown) => void): Promise<unknown> {
    signal?.throwIfAborted();
    await this.#ready;
    signal?.throwIfAborted();
    if (this.#closed || !this.channel.connected) throw unavailable();
    if (this.#pending.size >= this.options.maxConcurrentOperations) throw new Error("SUPERVISOR_REQUEST_LIMIT");
    const id = randomUUID();
    const message = { ...request, id };
    if (Buffer.byteLength(JSON.stringify(message)) > this.options.maxMessageBytes) throw new Error("SUPERVISOR_REQUEST_LIMIT");
    return new Promise((resolve, reject) => {
      const abort = () => this.#cancel(id, new Error("SUPERVISOR_CANCELLED"));
      const timer = setTimeout(() => this.#cancel(id, new Error("SUPERVISOR_TIMEOUT")), this.options.operationTimeoutMs);
      this.#pending.set(id, { resolve, reject, update, cleanup: () => {
        clearTimeout(timer); signal?.removeEventListener("abort", abort);
      } });
      signal?.addEventListener("abort", abort, { once: true });
      this.#send(message);
    });
  }

  #cancel(id: string, error: Error): void {
    const pending = this.#pending.get(id);
    if (!pending) return;
    this.#pending.delete(id);
    pending.cleanup();
    pending.reject(error);
    if (!this.#closed) this.#send({ type: "cancel", id });
  }

  async #acquire(owner: string, workspace: string): Promise<Descriptor> {
    const result = await this.#request({ type: "acquire", owner, workspace });
    if (!result || typeof result !== "object") throw unavailable();
    const value = result as Partial<Descriptor>;
    if (typeof value.token !== "string" || !/^[A-Za-z0-9_-]{1,80}$/.test(value.token)
      || value.workspace !== workspace || typeof value.agentDir !== "string" || !isAbsolute(value.agentDir)
      || typeof value.stateDir !== "string" || !isAbsolute(value.stateDir)) throw unavailable();
    return value as Descriptor;
  }

  /** Call only with the server's resolved UserContext and installation-owned paths. */
  async profile(context: UserContext, paths: Pick<ProtectedSessionTools, "trustedSkillPaths" | "providerPythonModuleDirectory">): Promise<ProtectedSessionTools> {
    const owner = context.user.id;
    const root = resolve(context.folderPath, "workspaces");
    let initial: Descriptor;
    try { initial = await this.#acquire(owner, join(root, "default")); }
    catch (error) {
      try { await this.#request({ type: "release", owner }); }
      catch (cleanupError) { throw new AggregateError([error, cleanupError], "SUPERVISOR_PROFILE_FAILED"); }
      throw error;
    }
    const cached = new Map<string, Promise<Descriptor>>([[initial.workspace, Promise.resolve(initial)]]);
    let disposed = false;
    let disposing: Promise<void> | undefined;
    const check = () => { if (disposed || this.#closed) throw unavailable(); };
    return {
      agentDir: initial.agentDir, trustedSkillPaths: [...paths.trustedSkillPaths],
      providerPythonModuleDirectory: paths.providerPythonModuleDirectory,
      resolveWorker: async workspace => {
        check();
        const canonical = resolve(workspace);
        // Only immediate workspaces belong to this profile. Root provisioning
        // independently checks canonical paths and rejects symlink escapes.
        if (resolve(canonical, "..") !== root || canonical === root) throw new Error("WORKER_OWNER_MISMATCH");
        let pending = cached.get(canonical);
        if (!pending) {
          pending = this.#acquire(owner, canonical).catch(error => { cached.delete(canonical); throw error; });
          cached.set(canonical, pending);
        }
        const lease = await pending;
        check();
        const worker: IsolatedToolExecutor = {
          workspace: canonical,
          assertIsolated: async () => { check(); await this.#request({ type: "assert", token: lease.token }); check(); },
          execute: async (name, parameters, signal, update) => {
            check();
            const value = await this.#request({ type: "execute", token: lease.token, name, parameters }, signal, update);
            check();
            return value;
          },
        };
        return worker;
      },
      dispose: () => disposing ??= (async () => {
        disposed = true;
        cached.clear();
        await this.#request({ type: "release", owner });
      })(),
    };
  }

  close = (): void => {
    if (this.#closed) return;
    this.#closed = true;
    clearTimeout(this.#startupTimer);
    this.#rejectReady(unavailable());
    this.channel.off("message", this.#receive);
    this.channel.off("disconnect", this.close);
    this.channel.off("error", this.close);
    for (const pending of this.#pending.values()) { pending.cleanup(); pending.reject(unavailable()); }
    this.#pending.clear();
    if (this.channel.connected) this.channel.disconnect();
  };
}
