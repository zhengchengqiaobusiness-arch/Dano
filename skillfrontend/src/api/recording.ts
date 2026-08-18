import { api } from "./client";

export interface RecordingResultSummary {
  id: string;
  action: string;
  title: string;
  goal_summary: string;
  capability_count: number;
  request_count: number;
  created_at: string;
  published: boolean;
}

export async function listRecordingResults(subsystem: string): Promise<RecordingResultSummary[]> {
  const { data } = await api.get("/v1/recording-results", { params: { subsystem } });
  return Array.isArray(data) ? data : [];
}

export interface RecordingResultDetail extends RecordingResultSummary {
  draft?: Record<string, unknown> | null;
}

export async function getRecordingResult(id: string): Promise<RecordingResultDetail> {
  const { data } = await api.get(`/v1/recording-results/${id}`);
  return data as RecordingResultDetail;
}

export async function deleteRecordingResult(id: string): Promise<void> {
  await api.delete(`/v1/recording-results/${id}`);
}
