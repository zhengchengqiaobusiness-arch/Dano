export function isStaleBridgeClientError(message: string): boolean {
  return message.trim() === "Client was not found";
}

export function bridgeServerErrorMessage(
  message: string,
  labels: { staleClient: string; fallback: string },
): string {
  return isStaleBridgeClientError(message) ? labels.staleClient : (message || labels.fallback);
}
