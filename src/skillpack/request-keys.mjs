import { issuedCredential } from "../auth-vault.mjs";

export function bodyKeyPaths(value, prefix = "", out = new Set(), empty = null) {
  if (Array.isArray(value)) {
    const next = prefix ? `${prefix}[]` : "[]";
    if (empty && !value.length) {
      empty.add(next);
      if (prefix) empty.add(prefix);
    }
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

function emptyFromQuery(query) {
  const text = String(query || "").replace(/^\?/, "");
  if (!text) return [];
  return [...new URLSearchParams(text).entries()].filter(([, value]) => value === "").map(([key]) => key);
}

export function responseKeyPreview(body, cap = 24) {
  const keys = [];
  const seen = new Set();
  let parsed = body;
  if (typeof body === "string") {
    if (Buffer.byteLength(body) > 32768) return ["_omitted"];
    try {
      parsed = JSON.parse(body);
    } catch {
      return [];
    }
  }
  if (!parsed || typeof parsed !== "object") return [];
  const queue = [{ value: parsed, prefix: "" }];
  while (queue.length && keys.length < cap) {
    const { value, prefix } = queue.shift();
    if (Array.isArray(value)) {
      if (value.length) queue.push({ value: value[0], prefix: prefix ? `${prefix}[]` : "[]" });
      continue;
    }
    if (!value || typeof value !== "object") continue;
    for (const [key, item] of Object.entries(value)) {
      if (key.startsWith("_") || keys.length >= cap) continue;
      const next = prefix ? `${prefix}.${key}` : key;
      if (seen.has(next)) continue;
      seen.add(next);
      keys.push(next);
      if (item && typeof item === "object") queue.push({ value: item, prefix: next });
    }
  }
  return keys;
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
      }
    }
    if (parsed) bodyKeyPaths(parsed, "", new Set(), empty);
  }
  const issued = issuedCredential(row.response_body ?? row.body?.response_body);
  return {
    id: row.id,
    method: row.method || "",
    path: row.path || "",
    keys: [...keys],
    ...(empty.size ? { empty: [...empty] } : {}),
    ...(issued ? { issues_credential: true } : {}),
  };
}

function escapeRegex(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function looksLikeFilePath(value) {
  return /\.(?:png|jpe?g|gif|webp|svg|ico|pdf|zip|xlsx?|docx?|txt|csv|bin|mp4|mp3|json)(?:\?|$)/i.test(value);
}

export function citedPaths(text) {
  const found = new Set();
  const add = (raw) => {
    const value = String(raw || "").replace(/\/+$/, "") || "/";
    if (/^\/(?:usr|bin|home|etc|tmp|var|opt|proc|sys)(?:\/|$)/.test(value)) return;
    if (looksLikeFilePath(value)) return;
    const parts = value.split("/").filter(Boolean);
    if (parts.length < 2 || !parts.some((part) => /[A-Za-z]/.test(part))) return;
    found.add(value);
  };
  const source = String(text || "");
  for (const match of source.matchAll(/["'`](\/(?:[A-Za-z0-9_{}-]+\/)+[A-Za-z0-9_{}.-]+)["'`]/g)) add(match[1]);
  for (const match of source.matchAll(/\b(?:GET|POST|PUT|PATCH|DELETE|HEAD)\s+(\/(?:[A-Za-z0-9_{}-]+\/)+[A-Za-z0-9_{}.-]+)/gi)) add(match[1]);
  for (const match of source.matchAll(/https?:\/\/[^\s"'`]+/gi)) {
    try {
      add(new URL(match[0]).pathname);
    } catch {
      // 不是 URL
    }
  }
  return [...found];
}

function keyWritten(text, key) {
  if (text.includes(key)) return true;
  const parts = String(key).split(".").map((part) => part.replace(/\[\]/g, "")).filter(Boolean);
  return parts.length > 0 && parts.every((part) => text.includes(part));
}

export function windowsForPath(text, path) {
  const source = String(text || "");
  const windows = [];
  let from = 0;
  while (from < source.length) {
    const at = source.indexOf(path, from);
    if (at < 0) break;
    const before = source.slice(0, at);
    let start = Math.max(before.lastIndexOf("\ndef "), before.lastIndexOf("\nasync def "));
    if (start < 0 && /^(?:async )?def /.test(before)) start = 0;
    const rest = source.slice(at + 1);
    const endRel = rest.search(/\ndef |\nasync def /);
    const end = endRel < 0 ? source.length : at + 1 + endRel;
    if (start >= 0) windows.push(source.slice(start, end));
    else if (!/\ndef |\nasync def /.test(source)) {
      windows.push(source);
      break;
    }
    from = at + path.length;
  }
  const aliases = [];
  const assign = new RegExp(`([A-Za-z_][A-Za-z0-9_]*)\\s*=\\s*["']${escapeRegex(path)}["']`, "g");
  for (const match of source.matchAll(assign)) {
    if (!/^(path|url|uri|route|endpoint|method)$/i.test(match[1])) aliases.push(match[1]);
  }
  const defs = new Map();
  for (const block of source.split(/(?=\n(?:async )?def )/)) {
    if (!/(?:^|\n)(?:async )?def /.test(block)) continue;
    const name = (block.match(/(?:^|\n)(?:async )?def\s+([A-Za-z_][A-Za-z0-9_]*)/) || [])[1];
    if (name) defs.set(name, block);
    if (aliases.some((alias) => new RegExp(`\\b${escapeRegex(alias)}\\b`).test(block))) windows.push(block);
  }
  const extra = [];
  for (const window of windows) {
    for (const [name, block] of defs) {
      if (window.includes(`def ${name}`)) continue;
      if (new RegExp(`\\b${escapeRegex(name)}\\s*\\(`).test(window)) extra.push(block);
    }
  }
  return [...windows, ...extra];
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
  const knownPaths = new Set(rows.map((row) => row.path).filter(Boolean));
  for (const cited of citedPaths(text)) {
    const hit = [...knownPaths].some((path) => (
      path === cited
      || path.endsWith(cited)
      || cited.endsWith(path)
      || path.startsWith(`${cited}/`)
    ));
    if (!hit) push({ code: "path_not_in_evidence", path: cited, file });
  }
  if (!requireKeys) return errors;
  for (const row of rows) {
    if (!row.path || !text.includes(row.path)) continue;
    const scope = windowsForPath(text, row.path).join("\n");
    for (const key of row.keys || []) {
      if (!keyWritten(scope, key)) push({ code: "key_not_written", path: row.path, key, id: row.id, file });
    }
  }
  return errors;
}
