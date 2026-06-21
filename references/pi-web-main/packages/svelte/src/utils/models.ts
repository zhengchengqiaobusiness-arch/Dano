export interface RpcModelInfo {
  id: string;
  provider: string;
  name: string;
  api?: string;
  reasoning?: boolean;
  contextWindow?: number;
  maxTokens?: number;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function readString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function readNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value)
    ? value
    : undefined;
}

export function normalizeRpcModel(value: unknown): RpcModelInfo | null {
  if (!isRecord(value)) return null;

  const id = readString(value.id);
  const provider = readString(value.provider);
  if (!id || !provider) return null;

  return {
    id,
    provider,
    name: readString(value.name) ?? id,
    api: readString(value.api),
    reasoning:
      typeof value.reasoning === "boolean" ? value.reasoning : undefined,
    contextWindow: readNumber(value.contextWindow),
    maxTokens: readNumber(value.maxTokens),
  };
}

export function getModelKey(
  model: Pick<RpcModelInfo, "provider" | "id">,
): string {
  return `${model.provider}/${model.id}`;
}

export function upsertModel(
  models: readonly RpcModelInfo[],
  model: RpcModelInfo,
): RpcModelInfo[] {
  const key = getModelKey(model);
  const index = models.findIndex(entry => getModelKey(entry) === key);
  if (index === -1) return [...models, model];
  const next = [...models];
  next[index] = model;
  return next;
}

function normalizeSearchText(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function scoreSubsequence(query: string, target: string): number {
  let score = 0;
  let lastIndex = -1;
  let streak = 0;

  for (const char of query) {
    const index = target.indexOf(char, lastIndex + 1);
    if (index === -1) return 0;

    score += 4;
    if (index === lastIndex + 1) {
      streak += 1;
      score += 8 + streak * 2;
    } else {
      streak = 0;
      score += Math.max(0, 3 - (index - lastIndex));
    }

    const prevChar = index === 0 ? " " : target[index - 1];
    if (
      prevChar === " " ||
      prevChar === "/" ||
      prevChar === "-" ||
      prevChar === "_"
    ) {
      score += 6;
    }

    lastIndex = index;
  }

  return score - Math.max(0, target.length - query.length) / 20;
}

function scoreToken(query: string, target: string): number {
  if (!query || !target) return 0;
  if (target === query) return 140 + query.length;
  if (target.startsWith(query)) return 120 + query.length;

  const boundaryIndex = target.indexOf(` ${query}`);
  if (boundaryIndex !== -1) return 105 + query.length - boundaryIndex / 100;

  const substringIndex = target.indexOf(query);
  if (substringIndex !== -1) return 90 + query.length - substringIndex / 100;

  return scoreSubsequence(query, target);
}

export function filterModels(
  models: readonly RpcModelInfo[],
  query: string,
): RpcModelInfo[] {
  const normalizedQuery = normalizeSearchText(query);
  if (!normalizedQuery) return [...models];

  const tokens = normalizedQuery.split(/\s+/).filter(Boolean);
  return models
    .map((model, index) => {
      const fields = [
        normalizeSearchText(model.name),
        normalizeSearchText(model.id),
        normalizeSearchText(model.provider),
        normalizeSearchText(model.api ?? ""),
        normalizeSearchText(`${model.provider}/${model.id}`),
      ].filter(Boolean);

      let score = 0;
      for (const token of tokens) {
        const tokenScore = fields.reduce(
          (best, field) => Math.max(best, scoreToken(token, field)),
          0,
        );
        if (!tokenScore) return { model, index, score: 0 };
        score += tokenScore;
      }

      return { model, index, score };
    })
    .filter(entry => entry.score > 0)
    .sort((a, b) => b.score - a.score || a.index - b.index)
    .map(entry => entry.model);
}
