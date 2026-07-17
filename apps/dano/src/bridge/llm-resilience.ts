import type {
  AgentSession,
  AgentSessionEvent,
  SettingsManager,
} from "@earendil-works/pi-coding-agent";

export const DEFAULT_DANO_LLM_TIMEOUT_MS = 300_000;
export const DANO_LLM_MAX_RETRIES = 10;
export const DANO_LLM_RETRY_WINDOW_MS = 15 * 60_000;
const DANO_LLM_RETRY_BASE_DELAY_MS = 2_000;

const READ_ONLY_TOOLS = new Set(["read", "get_dano_version"]);

export function resolveDanoLlmTimeoutMs(
  env: NodeJS.ProcessEnv = process.env,
): number {
  const configured = env.DANO_LLM_TIMEOUT_MS?.trim();
  if (!configured) return DEFAULT_DANO_LLM_TIMEOUT_MS;

  const timeoutMs = Number(configured);
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs <= 0) {
    throw new Error(
      `Invalid DANO_LLM_TIMEOUT_MS: expected a positive integer, received ${JSON.stringify(configured)}`,
    );
  }
  return timeoutMs;
}

function hasVisibleAssistantOutput(event: AgentSessionEvent): boolean {
  if (event.type !== "message_update" || event.message.role !== "assistant") {
    return false;
  }

  return event.message.content.some(block => {
    if (block.type === "text") return block.text.trim().length > 0;
    if (block.type === "thinking") return block.thinking.trim().length > 0;
    return false;
  });
}

export function configureDanoLlmResilience(
  settingsManager: SettingsManager,
  session: AgentSession,
  env: NodeJS.ProcessEnv = process.env,
): void {
  const timeoutMs = resolveDanoLlmTimeoutMs(env);
  let retryEnabled = true;
  let providerTimeoutMs = timeoutMs;
  let retryAttempt = 0;
  let requestStartedAt = Date.now();

  settingsManager.applyOverrides({
    retry: {
      enabled: true,
      maxRetries: DANO_LLM_MAX_RETRIES,
      provider: {
        timeoutMs,
        maxRetries: 0,
      },
    },
  });

  const setRetryEnabled = (enabled: boolean) => {
    if (retryEnabled === enabled) return;
    retryEnabled = enabled;
    settingsManager.applyOverrides({ retry: { enabled } });
  };

  const setProviderTimeout = (nextTimeoutMs: number) => {
    if (providerTimeoutMs === nextTimeoutMs) return;
    providerTimeoutMs = nextTimeoutMs;
    settingsManager.applyOverrides({
      retry: { provider: { timeoutMs: nextTimeoutMs } },
    });
  };

  session.subscribe(event => {
    if (event.type === "message_start" && event.message.role === "user") {
      requestStartedAt = Date.now();
      retryAttempt = 0;
      setProviderTimeout(timeoutMs);
      setRetryEnabled(true);
      return;
    }

    if (event.type === "auto_retry_start") {
      retryAttempt = event.attempt;
      const remainingMs = Math.max(
        1,
        DANO_LLM_RETRY_WINDOW_MS -
          (Date.now() - requestStartedAt) -
          event.delayMs,
      );
      setProviderTimeout(Math.min(timeoutMs, remainingMs));
      return;
    }

    if (
      event.type === "message_end" &&
      event.message.role === "assistant" &&
      event.message.stopReason === "error"
    ) {
      const nextDelayMs =
        DANO_LLM_RETRY_BASE_DELAY_MS * 2 ** retryAttempt;
      const retryWouldStartAt =
        Date.now() - requestStartedAt + nextDelayMs;
      if (retryWouldStartAt >= DANO_LLM_RETRY_WINDOW_MS) {
        setRetryEnabled(false);
      }
      return;
    }

    if (hasVisibleAssistantOutput(event)) {
      setRetryEnabled(false);
      return;
    }

    if (
      event.type === "tool_execution_start" &&
      !READ_ONLY_TOOLS.has(event.toolName)
    ) {
      setRetryEnabled(false);
    }
  });
}
