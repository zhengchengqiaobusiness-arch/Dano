/**
 * PI 分析过程日志。只记录事实，不改写 result。
 */

import { logPiOnly } from "./policy.mjs";

const TEXT_LIMIT = 360;

export function compactText(value, limit = TEXT_LIMIT) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (!text) return "";
  return text.length <= limit ? text : `${text.slice(0, limit)}…`;
}

export function summarizeToolArgs(name, args = {}) {
  const payload = args && typeof args === "object" ? args : {};
  if (name === "read_evidence_item") return `seq=${payload.seq ?? ""}`;
  if (name === "read_evidence_delta") return `after_seq=${payload.after_seq ?? 0} limit=${payload.limit ?? ""}`;
  if (name === "read_response_blob" || name === "read_screenshot") {
    return `blob=${payload.blob_id || ""} offset=${payload.offset ?? 0} length=${payload.length ?? ""}`;
  }
  if (name === "submit_recording_draft") {
    const caps = Array.isArray(payload.draft?.capabilities) ? payload.draft.capabilities.length : 0;
    return `capabilities=${caps} title=${compactText(payload.draft?.title, 80)}`;
  }
  if (name === "submit_recording_result") {
    const caps = Array.isArray(payload.result?.capabilities) ? payload.result.capabilities.length : 0;
    const unresolved = Array.isArray(payload.result?.unresolved) ? payload.result.unresolved.length : 0;
    return `final=${payload.final} capabilities=${caps} unresolved=${unresolved}`;
  }
  const keys = Object.keys(payload);
  return keys.length ? compactText(JSON.stringify(payload), 160) : "无参数";
}

export function summarizeToolResult(name, result) {
  if (result == null) return "空";
  if (typeof result !== "object") return compactText(result, 120);
  if (result.error || result.isError) return `失败 ${compactText(result.error || result.message || "error", 160)}`;
  if (name === "list_recording_index") return `items=${result.count ?? result.items?.length ?? 0}`;
  if (name === "list_recording_manifest") {
    return `status=${result.status || ""} last_seq=${result.lastSeq ?? result.last_seq ?? ""} frozen=${result.frozen}`;
  }
  if (name === "read_evidence_item") {
    const event = result.event || {};
    return result.found === false
      ? `未找到 seq=${result.seq ?? ""}`
      : `kind=${event.kind || ""} ${event.payload?.method || ""} ${compactText(event.payload?.url || event.payload?.path || "", 80)}`;
  }
  if (name === "read_evidence_delta") return `events=${result.events?.length ?? 0} next_seq=${result.next_seq ?? ""}`;
  if (name === "submit_recording_draft") return `saved=${result.saved} final=${result.final}`;
  if (name === "submit_recording_result") {
    const caps = result.result?.capabilities?.length ?? result.capability_count ?? result.capabilityCount;
    return `accepted=${result.accepted ?? true} capabilities=${caps ?? "?"}`;
  }
  return compactText(JSON.stringify(result), 160);
}

function messageText(message) {
  const content = message?.content;
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return String(message?.text || message?.errorMessage || "");
  return content
    .map((part) => {
      if (!part || typeof part !== "object") return "";
      if (part.type === "toolCall") return `调用 ${part.name || "tool"}`;
      return part.text || part.thinking || part.reasoning || "";
    })
    .filter(Boolean)
    .join(" ");
}

export function formatAgentEvent(event) {
  const type = String(event?.type || "");
  if (type === "turn_start") return "开始新一轮模型分析";
  if (type === "turn_end") {
    const tools = Array.isArray(event.toolResults) ? event.toolResults.length : 0;
    return `本轮结束 toolResults=${tools} ${compactText(messageText(event.message), 200)}`;
  }
  if (type === "message_end") {
    const role = event.message?.role || "assistant";
    const text = compactText(messageText(event.message), 280);
    if (!text) return "";
    return `模型${role === "assistant" ? "分析" : role}：${text}`;
  }
  if (type === "auto_retry_start") {
    return `模型重试 ${event.attempt}/${event.maxAttempts} ${compactText(event.errorMessage, 160)}`;
  }
  if (type === "auto_retry_end") {
    return `模型重试结束 success=${event.success} ${compactText(event.finalError, 160)}`;
  }
  if (type === "compaction_start") return `开始压缩上下文 reason=${event.reason || ""}`;
  if (type === "compaction_end") return `压缩结束 aborted=${event.aborted} ${compactText(event.errorMessage, 120)}`;
  if (type === "agent_end") return "PI 本轮 prompt 结束";
  return "";
}

export function createPiTrace() {
  const tools = [];
  let turns = 0;
  let lastTool = "";
  return {
    get toolCount() {
      return tools.length;
    },
    get lastTool() {
      return lastTool;
    },
    get turns() {
      return turns;
    },
    summary() {
      return `turns=${turns} tools=${tools.length} last=${lastTool || "-"}`;
    },
    recordTool(name, args, resultText, ok = true) {
      lastTool = `${name} ${summarizeToolArgs(name, args)}`.trim();
      tools.push({ name, ok, detail: lastTool, result: resultText });
      logPiOnly(`[PI分析] ${ok ? "工具完成" : "工具失败"} ${lastTool} → ${compactText(resultText, 200)}`);
    },
    handleEvent(event) {
      if (event?.type === "turn_start") turns += 1;
      if (String(event?.type || "").startsWith("tool_execution")) return;
      const line = formatAgentEvent(event);
      if (line) logPiOnly(`[PI分析] ${line}`);
    },
  };
}
