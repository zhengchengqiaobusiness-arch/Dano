import {
  DANO_LLM_AUTHENTICATION_ERROR,
  DANO_LLM_INCOMPLETE_ERROR,
  DANO_LLM_NETWORK_ERROR,
  DANO_LLM_QUOTA_ERROR,
  DANO_LLM_RATE_LIMIT_ERROR,
  DANO_LLM_SERVICE_ERROR,
  DANO_LLM_TIMEOUT_ERROR,
  DANO_LLM_UNKNOWN_ERROR,
} from "./types.js";

export {
  DANO_LLM_AUTHENTICATION_ERROR,
  DANO_LLM_INCOMPLETE_ERROR,
  DANO_LLM_NETWORK_ERROR,
  DANO_LLM_QUOTA_ERROR,
  DANO_LLM_RATE_LIMIT_ERROR,
  DANO_LLM_SERVICE_ERROR,
  DANO_LLM_TIMEOUT_ERROR,
  DANO_LLM_UNKNOWN_ERROR,
};

interface LlmMessageLike {
  role?: unknown;
  stopReason?: unknown;
  errorMessage?: unknown;
  content?: unknown;
}

function hasPartialOutput(content: unknown): boolean {
  if (typeof content === "string") return content.trim().length > 0;
  if (!Array.isArray(content)) return false;

  return content.some(block => {
    if (typeof block === "string") return block.trim().length > 0;
    if (!block || typeof block !== "object") return false;
    const typedBlock = block as { type?: unknown; text?: unknown; thinking?: unknown };
    if (typedBlock.type === "text") {
      return typeof typedBlock.text === "string" && typedBlock.text.trim().length > 0;
    }
    if (typedBlock.type === "thinking") {
      return (
        typeof typedBlock.thinking === "string" &&
        typedBlock.thinking.trim().length > 0
      );
    }
    return false;
  });
}

export function normalizeLlmErrorMessage(
  message: LlmMessageLike,
): string | undefined {
  if (
    message.role !== "assistant" ||
    message.stopReason !== "error" ||
    typeof message.errorMessage !== "string"
  ) {
    return typeof message.errorMessage === "string"
      ? message.errorMessage
      : undefined;
  }

  if (hasPartialOutput(message.content)) return DANO_LLM_INCOMPLETE_ERROR;

  const error = message.errorMessage;
  if (/timed?\s*out|timeout|deadline exceeded/i.test(error)) {
    return DANO_LLM_TIMEOUT_ERROR;
  }
  if (/401|403|unauthori[sz]ed|authentication|invalid api key|incorrect api key/i.test(error)) {
    return DANO_LLM_AUTHENTICATION_ERROR;
  }
  if (/insufficient_quota|quota exceeded|available balance|billing|out of budget/i.test(error)) {
    return DANO_LLM_QUOTA_ERROR;
  }
  if (/429|rate.?limit|too many requests|throttl/i.test(error)) {
    return DANO_LLM_RATE_LIMIT_ERROR;
  }
  if (/\b5\d\d\b|service.?unavailable|server.?error|internal.?error|overloaded/i.test(error)) {
    return DANO_LLM_SERVICE_ERROR;
  }
  if (/network|connection|fetch failed|socket hang up|terminated|reset before headers|upstream connect|ended without/i.test(error)) {
    return DANO_LLM_NETWORK_ERROR;
  }
  return DANO_LLM_UNKNOWN_ERROR;
}

export function normalizeLlmTranscriptMessage<T extends LlmMessageLike>(
  message: T,
): T {
  const errorMessage = normalizeLlmErrorMessage(message);
  if (errorMessage === message.errorMessage) return message;
  return { ...message, errorMessage };
}
