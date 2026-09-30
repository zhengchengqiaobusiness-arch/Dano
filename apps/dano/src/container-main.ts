import { constants } from "node:fs";
import { mkdir, open, realpath } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { containerProfile } from "./bridge/container-profile.js";
import { runProtectedSupervisor } from "./bridge/protected-supervisor.js";
import { snapshotOperatorSkills } from "./bridge/operator-skill-snapshot.js";

const execute = promisify(execFile);

/** Prepare only named host directories; never move, rewrite or recursively
 * chown session files. Existing roots must already be host/root owned. */
async function hostDirectory(path: string, uid: number, gid: number, mode: number): Promise<void> {
  await mkdir(path, { recursive: true, mode });
  if (await realpath(path) !== path) throw new Error("UNSAFE_CONTAINER_DIRECTORY");
  const handle = await open(path, constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW);
  try {
    const stat = await handle.stat();
    if (![0, uid].includes(stat.uid) || ![0, gid].includes(stat.gid) || (stat.mode & 0o002)) throw new Error("UNSAFE_CONTAINER_DIRECTORY");
    await handle.chown(uid, gid);
    await handle.chmod(mode);
  } finally { await handle.close(); }
}

export async function runContainerMain(args: readonly string[], environment: NodeJS.ProcessEnv = process.env): Promise<number> {
  if (process.platform !== "linux" || process.getuid?.() !== 0) throw new Error("CONTAINER_SUPERVISOR_REQUIRED");
  const installation = dirname(dirname(dirname(fileURLToPath(import.meta.url))));
  const options = containerProfile(environment, installation, args);
  const { hostUid: uid, hostGid: gid } = options.host;
  await hostDirectory(options.runtimeRoot, uid, gid, 0o711);
  await hostDirectory(join(options.runtimeRoot, "users"), uid, gid, 0o711);
  // A fresh Compose named volume starts root-owned. The ordinary startup
  // persistence probe still creates its default workspace under this mount.
  await hostDirectory(join(options.runtimeRoot, "workspaces"), uid, gid, 0o700);
  await hostDirectory(join(options.runtimeRoot, ".dano"), uid, gid, 0o700);
  await hostDirectory(options.sessionsRoot, uid, gid, 0o700);
  await hostDirectory(dirname(options.hostStateRoot), 0, 0, 0o711);
  await hostDirectory(options.hostStateRoot, uid, gid, 0o700);
  const agentDir = environment.PI_CODING_AGENT_DIR?.trim() || join(options.runtimeRoot, ".pi/agent");
  await hostDirectory(dirname(agentDir), uid, gid, 0o700);
  await hostDirectory(agentDir, uid, gid, 0o700);
  if (options.memoryRecoveryDirectory) await hostDirectory(options.memoryRecoveryDirectory, uid, gid, 0o700);
  const env = { ...environment, PI_CODING_AGENT_DIR: agentDir };
  await execute(options.broker.privilegeGuard, ["--reuid", String(uid), "--regid", String(gid), "--clear-groups", "--",
    "/bin/sh", join(installation, "deploy/docker-entrypoint.sh"), "--initialize-only"], { env });
  const skills = await snapshotOperatorSkills(installation, [
    environment.DANO_SKILLS_DIR?.trim() || join(agentDir, "skills"),
    join(options.runtimeRoot, ".agents/skills"),
  ], uid);
  options.host.trustedSkillPaths = skills.paths;
  const controller = new AbortController();
  const stop = () => controller.abort();
  process.on("SIGTERM", stop); process.on("SIGINT", stop);
  try { return await runProtectedSupervisor({ ...options, adoptLegacyHostData: true }, env, args, controller.signal); }
  finally { process.off("SIGTERM", stop); process.off("SIGINT", stop); await skills.cleanup(); }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  runContainerMain(process.argv.slice(2)).then(code => { process.exitCode = code; }, error => {
    console.error("[dano] Container startup failed:", error instanceof Error ? error.message : "unknown error");
    process.exitCode = 1;
  });
}
