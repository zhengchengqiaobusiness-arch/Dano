import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";
import { randomBytes } from "node:crypto";
import { dataRoot } from "../paths.mjs";
import { CLEARED } from "./throttle.mjs";

function storeFile() {
  return path.join(dataRoot(), "tenants.json");
}

function emptyStore() {
  return { version: 1, tenants: [], throttle: {} };
}

let queue = Promise.resolve();

function withStore(run) {
  const next = queue.then(run, run);
  queue = next.then(() => undefined, () => undefined);
  return next;
}

export function newApiKey() {
  return `dk_${randomBytes(16).toString("hex")}`;
}

export function publicTenant(rec) {
  if (!rec) return null;
  return {
    tenant: rec.tenant,
    display_name: rec.display_name || "",
    username: rec.username || "",
    api_key: rec.api_key,
  };
}

async function loadStore() {
  try {
    const raw = JSON.parse(await readFile(storeFile(), "utf8"));
    return {
      version: 1,
      tenants: Array.isArray(raw?.tenants) ? raw.tenants : [],
      throttle: raw?.throttle && typeof raw.throttle === "object" ? raw.throttle : {},
    };
  } catch (error) {
    if (error?.code === "ENOENT") return emptyStore();
    throw error;
  }
}

async function saveStore(store) {
  const file = storeFile();
  await mkdir(path.dirname(file), { recursive: true });
  const tmp = `${file}.${process.pid}.tmp`;
  await writeFile(tmp, `${JSON.stringify(store, null, 2)}\n`, "utf8");
  await rename(tmp, file);
}

export class TenantConflictError extends Error {
  constructor(detail) {
    super(detail);
    this.name = "TenantConflictError";
    this.status = 409;
    this.detail = detail;
  }
}

export async function createTenant(rec) {
  return withStore(async () => {
    const store = await loadStore();
    const existing = store.tenants.find((row) => row.tenant === rec.tenant);
    if (existing) return existing;
    const username = String(rec.username || "").trim();
    if (username && store.tenants.some((row) => row.username === username)) {
      throw new TenantConflictError("用户名已被占用");
    }
    const next = {
      tenant: rec.tenant,
      display_name: rec.display_name || "",
      username: username,
      password_hash: rec.password_hash || "",
      api_key: rec.api_key || newApiKey(),
      created_at: new Date().toISOString(),
    };
    store.tenants.push(next);
    await saveStore(store);
    return next;
  });
}

export async function getTenantByKey(apiKey) {
  const key = String(apiKey || "");
  if (!key) return null;
  const store = await loadStore();
  return store.tenants.find((row) => row.api_key === key) || null;
}

export async function getTenantByUsername(username) {
  const name = String(username || "");
  if (!name) return null;
  const store = await loadStore();
  return store.tenants.find((row) => row.username === name) || null;
}

export async function updateTenantPassword(tenant, passwordHash) {
  return withStore(async () => {
    const store = await loadStore();
    const rec = store.tenants.find((row) => row.tenant === tenant);
    if (!rec) return null;
    rec.password_hash = passwordHash;
    await saveStore(store);
    return rec;
  });
}

export async function getThrottle(username) {
  const store = await loadStore();
  return store.throttle[String(username || "")] || { ...CLEARED };
}

export async function setThrottle(username, state) {
  return withStore(async () => {
    const store = await loadStore();
    store.throttle[String(username || "")] = {
      fail_count: Number(state?.fail_count || 0),
      locked_until: Number(state?.locked_until || 0),
    };
    await saveStore(store);
  });
}
