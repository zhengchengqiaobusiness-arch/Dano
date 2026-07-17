import type {
  AgentSession,
  AgentSessionEvent,
  SettingsManager,
} from "@earendil-works/pi-coding-agent";

export const DEFAULT_DANO_LLM_TIMEOUT_MS = 300_000;
export const DANO_LLM_MAX_RETRIES = 10;

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

  session.subscribe(event => {
    if (event.type === "message_start" && event.message.role === "user") {
      setRetryEnabled(true);
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
