import { expect, it, vi } from "vitest";
import { memoryCollectionModel, type MemoryCollectionModelRuntime } from "../memory-collection-model.js";
import type { MemoryCollectionHostConfig } from "../memory-host-config.js";

const configuration: MemoryCollectionHostConfig = { policyVersion: "collection-v1", lifecycleTimeoutMs: 1000,
  model: { provider: "fixture", id: "selector", maxTokens: 512, temperature: 0, thinking: "disabled" },
  selector: { maxInputBytes: 8192, maxFacts: 5, timeoutMs: 1000 },
  scheduler: { pollIntervalMs: 10, mergeWindowMs: 20, maxWaitMs: 50, workTimeoutMs: 2000,
    leaseMs: 5000, initialBackoffMs: 50, maxBackoffMs: 100, maxAttempts: 2, maxRequestsPerBatch: 5 } };
function fixture() {
  const model = { provider: "fixture", id: "selector" };
  const runtime = { getModel: vi.fn(() => model), getAuth: vi.fn(async () => ({ auth: { apiKey: "synthetic-provider-key" } })),
    completeSimple: vi.fn(async (..._args: any[]) => ({ stopReason: "stop", content: [
      { type: "thinking", thinking: "private reasoning" }, { type: "text", text: '{"facts":[]}' },
    ] })) };
  const create = vi.fn(async () => runtime as unknown as MemoryCollectionModelRuntime);
  return { runtime, create, selector: memoryCollectionModel(configuration, create, ["synthetic-management-key", "synthetic-encryption-key"]) };
}
it("uses deployment-selected model, no tools, bounded options and only returned text", async () => {
  const f = fixture(); expect(f.create).not.toHaveBeenCalled();
  const signal = new AbortController().signal;
  expect(await f.selector.sensitiveValues!(signal)).toEqual(["synthetic-management-key", "synthetic-encryption-key", "synthetic-provider-key"]);
  expect(await f.selector.complete({ systemPrompt: "Select facts", data: "quoted untrusted input", signal })).toBe('{"facts":[]}');
  expect(f.runtime.getModel).toHaveBeenCalledWith("fixture", "selector");
  const [, context, options] = f.runtime.completeSimple.mock.calls[0]!;
  expect(context).toEqual({ systemPrompt: "Select facts", messages: [{ role: "user", content: "quoted untrusted input", timestamp: expect.any(Number) }] });
  expect(options).toMatchObject({ signal, maxTokens: 512, temperature: 0 });
  const payload = { model: "selector", messages: context.messages };
  expect(options.onPayload(payload)).toEqual({ ...payload, thinking: { type: "disabled" } });
  expect(payload).not.toHaveProperty("thinking");
});
it("refreshes the secret snapshot and never leaks provider error detail", async () => {
  const f = fixture();
  await f.selector.sensitiveValues!();
  f.runtime.getAuth.mockResolvedValue({ auth: { apiKey: "rotated-provider-key" } });
  expect(await f.selector.sensitiveValues!()).toContain("rotated-provider-key");
  f.runtime.getAuth.mockRejectedValue(new Error("PRIVATE_PROVIDER_BODY"));
  await expect(f.selector.sensitiveValues!()).rejects.toThrow(/^MEMORY_COLLECTION_MODEL_UNAVAILABLE$/);
  f.runtime.completeSimple.mockRejectedValue(new Error("PRIVATE_PROVIDER_BODY"));
  await expect(f.selector.complete({ systemPrompt: "s", data: "d", signal: new AbortController().signal }))
    .rejects.toThrow(/^MEMORY_SELECTION_FAILED$/);
});
it("rejects cancelled work before looking up credentials or sending an inference", async () => {
  const f = fixture();
  const signal = AbortSignal.abort();
  await expect(f.selector.sensitiveValues!(signal)).rejects.toThrow("UNAVAILABLE");
  await expect(f.selector.complete({ systemPrompt: "s", data: "d", signal })).rejects.toThrow("SELECTION_FAILED");
  expect(f.create).not.toHaveBeenCalled();
});
for (const reason of ["length", "toolUse", "error"]) it(`rejects an incomplete model result: ${reason}`, async () => {
  const f = fixture(); f.runtime.completeSimple.mockResolvedValue({ stopReason: reason, content: [{ type: "text", text: "untrusted partial JSON" }] });
  await expect(f.selector.complete({ systemPrompt: "s", data: "d", signal: new AbortController().signal })).rejects.toThrow("SELECTION_FAILED");
});
