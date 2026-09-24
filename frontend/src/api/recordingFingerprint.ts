export function preferStableDraftFingerprint(current?: string, incoming?: string): string {
  const next = String(incoming || "").trim();
  const prev = String(current || "").trim();
  if (next.startsWith("rec_") && prev && !prev.startsWith("rec_")) {
    return prev;
  }
  return next || prev;
}
