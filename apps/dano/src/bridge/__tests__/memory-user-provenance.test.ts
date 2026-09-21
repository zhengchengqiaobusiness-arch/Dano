import { createHash } from "node:crypto";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, expect, it } from "vitest";
import { SessionManager } from "@earendil-works/pi-coding-agent";
import { MemoryUserProvenance } from "../memory-user-provenance.js";

const owner = { accountId: "test", userId: "alice" };
const directories: string[] = [];
afterEach(async () => { await Promise.all(directories.splice(0).map(path => rm(path, { recursive: true, force: true }))); });
function add(session: SessionManager, text: string) {
  const message = { role: "user" as const, content: text, timestamp: Date.now() };
  const entryId = session.appendMessage(message);
  return { entryId, entryTimestamp: session.getEntry(entryId)!.timestamp,
    contentVersion: createHash("sha256").update(JSON.stringify(message)).digest("hex") };
}

it("retains only original input after real pi session reopen, without copying it into receipts", async () => {
  const directory = await mkdtemp(join(tmpdir(), "dano-provenance-")); directories.push(directory);
  const session = SessionManager.create(directory, directory);
  const provenance = new MemoryUserProvenance(owner);
  const original = "我的周报固定使用简体中文。";
  const dispatched = original + "\n<file>synthetic-upload-path</file>";
  const source = add(session, dispatched);
  expect(provenance.record(session, source.entryId, original, dispatched)).toBe(true);
  expect(provenance.record(session, source.entryId, original, dispatched)).toBe(true);
  session.appendMessage({ role: "assistant", content: [{ type: "text", text: "收到。" }], api: "openai-completions",
    provider: "fixture", model: "fixture", stopReason: "stop", timestamp: Date.now(),
    usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } } });
  const reopened = SessionManager.open(session.getSessionFile()!);
  expect(new MemoryUserProvenance(owner).project(reopened, source)).toBe(original);
  const fork = SessionManager.forkFrom(session.getSessionFile()!, directory, directory);
  expect(new MemoryUserProvenance(owner).project(fork, source)).toBe(original);
  expect(new MemoryUserProvenance({ ...owner, userId: "bob" }).project(reopened, source)).toBeUndefined();
  const metadata = (await readFile(session.getSessionFile()!, "utf8")).split("\n")
    .filter(line => line.includes('"customType":"dano.memory-user-provenance.v1"'));
  expect(metadata).toHaveLength(1);
  expect(metadata[0]).not.toContain(original);
  expect(metadata[0]).not.toContain("synthetic-upload-path");
});

it("does not attribute template expansion or unmatched entries", () => {
  const session = SessionManager.inMemory();
  const provenance = new MemoryUserProvenance(owner);
  const source = add(session, "Template example: I prefer XML reports.");
  expect(provenance.record(session, source.entryId, "/report", "/report")).toBe(false);
  expect(provenance.project(session, source)).toBeUndefined();
});

it("rejects conflicting capture and changed source digest or timestamp", () => {
  const session = SessionManager.inMemory();
  const provenance = new MemoryUserProvenance(owner);
  const source = add(session, "User text plus wrapper");
  provenance.record(session, source.entryId, "User text", "User text plus wrapper");
  expect(() => provenance.record(session, source.entryId, "User", "User text plus wrapper")).toThrow("MEMORY_PROVENANCE_CONFLICT");
  expect(provenance.project(session, { ...source, contentVersion: "0".repeat(64) })).toBeUndefined();
  expect(provenance.project(session, { ...source, entryTimestamp: "changed" })).toBeUndefined();
});
