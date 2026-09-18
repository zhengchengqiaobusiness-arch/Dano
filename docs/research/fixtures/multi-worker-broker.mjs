// Synthetic Linux acceptance. Run only in a disposable root container.
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, chmod, chown, writeFile, access, rm, symlink, readdir, readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { startWorkerBroker } from '../dano-server/bridge/start-worker-broker.js';
import { prepareLinuxProcessPrivacy } from '../dano-server/bridge/linux-process-privacy.js';

assert.equal(process.getuid(), 0);
const initialGid = process.getgid();
const [hostUid, hostGid, firstWorker] = process.argv.slice(2).map(Number);
assert([hostUid, hostGid, firstWorker].every(id => Number.isSafeInteger(id) && id > 0));
await prepareLinuxProcessPrivacy(hostUid, hostGid);
const root = await mkdtemp('/tmp/dano-multi-worker-');
await chmod(root, 0o711);
const clients = [];
const profiles = [];
async function workerPids(uid) {
  const results = await Promise.all((await readdir('/proc')).filter(name => /^\d+$/.test(name)).map(async pid => {
    const status = await readFile(`/proc/${pid}/status`, 'utf8').catch(() => '');
    return new RegExp(`^Uid:\\s+${uid}\\s`, 'm').test(status) ? pid : null;
  }));
  return results.filter(Boolean);
}
async function assertWorkerGone(uid) {
  const deadline = Date.now() + 2000;
  while ((await workerPids(uid)).length && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 20));
  assert.deepEqual(await workerPids(uid), []);
}
try {
  for (let i = 0; i < 2; i++) {
    const owner = join(root, `owner-${i}`);
    const workspace = join(owner, 'workspace');
    const agentDir = join(owner, 'private');
    await mkdir(owner, { mode: 0o711 });
    await mkdir(workspace, { mode: 0o770 });
    await chown(workspace, hostUid, firstWorker + i);
    await chmod(workspace, 0o770);
    await mkdir(agentDir, { mode: 0o700 });
    await chown(agentDir, hostUid, hostGid);
    await mkdir(join(workspace, '.pi'), { mode: 0o770 });
    await chown(join(workspace, '.pi'), firstWorker + i, firstWorker + i);
    await writeFile(join(workspace, '.pi/heimdall.json'), JSON.stringify({ sandbox: {
      enabled: true, userNamespace: false, paths: { [workspace]: { mode: 'write' } },
    } }));
    profiles.push({ workspace, agentDir, stateDir: agentDir, installationDir: '/app',
      hostUid, hostGid, workerUid: firstWorker + i, workerGid: firstWorker + i,
      piPackageContext: '/app/memory-extension/package.json', privilegeGuard: '/usr/bin/setpriv',
      path: process.env.PATH, startupTimeoutMs: 30000, operationTimeoutMs: 10000,
      shutdownTimeoutMs: 3000, maxConcurrentOperations: 4, maxResultBytes: 1048576 });
  }
  // Collect every outcome before cleanup, including when only one start fails.
  const starts = await Promise.allSettled(profiles.map(async profile => {
    const client = await startWorkerBroker(profile);
    clients.push(client);
    return client;
  }));
  for (const result of starts) if (result.status === 'rejected') throw result.reason;
  const [alice, bob] = starts.map(result => result.value);
  console.log('Both brokers ready');
  assert((await workerPids(firstWorker)).length > 0);
  assert((await workerPids(firstWorker + 1)).length > 0);
  assert.equal(process.getuid(), 0);
  assert.equal(process.getgid(), initialGid);
  await Promise.all([alice, bob].map((client, i) => client.execute('write', { path: 'owned.txt', content: `OWNER_${i}` })));
  await symlink(profiles[1].workspace, join(profiles[0].workspace, 'peer'));
  for (const [i, client] of [alice, bob].entries()) {
    const result = await client.execute('read', { path: 'owned.txt' });
    assert(result.content.some(part => part.text?.includes(`OWNER_${i}`)));
    let output = '';
    const identity = await client.execute('user_bash', { command: 'test ! -e /proc/1/cmdline && id -u && id -g' }, undefined,
      update => { output += Buffer.from(update.data, 'base64').toString(); });
    assert.equal(identity.exitCode, 0, output);
    assert.deepEqual(output.trim().split('\n'), [String(firstWorker + i), String(firstWorker + i)]);
    await assert.rejects(client.execute('read', { path: join(profiles[1 - i].workspace, 'owned.txt') }));
    await assert.rejects(client.execute('write', { path: join(profiles[1 - i].workspace, 'owned.txt'), content: 'CROSS_USER_WRITE' }));
  }
  await assert.rejects(alice.execute('read', { path: 'peer/owned.txt' }));
  for (const [i, client] of [alice, bob].entries()) {
    const result = await client.execute('read', { path: 'owned.txt' });
    assert(result.content.some(part => part.text?.includes(`OWNER_${i}`)));
  }
  const marker = join(profiles[0].workspace, 'late-write.txt');
  let started;
  const ready = new Promise(resolve => { started = resolve; });
  const pending = alice.execute('user_bash', {
    command: "setsid sh -c 'echo STARTED; sleep 3; printf escaped > late-write.txt' & wait",
  }, undefined, update => {
    if (Buffer.from(update.data, 'base64').toString().includes('STARTED')) started();
  });
  const rejected = assert.rejects(pending);
  await Promise.race([ready, pending.then(() => { throw new Error('Shell ended before close test'); })]);
  await alice.close();
  await rejected;
  await new Promise(resolve => setTimeout(resolve, 3500));
  await assert.rejects(access(marker), { code: 'ENOENT' });
  await assertWorkerGone(firstWorker);
  const surviving = await bob.execute('read', { path: 'owned.txt' });
  assert(surviving.content.some(part => part.text?.includes('OWNER_1')));
  await bob.close();
  await assertWorkerGone(firstWorker + 1);
  console.log(JSON.stringify({ parentUidPreserved: true, parentGidPreserved: true,
    distinctWorkerIdentities: true, peerReadsAndWritesDenied: true, peerSymlinkDenied: true, streamingShell: true,
    closeStopsDetachedWrite: true, noWorkerProcessesRemain: true,
    otherWorkerSurvivesClose: true, finalServerVerified: false }));
} finally {
  await Promise.allSettled(clients.map(client => client.close()));
  await rm(root, { recursive: true, force: true });
}
