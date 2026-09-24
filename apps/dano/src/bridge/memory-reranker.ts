import { Buffer } from "node:buffer";

export interface RecalledMemory { uri: string; text: string; score: number }

export interface MemoryRerankerConfig {
  url: string;
  model: string;
  minimumLogit: number;
  timeoutMs: number;
  maxInputBytes: number;
  maxDocumentBytes: number;
  maxCandidates: number;
}

/** Re-score only USER-scoped OpenViking candidates. Any uncertain result
 * suppresses recall; ordinary chat can still proceed without memory. */
export class MemoryReranker {
  readonly #config: MemoryRerankerConfig;

  constructor(config: MemoryRerankerConfig) {
    this.#config = config;
  }

  get maxCandidates(): number { return this.#config.maxCandidates; }

  async filter(query: string, candidates: RecalledMemory[], signal?: AbortSignal): Promise<RecalledMemory[]> {
    if (!candidates.length || signal?.aborted) return [];
    const { url, model, minimumLogit, timeoutMs, maxInputBytes, maxDocumentBytes, maxCandidates } = this.#config;
    const bounded = candidates.slice(0, maxCandidates);
    if (bounded.some(item => Buffer.byteLength(item.text) > maxDocumentBytes)
      || Buffer.byteLength(query) + Buffer.byteLength(JSON.stringify(bounded.map(item => item.text))) > maxInputBytes) return [];
    try {
      const response = await fetch(url, { method: "POST", redirect: "error",
        headers: { "Content-Type": "application/json" },
        signal: AbortSignal.any([AbortSignal.timeout(timeoutMs), ...(signal ? [signal] : [])]),
        body: JSON.stringify({ model, query, documents: bounded.map(item => item.text), top_n: bounded.length }) });
      if (!response.ok) return [];
      const payload: unknown = await response.json();
      if (!payload || typeof payload !== "object" || Array.isArray(payload)) return [];
      const results = (payload as Record<string, unknown>).results;
      if (!Array.isArray(results) || results.length !== bounded.length) return [];
      const seen = new Set<number>();
      const ranked: Array<{ item: RecalledMemory; logit: number }> = [];
      for (const result of results) {
        if (!result || typeof result !== "object" || Array.isArray(result)) return [];
        const { index, relevance_score: logit } = result as Record<string, unknown>;
        if (!Number.isSafeInteger(index) || (index as number) < 0 || (index as number) >= bounded.length
          || seen.has(index as number) || typeof logit !== "number" || !Number.isFinite(logit)) return [];
        seen.add(index as number);
        if (logit >= minimumLogit) ranked.push({ item: bounded[index as number]!, logit });
      }
      return ranked.sort((a, b) => b.logit - a.logit).map(({ item, logit }) => ({ ...item,
        score: logit >= 0 ? 1 / (1 + Math.exp(-logit)) : Math.exp(logit) / (1 + Math.exp(logit)) }));
    } catch { return []; }
  }
}
