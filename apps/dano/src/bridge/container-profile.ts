import { resolve, join } from "node:path";
import type { ProtectedSupervisorOptions } from "./protected-supervisor.js";

/** Container defaults, independent of whether the memory service is configured.
 * Runtime/session paths retain the ordinary server's existing environment contract.
 */
export function containerProfile(env: NodeJS.ProcessEnv, installation: string, args: readonly string[] = []): ProtectedSupervisorOptions {
  const integer = (name: string, fallback: number) => {
    const value = env[name] === undefined ? fallback : Number(env[name]);
    if (!Number.isSafeInteger(value) || value <= 0) throw new Error(`INVALID_${name}`);
    return value;
  };
  const runtimeRoot = resolve(env.DANO_RUNTIME_DIR?.trim() || "/opt/dano/runtime-data");
  let sessionsRoot = resolve(env.DANO_SESSIONS_ROOT?.trim() || env.PI_WEB_SESSIONS_ROOT?.trim() || join(runtimeRoot, ".dano/sessions"));
  for (let index = 0; index < args.length; index++) {
    if (args[index] !== "--sessions-root") continue;
    const value = args[++index];
    if (!value || value.startsWith("--")) throw new Error("Missing value for --sessions-root");
    sessionsRoot = resolve(value);
  }
  const privateRoot = resolve(env.DANO_HOST_DATA_DIR?.trim() || "/var/lib/dano-host");
  const hostUid = integer("DANO_HOST_UID", 1000), hostGid = integer("DANO_HOST_GID", 1000);
  const memoryConfigDirectory = env.DANO_MEMORY_CONFIG_DIR?.trim();
  return {
    runtimeRoot, sessionsRoot, hostStateRoot: join(privateRoot, "state"),
    identities: { directory: join(privateRoot, "identities"), firstUid: integer("DANO_WORKER_FIRST_UID", 10001),
      firstGid: integer("DANO_WORKER_FIRST_GID", 10001), count: integer("DANO_WORKER_IDENTITY_COUNT", 50000), lockTimeoutMs: 5000 },
    maxWorkers: integer("DANO_MAX_WORKERS", 16),
    broker: { installationDir: installation, hostUid, hostGid, piPackageContext: join(installation, "package.json"),
      privilegeGuard: "/usr/bin/setpriv", path: env.PATH || "/usr/local/lib/dano-python/bin:/usr/local/bin:/usr/bin:/bin",
      startupTimeoutMs: 30000, operationTimeoutMs: 120000, shutdownTimeoutMs: 5000,
      maxConcurrentOperations: 16, maxResultBytes: 16 * 1024 * 1024 },
    host: { hostUid, hostGid, startupTimeoutMs: 30000, operationTimeoutMs: 150000,
      maxConcurrentOperations: 32, maxMessageBytes: 32 * 1024 * 1024,
      trustedSkillPaths: [join(installation, "open-websearch-skill-seed/.agents/skills")],
      providerPythonModuleDirectory: join(installation, "dist/server/python") },
    ...(memoryConfigDirectory ? { memoryConfigDirectory: resolve(memoryConfigDirectory),
      memoryRecoveryDirectory: resolve(env.DANO_MEMORY_RECOVERY_DIR?.trim() || "/var/lib/dano-memory-recovery") } : {}),
  };
}
