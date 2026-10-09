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

const CREDENTIAL_HEADERS = new Set(["authorization", "tenant-id", "cookie", "x-token", "x-access-token"]);

export function usableAuthHeaders(raw) {
  const headers = {};
  const source = raw && typeof raw === "object" ? raw.headers || raw : {};
  for (const [key, value] of Object.entries(source || {})) {
    const name = canonicalHeaderName(key);
    const text = String(value ?? "").trim();
    if (!name || !text || isSealedHeaderValue(text)) continue;
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
      if (/^tenantId$|^tenant-id$|^TENANT_ID$/i.test(name)) headers["Tenant-Id"] = value;
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

export function pickCredentialHeaders(raw) {
  const picked = {};
  for (const [key, value] of Object.entries(raw || {})) {
    if (CREDENTIAL_HEADERS.has(String(key).toLowerCase())) picked[key] = value;
  }
  return usableAuthHeaders(picked);
}

export function replayHeaders(credential, vaultHeaders) {
  const fromCred = usableAuthHeaders(credential?.headers);
  if (Object.keys(fromCred).length) return fromCred;
  const fromVault = usableAuthHeaders(vaultHeaders);
  const out = { ...fromVault };
  delete out.Authorization;
  return out;
}

function querySecrets(url) {
  try {
    return [...new URL(url).searchParams.entries()];
  } catch {
    return [];
  }
}

export function sentRefreshSecret(url, previousSecret = "") {
  const rows = querySecrets(url);
  if (previousSecret && rows.some(([, value]) => value === previousSecret)) return previousSecret;
  const named = rows.find(([key]) => /refresh/i.test(key));
  if (named?.[1]) return named[1];
  const long = rows.find(([, value]) => String(value || "").length >= 8);
  return long?.[1] || previousSecret || "";
}

export function rollCredentialUrl(url, previousSecret, nextSecret) {
  if (!nextSecret) return url;
  const parsed = new URL(url);
  let changed = false;
  if (previousSecret && previousSecret !== nextSecret) {
    for (const [key, value] of [...parsed.searchParams.entries()]) {
      if (value === previousSecret) {
        parsed.searchParams.set(key, nextSecret);
        changed = true;
      }
    }
  }
  if (!changed) {
    for (const [key, value] of [...parsed.searchParams.entries()]) {
      if (/refresh/i.test(key) && value !== nextSecret) {
        parsed.searchParams.set(key, nextSecret);
        changed = true;
      }
    }
  }
  return changed ? parsed.toString() : url;
}

function credentialRecord(credential, previous = {}) {
  if (!credential?.url || !credential?.method) return previous.credential;
  const next = {
    method: String(credential.method),
    url: String(credential.url),
  };
  const refresh = credential.refresh_token || previous.credential?.refresh_token;
  if (refresh) next.refresh_token = String(refresh);
  const headers = usableAuthHeaders({
    ...(previous.credential?.headers || {}),
    ...(credential.headers || {}),
  });
  if (Object.keys(headers).length) next.headers = headers;
  return next;
}

export async function writeAuthVault(recordingId, headers, extra = {}) {
  const file = vaultPath(recordingId);
  await mkdir(path.dirname(file), { recursive: true });
  const previous = await readAuthVault(recordingId);
  const payload = {
    headers: { ...usableAuthHeaders(previous.headers), ...usableAuthHeaders(headers) },
  };
  const credential = credentialRecord(extra.credential, previous) || previous.credential;
  if (credential?.url && credential?.method) payload.credential = credential;
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
    const response = await fetch(cred.url, { method: cred.method, headers: replayHeaders(cred, vault.headers) });
    text = await response.text();
  } catch {
    return vault;
  }
  const issued = issuedCredential(text);
  const headers = headersFromLoginPayload(text);
  if (!issued || !Object.keys(headers).length) return vault;
  const sent = sentRefreshSecret(cred.url, cred.refresh_token);
  return writeAuthVault(recordingId, headers, {
    credential: {
      method: cred.method,
      url: rollCredentialUrl(cred.url, sent, issued.refreshToken),
      refresh_token: issued.refreshToken,
      headers: cred.headers,
    },
    expiresTime: issued.expiresTime,
  });
}

export function noteRequestHeaders(recordingId, headers) {
  const usable = pickCredentialHeaders(headers);
  if (!Object.keys(usable).length) return;
  writeAuthVault(recordingId, usable).catch(() => {});
}

export function noteLoginBody(recordingId, text, request = {}) {
  const headers = headersFromLoginPayload(text);
  const issued = issuedCredential(text);
  if (!Object.keys(headers).length && !issued) return;
  return (async () => {
    const previous = await readAuthVault(recordingId);
    const extra = {};
    if (issued && request.url) {
      const sent = sentRefreshSecret(request.url, previous.credential?.refresh_token);
      extra.credential = {
        method: String(request.method || "POST").toUpperCase(),
        url: rollCredentialUrl(String(request.url), sent, issued.refreshToken),
        refresh_token: issued.refreshToken,
        headers: pickCredentialHeaders(request.headers),
      };
      extra.expiresTime = issued.expiresTime;
    }
    return writeAuthVault(recordingId, headers, extra);
  })().catch(() => {});
}
