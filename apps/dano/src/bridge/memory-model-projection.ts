import type { MemoryHostConfig } from "./memory-host-config.js";
import { parseEnv } from "node:util";

/** Deployment outputs are derived artifacts, never independent model settings. */
export function projectMemoryModels(config: MemoryHostConfig, openViking: Record<string, unknown>, env: string) {
  const models = config.deployment;
  if (!models || !config.reranker) throw new Error("UNIFIED_MEMORY_MODEL_CONFIG_REQUIRED");
  const block = (value: unknown): Record<string, unknown> => {
    if (value === undefined) return {};
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("INVALID_MEMORY_MODEL_PROJECTION");
    return value as Record<string, unknown>;
  };
  const embedding = block(openViking.embedding);
  const ov = { ...openViking, vlm: { ...block(openViking.vlm), ...models.extraction },
    embedding: { ...embedding, max_input_tokens: models.embedding.maxInputTokens,
      dense: { ...block(embedding.dense), provider: "openai", model: models.embedding.model,
        api_base: models.embedding.apiBase, dimension: models.embedding.dimension, encoding_format: "float" } } };
  const variables: Record<string, string> = {
    DANO_EMBEDDING_MODEL_FILE: models.embedding.modelFile,
    DANO_EMBEDDING_MODEL_NAME: models.embedding.model,
    DANO_RERANKER_MODEL_FILE: models.rerankerModelFile,
    DANO_RERANKER_MODEL_NAME: config.reranker.model,
  };
  const lines = env.split(/\r?\n/);
  // Preserve source bytes instead of reserializing credentials. Restrict edits
  // to unambiguous single-line assignments, then verify with Node's env parser.
  // Compose also supports richer syntax; refuse it rather than partly editing it.
  for (const line of lines) {
    if (!line.trim() || line.trimStart().startsWith("#")) continue;
    const match = /^([A-Za-z_][A-Za-z0-9_]*)=([^\r\n]*)$/.exec(line);
    if (!match) throw new Error("UNSUPPORTED_MEMORY_ENV_FORMAT");
    const value = match[2]!.trim();
    const valid = value.startsWith("'") ? /^'[^']*'(?:\s*#.*)?$/.test(value)
      : value.startsWith('"') ? /^"(?:[^"\\]|\\.)*"(?:\s*#.*)?$/.test(value)
      : !/["']/.test(value);
    if (!valid) throw new Error("UNSUPPORTED_MEMORY_ENV_FORMAT");
  }
  for (const [key, value] of Object.entries(variables)) {
    if (!/^[A-Za-z0-9._-]+$/.test(value)) throw new Error("INVALID_MEMORY_MODEL_PROJECTION");
    const matching = lines.flatMap((line, index) => line.startsWith(`${key}=`) ? [index] : []);
    if (matching.length > 1) throw new Error("DUPLICATE_MEMORY_MODEL_ENV");
    if (matching.length) lines[matching[0]!] = `${key}=${value}`;
    else lines.push(`${key}=${value}`);
  }
  const projectedEnv = lines.join("\n");
  const parsed = parseEnv(projectedEnv);
  if (Object.entries(variables).some(([key, value]) => parsed[key] !== value)) throw new Error("INVALID_MEMORY_MODEL_PROJECTION");
  return { openViking: ov, env: projectedEnv };
}
