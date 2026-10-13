import axios from "axios";
import { adminBasename, adminHref } from "../adminBase";

// 网关用相对路径(dev 由 vite proxy 转发到 :8000)。X-Tenant-Key 从 localStorage 注入。
// 挂在路径前缀下时，请求必须带上和页面相同的前缀，否则会打到站点根上的其他服务。
export const TENANT_KEY = "dano.tenantKey";
export const TENANT_NAME = "dano.tenantName";

export const api = axios.create({ baseURL: adminBasename() });

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

export function isTenantAuthError(error: unknown): boolean {
  if (!error || typeof error !== "object") return false;
  const response = (error as { response?: { status?: number; data?: { detail?: unknown } } }).response;
  if (response?.status !== 401) return false;
  const detail = String(response?.data?.detail || "");
  return detail === "缺少 X-Tenant-Key" || detail === "X-Tenant-Key 无效";
}

function onTenantLoginPage(): boolean {
  return /\/tenant(?:\/|$)/.test(String(window.location.pathname || ""));
}

let tenantRedirecting = false;

api.interceptors.response.use(
  (res) => res,
  (error) => {
    if (typeof window !== "undefined" && isTenantAuthError(error) && !onTenantLoginPage()) {
      clearTenant();
      if (!tenantRedirecting) {
        tenantRedirecting = true;
        window.location.assign(adminHref("/tenant"));
      }
    }
    return Promise.reject(error);
  },
);
