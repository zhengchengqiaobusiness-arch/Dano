/**
 * 监控换页判定与上下文稿头。不认业务，只比较主文档 URL（含 hash）。
 */

export const CONTEXT_SKILL_META_START = "<!-- dano-monitor-meta -->";
export const CONTEXT_SKILL_META_END = "<!-- /dano-monitor-meta -->";

export function pageIdentityUrl(url) {
  const raw = String(url || "").trim();
  if (!raw || raw === "about:blank") return "";
  try {
    const parsed = new URL(raw);
    const path = parsed.pathname.replace(/\/+$/, "") || "/";
    return `${parsed.origin}${path}${parsed.search}${parsed.hash}`;
  } catch {
    return raw.replace(/\/+$/, "");
  }
}

export function isMonitorPageEvent(kind, payload = {}) {
  if (kind === "page_navigated") {
    if (payload?.is_main === false) return false;
    return Boolean(pageIdentityUrl(payload?.url || payload?.frame_id || ""));
  }
  if (kind === "visible_control" && /routed/i.test(String(payload?.reason || ""))) {
    return Boolean(pageIdentityUrl(payload?.url || ""));
  }
  return false;
}

export function wrapContextSkillContent(content, {
  entryUrl = "",
  pageUrl = "",
  reconUntilSeq = 0,
} = {}) {
  const strip = new RegExp(
    `${CONTEXT_SKILL_META_START}[\\s\\S]*?${CONTEXT_SKILL_META_END}\\s*`,
    "g",
  );
  const body = String(content || "").replace(strip, "").trim();
  const header = [
    CONTEXT_SKILL_META_START,
    `入口: ${String(entryUrl || "").trim()}`,
    `当前页: ${String(pageUrl || "").trim()}`,
    `recon_until_seq: ${Number(reconUntilSeq) || 0}`,
    CONTEXT_SKILL_META_END,
    "",
  ].join("\n");
  return body ? `${header}${body}\n` : `${header}`;
}
