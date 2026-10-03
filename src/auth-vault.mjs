import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { recordingDir } from "./paths.mjs";

export function canonicalHeaderName(name) {
  return String(name || "")
    .trim()
    .split("-")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1).toLowerCase())
    .join("-");
}

export function isSealedHeaderValue(value) {
  const text = String(value ?? "");
  return text.startsWith("[sealed:") || text.includes("****");
}

const CREDENTIAL_HEADERS = new Set([
  "authorization",
  "tenant-id",
  "tenantid",
  "tenant_id",
  "cookie",
  "x-token",
  "x-access-token",
]);

export function usableAuthHeaders(raw) {
  const headers = {};
  const source = raw && typeof raw === "object" ? raw.headers || raw : {};
  for (const [key, value] of Object.entries(source || {})) {
    const name = canonicalHeaderName(key);
    const text = String(value ?? "").trim();
    if (!name || !text || isSealedHeaderValue(text)) continue;
    if (/^(tenant-id|tenantid|tenant_id)$/i.test(String(key)) || /^(tenant-id|tenantid)$/i.test(name)) {
      headers["Tenant-Id"] = text;
      continue;
    }
    headers[name] = /^authorization$/i.test(name) ? asAuthorization(text) || text : text;
  }
  return headers;
}

export function hasCredentialHeaders(headers) {
  return Object.keys(usableAuthHeaders(headers)).length > 0;
}

export function asAuthorization(value) {
  let text = String(value ?? "").trim();
  if (!text || isSealedHeaderValue(text)) return "";
  while (/^bearer\s+/i.test(text)) text = text.replace(/^bearer\s+/i, "").trim();
  return text ? `Bearer ${text}` : "";
}

function vaultPath(recordingId) {
  return path.join(recordingDir(recordingId), "auth-vault.json");
}

export async function readAuthVault(recordingId) {
  try {
    return JSON.parse(await readFile(vaultPath(recordingId), "utf8"));
  } catch (error) {
    if (error?.code === "ENOENT") return { headers: {} };
    return { headers: {} };
  }
}

function unwrapStorageValue(raw) {
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
      if (/^tenantId$|^tenant-id$|^tenant_id$|^TENANT_ID$|^tenantid$/i.test(name)) headers["Tenant-Id"] = value;
    }
  }
  return usableAuthHeaders(headers);
}

function loginBody(raw) {
  let data = raw;
  if (typeof raw === "string") {
    try {
      data = JSON.parse(raw);
    } catch {
      return null;
    }
  }
  if (!data || typeof data !== "object") return null;
  const nested = data.data;
  return nested && typeof nested === "object" && !Array.isArray(nested) ? nested : data;
}

export function headersFromLoginPayload(raw) {
  const body = loginBody(raw);
  if (!body) return {};
  const token = body.accessToken || body.access_token || body.token;
  if (!token || isSealedHeaderValue(token)) return {};
  const headers = { Authorization: asAuthorization(token) };
  const tenantId = body.tenantId || body.tenant_id;
  if (tenantId) headers["Tenant-Id"] = String(tenantId);
  return usableAuthHeaders(headers);
}

export function issuedFieldNames(raw) {
  const body = loginBody(raw);
  if (!body) return [];
  const names = [];
  if (body.accessToken) names.push("accessToken");
  else if (body.access_token) names.push("access_token");
  if (body.refreshToken) names.push("refreshToken");
  else if (body.refresh_token) names.push("refresh_token");
  if (body.tenantId) names.push("tenantId");
  else if (body.tenant_id) names.push("tenant_id");
  return names;
}

export function issuedCredential(raw) {
  const body = loginBody(raw);
  if (!body) return null;
  const accessToken = body.accessToken || body.access_token;
  const refreshToken = body.refreshToken || body.refresh_token;
  if (!accessToken || !refreshToken || isSealedHeaderValue(accessToken) || isSealedHeaderValue(refreshToken)) return null;
  return {
    accessToken: String(accessToken),
    refreshToken: String(refreshToken),
    expiresTime: Number(body.expiresTime || body.expires_at || 0) || 0,
  };
}

export function rollCredentialUrl(url, previousSecret, nextSecret) {
  if (!previousSecret || !nextSecret || previousSecret === nextSecret) return url;
  const parsed = new URL(url);
  for (const [key, value] of [...parsed.searchParams.entries()]) {
    if (value === previousSecret) parsed.searchParams.set(key, nextSecret);
  }
  return parsed.toString();
}

export async function writeAuthVault(recordingId, headers, extra = {}) {
  const file = vaultPath(recordingId);
  await mkdir(path.dirname(file), { recursive: true });
  const previous = await readAuthVault(recordingId);
  const payload = {
    headers: { ...usableAuthHeaders(previous.headers), ...usableAuthHeaders(headers) },
  };
  const credential = extra.credential || previous.credential;
  if (credential?.url && credential?.method) {
    payload.credential = { method: String(credential.method), url: String(credential.url) };
    if (credential.refresh_token) payload.credential.refresh_token = String(credential.refresh_token);
  }
  if (extra.expiresTime || previous.expiresTime) payload.expiresTime = extra.expiresTime || previous.expiresTime;
  await writeFile(file, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  return payload;
}

export async function refreshVault(recordingId) {
  const vault = await readAuthVault(recordingId);
  const cred = vault.credential;
  if (!cred?.url || !cred.method) return vault;
  let text = "";
  try {
    const response = await fetch(cred.url, {
      method: cred.method,
      headers: usableAuthHeaders(vault.headers),
    });
    text = await response.text();
  } catch {
    return vault;
  }
  const issued = issuedCredential(text);
  const headers = headersFromLoginPayload(text);
  if (!issued || !Object.keys(headers).length) return vault;
  const params = [...new URL(cred.url).searchParams.values()];
  const sent = cred.refresh_token || (params.length === 1 ? params[0] : "");
  return writeAuthVault(recordingId, headers, {
    credential: {
      method: cred.method,
      url: rollCredentialUrl(cred.url, sent, issued.refreshToken),
      refresh_token: issued.refreshToken,
    },
    expiresTime: issued.expiresTime,
  });
}

export function noteRequestHeaders(recordingId, headers) {
  const picked = {};
  for (const [key, value] of Object.entries(headers || {})) {
    if (CREDENTIAL_HEADERS.has(String(key).toLowerCase())) picked[key] = value;
  }
  const usable = usableAuthHeaders(picked);
  if (!Object.keys(usable).length) return;
  writeAuthVault(recordingId, usable).catch(() => {});
}

export function noteLoginBody(recordingId, text, request = {}) {
  const headers = headersFromLoginPayload(text);
  const issued = issuedCredential(text);
  if (!Object.keys(headers).length && !issued) return;
  const extra = {};
  if (issued && request.url) {
    extra.credential = {
      method: String(request.method || "POST").toUpperCase(),
      url: String(request.url),
      refresh_token: issued.refreshToken,
    };
    extra.expiresTime = issued.expiresTime;
  }
  return writeAuthVault(recordingId, headers, extra).catch(() => {});
}
