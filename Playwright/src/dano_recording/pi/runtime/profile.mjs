import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { AuthStorage, ModelRegistry } from "@earendil-works/pi-coding-agent";

const MIN_SAFE_PI_VERSION = [0, 79, 8];

export function assertCompatiblePiVersion(version) {
  const match = /^(\d+)\.(\d+)\.(\d+)/.exec(String(version || ""));
  if (!match) throw new Error(`invalid Pi runtime version: ${String(version || "")}`);
  const actual = match.slice(1).map(Number);
  const compatible = actual.some((value, index) => {
    if (value === MIN_SAFE_PI_VERSION[index]) return false;
    return value > MIN_SAFE_PI_VERSION[index]
      && actual.slice(0, index).every((item, prior) => item === MIN_SAFE_PI_VERSION[prior]);
  }) || actual.every((value, index) => value === MIN_SAFE_PI_VERSION[index]);
  if (!compatible) {
    throw new Error(
      `Pi runtime ${version} is unsupported; >=0.79.8 is required to prevent `
      + "assistant-role continuation after successful overflow compaction",
    );
  }
  return String(version);
}

export function installedPiVersion() {
  const entry = fileURLToPath(import.meta.resolve("@earendil-works/pi-coding-agent"));
  const packageFile = path.resolve(path.dirname(entry), "../package.json");
  const manifest = JSON.parse(fs.readFileSync(packageFile, "utf8"));
  return assertCompatiblePiVersion(manifest.version);
}

export function createModelProfile() {
  installedPiVersion();
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
