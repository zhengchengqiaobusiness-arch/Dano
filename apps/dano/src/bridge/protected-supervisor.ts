import { spawn, execFile } from "node:child_process";
import { constants } from "node:fs";
import { lstat, mkdir, open, realpath } from "node:fs/promises";
import { join, relative, resolve, isAbsolute } from "node:path";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";
import { flock } from "fs-ext";
import { prepareLinuxProcessPrivacy } from "./linux-process-privacy.js";
import { rootFile, rootInstallation } from "./trusted-installation.js";
import { WorkerIdentityRegistry } from "./worker-identity-registry.js";
import { WorkerSupervisor, type WorkerSupervisorOptions } from "./worker-supervisor.js";
import { serveWorkerSupervisor } from "./worker-supervisor-rpc.js";
import { parseProtectedHostProfile, type ProtectedHostProfile } from "./protected-host-profile.js";

export interface ProtectedSupervisorOptions {
  runtimeRoot: string;
  sessionsRoot: string;
  hostStateRoot: string;
  memoryConfigDirectory?: string;
  memoryRecoveryDirectory?: string;
  identities: { directory: string; firstUid: number; firstGid: number; count: number; lockTimeoutMs: number };
  maxWorkers: number;
  broker: WorkerSupervisorOptions["broker"];
  host: Omit<ProtectedHostProfile, "memory">;
}
const execute = promisify(execFile);
const unsafe = () => new Error("UNSAFE_PROTECTED_SUPERVISOR_CONFIGURATION");
const inside = (parent: string, child: string) => {
  const suffix = relative(parent, child);
  return suffix === "" || (suffix !== ".." && !suffix.startsWith("../") && !isAbsolute(suffix));
};

/** Reserved worker IDs must not refer to NSS users or groups. */
export function assertUnusedWorkerRange(passwd: string, groups: string,
  range: ProtectedSupervisorOptions["identities"]): void {
  for (const [content, first, isPasswd] of [[passwd, range.firstUid, true], [groups, range.firstGid, false]] as const) {
    for (const line of content.split("\n").filter(Boolean)) {
      const fields = line.split(":");
      if (fields.length < 4 || !/^\d+$/.test(fields[2]!)) throw unsafe();
      const id = Number(fields[2]);
      if (id >= first && id < first + range.count) throw new Error("WORKER_IDENTITY_SYSTEM_COLLISION");
      if (isPasswd) {
        if (!/^\d+$/.test(fields[3]!)) throw unsafe();
        const primaryGroup = Number(fields[3]);
        if (primaryGroup >= range.firstGid && primaryGroup < range.firstGid + range.count) {
          throw new Error("WORKER_IDENTITY_SYSTEM_COLLISION");
        }
      }
    }
  }
}

async function provisionRoot(path: string, uid: number, gid: number, mode: number): Promise<void> {
  if (!isAbsolute(path) || resolve(path) !== path) throw unsafe();
  let created = false;
  try { await mkdir(path, { mode: 0o700 }); created = true; }
  catch (error) { if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error; }
  if (await realpath(path) !== path) throw unsafe();
  const handle = await open(path, constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW);
  try {
    if (created) { await handle.chown(uid, gid); await handle.chmod(mode); }
    const metadata = await handle.stat();
    // Existing data requires its deployed ownership/mode; never widen it here.
    if (metadata.uid !== uid || metadata.gid !== gid || (metadata.mode & 0o7777) !== mode) throw unsafe();
  } finally { await handle.close(); }
}

/** Root-only launch in a dedicated container/mount namespace. Environment is
 * passed only to the trusted HTTP host; worker spawning uses its own allowlist. */
export async function runProtectedSupervisor(options: ProtectedSupervisorOptions,
  hostEnvironment: NodeJS.ProcessEnv, args: readonly string[] = [], signal?: AbortSignal): Promise<number> {
  if (process.platform !== "linux" || process.getuid?.() !== 0) throw new Error("PRIVILEGED_SUPERVISOR_REQUIRED");
  signal?.throwIfAborted();
  const host = parseProtectedHostProfile(options.host);
  if (host.memory) throw unsafe();
  if (host.hostUid !== options.broker.hostUid || host.hostGid !== options.broker.hostGid) throw unsafe();
  const usersRoot = join(options.runtimeRoot, "users");
  const roots = [options.runtimeRoot, options.sessionsRoot, options.hostStateRoot, options.identities.directory];
  if (roots.some(root => !isAbsolute(root) || resolve(root) !== root)
    || [options.sessionsRoot, options.hostStateRoot, options.identities.directory].some(root => inside(usersRoot, root) || inside(root, usersRoot))
    || [options.sessionsRoot, options.hostStateRoot].some(root => inside(root, options.identities.directory) || inside(options.identities.directory, root))
    || inside(options.sessionsRoot, options.hostStateRoot) || inside(options.hostStateRoot, options.sessionsRoot)) throw unsafe();
  const identities = new WorkerIdentityRegistry({ ...options.identities, hostUid: host.hostUid, hostGid: host.hostGid });
  const installation = await realpath(options.broker.installationDir);
  if ((options.memoryConfigDirectory === undefined) !== (options.memoryRecoveryDirectory === undefined)) throw unsafe();
  if (options.memoryConfigDirectory !== undefined && options.memoryRecoveryDirectory !== undefined) {
    const directory = options.memoryConfigDirectory;
    if (!isAbsolute(directory) || resolve(directory) !== directory || await realpath(directory) !== directory
      || [...roots, installation].some(root => inside(root, directory) || inside(directory, root))) throw unsafe();
    const metadata = await lstat(directory);
    if (!metadata.isDirectory() || metadata.uid !== host.hostUid || metadata.gid !== host.hostGid
      || (metadata.mode & 0o7777) !== 0o700) throw unsafe();
    const recovery = options.memoryRecoveryDirectory;
    if (!isAbsolute(recovery) || resolve(recovery) !== recovery || await realpath(recovery) !== recovery
      || [...roots, installation, directory].some(root => inside(root, recovery) || inside(recovery, root))) throw unsafe();
    const recoveryMetadata = await lstat(recovery);
    if (!recoveryMetadata.isDirectory() || recoveryMetadata.uid !== host.hostUid || recoveryMetadata.gid !== host.hostGid
      || (recoveryMetadata.mode & 0o7777) !== 0o700) throw unsafe();
    host.memory = { configurationDirectory: directory, stateDirectory: join(options.hostStateRoot, "memory-service"),
      recoveryDirectory: recovery };
  }
  await rootInstallation(installation);
  const entry = await rootFile(fileURLToPath(new URL("./protected-host-entry.js", import.meta.url)));
  if (!inside(installation, entry)) throw unsafe();
  const node = await rootFile(process.execPath);
  const guard = await rootFile(options.broker.privilegeGuard);
  for (const resource of [...host.trustedSkillPaths, host.providerPythonModuleDirectory]) {
    if (!inside(installation, await realpath(resource))) throw unsafe();
  }
  host.trustedSkillPaths = await Promise.all(host.trustedSkillPaths.map(path => realpath(path)));
  const [passwd, groups] = await Promise.all([
    execute("/usr/bin/getent", ["passwd"], { timeout: 10000, maxBuffer: 1024 * 1024 }),
    execute("/usr/bin/getent", ["group"], { timeout: 10000, maxBuffer: 1024 * 1024 }),
  ]);
  assertUnusedWorkerRange(passwd.stdout, groups.stdout, options.identities);
  await provisionRoot(options.identities.directory, 0, 0, 0o700);
  const lock = await open(join(options.identities.directory, "supervisor.lock"),
    constants.O_RDWR | constants.O_CREAT | constants.O_NOFOLLOW | constants.O_NONBLOCK, 0o600);
  let locked = false;
  try {
    const metadata = await lock.stat();
    if (!metadata.isFile() || metadata.uid !== 0 || metadata.nlink !== 1 || (metadata.mode & 0o077)) throw unsafe();
    await new Promise<void>((accept, reject) => flock(lock.fd, "exnb", error => error ? reject(new Error("SUPERVISOR_ALREADY_RUNNING")) : accept()));
    locked = true;
    await provisionRoot(options.runtimeRoot, host.hostUid, host.hostGid, 0o711);
    await provisionRoot(usersRoot, host.hostUid, host.hostGid, 0o711);
    await provisionRoot(options.sessionsRoot, host.hostUid, host.hostGid, 0o700);
    await provisionRoot(options.hostStateRoot, host.hostUid, host.hostGid, 0o700);
    await identities.initialize([usersRoot, options.sessionsRoot, options.hostStateRoot]);
    await prepareLinuxProcessPrivacy(host.hostUid, host.hostGid);
    signal?.throwIfAborted();
    const pool = new WorkerSupervisor({ usersRoot, hostStateRoot: options.hostStateRoot, identities,
      maxWorkers: options.maxWorkers, broker: options.broker, trustedReadPaths: [...host.trustedSkillPaths, await realpath(host.providerPythonModuleDirectory)] });
    const child = spawn(guard, ["--reuid", String(host.hostUid), "--regid", String(host.hostGid),
      "--clear-groups", "--no-new-privs", "--", node, entry, JSON.stringify(host), ...args], {
      cwd: installation,
      // Own a separate process group so a killed HTTP host cannot orphan its
      // managed search daemon. Worker brokers have their own supervisor leases.
      detached: true,
      env: { ...hostEnvironment, DANO_RUNTIME_DIR: options.runtimeRoot, DANO_SESSIONS_ROOT: options.sessionsRoot },
      stdio: ["ignore", "inherit", "inherit", "ipc"],
    });
    const exited = new Promise<number>((accept, reject) => {
      child.once("error", reject);
      child.once("close", code => accept(code ?? 1));
    });
    const rpc = serveWorkerSupervisor(pool, child, host);
    let deadline: ReturnType<typeof setTimeout> | undefined;
    const stop = () => {
      if (deadline || child.exitCode !== null || child.signalCode !== null) return;
      child.kill("SIGTERM");
      deadline = setTimeout(() => child.kill("SIGKILL"), options.broker.shutdownTimeoutMs);
    };
    signal?.addEventListener("abort", stop, { once: true });
    if (signal?.aborted) stop();
    try { return await exited; }
    finally {
      signal?.removeEventListener("abort", stop);
      if (deadline) clearTimeout(deadline);
      try {
        if (child.pid) {
          try { process.kill(-child.pid, "SIGKILL"); }
          catch (error) { if ((error as NodeJS.ErrnoException).code !== "ESRCH") throw error; }
        }
      } finally { await rpc.close(); }
    }
  } finally {
    try {
      if (locked) await new Promise<void>((accept, reject) => flock(lock.fd, "un", error => error ? reject(error) : accept()));
    } finally { await lock.close(); }
  }
}
