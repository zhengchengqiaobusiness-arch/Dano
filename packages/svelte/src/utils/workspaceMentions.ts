import type { RpcWorkspaceEntry } from "@pi-web/bridge/types";

const PATH_DELIMITERS = new Set([" ", "\t", '"', "'", "=", "\n"]);
const DEFAULT_SUGGESTION_LIMIT = 20;

export interface WorkspaceMentionContext {
  prefix: string;
  rawQuery: string;
  isQuotedPrefix: boolean;
  start: number;
  end: number;
}

export interface WorkspaceMentionSuggestion {
  value: string;
  label: string;
  description: string;
  kind: RpcWorkspaceEntry["kind"];
  path: string;
}

function findLastDelimiter(text: string): number {
  for (let i = text.length - 1; i >= 0; i -= 1) {
    if (PATH_DELIMITERS.has(text[i] ?? "")) {
      return i;
    }
  }
  return -1;
}

function findUnclosedQuoteStart(text: string): number | null {
  let inQuotes = false;
  let quoteStart = -1;

  for (let i = 0; i < text.length; i += 1) {
    if (text[i] === '"') {
      inQuotes = !inQuotes;
      if (inQuotes) {
        quoteStart = i;
      }
    }
  }

  return inQuotes ? quoteStart : null;
}

function isTokenStart(text: string, index: number): boolean {
  return index === 0 || PATH_DELIMITERS.has(text[index - 1] ?? "");
}

function extractQuotedPrefix(text: string): string | null {
  const quoteStart = findUnclosedQuoteStart(text);
  if (quoteStart === null) return null;

  if (quoteStart > 0 && text[quoteStart - 1] === "@") {
    if (!isTokenStart(text, quoteStart - 1)) {
      return null;
    }
    return text.slice(quoteStart - 1);
  }

  return null;
}

function extractAtPrefix(text: string): string | null {
  const quotedPrefix = extractQuotedPrefix(text);
  if (quotedPrefix?.startsWith('@"')) {
    return quotedPrefix;
  }

  const lastDelimiterIndex = findLastDelimiter(text);
  const tokenStart = lastDelimiterIndex === -1 ? 0 : lastDelimiterIndex + 1;

  if (text[tokenStart] === "@") {
    return text.slice(tokenStart);
  }

  return null;
}

function parsePathPrefix(prefix: string): {
  rawPrefix: string;
  isAtPrefix: boolean;
  isQuotedPrefix: boolean;
} {
  if (prefix.startsWith('@"')) {
    return {
      rawPrefix: prefix.slice(2),
      isAtPrefix: true,
      isQuotedPrefix: true,
    };
  }
  if (prefix.startsWith('"')) {
    return {
      rawPrefix: prefix.slice(1),
      isAtPrefix: false,
      isQuotedPrefix: true,
    };
  }
  if (prefix.startsWith("@")) {
    return {
      rawPrefix: prefix.slice(1),
      isAtPrefix: true,
      isQuotedPrefix: false,
    };
  }
  return { rawPrefix: prefix, isAtPrefix: false, isQuotedPrefix: false };
}

function buildCompletionValue(
  path: string,
  options: { isAtPrefix: boolean; isQuotedPrefix: boolean },
): string {
  const needsQuotes = options.isQuotedPrefix || path.includes(" ");
  const prefix = options.isAtPrefix ? "@" : "";

  if (!needsQuotes) {
    return `${prefix}${path}`;
  }

  return `${prefix}"${path}"`;
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

function getBaseName(path: string): string {
  const normalized = path.endsWith("/") ? path.slice(0, -1) : path;
  const slashIndex = normalized.lastIndexOf("/");
  return slashIndex === -1 ? normalized : normalized.slice(slashIndex + 1);
}

function getScopedQuery(
  rawQuery: string,
): { displayBase: string; query: string } | null {
  const slashIndex = rawQuery.lastIndexOf("/");
  if (slashIndex === -1) return null;

  return {
    displayBase: rawQuery.slice(0, slashIndex + 1),
    query: rawQuery.slice(slashIndex + 1),
  };
}

function scoreEmptyScopedSuggestion(
  path: string,
  kind: RpcWorkspaceEntry["kind"],
): number {
  const segments = path.split("/").filter(Boolean).length;
  const immediateChildBonus =
    segments <= 1 ? 80 : Math.max(0, 48 - segments * 6);
  const kindBonus = kind === "directory" ? 10 : 0;
  return immediateChildBonus + kindBonus;
}

export function getWorkspaceMentionContext(
  text: string,
  cursorOffset: number,
): WorkspaceMentionContext | null {
  const safeCursor = Math.max(0, Math.min(cursorOffset, text.length));
  const lineStart = text.lastIndexOf("\n", Math.max(0, safeCursor - 1)) + 1;
  const textBeforeCursor = text.slice(lineStart, safeCursor);
  const prefix = extractAtPrefix(textBeforeCursor);
  if (!prefix) return null;

  const { rawPrefix, isQuotedPrefix } = parsePathPrefix(prefix);
  return {
    prefix,
    rawQuery: rawPrefix,
    isQuotedPrefix,
    start: safeCursor - prefix.length,
    end: safeCursor,
  };
}

export function getWorkspaceMentionSuggestions(
  entries: readonly RpcWorkspaceEntry[],
  context: WorkspaceMentionContext,
  limit: number = DEFAULT_SUGGESTION_LIMIT,
): WorkspaceMentionSuggestion[] {
  const scopedQuery = getScopedQuery(context.rawQuery);
  const searchQuery = scopedQuery?.query ?? context.rawQuery;
  const normalizedQuery = normalizeSearchText(searchQuery);
  const tokens = normalizedQuery.split(/\s+/).filter(Boolean);

  return entries
    .map((entry, index) => {
      if (scopedQuery && !entry.path.startsWith(scopedQuery.displayBase)) {
        return { entry, index, score: 0 };
      }

      const searchablePath = scopedQuery
        ? entry.path.slice(scopedQuery.displayBase.length)
        : entry.path;
      if (!searchablePath) {
        return { entry, index, score: 0 };
      }

      let score = 0;
      if (tokens.length === 0) {
        score = scoreEmptyScopedSuggestion(searchablePath, entry.kind);
      } else {
        const label = getBaseName(entry.path);
        const fields = [
          { value: normalizeSearchText(label), bonus: 40 },
          { value: normalizeSearchText(searchablePath), bonus: 14 },
          { value: normalizeSearchText(entry.path), bonus: 0 },
        ].filter(field => field.value);

        for (const token of tokens) {
          let tokenScore = 0;
          for (const field of fields) {
            tokenScore = Math.max(
              tokenScore,
              scoreToken(token, field.value) + field.bonus,
            );
          }
          if (!tokenScore) {
            return { entry, index, score: 0 };
          }
          score += tokenScore;
        }
      }

      if (entry.kind === "directory") {
        score += 6;
      }
      if (!searchablePath.includes("/")) {
        score += 12;
      }

      return { entry, index, score };
    })
    .filter(entry => entry.score > 0)
    .sort(
      (a, b) =>
        b.score - a.score ||
        a.entry.path.localeCompare(b.entry.path) ||
        a.index - b.index,
    )
    .slice(0, limit)
    .map(({ entry }) => {
      const displayPath = entry.path;
      const completionPath =
        entry.kind === "directory" ? `${displayPath}/` : displayPath;

      return {
        value: buildCompletionValue(completionPath, {
          isAtPrefix: true,
          isQuotedPrefix: context.isQuotedPrefix,
        }),
        label: `${getBaseName(entry.path)}${entry.kind === "directory" ? "/" : ""}`,
        description: displayPath,
        kind: entry.kind,
        path: entry.path,
      };
    });
}

export function applyWorkspaceMentionCompletion(
  text: string,
  cursorOffset: number,
  context: WorkspaceMentionContext,
  suggestion: WorkspaceMentionSuggestion,
): { text: string; cursor: number } {
  const safeCursor = Math.max(0, Math.min(cursorOffset, text.length));
  const beforePrefix = text.slice(0, context.start);
  const afterCursor = text.slice(safeCursor);
  const isQuotedPrefix =
    context.prefix.startsWith('"') || context.prefix.startsWith('@"');
  const hasLeadingQuoteAfterCursor = afterCursor.startsWith('"');
  const hasTrailingQuoteInValue = suggestion.value.endsWith('"');
  const adjustedAfterCursor =
    isQuotedPrefix && hasTrailingQuoteInValue && hasLeadingQuoteAfterCursor
      ? afterCursor.slice(1)
      : afterCursor;
  const suffix = suggestion.kind === "directory" ? "" : " ";
  const nextText = `${beforePrefix}${suggestion.value}${suffix}${adjustedAfterCursor}`;
  const cursorAdjustment =
    suggestion.kind === "directory" && hasTrailingQuoteInValue
      ? suggestion.value.length - 1
      : suggestion.value.length;

  return {
    text: nextText,
    cursor: beforePrefix.length + cursorAdjustment + suffix.length,
  };
}
