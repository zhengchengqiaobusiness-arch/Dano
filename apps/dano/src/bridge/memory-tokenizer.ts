import { Worker, isMainThread, parentPort, workerData } from "node:worker_threads";
import { createHash } from "node:crypto";
import { constants } from "node:fs";
import { open } from "node:fs/promises";
import { isAbsolute } from "node:path";

export interface MemoryModelIdentity { provider: string; api: string; id: string }
export interface MemoryTokenizerBinding {
  model: MemoryModelIdentity;
  tokenizer: { path: string; sha256: string };
  config: { path: string; sha256: string };
}
export interface MemoryTokenizerLimits {
  maxAssetBytes: number;
  maxInputBytes: number;
  startupTimeoutMs: number;
  maxQueuedRequests: number;
}
/** Bounded queue for version-1 private configs created before this field existed. */
export const DEFAULT_MAX_QUEUED_REQUESTS = 8;
const key = (model: MemoryModelIdentity) => JSON.stringify([model.provider, model.api, model.id]);
interface CountRequest {
  id: number;
  text: string;
  signal: AbortSignal;
  resolve(value: number): void;
  reject(error: Error): void;
  abort(): void;
  settled: boolean;
}
interface Slot { worker: Worker; failed: boolean; pending?: CountRequest; queue: CountRequest[] }

/** Trusted, explicitly configured model bindings. No network downloads or model-name guesses. */
export class MemoryTokenizers {
  readonly #slots = new Map<string, Slot>();
  readonly #limits: MemoryTokenizerLimits;
  #closed = false;
  #closing?: Promise<void>;
  #sequence = 0;
  private constructor(limits: MemoryTokenizerLimits) { this.#limits = { ...limits }; }

  static async create(bindings: readonly MemoryTokenizerBinding[], limits: MemoryTokenizerLimits): Promise<MemoryTokenizers> {
    if (![limits.maxAssetBytes, limits.maxInputBytes, limits.startupTimeoutMs, limits.maxQueuedRequests]
      .every(value => Number.isSafeInteger(value) && value > 0)) throw new Error("INVALID_MEMORY_TOKENIZER_LIMITS");
    const service = new MemoryTokenizers(limits);
    try {
      for (const binding of bindings) {
        if (![binding.model.provider, binding.model.api, binding.model.id].every(value => typeof value === "string" && value.length > 0)
          || [binding.tokenizer, binding.config].some(asset => !isAbsolute(asset.path) || !/^[a-f0-9]{64}$/.test(asset.sha256))
          || service.#slots.has(key(binding.model))) throw new Error("INVALID_MEMORY_TOKENIZER_BINDING");
        await service.#start(binding);
      }
      return service;
    } catch (error) { await service.close(); throw error; }
  }

  async #start(binding: MemoryTokenizerBinding): Promise<void> {
    // This module is a build entry; using its own URL keeps source and built
    // execution on the same implementation without eval or dynamic user code.
    const worker = new Worker(new URL(import.meta.url), { workerData: { kind: "dano-memory-tokenizer", binding, limits: this.#limits }, execArgv: [] });
    const slot: Slot = { worker, failed: false, queue: [] };
    this.#slots.set(key(binding.model), slot);
    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => {
        slot.failed = true; reject(new Error("MEMORY_TOKENIZER_STARTUP_TIMEOUT"));
      }, this.#limits.startupTimeoutMs);
      const fail = () => {
        slot.failed = true; clearTimeout(timer);
        if (slot.pending) {
          const pending = slot.pending;
          slot.pending = undefined;
          worker.unref();
          this.#settle(pending, new Error("MEMORY_TOKENIZER_UNAVAILABLE"));
        }
        for (const queued of slot.queue.splice(0)) this.#settle(queued, new Error("MEMORY_TOKENIZER_UNAVAILABLE"));
        reject(new Error("MEMORY_TOKENIZER_UNAVAILABLE"));
      };
      worker.on("error", fail);
      worker.on("exit", fail);
      worker.on("message", message => {
        if (message?.type === "ready") { clearTimeout(timer); resolve(); return; }
        const pending = slot.pending;
        if (!pending || message?.id !== pending.id) return;
        slot.pending = undefined; worker.unref();
        if (message.type === "count" && Number.isSafeInteger(message.count) && message.count >= 0) {
          this.#settle(pending, undefined, message.count);
        } else this.#settle(pending, new Error("MEMORY_TOKENIZER_UNAVAILABLE"));
        this.#dispatch(slot);
      });
    });
    worker.unref();
  }

  #settle(request: CountRequest, error?: Error, count?: number): void {
    if (request.settled) return;
    request.settled = true;
    request.signal.removeEventListener("abort", request.abort);
    if (error) request.reject(error);
    else request.resolve(count!);
  }

  #dispatch(slot: Slot): void {
    if (this.#closed || slot.failed || slot.pending) return;
    while (slot.queue.length) {
      const next = slot.queue.shift()!;
      if (next.signal.aborted) {
        this.#settle(next, new Error("MEMORY_TOKENIZER_ABORTED"));
        continue;
      }
      slot.pending = next;
      slot.worker.ref();
      try { slot.worker.postMessage({ id: next.id, text: next.text }); }
      catch {
        slot.pending = undefined;
        slot.worker.unref();
        this.#settle(next, new Error("MEMORY_TOKENIZER_UNAVAILABLE"));
        continue;
      }
      return;
    }
  }

  /** Bounded per-model FIFO; each caller's recall deadline also bounds its wait. */
  countTokens = (text: string, context: { model: MemoryModelIdentity; signal: AbortSignal }): Promise<number> => {
    try {
      context.signal.throwIfAborted();
      if (this.#closed) throw new Error("MEMORY_TOKENIZERS_CLOSED");
      const slot = this.#slots.get(key(context.model));
      if (!slot || slot.failed) throw new Error("MEMORY_TOKENIZER_UNAVAILABLE");
      if (Buffer.byteLength(text, "utf8") > this.#limits.maxInputBytes) throw new Error("MEMORY_TOKENIZER_INPUT_TOO_LARGE");
      if (slot.pending && slot.queue.length >= this.#limits.maxQueuedRequests) throw new Error("MEMORY_TOKENIZER_BUSY");
      return new Promise<number>((resolve, reject) => {
        const request: CountRequest = { id: ++this.#sequence, text, signal: context.signal,
          resolve, reject, abort: () => {}, settled: false };
        request.abort = () => {
          if (slot.pending !== request) {
            const index = slot.queue.indexOf(request);
            if (index >= 0) slot.queue.splice(index, 1);
          }
          // Keep an active job's slot until the worker acknowledges it.
          this.#settle(request, new Error("MEMORY_TOKENIZER_ABORTED"));
        };
        context.signal.addEventListener("abort", request.abort, { once: true });
        slot.queue.push(request);
        this.#dispatch(slot);
      });
    } catch (error) { return Promise.reject(error); }
  };

  close(): Promise<void> {
    if (this.#closing) return this.#closing;
    this.#closed = true;
    for (const slot of this.#slots.values()) {
      if (slot.pending) {
        const pending = slot.pending;
        slot.pending = undefined;
        slot.worker.unref();
        this.#settle(pending, new Error("MEMORY_TOKENIZERS_CLOSED"));
      }
      for (const queued of slot.queue.splice(0)) this.#settle(queued, new Error("MEMORY_TOKENIZERS_CLOSED"));
    }
    this.#closing = Promise.all([...this.#slots.values()].map(slot => slot.worker.terminate()))
      .then(() => { this.#slots.clear(); });
    return this.#closing;
  }
}

if (!isMainThread && parentPort && workerData?.kind === "dano-memory-tokenizer") {
  const { binding, limits } = workerData as { binding: MemoryTokenizerBinding; limits: MemoryTokenizerLimits };
  const read = async (asset: MemoryTokenizerBinding["tokenizer"]) => {
    const file = await open(asset.path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    try {
      const stat = await file.stat();
      if (!stat.isFile() || stat.size > limits.maxAssetBytes) throw new Error("INVALID_TOKENIZER_ASSET");
      const bytes = await file.readFile();
      if (bytes.length > limits.maxAssetBytes || createHash("sha256").update(bytes).digest("hex") !== asset.sha256) {
        throw new Error("INVALID_TOKENIZER_ASSET");
      }
      return JSON.parse(bytes.toString("utf8"));
    } finally { await file.close(); }
  };
  const { Tokenizer } = await import("@huggingface/tokenizers");
  const tokenizer = new Tokenizer(await read(binding.tokenizer), await read(binding.config));
  parentPort.on("message", ({ id, text }: { id: number; text: string }) => {
    try {
      if (typeof text !== "string" || Buffer.byteLength(text) > limits.maxInputBytes) throw new Error();
      parentPort!.postMessage({ type: "count", id, count: tokenizer.encode(text, { add_special_tokens: false }).ids.length });
    } catch { parentPort!.postMessage({ type: "error", id }); }
  });
  parentPort.postMessage({ type: "ready" });
}
