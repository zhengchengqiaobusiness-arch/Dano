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
