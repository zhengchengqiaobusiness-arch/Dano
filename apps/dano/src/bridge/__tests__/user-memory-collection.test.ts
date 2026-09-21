import { mkdtemp, mkdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, it } from "vitest";
import { SessionManager } from "@earendil-works/pi-coding-agent";
import { CollectionLifecycle, FileStateStore, MemoryDelivery } from "@josephyoung/pi-openviking/host";
import { MemoryUserProvenance } from "../memory-user-provenance.js";
import { UserMemoryCollection, type UserMemoryCollectionOptions } from "../user-memory-collection.js";

for (const attributed of [true, false]) it(`recovers original protected sources with attribution=${attributed}`, async () => {
  const root = await mkdtemp(join(tmpdir(), "dano-collection-"));
  const sessionRoot = join(root, "sessions");
  await mkdir(sessionRoot, { mode: 0o700 });
  const owner = { accountId: "test", userId: "alice" };
  const store = new FileStateStore({ owner, directory: join(root, "state"), policyVersion: "v1" });
  // Handoff is local; any attempted network delivery is a fixture failure.
  const transport = { owner } as ConstructorParameters<typeof MemoryDelivery>[0]["transport"];
  const delivery = new MemoryDelivery({ store, transport, maxPayloadBytes: 8192 });
  let calls = 0, wakes = 0;
  const configuration: UserMemoryCollectionOptions = { policyVersion: "v1", lifecycleTimeoutMs: 1000,
    selector: { timeoutMs: 1000, maxInputBytes: 8192, maxFacts: 5, complete: async ({ data }) => {
      calls++;
      expect(data).not.toContain("TEMPLATE_EXAMPLE");
      const message = JSON.parse(data).messages.find((message: { role: string }) => message.role === "user");
      return JSON.stringify({ facts: [{ sourceId: message.sourceId, quote: message.text }] });
    } },
    scheduler: { pollIntervalMs: 20, mergeWindowMs: 20, maxWaitMs: 50, workTimeoutMs: 2000,
      leaseMs: 5000, initialBackoffMs: 50, maxBackoffMs: 100, maxAttempts: 2, maxRequestsPerBatch: 5 } };
  const create = () => new UserMemoryCollection({ store, delivery, sessionRoot,
    provenance: new MemoryUserProvenance(owner), configuration, wakeDelivery: () => { wakes++; } });
  let collector = create();
  try {
    const session = SessionManager.create(root, sessionRoot);
    await collector.sessions.register(session);
    await delivery.enable("v1");
    await delivery.authorizeCollection({ policyVersion: "v1", scope: null, boundaries: [] });
    const lifecycle = new CollectionLifecycle(store);
    const request = await lifecycle.begin(session);
    const provenance = new MemoryUserProvenance(owner);
    const original = attributed ? "I prefer concise reports." : "/report";
    const dispatched = attributed ? original + "\nTEMPLATE_EXAMPLE file wrapper" : "/report";
    provenance.capture(session, original, dispatched);
    session.appendMessage({ role: "user", content: attributed ? dispatched : "TEMPLATE_EXAMPLE: I prefer XML reports.", timestamp: Date.now() });
    session.appendMessage({ role: "assistant", content: [{ type: "text", text: "Understood." }], api: "openai-completions",
      provider: "fixture", model: "fixture", stopReason: "stop", timestamp: Date.now(),
      usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } } });
    provenance.settle(session);
    await lifecycle.settle(request!, session);
    await collector.stop();
    // A new owner collector has no live session or transient input mapping.
    collector = create();
    collector.start();
    await expect.poll(async () => (await store.read()).collectionRequests![request!]!.phase,
      { timeout: 5000 }).toBe("processed");
    const operations = Object.values((await store.read()).operations);
    expect(operations).toHaveLength(attributed ? 1 : 0);
    expect(calls).toBe(attributed ? 1 : 0);
    if (attributed) {
      expect(operations[0]!.phase).toBe("queued");
      expect(wakes).toBeGreaterThan(0);
    }
  } finally { await collector.stop(); await rm(root, { recursive: true, force: true }); }
});
