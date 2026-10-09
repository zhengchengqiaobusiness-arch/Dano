#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { constants, closeSync, fchmodSync, fstatSync, lstatSync, mkdtempSync, openSync,
  readFileSync, realpathSync, rmSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const unsafe = () => new Error("UNSAFE_RELEASE_BUILD_CONTEXT");
function git(directory, args) {
  const result = spawnSync("git", args, { cwd: directory, encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
  if (result.status !== 0 || result.error) throw unsafe();
  return result.stdout;
}
function manifest(source, expectedSha) {
  const sha = git(source, ["rev-parse", "HEAD"]).trim();
  if (!/^[a-f0-9]{40}$/.test(sha) || (expectedSha && sha !== expectedSha)
    || git(source, ["status", "--porcelain", "--untracked-files=all", "--ignored"]).trim()) throw unsafe();
  const entries = git(source, ["ls-tree", "-rz", "--full-tree", sha]).split("\0").filter(Boolean).map(record => {
    const match = /^(100644|100755) blob ([a-f0-9]{40})\t([\s\S]+)$/.exec(record);
    if (!match) throw unsafe(); // No submodules, links or special files in an installation context.
    return { mode: match[1] === "100755" ? 0o755 : 0o644, blob: match[2], name: match[3] };
  });
  if (!entries.length) throw unsafe();
  return { sha, entries };
}
function normalize(directory, { sha, entries }) {
  const root = realpathSync(directory), directories = new Set([root]);
  for (const entry of entries) {
    const path = resolve(root, entry.name), suffix = relative(root, path);
    if (!suffix || suffix === ".." || suffix.startsWith("../") || suffix.startsWith("/")) throw unsafe();
    for (let parent = dirname(path); parent !== root; parent = dirname(parent)) directories.add(parent);
  }
  const update = (path, mode, isDirectory, blob) => {
    if (realpathSync(dirname(path)) !== dirname(path)) throw unsafe();
    const fd = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK
      | (isDirectory ? constants.O_DIRECTORY : 0));
    try {
      const before = fstatSync(fd);
      if ((isDirectory ? !before.isDirectory() : !before.isFile() || before.nlink !== 1)
        || before.uid !== process.getuid()) throw unsafe();
      if (blob) {
        const data = readFileSync(fd);
        const actual = createHash("sha1").update(`blob ${data.length}\0`).update(data).digest("hex");
        if (actual !== blob) throw unsafe();
      }
      fchmodSync(fd, mode);
      const after = lstatSync(path);
      if (after.ino !== before.ino || after.dev !== before.dev) throw unsafe();
    } finally { closeSync(fd); }
  };
  for (const path of [...directories].sort((a, b) => a.length - b.length)) update(path, 0o755, true);
  for (const entry of entries) update(join(root, entry.name), entry.mode, false, entry.blob);
  return { targetSha: sha, files: entries.length, directory: root };
}

/** Only a disposable, clean checkout; never pass a runtime or deploy-control directory. */
export function prepareBuildContext(directory, expectedSha) {
  return normalize(directory, manifest(directory, expectedSha));
}

/** Archive fallback uses Git's tree, not permissions preserved by a root tar extraction. */
export function archiveBuildContext(source, parent, expectedSha) {
  const tree = manifest(source, expectedSha);
  const directory = mkdtempSync(join(parent, "dano-build-archive-"));
  const mask = process.umask(0o022);
  try {
    const archive = spawnSync("git", ["archive", "--format=tar", tree.sha], {
      cwd: source, maxBuffer: 64 * 1024 * 1024,
    });
    if (archive.status !== 0 || archive.error) throw unsafe();
    const extracted = spawnSync("tar", ["-x", "--no-same-owner", "--no-same-permissions", "-C", directory], {
      input: archive.stdout, stdio: ["pipe", "pipe", "pipe"],
    });
    if (extracted.status !== 0 || extracted.error) throw unsafe();
    return normalize(directory, tree);
  } catch {
    rmSync(directory, { recursive: true, force: true });
    throw unsafe();
  } finally { process.umask(mask); }
}

if (process.argv[1] && realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const [mode, source, destinationOrSha, sha, ...extra] = process.argv.slice(2);
    if (!source || extra.length || !["prepare", "archive"].includes(mode)
      || (mode === "prepare" && sha) || (mode === "archive" && !destinationOrSha)) throw unsafe();
    console.log(JSON.stringify(mode === "prepare"
      ? prepareBuildContext(source, destinationOrSha)
      : archiveBuildContext(source, destinationOrSha, sha)));
  } catch {
    console.error("UNSAFE_RELEASE_BUILD_CONTEXT: use a clean disposable Git checkout at the expected commit; no runtime directories.");
    process.exitCode = 1;
  }
}
