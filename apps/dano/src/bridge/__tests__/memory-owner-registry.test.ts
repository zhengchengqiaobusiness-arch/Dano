import { mkdtemp, readdir, readFile, writeFile, chmod, symlink, rm, stat } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { afterEach, describe, expect, it } from "vitest";
import { MemoryOwnerRegistry } from "../memory-owner-registry.js";

const roots: string[] = [];
afterEach(async () => { await Promise.all(roots.splice(0).map(root => rm(root, { recursive: true, force: true }))); });
async function setup(accountId = "company") {
  const root = await mkdtemp(join(tmpdir(), "dano-memory-owner-")); roots.push(root);
  const directory = join(root, "owners");
  return { root, directory, registry: new MemoryOwnerRegistry({ directory, accountId }) };
}
const user = (id: string, username = "display") => ({ user: { id, username }, folderPath: "/unused-user-workspace" });

describe("memory owner registry", () => {
  it("persists stable, traceable bindings independently of display names and process instances", async () => {
    const { registry, directory } = await setup();
    const first = await registry.get(user("oa.user-1"));
    const reopened = new MemoryOwnerRegistry({ directory, accountId: "company" });
    expect(await reopened.get(user("oa.user-1", "renamed"))).toEqual(first);
    expect(first.userId).toMatch(/^u_[a-f0-9]{64}$/);
    expect(Object.isFrozen(first)).toBe(true);
    const files = await readdir(directory);
    expect(files).toHaveLength(1);
    expect((await stat(directory)).mode & 0o777).toBe(0o700);
    expect((await stat(join(directory, files[0]!))).mode & 0o777).toBe(0o600);
    expect(JSON.parse(await readFile(join(directory, files[0]!), "utf8"))).toEqual({ version: 1, danoUserId: "oa.user-1", owner: first });
  });
  it("removes only the selected owner binding and can repeat local cleanup", async () => {
    const { registry, directory } = await setup();
    await registry.get(user("alice"));
    const bob = await registry.get(user("bob"));
    await registry.remove(user("alice"));
    await registry.remove(user("alice"));
    expect(await readdir(directory)).toHaveLength(1);
    expect(await registry.get(user("bob"))).toEqual(bob);
  });
  it("separates IDs that lossy normalization would collapse and separates deployment accounts", async () => {
    const { registry } = await setup();
    const owners = await Promise.all(["a.b", "a_b", "ab", "a-b"].map(id => registry.get(user(id))));
    expect(new Set(owners.map(owner => owner.userId)).size).toBe(4);
    const other = await setup("other-company");
    expect((await other.registry.get(user("a.b"))).userId).not.toBe(owners[0]!.userId);
  });
  it("rejects anonymous callers before creating any identity state", async () => {
    const { registry, directory } = await setup();
    await expect(registry.get({ user: { id: "anonymous-1" }, folderPath: "/unused" })).rejects.toThrow("请先登录");
    await expect(readdir(directory)).rejects.toMatchObject({ code: "ENOENT" });
  });
  it("does not overwrite a corrupt or foreign mapping and rejects symlink records", async () => {
    const { registry, directory, root } = await setup();
    await registry.get(user("alice"));
    const file = join(directory, (await readdir(directory))[0]!);
    await writeFile(file, '{"version":1,"danoUserId":"bob"}', { mode: 0o600 });
    await expect(registry.get(user("alice"))).rejects.toThrow("不匹配");
    expect(await readFile(file, "utf8")).toContain('"bob"');
    await rm(file);
    await writeFile(join(root, "target"), "untouched", { mode: 0o600 });
    await symlink(join(root, "target"), file);
    await expect(registry.get(user("alice"))).rejects.toThrow("不可用");
    expect(await readFile(join(root, "target"), "utf8")).toBe("untouched");
  });
  it("refuses shared identity directories and supports concurrent same-user initialization", async () => {
    const { registry, directory } = await setup();
    const bindings = await Promise.all(Array.from({ length: 8 }, () => registry.get(user("alice"))));
    expect(bindings.every(binding => binding.userId === bindings[0]!.userId)).toBe(true);
    await chmod(directory, 0o755);
    await expect(registry.get(user("alice"))).rejects.toThrow("独占访问");
  });
  it("rejects exposed records without silently repairing their permissions", async () => {
    const { registry, directory } = await setup();
    await registry.get(user("alice"));
    const file = join(directory, (await readdir(directory))[0]!);
    await chmod(file, 0o644);
    await expect(registry.get(user("alice"))).rejects.toThrow("不可用");
    expect((await stat(file)).mode & 0o777).toBe(0o644);
  });
});
