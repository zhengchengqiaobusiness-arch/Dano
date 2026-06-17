import axios from "axios";

// 网关用相对路径(dev 由 vite proxy 转发到 :8000)。X-Tenant-Key 从 localStorage 注入。
export const TENANT_KEY = "dano.tenantKey";
export const TENANT_NAME = "dano.tenantName";

export const api = axios.create({ baseURL: "" });

api.interceptors.request.use((cfg) => {
  const key = localStorage.getItem(TENANT_KEY);
  if (key) cfg.headers["X-Tenant-Key"] = key;
  return cfg;
});

export function getTenantKey(): string | null {
  return localStorage.getItem(TENANT_KEY);
}

export function setTenant(name: string, key: string) {
  localStorage.setItem(TENANT_NAME, name);
  localStorage.setItem(TENANT_KEY, key);
}

export function clearTenant() {
  localStorage.removeItem(TENANT_NAME);
  localStorage.removeItem(TENANT_KEY);
}
