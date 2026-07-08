export function isStaleBridgeClientError(message: string): boolean {
  const normalized = message.trim();
  return normalized === "Client was not found" || normalized === "RECONNECT_REQUIRED";
}

export function bridgeServerErrorMessage(
  message: string,
  labels: { staleClient: string; fallback: string },
): string {
  return isStaleBridgeClientError(message) ? labels.staleClient : (message || labels.fallback);
}

export function summarizeErrorMessage(message: string, fallback: string): string {
  const line = message
    .split(/\r?\n/)
    .map(part => part.trim())
    .find(Boolean);
  if (!line) return fallback;
  return line.length > 220 ? `${line.slice(0, 217)}...` : line;
}

export function bridgeCommandErrorNotificationMessage(
  event: { type?: unknown; error?: unknown },
  fallback: string,
): string | null {
  if (event.type !== "command_error") return null;
  return summarizeErrorMessage(
    typeof event.error === "string" ? event.error : "",
    fallback,
  );
}
