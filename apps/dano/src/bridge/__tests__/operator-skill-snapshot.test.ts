import { afterEach, expect, it } from "vitest";
import { lstat, mkdir, mkdtemp, readFile, realpath, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { snapshotOperatorSkills } from "../operator-skill-snapshot.js";
const roots: string[] = [];
afterEach(async () => { for (const root of roots.splice(0)) await rm(root, { recursive: true, force: true }); });
async function fixture() {
  const root = await realpath(await mkdtemp(join(tmpdir(), "operator-skills-"))); roots.push(root);
  await mkdir(join(root, "installation"));
  await mkdir(join(root, "agent/skills/custom"), { recursive: true });
  return root;
}
it("retains operator skills and executable resources without publishing neighboring agent credentials", async () => {
  const root = await fixture();
  await writeFile(join(root, "agent/auth.json"), "private");
  await writeFile(join(root, "agent/skills/custom/SKILL.md"), "operator content");
  await writeFile(join(root, "agent/skills/custom/run.sh"), "echo okay", { mode: 0o755 });
  const snapshot = await snapshotOperatorSkills(join(root, "installation"), [join(root, "agent/skills"), join(root, "missing")], process.getuid!());
  expect(snapshot.paths).toHaveLength(1);
  expect(await readFile(join(snapshot.paths[0]!, "custom/SKILL.md"), "utf8")).toBe("operator content");
  expect((await lstat(join(snapshot.paths[0]!, "custom/run.sh"))).mode & 0o777).toBe(0o555);
  await expect(lstat(join(snapshot.paths[0]!, "auth.json"))).rejects.toMatchObject({ code: "ENOENT" });
  await snapshot.cleanup();
});
it("rejects links that could publish private files through a skill", async () => {
  const root = await fixture();
  await writeFile(join(root, "agent/auth.json"), "private");
  await symlink(join(root, "agent/auth.json"), join(root, "agent/skills/custom/secret"));
  await expect(snapshotOperatorSkills(join(root, "installation"), [join(root, "agent/skills")], process.getuid!())).rejects.toThrow("UNSAFE_OPERATOR_SKILL");
});
