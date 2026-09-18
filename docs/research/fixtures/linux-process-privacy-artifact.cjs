// Disposable root Linux container only: built module path, host identity, worker identity.
const assert = require('node:assert/strict');
const { spawnSync, execFileSync } = require('node:child_process');
const { pathToFileURL } = require('node:url');
const [artifact, hostText, workerText] = process.argv.slice(-3);
const hostId = Number(hostText), workerId = Number(workerText);
process.setgroups([]);
(async () => {
  const privacy = await import(pathToFileURL(artifact).href);
  await assert.rejects(privacy.assertWorkerProcessPrivacy(), /MEMORY_PROCESS_PRIVACY_REQUIRED/);
  await privacy.prepareLinuxProcessPrivacy(hostId, hostId);
  await privacy.prepareLinuxProcessPrivacy(hostId, hostId);
  const attempt = (gid, nnp) => {
    const args = ['-e', `import(${JSON.stringify(pathToFileURL(artifact).href)}).then(m => m.assertWorkerProcessPrivacy()).catch(() => {process.exitCode=1;});`];
    const child = spawnSync(nnp ? '/usr/bin/setpriv' : process.execPath,
      nnp ? ['--no-new-privs', '--', process.execPath, ...args] : args,
      { uid: workerId, gid, encoding: 'utf8' });
    assert.equal(child.error, undefined);
    return child.status;
  };
  assert.equal(attempt(workerId, true), 0);
  assert.equal(attempt(hostId, true), 1);
  assert.equal(attempt(workerId, false), 1);
  execFileSync('/usr/bin/mount', ['-o', 'remount,hidepid=0', '/proc']);
  assert.equal(attempt(workerId, true), 1);
  await privacy.prepareLinuxProcessPrivacy(hostId, hostId);
  assert.equal(attempt(workerId, true), 0);
  console.log(JSON.stringify({ builtArtifact: true, rootRejected: true, idempotentSetup: true,
    isolatedWorkerAccepted: true, exemptGroupRejected: true, missingNoNewPrivsRejected: true,
    changedMountRejected: true, repairedMountAccepted: true, finalLauncherVerified: false }));
})().catch(error => { console.error(error); process.exitCode = 1; });
