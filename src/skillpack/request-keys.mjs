import { issuedCredential } from "../auth-vault.mjs";

export function bodyKeyPaths(value, prefix = "", out = new Set(), empty = null) {
  if (Array.isArray(value)) {
    const next = prefix ? `${prefix}[]` : "[]";
    if (empty && !value.length) empty.add(next);
    for (const item of value) bodyKeyPaths(item, next, out, empty);
    return out;
  }
  if (!value || typeof value !== "object") return out;
  for (const [key, item] of Object.entries(value)) {
    if (internalKey(key)) continue;
    const next = prefix ? `${prefix}.${key}` : key;
    out.add(next);
    if (empty && (item === null || item === "")) empty.add(next);
    bodyKeyPaths(item, next, out, empty);
  }
  return out;
}

function internalKey(key) {
  return /^_[A-Z0-9_]+$/.test(String(key || ""));
}

function parsedPost(postData) {
  if (postData && typeof postData === "object") return postData;
  if (typeof postData !== "string" || !postData) return null;
  try {
    return JSON.parse(postData);
  } catch {
    const text = postData.trim();
    if (!text || text.startsWith("{") || text.startsWith("[") || text.startsWith("<") || text.startsWith("---") || !text.includes("=")) return null;
    const out = {};
    for (const [key, value] of new URLSearchParams(text)) {
      if (!key) continue;
      if (Object.hasOwn(out, key)) {
        const prev = out[key];
        out[key] = Array.isArray(prev) ? [...prev, value] : [prev, value];
      } else out[key] = value;
    }
    return Object.keys(out).length ? out : null;
  }
}

function multipartNames(postData) {
  const text = typeof postData === "string" ? postData.trim() : "";
  if (!text.startsWith("---")) return [];
  return [...new Set([...text.matchAll(/content-disposition:[^\r\n]*\bname="([^"]+)"/gi)].map((item) => item[1]).filter(Boolean))];
}

export function keysFromPostData(postData) {
  const named = multipartNames(postData);
  if (named.length) return named;
  const parsed = parsedPost(postData);
  if (!parsed) return [];
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

export function requestIndexRow(row) {
  const keys = new Set([
    ...keysFromPostData(row.post_data ?? row.body?.post_data),
    ...keysFromQuery(row.query ?? row.body?.query),
  ]);
  const empty = new Set(emptyFromQuery(row.query ?? row.body?.query));
  const post = row.post_data ?? row.body?.post_data;
  const parsed = parsedPost(post);
  if (parsed) bodyKeyPaths(parsed, "", new Set(), empty);
  const issued = issuedCredential(row.response_body ?? row.body?.response_body);
  const actionId = row.action_id || row.body?.action_id || "";
  const query = keysFromQuery(row.query ?? row.body?.query);
  return {
    id: row.id,
    method: row.method || "",
    path: row.path || "",
    keys: [...keys],
    ...(query.length ? { query } : {}),
    ...(empty.size ? { empty: [...empty] } : {}),
    ...(issued ? { issues_credential: true } : {}),
    ...(actionId ? { action_id: actionId } : {}),
    ...(row.action ? { action: row.action } : {}),
  };
}

function listedActions(row) {
  const names = [];
  if (Array.isArray(row?.actions)) names.push(...row.actions);
  if (row?.action) names.push(row.action);
  return names.filter(Boolean);
}

function rememberChanges(target, row) {
  const names = listedActions(row);
  if (!names.length) return;
  for (const key of row.changed_keys || []) {
    if (!target.changed_by) target.changed_by = [];
    let item = target.changed_by.find((entry) => entry.key === key);
    if (!item) {
      item = { key, actions: [] };
      target.changed_by.push(item);
    }
    for (const name of names) {
      if (!item.actions.includes(name)) item.actions.push(name);
    }
  }
}

function collectValues(value, prefix, out) {
  if (Array.isArray(value)) {
    const next = prefix ? `${prefix}[]` : "[]";
    if (!value.length) {
      if (next && !out.has(next)) out.set(next, []);
      return;
    }
    for (const item of value) collectValues(item, next, out);
    return;
  }
  if (value && typeof value === "object") {
    for (const [key, item] of Object.entries(value)) {
      if (internalKey(key)) continue;
      collectValues(item, prefix ? `${prefix}.${key}` : key, out);
    }
    return;
  }
  if (!prefix) return;
  if (!out.has(prefix)) out.set(prefix, []);
  out.get(prefix).push(value == null ? "" : String(value));
}

function payloadValues(row) {
  const out = new Map();
  const parsed = parsedPost(row?.post_data ?? row?.body?.post_data);
  if (parsed) collectValues(parsed, "", out);
  const query = String(row?.query ?? row?.body?.query ?? "").replace(/^\?/, "");
  if (query) {
    for (const [key, value] of new URLSearchParams(query)) {
      if (!out.has(key)) out.set(key, []);
      out.get(key).push(value);
    }
  }
  return out;
}

function sameList(left, right) {
  const a = left || [];
  const b = right || [];
  return a.length === b.length && a.every((item, index) => item === b[index]);
}

export function changedRequestKeys(before, after) {
  const left = payloadValues(before);
  const right = payloadValues(after);
  const changed = [];
  for (const key of new Set([...left.keys(), ...right.keys()])) {
    if (!sameList(left.get(key), right.get(key))) changed.push(key);
  }
  return changed;
}

export function sameRequestShape(left, right) {
  const list = (value) => [...(value || [])].map(String).sort().join("\n");
  const a = requestIndexRow(left || {});
  const b = requestIndexRow(right || {});
  return (left?.method || "") === (right?.method || "")
    && (left?.path || "") === (right?.path || "")
    && list(a.keys) === list(b.keys)
    && list(a.query) === list(b.query)
    && list(a.empty) === list(b.empty);
}

export function previousMatchingRequest(items, index) {
  const current = (items || [])[index];
  if (!current) return null;
  for (let at = index - 1; at >= 0; at -= 1) {
    const item = items[at];
    if (!item || item.kind === "script") continue;
    if (sameRequestShape(item, current)) return item;
  }
  return null;
}

export function previousPathRequest(items, index) {
  const current = (items || [])[index];
  if (!current) return null;
  for (let at = index - 1; at >= 0; at -= 1) {
    const item = items[at];
    if (!item || item.kind === "script") continue;
    if (item.method === current.method && item.path === current.path) return item;
  }
  return null;
}

export function keysAddedSince(before, after) {
  if (!before || !after || sameRequestShape(before, after)) return [];
  const left = new Set(requestIndexRow(before).keys);
  return requestIndexRow(after).keys.filter((key) => !left.has(key));
}

export function executableSource(source) {
  return String(source || "")
    .replace(/'''[\s\S]*?'''/g, "")
    .replace(/"""[\s\S]*?"""/g, "")
    .replace(/#[^\n]*/g, "");
}

export function readsIssuedCredential(source) {
  const code = executableSource(source);
  const has = (name) => new RegExp(`\\[\\s*["']${name}["']\\s*\\]|\\.get\\(\\s*["']${name}["']\\s*\\)`).test(code);
  return has("credential") && has("url") && has("method");
}

export function rebuildsCredentialPath(source, credPath) {
  const path = String(credPath || "");
  if (!path || path === "/") return false;
  return executableSource(source).includes(path);
}

export function rebuildsCredentialQuery(source) {
  const code = executableSource(source);
  if (/\[[\s]*["']url["']\s*\]\s*=[^;\n]{0,240}(?:\?|\.split\s*\()/.test(code)) return true;
  const lines = code.split(/\n/);
  for (let index = 0; index < lines.length; index += 1) {
    if (!/\[[\s]*["']url["']\s*\]\s*=/.test(lines[index])) continue;
    let start = index;
    while (start > 0 && !/^def |^async def /.test(lines[start])) start -= 1;
    let end = index + 1;
    while (end < lines.length && !/^def |^async def /.test(lines[end])) end += 1;
    const window = lines.slice(start, end).join("\n");
    if (/parse_qs|urlunparse|urlencode/.test(window)) return true;
  }
  return false;
}

export function authHeaderOverlay(source) {
  const code = executableSource(source);
  const readsFile = /\[[\s]*["']headers["']\s*\]|\.get\(\s*["']headers["']/.test(code);
  if (!readsFile) return { needed: false, replaces: false, missing: false };
  if (!code.includes("DANO_AUTH_HEADERS")) return { needed: true, replaces: false, missing: true };
  const read = code.match(/([A-Za-z_][\w]*)\s*=\s*os\.environ(?:\.get\(\s*["']DANO_AUTH_HEADERS["']\s*\)|\[\s*["']DANO_AUTH_HEADERS["']\s*\])/);
  const parsed = read ? `json\\.loads\\s*\\(\\s*${read[1]}\\s*\\)` : "json\\.loads\\s*\\(\\s*os\\.environ";
  const bare = new RegExp(`return\\s+${parsed}`).test(code);
  const wrapped = new RegExp(`return\\s+\\{\\s*["']headers["']\\s*:\\s*${parsed}\\s*\\}`).test(code);
  const assigned = new RegExp(`(?:\\[[\\s]*["']headers["']\\s*\\]|(?<![\\w.])headers)\\s*=\\s*${parsed}`).test(code);
  return { needed: true, replaces: bare || wrapped || assigned, missing: false };
}

export function citedPaths(text) {
  const found = new Set();
  const pattern = /(?<![A-Za-z0-9_])\/(?:[A-Za-z0-9_{}-]+\/)+[A-Za-z0-9_{}.-]+/g;
  for (const match of executableSource(text).matchAll(pattern)) {
    if (/^\/(?:usr|bin|home|etc|tmp|var|opt|proc|sys)\//.test(match[0])) continue;
    found.add(match[0]);
  }
  return [...found];
}

function keyToken(text, token) {
  const escaped = String(token || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  if (!escaped) return false;
  return new RegExp(`(?:^|[^A-Za-z0-9_])${escaped}(?:$|[^A-Za-z0-9_])`).test(String(text || ""));
}

function keyWritten(text, key) {
  const raw = String(key || "");
  if (!raw) return false;
  if (keyToken(text, raw)) return true;
  const parts = raw.split(/[.[\]]+/).filter(Boolean);
  if (parts.length < 2) return false;
  return parts.every((part) => keyToken(text, part));
}

export function windowsForPath(text, path) {
  const source = String(text || "");
  const windows = [];
  let from = 0;
  while (from < source.length) {
    const at = source.indexOf(path, from);
    if (at < 0) break;
    const before = source.slice(0, at);
    const start = Math.max(before.lastIndexOf("\ndef "), before.lastIndexOf("\nasync def "));
    const rest = source.slice(at + 1);
    const endRel = rest.search(/\ndef |\nasync def /);
    const end = endRel < 0 ? source.length : at + 1 + endRel;
    windows.push(source.slice(start < 0 ? 0 : start, end));
    from = at + path.length;
  }
  return windows;
}

function staticAsset(path) {
  const leaf = String(path || "").split("?")[0].split("/").pop() || "";
  return /\.(?:json|js|css|map|svg|png|woff2?|ttf|ico)$/i.test(leaf);
}

export function adoptedEvidencePaths(cited, requests) {
  const out = new Set();
  for (const path of cited || []) {
    const hit = (requests || []).find((row) => {
      const known = row.path || "";
      return known && (known === path || known.endsWith(path) || path.endsWith(known));
    });
    if (hit?.path) out.add(hit.path);
  }
  return [...out];
}

function requestShape(row) {
  const list = (value) => [...(value || [])].map(String).sort();
  return JSON.stringify({ keys: list(row.keys), query: list(row.query), empty: list(row.empty) });
}

function shapeOf(row) {
  const index = Array.isArray(row?.keys) ? row : requestIndexRow(row || {});
  const list = (value) => [...(value || [])].map(String).sort().join("\n");
  return `${row?.method || ""}\n${row?.path || ""}\n${list(index.keys)}\n${list(index.query)}\n${list(index.empty)}`;
}

export function sameAsUnlinked(row, history) {
  const list = history || [];
  const at = list.findIndex((item) => item && row?.id && item.id === row.id);
  const pool = at >= 0 ? list.slice(0, at) : list;
  const others = pool.filter((item) => item && item.id !== row?.id && item.kind !== "script" && item.method === row?.method && item.path === row?.path);
  if (!others.length) return false;
  const mine = shapeOf(row);
  const same = others.filter((item) => shapeOf(item) === mine);
  const unlinked = same.filter((item) => !item.action_id);
  if (unlinked.length >= 3) return true;
  if (unlinked.some((item) => !changedRequestKeys(item, row).length)) return true;
  return same.some((item) => item.action_id && item.action_id !== row.action_id && !changedRequestKeys(item, row).length);
}

export function writerRequestRows(rows) {
  const business = (rows || []).filter((row) => !staticAsset(row.path));
  const source = business.length ? business : (rows || []);
  const total = new Map();
  const linked = new Map();
  const unlinkedShapes = new Map();
  for (const row of source) {
    const id = `${row.method || ""}\n${row.path || ""}`;
    total.set(id, (total.get(id) || 0) + 1);
    if (row.action_id) linked.set(id, (linked.get(id) || 0) + 1);
    else {
      if (!unlinkedShapes.has(id)) unlinkedShapes.set(id, new Set());
      unlinkedShapes.get(id).add(requestShape(row));
    }
  }
  const kept = source.filter((row) => {
    if (row.issues_credential) return true;
    if (row.action_id && row.same_as_unlinked && !row.changed_keys?.length) return false;
    const id = `${row.method || ""}\n${row.path || ""}`;
    const unlinked = (total.get(id) || 0) - (linked.get(id) || 0);
    if (unlinked >= 3) {
      if (!row.action_id) return false;
      if (row.changed_keys?.length) return true;
      const shapes = unlinkedShapes.get(id);
      return Boolean(shapes && !shapes.has(requestShape(row)));
    }
    if (row.action_id) return true;
    return (total.get(id) || 0) < 3;
  });
  return collapseRequestIndex(kept.length ? kept : source);
}

function indexShape(row) {
  const list = (value) => [...(value || [])].map(String).sort().join("\n");
  return `${row?.method || ""}\n${row?.path || ""}\n${list(row?.keys)}\n${list(row?.query)}\n${list(row?.empty)}`;
}

export function collapseRequestIndex(rows) {
  const grouped = new Map();
  for (const row of rows || []) {
    const id = indexShape(row);
    const prev = grouped.get(id);
    if (!prev) {
      const actions = [...new Set(listedActions(row))];
      const next = {
        ...row,
        keys: [...(row.keys || [])],
        ...(row.empty ? { empty: [...row.empty] } : {}),
      };
      delete next.action;
      if (actions.length) next.actions = actions;
      rememberChanges(next, row);
      grouped.set(id, next);
      continue;
    }
    const actions = [...new Set([...listedActions(prev), ...listedActions(row)])];
    if (actions.length) prev.actions = actions;
    prev.keys = [...new Set([...(prev.keys || []), ...(row.keys || [])])];
    const query = new Set([...(prev.query || []), ...(row.query || [])]);
    if (query.size) prev.query = [...query];
    else delete prev.query;
    const empty = new Set([...(prev.empty || []), ...(row.empty || [])]);
    if (empty.size) prev.empty = [...empty];
    else delete prev.empty;
    if (row.issues_credential) prev.issues_credential = true;
    const changed = new Set([...(prev.changed_keys || []), ...(row.changed_keys || [])]);
    if (changed.size) prev.changed_keys = [...changed];
    else delete prev.changed_keys;
    const added = new Set([...(prev.added_keys || []), ...(row.added_keys || [])]);
    if (added.size) prev.added_keys = [...added];
    else delete prev.added_keys;
    rememberChanges(prev, row);
  }
  return [...grouped.values()];
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
    if (row.issues_credential) continue;
    const scope = windowsForPath(text, row.path).join("\n");
    const missing = (row.keys || []).filter((key) => !keyWritten(scope, key));
    for (const key of missing) {
      push({
        code: "key_not_written",
        path: row.path,
        key,
        keys: missing,
        id: row.id,
        file,
        hint: "引用这条 path 的函数里要逐个写出这些键名。只写 data=data 时，键名不在这个函数里。",
      });
    }
  }
  return errors;
}
