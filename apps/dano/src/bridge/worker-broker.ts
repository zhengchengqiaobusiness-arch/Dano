import type { IsolatedToolExecutor } from "@josephyoung/pi-openviking/worker-tools";
import type { EventEmitter } from "node:events";

export interface WorkerBrokerChannel extends Pick<EventEmitter, "on" | "off"> {
  readonly connected: boolean;
  send(value: object, callback: (error: Error | null) => void): unknown;
  disconnect(): void;
}
interface BrokerLimits { maxConcurrentOperations: number; maxResultBytes: number }
type Worker = IsolatedToolExecutor & { close(): void };
export const workerOperations: ReadonlySet<string> = new Set(["read", "write", "edit", "bash", "grep", "find", "ls", "user_bash"]);

export function assertWorkerProviderApi(capabilities: unknown): void {
  if (!capabilities || typeof capabilities !== "object"
    || (capabilities as Record<string, unknown>).protectedWorkerProviderApiVersion !== 1) {
    throw new Error("PROTECTED_WORKER_PROVIDER_API_REQUIRED");
  }
}

/** A fixed RPC surface in the already-dropped broker process. No filesystem,
 * environment, module loading or process creation is exposed by this protocol. */
export function serveWorkerBroker(worker: Worker, channel: WorkerBrokerChannel, limits: BrokerLimits): () => void {
  if (![limits.maxConcurrentOperations, limits.maxResultBytes].every(value => Number.isSafeInteger(value) && value > 0)) {
    worker.close();
    throw new Error("INVALID_WORKER_BROKER_LIMITS");
  }
  const running = new Map<string, AbortController>();
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    channel.off("message", receive);
    channel.off("disconnect", close);
    for (const controller of running.values()) controller.abort();
    worker.close();
    if (channel.connected) channel.disconnect();
  };
  const send = (message: object) => {
    if (closed || !channel.connected) return;
    try {
      if (Buffer.byteLength(JSON.stringify(message)) > limits.maxResultBytes) throw new Error();
      channel.send(message, error => { if (error) close(); });
    } catch { close(); }
  };
  const receive = (message: unknown) => {
    if (closed || !message || typeof message !== "object" || Array.isArray(message)) return;
    const request = message as Record<string, unknown>;
    try {
      if (Buffer.byteLength(JSON.stringify(request)) > limits.maxResultBytes) { close(); return; }
    } catch { close(); return; }
    if (request.type === "shutdown") { close(); return; }
    if (typeof request.id !== "string" || !/^[A-Za-z0-9_-]{1,80}$/.test(request.id)) return;
    const id = request.id;
    if (request.type === "cancel") { running.get(id)?.abort(); return; }
    if (running.has(id)) { close(); return; }
    if ((request.type !== "assert" && request.type !== "execute")
      || running.size >= limits.maxConcurrentOperations
      || (request.type === "execute" && (typeof request.name !== "string" || !workerOperations.has(request.name)
        || !request.parameters || typeof request.parameters !== "object" || Array.isArray(request.parameters)))) {
      send({ type: "error", id, code: "WORKER_BROKER_REQUEST_REJECTED" });
      return;
    }
    const controller = new AbortController();
    running.set(id, controller);
    void (async () => {
      try {
        await worker.assertIsolated();
        controller.signal.throwIfAborted();
        const value = request.type === "assert" ? null : await worker.execute(
          request.name as Parameters<Worker["execute"]>[0], request.parameters as Record<string, unknown>,
          controller.signal, value => send({ type: "update", id, value }));
        controller.signal.throwIfAborted();
        send({ type: "result", id, value });
      } catch { send({ type: "error", id, code: "WORKER_BROKER_OPERATION_FAILED" }); }
      finally { running.delete(id); }
    })();
  };
  channel.on("message", receive);
  channel.on("disconnect", close);
  if (!channel.connected) close();
  else send({ type: "ready" });
  return close;
}
