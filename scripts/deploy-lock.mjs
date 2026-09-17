import { spawnSync } from "node:child_process";
import { closeSync, constants, fstatSync, openSync, statSync } from "node:fs";

// flock locks the shared open-file description. Python acquires the lock on
// inherited fd 3; Node retains that description until the transaction ends.
// This interoperates with util-linux flock and works on macOS as well.
export function acquireDeploymentLock() {
  const path = process.env.DANO_DEPLOY_LOCK_PATH || "/var/lock/dano-production-deploy.lock";
  const inherited = process.env.DANO_DEPLOY_LOCK_FD;
  const fd = inherited ? Number(inherited) : openSync(path, constants.O_CREAT | constants.O_RDWR | constants.O_NOFOLLOW, 0o600);
  try {
    const actual = fstatSync(fd);
    const expected = statSync(path);
    if (!actual.isFile() || actual.dev !== expected.dev || actual.ino !== expected.ino) throw new Error("DEPLOY_LOCK_INVALID");
    const result = spawnSync("python3", ["-c", "import fcntl; fcntl.flock(3, fcntl.LOCK_EX | fcntl.LOCK_NB)"], { stdio: ["ignore", "ignore", "ignore", fd] });
    if (result.error || result.status !== 0) throw new Error("DEPLOY_LOCK_BUSY_OR_UNAVAILABLE");
    return () => { if (!inherited) closeSync(fd); };
  } catch (error) {
    if (!inherited) closeSync(fd);
    throw error;
  }
}
