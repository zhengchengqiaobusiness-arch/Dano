import type { ModelRuntime } from "@earendil-works/pi-coding-agent";
import type { MemoryCollectionHostConfig } from "./memory-host-config.js";
import type { UserMemoryCollectionOptions } from "./user-memory-collection.js";

export type MemoryCollectionModelRuntime = Pick<ModelRuntime, "getModel" | "getAuth" | "completeSimple">;

/** The caller supplies deployment model configuration, never a user's workspace.
 * No model/auth lookup occurs until a separately authorized selection is needed. */
export function memoryCollectionModel(configuration: MemoryCollectionHostConfig,
  runtime: () => Promise<MemoryCollectionModelRuntime>, hostSecrets: readonly string[]): UserMemoryCollectionOptions["selector"] {
  async function binding(signal?: AbortSignal) {
    signal?.throwIfAborted();
    const models = await runtime();
    signal?.throwIfAborted();
    const model = models.getModel(configuration.model.provider, configuration.model.id);
    if (!model) throw new Error("MEMORY_COLLECTION_MODEL_UNAVAILABLE");
    return { models, model };
  }
  return { ...configuration.selector,
    async sensitiveValues(signal) {
      try {
        const { models, model } = await binding(signal);
        const auth = await models.getAuth(model, { signal });
        signal?.throwIfAborted();
        const secrets = [...hostSecrets];
        if (auth?.auth.apiKey) secrets.push(auth.auth.apiKey);
        for (const [name, value] of Object.entries(auth?.auth.headers ?? {})) {
          if (typeof value === "string" && /authorization|cookie|token|key|secret/i.test(name)) {
            secrets.push(value, value.replace(/^(?:Bearer|Basic)\s+/i, ""));
          }
        }
        return secrets.filter(value => value.length > 0);
      } catch { throw new Error("MEMORY_COLLECTION_MODEL_UNAVAILABLE"); }
    },
    async complete({ systemPrompt, data, signal }) {
      try {
        const { models, model } = await binding(signal);
        const result = await models.completeSimple(model, { systemPrompt,
          messages: [{ role: "user", content: data, timestamp: Date.now() }] }, {
          signal, maxTokens: configuration.model.maxTokens, temperature: configuration.model.temperature,
          ...(configuration.model.thinking === undefined ? {} : {
            onPayload: (payload: unknown) => {
              if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw new Error("INVALID_MODEL_PAYLOAD");
              return { ...payload, thinking: { type: configuration.model.thinking } };
            },
          }),
        });
        signal.throwIfAborted();
        if (result.stopReason !== "stop" || result.content.some(block => block.type === "toolCall")) {
          throw new Error("MEMORY_SELECTION_FAILED");
        }
        return result.content.filter(block => block.type === "text").map(block => block.text).join("");
      } catch { throw new Error("MEMORY_SELECTION_FAILED"); }
    },
  };
}
