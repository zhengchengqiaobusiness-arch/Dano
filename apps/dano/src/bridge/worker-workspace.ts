import { constants } from "node:fs";
import { createHash } from "node:crypto";
import { mkdir, open, realpath, type FileHandle } from "node:fs/promises";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import type { WorkerIdentity, WorkerIdentityRegistry } from "./worker-identity-registry.js";
export { WorkerIdentityRegistry } from "./worker-identity-registry.js";

interface Options {
  usersRoot: string;
  hostStateRoot: string;
  userId: string;
  workspace: string;
  hostUid: number;
  hostGid: number;
  identities: WorkerIdentityRegistry;
}
const unsafe = () => new Error("UNSAFE_WORKER_WORKSPACE");

async function directory(path: string, hostUid: number, gid: number, mode: number, allowedGroups: number[]): Promise<FileHandle> {
  try { await mkdir(path, { mode: 0o700 }); }
  catch (error) { if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error; }
  const handle = await open(path, constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW);
  try {
    const metadata = await handle.stat();
    // Root creates new entries; existing entries must remain host-owned. Never
    // take over a directory that a model tool could already control.
    if (!metadata.isDirectory() || ![0, hostUid].includes(metadata.uid) || !allowedGroups.includes(metadata.gid)
      || (metadata.mode & 0o002)) throw unsafe();
    await handle.chown(hostUid, gid);
    await handle.chmod(mode);
    return handle;
  } catch (error) { await handle.close(); throw error; }
}

async function policyFile(path: string, value: object, hostUid: number, workerGid: number): Promise<void> {
  const handle = await open(path, constants.O_RDWR | constants.O_CREAT | constants.O_NOFOLLOW | constants.O_NONBLOCK, 0o600);
  try {
    const metadata = await handle.stat();
    if (!metadata.isFile() || ![0, hostUid].includes(metadata.uid) || metadata.nlink !== 1 || (metadata.mode & 0o022)) throw unsafe();
    await handle.chown(hostUid, workerGid);
    await handle.chmod(0o640);
    await handle.truncate(0);
    await handle.writeFile(JSON.stringify(value) + "\n");
    await handle.sync();
  } finally { await handle.close(); }
}

/** Root supervisor operation. Inputs must come from its authenticated-owner
 * binding; never accept a User Folder or an arbitrary path from model tools.
 * Shared ancestors must already allow traversal; this function never opens
 * permissions on an existing runtime root or rewrites user file ownership. */
export async function provisionWorkerWorkspace(options: Options): Promise<{ workspace: string; agentDir: string; stateDir: string; identity: WorkerIdentity }> {
  if (process.platform !== "linux" || process.getuid?.() !== 0) throw new Error("PRIVILEGED_WORKSPACE_PROVISIONING_REQUIRED");
  const validId = (id: number) => Number.isSafeInteger(id) && id > 0 && id < 2 ** 32 - 1;
  if (![options.hostUid, options.hostGid].every(validId)
    || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(options.userId)
    || !isAbsolute(options.usersRoot) || !isAbsolute(options.hostStateRoot) || !isAbsolute(options.workspace)) throw unsafe();
  const usersRoot = await realpath(options.usersRoot);
  const hostStateRoot = await realpath(options.hostStateRoot);
  if (usersRoot !== resolve(options.usersRoot)) throw unsafe();
  if (hostStateRoot !== resolve(options.hostStateRoot) || hostStateRoot === usersRoot
    || hostStateRoot.startsWith(usersRoot + "/") || usersRoot.startsWith(hostStateRoot + "/")) throw unsafe();
  const hostState = await open(hostStateRoot, constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW);
  try {
    const metadata = await hostState.stat();
    if (metadata.uid !== options.hostUid || (metadata.mode & 0o077)) throw unsafe();
  } finally { await hostState.close(); }
  await options.identities.assertInitialized();
  const identity = await options.identities.get(options.userId);
  if (![identity.uid, identity.gid].every(validId) || options.hostUid === identity.uid || options.hostGid === identity.gid) throw unsafe();
  const user = join(usersRoot, options.userId);
  const workspaces = join(user, "workspaces");
  const workspace = resolve(options.workspace);
  // Only direct, non-empty workspace names are provisioned. Nested paths must
  // be used through their existing workspace, never as a privileged chmod API.
  const name = relative(workspaces, workspace);
  if (!name || name === "." || name === ".." || name.includes("/") || isAbsolute(name)) throw unsafe();
  for (const [root, traversable] of [[usersRoot, true], [hostStateRoot, false]] as const) {
    let ancestor = root;
    let childOwner: number | undefined;
    for (;;) {
      const handle = await open(ancestor, constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW);
      try {
        const metadata = await handle.stat();
        const access = metadata.gid === identity.gid ? (metadata.mode >> 3) & 7 : metadata.mode & 7;
        const sticky = Boolean(metadata.mode & 0o1000) && childOwner !== undefined && [0, options.hostUid].includes(childOwner);
        if (![0, options.hostUid].includes(metadata.uid) || (traversable && !(access & 1)) || ((access & 2) && !sticky)) throw unsafe();
        childOwner = metadata.uid;
      } finally { await handle.close(); }
      const parent = dirname(ancestor);
      if (parent === ancestor) break;
      ancestor = parent;
    }
  }
  const handles: FileHandle[] = [];
  const ensure = async (path: string, gid: number, mode: number) => {
    handles.push(await directory(path, options.hostUid, gid, mode, [0, options.hostGid, identity.gid]));
  };
  try {
    await ensure(user, identity.gid, 0o710);
    await ensure(workspaces, identity.gid, 0o710);
    // Sticky + setgid: the worker can manage its files but cannot replace the
    // host-owned .pi directory. New normal files retain the owner's worker GID.
    await ensure(workspace, identity.gid, 0o3770);
    const configuration = join(workspace, ".pi");
    await ensure(configuration, identity.gid, 0o750);
    await ensure(join(configuration, "agent"), identity.gid, 0o750);
    await policyFile(join(configuration, "agent/heimdall.json"), {}, options.hostUid, identity.gid);
    await policyFile(join(configuration, "heimdall.json"), { disabled: [], sandbox: {
      enabled: true, userNamespace: false, paths: { [workspace]: { mode: "write" } },
    } }, options.hostUid, identity.gid);
    const privateRoot = join(hostStateRoot, createHash("sha256").update(options.userId).digest("hex"));
    await ensure(privateRoot, options.hostGid, 0o700);
    const agentDir = join(privateRoot, "agent"), stateDir = join(privateRoot, "state");
    await ensure(agentDir, options.hostGid, 0o700);
    await ensure(stateDir, options.hostGid, 0o700);
    for (const handle of handles) await handle.sync();
    return { workspace, agentDir, stateDir, identity };
  } finally { await Promise.all(handles.map(handle => handle.close())); }
}
