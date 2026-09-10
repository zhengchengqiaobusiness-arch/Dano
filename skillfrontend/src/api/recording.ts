import { api } from "./client";
import { describeExportFailure } from "./exportError";
import { mergeRecordingHistory } from "./skillCatalog";
import {
  normalizeSkillExportDraft,
  serializeSkillExportDraft,
} from "./skillExportDraft";
import type { SkillExportDraft } from "./skillExportDraft";

export type { SkillExportDraft, RouteSummary } from "./skillExportDraft";

export const EXPORT_DIR_LS = "dano.exportDir";
export const SKILL_EXPORT_DRAFT_LS = "dano.skillExportDrafts";

export function rememberedSkillExportDraft(resultId: string): SkillExportDraft {
  const key = String(resultId || "").trim();
  if (!key) return normalizeSkillExportDraft({});
  try {
    const parsed = JSON.parse(localStorage.getItem(SKILL_EXPORT_DRAFT_LS) || "{}");
    const row = parsed && typeof parsed === "object" ? parsed[key] : null;
    if (!row || typeof row !== "object") return normalizeSkillExportDraft({});
    return normalizeSkillExportDraft(row);
  } catch {
    return normalizeSkillExportDraft({});
  }
}

export function rememberSkillExportDraft(resultId: string, draft: SkillExportDraft) {
  const key = String(resultId || "").trim();
  if (!key) return;
  try {
    const parsed = JSON.parse(localStorage.getItem(SKILL_EXPORT_DRAFT_LS) || "{}");
    const all = parsed && typeof parsed === "object" ? parsed : {};
    all[key] = serializeSkillExportDraft(draft);
    localStorage.setItem(SKILL_EXPORT_DRAFT_LS, JSON.stringify(all));
  } catch {
    // ignore quota / private mode
  }
}

export function rememberedExportDir() {
  try {
    const raw = localStorage.getItem(EXPORT_DIR_LS) || "";
    return raw.replace(/[\\/]+agent-skills[\\/]*$/i, "") || "";
  } catch {
    return "";
  }
}

export function rememberExportDir(value: string) {
  const next = String(value || "").trim();
  if (!next) return;
  try {
    localStorage.setItem(EXPORT_DIR_LS, next);
  } catch {
    // ignore quota / private mode
  }
}

export type RecordingSkillLifecycle =
  | "stage_six_done"
  | "verifying"
  | "verified_not_exported"
  | "generating"
  | "exported"
  | "export_failed"
  | "needs_reexport"
  | string;

export interface RecordingResultSummary {
  id: string;
  action: string;
  title: string;
  goal_summary: string;
  capability_count: number;
  request_count: number;
  created_at: string;
  published: boolean;
  machine_verification_ran?: boolean;
  machine_verification_required?: boolean;
  machine_verification_status?: string;
  stage_seven_attempt_id?: string;
  stage_seven_updated_at?: string;
  stage_seven_fingerprint?: string;
  recording_id?: string;
  skill_id?: string;
  skill_version?: number;
  skill_export_status?: string;
  skill_export_path?: string;
  skill_lifecycle?: RecordingSkillLifecycle;
  skill_needs_reexport?: boolean;
  skill_export_title?: string;
}

export async function listPiRecordings(subsystem: string): Promise<RecordingResultSummary[]> {
  const { data } = await api.get("/v1/pi-recordings", { params: { subsystem } });
  return Array.isArray(data) ? data : [];
}

export async function listRecordingResults(subsystem: string): Promise<RecordingResultSummary[]> {
  const [gateway, pi] = await Promise.all([
    api.get("/v1/recording-results", { params: { subsystem } })
      .then(({ data }) => (Array.isArray(data) ? data : []))
      .catch(() => []),
    listPiRecordings(subsystem).catch(() => []),
  ]);
  return mergeRecordingHistory(gateway as RecordingResultSummary[], pi);
}

export interface RecordingStageSevenSummary {
  status?: string;
  working_fingerprint?: string;
  publishable?: boolean;
}

export interface RecordingResultDetail extends RecordingResultSummary {
  draft?: Record<string, unknown> | null;
  draft_fingerprint?: string;
  stage_seven?: RecordingStageSevenSummary | null;
}

export async function getRecordingResult(id: string): Promise<RecordingResultDetail> {
  const key = String(id || "").trim();
  const path = key.startsWith("rec_")
    ? `/v1/pi-recordings/${encodeURIComponent(key)}`
    : `/v1/recording-results/${encodeURIComponent(key)}`;
  const { data } = await api.get(path);
  return data as RecordingResultDetail;
}

export async function deleteRecordingResult(id: string): Promise<void> {
  await api.delete(`/v1/recording-results/${id}`);
}

export async function patchRecordingResult(
  id: string,
  request: { edits: Array<Record<string, unknown>>; expected_fingerprint?: string },
): Promise<RecordingResultDetail> {
  const { data } = await api.patch(`/v1/recording-results/${encodeURIComponent(id)}`, request);
  return data as RecordingResultDetail;
}

export async function putRecordingDraft(
  recordingId: string,
  draft: Record<string, unknown>,
  title = "",
): Promise<void> {
  const id = String(recordingId || "").trim();
  if (!id.startsWith("rec_")) return;
  await api.put(`/v1/recording-results/${encodeURIComponent(id)}/draft`, {
    recording_id: id,
    title,
    draft,
  });
}

export interface SkillGenerationRequest {
  title: string;
  out_dir?: string;
  recording_id?: string;
  draft?: Record<string, unknown> | null;
  skill_id?: string;
  tenant?: string;
  subsystem?: string;
}

export interface SkillExportOutcome {
  status: string;
  skill_id?: string;
  skill_name?: string;
  version?: number;
  routes?: Array<Record<string, unknown>>;
  unresolved_branches?: string[];
  export_path?: string;
  errors?: string[];
  catalog_item?: Record<string, unknown> | null;
  token_missing?: boolean;
}

export { describeExportFailure } from "./exportError";

export async function exportRecordingSkill(
  resultId: string,
  request: SkillGenerationRequest,
): Promise<SkillExportOutcome> {
  const recordingId = String(request.recording_id || resultId || "").trim();
  const path = `/v1/pi-recordings/${encodeURIComponent(recordingId)}/export-skill`;
  console.info("[dano-export] 开始", {
    path,
    resultId,
    recording_id: recordingId,
    title: request.title,
    tenant: request.tenant,
    caps: Array.isArray(request.draft?.capabilities) ? request.draft?.capabilities.length : 0,
  });
  if (!recordingId.startsWith("rec_")) {
    const error = Object.assign(new Error("缺少 recording_id，无法按最新能力导出"), {
      response: { data: { detail: "缺少 recording_id，无法按最新能力导出" } },
    });
    console.error("[dano-export] 未发出请求", describeExportFailure(error));
    throw error;
  }
  try {
    const { data } = await api.post(path, { ...request, recording_id: recordingId });
    console.info("[dano-export] 响应", data);
    return data as SkillExportOutcome;
  } catch (error) {
    console.error("[dano-export] 失败", describeExportFailure(error), error);
    throw error;
  }
}
