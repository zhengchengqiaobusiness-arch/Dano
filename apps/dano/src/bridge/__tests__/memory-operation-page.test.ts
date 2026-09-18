import { expect, it } from "vitest";
import type { Operation } from "@josephyoung/pi-openviking/host";
import { memoryOperationPage, InvalidMemoryCursor } from "../memory-operation-page.js";

function operation(index: number): Operation {
  return { id: String(index).padStart(3, "0"), createdAt: "2026-09-18T00:00:00Z", updatedAt: "2026-09-18T00:00:00Z",
    owner: { accountId: "account", userId: "alice" }, phase: "processing", kind: "explicit", scope: null,
    authorizationEpoch: 1, remoteSessionId: "PRIVATE_REMOTE", payload: "PRIVATE_PAYLOAD",
    source: { sessionId: "session", entryId: "entry", branchId: "branch", contentVersion: "PRIVATE_HASH" } };
}

it("paginates tied timestamps without duplicates even if earlier records are added or removed", () => {
  const operations = Array.from({ length: 60 }, (_, index) => operation(index));
  const first = memoryOperationPage(operations);
  expect(first.items).toHaveLength(25);
  expect(first.items[0]!.id).toBe("059");
  expect(JSON.stringify(first)).not.toContain("PRIVATE");
  const changed = [...operations.filter(item => item.id !== first.items.at(-1)!.id), operation(61)];
  const second = memoryOperationPage(changed, first.nextCursor!);
  const third = memoryOperationPage(changed, second.nextCursor!);
  expect([...first.items, ...second.items, ...third.items].map(item => item.id)).toEqual(
    [...operations].reverse().map(item => item.id));
  expect(third.nextCursor).toBeNull();
});

it("rejects malformed cursor input without exposing its contents", () => {
  for (const cursor of ["", "!", "a".repeat(1025), Buffer.from('["not-a-date","id"]').toString("base64url")]) {
    expect(() => memoryOperationPage([], cursor)).toThrow(InvalidMemoryCursor);
  }
  expect(memoryOperationPage([])).toEqual({ items: [], nextCursor: null });
});
