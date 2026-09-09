/**
 * 无分类事实：动作台账和请求形状。时间接近只构成候选。
 */

function asRecord(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

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

function flattenKeys(value, prefix, into) {
  if (value == null) return;
  if (Array.isArray(value)) {
    if (!value.length) {
      into.push({ key: prefix.split(".").at(-1) || prefix, path: prefix, kind: "array" });
      return;
    }
    flattenKeys(value[0], prefix, into);
    return;
  }
  if (typeof value !== "object") {
    into.push({
      key: prefix.split(".").at(-1) || prefix,
      path: prefix,
      kind: typeof value,
    });
    return;
  }
  const keys = Object.keys(value);
  if (!keys.length) {
    into.push({ key: prefix.split(".").at(-1) || prefix, path: prefix, kind: "object" });
    return;
  }
  for (const key of keys) {
    flattenKeys(value[key], prefix ? `${prefix}.${key}` : key, into);
  }
}

function parseBody(payload) {
  const body = asRecord(payload.body);
  if (typeof body.text === "string" && body.text.trim()) {
    try {
      return JSON.parse(body.text);
    } catch {
      return null;
    }
  }
  if (body.json && typeof body.json === "object") return body.json;
  return null;
}

function parseQuery(url) {
  try {
    const parsed = new URL(String(url || ""), "http://local.test");
    return Object.fromEntries(parsed.searchParams.entries());
  } catch {
    return {};
  }
}

export function buildActionTimeline(events) {
  const rows = [];
  const list = Array.isArray(events) ? events : [];
  for (let i = 0; i < list.length; i += 1) {
    const event = list[i];
    const payload = asRecord(event?.payload);
    const kind = String(event?.kind || "");
    if (kind !== "interaction") continue;
    const action = String(payload.kind || "");
    if (/pointer|mousemove|mouseover|mouseenter|mouseleave/i.test(action)) continue;
    const requests = [];
    for (let j = i + 1; j < list.length; j += 1) {
      const next = list[j];
      if (next.kind === "interaction") break;
      if (next.kind !== "network_request") continue;
      const type = String(next.payload?.resource_type || "");
      if (type && type !== "xhr" && type !== "fetch") continue;
      requests.push({
        seq: next.seq,
        method: String(next.payload?.method || ""),
        path: compactUrl(next.payload?.url || next.payload?.path),
        request_id: String(next.payload?.request_id || ""),
      });
    }
    rows.push({
      seq: event.seq,
      actor: String(payload.actor || "human"),
      action,
      label: String(payload.label || payload.text || payload.name || payload.tag || ""),
      requests,
    });
  }
  return { count: rows.length, actions: rows };
}

export function requestShapeFromEvents(events, seq) {
  const list = Array.isArray(events) ? events : [];
  const target = Number(seq) || 0;
  const event = list.find((item) => Number(item.seq) === target);
  if (!event) return { found: false, seq: target };
  const payload = asRecord(event.payload);
  const query = parseQuery(payload.url);
  const body = parseBody(payload);
  const queryKeys = [];
  const bodyKeys = [];
  flattenKeys(query, "query", queryKeys);
  if (body && typeof body === "object") flattenKeys(body, "body", bodyKeys);
  return {
    found: true,
    seq: event.seq,
    method: String(payload.method || ""),
    path: compactUrl(payload.url || payload.path),
    request_id: String(payload.request_id || ""),
    query_keys: queryKeys,
    body_keys: bodyKeys,
  };
}
