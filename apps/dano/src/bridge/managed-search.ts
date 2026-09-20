import { spawn, execFile } from "node:child_process";
import { promisify } from "node:util";
import { setTimeout as delay } from "node:timers/promises";

const execute = promisify(execFile);

/** Runs under the already unprivileged host identity, never under the supervisor. */
export async function startManagedSearch(options: {
  signal: AbortSignal;
  onFailure: () => void;
  host?: string;
  port?: string;
  executable?: string;
  prefixArgs?: string[];
  startupTimeoutMs?: number;
  shutdownTimeoutMs?: number;
}): Promise<{ close(): Promise<void> }> {
  options.signal.throwIfAborted();
  const executable = options.executable ?? "open-websearch";
  const prefix = options.prefixArgs ?? [];
  const host = options.host ?? "127.0.0.1", port = options.port ?? "3210";
  const child = spawn(executable, [...prefix, "serve", "--host", host, "--port", port],
    { stdio: ["ignore", "inherit", "inherit"] });
  let ended = false, closing = false, ready = false;
  let closePromise: Promise<void> | undefined;
  const exited = new Promise<void>(accept => {
    const finish = () => {
      if (ended) return;
      ended = true;
      if (ready && !closing) options.onFailure();
      accept();
    };
    child.once("error", finish);
    child.once("close", finish);
  });
  const close = (): Promise<void> => closePromise ??= (async () => {
    closing = true;
    options.signal.removeEventListener("abort", stop);
    if (ended) return;
    child.kill("SIGTERM");
    const deadline = setTimeout(() => child.kill("SIGKILL"), options.shutdownTimeoutMs ?? 5000);
    try { await exited; } finally { clearTimeout(deadline); }
  })();
  const stop = () => { void close(); };
  options.signal.addEventListener("abort", stop, { once: true });
  if (options.signal.aborted) stop();
  const startup = AbortSignal.any([options.signal, AbortSignal.timeout(options.startupTimeoutMs ?? 10000)]);
  try {
    while (true) {
      startup.throwIfAborted();
      if (ended) throw new Error("SEARCH_STARTUP_FAILED");
      try {
        await execute(executable, [...prefix, "status", "--base-url", `http://${host}:${port}`],
          { signal: startup, timeout: 1000, maxBuffer: 4096 });
        startup.throwIfAborted();
        if (ended) throw new Error("SEARCH_STARTUP_FAILED");
        ready = true;
        return { close };
      } catch {
        startup.throwIfAborted();
        if (ended) throw new Error("SEARCH_STARTUP_FAILED");
        await delay(100, undefined, { signal: startup });
      }
    }
  } catch (error) {
    await close();
    throw error;
  }
}
