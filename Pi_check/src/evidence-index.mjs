/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 *
 * 只把已有证据投影成短摘要。不分类、不判断能力、不补字段。
 */

import { projectVisibleControlSnapshot, summarizeVisibleControls } from "./visible-controls.mjs";

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
        placeholder: String(payload.placeholder || ""),
        label: String(payload.label || ""),
      });
      continue;
    }
    if (kind === "network_request") {
      const resourceType = String(payload.resource_type || "").toLowerCase();
      if (resourceType && resourceType !== "xhr" && resourceType !== "fetch") continue;
      items.push({
        seq,
        kind,
        method: String(payload.method || ""),
        path: compactUrl(payload.url),
        resource_type: resourceType || String(payload.resource_type || ""),
        request_id: String(payload.request_id || ""),
      });
      continue;
    }
    if (kind === "network_response") {
      const url = compactUrl(payload.url);
      if (/\.(js|css|png|jpe?g|gif|svg|ico|woff2?|map)(\?|$)/i.test(url)) continue;
      const body = asRecord(payload.body);
      items.push({
        seq,
        kind,
        status: Number(payload.status) || 0,
        path: url,
        request_id: String(payload.request_id || ""),
        body_stored: String(body.stored || ""),
        blob_id: String(body.blob_id || ""),
      });
      continue;
    }
    if (kind === "page_navigated") {
      items.push({
        seq,
        kind,
        url: String(payload.url || payload.frame_url || ""),
      });
      continue;
    }
    if (kind === "screenshot") {
      const image = asRecord(payload.image);
      items.push({
        seq,
        kind,
        reason: String(payload.reason || ""),
        blob_id: String(image.blob_id || payload.blob_id || ""),
      });
      continue;
    }
    if (kind === "visible_control") {
      const snapshot = projectVisibleControlSnapshot(event);
      items.push({
        seq,
        kind,
        url: snapshot.url,
        reason: snapshot.reason,
        count: snapshot.count,
        labels: summarizeVisibleControls(snapshot.controls),
      });
    }
  }
  return { count: items.length, items };
}
