import { expect, it } from "vitest";
import { projectMemoryModels } from "../memory-model-projection.js";
import type { MemoryHostConfig } from "../memory-host-config.js";

const config = {
  deployment: {
    extraction: { provider: "openai", model: "current-model", api_base: "https://model.example.test/v1", api_key: "synthetic-key" },
    embedding: { model: "embed", modelFile: "embed.gguf", apiBase: "http://embedding:8080/v1", dimension: 512, maxInputTokens: 100 },
    rerankerModelFile: "rank.gguf",
  },
  reranker: { model: "rank" },
} as MemoryHostConfig;

it("projects one source to OpenViking and Compose without changing service identity or unrelated settings", () => {
  const ov = { server: { root_api_key: "synthetic-root" }, storage: { workspace: "/data" },
    vlm: { model: "old-model", temperature: 0 }, embedding: { dense: { api_key: "local-key" } },
    memory: { custom_templates_dir: "/templates" } };
  const env = "KEEP=unchanged\nDANO_EMBEDDING_MODEL_NAME=old\n";
  const result = projectMemoryModels(config, ov, env);
  expect(result.openViking).toMatchObject({ server: ov.server, storage: ov.storage, memory: ov.memory,
    vlm: { model: "current-model", temperature: 0 },
    embedding: { max_input_tokens: 100, dense: { model: "embed", api_key: "local-key" } } });
  expect(result.env).toContain("KEEP=unchanged");
  expect(result.env).toContain("DANO_EMBEDDING_MODEL_NAME=embed");
  expect(result.env).toContain("DANO_RERANKER_MODEL_FILE=rank.gguf");
  expect(result.env).not.toContain("synthetic-key");
  expect(projectMemoryModels(config, result.openViking, result.env)).toEqual(result);
  expect(ov.vlm.model).toBe("old-model");
});

it("refuses duplicate model overrides and env injection", () => {
  expect(() => projectMemoryModels(config, {}, "DANO_RERANKER_MODEL_NAME=a\nDANO_RERANKER_MODEL_NAME=b"))
    .toThrow("DUPLICATE_MEMORY_MODEL_ENV");
  expect(() => projectMemoryModels({ ...config, reranker: { ...config.reranker!, model: "bad\nKEY=value" } }, {}, ""))
    .toThrow("INVALID_MEMORY_MODEL_PROJECTION");
});

it("refuses multiline secrets and alternate Compose assignments without rewriting them", () => {
  for (const env of [
    "SECRET='first\nDANO_RERANKER_MODEL_NAME=inside-secret\nlast'",
    "DANO_RERANKER_MODEL_NAME=old\nDANO_RERANKER_MODEL_NAME: stale",
    " DANO_RERANKER_MODEL_NAME = old",
  ]) expect(() => projectMemoryModels(config, {}, env)).toThrow("UNSUPPORTED_MEMORY_ENV_FORMAT");
});

// The command is also exercised against the built modules by the release preflight.
