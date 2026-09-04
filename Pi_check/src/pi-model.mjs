/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 *
 * 只负责解析网关已配置的模型凭证。不分析录制、不生成能力。
 */

import { PiRequiredError, logPiOnly } from "./policy.mjs";

export function readPiModelEnv(env = process.env) {
  const apiKey = String(
    env.DANO_PI_API_KEY || env.PI_API_KEY || env.ANTHROPIC_API_KEY || env.OPENAI_API_KEY || "",
  ).trim();
  const baseUrl = String(env.DANO_PI_BASE_URL || env.PI_BASE_URL || "").trim();
  const modelId = String(env.DANO_PI_MODEL || env.PI_MODEL || "").trim();
  let provider = String(env.DANO_PI_PROVIDER || env.PI_PROVIDER || "").trim();
  if (!provider) {
    if (env.ANTHROPIC_API_KEY && !env.DANO_PI_API_KEY && !env.PI_API_KEY) provider = "anthropic";
    else if (env.OPENAI_API_KEY && !env.DANO_PI_API_KEY && !env.PI_API_KEY) provider = "openai";
    else provider = "openai-compat";
  }
  return { apiKey, baseUrl, provider, modelId };
}

export function applyStandardRuntimeKeys(authStorage, env = process.env) {
  const pairs = [
    ["anthropic", env.ANTHROPIC_API_KEY],
    ["openai", env.OPENAI_API_KEY],
    ["google", env.GOOGLE_API_KEY || env.GEMINI_API_KEY],
  ];
  for (const [name, key] of pairs) {
    if (key && typeof authStorage.setRuntimeApiKey === "function") {
      authStorage.setRuntimeApiKey(name, key);
    }
  }
}

export function applyPiModelConfig(authStorage, modelRegistry, env = process.env) {
  const { apiKey, baseUrl, provider, modelId } = readPiModelEnv(env);
  applyStandardRuntimeKeys(authStorage, env);

  if (apiKey && typeof authStorage.setRuntimeApiKey === "function") {
    authStorage.setRuntimeApiKey(provider, apiKey);
  }

  if (baseUrl && apiKey && modelId && typeof modelRegistry.registerProvider === "function") {
    modelRegistry.registerProvider(provider, {
      name: provider,
      baseUrl,
      api: "openai-completions",
      apiKey,
      models: [{
        id: modelId,
        name: modelId,
        reasoning: false,
        input: ["text", "image"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: Number(env.DANO_PI_CONTEXT_WINDOW || env.PI_CONTEXT_WINDOW || 198000),
        maxTokens: Number(env.DANO_PI_MAX_TOKENS || env.PI_MAX_TOKENS || 32768),
      }],
    });
  }

  const available = typeof modelRegistry.getAvailable === "function" ? modelRegistry.getAvailable() : [];
  let model = null;
  if (modelId && typeof modelRegistry.find === "function") {
    model = modelRegistry.find(provider, modelId);
  }
  if (!model && requestedExact(available, provider, modelId)) {
    model = requestedExact(available, provider, modelId);
  }
  if (!model) {
    const first = Array.isArray(available) ? available[0] : null;
    model = first?.model || first || null;
  }
  if (!model) {
    throw new PiRequiredError(
      `PI 无法启动：没有可用的 PI 模型或凭证 provider=${provider || "(empty)"} model=${modelId || "(empty)"} key_set=${Boolean(apiKey)} baseUrl=${baseUrl ? "set" : "(none)"}`,
    );
  }
  logPiOnly(
    `PI 模型已解析 provider=${model.provider || provider} model=${model.id || modelId} key_set=${Boolean(apiKey)} baseUrl=${baseUrl ? "set" : "(none)"}`,
  );
  return {
    model: model.model || model,
    provider: model.provider || provider,
    modelId: model.id || modelId,
    keySet: Boolean(apiKey),
    baseUrl,
  };
}

function requestedExact(available, provider, modelId) {
  if (!modelId || !Array.isArray(available)) return null;
  return available.find((item) => {
    const id = item.id || item.model?.id || item.modelId;
    const prov = item.provider || item.model?.provider;
    return id === modelId && (!provider || prov === provider);
  }) || null;
}
