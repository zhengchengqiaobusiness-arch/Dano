import { fork } from "node:child_process";
import { fileURLToPath } from "node:url";
import { expect, it, vi } from "vitest";
import { serveWorkerSupervisor } from "../worker-supervisor-rpc.js";
import { WorkerSupervisor, type WorkerSupervisorOptions } from "../worker-supervisor.js";

it("uses an actual child IPC channel for profile acquisition, streaming, cancellation and release", async () => {
  const closeWorker = vi.fn(async () => {});
  const cancelled = vi.fn();
  const pool = new WorkerSupervisor({ maxWorkers: 2 } as WorkerSupervisorOptions, async (_owner, workspace) => ({
    workspace, agentDir: "/private/agent", stateDir: "/private/state", worker: {
      workspace, async assertIsolated() {}, close: closeWorker,
      async execute(_name, parameters, signal, update) {
        update?.({ progress: true });
        if (parameters.wait) await new Promise<void>(resolve => signal!.addEventListener("abort", () => { cancelled(); resolve(); }, { once: true }));
        signal?.throwIfAborted();
        return { value: parameters.value, workspace };
      },
    },
  }));
  const child = fork(fileURLToPath(new URL("./fixtures/supervisor-host-child.mjs", import.meta.url)), [], {
    execArgv: ["--experimental-transform-types"], env: { PATH: process.env.PATH },
    stdio: ["ignore", "pipe", "pipe", "ipc"],
  });
  let stdout = ""; let stderr = "";
  child.stdout!.on("data", chunk => { stdout += chunk; });
  child.stderr!.on("data", chunk => { stderr += chunk; });
  const exited = new Promise<number | null>((resolve, reject) => {
    child.once("close", resolve); child.once("error", reject);
  });
  const server = serveWorkerSupervisor(pool, child, { maxConcurrentOperations: 8, maxMessageBytes: 4096 });
  const timer = setTimeout(() => child.kill("SIGKILL"), 10000);
  try {
    expect(await exited, stderr).toBe(0);
    await server.close();
    expect(JSON.parse(stdout)).toEqual({
      result: { value: "real-ipc", workspace: "/users/alice/workspaces/default" },
      updates: [{ progress: true }], cancelled: true, staleRejected: true,
    });
    expect(cancelled).toHaveBeenCalledTimes(1);
    expect(closeWorker).toHaveBeenCalledTimes(1);
  } finally {
    clearTimeout(timer);
    if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL");
    await exited;
    await server.close();
  }
}, 15000);
