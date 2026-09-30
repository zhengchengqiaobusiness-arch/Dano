import { spawn } from "node:child_process";
import { readFile } from "node:fs/promises";
import { isAbsolute, join, relative } from "node:path";
import * as bootstrap from "@josephyoung/pi-openviking/bootstrap";
import { assertWorkerPrivacyEvidence } from "./linux-process-privacy.js";
import { rootFile, rootInstallation, rootSearchPath } from "./trusted-installation.js";
import { WorkerBrokerClient } from "./worker-broker-client.js";

export type WorkerBrokerProfile = Parameters<typeof bootstrap.bootstrapProtectedWorker>[0] & { shutdownTimeoutMs: number };

/** Called by the privileged supervisor only, after private procfs setup and
 * owner-bound workspace provisioning. The supervisor's own UID/GID is unchanged. */
export async function startWorkerBroker(profile: WorkerBrokerProfile): Promise<WorkerBrokerClient> {
  if (process.platform !== "linux" || process.getuid?.() !== 0) throw new Error("PRIVILEGED_WORKER_BROKER_REQUIRED");
  if (![profile.startupTimeoutMs, profile.operationTimeoutMs, profile.shutdownTimeoutMs,
    profile.maxConcurrentOperations, profile.maxResultBytes].every(value => Number.isSafeInteger(value) && value > 0)
    || !Number.isSafeInteger(profile.hostGid) || profile.hostGid <= 0 || profile.hostGid >= 2 ** 32 - 1
    || profile.hostGid === profile.workerGid) throw new Error("INVALID_WORKER_BROKER_PROFILE");
  const paths = await bootstrap.validateProtectedPaths(profile);
  await rootInstallation(paths.installationDir);
  const entry = await rootFile(join(paths.installationDir, "dist/server/bridge/worker-broker-entry.js"));
  const suffix = relative(paths.installationDir, entry);
  if (!suffix || suffix === ".." || suffix.startsWith("../") || isAbsolute(suffix)) throw new Error("WORKER_BROKER_INSTALLATION_REQUIRED");
  const node = await rootFile(process.execPath);
  const guard = await rootFile(profile.privilegeGuard);
  const path = await rootSearchPath(profile.path);
  // Explicit projection prevents accidental credentials or environment fields
  // in a caller's config object from entering the child's argv or environment.
  const childProfile = {
    workspace: paths.workspace,
    workerUid: profile.workerUid, workerGid: profile.workerGid,
    startupTimeoutMs: profile.startupTimeoutMs, operationTimeoutMs: profile.operationTimeoutMs,
    maxConcurrentOperations: profile.maxConcurrentOperations, maxResultBytes: profile.maxResultBytes,
  };
  const child = spawn(guard, ["--reuid", String(profile.workerUid), "--regid", String(profile.workerGid),
    "--clear-groups", "--", node, entry, JSON.stringify(childProfile)], {
    cwd: paths.workspace,
    env: { PATH: path, LANG: "C.UTF-8", HOME: paths.workspace,
      PI_CODING_AGENT_DIR: join(paths.workspace, ".pi/agent"), PI_OFFLINE: "1" },
    stdio: ["ignore", "ignore", "ignore", "ipc"],
  });
  const client = new WorkerBrokerClient(child, { ...childProfile,
    shutdownTimeoutMs: profile.shutdownTimeoutMs,
    async assertBrokerIdentity() {
      try {
        if (!child.pid || child.exitCode !== null || child.signalCode !== null) throw new Error();
        const status = await readFile(`/proc/${child.pid}/status`, "utf8");
        assertWorkerPrivacyEvidence(status, true);
        for (const [field, expected] of [["Uid", profile.workerUid], ["Gid", profile.workerGid]] as const) {
          const match = new RegExp(`^${field}:\\s+(\\d+)\\s+(\\d+)\\s+(\\d+)\\s+(\\d+)$`, "m").exec(status);
          if (!match || match.slice(1).some(value => Number(value) !== expected)) throw new Error();
        }
      } catch { throw new Error("WORKER_BROKER_IDENTITY_MISMATCH"); }
    },
  });
  try { await client.assertIsolated(); return client; }
  catch (error) { await client.close(); throw error; }
}
