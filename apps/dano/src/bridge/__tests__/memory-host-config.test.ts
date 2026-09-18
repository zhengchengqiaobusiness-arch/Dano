import { chmod, link, mkdtemp, realpath, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, expect, it } from "vitest";
import { parseMemoryHostConfig, readMemoryHostConfig } from "../memory-host-config.js";
const roots: string[] = [];
afterEach(async () => { await Promise.all(roots.splice(0).map(root => rm(root, { recursive: true, force: true }))); });
function config() {
  return { version: 1, baseUrl: "https://memory.example.test/", accountId: "account", managementKey: "SYNTHETIC_PRIVATE_KEY",
    encryptionKey: "ab".repeat(32), encryptionKeyVersion: "v1", requestTimeoutMs: 1000,
    maxContentBytes: 16384, policyVersion: "v1",
    policy: { maxPayloadBytes: 4096, recallTimeoutMs: 1000, recallTokenBudget: 1500, recallLimit: 5, minimumScore: 0.5 },
    scheduler: { pollIntervalMs: 1000, initialBackoffMs: 1000, maxBackoffMs: 5000, maxAttemptsPerPhase: 5, maxOperationsPerTick: 4 },
    tokenizerLimits: { maxAssetBytes: 65536, maxInputBytes: 8192, startupTimeoutMs: 2000 },
    tokenizers: [{ model: { provider: "fixture", api: "openai-completions", id: "fixture" },
      tokenizer: { path: "/installed/tokenizer.json", sha256: "a".repeat(64) },
      config: { path: "/installed/tokenizer_config.json", sha256: "b".repeat(64) } }] };
}
async function directory() { const root = await realpath(await mkdtemp(join(tmpdir(), "dano-memory-config-"))); roots.push(root); return root; }
it("normalizes the service origin and requires explicit policies and model bindings", () => {
  const input = config(), result = parseMemoryHostConfig(input);
  expect(result).toEqual({ ...input, baseUrl: "https://memory.example.test" });
  for (const change of [
    (value: any) => { value.policy.countTokens = "untrusted-module"; },
    (value: any) => { value.tokenizers.push(value.tokenizers[0]); },
    (value: any) => { value.encryptionKey = "not-a-key"; },
    (value: any) => { value.baseUrl = "https://user:SYNTHETIC_PRIVATE_KEY@example.test/"; },
    (value: any) => { value.scheduler.maxBackoffMs = 1; },
    (value: any) => { value.policy.recallTokenBudget = 0; },
    (value: any) => { value.tokenizers = []; },
    (value: any) => { value.tokenizers[0].tokenizer.path = "./workspace/model.json"; },
  ]) {
    const value = config(); change(value);
    expect(() => parseMemoryHostConfig(value)).toThrow("INVALID_MEMORY_HOST_CONFIG");
    try { parseMemoryHostConfig(value); } catch (error) { expect(String(error)).not.toContain("SYNTHETIC_PRIVATE_KEY"); }
  }
});
it("loads private configuration and treats only a missing file as unconfigured", async () => {
  const root = await directory();
  expect(await readMemoryHostConfig(root)).toBeUndefined();
  await writeFile(join(root, "memory-service.json"), JSON.stringify(config()), { mode: 0o600 });
  expect(await readMemoryHostConfig(root)).toEqual(parseMemoryHostConfig(config()));
  await writeFile(join(root, "memory-service.json"), "{SYNTHETIC_PRIVATE_KEY");
  await expect(readMemoryHostConfig(root)).rejects.toThrow("INVALID_MEMORY_HOST_CONFIG");
});
it("rejects shared directory/file permissions, symlinks and hard-linked secret files", async () => {
  const root = await directory(), file = join(root, "memory-service.json"), source = join(root, "secret.json");
  await writeFile(source, JSON.stringify(config()), { mode: 0o600 });
  await symlink(source, file);
  await expect(readMemoryHostConfig(root)).rejects.toThrow("INVALID_MEMORY_HOST_CONFIG");
  await rm(file); await link(source, file);
  await expect(readMemoryHostConfig(root)).rejects.toThrow("INVALID_MEMORY_HOST_CONFIG");
  await rm(file); await writeFile(file, JSON.stringify(config()), { mode: 0o644 });
  await expect(readMemoryHostConfig(root)).rejects.toThrow("INVALID_MEMORY_HOST_CONFIG");
  await chmod(file, 0o600); await chmod(root, 0o755);
  await expect(readMemoryHostConfig(root)).rejects.toThrow("INVALID_MEMORY_HOST_CONFIG");
});
