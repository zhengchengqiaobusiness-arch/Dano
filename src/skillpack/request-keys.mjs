import { issuedCredential, issuedFieldNames } from "../auth-vault.mjs";

export function bodyKeyPaths(value, prefix = "", out = new Set(), empty = null) {
  if (Array.isArray(value)) {
    const next = prefix ? `${prefix}[]` : "[]";
    if (empty && !value.length) empty.add(next);
    for (const item of value) bodyKeyPaths(item, next, out, empty);
    return out;
  }
  if (!value || typeof value !== "object") return out;
  for (const [key, item] of Object.entries(value)) {
    if (key.startsWith("_")) continue;
    const next = prefix ? `${prefix}.${key}` : key;
    out.add(next);
    if (empty && (item === null || item === "")) empty.add(next);
    bodyKeyPaths(item, next, out, empty);
  }
  return out;
}

export function keysFromPostData(postData) {
  if (!postData) return [];
  if (typeof postData === "object") return [...bodyKeyPaths(postData)];
  const text = String(postData);
  try {
    return [...bodyKeyPaths(JSON.parse(text))];
  } catch {
    const trimmed = text.trim();
    if (!trimmed || trimmed.startsWith("<") || trimmed.startsWith("--") || !trimmed.includes("=")) return [];
    return [...new URLSearchParams(trimmed).keys()].filter(Boolean);
  }
}

export function keysFromQuery(query) {
  const text = String(query || "").replace(/^\?/, "");
  if (!text) return [];
  return [...new URLSearchParams(text).keys()];
}

function emptyFromQuery(query) {
  const text = String(query || "").replace(/^\?/, "");
  if (!text) return [];
  return [...new URLSearchParams(text).entries()].filter(([, value]) => value === "").map(([key]) => key);
}

export function requestIndexRow(row) {
  const keys = new Set([
    ...keysFromPostData(row.post_data ?? row.body?.post_data),
    ...keysFromQuery(row.query ?? row.body?.query),
  ]);
  const empty = new Set(emptyFromQuery(row.query ?? row.body?.query));
  const post = row.post_data ?? row.body?.post_data;
  if (post) {
    let parsed = post;
    if (typeof post === "string") {
      try {
        parsed = JSON.parse(post);
      } catch {
        parsed = null;
        const trimmed = String(post).trim();
        if (trimmed.includes("=") && !trimmed.startsWith("<") && !trimmed.startsWith("--")) {
          for (const [key, value] of new URLSearchParams(trimmed).entries()) {
            if (value === "") empty.add(key);
          }
        }
      }
    }
    if (parsed) bodyKeyPaths(parsed, "", new Set(), empty);
  }
  const issued = issuedCredential(row.response_body ?? row.body?.response_body);
  const issuedNames = issuedFieldNames(row.response_body ?? row.body?.response_body);
  return {
    id: row.id,
    method: row.method || "",
    path: row.path || "",
    keys: [...keys],
    ...(empty.size ? { empty: [...empty] } : {}),
    ...(issued ? { issues_credential: true } : {}),
    ...(issuedNames.length ? { issued: issuedNames } : {}),
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

export function pathnamesFromRequest(row) {
  const found = new Set();
  if (row?.path) found.add(row.path);
  const parts = [
    row?.url,
    row?.query,
    row?.post_data,
    row?.response_body,
    row?.body?.url,
    row?.body?.query,
    row?.body?.post_data,
    row?.body?.response_body,
  ];
  for (const item of citedPaths(parts.filter(Boolean).join("\n"))) found.add(item);
  return found;
}

export function collapseRequestIndex(rows) {
  const grouped = new Map();
  for (const row of rows || []) {
    const id = `${row.method || ""}\n${row.path || ""}`;
    const prev = grouped.get(id);
    if (!prev) {
      grouped.set(id, {
        ...(row.id ? { id: row.id } : {}),
        method: row.method || "",
        path: row.path || "",
        keys: [...(row.keys || [])],
        ...(row.empty ? { empty: [...row.empty] } : {}),
        ...(row.issues_credential ? { issues_credential: true } : {}),
        ...(row.issued?.length ? { issued: [...row.issued] } : {}),
      });
      continue;
    }
    prev.keys = [...new Set([...(prev.keys || []), ...(row.keys || [])])];
    const empty = new Set([...(prev.empty || []), ...(row.empty || [])]);
    if (empty.size) prev.empty = [...empty];
    else delete prev.empty;
    if (row.issues_credential) prev.issues_credential = true;
    if (row.issued?.length) prev.issued = [...new Set([...(prev.issued || []), ...row.issued])];
  }
  return [...grouped.values()];
}

export function citationErrors(skillText, requests, file = "", { requireKeys = true } = {}) {
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
  const knownPaths = new Set();
  for (const row of rows) {
    for (const item of pathnamesFromRequest(row)) knownPaths.add(item);
  }
  for (const cited of citedPaths(text)) {
    const hit = [...knownPaths].some((path) => path === cited || path.endsWith(cited) || cited.endsWith(path));
    if (!hit) push({ code: "path_not_in_evidence", path: cited, file });
  }
  if (!requireKeys) return errors;
  for (const row of rows) {
    if (!row.path || !text.includes(row.path)) continue;
    for (const key of row.keys || []) {
      if (!keyWritten(text, key)) push({ code: "key_not_written", path: row.path, key, id: row.id, file });
    }
  }
  return errors;
}
