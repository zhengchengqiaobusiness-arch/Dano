import { mkdtemp, writeFile, rm, symlink } from "node:fs/promises";
import { createHash } from "node:crypto";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { afterEach, expect, it } from "vitest";
import { MemoryTokenizers } from "../memory-tokenizer.js";
const roots: string[] = [], services: MemoryTokenizers[] = [];
afterEach(async () => {
  await Promise.all(services.splice(0).map(service => service.close()));
  await Promise.all(roots.splice(0).map(root => rm(root, { recursive: true, force: true })));
});
const model = { provider: "fixture", api: "openai-completions", id: "word-level" };
const limits = { maxAssetBytes: 10000, maxInputBytes: 500, startupTimeoutMs: 5000 };
async function fixture() {
  const root = await mkdtemp(join(tmpdir(), "dano-tokenizer-")); roots.push(root);
  const write = async (name: string, value: unknown) => {
    const text = JSON.stringify(value), path = join(root, name);
    await writeFile(path, text);
    return { path, sha256: createHash("sha256").update(text).digest("hex") };
  };
  return { model,
    tokenizer: await write("tokenizer.json", { version: "1.0", added_tokens: [], normalizer: null,
      pre_tokenizer: { type: "Whitespace" }, post_processor: null, decoder: null,
      model: { type: "WordLevel", vocab: { "[UNK]": 0, hello: 1, world: 2 }, unk_token: "[UNK]" } }),
    config: await write("tokenizer_config.json", { tokenizer_class: "PreTrainedTokenizerFast", unk_token: "[UNK]" }),
  };
}
async function start() {
  const binding = await fixture();
  const service = await MemoryTokenizers.create([binding], limits); services.push(service);
  return { service, binding };
}
it("counts with the configured tokenizer in a real worker and refuses an unbound model", async () => {
  const { service } = await start();
  expect(await service.countTokens("hello world", { model, signal: new AbortController().signal })).toBe(2);
  await expect(service.countTokens("hello world", { model: { ...model, provider: "other" }, signal: new AbortController().signal }))
    .rejects.toThrow("MEMORY_TOKENIZER_UNAVAILABLE");
});
it("checks asset hashes and rejects symlinks, oversized assets and duplicate model bindings", async () => {
  const binding = await fixture();
  await expect(MemoryTokenizers.create([{ ...binding, tokenizer: { ...binding.tokenizer, sha256: "0".repeat(64) } }], limits))
    .rejects.toThrow("MEMORY_TOKENIZER_UNAVAILABLE");
  const link = `${binding.tokenizer.path}.link`; await symlink(binding.tokenizer.path, link);
  await expect(MemoryTokenizers.create([{ ...binding, tokenizer: { ...binding.tokenizer, path: link } }], limits))
    .rejects.toThrow("MEMORY_TOKENIZER_UNAVAILABLE");
  await expect(MemoryTokenizers.create([binding], { ...limits, maxAssetBytes: 1 })).rejects.toThrow("MEMORY_TOKENIZER_UNAVAILABLE");
  await expect(MemoryTokenizers.create([binding, binding], limits)).rejects.toThrow("INVALID_MEMORY_TOKENIZER_BINDING");
});
it("bounds concurrent work, supports cancellation and rejects work after close", async () => {
  const { service } = await start();
  const controller = new AbortController();
  const first = service.countTokens("hello", { model, signal: controller.signal });
  const rejected = expect(first).rejects.toThrow("MEMORY_TOKENIZER_ABORTED");
  await expect(service.countTokens("world", { model, signal: new AbortController().signal })).rejects.toThrow("MEMORY_TOKENIZER_BUSY");
  controller.abort(); await rejected;
  await expect(service.countTokens("hello", { model, signal: controller.signal })).rejects.toBeDefined();
  const closing = service.close(); expect(service.close()).toBe(closing); await closing;
  await expect(service.countTokens("hello", { model, signal: new AbortController().signal })).rejects.toThrow("MEMORY_TOKENIZERS_CLOSED");
});
it("limits UTF-8 input bytes before posting work", async () => {
  const { service } = await start();
  await expect(service.countTokens("中".repeat(200), { model, signal: new AbortController().signal }))
    .rejects.toThrow("MEMORY_TOKENIZER_INPUT_TOO_LARGE");
});

it("keeps an otherwise idle process alive until its pending token count settles", async () => {
  const binding = await fixture();
  const script = `${binding.tokenizer.path}.mjs`;
  await writeFile(script, `import { MemoryTokenizers } from ${JSON.stringify(new URL("../memory-tokenizer.ts", import.meta.url).href)};
    const service = await MemoryTokenizers.create(${JSON.stringify([binding])}, ${JSON.stringify(limits)});
    try { console.log(await service.countTokens('hello world', { model: ${JSON.stringify(model)}, signal: new AbortController().signal })); }
    finally { await service.close(); }`);
  const result = await promisify(execFile)(process.execPath, [script], { timeout: 10000 });
  expect(result.stdout.trim()).toBe("2");
});
