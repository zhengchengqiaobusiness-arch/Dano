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

export function requestIndexRow(row) {
  return {
    id: row.id,
    method: row.method || "",
    path: row.path || "",
    keys: keysFromPostData(row.post_data ?? row.body?.post_data),
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

export function citationErrors(skillText, requests) {
  const errors = [];
  const text = String(skillText || "");
  const rows = Array.isArray(requests) ? requests : [];
  const knownPaths = new Set(rows.map((row) => row.path).filter(Boolean));
  for (const cited of citedPaths(text)) {
    const hit = [...knownPaths].some((path) => path === cited || path.endsWith(cited) || cited.endsWith(path));
    if (!hit) errors.push({ code: "path_not_in_evidence", path: cited });
  }
  for (const row of rows) {
    if (!row.path || !text.includes(row.path)) continue;
    for (const key of row.keys || []) {
      if (!text.includes(key)) errors.push({ code: "key_not_written", path: row.path, key, id: row.id });
    }
  }
  return errors;
}
