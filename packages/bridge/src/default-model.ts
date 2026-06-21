import { existsSync } from "node:fs";
import type {
  AgentSession,
  SessionEntry,
  SessionManager,
} from "@earendil-works/pi-coding-agent";
import type { RpcModel, RpcThinkingLevel } from "./types.js";

export interface DefaultModelSettings {
  provider?: string;
  modelId?: string;
}

export interface DefaultSessionSettings {
  models?: readonly DefaultModelSettings[];
  provider?: string;
  modelId?: string;
  thinkingLevel?: RpcThinkingLevel;
}

export interface SessionDefaultsState {
  model?: RpcModel;
  thinkingLevel: RpcThinkingLevel;
}

type ModelSessionManager = Pick<
  SessionManager,
  | "appendModelChange"
  | "appendThinkingLevelChange"
  | "getBranch"
  | "getSessionFile"
>;

type FlushableSessionManager = {
  _rewriteFile?: () => void;
  flushed?: boolean;
};

// Mirrors pi-coding-agent 0.74.0; the resolver module is not exported.
const DEFAULT_MODEL_PER_PROVIDER: Record<string, string> = {
  "amazon-bedrock": "us.anthropic.claude-opus-4-6-v1",
  anthropic: "claude-opus-4-7",
  openai: "gpt-5.4",
  "azure-openai-responses": "gpt-5.4",
  "openai-codex": "gpt-5.5",
  deepseek: "deepseek-v4-pro",
  google: "gemini-3.1-pro-preview",
  "google-vertex": "gemini-3.1-pro-preview",
  "github-copilot": "gpt-5.4",
  openrouter: "moonshotai/kimi-k2.6",
  "vercel-ai-gateway": "zai/glm-5.1",
  xai: "grok-4.20-0309-reasoning",
  groq: "openai/gpt-oss-120b",
  cerebras: "zai-glm-4.7",
  zai: "glm-5.1",
  mistral: "devstral-medium-latest",
  minimax: "MiniMax-M2.7",
  "minimax-cn": "MiniMax-M2.7",
  moonshotai: "kimi-k2.6",
  "moonshotai-cn": "kimi-k2.6",
  huggingface: "moonshotai/Kimi-K2.6",
  fireworks: "accounts/fireworks/models/kimi-k2p6",
  opencode: "kimi-k2.6",
  "opencode-go": "kimi-k2.6",
  "kimi-coding": "kimi-for-coding",
  "cloudflare-workers-ai": "@cf/moonshotai/kimi-k2.6",
  "cloudflare-ai-gateway": "workers-ai/@cf/moonshotai/kimi-k2.6",
  xiaomi: "mimo-v2.5-pro",
  "xiaomi-token-plan-cn": "mimo-v2.5-pro",
  "xiaomi-token-plan-ams": "mimo-v2.5-pro",
  "xiaomi-token-plan-sgp": "mimo-v2.5-pro",
};

function sameModel(
  left: Pick<RpcModel, "provider" | "id"> | null | undefined,
  right: Pick<RpcModel, "provider" | "id"> | null | undefined,
): boolean {
  return Boolean(
    left && right && left.provider === right.provider && left.id === right.id,
  );
}

function findAvailableModel(
  availableModels: readonly RpcModel[],
  target: Pick<RpcModel, "provider" | "id">,
): RpcModel | undefined {
  return availableModels.find(model => sameModel(model, target));
}

export function findLatestModelInfo(
  branch: readonly SessionEntry[],
): RpcModel | null {
  for (let index = branch.length - 1; index >= 0; index -= 1) {
    const entry = branch[index];
    if (entry?.type === "model_change") {
      return { provider: entry.provider, id: entry.modelId };
    }
  }

  return null;
}

export function findLatestThinkingLevelInfo(
  branch: readonly SessionEntry[],
): RpcThinkingLevel | null {
  for (let index = branch.length - 1; index >= 0; index -= 1) {
    const entry = branch[index];
    if (entry?.type !== "thinking_level_change") {
      continue;
    }

    return normalizeThinkingLevel(entry.thinkingLevel);
  }

  return null;
}

function normalizeThinkingLevel(value: string): RpcThinkingLevel {
  switch (value) {
    case "off":
    case "minimal":
    case "low":
    case "medium":
    case "high":
    case "xhigh":
      return value;
    default:
      return "off";
  }
}

function normalizeModelDefaults(
  defaults?: DefaultModelSettings | DefaultSessionSettings,
): readonly DefaultModelSettings[] {
  const settings = defaults as DefaultSessionSettings | undefined;
  if (settings?.models) {
    return settings.models;
  }

  return defaults ? [defaults] : [];
}

export function selectInitialModel(
  availableModels: readonly RpcModel[],
  defaults?: DefaultModelSettings | DefaultSessionSettings,
): RpcModel | null {
  for (const candidate of normalizeModelDefaults(defaults)) {
    if (!candidate.provider || !candidate.modelId) {
      continue;
    }

    const savedDefault = findAvailableModel(availableModels, {
      provider: candidate.provider,
      id: candidate.modelId,
    });
    if (savedDefault) {
      return savedDefault;
    }
  }

  for (const [provider, id] of Object.entries(DEFAULT_MODEL_PER_PROVIDER)) {
    const providerDefault = findAvailableModel(availableModels, {
      provider,
      id,
    });
    if (providerDefault) {
      return providerDefault;
    }
  }

  return availableModels[0] ?? null;
}

export function ensureSessionManagerModelChange(
  sessionManager: ModelSessionManager,
  model: RpcModel,
): void {
  const latestModel = findLatestModelInfo(sessionManager.getBranch());
  if (sameModel(latestModel, model)) {
    flushMissingSessionFile(sessionManager);
    return;
  }

  sessionManager.appendModelChange(model.provider, model.id);
  flushMissingSessionFile(sessionManager);
}

export function ensureSessionManagerThinkingLevelChange(
  sessionManager: ModelSessionManager,
  thinkingLevel: RpcThinkingLevel,
): void {
  const latestThinkingLevel = findLatestThinkingLevelInfo(
    sessionManager.getBranch(),
  );
  if (latestThinkingLevel === thinkingLevel) {
    flushMissingSessionFile(sessionManager);
    return;
  }

  sessionManager.appendThinkingLevelChange(thinkingLevel);
  flushMissingSessionFile(sessionManager);
}

export function initializeSessionManagerDefaults(
  sessionManager: ModelSessionManager,
  availableModels: readonly RpcModel[],
  defaults?: DefaultSessionSettings,
): SessionDefaultsState {
  const latestModel = findLatestModelInfo(sessionManager.getBranch());
  const model = latestModel
    ? (findAvailableModel(availableModels, latestModel) ?? latestModel)
    : (selectInitialModel(availableModels, defaults) ?? undefined);

  if (!latestModel && model) {
    ensureSessionManagerModelChange(sessionManager, model);
  } else {
    flushMissingSessionFile(sessionManager);
  }

  const latestThinkingLevel = findLatestThinkingLevelInfo(
    sessionManager.getBranch(),
  );
  const thinkingLevel =
    latestThinkingLevel ?? (model ? (defaults?.thinkingLevel ?? "off") : "off");

  if (!latestThinkingLevel) {
    ensureSessionManagerThinkingLevelChange(sessionManager, thinkingLevel);
  } else {
    flushMissingSessionFile(sessionManager);
  }

  return {
    ...(model ? { model } : {}),
    thinkingLevel,
  };
}

export function initializeSessionManagerModel(
  sessionManager: ModelSessionManager,
  availableModels: readonly RpcModel[],
  defaults?: DefaultSessionSettings,
): RpcModel | undefined {
  return initializeSessionManagerDefaults(
    sessionManager,
    availableModels,
    defaults,
  ).model;
}

function flushMissingSessionFile(
  sessionManager: ModelSessionManager,
): void {
  const sessionFile = sessionManager.getSessionFile();
  if (!sessionFile) {
    return;
  }

  const flushable = sessionManager as unknown as FlushableSessionManager;
  if (typeof flushable._rewriteFile !== "function") {
    return;
  }

  if (existsSync(sessionFile) && flushable.flushed !== false) {
    return;
  }

  flushable._rewriteFile();
  flushable.flushed = true;
}

export function resolveAgentSessionDefaults(
  session: AgentSession,
  defaults?: DefaultSessionSettings,
): SessionDefaultsState {
  const availableModels = session.modelRegistry.getAvailable();
  const branch = session.sessionManager.getBranch();
  const latestModel = findLatestModelInfo(branch);
  const model =
    session.model ??
    (latestModel
      ? (findAvailableModel(availableModels, latestModel) ?? latestModel)
      : (selectInitialModel(availableModels, defaults) ?? undefined));

  if (model) {
    const state = session.state as typeof session.state | undefined;
    if (state) {
      state.model = model as typeof state.model;
    }
  }

  if (!latestModel && model) {
    ensureSessionManagerModelChange(session.sessionManager, model);
  }

  const latestThinkingLevel = findLatestThinkingLevelInfo(branch);
  const thinkingLevel =
    latestThinkingLevel ??
    (model
      ? (defaults?.thinkingLevel ??
        normalizeThinkingLevel(
          session.settingsManager.getDefaultThinkingLevel() ?? "off",
        ))
      : "off");
  const state = session.state as typeof session.state | undefined;
  if (state) {
    state.thinkingLevel = thinkingLevel;
  }

  if (!latestThinkingLevel) {
    ensureSessionManagerThinkingLevelChange(
      session.sessionManager,
      thinkingLevel,
    );
  }

  return {
    ...(model ? { model } : {}),
    thinkingLevel,
  };
}

export function resolveAgentSessionModel(
  session: AgentSession,
  defaults?: DefaultSessionSettings,
): RpcModel | undefined {
  return resolveAgentSessionDefaults(session, defaults).model;
}
