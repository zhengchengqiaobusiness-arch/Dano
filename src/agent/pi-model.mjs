export function readPiModelEnv(env = process.env) {
  const apiKey = String(
    env.DANO_PI_API_KEY || env.PI_API_KEY || env.API_KEY || env.ANTHROPIC_API_KEY || env.OPENAI_API_KEY || "",
  ).trim();
  const baseUrl = String(env.DANO_PI_BASE_URL || env.PI_BASE_URL || env.BASE_URL || "").trim();
  const modelId = String(env.DANO_PI_MODEL || env.PI_MODEL || env.MODEL || "").trim();
  let provider = String(env.DANO_PI_PROVIDER || env.PI_PROVIDER || "").trim();
  if (!provider) {
    if (env.ANTHROPIC_API_KEY && !env.DANO_PI_API_KEY && !env.PI_API_KEY) provider = "anthropic";
    else if (env.OPENAI_API_KEY && !env.DANO_PI_API_KEY && !env.PI_API_KEY) provider = "openai";
    else provider = "openai-compat";
  }
  return { apiKey, baseUrl, provider, modelId };
}

export function applyPiModelConfig(authStorage, modelRegistry, env = process.env) {
  const { apiKey, baseUrl, provider, modelId } = readPiModelEnv(env);
  if (apiKey && typeof authStorage.setRuntimeApiKey === "function") {
    authStorage.setRuntimeApiKey(provider, apiKey);
  }
  if (baseUrl && apiKey && modelId && typeof modelRegistry.registerProvider === "function") {
    const contextWindow = Number(env.DANO_PI_CONTEXT_WINDOW || env.PI_CONTEXT_WINDOW || 198000);
    const maxTokens = Number(env.DANO_PI_MAX_TOKENS || env.PI_MAX_TOKENS || 32768);
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
        contextWindow,
        maxTokens,
      }],
    });
  }
  let model = null;
  if (modelId && typeof modelRegistry.find === "function") {
    model = modelRegistry.find(provider, modelId);
  }
  if (!model) {
    throw new Error(
      `PI 无法启动：没有可用的 PI 模型或凭证 provider=${provider || "(empty)"} model=${modelId || "(empty)"} key_set=${Boolean(apiKey)} baseUrl=${baseUrl ? "set" : "(none)"}`,
    );
  }
  const resolved = model.model || model;
  return {
    model: resolved,
    provider: model.provider || provider,
    modelId: model.id || modelId,
    keySet: Boolean(apiKey),
    baseUrl,
  };
}
