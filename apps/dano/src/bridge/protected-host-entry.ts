import { readFile } from "node:fs/promises";
import { parseProtectedHostProfile } from "./protected-host-profile.js";
import { fileURLToPath } from "node:url";
import { assertWorkerPrivacyEvidence } from "./linux-process-privacy.js";
import { WorkerSupervisorClient } from "./worker-supervisor-client.js";
import { createProtectedMemoryServices } from "./protected-memory-services.js";
import { withUserMemory } from "./user-memory-runtime.js";
import { startManagedSearch } from "./managed-search.js";
import { ModelRuntime, getAgentDir } from "@earendil-works/pi-coding-agent";
import { join } from "node:path";

/** Spawned by the root supervisor after setpriv drops all host capabilities. */
export async function runProtectedHost(): Promise<number> {
  if (process.platform !== "linux" || !process.send || !process.connected) throw new Error("PROTECTED_HOST_REQUIRED");
  const profile = parseProtectedHostProfile(JSON.parse(process.argv[2] ?? "null"));
  if (process.getuid?.() !== profile.hostUid || process.getgid?.() !== profile.hostGid) throw new Error("PROTECTED_HOST_IDENTITY_MISMATCH");
  assertWorkerPrivacyEvidence(await readFile("/proc/self/status", "utf8"));
  const stopped = new AbortController();
  const disconnected = () => stopped.abort(new Error("SUPERVISOR_DISCONNECTED"));
  process.on("disconnect", disconnected);
  const terminate = () => stopped.abort();
  process.on("SIGTERM", terminate);
  process.on("SIGINT", terminate);
  const client = new WorkerSupervisorClient({
    get connected() { return Boolean(process.connected); },
    on: process.on.bind(process), off: process.off.bind(process),
    send: (value, callback) => process.send!(value, callback),
    disconnect: () => { if (process.connected) process.disconnect?.(); },
  }, profile);
  let memory: Awaited<ReturnType<typeof createProtectedMemoryServices>>;
  let search: Awaited<ReturnType<typeof startManagedSearch>> | undefined;
  let searchFailed = false;
  try {
    let collectionModels: Promise<ModelRuntime> | undefined;
    if (profile.memory) memory = await createProtectedMemoryServices(profile.memory.configurationDirectory, profile.memory.stateDirectory,
      () => {
        // Resolve lazily, after runDanoMain has selected the deployment agent
        // directory. Never use a per-user resource directory for credentials.
        stopped.signal.throwIfAborted();
        collectionModels ??= ModelRuntime.create({ authPath: join(getAgentDir(), "auth.json"),
          modelsPath: join(getAgentDir(), "models.json"), refreshOnCreate: false, allowModelNetwork: false,
          signal: stopped.signal }).catch(error => { collectionModels = undefined; throw error; });
        return collectionModels;
      });
    search = await startManagedSearch({ signal: stopped.signal,
      host: process.env.OPEN_WEBSEARCH_HOST, port: process.env.OPEN_WEBSEARCH_PORT,
      onFailure: () => { searchFailed = true; stopped.abort(new Error("SEARCH_DAEMON_EXITED")); } });
    // Remove only the launcher's fixed profile argument; retain normal Dano CLI options.
    process.argv.splice(2, 1);
    const { runDanoMain } = await import("../main.js");
    const protectedToolsForUser = (context: Parameters<typeof client.profile>[0]) => client.profile(context, profile);
    const code = await runDanoMain({ signal: stopped.signal,
      protectedToolsForUser: memory ? withUserMemory(protectedToolsForUser, memory.services) : protectedToolsForUser });
    return searchFailed ? 1 : code;
  } finally {
    try { await search?.close(); }
    finally {
      try { await memory?.close(); }
      finally {
        client.close(); process.off("disconnect", disconnected);
        process.off("SIGTERM", terminate); process.off("SIGINT", terminate);
      }
    }
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  runProtectedHost().then(code => { process.exitCode = code; }, () => {
    console.error("[dano] Protected host startup or shutdown failed.");
    process.exitCode = 1;
    if (process.connected) process.disconnect?.();
  });
}
