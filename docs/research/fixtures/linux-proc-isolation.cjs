// Run only inside a disposable root Linux container with a host identity and two distinct worker identities.
const { spawn, execFileSync } = require('node:child_process');
const { mkdtempSync, chmodSync, symlinkSync, rmSync } = require('node:fs');
const assert = require('node:assert/strict');
const [hostId, ownerUid, otherUid] = process.argv.slice(-3).map(Number);
assert.equal(process.getuid(), 0);
assert([hostId, ownerUid, otherUid].every(id => Number.isSafeInteger(id) && id > 0));
assert.equal(new Set([hostId, ownerUid, otherUid]).size, 3);
const directory = mkdtempSync('/tmp/dano-hidepid-');
chmodSync(directory, 0o755);
const target = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)', 'SYNTHETIC_PROVIDER_CAPABILITY'],
  { uid: ownerUid, gid: ownerUid, stdio: 'ignore' });
const link = directory + '/cmdline-link';
symlinkSync(`/proc/${target.pid}/cmdline`, link);
const probe = uid => JSON.parse(execFileSync(process.execPath, ['-e', `
const fs=require('node:fs');
const paths=${JSON.stringify([`/proc/${target.pid}/cmdline`, link])};
console.log(JSON.stringify(paths.map(path => { try {return fs.readFileSync(path).includes('SYNTHETIC_PROVIDER_CAPABILITY');} catch {return false;} })));
`], { uid, gid: uid, encoding: 'utf8' }));
try {
  assert.deepEqual(probe(otherUid), [true, true]);
  execFileSync('mount', ['-o', `remount,hidepid=2,gid=${hostId}`, '/proc']);
  assert.deepEqual(probe(otherUid), [false, false]);
  assert.deepEqual(probe(ownerUid), [true, true]);
  assert.deepEqual(probe(hostId), [true, true]);
  console.log(JSON.stringify({ crossUidCmdlineVisibleBefore: true, crossUidCmdlineDeniedAfter: true,
    symlinkDeniedAfter: true, ownUidStillWorks: true, trustedHostInspection: true, hidepid: 2, finalLauncherVerified: false }));
} finally {
  target.kill();
  rmSync(directory, { recursive: true });
}
