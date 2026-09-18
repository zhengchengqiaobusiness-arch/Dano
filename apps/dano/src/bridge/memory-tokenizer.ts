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
}
const key = (model: MemoryModelIdentity) => JSON.stringify([model.provider, model.api, model.id]);
interface Pending { id: number; resolve(value: number): void; reject(error: Error): void; cleanup(): void }
interface Slot { worker: Worker; failed: boolean; pending?: Pending }

/** Trusted, explicitly configured model bindings. No network downloads or model-name guesses. */
export class MemoryTokenizers {
  readonly #slots = new Map<string, Slot>();
  readonly #limits: MemoryTokenizerLimits;
  #closed = false;
  #closing?: Promise<void>;
  #sequence = 0;
  private constructor(limits: MemoryTokenizerLimits) { this.#limits = { ...limits }; }

  static async create(bindings: readonly MemoryTokenizerBinding[], limits: MemoryTokenizerLimits): Promise<MemoryTokenizers> {
    if (![limits.maxAssetBytes, limits.maxInputBytes, limits.startupTimeoutMs]
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
    const slot: Slot = { worker, failed: false };
    this.#slots.set(key(binding.model), slot);
    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => {
        slot.failed = true; reject(new Error("MEMORY_TOKENIZER_STARTUP_TIMEOUT"));
      }, this.#limits.startupTimeoutMs);
      const fail = () => {
        slot.failed = true; clearTimeout(timer);
        slot.pending?.cleanup(); slot.pending?.reject(new Error("MEMORY_TOKENIZER_UNAVAILABLE"));
        slot.pending = undefined; reject(new Error("MEMORY_TOKENIZER_UNAVAILABLE"));
      };
      worker.on("error", fail);
      worker.on("exit", fail);
      worker.on("message", message => {
        if (message?.type === "ready") { clearTimeout(timer); resolve(); return; }
        const pending = slot.pending;
        if (!pending || message?.id !== pending.id) return;
        slot.pending = undefined; pending.cleanup();
        if (message.type === "count" && Number.isSafeInteger(message.count) && message.count >= 0) pending.resolve(message.count);
        else pending.reject(new Error("MEMORY_TOKENIZER_UNAVAILABLE"));
      });
    });
    worker.unref();
  }

  /** One outstanding count per model. Saturation omits recall instead of queuing chat behind it. */
  countTokens = (text: string, context: { model: MemoryModelIdentity; signal: AbortSignal }): Promise<number> => {
    try {
      context.signal.throwIfAborted();
      if (this.#closed) throw new Error("MEMORY_TOKENIZERS_CLOSED");
      const slot = this.#slots.get(key(context.model));
      if (!slot || slot.failed) throw new Error("MEMORY_TOKENIZER_UNAVAILABLE");
      if (slot.pending) throw new Error("MEMORY_TOKENIZER_BUSY");
      if (Buffer.byteLength(text, "utf8") > this.#limits.maxInputBytes) throw new Error("MEMORY_TOKENIZER_INPUT_TOO_LARGE");
      return new Promise<number>((resolve, reject) => {
        const id = ++this.#sequence;
        const abort = () => { slot.worker.unref(); reject(new Error("MEMORY_TOKENIZER_ABORTED")); };
        const cleanup = () => { context.signal.removeEventListener("abort", abort); slot.worker.unref(); };
        slot.pending = { id, resolve, reject, cleanup };
        context.signal.addEventListener("abort", abort, { once: true });
        slot.worker.ref();
        // An aborted job keeps its slot until the worker finishes, bounding the queue.
        try { slot.worker.postMessage({ id, text }); }
        catch { slot.pending = undefined; cleanup(); reject(new Error("MEMORY_TOKENIZER_UNAVAILABLE")); }
      });
    } catch (error) { return Promise.reject(error); }
  };

  close(): Promise<void> {
    if (this.#closing) return this.#closing;
    this.#closed = true;
    for (const slot of this.#slots.values()) {
      slot.pending?.cleanup(); slot.pending?.reject(new Error("MEMORY_TOKENIZERS_CLOSED")); slot.pending = undefined;
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
