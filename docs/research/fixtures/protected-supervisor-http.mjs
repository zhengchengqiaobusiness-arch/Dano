// Development contract check in a disposable Linux container. Not browser/model acceptance.
import assert from 'node:assert/strict';
import { mkdtemp, chmod, writeFile, readdir, readFile, rm } from 'node:fs/promises';
import { createHmac } from 'node:crypto';
import { join } from 'node:path';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import { runProtectedSupervisor } from '../dano-server/bridge/protected-supervisor.js';
const root = await mkdtemp('/tmp/dano-supervisor-http-');
await chmod(root, 0o711);
await writeFile(join(root, 'dano.config.json'), '{}\n', { mode: 0o644 });
const hostUid = 1000, hostGid = 1000;
const options = {
  runtimeRoot: join(root, 'runtime'), sessionsRoot: join(root, 'sessions'), hostStateRoot: join(root, 'host-state'),
  identities: { directory: join(root, 'identities'), firstUid: 10001, firstGid: 10001, count: 4, lockTimeoutMs: 5000 },
  maxWorkers: 4,
  broker: { installationDir: '/app/memory-extension', hostUid, hostGid,
    piPackageContext: '/app/memory-extension/package.json', privilegeGuard: '/usr/bin/setpriv',
    path: process.env.PATH, startupTimeoutMs: 30000, operationTimeoutMs: 10000,
    shutdownTimeoutMs: 5000, maxConcurrentOperations: 4, maxResultBytes: 1048576 },
  host: { hostUid, hostGid, startupTimeoutMs: 30000, operationTimeoutMs: 40000,
    maxConcurrentOperations: 8, maxMessageBytes: 1048576, trustedSkillPaths: [],
    providerPythonModuleDirectory: '/app/memory-extension/dano-server/python' },
};
const environment = { PATH: process.env.PATH, NODE_ENV: 'test', HOME: options.runtimeRoot,
  DANO_HOST: '127.0.0.1', DANO_PORT: '18710', DANO_PRODUCT_NAME: 'Supervisor Fixture', DANO_CONFIG_PATH: join(root, 'dano.config.json'),
  DANO_AUTH_JWT_SECRET: 'synthetic-supervisor-http-test-key' };
const crashHost = process.argv.includes('--crash-host');
const useCli = process.argv.includes('--cli');
const profilePath = '/etc/dano-supervisor-fixture.json';
if (useCli) await writeFile(profilePath, JSON.stringify(options), { mode: 0o600, flag: 'wx' });
const stop = new AbortController();
let finished = false, failure;
const launch = () => {
  if (!useCli) return runProtectedSupervisor(options, environment, [], stop.signal);
  const child = spawn(process.execPath, ['/app/memory-extension/dano-server/protected-main.js', profilePath],
    { env: environment, stdio: ['ignore', 'inherit', 'inherit'] });
  stop.signal.addEventListener('abort', () => child.kill('SIGTERM'), { once: true });
  return new Promise((resolve, reject) => { child.once('error', reject); child.once('close', code => resolve(code ?? 1)); });
};
const serving = launch().then(code => {
  finished = true; return code;
}, error => { finished = true; failure = error; throw error; });
void serving.catch(() => {});
const origin = 'http://127.0.0.1:18710';
async function identities() {
  const records = await Promise.all((await readdir('/proc')).filter(name => /^\d+$/.test(name)).map(async pid => {
    try {
      const status = await readFile(`/proc/${pid}/status`, 'utf8');
      const cmdline = await readFile(`/proc/${pid}/cmdline`, 'utf8');
      return { pid, uid: Number(/^Uid:\s+(\d+)/m.exec(status)?.[1]), cmdline };
    } catch { return null; }
  }));
  return records.filter(Boolean);
}
function token(id) {
  const enc = value => Buffer.from(JSON.stringify(value)).toString('base64url');
  const unsigned = `${enc({ alg: 'HS256', typ: 'JWT' })}.${enc({ sub: id, exp: Math.floor(Date.now()/1000)+120 })}`;
  return `${unsigned}.${createHmac('sha256', environment.DANO_AUTH_JWT_SECRET).update(unsigned).digest('base64url')}`;
}
try {
  const deadline = Date.now() + 40000;
  let healthy = false;
  while (Date.now() < deadline && !finished) {
    healthy = await fetch(`${origin}/api/health`).then(r => r.ok).catch(() => false);
    if (healthy) break;
    await delay(100);
  }
  if (failure) throw failure;
  assert(healthy, 'protected HTTP host did not start');
  await assert.rejects(runProtectedSupervisor(options, environment), /SUPERVISOR_ALREADY_RUNNING/);
  for (const id of ['alice-fixture', 'bob-fixture']) {
    const response = await fetch(`${origin}/api/clients`, { method: 'POST',
      headers: { authorization: `Bearer ${token(id)}`, 'content-type': 'application/json' }, body: '{}' });
    assert.equal(response.status, 201, await response.text());
  }
  const live = await identities();
  assert(live.some(p => p.uid === hostUid && p.cmdline.includes('/protected-host-entry.js')));
  assert(live.some(p => p.uid === 10001));
  assert(live.some(p => p.uid === 10002));
  if (crashHost) {
    const host = live.find(p => p.uid === hostUid && p.cmdline.includes('/protected-host-entry.js'));
    process.kill(Number(host.pid), 'SIGKILL');
  } else stop.abort();
  assert.equal(await serving, crashHost ? 1 : 0);
  const remaining = (await identities()).filter(p => [10001, 10002].includes(p.uid)
    || p.cmdline.includes('/protected-host-entry.js') || p.cmdline.includes('/worker-broker-entry.js'));
  assert.deepEqual(remaining, []);
  console.log(JSON.stringify({ actualHttpHost: true, cliEntrypoint: useCli, hostNonRoot: true, twoWorkerIdentities: true,
    exclusiveSupervisor: true, shutdownMode: crashHost ? 'host-killed' : 'graceful', shutdownReclaimsChildren: true, browserVerified: false, modelVerified: false }));
} finally {
  stop.abort();
  await serving.catch(() => {});
  if (useCli) await rm(profilePath);
  await rm(root, { recursive: true });
}
