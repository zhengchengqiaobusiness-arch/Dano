export async function completeRecordingSession(request) {
  const session = await request("/api/browser/stop", { method: "POST", body: "{}" });
  return { session };
}
