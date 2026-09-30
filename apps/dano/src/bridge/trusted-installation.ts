import { lstat, readdir, realpath } from "node:fs/promises";
import { dirname, isAbsolute, join, relative } from "node:path";

/** Every search directory and ancestor must be immutable to model tools. */
export async function rootSearchPath(path: string): Promise<string> {
  const directories: string[] = [];
  for (const entry of path.split(":")) {
    if (!isAbsolute(entry)) throw new Error("WORKER_BROKER_INSTALLATION_REQUIRED");
    const canonical = await realpath(entry);
    let current = canonical;
    for (;;) {
      const metadata = await lstat(current);
      if (!metadata.isDirectory() || metadata.uid !== 0 || (metadata.mode & 0o022)) {
        throw new Error("WORKER_BROKER_INSTALLATION_REQUIRED");
      }
      const parent = dirname(current);
      if (parent === current) break;
      current = parent;
    }
    directories.push(canonical);
  }
  return directories.join(":");
}

/** Root code/executables must not be replaceable by the dropped host either. */
export async function rootFile(path: string): Promise<string> {
  if (!isAbsolute(path)) throw new Error("WORKER_BROKER_INSTALLATION_REQUIRED");
  const canonical = await realpath(path);
  let current = canonical;
  for (;;) {
    const metadata = await lstat(current);
    if (metadata.uid !== 0 || (metadata.mode & 0o022)
      || (current === canonical ? !metadata.isFile() : !metadata.isDirectory())) {
      throw new Error("WORKER_BROKER_INSTALLATION_REQUIRED");
    }
    const parent = dirname(current);
    if (parent === current) return canonical;
    current = parent;
  }
}

export async function rootInstallation(root: string): Promise<void> {
  const visited = new Set<string>();
  const pending = [root];
  while (pending.length) {
    const path = await realpath(pending.pop()!);
    const suffix = relative(root, path);
    if (suffix === ".." || suffix.startsWith("../") || isAbsolute(suffix)) throw new Error("WORKER_BROKER_INSTALLATION_REQUIRED");
    if (visited.has(path)) continue;
    visited.add(path);
    const metadata = await lstat(path);
    if (metadata.uid !== 0 || (metadata.mode & 0o022)) throw new Error("WORKER_BROKER_INSTALLATION_REQUIRED");
    if (metadata.isDirectory()) {
      for (const child of await readdir(path)) pending.push(join(path, child));
    } else if (!metadata.isFile()) throw new Error("WORKER_BROKER_INSTALLATION_REQUIRED");
  }
}
