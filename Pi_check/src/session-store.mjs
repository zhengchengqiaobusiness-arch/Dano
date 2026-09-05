/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 *
 * 只保存/恢复浏览器登录态。不识别页面，不生成能力。
 */

import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SESSION_DIR = path.join(ROOT, "data", "sessions");

export function originFromUrl(raw) {
  try {
    return new URL(String(raw || "")).origin;
  } catch {
    return "";
  }
}

export function isLoginUrl(raw) {
  try {
    return /\/login(?:\/|$)/i.test(new URL(String(raw || "")).pathname);
  } catch {
    return /\/login/i.test(String(raw || ""));
  }
}

function normalizeExplicit(explicit) {
  if (!explicit) return null;
  if (typeof explicit === "string") {
    const text = explicit.trim();
    if (!text) return null;
    try {
      const parsed = JSON.parse(text);
      return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : null;
    } catch {
      return null;
    }
  }
  if (typeof explicit === "object" && !Array.isArray(explicit)) return explicit;
  return null;
}

export function sessionFileForOrigin(origin) {
  const host = String(origin || "").replace(/^https?:\/\//, "").replace(/[^\w.-]+/g, "_");
  return path.join(SESSION_DIR, `${host || "unknown"}.json`);
}

export function looksLoggedIn(state) {
  const origins = Array.isArray(state?.origins) ? state.origins : [];
  for (const item of origins) {
    const rows = Array.isArray(item?.localStorage) ? item.localStorage : [];
    if (rows.some((row) => /access_token|ACCESS_TOKEN|token|core-access/i.test(String(row?.name || "")) && String(row?.value || "").trim())) {
      return true;
    }
  }
  return Array.isArray(state?.cookies) && state.cookies.some((cookie) => String(cookie?.value || "").trim());
}

export async function loadStorageState(targetUrl) {
  const origin = originFromUrl(targetUrl);
  if (!origin) return null;
  try {
    const raw = JSON.parse(await readFile(sessionFileForOrigin(origin), "utf8"));
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
    return raw;
  } catch {
    return null;
  }
}

export async function saveStorageState(targetUrl, state) {
  const origin = originFromUrl(targetUrl);
  if (!origin || !state || typeof state !== "object") return false;
  await mkdir(SESSION_DIR, { recursive: true });
  await writeFile(sessionFileForOrigin(origin), `${JSON.stringify(state)}\n`, "utf8");
  return true;
}

function wsCache(value, expireMs = 253402300799000) {
  return JSON.stringify({
    c: Date.now(),
    e: expireMs,
    v: JSON.stringify(value),
  });
}

function firstLoginCredential() {
  const raw = process.env.DANO_RUNTIME_CREDENTIALS || "";
  if (!raw.trim()) return null;
  try {
    const table = JSON.parse(raw);
    if (!table || typeof table !== "object") return null;
    for (const creds of Object.values(table)) {
      if (!creds || typeof creds !== "object") continue;
      const username = creds.username || creds.user;
      const password = creds.password;
      if (username && password) {
        const tenantName = String(creds.tenant_name || creds.tenantName || "").trim();
        return {
          tenantName: !tenantName || /^\?+$/.test(tenantName) ? "点狮信息" : tenantName,
          username,
          password,
          tenantId: String(creds.tenant_id || creds.tenantId || "1"),
        };
      }
    }
  } catch {
    return null;
  }
  return null;
}

export function playwrightStateFromTokens(origin, { accessToken, refreshToken, tenantId }) {
  if (!origin || !accessToken) return null;
  return {
    cookies: [],
    origins: [{
      origin,
      localStorage: [
        { name: "ACCESS_TOKEN", value: wsCache(accessToken) },
        { name: "REFRESH_TOKEN", value: wsCache(refreshToken || accessToken) },
        { name: "tenantId", value: wsCache(String(tenantId || "1")) },
      ],
    }],
  };
}

export async function silentLoginStorage(targetUrl) {
  const origin = originFromUrl(targetUrl);
  const creds = firstLoginCredential();
  if (!origin || !creds || process.env.PI_CHECK_AUTO_LOGIN === "0") return null;
  const response = await fetch(`${origin}/admin-api/system/auth/login`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "tenant-id": creds.tenantId,
    },
    body: JSON.stringify({
      tenantName: creds.tenantName,
      username: creds.username,
      password: creds.password,
      rememberMe: true,
    }),
  });
  if (!response.ok) return null;
  const payload = await response.json();
  const data = payload?.data && typeof payload.data === "object" ? payload.data : {};
  const accessToken = data.accessToken || data.access_token;
  if (payload?.code !== 0 && payload?.code !== "0" && !accessToken) return null;
  if (!accessToken) return null;
  return playwrightStateFromTokens(origin, {
    accessToken,
    refreshToken: data.refreshToken || data.refresh_token,
    tenantId: data.tenantId || creds.tenantId,
  });
}

export async function resolveStorageState(targetUrl, explicit = null) {
  const provided = normalizeExplicit(explicit);
  if (looksLoggedIn(provided)) return provided;
  const saved = await loadStorageState(targetUrl);
  if (looksLoggedIn(saved)) return saved;
  const fresh = await silentLoginStorage(targetUrl).catch(() => null);
  if (fresh) return fresh;
  return provided || saved;
}
