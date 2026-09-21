import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, expect, it } from "vitest";
import { FileStateStore, MemoryDelivery, CollectionLifecycle, CollectionInputBuilder } from "@josephyoung/pi-openviking/host";
import { SessionManager } from "@earendil-works/pi-coding-agent";
import { MemoryTaskFacts, parseMemoryTaskFactConfig, type ProviderTaskFactInput } from "../memory-task-facts.js";
import { oauthUserId } from "../oauth-user-id.js";

const roots: string[] = [];
afterEach(async () => { await Promise.all(roots.splice(0).map(root => rm(root, { recursive: true, force: true }))); });
const config = () => ({ maxResponseBytes: 16384, maxFactBytes: 4096, contracts: [{
  id: "confirmed-report", method: "POST", path: "/reports/submit",
  success: { path: ["code"], equals: 0 }, actorPath: ["data", "owner"],
  fields: [{ label: "report", path: ["data", "reference"], type: "string" as const }],
}] });
async function harness() {
  const root = await mkdtemp(join(tmpdir(), "dano-task-facts-")); roots.push(root);
  const owner = { accountId: "fixture", userId: "memory-alice" };
  const store = new FileStateStore({ owner, directory: join(root, "state"), policyVersion: "v1" });
  const delivery = new MemoryDelivery({ store, transport: { owner } as never, maxPayloadBytes: 8192 });
  const options = { config: config(), key: Buffer.alloc(32, 7), store, userId: oauthUserId("oa-alice"), policyVersion: "v1", timeoutMs: 1000 };
  const facts = new MemoryTaskFacts(options);
  const input: ProviderTaskFactInput = { toolName: "bash", toolCallId: "call-a", loginSessionBound: true,
    request: { method: "POST", path: "/reports/submit" }, response: { ok: true, status: 200, headers: {},
      body: JSON.stringify({ code: 0, data: { owner: "oa-alice", reference: "REPORT-42", private: "MUST_NOT_PROJECT" } }) } };
  const result = (receipts: unknown[], override = {}) => ({ role: "toolResult", toolName: "bash", toolCallId: "call-a",
    content: [{ type: "text", text: "UNTRUSTED_STDOUT" }], details: { danoTaskFacts: receipts }, isError: false, timestamp: Date.now(), ...override } as const);
  const project = (receipts: unknown[], override = {}, selected = facts) => selected.policy().tools.get("bash")!(result(receipts, override) as never,
    { owner, scope: null });
  await delivery.enable("v1");
  return { input, facts, options, store, delivery, project };
}
it("requires independent consent and projects only configured fields from verified successful owned responses", async () => {
  const h = await harness();
  expect(await h.facts.capture(h.input)).toBeUndefined();
  await h.delivery.authorizeCollection({ policyVersion: "v1", scope: null, boundaries: [] });
  const receipt = await h.facts.capture(h.input); expect(receipt).toBeDefined();
  const text = await h.project([receipt]);
  expect(text).toContain("REPORT-42");
  for (const forbidden of ["MUST_NOT_PROJECT", "UNTRUSTED_STDOUT", "oa-alice"]) expect(text).not.toContain(forbidden);
  expect(await h.project([receipt], {}, new MemoryTaskFacts(h.options))).toBe(text);
});
it("rejects failure, foreign actors, malformed bodies, unbound credentials and unapproved routes", async () => {
  const h = await harness(); await h.delivery.authorizeCollection({ policyVersion: "v1", scope: null, boundaries: [] });
  const body = (value: unknown) => ({ ok: true as const, status: 200, headers: {}, body: JSON.stringify(value) });
  const invalid: Partial<ProviderTaskFactInput>[] = [
    { loginSessionBound: false }, { response: { ...body({}), status: 503 } },
    { response: body({ code: 1, data: { owner: "oa-alice", reference: "REPORT-42" } }) },
    { response: body({ code: 0, data: { owner: "oa-bob", reference: "REPORT-42" } }) },
    { response: body({ code: 0, data: { owner: "oa-alice", reference: { private: "no" } } }) },
    { response: { ...body({}), body: "not json" } },
    { response: { ...body({}), body: "x".repeat(16385) } },
    { request: { method: "GET", path: "/reports/submit" } },
    { request: { method: "POST", path: "https://foreign.invalid/reports/submit" } },
    { request: { method: "POST", path: "/reports/%73ubmit" } },
    { signal: AbortSignal.abort() },
  ];
  for (const change of invalid) expect(await h.facts.capture({ ...h.input, ...change })).toBeUndefined();
});
it("rejects forged receipts, another tool call/owner, and replay after revoke or pause/resume", async () => {
  const h = await harness(); await h.delivery.authorizeCollection({ policyVersion: "v1", scope: null, boundaries: [] });
  const receipt = (await h.facts.capture(h.input))!;
  expect(await h.project([{ ...receipt, data: receipt.data.replace("REPORT-42", "FORGED") }])).toBeUndefined();
  expect(await h.project([receipt], { toolCallId: "call-b" })).toBeUndefined();
  expect(await h.project([receipt], { toolName: "read" })).toBeUndefined();
  expect(await h.project([receipt], { isError: true })).toBeUndefined();
  const foreign = new MemoryTaskFacts({ ...h.options, store: { ...h.store, owner: { accountId: "fixture", userId: "bob" }, read: h.store.read.bind(h.store) } as never });
  expect(await h.project([receipt], {}, foreign)).toBeUndefined();
  await h.delivery.revokeCollection(); expect(await h.project([receipt])).toBeUndefined();
  await h.delivery.authorizeCollection({ policyVersion: "v1", scope: null, boundaries: [] });
  expect(await h.project([receipt])).toBeUndefined();
  const renewed = (await h.facts.capture(h.input))!; expect(await h.project([renewed])).toContain("REPORT-42");
  await h.delivery.pause(); await h.delivery.enable("v1");
  expect(await h.project([renewed])).toBeUndefined();
  h.facts.close(); expect(await h.facts.capture(h.input)).toBeUndefined();
});
it("screens projected secrets before the selection model and rejects invalid tool completion", async () => {
  const h = await harness(); await h.delivery.authorizeCollection({ policyVersion: "v1", scope: null, boundaries: [] });
  const session = SessionManager.inMemory();
  const lifecycle = new CollectionLifecycle(h.store);
  const builder = new CollectionInputBuilder({ store: h.store, maxInputBytes: 8192, taskFacts: h.facts.policy() });
  for (const [reference, isError, expectedFacts] of [["REPORT-42", false, 1],
    ["password is SYNTHETIC_NOT_A_REAL_CREDENTIAL", false, 0], ["REPORT-43", true, 0]] as const) {
    const request = (await lifecycle.begin(session))!;
    session.appendMessage({ role: "user", content: "请查询我的报告结果。", timestamp: Date.now() });
    session.appendMessage({ role: "assistant", stopReason: "toolUse", content: [{ type: "toolCall", id: "call-a", name: "bash", arguments: {} }], timestamp: Date.now() } as never);
    const receipt = await h.facts.capture({ ...h.input, response: { ok: true, status: 200, headers: {},
      body: JSON.stringify({ code: 0, data: { owner: "oa-alice", reference } }) } });
    session.appendMessage({ role: "toolResult", toolName: "bash", toolCallId: "call-a", isError,
      content: [{ type: "text", text: "RAW_PRIVATE_BODY" }], details: { danoTaskFacts: [receipt] }, timestamp: Date.now() });
    session.appendMessage({ role: "assistant", stopReason: "stop", content: [{ type: "text", text: "请求完成。" }], timestamp: Date.now() } as never);
    await lifecycle.settle(request, session);
    const result = await builder.build(request, session);
    expect(result.status).toBe("ready");
    if (result.status === "ready") {
      expect(result.messages.filter(message => message.role === "task_fact")).toHaveLength(expectedFacts);
      expect(JSON.stringify(result.messages)).not.toContain("SYNTHETIC_NOT_A_REAL_CREDENTIAL");
      expect(JSON.stringify(result.messages)).not.toContain("RAW_PRIVATE_BODY");
    }
  }
});
it("validates deployment contracts without executable projectors or ambiguous routes", () => {
  expect(parseMemoryTaskFactConfig(config())).toEqual(config());
  for (const mutate of [
    (v: any) => { v.contracts[0].actorPath = ["__proto__"]; },
    (v: any) => { v.contracts[0].path = "/reports/../submit"; },
    (v: any) => { v.contracts[0].fields[0].type = "object"; },
    (v: any) => { v.contracts[0].project = "untrusted-module"; },
    (v: any) => { v.contracts.push(v.contracts[0]); },
    (v: any) => { v.maxFactBytes = Infinity; },
  ]) { const value = config(); mutate(value); expect(() => parseMemoryTaskFactConfig(value)).toThrow("INVALID_MEMORY_TASK_FACT_CONFIG"); }
});
