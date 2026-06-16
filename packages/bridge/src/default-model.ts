import { existsSync } from "node:fs";
import type {
  AgentSession,
  SessionEntry,
  SessionManager,
} from "@earendil-works/pi-coding-agent";
import type { RpcModel } from "./types.js";

export interface DefaultModelSettings {
  provider?: string;
  modelId?: string;
}

type ModelSessionManager = Pick<
  SessionManager,
  "appendModelChange" | "getBranch" | "getSessionFile"
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

export function selectInitialModel(
  availableModels: readonly RpcModel[],
  defaults?: DefaultModelSettings,
): RpcModel | null {
  if (defaults?.provider && defaults.modelId) {
    const savedDefault = findAvailableModel(availableModels, {
      provider: defaults.provider,
      id: defaults.modelId,
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

export function initializeSessionManagerModel(
  sessionManager: ModelSessionManager,
  availableModels: readonly RpcModel[],
  defaults?: DefaultModelSettings,
): RpcModel | undefined {
  const latestModel = findLatestModelInfo(sessionManager.getBranch());
  if (latestModel) {
    flushMissingSessionFile(sessionManager);
    return findAvailableModel(availableModels, latestModel) ?? latestModel;
  }

  const initialModel = selectInitialModel(availableModels, defaults);
  if (!initialModel) {
    return undefined;
  }

  ensureSessionManagerModelChange(sessionManager, initialModel);
  return initialModel;
}

function flushMissingSessionFile(
  sessionManager: ModelSessionManager,
): void {
  const sessionFile = sessionManager.getSessionFile();
  if (!sessionFile || existsSync(sessionFile)) {
    return;
  }

  const flushable = sessionManager as unknown as FlushableSessionManager;
  if (typeof flushable._rewriteFile !== "function") {
    return;
  }

  flushable._rewriteFile();
  flushable.flushed = true;
}

export function resolveAgentSessionModel(
  session: AgentSession,
): RpcModel | undefined {
  if (session.model) {
    return session.model;
  }

  const availableModels = session.modelRegistry.getAvailable();
  const latestModel = findLatestModelInfo(session.sessionManager.getBranch());
  if (latestModel) {
    const restoredModel =
      findAvailableModel(availableModels, latestModel) ?? latestModel;
    session.state.model = restoredModel as typeof session.state.model;
    return restoredModel;
  }

  const initialModel = selectInitialModel(availableModels, {
    provider: session.settingsManager.getDefaultProvider(),
    modelId: session.settingsManager.getDefaultModel(),
  });
  if (!initialModel) {
    return undefined;
  }

  session.state.model = initialModel as typeof session.state.model;
  ensureSessionManagerModelChange(session.sessionManager, initialModel);
  return initialModel;
}
