import { createCipheriv, createDecipheriv, createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import * as fs from "node:fs/promises";
import * as path from "node:path";
import { writeFile as writeFileAtomically } from "atomically";
import { ensureSafeDirectory } from "./safe-directory.js";
import type { MemoryOwner } from "./memory-owner-registry.js";

/** USER credentials only; keep this store and its encryption key in the trusted host. */
export class MemoryCredentialStore {
  readonly #directory: string;
  readonly #key: Buffer;
  readonly #keyVersion: string;

  constructor(options: { directory: string; encryptionKey: Uint8Array; keyVersion: string }) {
    if (options.encryptionKey.byteLength !== 32 || !options.keyVersion) throw new Error("MEMORY_ENCRYPTION_CONFIG_INVALID");
    this.#directory = path.resolve(options.directory);
    this.#key = Buffer.from(options.encryptionKey);
    this.#keyVersion = options.keyVersion;
  }

  async read(owner: MemoryOwner): Promise<string | undefined> {
    const binding = this.#binding(owner);
    await this.#ensureDirectory();
    let handle;
    try {
      handle = await fs.open(this.#file(binding), constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined;
      throw new Error("MEMORY_CREDENTIAL_UNAVAILABLE");
    }
    try {
      const stat = await handle.stat();
      if (!stat.isFile() || stat.uid !== process.getuid?.() || (stat.mode & 0o077)
        || stat.size > 65536) throw new Error();
      const value = JSON.parse(await handle.readFile("utf8"));
      if (value.version !== 1 || value.algorithm !== "aes-256-gcm" || value.keyVersion !== this.#keyVersion
        || value.owner?.accountId !== owner.accountId || value.owner?.userId !== owner.userId) throw new Error();
      const decipher = createDecipheriv("aes-256-gcm", this.#key, decode(value.iv, 12));
      decipher.setAAD(Buffer.from(binding));
      decipher.setAuthTag(decode(value.tag, 16));
      const key = Buffer.concat([decipher.update(decode(value.ciphertext)), decipher.final()]).toString("utf8");
      validateKey(key);
      return key;
    } catch {
      throw new Error("MEMORY_CREDENTIAL_UNAVAILABLE");
    } finally { await handle.close(); }
  }

  /** Call only after authenticated /health proves this key has this USER owner. */
  async write(owner: MemoryOwner, apiKey: string): Promise<void> {
    validateKey(apiKey);
    const binding = this.#binding(owner);
    // A malformed or foreign existing file is an operational error, not a
    // reason to replace identity state silently.
    await this.read(owner);
    const iv = randomBytes(12);
    const cipher = createCipheriv("aes-256-gcm", this.#key, iv);
    cipher.setAAD(Buffer.from(binding));
    const ciphertext = Buffer.concat([cipher.update(apiKey, "utf8"), cipher.final()]);
    const record = { version: 1, algorithm: "aes-256-gcm", keyVersion: this.#keyVersion,
      owner: { accountId: owner.accountId, userId: owner.userId },
      iv: iv.toString("base64url"), tag: cipher.getAuthTag().toString("base64url"),
      ciphertext: ciphertext.toString("base64url") };
    await writeFileAtomically(this.#file(binding), JSON.stringify(record) + "\n", { mode: 0o600, fsync: true });
    const directory = await fs.open(this.#directory, constants.O_RDONLY);
    try { await directory.sync(); } finally { await directory.close(); }
  }

  #binding(owner: MemoryOwner): string {
    if (![owner.accountId, owner.userId].every(id => /^[A-Za-z0-9_-]{1,128}$/.test(id))) {
      throw new Error("MEMORY_OWNER_INVALID");
    }
    return JSON.stringify(["dano-memory-credential", 1, this.#keyVersion, owner.accountId, owner.userId]);
  }

  #file(binding: string): string {
    // Key version participates in authentication, not the filename: changing
    // encryption configuration must not make an existing record look absent.
    const [, , , accountId, userId] = JSON.parse(binding);
    const name = createHash("sha256").update(JSON.stringify([accountId, userId])).digest("hex");
    return path.join(this.#directory, `${name}.json`);
  }

  async #ensureDirectory(): Promise<void> {
    await ensureSafeDirectory(this.#directory, { recursive: true,
      unsafeDirectoryError: () => new Error("MEMORY_CREDENTIAL_DIRECTORY_UNSAFE") });
    const stat = await fs.lstat(this.#directory);
    if (stat.uid !== process.getuid?.() || (stat.mode & 0o077)) throw new Error("MEMORY_CREDENTIAL_DIRECTORY_UNSAFE");
  }
}

function validateKey(key: string): void {
  if (typeof key !== "string" || !key || Buffer.byteLength(key) > 16384 || /[\s\x00-\x1f\x7f]/.test(key)) {
    throw new Error("MEMORY_USER_KEY_INVALID");
  }
}

function decode(value: unknown, length?: number): Buffer {
  if (typeof value !== "string" || !/^[A-Za-z0-9_-]+$/.test(value)) throw new Error();
  const buffer = Buffer.from(value, "base64url");
  if (buffer.toString("base64url") !== value || (length !== undefined && buffer.length !== length)) throw new Error();
  return buffer;
}
