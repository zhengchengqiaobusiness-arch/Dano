import { isAbsolute, resolve } from "node:path";
import type { ProtectedSupervisorOptions } from "./protected-supervisor.js";
import { parseProtectedHostProfile } from "./protected-host-profile.js";

const invalid = () => new Error("INVALID_PROTECTED_SUPERVISOR_PROFILE");
function record(input: unknown, keys: string[]): Record<string, unknown> {
  if (!input || typeof input !== "object" || Array.isArray(input)) throw invalid();
  if (Object.keys(input).some(key => !keys.includes(key))) throw invalid();
  return input as Record<string, unknown>;
}
function positive(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value <= 0) throw invalid();
  return value;
}
function path(value: unknown): string {
  if (typeof value !== "string" || !isAbsolute(value) || resolve(value) !== value) throw invalid();
  return value;
}

/** Administrator configuration only: reject implicit defaults and environment/secret fields. */
export function parseProtectedSupervisorProfile(input: unknown): ProtectedSupervisorOptions {
  const value = record(input, ["runtimeRoot", "sessionsRoot", "hostStateRoot", "memoryConfigDirectory",
    "memoryRecoveryDirectory", "identities", "maxWorkers", "broker", "host"]);
  if ((value.memoryConfigDirectory === undefined) !== (value.memoryRecoveryDirectory === undefined)) throw invalid();
  const identities = record(value.identities, ["directory", "firstUid", "firstGid", "count", "lockTimeoutMs"]);
  const broker = record(value.broker, ["installationDir", "hostUid", "hostGid", "piPackageContext", "privilegeGuard", "path",
    "startupTimeoutMs", "operationTimeoutMs", "shutdownTimeoutMs", "maxConcurrentOperations", "maxResultBytes"]);
  record(value.host, ["hostUid", "hostGid", "startupTimeoutMs", "operationTimeoutMs", "maxConcurrentOperations",
    "maxMessageBytes", "trustedSkillPaths", "providerPythonModuleDirectory"]);
  const host = parseProtectedHostProfile(value.host);
  if (broker.hostUid !== host.hostUid || broker.hostGid !== host.hostGid || typeof broker.path !== "string"
    || !broker.path || broker.path.split(":").some(entry => !isAbsolute(entry) || resolve(entry) !== entry)) throw invalid();
  return { runtimeRoot: path(value.runtimeRoot), sessionsRoot: path(value.sessionsRoot), hostStateRoot: path(value.hostStateRoot),
    maxWorkers: positive(value.maxWorkers), host,
    ...(value.memoryConfigDirectory === undefined ? {} : { memoryConfigDirectory: path(value.memoryConfigDirectory) }),
    ...(value.memoryRecoveryDirectory === undefined ? {} : { memoryRecoveryDirectory: path(value.memoryRecoveryDirectory) }),
    identities: { directory: path(identities.directory), firstUid: positive(identities.firstUid), firstGid: positive(identities.firstGid),
      count: positive(identities.count), lockTimeoutMs: positive(identities.lockTimeoutMs) },
    broker: { installationDir: path(broker.installationDir), hostUid: host.hostUid, hostGid: host.hostGid,
      piPackageContext: path(broker.piPackageContext), privilegeGuard: path(broker.privilegeGuard), path: broker.path,
      startupTimeoutMs: positive(broker.startupTimeoutMs), operationTimeoutMs: positive(broker.operationTimeoutMs),
      shutdownTimeoutMs: positive(broker.shutdownTimeoutMs), maxConcurrentOperations: positive(broker.maxConcurrentOperations),
      maxResultBytes: positive(broker.maxResultBytes) } };
}
