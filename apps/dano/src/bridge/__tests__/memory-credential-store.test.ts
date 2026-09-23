import { randomBytes } from "node:crypto";
import { chmod, copyFile, mkdtemp, readdir, readFile, rm, stat, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { MemoryCredentialStore } from "../memory-credential-store.js";

const roots: string[] = [];
const alice = { accountId: "company", userId: "alice" };
const bob = { accountId: "company", userId: "bob" };
afterEach(async () => { await Promise.all(roots.splice(0).map(root => rm(root, { recursive: true, force: true }))); });
async function setup() {
  const root = await mkdtemp(join(tmpdir(), "dano-memory-credentials-"));
  roots.push(root);
  const options = { directory: join(root, "credentials"), encryptionKey: randomBytes(32), keyVersion: "test-v1" };
  return { root, options, store: new MemoryCredentialStore(options) };
}

describe("memory credential store", () => {
  it("persists encrypted private credentials and recovers them after reopening", async () => {
    const { options, store } = await setup();
    expect(await store.read(alice)).toBeUndefined();
    await store.write(alice, "synthetic-user-secret");
    const file = join(options.directory, (await readdir(options.directory))[0]!);
    const serialized = await readFile(file, "utf8");
    expect(serialized).not.toContain("synthetic-user-secret");
    expect((await stat(file)).mode & 0o777).toBe(0o600);
    expect((await stat(options.directory)).mode & 0o777).toBe(0o700);
    expect(await new MemoryCredentialStore(options).read(alice)).toBe("synthetic-user-secret");
    await store.write(alice, "replacement-user-secret");
    expect(await new MemoryCredentialStore(options).read(alice)).toBe("replacement-user-secret");
  });
  it("removes only the validated owner credential and tolerates cleanup retry", async () => {
    const { options, store } = await setup();
    await store.write(alice, "alice-secret");
    await store.write(bob, "bob-secret");
    await store.remove(alice);
    await store.remove(alice);
    expect(await store.read(alice)).toBeUndefined();
    expect(await store.read(bob)).toBe("bob-secret");
    expect(await readdir(options.directory)).toHaveLength(1);
  });
  it("authenticates owner metadata, so moving and relabeling ciphertext cannot cross users", async () => {
    const { options, store } = await setup();
    await store.write(alice, "alice-secret");
    await store.write(bob, "bob-secret");
    const files = await readdir(options.directory);
    const records = await Promise.all(files.map(async name => ({ file: join(options.directory, name),
      value: JSON.parse(await readFile(join(options.directory, name), "utf8")) })));
    const a = records.find(record => record.value.owner.userId === "alice")!;
    const b = records.find(record => record.value.owner.userId === "bob")!;
    await copyFile(a.file, b.file);
    await expect(store.read(bob)).rejects.toThrow("MEMORY_CREDENTIAL_UNAVAILABLE");
    await writeFile(b.file, JSON.stringify({ ...a.value, owner: bob }));
    await expect(store.read(bob)).rejects.toThrow("MEMORY_CREDENTIAL_UNAVAILABLE");
    expect(await store.read(alice)).toBe("alice-secret");
  });
  it("rejects wrong encryption keys and versions without treating records as absent", async () => {
    const { options, store } = await setup();
    await store.write(alice, "alice-secret");
    await expect(new MemoryCredentialStore({ ...options, encryptionKey: randomBytes(32) }).read(alice))
      .rejects.toThrow("MEMORY_CREDENTIAL_UNAVAILABLE");
    const changed = new MemoryCredentialStore({ ...options, keyVersion: "test-v2" });
    await expect(changed.read(alice)).rejects.toThrow("MEMORY_CREDENTIAL_UNAVAILABLE");
    await expect(changed.write(alice, "replacement")).rejects.toThrow("MEMORY_CREDENTIAL_UNAVAILABLE");
    expect(await store.read(alice)).toBe("alice-secret");
  });
  it("rejects tampering and preserves corrupt evidence rather than overwriting it", async () => {
    const { options, store } = await setup();
    await store.write(alice, "alice-secret");
    const file = join(options.directory, (await readdir(options.directory))[0]!);
    const value = JSON.parse(await readFile(file, "utf8"));
    value.tag = randomBytes(16).toString("base64url");
    const corrupted = JSON.stringify(value);
    await writeFile(file, corrupted);
    await expect(store.read(alice)).rejects.toThrow("MEMORY_CREDENTIAL_UNAVAILABLE");
    await expect(store.write(alice, "replacement")).rejects.toThrow("MEMORY_CREDENTIAL_UNAVAILABLE");
    expect(await readFile(file, "utf8")).toBe(corrupted);
  });
  it("rejects symlinks and exposed permissions", async () => {
    const { options, root, store } = await setup();
    await store.write(alice, "alice-secret");
    const file = join(options.directory, (await readdir(options.directory))[0]!);
    await chmod(file, 0o644);
    await expect(store.read(alice)).rejects.toThrow("MEMORY_CREDENTIAL_UNAVAILABLE");
    await rm(file);
    const target = join(root, "target");
    await writeFile(target, "untouched", { mode: 0o600 });
    await symlink(target, file);
    await expect(store.write(alice, "replacement")).rejects.toThrow("MEMORY_CREDENTIAL_UNAVAILABLE");
    expect(await readFile(target, "utf8")).toBe("untouched");
    await chmod(options.directory, 0o755);
    await expect(store.read(alice)).rejects.toThrow("MEMORY_CREDENTIAL_DIRECTORY_UNSAFE");
  });
});
