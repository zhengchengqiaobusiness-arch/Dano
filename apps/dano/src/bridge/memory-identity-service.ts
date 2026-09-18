import type { MemoryCredentialStore } from "./memory-credential-store.js";
import type { MemoryOwner, MemoryOwnerRegistry } from "./memory-owner-registry.js";
import type { MemoryProvisioner } from "./memory-provisioner.js";
import type { UserContext } from "./user-context.js";

export interface MemoryUserConnection {
  readonly owner: MemoryOwner;
  readonly apiKey: string;
}

interface MemoryIdentityServiceOptions {
  owners: Pick<MemoryOwnerRegistry, "get">;
  credentials: Pick<MemoryCredentialStore, "read" | "write">;
  provisioner: Pick<MemoryProvisioner, "provision" | "verifyUserKey">;
  assertToolIsolation(): Promise<void>;
}

/** Host-only startup seam. Never serialize its result into browser state. */
export class MemoryIdentityService {
  readonly #options: MemoryIdentityServiceOptions;
  readonly #pending = new Map<string, Promise<MemoryUserConnection>>();

  constructor(options: MemoryIdentityServiceOptions) {
    this.#options = options;
  }

  async connect(context: UserContext): Promise<MemoryUserConnection> {
    if (!("username" in context.user) || !context.user.id) throw new Error("MEMORY_LOGIN_REQUIRED");
    // Check each caller, including callers joining an in-progress initialization.
    await this.#options.assertToolIsolation();
    const existing = this.#pending.get(context.user.id);
    if (existing) return existing;
    const connecting = this.#connect(context);
    this.#pending.set(context.user.id, connecting);
    try { return await connecting; }
    finally { if (this.#pending.get(context.user.id) === connecting) this.#pending.delete(context.user.id); }
  }

  async #connect(context: UserContext): Promise<MemoryUserConnection> {
    const owner = await this.#options.owners.get(context);
    let apiKey = await this.#options.credentials.read(owner);
    if (apiKey !== undefined) {
      // Restart does not need management access while the saved USER key is
      // valid. A rejected saved key does not trigger an implicit rotation.
      await this.#options.provisioner.verifyUserKey(owner, apiKey);
    } else {
      apiKey = await this.#options.provisioner.provision(owner);
      await this.#options.credentials.write(owner, apiKey);
    }
    await this.#options.assertToolIsolation();
    return Object.freeze({ owner, apiKey });
  }
}
