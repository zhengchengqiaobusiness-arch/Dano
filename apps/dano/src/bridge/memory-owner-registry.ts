import { createHash } from "node:crypto";
import * as fs from "node:fs/promises";
import { constants } from "node:fs";
import * as path from "node:path";
import { writeFile as writeFileAtomically } from "atomically";
import type { UserContext } from "./user-context.js";
import { ensureSafeDirectory } from "./safe-directory.js";
import type { Owner } from "@josephyoung/pi-openviking/host";

export type MemoryOwner = Owner;

interface StoredMemoryOwner {
  readonly version: 1;
  readonly danoUserId: string;
  readonly owner: MemoryOwner;
}

/** Host-only identity metadata. Credentials and conversation bodies never belong here. */
export class MemoryOwnerRegistry {
  readonly #directory: string;
  readonly #accountId: string;

  constructor(options: { directory: string; accountId: string }) {
    if (!/^[A-Za-z0-9_-]{1,128}$/.test(options.accountId)) {
      throw new Error("记忆服务账户配置无效");
    }
    this.#directory = path.resolve(options.directory);
    this.#accountId = options.accountId;
  }

  async get(context: UserContext): Promise<MemoryOwner> {
    // UserContext comes exclusively from Dano's server-side identity resolver.
    if (!("username" in context.user) || !context.user.id) {
      throw new Error("请先登录后再启用长期记忆");
    }
    const digest = createHash("sha256")
      .update(JSON.stringify([this.#accountId, context.user.id]))
      .digest("hex");
    const owner = Object.freeze({ accountId: this.#accountId, userId: `u_${digest}` });
    const expected: StoredMemoryOwner = { version: 1, danoUserId: context.user.id, owner };
    await ensureSafeDirectory(this.#directory, {
      recursive: true,
      unsafeDirectoryError: () => new Error("记忆身份目录不可用"),
    });
    const directory = await fs.lstat(this.#directory);
    if (directory.uid !== process.getuid?.() || (directory.mode & 0o077) !== 0) {
      throw new Error("记忆身份目录必须由宿主独占访问");
    }
    const file = path.join(this.#directory, `${digest}.json`);
    let handle;
    try {
      handle = await fs.open(file, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
        throw new Error("记忆身份记录不可用");
      }
    }
    if (handle) {
      try {
        const metadata = await handle.stat();
        if (!metadata.isFile() || metadata.uid !== directory.uid || (metadata.mode & 0o077) !== 0) {
          throw new Error("记忆身份记录不可用");
        }
        const stored: StoredMemoryOwner = JSON.parse(await handle.readFile("utf8"));
        if (stored.version !== 1 || stored.danoUserId !== expected.danoUserId
          || stored.owner?.accountId !== owner.accountId || stored.owner?.userId !== owner.userId) {
          throw new Error("记忆身份记录不匹配");
        }
      } catch {
        throw new Error("记忆身份记录不可用或不匹配");
      } finally { await handle.close(); }
      return owner;
    }
    // Same-user initializers write identical deterministic metadata; different
    // users have different files. No timestamp, key or mutable username is stored.
    await writeFileAtomically(file, JSON.stringify(expected) + "\n", { mode: 0o600, fsync: true });
    const directoryHandle = await fs.open(this.#directory, constants.O_RDONLY);
    try { await directoryHandle.sync(); } finally { await directoryHandle.close(); }
    return owner;
  }
}
