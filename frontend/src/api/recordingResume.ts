export function selectRecordingResultToResume<T extends { id: string }>(
  rememberedResultId: unknown,
  rows: T[],
): T | undefined {
  const id = typeof rememberedResultId === "string" ? rememberedResultId.trim() : "";
  return id ? rows.find((row) => row.id === id) : undefined;
}

export const ACTIVE_RECORDING_RESULT_SS = "dano.recording.activeResult";

export function rememberedRecordingResultId(storage: Pick<Storage, "getItem">): string {
  try {
    return String(storage.getItem(ACTIVE_RECORDING_RESULT_SS) || "").trim();
  } catch {
    return "";
  }
}

export function rememberRecordingResultId(
  storage: Pick<Storage, "setItem">,
  resultId: string,
): void {
  const id = String(resultId || "").trim();
  if (!id) return;
  try {
    storage.setItem(ACTIVE_RECORDING_RESULT_SS, id);
  } catch {
    // A disabled session store must not block the recording result page.
  }
}

export function forgetRecordingResultId(storage: Pick<Storage, "removeItem">): void {
  try {
    storage.removeItem(ACTIVE_RECORDING_RESULT_SS);
  } catch {
    // A disabled session store must not block starting another recording.
  }
}

export function beginFreshRecordingEntry(
  storage: Pick<Storage, "removeItem">,
): { freshRecordingEntry: true } {
  forgetRecordingResultId(storage);
  forgetLiveRecording(storage);
  return { freshRecordingEntry: true };
}

export const LIVE_RECORDING_SS = "dano.recording.live";

export type LiveRecordingSession = {
  recordingId?: string;
  action?: string;
};

export function rememberedLiveRecording(
  storage: Pick<Storage, "getItem">,
): LiveRecordingSession | null {
  try {
    const raw = JSON.parse(String(storage.getItem(LIVE_RECORDING_SS) || "null"));
    if (!raw || typeof raw !== "object") return null;
    const recordingId = String(raw.recordingId || "").trim();
    const action = String(raw.action || "").trim();
    const keep = raw.keep === true || recordingId.startsWith("rec_");
    if (!keep) return null;
    const session: LiveRecordingSession = {};
    if (recordingId.startsWith("rec_")) session.recordingId = recordingId;
    if (action) session.action = action;
    return session;
  } catch {
    return null;
  }
}

export function rememberLiveRecording(
  storage: Pick<Storage, "setItem">,
  session: LiveRecordingSession,
): void {
  const recordingId = String(session?.recordingId || "").trim();
  const action = String(session?.action || "").trim();
  if (!recordingId.startsWith("rec_") && !action) return;
  try {
    const payload: { keep: true; recordingId?: string; action?: string } = { keep: true };
    if (recordingId.startsWith("rec_")) payload.recordingId = recordingId;
    if (action) payload.action = action;
    storage.setItem(LIVE_RECORDING_SS, JSON.stringify(payload));
  } catch {
    // A disabled session store must not block the live preview.
  }
}

export function forgetLiveRecording(storage: Pick<Storage, "removeItem">): void {
  try {
    storage.removeItem(LIVE_RECORDING_SS);
  } catch {
    // A disabled session store must not block starting another recording.
  }
}
