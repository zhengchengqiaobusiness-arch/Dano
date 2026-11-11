import { afterEach, expect, it } from "vitest";
import { chmod, mkdtemp, readFile, readdir, rm, stat, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { registerMemoryAcceptanceTrace } from "../memory-acceptance-trace.js";
import { measureMemoryMethods } from "../memory-latency-span.js";

const directories: string[] = [];
afterEach(async () => { await Promise.all(directories.splice(0).map(path => rm(path, { recursive: true, force: true }))); });
async function fixture(useFile = false) {
  const directory = await mkdtemp(join(tmpdir(), "memory-trace-")); directories.push(directory);
  const handlers = new Map<string, (event: any, ctx: any) => Promise<unknown>>();
  const records: unknown[] = [];
  const traced = registerMemoryAcceptanceTrace({ on: (name: string, handler: any) => handlers.set(name, handler) } as unknown as ExtensionAPI,
    directory, useFile ? undefined : record => records.push(record));
  const ctx = (sessionId: string) => ({ sessionManager: { getSessionId: () => sessionId } });
  const arm = (extra = {}) => writeFile(join(directory, "acceptance-trace.json"), JSON.stringify({
    runId: "trace-test", sessionId: "target-session", expiresAt: new Date(Date.now() + 60_000).toISOString(), ...extra,
  }), { mode: 0o600 });
  return { handlers, records, ctx, arm, directory, traced };
}

it("isolates overlapping handler spans and preserves private receivers, results and failures", async () => {
  const f = await fixture(); await f.arm();
  class Service {
    #value = "private result";
    async read() { await new Promise(resolve => setTimeout(resolve, 5)); return this.#value; }
    async fail() { throw new Error("private error"); }
  }
  const service = measureMemoryMethods(new Service(), { read: "state.read", fail: "state.fail" });
  f.traced.on("context", async () => { await service.read(); await service.fail(); });
  const results = await Promise.allSettled([
    f.handlers.get("context")!({}, f.ctx("target-session")),
    f.handlers.get("context")!({}, f.ctx("other-session")),
    service.read(),
  ]);
  expect(results[0]).toMatchObject({ status: "rejected", reason: new Error("private error") });
  expect(results[1]).toMatchObject({ status: "rejected" });
  expect(results[2]).toEqual({ status: "fulfilled", value: "private result" });
  expect(f.records).toHaveLength(3);
  expect(f.records).toEqual(expect.arrayContaining([
    expect.objectContaining({ event: "latency_span", stage: "state.read", outcome: "ok", durationMs: expect.any(Number) }),
    expect.objectContaining({ stage: "state.fail", outcome: "error" }),
    expect.objectContaining({ stage: "memory.context", outcome: "error" }),
  ]));
  expect(JSON.stringify(f.records)).not.toMatch(/private result|private error|other-session/);
});

it("does not emit handler spans when disabled or disarmed during execution", async () => {
  const f = await fixture();
  f.traced.on("context", async () => { await rm(join(f.directory, "acceptance-trace.json"), { force: true }); });
  await f.handlers.get("context")!({}, f.ctx("target-session"));
  await f.arm();
  await f.handlers.get("context")!({}, f.ctx("target-session"));
  expect(f.records).toEqual([]);
});

it("records actual payload inclusion and explicit calls only for the armed session, without contents", async () => {
  const f = await fixture();
  const text = JSON.stringify({ type: "quoted_memory_data", memories: [{ text: "private form answer", source: "private-uri" }] });
  const event = { messages: [{ role: "custom", customType: "openviking-reference-data", content: text }] };
  await f.handlers.get("context_with_system")!(event, f.ctx("target-session"));
  expect(f.records).toEqual([]);
  await f.arm();
  await f.handlers.get("context_with_system")!(event, f.ctx("other-session"));
  expect(f.records).toEqual([]);
  await f.handlers.get("context_with_system")!(event, f.ctx("target-session"));
  await f.handlers.get("before_provider_request")!({ payload: { messages: [{ role: "user", content: text }] } }, f.ctx("target-session"));
  await f.handlers.get("tool_call")!({ toolName: "memory_recall", toolCallId: "call-test", input: { query: "secret query" } }, f.ctx("target-session"));
  expect(f.records).toEqual(expect.arrayContaining([
    expect.objectContaining({ event: "provider_request", automaticMemoryMessages: 1, payloadMatchedMemoryMessages: 1 }),
    expect.objectContaining({ event: "tool_call", toolName: "memory_recall", toolCallId: "call-test" }),
  ]));
  expect(JSON.stringify(f.records)).not.toMatch(/private|secret query/);
});

it("writes only the exact session private file without creating files for other sessions", async () => {
  const f = await fixture(true); await f.arm();
  const event = { toolName: "memory_recall", toolCallId: "call-test", input: { query: "private query" } };
  await f.handlers.get("tool_call")!(event, f.ctx("other-session"));
  expect(await readdir(f.directory)).toEqual(["acceptance-trace.json"]);
  await f.handlers.get("tool_call")!(event, f.ctx("target-session"));
  const path = join(f.directory, "acceptance-trace.trace-test.target-session.jsonl");
  const raw = await readFile(path, "utf8");
  expect(JSON.parse(raw)).toMatchObject({ sessionId: "target-session", event: "tool_call" });
  expect(raw).not.toContain("private query");
  expect((await stat(path)).mode & 0o777).toBe(0o600);
  await rm(path);
  const target = join(f.directory, "untouched"); await writeFile(target, "untouched", { mode: 0o600 });
  await symlink(target, path);
  await f.handlers.get("tool_call")!(event, f.ctx("target-session"));
  expect(await readFile(target, "utf8")).toBe("untouched");
});

it("rejects public or linked controls and stops immediately when removed", async () => {
  const f = await fixture(); await f.arm();
  const path = join(f.directory, "acceptance-trace.json");
  const event = { toolName: "memory_recall", toolCallId: "call-test" };
  await chmod(path, 0o644);
  await f.handlers.get("tool_call")!(event, f.ctx("target-session"));
  expect(f.records).toEqual([]);
  await chmod(path, 0o600);
  await f.handlers.get("tool_call")!(event, f.ctx("target-session"));
  expect(f.records).toHaveLength(1);
  await rm(path);
  await f.handlers.get("tool_call")!(event, f.ctx("target-session"));
  expect(f.records).toHaveLength(1);
  const target = join(f.directory, "linked-control.json");
  await writeFile(target, JSON.stringify({ runId: "test", sessionId: "target-session", expiresAt: new Date(Date.now() + 60000).toISOString() }), { mode: 0o600 });
  await symlink(target, path);
  await f.handlers.get("tool_call")!(event, f.ctx("target-session"));
  expect(f.records).toHaveLength(1);
});

it("caps diagnostic output without changing tool execution", async () => {
  const f = await fixture(); await f.arm();
  for (let i = 0; i < 205; i++) {
    expect(await f.handlers.get("tool_call")!({ toolName: "memory_recall", toolCallId: "call-test" }, f.ctx("target-session"))).toBeUndefined();
  }
  expect(f.records).toHaveLength(200);
});

it("does not mistake context presence for payload inclusion and stops on expiry", async () => {
  const f = await fixture(); await f.arm();
  await f.handlers.get("context_with_system")!({ messages: [{ role: "custom", customType: "openviking-reference-data", content: "memory" }] }, f.ctx("target-session"));
  await f.handlers.get("before_provider_request")!({ payload: { messages: [] } }, f.ctx("target-session"));
  expect(f.records.at(-1)).toMatchObject({ automaticMemoryMessages: 1, payloadMatchedMemoryMessages: 0 });
  const count = f.records.length;
  await f.arm({ expiresAt: new Date(Date.now() - 1).toISOString() });
  await f.handlers.get("tool_call")!({ toolName: "memory_recall", toolCallId: "call-expired" }, f.ctx("target-session"));
  expect(f.records).toHaveLength(count);
});

it("records scoped question error codes and safe paths without messages or business keys", async () => {
  const f = await fixture(); await f.arm();
  const details = { status: "invalid", error: { code: "invalid_question_arguments", category: "validation",
    message: "private message", retryable: true, issues: [
      { code: "invalid_question_item", path: "questions[0].inputType", message: "private value" },
      { code: "invalid_question_item", path: "questions[0].privateBusinessKey", message: "private value" },
    ] } };
  const event = { toolName: "ask_user_question", toolCallId: "question-test", isError: true, details, content: [] };
  await f.handlers.get("tool_result")!(event, f.ctx("other-session"));
  expect(f.records).toEqual([]);
  await f.handlers.get("tool_result")!(event, f.ctx("target-session"));
  expect(f.records).toEqual(expect.arrayContaining([
    expect.objectContaining({ event: "question_failure", code: "invalid_question_arguments", retryable: true }),
    expect.objectContaining({ event: "question_issue", path: "questions[0].inputType" }),
    expect.objectContaining({ event: "question_issue", path: "redacted" }),
  ]));
  expect(JSON.stringify(f.records)).not.toMatch(/private/);
});

it.each(["yyyy-MM-dd HH:mm:ss", "yyyy-MM-dd h:mm", "private business text", undefined])("records only safe failing date formats: %s", async value => {
  const f = await fixture(); await f.arm();
  const event = { toolName: "ask_user_question", toolCallId: "date-test", isError: true,
    input: { questions: JSON.stringify([{ inputType: "date", dateFormat: value, default: "private answer" }]) },
    details: { status: "invalid", error: { code: "invalid_question_arguments", category: "validation", message: "private error", retryable: true,
      issues: [{ code: "invalid_date_format", path: "questions[0].dateFormat", message: "private reason" }] } }, content: [] };
  await f.handlers.get("tool_result")!(event, f.ctx("target-session"));
  expect(f.records).toContainEqual(expect.objectContaining({ event: "question_date_format", path: "questions[0].dateFormat",
    formatType: value === undefined ? "missing" : "string", format: value?.startsWith("yyyy") ? value : "redacted" }));
  expect(JSON.stringify(f.records)).not.toMatch(/private/);
});
