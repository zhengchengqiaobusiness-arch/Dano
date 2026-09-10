/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 *
 * 给现有录制页复用的结果目录。能力数量只来自 PI 提交的 capabilities。
 * 列表以磁盘草稿为准，导出进程和录制进程看到同一批已交能力。
 */

import { capabilityCountFromPiResult } from "./capability-presence.mjs";
import { displayTitleFromResult, requestCountFromResult } from "./result-summary.mjs";

function contractFromSaved(saved, result) {
  if (saved?.draft && typeof saved.draft === "object") return saved.draft;
  if (saved && Array.isArray(saved.capabilities)) return saved;
  if (result && typeof result === "object") return result;
  return {};
}

export class ResultsCatalog {
  constructor(files) {
    this.files = files;
    this.rows = new Map();
  }

  async remember({ recordingId, action, title, goal, result, evidenceCount, subsystem }) {
    const capabilityCount = capabilityCountFromPiResult(result);
    const row = {
      id: recordingId,
      recording_id: recordingId,
      action: action || recordingId,
      title: displayTitleFromResult({ userTitle: title, goal, result }),
      goal_summary: String(result.business_understanding?.summary || goal || ""),
      capability_count: capabilityCount,
      request_count: requestCountFromResult(result),
      created_at: new Date().toISOString(),
      published: capabilityCount > 0,
      subsystem: subsystem || "",
      draft: structuredClone(result),
      draft_fingerprint: recordingId,
    };
    this.rows.set(recordingId, row);
    return this.summary(row);
  }

  summary(row) {
    const recordingId = String(row.recording_id || (String(row.id || "").startsWith("rec_") ? row.id : "") || "");
    const {
      id, action, title, goal_summary, capability_count, request_count, created_at, published,
    } = row;
    return {
      id,
      recording_id: recordingId,
      action,
      title,
      goal_summary,
      capability_count,
      request_count,
      created_at,
      published,
    };
  }

  list(subsystem) {
    return [...this.rows.values()]
      .filter((row) => !subsystem || !row.subsystem || row.subsystem === subsystem)
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

  async rowFromDisk(recordingId) {
    if (!this.files?.readDraft || !String(recordingId || "").startsWith("rec_")) return null;
    const saved = await this.files.readDraft(recordingId).catch(() => null);
    const result = await this.files.readPiResult?.(recordingId).catch(() => null);
    const draft = contractFromSaved(saved, result);
    const capabilityCount = capabilityCountFromPiResult(draft);
    if (!capabilityCount) return null;
    const title = displayTitleFromResult({
      userTitle: saved?.title || draft.title,
      result: draft,
    });
    return {
      id: recordingId,
      recording_id: recordingId,
      action: recordingId,
      title,
      goal_summary: String(draft.business_understanding?.summary || saved?.title || ""),
      capability_count: capabilityCount,
      request_count: requestCountFromResult(draft),
      created_at: String(saved?.saved_at || ""),
      published: true,
      subsystem: String(saved?.subsystem || draft.subsystem || ""),
      draft,
      draft_fingerprint: recordingId,
    };
  }

  async listPublished(subsystem = "") {
    const fromMemory = new Map(this.rows);
    if (typeof this.files?.listRecordingIds === "function") {
      const ids = await this.files.listRecordingIds();
      for (const id of ids) {
        if (fromMemory.has(id)) continue;
        const row = await this.rowFromDisk(id);
        if (row) fromMemory.set(id, row);
      }
    }
    return [...fromMemory.values()]
      .filter((row) => row.capability_count > 0)
      .filter((row) => !subsystem || !row.subsystem || row.subsystem === subsystem)
      .sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")))
      .map((row) => this.summary(row));
  }

  async detailOf(id) {
    const live = this.detail(id);
    if (live?.draft) return live;
    const row = await this.rowFromDisk(id);
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
