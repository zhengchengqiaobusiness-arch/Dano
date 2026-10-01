import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { chown, chmod, lstat, mkdir, mkdtemp, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { promisify } from "node:util";

// Run in a disposable root Linux container with no production mounts. Exercise
// real provisioning and real unprivileged processes, never mocked UID/GID checks.
assert.equal(process.platform, "linux");
assert.equal(process.getuid(), 0);
const modulePath = process.argv[2] ?? resolve(import.meta.dirname, "../apps/dano/dist/server/bridge/worker-workspace.js");
const { provisionWorkerWorkspace, WorkerIdentityRegistry } = await import(pathToFileURL(modulePath));
const hostUid = 1000, hostGid = 1000;
const root = await mkdtemp(join(tmpdir(), "dano-upload-access-"));
const usersRoot = join(root, "users"), hostStateRoot = join(root, "state");
const run = (uid, gid, code, ...args) => promisify(execFile)("setpriv",
  ["--reuid", String(uid), "--regid", String(gid), "--clear-groups", process.execPath,
    "--input-type=module", "-e", code, ...args], { timeout: 10000 });
try {
  await chown(root, hostUid, hostGid); await chmod(root, 0o755);
  await mkdir(usersRoot); await chown(usersRoot, hostUid, hostGid);
  await mkdir(hostStateRoot, { mode: 0o700 }); await chown(hostStateRoot, hostUid, hostGid);
  const identities = new WorkerIdentityRegistry({ directory: join(root, "identities"),
    hostUid, hostGid, firstUid: 20000, firstGid: 20000, count: 10, lockTimeoutMs: 1000 });
  await identities.initialize([usersRoot, hostStateRoot]);
  for (const kind of ["legacy", "worker-created", "fresh"]) {
    const identity = await identities.get(kind);
    const workspace = join(usersRoot, kind, "workspaces", "synthetic");
    await mkdir(workspace, { recursive: true });
    for (const path of [join(usersRoot, kind), join(usersRoot, kind, "workspaces"), workspace]) {
      await chown(path, hostUid, kind === "worker-created" ? identity.gid : hostGid);
    }
    const uploads = join(workspace, "uploads");
    if (kind !== "fresh") {
      await mkdir(uploads);
      await chown(uploads, kind === "legacy" ? hostUid : identity.uid,
        kind === "legacy" ? hostGid : identity.gid);
      await chmod(uploads, 0o2775);
      await writeFile(join(uploads, "existing.txt"), "existing upload", { mode: 0o644 });
      await chown(join(uploads, "existing.txt"), kind === "legacy" ? hostUid : identity.uid, identity.gid);
    }
    const options = { usersRoot, hostStateRoot, userId: kind, workspace, hostUid, hostGid, identities };
    await provisionWorkerWorkspace(options);
    await provisionWorkerWorkspace(options); // Restart must retain access.
    const metadata = await lstat(uploads);
    assert.equal(metadata.uid, hostUid); assert.equal(metadata.gid, identity.gid);
    assert.equal(metadata.mode & 0o7777, 0o3770);
    await run(hostUid, hostGid, `
      import assert from 'node:assert/strict';
      import { mkdir, writeFile, rename, readFile, unlink } from 'node:fs/promises';
      import { createHash } from 'node:crypto';
      import { join } from 'node:path';
      assert.match(await readFile('/proc/self/status', 'utf8'), /^Groups:\\s*$/m);
      const uploads = process.argv[1];
      await mkdir(uploads, { recursive: true });
      const bytes = Buffer.from('synthetic upload');
      const file = join(uploads, createHash('sha256').update(bytes).digest('hex') + '.txt');
      await writeFile(file + '.part', bytes, { flag: 'wx' });
      await rename(file + '.part', file);
      assert.deepEqual(await readFile(file), bytes);
      if (process.argv[2] !== 'fresh') assert.equal(await readFile(join(uploads, 'existing.txt'), 'utf8'), 'existing upload');
      await unlink(file);
      await writeFile(join(uploads, 'model-readable.txt'), bytes);
    `, uploads, kind);
    await run(identity.uid, identity.gid, `
      import assert from 'node:assert/strict';
      import { readFile, writeFile, unlink, rename } from 'node:fs/promises';
      import { join } from 'node:path';
      const uploads = process.argv[1];
      assert.equal(await readFile(join(uploads, 'model-readable.txt'), 'utf8'), 'synthetic upload');
      await writeFile(join(uploads, 'worker.txt'), 'worker-created');
      await unlink(join(uploads, 'worker.txt'));
      await assert.rejects(unlink(join(uploads, 'model-readable.txt')), { code: 'EPERM' });
      await assert.rejects(rename(uploads, uploads + '-replaced'), { code: 'EPERM' });
    `, uploads);
    await run(identity.uid + 100, identity.gid + 100, `
      import assert from 'node:assert/strict';
      import { readFile, writeFile } from 'node:fs/promises';
      import { join } from 'node:path';
      await assert.rejects(readFile(join(process.argv[1], 'model-readable.txt')), { code: 'EACCES' });
      await assert.rejects(writeFile(join(process.argv[1], 'intruder.txt'), 'intruder'), { code: 'EACCES' });
    `, uploads);
    await rm(uploads, { recursive: true });
    const outside = join(root, 'outside-' + kind);
    await mkdir(outside, { mode: 0o700 });
    await symlink(outside, uploads);
    await assert.rejects(provisionWorkerWorkspace(options));
    assert.equal((await lstat(outside)).mode & 0o7777, 0o700);
    await rm(uploads);
    await mkdir(uploads); await chown(uploads, identity.uid + 100, identity.gid + 100);
    await assert.rejects(provisionWorkerWorkspace(options), /UNSAFE_WORKER_WORKSPACE/);
    assert.equal((await lstat(uploads)).uid, identity.uid + 100);
  }
  console.log(JSON.stringify({ uploadWorkspaceAccess: "passed", cases: ["legacy", "worker-created", "fresh"],
    checks: ["host-without-groups", "upload-rename-preview-cleanup", "model-read-write", "restart", "cross-user-denial", "symlink-denial", "foreign-owner-denial"] }));
} finally { await rm(root, { recursive: true, force: true }); }
