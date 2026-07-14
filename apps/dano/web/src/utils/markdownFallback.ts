const OBJECT_LITERAL_KEY_PATTERN = /(?:^|[{,]\s*)(?:"([^"]+)"|'([^']+)'|([A-Za-z_$][\w$-]*))\s*:/gm;

function escapedPattern(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function markdownDroppedObjectLiteralContent(
  source: string,
  renderedText: string,
): boolean {
  const keys = new Set<string>();
  for (const match of source.matchAll(OBJECT_LITERAL_KEY_PATTERN)) {
    const key = match[1] ?? match[2] ?? match[3];
    if (key) keys.add(key);
  }
  if (keys.size === 0) return false;

  return [...keys].some(key =>
    !new RegExp(`(?:^|[{,\\s])(?:["']?)${escapedPattern(key)}(?:["']?)\\s*:`, "m")
      .test(renderedText),
  );
}
