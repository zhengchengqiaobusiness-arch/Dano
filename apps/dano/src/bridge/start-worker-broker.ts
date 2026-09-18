import { spawn } from "node:child_process";
import { readFile } from "node:fs/promises";
import { isAbsolute, relative } from "node:path";
import { fileURLToPath } from "node:url";
import * as bootstrap from "@josephyoung/pi-openviking/bootstrap";
import { assertWorkerPrivacyEvidence } from "./linux-process-privacy.js";
import { assertWorkerProviderApi } from "./worker-broker.js";
import { rootFile, rootInstallation } from "./trusted-installation.js";
import { WorkerBrokerClient } from "./worker-broker-client.js";

export type WorkerBrokerProfile = Parameters<typeof bootstrap.bootstrapProtectedWorker>[0] & { shutdownTimeoutMs: number };

/** Called by the privileged supervisor only, after private procfs setup and
 * owner-bound workspace provisioning. The supervisor's own UID/GID is unchanged. */
export async function startWorkerBroker(profile: WorkerBrokerProfile): Promise<WorkerBrokerClient> {
  if (process.platform !== "linux" || process.getuid?.() !== 0) throw new Error("PRIVILEGED_WORKER_BROKER_REQUIRED");
  assertWorkerProviderApi(bootstrap);
  if (![profile.startupTimeoutMs, profile.operationTimeoutMs, profile.shutdownTimeoutMs,
    profile.maxConcurrentOperations, profile.maxResultBytes].every(value => Number.isSafeInteger(value) && value > 0)
    || !Number.isSafeInteger(profile.hostGid) || profile.hostGid <= 0 || profile.hostGid >= 2 ** 32 - 1
    || profile.hostGid === profile.workerGid) throw new Error("INVALID_WORKER_BROKER_PROFILE");
  const paths = await bootstrap.validateProtectedPaths(profile);
  await rootInstallation(paths.installationDir);
  const entry = await rootFile(fileURLToPath(new URL("./worker-broker-entry.js", import.meta.url)));
  const suffix = relative(paths.installationDir, entry);
  if (!suffix || suffix === ".." || suffix.startsWith("../") || isAbsolute(suffix)) throw new Error("WORKER_BROKER_INSTALLATION_REQUIRED");
  const node = await rootFile(process.execPath);
  const guard = await rootFile(profile.privilegeGuard);
  // Explicit projection prevents accidental credentials or environment fields
  // in a caller's config object from entering the child's argv or environment.
  const childProfile = {
    workspace: paths.workspace, agentDir: paths.agentDir, stateDir: paths.stateDir,
    installationDir: paths.installationDir, hostUid: profile.hostUid, hostGid: profile.hostGid,
    workerUid: profile.workerUid, workerGid: profile.workerGid,
    piPackageContext: profile.piPackageContext, privilegeGuard: guard, path: profile.path,
    startupTimeoutMs: profile.startupTimeoutMs, operationTimeoutMs: profile.operationTimeoutMs,
    maxConcurrentOperations: profile.maxConcurrentOperations, maxResultBytes: profile.maxResultBytes,
  };
  const child = spawn(guard, ["--no-new-privs", "--", node, entry, JSON.stringify(childProfile)], {
    cwd: paths.installationDir, env: { PATH: profile.path, LANG: "C.UTF-8" },
    stdio: ["ignore", "ignore", "ignore", "ipc"],
  });
  const client = new WorkerBrokerClient(child, { ...childProfile,
    shutdownTimeoutMs: profile.shutdownTimeoutMs,
    async assertBrokerIdentity() {
      try {
        if (!child.pid || child.exitCode !== null || child.signalCode !== null) throw new Error();
        const status = await readFile(`/proc/${child.pid}/status`, "utf8");
        assertWorkerPrivacyEvidence(status);
        for (const [field, expected] of [["Uid", profile.hostUid], ["Gid", profile.hostGid]] as const) {
          const match = new RegExp(`^${field}:\\s+(\\d+)\\s+(\\d+)\\s+(\\d+)\\s+(\\d+)$`, "m").exec(status);
          if (!match || match.slice(1).some(value => Number(value) !== expected)) throw new Error();
        }
      } catch { throw new Error("WORKER_BROKER_IDENTITY_MISMATCH"); }
    },
  });
  try { await client.assertIsolated(); return client; }
  catch (error) { await client.close(); throw error; }
}
