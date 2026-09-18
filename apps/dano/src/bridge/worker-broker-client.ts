import type { ChildProcess } from "node:child_process";
import { randomUUID } from "node:crypto";
import type { IsolatedToolExecutor } from "@josephyoung/pi-openviking/worker-tools";

interface Options {
  workspace: string;
  startupTimeoutMs: number;
  operationTimeoutMs: number;
  shutdownTimeoutMs: number;
  maxConcurrentOperations: number;
  maxResultBytes: number;
  /** Trusted supervisor checks the broker's kernel identity, not its messages. */
  assertBrokerIdentity(): Promise<void>;
}
interface Pending {
  resolve(value: unknown): void;
  reject(error: Error): void;
  cleanup(): void;
  update?(value: unknown): void;
}
const unavailable = () => new Error("WORKER_BROKER_UNAVAILABLE");

/** Owns one supervisor-created broker child. Never reconnects or falls back to
 * host tools after that process dies; callers must acquire a new worker. */
export class WorkerBrokerClient implements IsolatedToolExecutor {
  readonly workspace: string;
  readonly #child: ChildProcess;
  readonly #options: Options;
  readonly #pending = new Map<string, Pending>();
  readonly #ready: Promise<void>;
  readonly #exited: Promise<void>;
  #closed = false;
  #closing?: Promise<void>;

  constructor(child: ChildProcess, options: Options) {
    if (![options.startupTimeoutMs, options.operationTimeoutMs, options.shutdownTimeoutMs,
      options.maxConcurrentOperations, options.maxResultBytes].every(value => Number.isSafeInteger(value) && value > 0)) {
      throw new Error("INVALID_WORKER_BROKER_LIMITS");
    }
    this.workspace = options.workspace;
    this.#child = child;
    this.#options = options;
    this.#exited = child.exitCode !== null || child.signalCode !== null
      ? Promise.resolve() : new Promise(resolve => {
        child.once("exit", () => resolve());
        child.once("close", () => resolve()); // A spawn error need not emit exit.
      });
    this.#ready = new Promise((resolve, reject) => {
      let ready = false;
      const timer = setTimeout(() => { reject(unavailable()); void this.close(); }, options.startupTimeoutMs);
      const failed = () => {
        clearTimeout(timer);
        reject(unavailable());
        this.#failPending();
        void this.close();
      };
      child.once("error", failed);
      child.once("exit", failed);
      child.once("disconnect", failed);
      child.on("message", message => {
        if (this.#closed || !message || typeof message !== "object") return;
        let size: number;
        try { size = Buffer.byteLength(JSON.stringify(message)); } catch { void this.close(); return; }
        if (size > options.maxResultBytes) { void this.close(); return; }
        const result = message as Record<string, unknown>;
        if (result.type === "ready") {
          if (ready) { void this.close(); return; }
          ready = true;
          clearTimeout(timer);
          resolve();
          return;
        }
        if (!ready || typeof result.id !== "string") { void this.close(); return; }
        const pending = this.#pending.get(result.id);
        if (!pending) return; // A cancelled operation can still send its last update.
        if (result.type === "update") {
          try { pending.update?.(result.value); }
          catch { this.#cancel(result.id, new Error("WORKER_BROKER_UPDATE_FAILED")); }
          return;
        }
        this.#pending.delete(result.id);
        pending.cleanup();
        if (result.type === "result") pending.resolve(result.value);
        else pending.reject(new Error("WORKER_BROKER_OPERATION_FAILED"));
      });
      if (!child.connected || child.exitCode !== null || child.signalCode !== null) failed();
    });
    void this.#ready.catch(() => {});
  }

  async #check(signal?: AbortSignal): Promise<void> {
    signal?.throwIfAborted();
    if (signal) {
      await new Promise<void>((resolve, reject) => {
        const abort = () => { signal.removeEventListener("abort", abort); reject(signal.reason); };
        signal.addEventListener("abort", abort, { once: true });
        this.#ready.then(() => { signal.removeEventListener("abort", abort); resolve(); },
          error => { signal.removeEventListener("abort", abort); reject(error); });
      });
    } else await this.#ready;
    signal?.throwIfAborted();
    if (this.#closed || !this.#child.connected) throw unavailable();
    await this.#options.assertBrokerIdentity();
    signal?.throwIfAborted();
    if (this.#closed || !this.#child.connected) throw unavailable();
  }

  async assertIsolated(): Promise<void> {
    await this.#check();
    await this.#request({ type: "assert" });
  }

  async execute(name: Parameters<IsolatedToolExecutor["execute"]>[0], parameters: Record<string, unknown>,
    signal?: AbortSignal, onUpdate?: (value: unknown) => void): Promise<unknown> {
    await this.#check(signal);
    return this.#request({ type: "execute", name, parameters }, signal, onUpdate);
  }

  #request(request: Record<string, unknown>, signal?: AbortSignal, update?: (value: unknown) => void): Promise<unknown> {
    signal?.throwIfAborted();
    if (this.#closed || !this.#child.connected) return Promise.reject(unavailable());
    if (this.#pending.size >= this.#options.maxConcurrentOperations) return Promise.reject(new Error("WORKER_BROKER_REQUEST_LIMIT"));
    const id = randomUUID();
    const message = { ...request, id };
    if (Buffer.byteLength(JSON.stringify(message)) > this.#options.maxResultBytes) return Promise.reject(new Error("WORKER_BROKER_REQUEST_LIMIT"));
    return new Promise((resolve, reject) => {
      const abort = () => this.#cancel(id, new Error("WORKER_BROKER_CANCELLED"));
      const timer = setTimeout(() => this.#cancel(id, new Error("WORKER_BROKER_TIMEOUT")), this.#options.operationTimeoutMs);
      this.#pending.set(id, { resolve, reject, update, cleanup: () => {
        clearTimeout(timer); signal?.removeEventListener("abort", abort);
      } });
      signal?.addEventListener("abort", abort, { once: true });
      this.#send(message);
    });
  }

  #send(message: object): void {
    try { this.#child.send(message, error => { if (error) { this.#failPending(); void this.close(); } }); }
    catch { this.#failPending(); void this.close(); }
  }

  #cancel(id: string, error: Error): void {
    const pending = this.#pending.get(id);
    if (!pending) return;
    this.#pending.delete(id);
    pending.cleanup();
    pending.reject(error);
    if (this.#child.connected) this.#send({ type: "cancel", id });
  }

  #failPending(): void {
    this.#closed = true;
    for (const pending of this.#pending.values()) { pending.cleanup(); pending.reject(unavailable()); }
    this.#pending.clear();
  }

  close(): Promise<void> {
    if (this.#closing) return this.#closing;
    this.#failPending();
    this.#closing = Promise.resolve().then(async () => {
      // Register the deadline before asking the broker to exit. Waiting on the
      // actual child exit prevents reporting shutdown while it remains alive.
      const timer = setTimeout(() => {
        if (this.#child.exitCode === null && this.#child.signalCode === null) this.#child.kill("SIGKILL");
      }, this.#options.shutdownTimeoutMs);
      if (this.#child.connected) this.#send({ type: "shutdown" });
      try { await this.#exited; } finally { clearTimeout(timer); }
    });
    return this.#closing;
  }
}
