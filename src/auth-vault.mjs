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
  if (tenantId) headers["Tenant-Id"] = String(tenantId);
  return usableAuthHeaders(headers);
}

export async function writeAuthVault(recordingId, headers) {
  const file = vaultPath(recordingId);
  await mkdir(path.dirname(file), { recursive: true });
  const previous = await readAuthVault(recordingId);
  const payload = { headers: { ...usableAuthHeaders(previous.headers), ...usableAuthHeaders(headers) } };
  await writeFile(file, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  return payload;
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

export function noteLoginBody(recordingId, text) {
  const headers = headersFromLoginPayload(text);
  if (!Object.keys(headers).length) return;
  writeAuthVault(recordingId, headers).catch(() => {});
}
