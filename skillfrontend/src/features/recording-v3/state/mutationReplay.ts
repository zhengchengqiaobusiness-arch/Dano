export function replayUnknownMutation(message: Record<string, unknown>): Record<string, unknown> {
  return { ...message };
}

export function retryAfterRevisionConflict(message: Record<string, unknown>): Record<string, unknown> {
  const retry = { ...message };
  delete retry.expected_revision;
  delete retry.operation_id;
  return retry;
}

export function drainFlowSyncCallbacks(callbacks: Array<() => void>): void {
  const queued = callbacks.splice(0, callbacks.length);
  for (const callback of queued) callback();
}
