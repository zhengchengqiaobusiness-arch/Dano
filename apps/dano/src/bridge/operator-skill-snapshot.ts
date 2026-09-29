import { constants } from "node:fs";
import { chmod, lstat, mkdir, mkdtemp, open, readdir, realpath, rm, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";

/** Publish only operator Skill trees as installation resources. Global agent
 * credentials stay private; tool workers receive read-only snapshot paths. */
export async function snapshotOperatorSkills(installation: string, sources: readonly string[], hostUid: number) {
  const directory = await mkdtemp(join(installation, ".operator-skills-"));
  const paths: string[] = [];
  const copy = async (source: string, target: string): Promise<void> => {
    const stat = await lstat(source);
    if (![0, hostUid].includes(stat.uid) || (stat.mode & 0o022)
      || (!stat.isDirectory() && !stat.isFile())) throw new Error("UNSAFE_OPERATOR_SKILL");
    if (stat.isDirectory()) {
      await mkdir(target, { mode: 0o700 });
      for (const name of await readdir(source)) await copy(join(source, name), join(target, name));
      await chmod(target, 0o755);
    } else {
      const handle = await open(source, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
      try {
        const current = await handle.stat();
        if (!current.isFile() || current.ino !== stat.ino || current.dev !== stat.dev || current.nlink !== 1) {
          throw new Error("UNSAFE_OPERATOR_SKILL");
        }
        await writeFile(target, await handle.readFile(), { flag: "wx", mode: (stat.mode & 0o111) ? 0o555 : 0o444 });
      } finally { await handle.close(); }
    }
  };
  try {
    for (const source of [...new Set(sources.map(path => resolve(path)))]) {
      try { await lstat(source); }
      catch (error) { if ((error as NodeJS.ErrnoException).code === "ENOENT") continue; throw error; }
      if (await realpath(source) !== source || !(await lstat(source)).isDirectory()) throw new Error("UNSAFE_OPERATOR_SKILL");
      const target = join(directory, String(paths.length));
      await copy(source, target);
      paths.push(target);
    }
    await chmod(directory, 0o755);
    return { paths, cleanup: () => rm(directory, { recursive: true, force: true }) };
  } catch (error) { await rm(directory, { recursive: true, force: true }); throw error; }
}
