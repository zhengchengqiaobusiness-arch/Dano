/** Decode SourceMap JSON as data.  No source content is ever executed. */
export function parseSourceMap(text, { maxBytes = 10 * 1024 * 1024, maxSources = 10_000 } = {}) {
  if (text == null || text === "") return { status: "missing", sources: [], sourceContents: [] };
  const bytes = Buffer.byteLength(String(text), "utf8");
  if (bytes > maxBytes) return { status: "too_large", sources: [], sourceContents: [] };
  try {
    const value = JSON.parse(String(text));
    if (!value || value.version !== 3 || !Array.isArray(value.sources)) {
      return { status: "invalid", sources: [], sourceContents: [] };
    }
    if (value.sources.length > maxSources) {
      return { status: "too_large", sources: [], sourceContents: [] };
    }
    return {
      status: "loaded",
      sources: value.sources.map(String),
      sourceContents: Array.isArray(value.sourcesContent) ? value.sourcesContent : [],
      names: Array.isArray(value.names) ? value.names.map(String) : [],
    };
  } catch (error) {
    return { status: "invalid", sources: [], sourceContents: [], error: String(error) };
  }
}
