/**
 * 录制期把完整鉴权头写到 recording 目录，出包再读。
 * 证据里的 [sealed:auth_ref_…] 不算有证。
 */

import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { loadStorageState } from "./session-store.mjs";

const SEALED_RE = /^\[sealed:([^\]]+)\]$/i;

export function isSealedHeaderValue(value) {
  const text = String(value ?? "").trim();
  return !text || SEALED_RE.test(text) || text.startsWith("****") || text.includes("…");
}

export function sealedRefId(value) {
  const match = String(value ?? "").trim().match(SEALED_RE);
  return match ? match[1] : "";
}

export function usableAuthHeaders(raw) {
  const headers = {};
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return headers;
  for (const [key, value] of Object.entries(raw)) {
    const name = canonicalHeaderName(key);
    const text = String(value ?? "").trim();
    if (!name || isSealedHeaderValue(text)) continue;
    headers[name] = /^authorization$/i.test(name) ? asAuthorization(text) || text : text;
  }
  return headers;
}

export function hasCredentialHeaders(raw) {
  const headers = usableAuthHeaders(raw);
  return Boolean(
    headers.Authorization
    || headers.Cookie
    || headers["X-Token"]
    || headers["X-Access-Token"],
  );
}

export function mergeAuthHeaders(...parts) {
  const headers = {};
  for (const part of parts) {
    Object.assign(headers, usableAuthHeaders(part));
  }
  return headers;
}

export function canonicalHeaderName(name) {
  const key = String(name || "").trim();
  if (!key) return "";
  if (/^authorization$/i.test(key)) return "Authorization";
  if (/^tenant-id$/i.test(key)) return "tenant-id";
  if (/^x-token$/i.test(key)) return "X-Token";
  return key;
}

export function asAuthorization(value) {
  let text = String(value ?? "").trim();
  if (!text || isSealedHeaderValue(text)) return "";
  while (/^bearer\s+/i.test(text)) {
    text = text.replace(/^bearer\s+/i, "").trim();
  }
  return text ? `Bearer ${text}` : "";
}

export function composeAuthHeader(headerName, prefix, token) {
  const name = canonicalHeaderName(headerName) || String(headerName || "Authorization");
  let raw = String(token ?? "").trim();
  if (!raw) return "";
  if (/^authorization$/i.test(name)) {
    while (/^bearer\s+/i.test(raw)) {
      raw = raw.replace(/^bearer\s+/i, "").trim();
    }
    if (!String(prefix ?? "Bearer ").trim()) return raw;
    return asAuthorization(raw);
  }
  const pref = String(prefix ?? "");
  if (pref && raw.toLowerCase().startsWith(pref.trim().toLowerCase())) return raw;
  return `${pref}${raw}`;
}

export function unwrapStorageValue(raw) {
  let text = String(raw ?? "").trim();
  if (!text) return "";
  for (let i = 0; i < 3; i += 1) {
    try {
      const parsed = JSON.parse(text);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed) && parsed.v != null) {
        text = typeof parsed.v === "string" ? parsed.v : JSON.stringify(parsed.v);
        continue;
      }
      if (typeof parsed === "string") {
        text = parsed;
        continue;
      }
      return text;
    } catch {
      return text;
    }
  }
  return text;
}

export function headersFromSealedMap(sealed) {
  const headers = {};
  if (!sealed || typeof sealed !== "object") return headers;
  for (const item of Object.values(sealed)) {
    const name = canonicalHeaderName(item?.name);
    const value = String(item?.value ?? "").trim();
    if (!name || isSealedHeaderValue(value)) continue;
    headers[name] = /^authorization$/i.test(name) ? asAuthorization(value) || value : value;
  }
  return headers;
}

export function unsealHeaders(raw, vault) {
  const headers = {};
  const refs = vault?.refs && typeof vault.refs === "object" ? vault.refs : {};
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return headers;
  for (const [key, value] of Object.entries(raw)) {
    const name = canonicalHeaderName(key);
    const text = String(value ?? "").trim();
    if (!name || !text) continue;
    const ref = sealedRefId(text);
    if (ref) {
      const opened = String(refs[ref]?.value ?? "").trim();
      if (!opened || isSealedHeaderValue(opened)) continue;
      headers[name] = /^authorization$/i.test(name) ? asAuthorization(opened) || opened : opened;
      continue;
    }
    if (isSealedHeaderValue(text)) continue;
    headers[name] = /^authorization$/i.test(name) ? asAuthorization(text) || text : text;
  }
  return headers;
}

export function headersFromLoginPayload(raw) {
  let data = raw;
  if (typeof raw === "string") {
    try {
      data = JSON.parse(raw);
    } catch {
      return {};
    }
  }
  if (!data || typeof data !== "object") return {};
  const body = data.data && typeof data.data === "object" ? data.data : data;
  const token = body.accessToken || body.access_token || body.token;
  if (!token || isSealedHeaderValue(token)) return {};
  const headers = { Authorization: asAuthorization(token) };
  const tenantId = body.tenantId || body.tenant_id;
  if (tenantId) headers["tenant-id"] = String(tenantId);
  return headers;
}

export function headersFromStorageState(state) {
  const headers = {};
  const origins = Array.isArray(state?.origins) ? state.origins : [];
  for (const item of origins) {
    const rows = Array.isArray(item?.localStorage) ? item.localStorage : [];
    for (const row of rows) {
      const name = String(row?.name || "");
      const value = unwrapStorageValue(row?.value);
      if (!value || isSealedHeaderValue(value)) continue;
      if (/^(ACCESS_TOKEN|Admin-Token|token)$/i.test(name) || /access_token/i.test(name)) {
        headers.Authorization = asAuthorization(value);
      }
      if (/^tenantId$|^tenant-id$|^TENANT_ID$/i.test(name)) {
        headers["tenant-id"] = value;
      }
    }
  }
  const cookies = Array.isArray(state?.cookies) ? state.cookies : [];
  for (const cookie of cookies) {
    const name = String(cookie?.name || "");
    const value = String(cookie?.value || "").trim();
    if (!value || isSealedHeaderValue(value)) continue;
    if (/^(Admin-Token|ACCESS_TOKEN|token)$/i.test(name) && !headers.Authorization) {
      headers.Authorization = asAuthorization(value);
    }
  }
  return usableAuthHeaders(headers);
}

export function originFromPath(raw) {
  try {
    return new URL(String(raw || "")).origin;
  } catch {
    return "";
  }
}

export function authHeadersFromEvidenceEvents(events, vault = null) {
  const headers = {};
  const list = Array.isArray(events) ? events : [];
  for (const event of list) {
    if (event?.kind === "network_response") {
      const url = String(event.payload?.url || "");
      const text = event.payload?.body?.text;
      if (/\/auth\/login(?:\?|$)/i.test(url) && text) {
        Object.assign(headers, headersFromLoginPayload(text));
      }
    }
    if (event?.kind !== "network_request") continue;
    const raw = event.payload?.headers || event.payload?.request_headers || {};
    const opened = unsealHeaders(raw, vault);
    for (const [name, value] of Object.entries(opened)) {
      if (/^authorization$/i.test(name) || /^tenant-id$/i.test(name) || /^x-token$/i.test(name)) {
        headers[name] = value;
      }
    }
    for (const [key, value] of Object.entries(raw || {})) {
      const name = canonicalHeaderName(key);
      const text = String(value ?? "").trim();
      if (!name || isSealedHeaderValue(text)) continue;
      if (/^tenant-id$/i.test(name) || /^x-token$/i.test(name)) headers[name] = text;
    }
  }
  Object.assign(headers, usableAuthHeaders(vault?.headers));
  Object.assign(headers, headersFromSealedMap(vault?.refs));
  return usableAuthHeaders(headers);
}

export function baseUrlFromSources({ draft, events = [], startUrl = "" } = {}) {
  const caps = Array.isArray(draft?.capabilities) ? draft.capabilities : [];
  const steps = Array.isArray(draft?.steps) ? draft.steps : [];
  for (const cap of caps) {
    for (const ref of Array.isArray(cap.request_refs) ? cap.request_refs : []) {
      const origin = originFromPath(ref?.path || ref?.url);
      if (origin) return origin;
    }
  }
  for (const step of steps) {
    const origin = originFromPath(step.path || step.url);
    if (origin) return origin;
  }
  for (const event of Array.isArray(events) ? events : []) {
    const url = String(event?.payload?.url || event?.payload?.path || "");
    if (/\/admin-api\//i.test(url)) {
      const origin = originFromPath(url);
      if (origin) return origin;
    }
  }
  for (const event of Array.isArray(events) ? events : []) {
    const origin = originFromPath(event?.payload?.url);
    if (origin) return origin;
  }
  return originFromPath(startUrl);
}

export function vaultFilePath(recordingDir) {
  return path.join(String(recordingDir || ""), "auth-vault.json");
}

export async function readAuthVault(filePath) {
  try {
    const raw = JSON.parse(await readFile(filePath, "utf8"));
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
    return {
      headers: usableAuthHeaders(raw.headers),
      refs: raw.refs && typeof raw.refs === "object" ? raw.refs : {},
      updated_at: String(raw.updated_at || ""),
    };
  } catch (error) {
    if (error?.code === "ENOENT") return null;
    throw error;
  }
}

export async function writeAuthVault(filePath, { headers = {}, refs = {} } = {}) {
  if (!filePath) return null;
  const previous = await readAuthVault(filePath).catch(() => null);
  const record = {
    headers: {
      ...usableAuthHeaders(previous?.headers),
      ...usableAuthHeaders(headers),
    },
    refs: { ...(previous?.refs || {}), ...(refs && typeof refs === "object" ? refs : {}) },
    updated_at: new Date().toISOString(),
  };
  await mkdir(path.dirname(filePath), { recursive: true });
  await writeFile(filePath, `${JSON.stringify(record, null, 2)}\n`, "utf8");
  return record;
}

export async function collectRecordingAuth({ events = [], vault = null, startUrl = "" } = {}) {
  const headers = {
    ...authHeadersFromEvidenceEvents(events, vault),
  };
  if (!headers.Authorization && startUrl) {
    const state = await loadStorageState(startUrl).catch(() => null);
    Object.assign(headers, headersFromStorageState(state));
  }
  return usableAuthHeaders(headers);
}
