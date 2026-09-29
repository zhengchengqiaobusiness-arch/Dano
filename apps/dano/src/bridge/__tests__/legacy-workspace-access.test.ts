import { afterEach, expect, it } from "vitest";
import { chmod, link, lstat, mkdir, mkdtemp, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { adoptLegacyWorkspaceAccess } from "../legacy-workspace-access.js";

const roots: string[] = [];
afterEach(async () => { for (const root of roots.splice(0)) await rm(root, { recursive: true, force: true }); });
async function fixture() {
  const root = await mkdtemp(join(tmpdir(), "legacy-access-")); roots.push(root);
  return root;
}
it("preserves content, grants private group access, and leaves agent secrets and symlink targets alone", async () => {
  const root = await fixture();
  const workspace = join(root, "workspace");
  await mkdir(join(workspace, "nested"), { recursive: true, mode: 0o700 });
  await chmod(join(workspace, "nested"), 0o700);
  await writeFile(join(workspace, "nested/file"), "original bytes", { mode: 0o600 });
  await mkdir(join(workspace, ".pi"));
  await writeFile(join(workspace, ".pi/auth.json"), "private", { mode: 0o600 });
  await writeFile(join(root, "outside"), "outside", { mode: 0o600 });
  await symlink(join(root, "outside"), join(workspace, "link"));
  await adoptLegacyWorkspaceAccess(workspace, process.getuid!(), process.getgid!(), process.getuid!(), process.getgid!());
  expect(await readFile(join(workspace, "nested/file"), "utf8")).toBe("original bytes");
  expect((await lstat(join(workspace, "nested/file"))).mode & 0o7777).toBe(0o660);
  expect((await lstat(join(workspace, "nested"))).mode & 0o7777).toBe(0o2770);
  expect((await lstat(join(workspace, ".pi/auth.json"))).mode & 0o777).toBe(0o600);
  expect((await lstat(join(root, "outside"))).mode & 0o777).toBe(0o600);
});
it("refuses hard links so adoption cannot change access outside the workspace", async () => {
  const root = await fixture();
  await writeFile(join(root, "file"), "original", { mode: 0o600 });
  await link(join(root, "file"), join(root, "alias"));
  await expect(adoptLegacyWorkspaceAccess(root, process.getuid!(), process.getgid!(), process.getuid!(), process.getgid!())).rejects.toThrow("UNSAFE_LEGACY_WORKSPACE_ENTRY");
  expect((await lstat(join(root, "file"))).mode & 0o777).toBe(0o600);
});
