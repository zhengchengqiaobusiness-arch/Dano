export function emit(message) {
  process.stdout.write(JSON.stringify(message) + "\n");
}

export function response(requestId, ok, fields = {}) {
  emit({ type: "response", request_id: requestId, ok, ...fields });
}

export function safeEvent(event) {
  const allowed = {
    type: event?.type || "unknown",
    toolCallId: event?.toolCallId,
    toolName: event?.toolName,
    attempt: event?.attempt,
    maxAttempts: event?.maxAttempts,
    delayMs: event?.delayMs,
    reason: event?.reason,
    aborted: event?.aborted,
    willRetry: event?.willRetry,
    success: event?.success,
    errorMessage: event?.errorMessage,
    finalError: event?.finalError,
  };
  const message = event?.message;
  if (message?.role === "assistant") {
    allowed.message = {
      role: "assistant",
      model: message.model,
      stopReason: message.stopReason,
      usage: message.usage,
      errorMessage: message.errorMessage,
    };
    allowed.usage = message.usage;
  }
  return Object.fromEntries(Object.entries(allowed).filter(([, value]) => value !== undefined));
}
