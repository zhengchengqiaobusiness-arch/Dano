// Disposable Linux contract check. Optional --real-service performs model calls;
// synthetic JWT authentication never establishes OAuth/browser acceptance.
import assert from 'node:assert/strict';
import { mkdtemp, chmod, chown, mkdir, writeFile, readdir, readFile, rm } from 'node:fs/promises';
import { createHash, createHmac, randomUUID } from 'node:crypto';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
const installationDir = process.env.DANO_FIXTURE_INSTALLATION ?? '/app/memory-extension';
const serverDir = process.env.DANO_FIXTURE_SERVER ?? join(installationDir, 'dano-server');
const { runProtectedSupervisor } = await import(pathToFileURL(join(serverDir, 'bridge/protected-supervisor.js')).href);
const root = await mkdtemp('/tmp/dano-supervisor-http-');
await chmod(root, 0o711);
await writeFile(join(root, 'dano.config.json'), '{}\n', { mode: 0o644 });
const hostUid = 1000, hostGid = 1000;
const capacityMode = process.argv.includes('--worker-capacity');
const options = {
  runtimeRoot: join(root, 'runtime'), sessionsRoot: join(root, 'sessions'), hostStateRoot: join(root, 'host-state'),
  identities: { directory: join(root, 'identities'), firstUid: 10001, firstGid: 10001, count: capacityMode ? 12 : 4, lockTimeoutMs: 5000 },
  maxWorkers: capacityMode ? 2 : 4,
  broker: { installationDir, hostUid, hostGid,
    piPackageContext: join(installationDir, 'package.json'), privilegeGuard: '/usr/bin/setpriv',
    path: process.env.PATH, startupTimeoutMs: 30000, operationTimeoutMs: 10000,
    shutdownTimeoutMs: 5000, maxConcurrentOperations: 4, maxResultBytes: 1048576 },
  host: { hostUid, hostGid, startupTimeoutMs: 30000, operationTimeoutMs: 40000,
    maxConcurrentOperations: 8, maxMessageBytes: 1048576, trustedSkillPaths: [],
    providerPythonModuleDirectory: join(serverDir, 'python') },
};
const environment = { PATH: process.env.PATH, NODE_ENV: 'test', HOME: options.runtimeRoot,
  DANO_HOST: '127.0.0.1', DANO_PORT: '18710', DANO_PRODUCT_NAME: 'Supervisor Fixture', DANO_CONFIG_PATH: join(root, 'dano.config.json'),
  DANO_AUTH_JWT_SECRET: 'synthetic-supervisor-http-test-key' };
const crashHost = process.argv.includes('--crash-host');
const crashSearch = process.argv.includes('--crash-search');
const useCli = process.argv.includes('--cli');
const withMemory = process.argv.includes('--memory');
const realService = process.argv.includes('--real-service');
// The remote service outlives disposable containers. Use new authenticated
// test identities so a prior run's extracted facts cannot satisfy or suppress
// this run's save/recall assertions.
const runIdentity = randomUUID();
const primaryUsers = realService
  ? [`alice-fixture-${runIdentity}`, `bob-fixture-${runIdentity}`]
  : ['alice-fixture', 'bob-fixture'];
const memoryFailure = process.argv.includes('--memory-failure');
let memoryAccountId, corruptOwnerPath, realModel;
if (memoryFailure) assert(realService, 'Memory failure check requires the real model/service configuration');
if (realService) {
  assert(withMemory && useCli, 'Real service mode requires --cli --memory');
  realModel = { provider: process.env.DANO_FIXTURE_PROVIDER, modelId: process.env.DANO_FIXTURE_MODEL };
  assert(realModel.provider && realModel.modelId, 'Set DANO_FIXTURE_PROVIDER and DANO_FIXTURE_MODEL');
  const modelsBytes = await readFile(process.env.DANO_FIXTURE_MODELS);
  const providerConfig = JSON.parse(modelsBytes).providers?.[realModel.provider];
  assert(providerConfig?.models?.some(model => model.id === realModel.modelId), 'Selected model is absent from fixture models.json');
  // Forward only the selected provider's declared environment credential.
  // Never copy the whole process environment into the fixture host.
  const keyReference = /^\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))$/.exec(providerConfig.apiKey ?? '');
  if (keyReference) {
    const name = keyReference[1] ?? keyReference[2];
    assert(process.env[name], 'Selected model credential environment is missing');
    environment[name] = process.env[name];
  }
  const agentDir = join(root, 'host-agent');
  await mkdir(agentDir, { mode: 0o700 }); await chown(agentDir, hostUid, hostGid);
  const modelsPath = join(agentDir, 'models.json');
  await writeFile(modelsPath, modelsBytes, { mode: 0o600 });
  await chown(modelsPath, hostUid, hostGid);
  environment.PI_CODING_AGENT_DIR = agentDir;
  environment.NODE_EXTRA_CA_CERTS = process.env.NODE_EXTRA_CA_CERTS;
}
if (withMemory) {
  options.memoryConfigDirectory = join(root, 'private-config');
  await mkdir(options.memoryConfigDirectory, { mode: 0o700 });
  await chown(options.memoryConfigDirectory, hostUid, hostGid);
  const asset = async (name, value) => {
    const path = join(root, name), bytes = JSON.stringify(value); await writeFile(path, bytes, { mode: 0o644 });
    return { path, sha256: createHash('sha256').update(bytes).digest('hex') };
  };
  const config = realService ? JSON.parse(await readFile(process.env.DANO_FIXTURE_MEMORY_CONFIG, 'utf8')) : { version: 1, baseUrl: 'http://127.0.0.1:1', accountId: 'fixture', managementKey: 'SYNTHETIC_MANAGEMENT_KEY',
    encryptionKey: 'ab'.repeat(32), encryptionKeyVersion: 'v1', requestTimeoutMs: 100, maxContentBytes: 16384, policyVersion: 'v1',
    policy: { maxPayloadBytes: 4096, recallTimeoutMs: 1000, recallTokenBudget: 1500, recallLimit: 5, minimumScore: 0.5 },
    scheduler: { pollIntervalMs: 1000, initialBackoffMs: 1000, maxBackoffMs: 5000, maxAttemptsPerPhase: 5, maxOperationsPerTick: 4 },
    tokenizerLimits: { maxAssetBytes: 65536, maxInputBytes: 8192, startupTimeoutMs: 5000 },
    tokenizers: [{ model: { provider: 'fixture', api: 'openai-completions', id: 'fixture' },
      tokenizer: await asset('tokenizer.json', { version: '1.0', added_tokens: [], normalizer: null,
        pre_tokenizer: { type: 'Whitespace' }, post_processor: null, decoder: null,
        model: { type: 'WordLevel', vocab: { '[UNK]': 0, hello: 1 }, unk_token: '[UNK]' } }),
      config: await asset('tokenizer_config.json', { tokenizer_class: 'PreTrainedTokenizerFast', unk_token: '[UNK]' }) }] };
  const path = join(options.memoryConfigDirectory, 'memory-service.json');
  memoryAccountId = config.accountId;
  await writeFile(path, JSON.stringify(config), { mode: 0o600 }); await chown(path, hostUid, hostGid);
}
const profilePath = '/etc/dano-supervisor-fixture.json';
if (useCli) await writeFile(profilePath, JSON.stringify(options), { mode: 0o600, flag: 'wx' });
const stop = new AbortController();
let finished = false, failure;
const launch = () => {
  if (!useCli) return runProtectedSupervisor(options, environment, [], stop.signal);
  const child = spawn(process.execPath, [join(serverDir, 'protected-main.js'), profilePath],
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
  if (memoryFailure) {
    const directory = join(options.hostStateRoot, 'memory-service', 'owners');
    await mkdir(directory, { recursive: true, mode: 0o700 }); await chown(directory, hostUid, hostGid);
    const digest = createHash('sha256').update(JSON.stringify([memoryAccountId, primaryUsers[0]])).digest('hex');
    corruptOwnerPath = join(directory, `${digest}.json`);
    await writeFile(corruptOwnerPath, '{damaged', { mode: 0o600 }); await chown(corruptOwnerPath, hostUid, hostGid);
  }
  const clients = [];
  for (const id of primaryUsers) {
    const response = await fetch(`${origin}/api/clients`, { method: 'POST',
      headers: { authorization: `Bearer ${token(id)}`, 'content-type': 'application/json' }, body: '{}' });
    assert.equal(response.status, 201);
    clients.push({ id, ...await response.json() });
  }
  if (capacityMode) {
    for (let index = 0; index < 6; index++) {
      const id = `capacity-user-${index}`;
      const headers = { authorization: `Bearer ${token(id)}`, 'content-type': 'application/json' };
      const response = await fetch(`${origin}/api/clients`, { method: 'POST', headers, body: '{}' });
      assert.equal(response.status, 201, 'sequential user could not acquire an idle worker slot');
      const entry = await response.json();
      const disconnected = await fetch(`${origin}/api/clients/${entry.client.id}/disconnect`, {
        method: 'POST', headers, body: '{}' });
      assert.equal(disconnected.status, 202);
    }
  }
  if (withMemory) {
    const settings = async (entry, enabled) => {
      const response = await fetch(`${origin}/api/clients/${entry.client.id}/memory/settings`, {
        method: enabled === undefined ? 'GET' : 'PUT',
        headers: { authorization: `Bearer ${token(entry.id)}`, 'content-type': 'application/json' },
        ...(enabled === undefined ? {} : { body: JSON.stringify({ enabled }) }) });
      assert.equal(response.status, 200); return response.json();
    };
    for (const entry of clients) {
      if (memoryFailure && entry === clients[0]) {
        const response = await fetch(`${origin}/api/clients/${entry.client.id}/memory/settings`, {
          headers: { authorization: `Bearer ${token(entry.id)}` } });
        assert.equal(response.status, 503);
        continue;
      }
      const state = await settings(entry); assert.equal(state.enabled, false); assert.equal(state.automaticCollection, false);
    }
    if (!memoryFailure) assert.equal((await settings(clients[0], true)).enabled, true);
    assert.equal((await settings(clients[1])).enabled, false);
    if (realService) {
      const { verifyMemoryHttpFlow } = await import('./protected-memory-http-flow.mjs');
      await verifyMemoryHttpFlow({ origin, clients, token, model: realModel, memoryUnavailable: memoryFailure });
    }
    if (!memoryFailure) assert.equal((await settings(clients[0], false)).enabled, false);
    else assert.equal(await readFile(corruptOwnerPath, 'utf8'), '{damaged');
    const foreign = await fetch(`${origin}/api/clients/${clients[0].client.id}/memory/settings`, {
      headers: { authorization: `Bearer ${token(clients[1].id)}` } });
    assert.equal(foreign.status, 403);
  }
  const live = await identities();
  assert(live.some(p => p.uid === hostUid && p.cmdline.includes('/protected-host-entry.js')));
  const search = live.filter(p => p.cmdline.includes('open-websearch') && p.cmdline.includes('serve'));
  assert(search.length > 0, 'managed search daemon missing');
  assert(search.every(p => p.uid === hostUid), 'search daemon must run as the non-root host');
  const workerUid = uid => uid >= options.identities.firstUid && uid < options.identities.firstUid + options.identities.count;
  if (capacityMode) {
    const resident = new Set(live.filter(p => workerUid(p.uid)).map(p => p.uid));
    assert(resident.size > 0 && resident.size <= options.maxWorkers, 'resident worker capacity exceeded');
  } else {
    assert(live.some(p => p.uid === 10001));
    assert(live.some(p => p.uid === 10002));
  }
  if (crashHost) {
    const host = live.find(p => p.uid === hostUid && p.cmdline.includes('/protected-host-entry.js'));
    process.kill(Number(host.pid), 'SIGKILL');
  } else if (crashSearch) {
    process.kill(Number(search[0].pid), 'SIGKILL');
  } else stop.abort();
  assert.equal(await serving, crashHost || crashSearch ? 1 : 0);
  let remaining;
  const cleanupDeadline = Date.now() + 5000;
  do {
    remaining = (await identities()).filter(p => workerUid(p.uid)
      || p.cmdline.includes('/protected-host-entry.js') || p.cmdline.includes('/worker-broker-entry.js')
      || (p.cmdline.includes('open-websearch') && p.cmdline.includes('serve')));
    if (!remaining.length) break;
    await delay(50);
  } while (Date.now() < cleanupDeadline);
  assert.deepEqual(remaining, []);
  console.log(JSON.stringify({ actualHttpHost: true, cliEntrypoint: useCli, hostNonRoot: true, twoWorkerIdentities: true,
    exclusiveSupervisor: true, searchNonRoot: true, memorySettingsVerified: withMemory, memoryFailureVerified: memoryFailure,
    sequentialCapacityVerified: capacityMode,
    shutdownMode: crashHost ? 'host-killed' : crashSearch ? 'search-killed' : 'graceful',
    shutdownReclaimsChildren: true, browserVerified: false, modelVerified: realService }));
} finally {
  stop.abort();
  await serving.catch(() => {});
  if (useCli) await rm(profilePath);
  await rm(root, { recursive: true });
}
