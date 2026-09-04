/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 *
 * 给现有录制页复用的结果目录。能力数量只来自 PI 提交的 capabilities。
 */

import { capabilityCountFromPiResult } from "./capability-presence.mjs";

export class ResultsCatalog {
  constructor(files) {
    this.files = files;
    this.rows = new Map();
  }

  async remember({ recordingId, action, title, goal, result, evidenceCount, subsystem }) {
    const capabilityCount = capabilityCountFromPiResult(result);
    const row = {
      id: recordingId,
      action: action || recordingId,
      title: title || result.title || goal || "",
      goal_summary: String(result.business_understanding?.summary || goal || ""),
      capability_count: capabilityCount,
      request_count: evidenceCount || 0,
      created_at: new Date().toISOString(),
      published: false,
      subsystem: subsystem || "",
      draft: structuredClone(result),
      draft_fingerprint: recordingId,
    };
    this.rows.set(recordingId, row);
    return this.summary(row);
  }

  summary(row) {
    const {
      id, action, title, goal_summary, capability_count, request_count, created_at, published,
    } = row;
    return {
      id, action, title, goal_summary, capability_count, request_count, created_at, published,
    };
  }

  list(subsystem) {
    return [...this.rows.values()]
      .filter((row) => !subsystem || row.subsystem === subsystem)
      .map((row) => this.summary(row));
  }

  detail(id) {
    const row = this.rows.get(id);
    if (!row) return null;
    return {
      ...this.summary(row),
      draft: structuredClone(row.draft),
      draft_fingerprint: row.draft_fingerprint,
    };
  }

  remove(id) {
    this.rows.delete(id);
  }
}
