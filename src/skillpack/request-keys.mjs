export function bodyKeyPaths(value, prefix = "", out = new Set()) {
  if (Array.isArray(value)) {
    const next = prefix ? `${prefix}[]` : "[]";
    for (const item of value) bodyKeyPaths(item, next, out);
    return out;
  }
  if (!value || typeof value !== "object") return out;
  for (const [key, item] of Object.entries(value)) {
    const next = prefix ? `${prefix}.${key}` : key;
    out.add(next);
    bodyKeyPaths(item, next, out);
  }
  return out;
}

export function keysFromPostData(postData) {
  if (!postData) return [];
  let parsed = postData;
  if (typeof postData === "string") {
    try {
      parsed = JSON.parse(postData);
    } catch {
      return [];
    }
  }
  return [...bodyKeyPaths(parsed)];
}

export function keysFromQuery(query) {
  const text = String(query || "").replace(/^\?/, "");
  if (!text) return [];
  return [...new URLSearchParams(text).keys()];
}

export function requestIndexRow(row) {
  const keys = new Set([
    ...keysFromPostData(row.post_data ?? row.body?.post_data),
    ...keysFromQuery(row.query ?? row.body?.query),
  ]);
  return {
    id: row.id,
    method: row.method || "",
    path: row.path || "",
    keys: [...keys],
  };
}

export function citedPaths(text) {
  const found = new Set();
  const pattern = /\/(?:[A-Za-z0-9_{}-]+\/)+[A-Za-z0-9_{}.-]+/g;
  for (const match of String(text || "").matchAll(pattern)) {
    if (/^\/(?:usr|bin|home|etc|tmp|var|opt|proc|sys)\//.test(match[0])) continue;
    found.add(match[0]);
  }
  return [...found];
}

function keyWritten(text, key) {
  if (text.includes(key)) return true;
  const parts = String(key).split(".").map((part) => part.replace(/\[\]/g, "")).filter(Boolean);
  return parts.length > 0 && parts.every((part) => text.includes(part));
}

export function citationErrors(skillText, requests, file = "") {
  const errors = [];
  const seen = new Set();
  const push = (error) => {
    const id = [error.code, error.file, error.path, error.key].join("|");
    if (seen.has(id)) return;
    seen.add(id);
    errors.push(error);
  };
  const text = String(skillText || "");
  const rows = Array.isArray(requests) ? requests : [];
  const knownPaths = new Set(rows.map((row) => row.path).filter(Boolean));
  for (const cited of citedPaths(text)) {
    const hit = [...knownPaths].some((path) => path === cited || path.endsWith(cited) || cited.endsWith(path));
    if (!hit) push({ code: "path_not_in_evidence", path: cited, file });
  }
  for (const row of rows) {
    if (!row.path || !text.includes(row.path)) continue;
    for (const key of row.keys || []) {
      if (!keyWritten(text, key)) push({ code: "key_not_written", path: row.path, key, id: row.id, file });
    }
  }
  return errors;
}
