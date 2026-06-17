import { api } from "./client";

export interface RuntimeConfig {
  pi_api_key?: string;
  pi_base_url?: string;
  pi_model?: string;
  insecure_tls?: boolean;
  runtime_credentials?: Record<string, { token: string }>;
}

export interface RuntimeStatus {
  pi_key_set: boolean;
  pi_base_url: string;
  pi_model: string;
  insecure_tls: boolean;
  runtime_credential_keys: string[];
}

const LS = "dano.runtimeConfig";

export function loadSaved(): RuntimeConfig {
  try {
    return JSON.parse(localStorage.getItem(LS) || "{}");
  } catch {
    return {};
  }
}

export function saveLocal(cfg: RuntimeConfig) {
  localStorage.setItem(LS, JSON.stringify(cfg));
}

export async function getRuntime(): Promise<RuntimeStatus> {
  const { data } = await api.get("/settings/runtime");
  return data;
}

export async function applyRuntime(cfg: RuntimeConfig): Promise<RuntimeStatus> {
  const { data } = await api.post("/settings/runtime", cfg);
  return data;
}

// 应用启动时把本地保存的配置重新推给后端(后端重启后内存会丢,这里自动补回)
export async function reapplyIfSaved() {
  const cfg = loadSaved();
  if (cfg.pi_api_key || cfg.runtime_credentials) {
    try {
      await applyRuntime(cfg);
    } catch {
      /* 后端未起/忽略 */
    }
  }
}
