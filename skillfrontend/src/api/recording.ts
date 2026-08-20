import { api } from "./client";

export const EXPORT_DIR_LS = "dano.exportDir";
export const SKILL_EXPORT_DRAFT_LS = "dano.skillExportDrafts";

export interface SkillExportDraft {
  title?: string;
  description?: string;
}

export function rememberedSkillExportDraft(resultId: string): SkillExportDraft {
  const key = String(resultId || "").trim();
  if (!key) return {};
  try {
    const parsed = JSON.parse(localStorage.getItem(SKILL_EXPORT_DRAFT_LS) || "{}");
    const row = parsed && typeof parsed === "object" ? parsed[key] : null;
    if (!row || typeof row !== "object") return {};
    return {
      title: typeof row.title === "string" ? row.title : "",
      description: typeof row.description === "string" ? row.description : "",
    };
  } catch {
    return {};
  }
}

export function rememberSkillExportDraft(resultId: string, draft: SkillExportDraft) {
  const key = String(resultId || "").trim();
  if (!key) return;
  try {
    const parsed = JSON.parse(localStorage.getItem(SKILL_EXPORT_DRAFT_LS) || "{}");
    const all = parsed && typeof parsed === "object" ? parsed : {};
    all[key] = {
      title: String(draft.title || ""),
      description: String(draft.description || ""),
    };
    localStorage.setItem(SKILL_EXPORT_DRAFT_LS, JSON.stringify(all));
  } catch {
    // ignore quota / private mode
  }
}

export function rememberedExportDir() {
  try {
    return localStorage.getItem(EXPORT_DIR_LS) || "";
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
  skill_id?: string;
  skill_version?: number;
  skill_export_status?: string;
  skill_export_path?: string;
  skill_lifecycle?: RecordingSkillLifecycle;
  skill_needs_reexport?: boolean;
  skill_export_title?: string;
  skill_export_description?: string;
}

export async function listRecordingResults(subsystem: string): Promise<RecordingResultSummary[]> {
  const { data } = await api.get("/v1/recording-results", { params: { subsystem } });
  return Array.isArray(data) ? data : [];
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
  skill_plan?: Record<string, unknown> | null;
}

export async function getRecordingResult(id: string): Promise<RecordingResultDetail> {
  const { data } = await api.get(`/v1/recording-results/${id}`);
  return data as RecordingResultDetail;
}

export async function deleteRecordingResult(id: string): Promise<void> {
  await api.delete(`/v1/recording-results/${id}`);
}

export interface SkillGenerationRequest {
  title: string;
  business_description: string;
  planning_mode: "dynamic" | "fixed";
  example_requests?: string[];
  success_criteria?: string;
  forbidden_actions?: string;
  out_dir?: string;
  require_stage_seven?: boolean;
}

export interface SkillExportOutcome {
  status: string;
  skill_id?: string;
  skill_name?: string;
  version?: number;
  planning_mode?: string;
  used_capabilities?: Array<Record<string, unknown>>;
  unused_capabilities?: Array<Record<string, unknown>>;
  routes?: Array<Record<string, unknown>>;
  export_path?: string;
  plan?: Record<string, unknown> | null;
  clarification_questions?: string[];
  errors?: string[];
  idempotent?: boolean;
}

export async function exportRecordingSkill(
  resultId: string,
  request: SkillGenerationRequest,
): Promise<SkillExportOutcome> {
  const { data } = await api.post(
    `/v1/recording-results/${encodeURIComponent(resultId)}/export-skill`,
    request,
  );
  return data as SkillExportOutcome;
}
