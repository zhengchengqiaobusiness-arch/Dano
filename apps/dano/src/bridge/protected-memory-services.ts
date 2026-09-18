import { lstat, realpath } from "node:fs/promises";
import { dirname, isAbsolute, join } from "node:path";
import { readMemoryHostConfig } from "./memory-host-config.js";
import { MemoryOwnerRegistry } from "./memory-owner-registry.js";
import { MemoryCredentialStore } from "./memory-credential-store.js";
import { MemoryProvisioner } from "./memory-provisioner.js";
import { MemoryTokenizers } from "./memory-tokenizer.js";
import { ensureSafeDirectory } from "./safe-directory.js";
import type { UserMemoryServices } from "./user-memory-runtime.js";

/** Only the protected HTTP host constructs these services. No remote connection
 * is made at startup; authenticated users still need explicit memory consent. */
export async function createProtectedMemoryServices(configurationDirectory: string, stateDirectory: string):
Promise<{ services: UserMemoryServices; close(): Promise<void> } | undefined> {
  const config = await readMemoryHostConfig(configurationDirectory);
  if (!config) return undefined;
  const invalid = () => new Error("UNPROTECTED_MEMORY_SERVICE_STATE");
  const parent = dirname(stateDirectory);
  if (!isAbsolute(stateDirectory) || await realpath(parent) !== parent) throw invalid();
  const parentStat = await lstat(parent);
  if (!parentStat.isDirectory() || parentStat.uid !== process.getuid?.() || (parentStat.mode & 0o077)) throw invalid();
  await ensureSafeDirectory(stateDirectory, { unsafeDirectoryError: invalid });
  const state = await lstat(stateDirectory);
  if (await realpath(stateDirectory) !== stateDirectory || state.uid !== process.getuid?.() || (state.mode & 0o077)) throw invalid();
  const tokenizers = await MemoryTokenizers.create(config.tokenizers, config.tokenizerLimits);
  try {
    const services: UserMemoryServices = {
      owners: new MemoryOwnerRegistry({ directory: join(stateDirectory, "owners"), accountId: config.accountId }),
      credentials: new MemoryCredentialStore({ directory: join(stateDirectory, "credentials"),
        encryptionKey: Buffer.from(config.encryptionKey, "hex"), keyVersion: config.encryptionKeyVersion }),
      provisioner: new MemoryProvisioner({ baseUrl: config.baseUrl, accountId: config.accountId,
        managementKey: config.managementKey, timeoutMs: config.requestTimeoutMs }),
      baseUrl: config.baseUrl, requestTimeoutMs: config.requestTimeoutMs, maxContentBytes: config.maxContentBytes,
      policyVersion: config.policyVersion, policy: { ...config.policy, countTokens: tokenizers.countTokens },
      scheduler: config.scheduler,
    };
    return { services, close: () => tokenizers.close() };
  } catch (error) { await tokenizers.close(); throw error; }
}
