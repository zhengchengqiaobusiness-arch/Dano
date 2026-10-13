import { AUTH_MIN_PASSWORD_LENGTH, PasswordPolicyError, validatePassword } from "./policy.mjs";
import { DUMMY_PASSWORD_HASH, hashPassword, verifyPassword } from "./passwords.mjs";
import { AUTH_LOCK_MAX_MINUTES, AUTH_MAX_FAILURES, CLEARED, isLocked, registerFailure } from "./throttle.mjs";
import {
  createTenant,
  getTenantByKey,
  getTenantByUsername,
  getThrottle,
  newApiKey,
  publicTenant,
  setThrottle,
  TenantConflictError,
  updateTenantPassword,
} from "./tenant-store.mjs";

export const CREDENTIAL_ERROR = "用户名或密码错误;连续失败会临时锁定账号";

export class AuthError extends Error {
  constructor(status, detail) {
    super(detail);
    this.name = "AuthError";
    this.status = status;
    this.detail = detail;
  }
}

async function registerLoginFailure(key, state, now) {
  await setThrottle(key, registerFailure(state, {
    now,
    maxFailures: AUTH_MAX_FAILURES,
    lockMaxMinutes: AUTH_LOCK_MAX_MINUTES,
  }));
}

export async function login(username, password, now = Date.now() / 1000) {
  const name = String(username || "").trim();
  const key = name.toLowerCase();
  const state = await getThrottle(key);
  if (isLocked(state, now)) {
    throw new AuthError(401, CREDENTIAL_ERROR);
  }
  const rec = await getTenantByUsername(name);
  if (!rec || !rec.password_hash) {
    verifyPassword(password, DUMMY_PASSWORD_HASH);
    await registerLoginFailure(key, state, now);
    throw new AuthError(401, CREDENTIAL_ERROR);
  }
  if (!verifyPassword(password, rec.password_hash)) {
    await registerLoginFailure(key, state, now);
    throw new AuthError(401, CREDENTIAL_ERROR);
  }
  await setThrottle(key, CLEARED);
  return { tenant: rec.tenant, api_key: rec.api_key };
}

export async function changePassword(apiKey, oldPassword, newPassword) {
  const rec = await getTenantByKey(apiKey || "");
  if (!rec) throw new AuthError(401, "X-Tenant-Key 无效");
  if (!rec.password_hash) throw new AuthError(403, "该租户未启用密码登录");
  if (!verifyPassword(oldPassword, rec.password_hash)) {
    throw new AuthError(401, "原密码错误");
  }
  const candidate = String(newPassword || "").trim();
  try {
    validatePassword(candidate, {
      username: rec.username,
      tenant: rec.tenant,
      minLength: AUTH_MIN_PASSWORD_LENGTH,
    });
  } catch (error) {
    if (error instanceof PasswordPolicyError) throw new AuthError(400, error.message);
    throw error;
  }
  await updateTenantPassword(rec.tenant, hashPassword(candidate));
  await setThrottle(String(rec.username || "").toLowerCase(), CLEARED);
}

export async function registerTenant({ tenant, username = "", password = "", display_name = "" } = {}) {
  const name = String(tenant || "").trim();
  if (!name) throw new AuthError(400, "缺少 tenant");
  const loginName = String(username || name).trim();
  const secret = String(password || "").trim();
  const rec = { tenant: name, display_name: String(display_name || ""), username: loginName, api_key: newApiKey() };
  if (loginName && secret) {
    try {
      validatePassword(secret, { username: loginName, tenant: name, minLength: AUTH_MIN_PASSWORD_LENGTH });
    } catch (error) {
      if (error instanceof PasswordPolicyError) throw new AuthError(400, error.message);
      throw error;
    }
    rec.password_hash = hashPassword(secret);
  }
  try {
    const saved = await createTenant(rec);
    return publicTenant(saved);
  } catch (error) {
    if (error instanceof TenantConflictError) throw new AuthError(error.status, error.detail);
    throw error;
  }
}

export { getTenantByKey, publicTenant };
