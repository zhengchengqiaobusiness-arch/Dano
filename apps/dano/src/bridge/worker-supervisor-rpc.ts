import { randomUUID } from "node:crypto";
import { workerOperations, type WorkerBrokerChannel } from "./worker-broker.js";
import type { SupervisedWorker, WorkerSupervisor } from "./worker-supervisor.js";

export interface SupervisorRpcLimits {
  maxConcurrentOperations: number;
  maxMessageBytes: number;
}
type Supervisor = Pick<WorkerSupervisor, "acquire" | "use" | "release" | "retire" | "close">;
interface Lease { owner: string; value: SupervisedWorker }
const validId = (value: unknown): value is string => typeof value === "string" && /^[A-Za-z0-9_-]{1,80}$/.test(value);
const validOwner = (value: unknown): value is string => typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(value);

/** The dedicated IPC channel belongs to the trusted HTTP host. User ownership
 * comes from its authenticated UserContext, never a browser/tool argument.
 * The root endpoint only selects existing worker executors; it never evaluates
 * commands, imports modules, or accepts environment/UID/config overrides. */
export function serveWorkerSupervisor(supervisor: Supervisor, channel: WorkerBrokerChannel,
  limits: SupervisorRpcLimits): { close(): Promise<void> } {
  if (![limits.maxConcurrentOperations, limits.maxMessageBytes].every(value => Number.isSafeInteger(value) && value > 0)) {
    throw new Error("INVALID_SUPERVISOR_RPC_LIMITS");
  }
  const leases = new Map<string, Lease>();
  const tokens = new WeakMap<SupervisedWorker, string>();
  const running = new Map<string, AbortController>();
  const releasing = new Set<string>();
  let closed = false;
  let closing: Promise<void> | undefined;
  const close = (): Promise<void> => {
    if (closing) return closing;
    closed = true;
    channel.off("message", receive);
    channel.off("disconnect", disconnected);
    channel.off("error", disconnected);
    for (const controller of running.values()) controller.abort();
    leases.clear();
    closing = Promise.resolve().then(() => supervisor.close()).finally(() => {
      if (channel.connected) channel.disconnect();
    });
    return closing;
  };
  // Keep a failed cleanup observable through close() without an unhandled rejection.
  const disconnected = () => { void close().catch(() => {}); };
  const send = (message: object) => {
    if (closed || !channel.connected) return;
    try {
      if (Buffer.byteLength(JSON.stringify(message)) > limits.maxMessageBytes) throw new Error();
      channel.send(message, error => { if (error) disconnected(); });
    } catch { disconnected(); }
  };
  const dispatch = async (request: Record<string, unknown>, signal: AbortSignal) => {
    const owner = request.owner;
    if (request.type === "acquire") {
      if (!validOwner(owner) || typeof request.workspace !== "string" || releasing.has(owner)) throw new Error();
      const value = await supervisor.acquire(owner, request.workspace);
      signal.throwIfAborted();
      if (closed || releasing.has(owner)) throw new Error();
      let token = tokens.get(value);
      if (!token) { token = randomUUID(); tokens.set(value, token); }
      leases.set(token, { owner, value });
      return { token, workspace: value.workspace, agentDir: value.agentDir, stateDir: value.stateDir };
    }
    if (request.type === "release" || request.type === "retire") {
      if (!validOwner(owner)) throw new Error();
      releasing.add(owner);
      for (const [token, lease] of leases) if (lease.owner === owner) leases.delete(token);
      // Removal happens before awaiting cleanup so old tokens cannot revive.
      await (request.type === "retire" ? supervisor.retire(owner) : supervisor.release(owner));
      if (request.type === "release") releasing.delete(owner);
      return null;
    }
    if ((request.type !== "assert" && request.type !== "execute") || !validId(request.token)) throw new Error();
    const lease = leases.get(request.token);
    if (!lease || releasing.has(lease.owner)) throw new Error();
    if (request.type === "execute" && (typeof request.name !== "string" || !workerOperations.has(request.name)
      || !request.parameters || typeof request.parameters !== "object" || Array.isArray(request.parameters))) throw new Error();
    return supervisor.use(lease.owner, lease.value.workspace, async current => {
      signal.throwIfAborted();
      if (leases.get(request.token as string) !== lease || current.agentDir !== lease.value.agentDir
        || current.stateDir !== lease.value.stateDir) throw new Error();
      await current.worker.assertIsolated();
      signal.throwIfAborted();
      if (leases.get(request.token as string) !== lease) throw new Error();
      return request.type === "assert" ? null : current.worker.execute(
        request.name as Parameters<SupervisedWorker["worker"]["execute"]>[0],
        request.parameters as Record<string, unknown>, signal,
        value => send({ type: "update", id: request.id, value }));
    });
  };
  const receive = (message: unknown) => {
    if (closed || !message || typeof message !== "object" || Array.isArray(message)) return;
    const request = message as Record<string, unknown>;
    try {
      if (Buffer.byteLength(JSON.stringify(request)) > limits.maxMessageBytes) { disconnected(); return; }
    } catch { disconnected(); return; }
    if (request.type === "shutdown") { disconnected(); return; }
    if (!validId(request.id)) return;
    const id = request.id;
    if (request.type === "cancel") { running.get(id)?.abort(); return; }
    if (running.has(id)) { disconnected(); return; }
    if (running.size >= limits.maxConcurrentOperations) {
      send({ type: "error", id, code: "SUPERVISOR_REQUEST_LIMIT" });
      return;
    }
    const controller = new AbortController();
    running.set(id, controller);
    void (async () => {
      try {
        const value = await dispatch(request, controller.signal);
        controller.signal.throwIfAborted();
        send({ type: "result", id, value });
      } catch { send({ type: "error", id, code: "SUPERVISOR_OPERATION_FAILED" }); }
      finally { running.delete(id); }
    })();
  };
  channel.on("message", receive);
  channel.on("disconnect", disconnected);
  channel.on("error", disconnected);
  if (!channel.connected) disconnected();
  else send({ type: "ready" });
  return { close };
}
