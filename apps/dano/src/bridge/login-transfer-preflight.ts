import assert from "node:assert/strict";
import { spawn, execFileSync } from "node:child_process";
import { constants } from "node:fs";
import { access, chmod, chown, lstat, mkdir, mkdtemp, readFile, readdir, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { UserRuntimeRegistry } from "./user-runtime-registry.js";
import { WorkerSupervisor, type WorkerSupervisorOptions } from "./worker-supervisor.js";
import { WorkerSupervisorClient } from "./worker-supervisor-client.js";
import { serveWorkerSupervisor } from "./worker-supervisor-rpc.js";
import { WorkerIdentityRegistry } from "./worker-identity-registry.js";

const hostUid = 1000, hostGid = 1000;
const limits = { startupTimeoutMs: 5000, operationTimeoutMs: 10000, maxConcurrentOperations: 4, maxMessageBytes: 4096 };
const filename = `${"a".repeat(64)}.txt`, content = "synthetic login upload\n";

async function hostTransfer(root: string, scenario: string): Promise<void> {
  assert.equal(process.getuid?.(), hostUid); assert.equal(process.getgid?.(), hostGid);
  assert.ok(process.send && process.connected);
  const client = new WorkerSupervisorClient({
    get connected() { return Boolean(process.connected); }, on: process.on.bind(process), off: process.off.bind(process),
    send: (value, callback) => process.send!(value, callback), disconnect: () => { if (process.connected) process.disconnect?.(); },
  }, limits);
  const source = { user: { id: "login-source" }, folderPath: join(root, "users", "login-source") };
  const target = { user: { id: "login-target" }, folderPath: join(root, "users", "login-target") };
  const workspace = join(target.folderPath, "workspaces", "default");
  let committed = false, commitAttempted = false, preparations = 0;
  const registry = new UserRuntimeRegistry(async () => { throw new Error("BACKEND_MUST_NOT_START"); }, {
    prepareWorkspaceForUser: async (context, path) => { preparations++; await client.prepareWorkspace(context, path); },
  });
  try {
    assert.equal(await readFile(join(source.folderPath, "workspaces", "default", "uploads", filename), "utf8"), content);
    await access(workspace, constants.X_OK);
    let failure: unknown;
    try {
      await registry.transferOwnership(source, target, { assertIdle() {}, async commitOwnership() {
        commitAttempted = true;
        assert.equal(await readFile(join(workspace, "uploads", filename), "utf8"), content);
        if (scenario === "rollback") throw new Error("SYNTHETIC_COMMIT_FAILURE");
        committed = true;
      } });
    } catch (error) { failure = error; }
    assert.equal(preparations, 1);
    assert.equal(Boolean(failure), scenario !== "cold"); assert.equal(committed, scenario === "cold");
    assert.equal(commitAttempted, scenario !== "unsafe");
    if (scenario === "rollback") assert.equal((failure as Error).message, "SYNTHETIC_COMMIT_FAILURE");
    if (scenario === "cold") assert.equal(await readFile(join(workspace, "uploads", filename), "utf8"), content);
    else assert.ok(!(await readdir(join(workspace, "uploads"))).includes(filename));
    assert.equal(await readFile(join(source.folderPath, "workspaces", "default", "uploads", filename), "utf8"), content);
    process.send!({ type: "test-result", passed: true });
  } finally { await registry.dispose(); client.close(); }
}

/** Real UID/GID + root IPC regression; disposable fixtures only, no model/provider or production state. */
export async function checkLoginTransferAccess(): Promise<void> {
  assert.equal(process.platform, "linux"); assert.equal(process.getuid?.(), 0);
  for (const mask of [0o022, 0o077]) for (const scenario of ["cold", "unsafe", "rollback"]) {
    const previous = process.umask(mask), root = await mkdtemp(join(tmpdir(), "dano-login-transfer-"));
    try {
      await chmod(root, 0o755); await chown(root, hostUid, hostGid);
      const usersRoot = join(root, "users"), hostStateRoot = join(root, "state");
      for (const [path, mode] of [[usersRoot, 0o755], [hostStateRoot, 0o700]] as const) {
        await mkdir(path); await chmod(path, mode); await chown(path, hostUid, hostGid);
      }
      const identities = new WorkerIdentityRegistry({ directory: join(root, "identities"),
        hostUid, hostGid, firstUid: 20000, firstGid: 20000, count: 10, lockTimeoutMs: 1000 });
      await identities.initialize([usersRoot, hostStateRoot]);
      const identity = await identities.get("login-target"), peer = await identities.get("peer");
      for (const user of ["login-source", "login-target"]) {
        let path = usersRoot;
        for (const part of [user, "workspaces", "default", "uploads"]) {
          path = join(path, part); await mkdir(path); await chmod(path, 0o755); await chown(path, hostUid, hostGid);
        }
      }
      const workspace = join(usersRoot, "login-target", "workspaces", "default"), uploads = join(workspace, "uploads");
      await chown(workspace, hostUid, identity.gid); await chmod(workspace, 0o3770);
      const sourceFile = join(usersRoot, "login-source", "workspaces", "default", "uploads", filename);
      await writeFile(sourceFile, content); await chmod(sourceFile, 0o644); await chown(sourceFile, hostUid, hostGid);
      if (scenario === "unsafe") {
        const outside = join(root, "outside"); await mkdir(outside); await chmod(outside, 0o700); await chown(outside, hostUid, hostGid);
        await rm(uploads, { recursive: true }); await symlink(outside, uploads);
      } else { await chown(uploads, identity.uid, identity.gid); await chmod(uploads, 0o2775); }
      let workerStarts = 0;
      const pool = new WorkerSupervisor({ usersRoot, hostStateRoot, identities, maxWorkers: 1,
        broker: { hostUid, hostGid } as WorkerSupervisorOptions["broker"] }, async () => {
        workerStarts++; throw new Error("WORKER_MUST_NOT_START");
      });
      const child = spawn("setpriv", ["--reuid", String(hostUid), "--regid", String(hostGid), "--clear-groups", "--no-new-privs",
        process.execPath, fileURLToPath(import.meta.url), "host", root, scenario], { stdio: ["ignore", "pipe", "pipe", "ipc"] });
      let passed = false;
      child.on("message", message => { if (message && typeof message === "object" && "passed" in message) passed = message.passed === true; });
      const timer = setTimeout(() => child.kill("SIGKILL"), 20000);
      const server = serveWorkerSupervisor(pool, child, limits);
      try {
        const code = await new Promise<number | null>((done, reject) => { child.once("close", done); child.once("error", reject); });
        assert.equal(code, 0); assert.equal(passed, true); assert.equal(workerStarts, 0);
      } finally { clearTimeout(timer); await server.close(); }
      if (scenario === "unsafe") {
        assert.equal((await lstat(join(root, "outside"))).mode & 0o7777, 0o700);
        assert.deepEqual(await readdir(join(root, "outside")), []);
      } else {
        const metadata = await lstat(uploads);
        assert.equal(metadata.uid, hostUid); assert.equal(metadata.gid, identity.gid); assert.equal(metadata.mode & 0o7777, 0o3770);
        if (scenario === "cold") execFileSync("setpriv", ["--reuid", String(identity.uid), "--regid", String(identity.gid), "--clear-groups",
          process.execPath, "--input-type=module", "-e", `
          import assert from 'node:assert/strict'; import {readFile} from 'node:fs/promises';
          assert.equal(await readFile(process.argv[1], 'utf8'), process.argv[2]);`, join(uploads, filename), content],
          { stdio: ["ignore", "pipe", "pipe"] });
        execFileSync("setpriv", ["--reuid", String(peer.uid), "--regid", String(peer.gid), "--clear-groups",
          process.execPath, "--input-type=module", "-e", `
          import assert from 'node:assert/strict'; import {readdir} from 'node:fs/promises';
          await assert.rejects(readdir(process.argv[1]),{code:'EACCES'});`, uploads], { stdio: ["ignore", "pipe", "pipe"] });
      }
      console.log(JSON.stringify({ loginTransferAccess: "passed", scenario, umask: mask.toString(8), workerStarts }));
    } finally { await rm(root, { recursive: true, force: true }); process.umask(previous); }
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  (process.argv[2] === "host" ? hostTransfer(process.argv[3]!, process.argv[4]!) : checkLoginTransferAccess())
    .catch(() => { console.error("LOGIN_TRANSFER_PREFLIGHT_FAILED"); process.exitCode = 1; });
}
