/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 *
 * 证据层只按发生顺序追加原始事实。禁止分类、过滤或推断业务含义。
 */

import { randomUUID } from "node:crypto";
import { buildEvidenceIndex } from "./evidence-index.mjs";

const clone = (value) => structuredClone(value);

export class EvidenceStore {
  constructor(files, { onEvent = () => {} } = {}) {
    this.files = files;
    this.onEvent = onEvent;
    this.sessions = new Map();
  }

  list() {
    return [...this.sessions.values()].map((session) => clone(session));
  }

  async create({ targetUrl, goal, title = "", action = "" }) {
    const id = `rec_${randomUUID().replaceAll("-", "")}`;
    const now = new Date().toISOString();
    const session = {
      id,
      targetUrl,
      goal,
      title: title || goal,
      action,
      status: "starting_pi",
      piStatus: "starting",
      browserStatus: "idle",
      lastSeq: 0,
      evidenceCount: 0,
      evidenceKinds: {},
      createdAt: now,
      updatedAt: now,
      frozen: false,
      frozenAt: "",
      error: "",
      publicMessage: "",
      hasFinalResult: false,
      piSessionId: "",
    };
    this.sessions.set(id, session);
    await this.files.initialize(id, session);
    this.onEvent(id, { type: "status", session: clone(session) });
    return clone(session);
  }

  require(recordingId) {
    const session = this.sessions.get(recordingId);
    if (!session) throw new Error(`recording not found: ${recordingId}`);
    return session;
  }

  snapshot(recordingId) {
    return clone(this.require(recordingId));
  }

  async setStatus(recordingId, patch) {
    const session = this.require(recordingId);
    Object.assign(session, clone(patch), { updatedAt: new Date().toISOString() });
    await this.files.writeManifest(recordingId, session);
    this.onEvent(recordingId, { type: "status", session: clone(session) });
    return clone(session);
  }

  async append(recordingId, kind, payload) {
    const session = this.require(recordingId);
    if (session.frozen) {
      throw new Error("证据已冻结，禁止继续写入");
    }
    if (session.status === "failed" || session.status === "succeeded") {
      throw new Error("录制已结束，禁止继续写入证据");
    }
    const event = {
      seq: ++session.lastSeq,
      kind: String(kind),
      capturedAt: new Date().toISOString(),
      payload: clone(payload),
    };
    session.evidenceCount += 1;
    session.evidenceKinds[event.kind] = (session.evidenceKinds[event.kind] ?? 0) + 1;
    session.updatedAt = event.capturedAt;
    await this.files.appendEvidence(recordingId, event);
    await this.files.writeManifest(recordingId, session);
    this.onEvent(recordingId, { type: "evidence", event: clone(event), session: clone(session) });
    return clone(event);
  }

  async freeze(recordingId) {
    const session = this.require(recordingId);
    if (session.frozen) return clone(session);
    session.frozen = true;
    session.frozenAt = new Date().toISOString();
    session.updatedAt = session.frozenAt;
    await this.files.writeManifest(recordingId, session);
    this.onEvent(recordingId, { type: "status", session: clone(session) });
    return clone(session);
  }

  async read(recordingId, { afterSeq = 0, limit = 20 } = {}) {
    this.require(recordingId);
    const events = await this.files.readEvidence(recordingId);
    return events.filter((event) => event.seq > afterSeq).slice(0, limit).map(clone);
  }

  async readOne(recordingId, seq) {
    this.require(recordingId);
    const events = await this.files.readEvidence(recordingId);
    const event = events.find((item) => item.seq === Number(seq));
    return event ? clone(event) : null;
  }

  async index(recordingId) {
    this.require(recordingId);
    const events = await this.files.readEvidence(recordingId);
    return buildEvidenceIndex(events);
  }

  async writeBlob(recordingId, bytes) {
    this.require(recordingId);
    const blobId = `blob_${randomUUID().replaceAll("-", "")}`;
    await this.files.writeBlob(recordingId, blobId, bytes);
    return { blobId, byteLength: bytes.byteLength };
  }

  async readBlob(recordingId, blobId, { offset = 0, length = 65536 } = {}) {
    this.require(recordingId);
    const bytes = await this.files.readBlob(recordingId, blobId);
    const start = Math.min(Math.max(0, offset), bytes.byteLength);
    const end = Math.min(start + Math.max(0, length), bytes.byteLength);
    return {
      blobId,
      offset: start,
      totalBytes: bytes.byteLength,
      hasMore: end < bytes.byteLength,
      bytes: bytes.subarray(start, end),
    };
  }
}
