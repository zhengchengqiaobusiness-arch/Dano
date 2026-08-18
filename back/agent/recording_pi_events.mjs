// Map Pi session.subscribe events into compact JSONL agent_event rows.

const STREAM_DELTA_TYPES = new Set(["text_delta", "thinking_delta"]);
const STREAM_EVENTS = new Set(["tool_execution_start", "tool_execution_end"]);

export function summarizeAgentEvent(event) {
  const summary = { type: "agent_event", event: event?.type || "unknown" };
  for (const key of ["toolName", "toolCallId", "attempt", "maxAttempts", "delayMs", "reason", "willRetry", "success", "aborted"]) {
    if (event?.[key] !== undefined) summary[key] = event[key];
  }
  const message = event?.message;
  if (message?.role) summary.role = message.role;
  if (message?.stopReason) summary.stop_reason = message.stopReason;
  if (message?.errorMessage) summary.error = String(message.errorMessage).slice(0, 2000);
  if (message?.usage) summary.usage = message.usage;
  if (event?.errorMessage) summary.error = String(event.errorMessage).slice(0, 2000);
  if (event?.error) summary.error = String(event.error).slice(0, 2000);

  const deltaEvent = event?.assistantMessageEvent;
  if (deltaEvent && typeof deltaEvent === "object") {
    summary.delta_type = String(deltaEvent.type || "");
    const delta = deltaEvent.delta ?? deltaEvent.text ?? deltaEvent.thinking ?? "";
    if (typeof delta === "string" && delta) {
      summary.delta = delta.slice(0, 4000);
    }
  }
  if (event?.type === "tool_execution_start") {
    const args = event.args ?? event.toolArgs;
    if (args && typeof args === "object") {
      try {
        summary.tool_args = JSON.stringify(args).slice(0, 400);
      } catch {
        summary.tool_args = "";
      }
    }
  }
  if (event?.type === "tool_execution_end") {
    summary.success = event.isError ? false : event.success !== false;
    const result = event.result ?? event.errorMessage ?? "";
    if (result) summary.tool_result = String(result).slice(0, 400);
  }
  return summary;
}

export function shouldEmitAgentEvent(summary) {
  if (summary?.error || summary?.stop_reason === "error") return true;
  if (summary?.event === "cancelled") return true;
  if (STREAM_DELTA_TYPES.has(String(summary?.delta_type || "")) && summary?.delta) return true;
  if (STREAM_EVENTS.has(String(summary?.event || ""))) return true;
  return false;
}
