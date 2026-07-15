import path from "node:path";
import { AuthStorage, ModelRegistry } from "@earendil-works/pi-coding-agent";

export function createModelProfile() {
  const auth = AuthStorage.inMemory();
  const registry = ModelRegistry.create(auth);
  const apiKey = process.env.DANO_PI_API_KEY || "";
  const baseUrl = process.env.DANO_PI_BASE_URL || "";
  const provider = process.env.DANO_PI_PROVIDER || "openai-compat";
  const modelId = process.env.DANO_PI_MODEL || "";
  if (!apiKey || !modelId) throw new Error("DANO_PI_API_KEY/DANO_PI_MODEL are required");
  auth.setRuntimeApiKey(provider, apiKey);
  if (baseUrl) {
    registry.registerProvider(provider, {
      name: provider,
      baseUrl,
      apiKey,
      api: "openai-completions",
      models: [{
        id: modelId,
        name: modelId,
        reasoning: false,
        input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 128000,
        maxTokens: 8192,
      }],
    });
  }
  const model = registry.find(provider, modelId);
  if (!model) throw new Error(`Pi model not found: ${provider}/${modelId}`);
  return { auth, registry, model };
}

export function safeSessionPath(candidate, sessionDir) {
  if (!candidate) return "";
  const root = path.resolve(sessionDir) + path.sep;
  const resolved = path.resolve(candidate);
  if (!resolved.startsWith(root)) throw new Error("session_path escapes recording session directory");
  return resolved;
}
