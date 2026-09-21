import { createHash } from "node:crypto";
import type { SessionManager, SessionEntry } from "@earendil-works/pi-coding-agent";
import type { MemoryOwner } from "./memory-owner-registry.js";

const customType = "dano.memory-user-provenance.v1";
const digest = (value: string) => createHash("sha256").update(value).digest("hex");
type Source = { entryId: string; entryTimestamp: string; contentVersion: string };
type Session = Pick<SessionManager, "getEntry" | "getEntries" | "appendCustomEntry">;
type Reader = Pick<SessionManager, "getEntry" | "getEntries">;
type CaptureSession = Reader & Pick<SessionManager, "getSessionId" | "getBranch">;
export type MemoryInputCapture = (...input: Parameters<MemoryUserProvenance["capture"]>) => (() => void) | Promise<() => void>;

function userText(entry: SessionEntry | undefined): string | undefined {
  if (entry?.type !== "message" || entry.message.role !== "user") return undefined;
  return typeof entry.message.content === "string" ? entry.message.content
    : entry.message.content.filter(block => block.type === "text").map(block => block.text).join("\n");
}

/** Host-only receipts in the protected original pi session. No conversation copy.
 * The dispatcher supplies original browser input, before file/template injection.
 * A transformed message cannot acquire provenance merely by having role=user. */
export class MemoryUserProvenance {
  readonly #ownerDigest: string;
  readonly #pending = new Map<string, Map<symbol, { textDigest: string; userLength: number; baseline: Set<string> }>>();
  constructor(owner: MemoryOwner) {
    if (![owner.accountId, owner.userId].every(id => /^[A-Za-z0-9_-]{1,128}$/.test(id))) {
      throw new Error("MEMORY_OWNER_INVALID");
    }
    this.#ownerDigest = digest(JSON.stringify([owner.accountId, owner.userId]));
  }

  /** Capture before dispatch; pending metadata contains no plaintext input. */
  capture(session: CaptureSession, originalText: string, dispatchedText: string): () => void {
    if (!originalText || !dispatchedText.startsWith(originalText)) return () => {};
    const sessionId = session.getSessionId();
    const pending = this.#pending.get(sessionId) ?? new Map();
    const token = Symbol();
    pending.set(token, { textDigest: digest(dispatchedText), userLength: originalText.length,
      baseline: new Set(session.getEntries().map(entry => entry.id)) });
    this.#pending.set(sessionId, pending);
    return () => { pending.delete(token); if (!pending.size && this.#pending.get(sessionId) === pending) this.#pending.delete(sessionId); };
  }

  /** Run before collection's agent_settled hook, after pi persisted user entries. */
  settle(session: CaptureSession & Pick<Session, "appendCustomEntry">): void {
    const pending = this.#pending.get(session.getSessionId());
    if (!pending) return;
    this.#pending.delete(session.getSessionId());
    for (const entry of session.getBranch()) {
      const text = userText(entry);
      if (text === undefined) continue;
      const matching = [...pending.values()].filter(input => !input.baseline.has(entry.id) && input.textDigest === digest(text));
      if (!matching.length) continue;
      if (new Set(matching.map(input => input.userLength)).size !== 1) throw new Error("MEMORY_PROVENANCE_CONFLICT");
      this.record(session, entry.id, text.slice(0, matching[0]!.userLength), text);
    }
  }

  clear(): void { this.#pending.clear(); }

  /** Call only after matching the actual persisted entry to its dispatched input.
   * Dano appends file references, so user-authored text must be the exact prefix.
   * Expanded templates fail this match instead of attributing their examples. */
  record(session: Session, entryId: string, originalText: string, dispatchedText: string): boolean {
    const entry = session.getEntry(entryId);
    if (entry?.type !== "message" || entry.message.role !== "user"
      || userText(entry) !== dispatchedText || !originalText || !dispatchedText.startsWith(originalText)) return false;
    const source = { entryId, entryTimestamp: entry.timestamp, contentVersion: digest(JSON.stringify(entry.message)) };
    const records = this.#records(session, source);
    if (records.length) {
      // Retrying capture is idempotent; a conflicting attribution must not win.
      if (records.length !== 1 || records[0]!.userLength !== originalText.length) throw new Error("MEMORY_PROVENANCE_CONFLICT");
      return true;
    }
    session.appendCustomEntry(customType, { version: 1, ownerDigest: this.#ownerDigest,
      ...source, userLength: originalText.length });
    return true;
  }

  project(session: Reader, source: Source): string | undefined {
    const entry = session.getEntry(source.entryId);
    const text = userText(entry);
    if (text === undefined || entry?.type !== "message" || entry.timestamp !== source.entryTimestamp
      || digest(JSON.stringify(entry.message)) !== source.contentVersion) return;
    const records = this.#records(session, source);
    if (records.length !== 1) return;
    const length = records[0]!.userLength;
    if (!Number.isSafeInteger(length) || length < 1 || length > text.length) return;
    return text.slice(0, length);
  }

  #records(session: Reader, source: Source): Array<{ userLength: number }> {
    return session.getEntries().flatMap(entry => {
      if (entry.type !== "custom" || entry.customType !== customType) return [];
      const data = entry.data as Record<string, unknown> | undefined;
      if (!data || data.version !== 1 || data.ownerDigest !== this.#ownerDigest
        || data.entryId !== source.entryId || data.entryTimestamp !== source.entryTimestamp
        || data.contentVersion !== source.contentVersion) return [];
      return [{ userLength: data.userLength as number }];
    });
  }
}
