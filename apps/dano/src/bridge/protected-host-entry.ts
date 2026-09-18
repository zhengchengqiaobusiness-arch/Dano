import { readFile } from "node:fs/promises";
import { parseProtectedHostProfile } from "./protected-host-profile.js";
import { fileURLToPath } from "node:url";
import { assertWorkerPrivacyEvidence } from "./linux-process-privacy.js";
import { WorkerSupervisorClient } from "./worker-supervisor-client.js";
import { createProtectedMemoryServices } from "./protected-memory-services.js";
import { withUserMemory } from "./user-memory-runtime.js";

/** Spawned by the root supervisor after setpriv drops all host capabilities. */
export async function runProtectedHost(): Promise<number> {
  if (process.platform !== "linux" || !process.send || !process.connected) throw new Error("PROTECTED_HOST_REQUIRED");
  const profile = parseProtectedHostProfile(JSON.parse(process.argv[2] ?? "null"));
  if (process.getuid?.() !== profile.hostUid || process.getgid?.() !== profile.hostGid) throw new Error("PROTECTED_HOST_IDENTITY_MISMATCH");
  assertWorkerPrivacyEvidence(await readFile("/proc/self/status", "utf8"));
  const stopped = new AbortController();
  const disconnected = () => stopped.abort(new Error("SUPERVISOR_DISCONNECTED"));
  process.on("disconnect", disconnected);
  const client = new WorkerSupervisorClient({
    get connected() { return Boolean(process.connected); },
    on: process.on.bind(process), off: process.off.bind(process),
    send: (value, callback) => process.send!(value, callback),
    disconnect: () => { if (process.connected) process.disconnect?.(); },
  }, profile);
  let memory: Awaited<ReturnType<typeof createProtectedMemoryServices>>;
  try {
    if (profile.memory) memory = await createProtectedMemoryServices(profile.memory.configurationDirectory, profile.memory.stateDirectory);
    // Remove only the launcher's fixed profile argument; retain normal Dano CLI options.
    process.argv.splice(2, 1);
    const { runDanoMain } = await import("../main.js");
    const protectedToolsForUser = (context: Parameters<typeof client.profile>[0]) => client.profile(context, profile);
    return await runDanoMain({ signal: stopped.signal,
      protectedToolsForUser: memory ? withUserMemory(protectedToolsForUser, memory.services) : protectedToolsForUser });
  } finally {
    try { await memory?.close(); }
    finally { client.close(); process.off("disconnect", disconnected); }
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  runProtectedHost().then(code => { process.exitCode = code; }, () => {
    console.error("[dano] Protected host startup or shutdown failed.");
    process.exitCode = 1;
    if (process.connected) process.disconnect?.();
  });
}
