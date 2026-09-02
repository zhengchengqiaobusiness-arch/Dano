export type RecordingRequest = (path: string, options: { method: string; body: string }) => Promise<any>;

export interface CompletedRecording {
  session: { id: string; [key: string]: unknown };
}

export function completeRecordingSession(request: RecordingRequest): Promise<CompletedRecording>;
