import type { MemoryOwner } from "./memory-owner-registry.js";

/** Management-only client. Never pass this object to a model tool or extension. */
export class MemoryProvisioner {
  readonly #origin: string;
  readonly #accountId: string;
  readonly #managementKey: string;
  readonly #timeoutMs: number;

  constructor(options: { baseUrl: string; accountId: string; managementKey: string; timeoutMs: number }) {
    const url = new URL(options.baseUrl);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password
      || url.search || url.hash || url.pathname !== "/"
      || !/^[A-Za-z0-9_-]{1,128}$/.test(options.accountId) || !options.managementKey
      || !Number.isSafeInteger(options.timeoutMs) || options.timeoutMs <= 0) {
      throw new Error("MEMORY_MANAGEMENT_CONFIG_INVALID");
    }
    this.#origin = url.origin;
    this.#accountId = options.accountId;
    this.#managementKey = options.managementKey;
    this.#timeoutMs = options.timeoutMs;
  }

  /** Caller must persist the returned USER key in protected host storage. */
  async provision(owner: MemoryOwner): Promise<string> {
    if (owner.accountId !== this.#accountId || !/^[A-Za-z0-9_-]{1,128}$/.test(owner.userId)) {
      throw new Error("MEMORY_OWNER_MISMATCH");
    }
    const identity = object(await this.#request("/health", this.#managementKey));
    if (identity.auth_mode !== "api_key" || !(identity.role === "root"
      || (identity.role === "admin" && identity.account_id === owner.accountId))) {
      throw new Error("MEMORY_MANAGEMENT_IDENTITY_INVALID");
    }
    const usersPath = `/api/v1/admin/accounts/${owner.accountId}/users`;
    const lookup = async (): Promise<string | undefined> => {
      const response = object(await this.#request(`${usersPath}?name=${owner.userId}`, this.#managementKey));
      if (response.status !== "ok" || !Array.isArray(response.result)) throw new Error("MEMORY_USER_LOOKUP_INVALID");
      // The server supports wildcard names; the validated identifier contains no
      // wildcards, and exact matching remains mandatory on the response.
      const matches = response.result.map(object).filter(user => user.user_id === owner.userId);
      if (!matches.length) return undefined;
      if (matches.length !== 1 || matches[0]!.role !== "user") throw new Error("MEMORY_USER_ROLE_INVALID");
      const key = matches[0]!.api_key;
      // Hashed-key deployments expose only a prefix. Do not silently rotate an
      // existing identity or substitute the administrative credential.
      if (typeof key !== "string" || !key) throw new Error("MEMORY_USER_KEY_RECOVERY_REQUIRED");
      return key;
    };
    let key = await lookup();
    if (!key) {
      try {
        const response = object(await this.#request(usersPath, this.#managementKey,
          { user_id: owner.userId, role: "user" }));
        const result = object(response.result);
        if (response.status !== "ok" || result.account_id !== owner.accountId
          || result.user_id !== owner.userId || typeof result.user_key !== "string" || !result.user_key) {
          throw new Error("MEMORY_USER_REGISTRATION_INVALID");
        }
        key = result.user_key;
      } catch {
        // Creation may already have succeeded. A read can reconcile that case;
        // never replay the POST or rotate a key on an ambiguous result.
        key = await lookup();
        if (!key) throw new Error("MEMORY_USER_REGISTRATION_UNRESOLVED");
      }
    }
    const bound = object(await this.#request("/health", key));
    if (bound.auth_mode !== "api_key" || bound.role !== "user"
      || bound.account_id !== owner.accountId || bound.user_id !== owner.userId) {
      throw new Error("MEMORY_CREDENTIAL_OWNER_MISMATCH");
    }
    return key;
  }

  async #request(route: string, key: string, body?: object): Promise<unknown> {
    try {
      const response = await fetch(`${this.#origin}${route}`, {
        method: body ? "POST" : "GET", redirect: "error",
        signal: AbortSignal.timeout(this.#timeoutMs),
        headers: { "X-API-Key": key, ...(body ? { "Content-Type": "application/json" } : {}) },
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!response.ok) throw new Error();
      return await response.json();
    } catch {
      // Upstream bodies and network errors can contain secrets or internal URLs.
      throw new Error("MEMORY_MANAGEMENT_REQUEST_FAILED");
    }
  }
}

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("MEMORY_MANAGEMENT_RESPONSE_INVALID");
  return value as Record<string, unknown>;
}
