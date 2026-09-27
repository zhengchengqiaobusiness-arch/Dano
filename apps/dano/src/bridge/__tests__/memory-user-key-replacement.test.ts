import { randomBytes } from "node:crypto";
import { chmod, mkdir, mkdtemp, realpath, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, expect, it, vi } from "vitest";
import { replaceMemoryUserKey } from "../../../runtime/replace-memory-user-key.mjs";
import { MemoryCredentialStore } from "../memory-credential-store.js";

const roots: string[] = [];
afterEach(async () => {
  vi.unstubAllGlobals();
  await Promise.all(roots.splice(0).map(root => rm(root, { recursive: true, force: true })));
});

async function fixture() {
  const root = await realpath(await mkdtemp(join(tmpdir(), "dano-memory-key-replacement-")));
  roots.push(root);
  const configRoot = join(root, "config"), dataRoot = join(root, "data");
  const memoryRoot = join(configRoot, "memory");
  const credentialsRoot = join(dataRoot, "host-state", "memory-service", "credentials");
  for (const path of [configRoot, memoryRoot, dataRoot, join(dataRoot, "host-state"),
    join(dataRoot, "host-state", "memory-service"), credentialsRoot]) {
    await mkdir(path, { mode: 0o700 });
  }
  const encryptionKey = randomBytes(32);
  await writeFile(join(memoryRoot, "memory-service.json"), JSON.stringify({
    accountId: "company", baseUrl: "http://localhost:19433", encryptionKey: encryptionKey.toString("hex"),
    encryptionKeyVersion: "test-v1", requestTimeoutMs: 1000,
  }), { mode: 0o600 });
  const store = new MemoryCredentialStore({ directory: credentialsRoot, encryptionKey, keyVersion: "test-v1" });
  const owner = { accountId: "company", userId: "alice" };
  await store.write(owner, "old-user-key");
  return { configRoot, dataRoot, memoryRoot, store, owner };
}

function identity(key: string) {
  if (key === "new-user-key") return { auth_mode: "api_key", role: "user",
    account_id: "company", user_id: "alice" };
  if (key === "bob-user-key") return { auth_mode: "api_key", role: "user",
    account_id: "company", user_id: "bob" };
  return { auth_mode: "none" };
}

it("replaces only an existing credential after new owner and old revocation are verified", async () => {
  const f = await fixture();
  const fetcher = vi.fn(async (_url: string, init: RequestInit) =>
    Response.json(identity((init.headers as Record<string, string>)["X-API-Key"]!)));
  vi.stubGlobal("fetch", fetcher);
  await expect(replaceMemoryUserKey(f.configRoot, f.dataRoot, f.owner.userId, "new-user-key"))
    .resolves.toEqual({ replaced: 1 });
  expect(await f.store.read(f.owner)).toBe("new-user-key");
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it("preserves the old credential when the replacement belongs to another user", async () => {
  const f = await fixture();
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(identity("bob-user-key"))));
  await expect(replaceMemoryUserKey(f.configRoot, f.dataRoot, f.owner.userId, "bob-user-key"))
    .rejects.toThrow("MEMORY_NEW_USER_KEY_OWNER_MISMATCH");
  expect(await f.store.read(f.owner)).toBe("old-user-key");
});

it("refuses to replace an old key that still has owner access", async () => {
  const f = await fixture();
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init: RequestInit) => {
    const key = (init.headers as Record<string, string>)["X-API-Key"];
    return Response.json(key === "old-user-key"
      ? { auth_mode: "api_key", role: "user", account_id: "company", user_id: "alice" }
      : identity("new-user-key"));
  }));
  await expect(replaceMemoryUserKey(f.configRoot, f.dataRoot, f.owner.userId, "new-user-key"))
    .rejects.toThrow("MEMORY_OLD_USER_KEY_STILL_ACTIVE");
  expect(await f.store.read(f.owner)).toBe("old-user-key");
});

it("requires positive proof that the saved key lost USER access", async () => {
  const f = await fixture();
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init: RequestInit) =>
    Response.json((init.headers as Record<string, string>)["X-API-Key"] === "old-user-key"
      ? { status: "ok" } : identity("new-user-key"))));
  await expect(replaceMemoryUserKey(f.configRoot, f.dataRoot, f.owner.userId, "new-user-key"))
    .rejects.toThrow("MEMORY_OLD_USER_KEY_REVOCATION_UNPROVEN");
  expect(await f.store.read(f.owner)).toBe("old-user-key");
});

it("rejects exposed config before contacting the service or changing a credential", async () => {
  const f = await fixture();
  await chmod(join(f.memoryRoot, "memory-service.json"), 0o644);
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  await expect(replaceMemoryUserKey(f.configRoot, f.dataRoot, f.owner.userId, "new-user-key"))
    .rejects.toThrow("UNPROTECTED_RECOVERY_PATH");
  expect(fetcher).not.toHaveBeenCalled();
  expect(await f.store.read(f.owner)).toBe("old-user-key");
});
