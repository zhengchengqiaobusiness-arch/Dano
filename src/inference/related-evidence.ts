import type { EvidenceEvent, NetworkEvidence, UiEvidence } from "../domain.js";
import { normalizeUrl } from "./heuristics.js";

function pagePath(url?: string) {
  if (!url) return "";
  try {
    return new URL(url).pathname;
  } catch {
    return "";
  }
}

export function relatedEvidence(primary: EvidenceEvent[], catalog: EvidenceEvent[]): EvidenceEvent[] {
  const paths = new Set<string>();
  const pages = new Set<string>();
  for (const event of primary) {
    if (event.kind === "network") paths.add(normalizeUrl(event.request.url).pathTemplate);
    const page = pagePath((event as UiEvidence).pageUrl || (event as NetworkEvidence).request?.url);
    if (page) pages.add(page);
  }
  const seen = new Set(primary.map(event => event.id));
  const extra = catalog.filter(event => {
    if (seen.has(event.id)) return false;
    if (event.kind === "network") {
      return paths.has(normalizeUrl(event.request.url).pathTemplate);
    }
    const page = pagePath(event.pageUrl);
    return Boolean(page && pages.has(page));
  });
  return [...primary, ...extra];
}
