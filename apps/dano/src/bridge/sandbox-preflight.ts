import assert from "node:assert/strict";
import { mkdtemp, mkdir, chmod, chown, writeFile, readFile, symlink, rm } from "node:fs/promises";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { containerProfile } from "./container-profile.js";
import { prepareLinuxProcessPrivacy } from "./linux-process-privacy.js";
import { WorkerIdentityRegistry } from "./worker-identity-registry.js";
import { WorkerSupervisor } from "./worker-supervisor.js";

/** Run in a disposable container with the deployment's capabilities and mount
 * policy. All fixtures are synthetic; never read or modify production state. */
export async function checkSandbox(installation: string): Promise<void> {
  if (process.platform !== "linux" || process.getuid?.() !== 0) throw new Error("SANDBOX_PREFLIGHT_REQUIRES_ROOT_CONTAINER");
  const profile = containerProfile(process.env, installation);
  const { hostUid, hostGid } = profile.broker;
  const root = await mkdtemp("/tmp/dano-sandbox-preflight-");
  let pool: WorkerSupervisor | undefined;
  try {
    await chmod(root, 0o711);
    const usersRoot = join(root, "users"), hostStateRoot = join(root, "private");
    for (const [path, mode] of [[usersRoot, 0o711], [hostStateRoot, 0o700]] as const) {
      await mkdir(path, { mode }); await chown(path, hostUid, hostGid);
    }
    const identities = new WorkerIdentityRegistry({ ...profile.identities, directory: join(root, "identities"), hostUid, hostGid });
    await identities.initialize([usersRoot, hostStateRoot]);
    await prepareLinuxProcessPrivacy(hostUid, hostGid);
    pool = new WorkerSupervisor({ usersRoot, hostStateRoot, identities, maxWorkers: 2, broker: profile.broker });
    const alice = await pool.acquire("sandbox-alice", join(usersRoot, "sandbox-alice/workspaces/check"));
    const bob = await pool.acquire("sandbox-bob", join(usersRoot, "sandbox-bob/workspaces/check"));
    const privateFile = join(hostStateRoot, "configuration-credential-recovery");
    const marker = "synthetic-private-marker";
    await writeFile(privateFile, marker, { mode: 0o600 }); await chown(privateFile, hostUid, hostGid);
    await bob.worker.execute("write", { path: "private.txt", content: marker });
    await symlink(privateFile, join(alice.workspace, "host-link"));
    await symlink(join(bob.workspace, "private.txt"), join(alice.workspace, "other-user-link"));
    for (const path of [privateFile, "host-link", join(bob.workspace, "private.txt"), "other-user-link"]) {
      await assert.rejects(alice.worker.execute("read", { path }));
      await assert.rejects(alice.worker.execute("write", { path, content: "changed" }));
      await assert.rejects(alice.worker.execute("edit", { path, edits: [{ oldText: marker, newText: "changed" }] }));
    }
    await assert.rejects(alice.worker.execute("write", { path: ".pi/agent/bin/rg", content: "untrusted executable" }));
    await assert.rejects(alice.worker.execute("write", { path: ".pi/heimdall.json", content: "{}" }));
    const shell = await alice.worker.execute("bash", { command: "ls >/dev/null && test ! -r host-link && test ! -w host-link && test ! -r other-user-link && test ! -w other-user-link && printf SANDBOX_OK" });
    assert.match(JSON.stringify(shell), /SANDBOX_OK/);
    assert.equal(await readFile(privateFile, "utf8"), marker);
    assert.equal(await readFile(join(bob.workspace, "private.txt"), "utf8"), marker);
    console.log(JSON.stringify({ bash: true, shellNoNewPrivileges: true, shellCapabilitiesZero: true,
      privateReadWriteEditDenied: true, crossUserReadWriteEditDenied: true, symlinkDenied: true }));
  } finally {
    await pool?.close();
    await rm(root, { recursive: true, force: true });
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  checkSandbox(resolve(process.argv[2] || process.cwd())).catch(() => {
    console.error("DANO_SANDBOX_PREFLIGHT_FAILED: verify setuid Bubblewrap, container capabilities and procfs privacy; production must not be switched.");
    process.exitCode = 1;
  });
}
