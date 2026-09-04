/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 *
 * 只把已有证据投影成短摘要。不分类、不判断能力、不补字段。
 */

function compactUrl(raw) {
  const text = String(raw || "");
  if (!text) return "";
  try {
    const url = new URL(text);
    return `${url.pathname}${url.search}`;
  } catch {
    return text;
  }
}

function asRecord(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

export function buildEvidenceIndex(events) {
  const items = [];
  for (const event of Array.isArray(events) ? events : []) {
    const payload = asRecord(event?.payload);
    const seq = Number(event?.seq) || 0;
    const kind = String(event?.kind || "");
    if (kind === "interaction") {
      items.push({
        seq,
        kind,
        action: String(payload.kind || ""),
        tag: String(payload.tag || ""),
        name: String(payload.name || ""),
        text: String(payload.text || payload.value || ""),
        href: String(payload.href || ""),
      });
      continue;
    }
    if (kind === "network_request") {
      items.push({
        seq,
        kind,
        method: String(payload.method || ""),
        path: compactUrl(payload.url),
        resource_type: String(payload.resource_type || ""),
      });
      continue;
    }
    if (kind === "page_navigated") {
      items.push({
        seq,
        kind,
        url: String(payload.url || payload.frame_url || ""),
      });
    }
  }
  return { count: items.length, items };
}
