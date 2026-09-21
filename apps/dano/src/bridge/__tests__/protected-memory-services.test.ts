import { chmod, mkdtemp, mkdir, readFile, readdir, realpath, rm, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, expect, it, vi } from "vitest";
import { createProtectedMemoryServices } from "../protected-memory-services.js";

const roots: string[] = [];
const services: NonNullable<Awaited<ReturnType<typeof createProtectedMemoryServices>>>[] = [];
afterEach(async () => {
  await Promise.all(services.splice(0).map(service => service.close()));
  await Promise.all(roots.splice(0).map(root => rm(root, { recursive: true, force: true })));
  vi.unstubAllGlobals();
});
async function fixture() {
  const root = await realpath(await mkdtemp(join(tmpdir(), "dano-memory-services-"))); roots.push(root);
  const directory = join(root, "config"), stateParent = join(root, "host-state"), stateDirectory = join(stateParent, "memory-service");
  await mkdir(directory, { mode: 0o700 }); await mkdir(stateParent, { mode: 0o700 });
  const asset = async (name: string, value: unknown) => {
    const bytes = JSON.stringify(value), path = join(root, name); await writeFile(path, bytes);
    return { path, sha256: createHash("sha256").update(bytes).digest("hex") };
  };
  const model = { provider: "fixture", api: "openai-completions", id: "word-level" };
  const config = { version: 1, baseUrl: "https://memory.example.test", accountId: "account", managementKey: "SYNTHETIC_MANAGEMENT_KEY",
    encryptionKey: "ab".repeat(32), encryptionKeyVersion: "v1", requestTimeoutMs: 1000, maxContentBytes: 16384, policyVersion: "v1",
    policy: { maxPayloadBytes: 4096, recallTimeoutMs: 1000, recallTokenBudget: 1500, recallLimit: 5, minimumScore: 0.5 },
    scheduler: { pollIntervalMs: 1000, initialBackoffMs: 1000, maxBackoffMs: 5000, maxAttemptsPerPhase: 5, maxOperationsPerTick: 4 },
    tokenizerLimits: { maxAssetBytes: 65536, maxInputBytes: 8192, startupTimeoutMs: 5000 },
    tokenizers: [{ model,
      tokenizer: await asset("tokenizer.json", { version: "1.0", added_tokens: [], normalizer: null,
        pre_tokenizer: { type: "Whitespace" }, post_processor: null, decoder: null,
        model: { type: "WordLevel", vocab: { "[UNK]": 0, hello: 1, world: 2 }, unk_token: "[UNK]" } }),
      config: await asset("tokenizer_config.json", { tokenizer_class: "PreTrainedTokenizerFast", unk_token: "[UNK]" }) }] };
  const save = () => writeFile(join(directory, "memory-service.json"), JSON.stringify(config), { mode: 0o600 });
  return { root, directory, stateParent, stateDirectory, config, model, save };
}
it("constructs private owner/credential services and a real model tokenizer without contacting the network", async () => {
  const f = await fixture(); await f.save();
  const network = vi.fn(() => { throw new Error("UNEXPECTED_NETWORK"); }); vi.stubGlobal("fetch", network);
  const service = (await createProtectedMemoryServices(f.directory, f.stateDirectory))!; services.push(service);
  const owner = await service.services.owners.get({ user: { id: "alice", username: "Alice" }, folderPath: join(f.root, "alice") });
  expect(await service.services.credentials.read(owner)).toBeUndefined();
  await service.services.credentials.write(owner, "SYNTHETIC_USER_KEY");
  expect(await service.services.credentials.read(owner)).toBe("SYNTHETIC_USER_KEY");
  const files = await readdir(join(f.stateDirectory, "credentials"));
  expect(await readFile(join(f.stateDirectory, "credentials", files[0]!), "utf8")).not.toContain("SYNTHETIC_USER_KEY");
  const context = { model: f.model, signal: new AbortController().signal };
  expect(await service.services.policy.countTokens("hello world", context)).toBe(2);
  expect(network).not.toHaveBeenCalled();
  const closing = service.close(); expect(service.close()).toBe(closing); await closing;
  await expect(service.services.policy.countTokens("hello", context)).rejects.toThrow("MEMORY_TOKENIZERS_CLOSED");
});
it("keeps missing configuration unconfigured but refuses invalid configuration and shared state", async () => {
  const f = await fixture();
  expect(await createProtectedMemoryServices(f.directory, f.stateDirectory)).toBeUndefined();
  await f.save(); await chmod(f.stateParent, 0o755);
  await expect(createProtectedMemoryServices(f.directory, f.stateDirectory)).rejects.toThrow("UNPROTECTED_MEMORY_SERVICE_STATE");
  await chmod(f.stateParent, 0o700);
  await writeFile(join(f.directory, "memory-service.json"), '{"managementKey":"SYNTHETIC_MANAGEMENT_KEY"}');
  await expect(createProtectedMemoryServices(f.directory, f.stateDirectory)).rejects.toThrow("INVALID_MEMORY_HOST_CONFIG");
});
it("fails initialization when configured tokenizer assets do not match", async () => {
  const f = await fixture(); f.config.tokenizers[0]!.tokenizer.sha256 = "0".repeat(64); await f.save();
  await expect(createProtectedMemoryServices(f.directory, f.stateDirectory)).rejects.toThrow("MEMORY_TOKENIZER_UNAVAILABLE");
});

it("wires configured collection lazily and requires a trusted deployment model factory", async () => {
  const f = await fixture();
  Object.assign(f.config, { collection: { policyVersion: "collection-v1", lifecycleTimeoutMs: 1000,
    model: { provider: "fixture", id: "selector", maxTokens: 512, temperature: 0, thinking: "disabled" },
    selector: { maxInputBytes: 8192, maxFacts: 5, timeoutMs: 1000 },
    taskFacts: { maxResponseBytes: 8192, maxFactBytes: 1024, contracts: [{ id: "report", method: "GET", path: "/report",
      success: { path: ["code"], equals: 0 }, actorPath: ["data", "owner"],
      fields: [{ label: "reference", path: ["data", "reference"], type: "string" }] }] },
    scheduler: { pollIntervalMs: 10, mergeWindowMs: 20, maxWaitMs: 50, workTimeoutMs: 2000,
      leaseMs: 5000, initialBackoffMs: 50, maxBackoffMs: 100, maxAttempts: 2, maxRequestsPerBatch: 5 } } });
  await f.save();
  await expect(createProtectedMemoryServices(f.directory, f.stateDirectory)).rejects.toThrow("MEMORY_COLLECTION_MODEL_REQUIRED");
  const factory = vi.fn(async () => { throw new Error("PRIVATE_PROVIDER_DETAIL"); });
  const service = (await createProtectedMemoryServices(f.directory, f.stateDirectory, factory))!;
  services.push(service);
  expect(service.services.collection?.policyVersion).toBe("collection-v1");
  expect(service.services.collection?.taskFacts?.config.contracts[0]?.id).toBe("report");
  expect(service.services.collection?.taskFacts?.key).toEqual(Buffer.from(f.config.encryptionKey, "hex"));
  expect(factory).not.toHaveBeenCalled();
  await expect(service.services.collection!.selector.sensitiveValues!()).rejects.toThrow(/^MEMORY_COLLECTION_MODEL_UNAVAILABLE$/);
  expect(factory).toHaveBeenCalledOnce();
});
