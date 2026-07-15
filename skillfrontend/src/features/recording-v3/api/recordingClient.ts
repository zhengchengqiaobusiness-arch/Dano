import { api } from "../../../api/client";
import { isAxiosError } from "axios";

export type RecordingMode = "real_submit" | "record_only";

export interface RecordingSessionInput {
  subsystem: string;
  start_url: string;
  base_url: string;
  recording_mode: RecordingMode;
}

export interface RecordingSessionResponse {
  recording_id: string;
  websocket_ticket: string;
  current_revision: number;
  resume_token: string;
  snapshot?: Record<string, unknown> | null;
  pi_status?: Record<string, unknown> | null;
}

export class RecordingSessionRequestError extends Error {
  readonly retryable: boolean;
  readonly status?: number;

  constructor(message: string, retryable: boolean, status?: number) {
    super(message);
    this.name = "RecordingSessionRequestError";
    this.retryable = retryable;
    this.status = status;
  }
}

function recordingRequestError(error: unknown, fallback: string): RecordingSessionRequestError {
  if (isAxiosError(error)) {
    const status = error.response?.status;
    const detail = error.response?.data?.detail;
    const message = typeof detail === "string" && detail.trim()
      ? detail
      : (error.message || fallback);
    const retryable = status == null || status === 408 || status === 429 || status >= 500;
    return new RecordingSessionRequestError(message, retryable, status);
  }
  return new RecordingSessionRequestError(
    error instanceof Error ? error.message : fallback,
    true,
  );
}

export function isRetryableRecordingSessionError(error: unknown): boolean {
  return !(error instanceof RecordingSessionRequestError) || error.retryable;
}

function requireSessionResponse(value: unknown): RecordingSessionResponse {
  const data = value as Partial<RecordingSessionResponse> | null;
  if (!data?.recording_id || !data.websocket_ticket || !data.resume_token) {
    throw new Error("录制服务返回的会话凭据不完整");
  }
  return {
    recording_id: data.recording_id,
    websocket_ticket: data.websocket_ticket,
    resume_token: data.resume_token,
    current_revision: Number(data.current_revision || 0),
    snapshot: data.snapshot || null,
    pi_status: data.pi_status || null,
  };
}

export async function createRecordingSession(input: RecordingSessionInput): Promise<RecordingSessionResponse> {
  try {
    const response = await api.post("/recording-v3/sessions", input);
    return requireSessionResponse(response.data);
  } catch (error) {
    throw recordingRequestError(error, "创建录制会话失败");
  }
}

export async function resumeRecordingSession(
  recordingId: string,
  resumeToken: string,
): Promise<RecordingSessionResponse> {
  try {
    const response = await api.post(
      `/recording-v3/sessions/${encodeURIComponent(recordingId)}/resume`,
      { resume_token: resumeToken },
    );
    return requireSessionResponse({
      ...response.data,
      recording_id: response.data?.recording_id || recordingId,
      resume_token: response.data?.resume_token || resumeToken,
    });
  } catch (error) {
    throw recordingRequestError(error, "恢复录制会话失败");
  }
}

export function recordingWebSocketUrl(recordingId: string, ticket: string): string {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  return `${protocol}://${location.host}/recording-v3/sessions/${encodeURIComponent(recordingId)}/ws?ticket=${encodeURIComponent(ticket)}`;
}
