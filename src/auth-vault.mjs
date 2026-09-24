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

export function usableAuthHeaders(raw) {
  const headers = {};
  const source = raw && typeof raw === "object" ? raw.headers || raw : {};
  for (const [key, value] of Object.entries(source || {})) {
    const name = canonicalHeaderName(key);
    const text = String(value ?? "").trim();
    if (!name || !text || isSealedHeaderValue(text)) continue;
    headers[name] = text;
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

export async function writeAuthVault(recordingId, headers) {
  const file = vaultPath(recordingId);
  await mkdir(path.dirname(file), { recursive: true });
  const payload = { headers: usableAuthHeaders(headers) };
  await writeFile(file, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  return payload;
}
