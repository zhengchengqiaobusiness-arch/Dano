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
    tokenizerLimits: { maxAssetBytes: 65536, maxInputBytes: 8192, startupTimeoutMs: 2000, maxQueuedRequests: 8 },
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
    (value: any) => { value.tokenizerLimits.maxQueuedRequests = 0; },
    (value: any) => { value.tokenizers = []; },
    (value: any) => { value.tokenizers[0].tokenizer.path = "./workspace/model.json"; },
  ]) {
    const value = config(); change(value);
    expect(() => parseMemoryHostConfig(value)).toThrow("INVALID_MEMORY_HOST_CONFIG");
    try { parseMemoryHostConfig(value); } catch (error) { expect(String(error)).not.toContain("SYNTHETIC_PRIVATE_KEY"); }
  }
});
it("starts from a private config written before tokenizer queuing was introduced", () => {
  const old = config();
  const { maxQueuedRequests, ...previousLimits } = old.tokenizerLimits;
  const parsed = parseMemoryHostConfig({ ...old, tokenizerLimits: previousLimits });
  expect(parsed.tokenizerLimits).toEqual({ ...previousLimits, maxQueuedRequests });
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

it("accepts explicit bounded collection configuration and rejects model/tool payload overrides", () => {
  const collection = { policyVersion: "collection-v1", lifecycleTimeoutMs: 1000,
    model: { provider: "fixture", id: "selector", maxTokens: 512, temperature: 0, thinking: "disabled" },
    selector: { maxInputBytes: 8192, maxFacts: 5, timeoutMs: 1000 },
    scheduler: { pollIntervalMs: 10, mergeWindowMs: 20, maxWaitMs: 50, workTimeoutMs: 2000,
      leaseMs: 5000, initialBackoffMs: 50, maxBackoffMs: 100, maxAttempts: 2, maxRequestsPerBatch: 5 } };
  expect(parseMemoryHostConfig({ ...config(), collection }).collection).toEqual(collection);
  for (const mutate of [
    (value: any) => { value.model.payload = { tools: [{ name: "bash" }] }; },
    (value: any) => { value.model.modelsPath = "/user/workspace/models.json"; },
    (value: any) => { value.model.temperature = Infinity; },
    (value: any) => { value.model.thinking = "perhaps"; },
    (value: any) => { value.selector.complete = "untrusted-module"; },
    (value: any) => { value.scheduler.leaseMs = value.scheduler.workTimeoutMs; },
    (value: any) => { value.scheduler.mergeWindowMs = value.scheduler.maxWaitMs + 1; },
    (value: any) => { value.policyVersion = " "; },
  ]) {
    const changed = structuredClone(collection); mutate(changed);
    expect(() => parseMemoryHostConfig({ ...config(), collection: changed })).toThrow("INVALID_MEMORY_HOST_CONFIG");
  }
});

it("accepts a private bounded reranker endpoint and rejects unsafe overrides", () => {
  const reranker = { url: "http://reranker:8080/v1/rerank", model: "synthetic-reranker",
    minimumLogit: 0, timeoutMs: 900, maxInputBytes: 16384, maxDocumentBytes: 4096, maxCandidates: 2 };
  expect(parseMemoryHostConfig({ ...config(), reranker }).reranker).toEqual(reranker);
  for (const changed of [
    { ...reranker, url: "http://user:password@reranker:8080/v1/rerank" },
    { ...reranker, url: "http://reranker:8080/other" },
    { ...reranker, minimumLogit: Infinity },
    { ...reranker, timeoutMs: 0 },
    { ...reranker, maxDocumentBytes: 20000 },
    { ...reranker, maxCandidates: 0 },
  ]) expect(() => parseMemoryHostConfig({ ...config(), reranker: changed })).toThrow("INVALID_MEMORY_HOST_CONFIG");
});
