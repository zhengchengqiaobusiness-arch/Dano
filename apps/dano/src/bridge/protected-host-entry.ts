import { readFile } from "node:fs/promises";
import { isAbsolute } from "node:path";
import { fileURLToPath } from "node:url";
import { assertWorkerPrivacyEvidence } from "./linux-process-privacy.js";
import { WorkerSupervisorClient } from "./worker-supervisor-client.js";

export interface ProtectedHostProfile {
  hostUid: number;
  hostGid: number;
  startupTimeoutMs: number;
  operationTimeoutMs: number;
  maxConcurrentOperations: number;
  maxMessageBytes: number;
  trustedSkillPaths: string[];
  providerPythonModuleDirectory: string;
}

export function parseProtectedHostProfile(input: unknown): ProtectedHostProfile {
  if (!input || typeof input !== "object" || Array.isArray(input)) throw new Error("INVALID_PROTECTED_HOST_PROFILE");
  const value = input as Record<string, unknown>;
  const positive = (key: string) => {
    const item = value[key];
    if (typeof item !== "number" || !Number.isSafeInteger(item) || item <= 0) throw new Error("INVALID_PROTECTED_HOST_PROFILE");
    return item;
  };
  const hostUid = positive("hostUid");
  const hostGid = positive("hostGid");
  if (hostUid >= 2 ** 32 - 1 || hostGid >= 2 ** 32 - 1
    || !Array.isArray(value.trustedSkillPaths)
    || value.trustedSkillPaths.some(path => typeof path !== "string" || !isAbsolute(path))
    || typeof value.providerPythonModuleDirectory !== "string" || !isAbsolute(value.providerPythonModuleDirectory)) {
    throw new Error("INVALID_PROTECTED_HOST_PROFILE");
  }
  return { hostUid, hostGid, startupTimeoutMs: positive("startupTimeoutMs"),
    operationTimeoutMs: positive("operationTimeoutMs"), maxConcurrentOperations: positive("maxConcurrentOperations"),
    maxMessageBytes: positive("maxMessageBytes"), trustedSkillPaths: [...value.trustedSkillPaths] as string[],
    providerPythonModuleDirectory: value.providerPythonModuleDirectory };
}

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
  try {
    // Remove only the launcher's fixed profile argument; retain normal Dano CLI options.
    process.argv.splice(2, 1);
    const { runDanoMain } = await import("../main.js");
    return await runDanoMain({ signal: stopped.signal,
      protectedToolsForUser: context => client.profile(context, profile) });
  } finally {
    client.close();
    process.off("disconnect", disconnected);
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  runProtectedHost().then(code => { process.exitCode = code; }, () => {
    console.error("[dano] Protected host startup or shutdown failed.");
    process.exitCode = 1;
    if (process.connected) process.disconnect?.();
  });
}
