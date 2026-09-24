import { isAbsolute, resolve } from "node:path";

export interface ProtectedHostProfile {
  hostUid: number;
  hostGid: number;
  startupTimeoutMs: number;
  operationTimeoutMs: number;
  maxConcurrentOperations: number;
  maxMessageBytes: number;
  trustedSkillPaths: string[];
  providerPythonModuleDirectory: string;
  /** Supervisor-derived paths only. Configuration contents never cross IPC. */
  memory?: { configurationDirectory: string; stateDirectory: string; recoveryDirectory: string };
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
  let memory: ProtectedHostProfile["memory"];
  if (value.memory !== undefined) {
    if (!value.memory || typeof value.memory !== "object" || Array.isArray(value.memory)) throw new Error("INVALID_PROTECTED_HOST_PROFILE");
    const paths = value.memory as Record<string, unknown>;
    if (Object.keys(paths).some(key => !["configurationDirectory", "stateDirectory", "recoveryDirectory"].includes(key))
      || [paths.configurationDirectory, paths.stateDirectory, paths.recoveryDirectory]
        .some(path => typeof path !== "string" || !isAbsolute(path) || resolve(path) !== path)) {
      throw new Error("INVALID_PROTECTED_HOST_PROFILE");
    }
    memory = { configurationDirectory: paths.configurationDirectory as string,
      stateDirectory: paths.stateDirectory as string, recoveryDirectory: paths.recoveryDirectory as string };
  }
  return { hostUid, hostGid, startupTimeoutMs: positive("startupTimeoutMs"),
    operationTimeoutMs: positive("operationTimeoutMs"), maxConcurrentOperations: positive("maxConcurrentOperations"),
    maxMessageBytes: positive("maxMessageBytes"), trustedSkillPaths: [...value.trustedSkillPaths] as string[],
    providerPythonModuleDirectory: value.providerPythonModuleDirectory, ...(memory ? { memory } : {}) };
}
