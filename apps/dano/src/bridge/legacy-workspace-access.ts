import { constants } from "node:fs";
import { lchown, lstat, open, readdir } from "node:fs/promises";
import { join } from "node:path";

/** Transfer only a user's ordinary workspace access to its isolated group.
 * Session records are elsewhere. Never follow links or touch host-managed
 * configuration and upload storage; HTTP retains access to browser uploads.
 * Already converted entries make interrupted adoption safe to resume.
 */
export async function adoptLegacyWorkspaceAccess(workspace: string, hostUid: number, hostGid: number, workerUid: number, workerGid: number): Promise<void> {
  const pending = (await readdir(workspace)).filter(name => name !== ".pi" && name !== "uploads").map(name => join(workspace, name));
  while (pending.length) {
    const path = pending.pop()!;
    const metadata = await lstat(path);
    if (metadata.isSymbolicLink()) {
      if (![hostUid, workerUid].includes(metadata.uid) || ![hostGid, workerGid].includes(metadata.gid)
        || metadata.nlink !== 1) throw new Error("UNSAFE_LEGACY_WORKSPACE_ENTRY");
      // Transfer the link inode so sticky-directory rename/unlink works,
      // without following it or changing its target's ownership or access.
      await lchown(path, workerUid, workerGid);
      continue;
    }
    if (!metadata.isDirectory() && !metadata.isFile()) throw new Error("UNSAFE_LEGACY_WORKSPACE_ENTRY");
    const handle = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    try {
      const stat = await handle.stat();
      if (![hostUid, workerUid].includes(stat.uid) || ![hostGid, workerGid].includes(stat.gid)
        || (!stat.isDirectory() && stat.nlink !== 1)) throw new Error("UNSAFE_LEGACY_WORKSPACE_ENTRY");
      // Give the user ownership so rename/unlink still work under the sticky
      // workspace root; preserve access without making content public.
      await handle.chown(workerUid, workerGid);
      await handle.chmod((stat.mode & 0o707) | ((stat.mode & 0o700) >> 3) | (stat.isDirectory() ? 0o2000 : 0));
      if (stat.isDirectory()) for (const child of await readdir(path)) pending.push(join(path, child));
    } finally { await handle.close(); }
  }
}
