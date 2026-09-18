import type { Operation } from "@josephyoung/pi-openviking/host";
import type { UserMemoryOperation, UserMemoryOperationPage } from "../../types/memory.js";

export class InvalidMemoryCursor extends Error {}
const pageSize = 25;
type Position = Pick<Operation, "createdAt" | "id">;

function compare(a: Position, b: Position): number {
  for (const key of ["createdAt", "id"] as const) {
    if (a[key] !== b[key]) return a[key] > b[key] ? -1 : 1;
  }
  return 0;
}

export function projectMemoryOperation(operation: UserMemoryOperation): UserMemoryOperation {
  return { id: operation.id, phase: operation.phase, createdAt: operation.createdAt,
    updatedAt: operation.updatedAt, source: { sessionId: operation.source.sessionId,
      entryId: operation.source.entryId, branchId: operation.source.branchId } };
}

/** Cursor is an ordering boundary, never an owner, path or remote identifier. */
export function memoryOperationPage(operations: Operation[], cursor?: string): UserMemoryOperationPage {
  let boundary: Position | undefined;
  if (cursor !== undefined) {
    try {
      if (!cursor || cursor.length > 1024 || !/^[A-Za-z0-9_-]+$/.test(cursor)) throw new Error();
      const decoded = Buffer.from(cursor, "base64url");
      if (decoded.toString("base64url") !== cursor) throw new Error();
      const value: unknown = JSON.parse(decoded.toString("utf8"));
      if (!Array.isArray(value) || value.length !== 2 || typeof value[0] !== "string"
        || !Number.isFinite(Date.parse(value[0])) || typeof value[1] !== "string"
        || !value[1] || value[1].length > 256) throw new Error();
      boundary = { createdAt: value[0], id: value[1] };
    } catch { throw new InvalidMemoryCursor("INVALID_MEMORY_CURSOR"); }
  }
  const candidates = operations.filter(operation => !boundary || compare(operation, boundary) > 0).sort(compare);
  const items = candidates.slice(0, pageSize).map(projectMemoryOperation);
  const last = items.at(-1);
  return { items, nextCursor: candidates.length > pageSize && last
    ? Buffer.from(JSON.stringify([last.createdAt, last.id])).toString("base64url") : null };
}
