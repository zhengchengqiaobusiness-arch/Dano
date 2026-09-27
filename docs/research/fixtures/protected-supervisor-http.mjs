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
const realMemoryService = process.argv.includes('--real-memory-service');
const rotateUserKey = process.argv.includes('--rotate-user-key');
const actualMemoryService = realService || realMemoryService;
// The remote service outlives disposable containers. Use new authenticated
// test identities so a prior run's extracted facts cannot satisfy or suppress
// this run's save/recall assertions.
const runIdentity = randomUUID();
const primaryUsers = actualMemoryService
  ? [`alice-fixture-${runIdentity}`, `bob-fixture-${runIdentity}`]
  : ['alice-fixture', 'bob-fixture'];
const memoryFailure = process.argv.includes('--memory-failure');
let memoryAccountId, corruptOwnerPath, realModel, rotation;
let memoryHttpBoundaryProbes = 0;
if (memoryFailure) assert(realService, 'Memory failure check requires the real model/service configuration');
if (realMemoryService) assert(withMemory && useCli, 'Real memory service mode requires --cli --memory');
if (rotateUserKey) assert(realMemoryService, 'USER key rotation requires --real-memory-service');
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
  options.memoryRecoveryDirectory = join(root, 'memory-recovery');
  await mkdir(options.memoryRecoveryDirectory, { mode: 0o700 });
  await chown(options.memoryRecoveryDirectory, hostUid, hostGid);
  const asset = async (name, value) => {
    const path = join(root, name), bytes = JSON.stringify(value); await writeFile(path, bytes, { mode: 0o644 });
    return { path, sha256: createHash('sha256').update(bytes).digest('hex') };
  };
  const config = realService ? JSON.parse(await readFile(process.env.DANO_FIXTURE_MEMORY_CONFIG, 'utf8')) : { version: 1, baseUrl: 'http://127.0.0.1:1', accountId: 'fixture', managementKey: 'SYNTHETIC_MANAGEMENT_KEY',
    encryptionKey: 'ab'.repeat(32), encryptionKeyVersion: 'v1', requestTimeoutMs: 100, maxContentBytes: 16384, policyVersion: 'v1',
    policy: { maxPayloadBytes: 4096, recallTimeoutMs: 1000, recallTokenBudget: 1500, recallLimit: 5, minimumScore: 0.5 },
    scheduler: { pollIntervalMs: 1000, initialBackoffMs: 1000, maxBackoffMs: 5000, maxAttemptsPerPhase: 5, maxOperationsPerTick: 4 },
    tokenizerLimits: { maxAssetBytes: 65536, maxInputBytes: 8192, startupTimeoutMs: 5000, maxQueuedRequests: 8 },
    tokenizers: [{ model: { provider: 'fixture', api: 'openai-completions', id: 'fixture' },
      tokenizer: await asset('tokenizer.json', { version: '1.0', added_tokens: [], normalizer: null,
        pre_tokenizer: { type: 'Whitespace' }, post_processor: null, decoder: null,
        model: { type: 'WordLevel', vocab: { '[UNK]': 0, hello: 1 }, unk_token: '[UNK]' } }),
      config: await asset('tokenizer_config.json', { tokenizer_class: 'PreTrainedTokenizerFast', unk_token: '[UNK]' }) }] };
  if (realMemoryService) {
    assert(process.env.DANO_FIXTURE_MEMORY_BASE_URL && process.env.DANO_FIXTURE_MEMORY_ACCOUNT_ID
      && process.env.DANO_FIXTURE_MEMORY_MANAGEMENT_KEY, 'Set real memory service connection');
    config.baseUrl = process.env.DANO_FIXTURE_MEMORY_BASE_URL;
    config.accountId = `${process.env.DANO_FIXTURE_MEMORY_ACCOUNT_ID}_${runIdentity.replaceAll('-', '')}`;
    config.managementKey = process.env.DANO_FIXTURE_MEMORY_MANAGEMENT_KEY;
    config.requestTimeoutMs = 5000;
  }
  const path = join(options.memoryConfigDirectory, 'memory-service.json');
  memoryAccountId = config.accountId;
  await writeFile(path, JSON.stringify(config), { mode: 0o600 }); await chown(path, hostUid, hostGid);
  if (realMemoryService) {
    const response = await fetch(`${config.baseUrl}/api/v1/admin/accounts`, {
      method: 'POST', headers: { 'X-API-Key': config.managementKey, 'Content-Type': 'application/json' },
      body: JSON.stringify({ account_id: config.accountId, admin_user_id: 'admin' }),
    });
    assert(response.ok, 'real OpenViking account setup failed');
  }
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
    const aliceId = clients[0].client.id;
    const memoryPath = `/api/clients/${aliceId}/memory`;
    const jobId = '00000000-0000-4000-8000-000000000001';
    const boundaryRoutes = [
      ['GET', '/settings'],
      ['PUT', '/settings', { enabled: false }],
      ['PUT', '/settings', { automaticCollection: false }],
      ['GET', '/operations'],
      ['GET', '/operations/nonexistent'],
      ['GET', '/operations/nonexistent/content/0'],
      ['GET', '/export'],
      ['GET', '/governance'],
      ['POST', '/governance', { action: 'invalid' }],
      ['GET', `/governance/${jobId}`],
      ['GET', `/governance/${jobId}/review`],
      ['POST', `/governance/${jobId}/review`, {}],
    ];
    for (const [method, suffix, body] of boundaryRoutes) {
      for (const [identity, authorization, expected] of [
        ['missing', undefined, 401],
        ['invalid', 'Bearer invalid-token', 401],
        ['foreign', `Bearer ${token(clients[1].id)}`, 403],
      ]) {
        const response = await fetch(`${origin}${memoryPath}${suffix}`, {
          method,
          headers: { ...(authorization ? { authorization } : {}),
            ...(body ? { 'content-type': 'application/json' } : {}),
            ...(identity === 'foreign' ? {
              'X-OpenViking-Account': memoryAccountId ?? 'forged-account',
              'X-OpenViking-User': clients[0].id,
            } : {}) },
          ...(body ? { body: JSON.stringify(body) } : {}),
        });
        assert.equal(response.status, expected, `${identity} ${method} ${suffix} crossed Dano memory boundary`);
        memoryHttpBoundaryProbes++;
      }
    }
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
    if (realMemoryService) {
      for (const entry of clients) {
        const exported = await fetch(`${origin}/api/clients/${entry.client.id}/memory/export`, {
          headers: { authorization: `Bearer ${token(entry.id)}` },
        });
        assert.equal(exported.status, 200, 'real memory export did not reach OpenViking');
        assert.equal(exported.headers.get('cache-control'), 'no-store');
        assert(!JSON.stringify(await exported.json()).includes(process.env.DANO_FIXTURE_MEMORY_MANAGEMENT_KEY));
      }
      const boundUsers = [];
      for (const entry of clients) {
        const boundUserId = `u_${createHash('sha256').update(JSON.stringify([memoryAccountId, entry.id])).digest('hex')}`;
        const response = await fetch(`${process.env.DANO_FIXTURE_MEMORY_BASE_URL}/api/v1/admin/accounts/${memoryAccountId}/users?name=${encodeURIComponent(boundUserId)}`, {
          headers: { 'X-API-Key': process.env.DANO_FIXTURE_MEMORY_MANAGEMENT_KEY },
        });
        assert.equal(response.status, 200);
        const payload = await response.json();
        const users = payload.result.filter(user => user.user_id === boundUserId && user.role === 'user');
        assert.equal(users.length, 1, 'Dano did not provision one real USER key per owner');
        const bound = await fetch(`${process.env.DANO_FIXTURE_MEMORY_BASE_URL}/health`, {
          headers: { 'X-API-Key': users[0].api_key },
        });
        assert.equal(bound.status, 200);
        const identity = await bound.json();
        assert.equal(identity.role, 'user');
        assert.equal(identity.user_id, boundUserId);
        assert.equal(identity.account_id, memoryAccountId);
        boundUsers.push({ userId: boundUserId, key: users[0].api_key });
      }
      const marker = `T11_${runIdentity.replaceAll('-', '')}`;
      const uri = `viking://user/${boundUsers[0].userId}/memories/isolated-fact.md`;
      const memoryBase = `${process.env.DANO_FIXTURE_MEMORY_BASE_URL}/api/v1`;
      const write = await fetch(`${memoryBase}/content/write`, { method: 'POST',
        headers: { 'X-API-Key': boundUsers[0].key, 'Content-Type': 'application/json' },
        body: JSON.stringify({ uri, content: `# Synthetic isolation fact\n${marker}\n`, mode: 'create', wait: true, timeout: 120 }) });
      assert.equal(write.status, 200, 'Alice memory write failed');
      const readUrl = `${memoryBase}/content/read?uri=${encodeURIComponent(uri)}&raw=true`;
      const ownRead = await fetch(readUrl, { headers: { 'X-API-Key': boundUsers[0].key } });
      assert.equal(ownRead.status, 200, 'Alice memory readback failed');
      assert((await ownRead.text()).includes(marker));
      const foreignRead = await fetch(readUrl, { headers: {
        'X-API-Key': boundUsers[1].key,
        'X-OpenViking-Account': memoryAccountId,
        'X-OpenViking-User': boundUsers[0].userId,
      } });
      assert.notEqual(foreignRead.status, 200, 'Bob read Alice memory with forged headers');
      assert(!(await foreignRead.text()).includes(marker));
      for (let index = 0; index < clients.length; index++) {
        const entry = clients[index];
        const response = await fetch(`${origin}/api/clients/${entry.client.id}/memory/export`, {
          headers: { authorization: `Bearer ${token(entry.id)}` },
        });
        assert.equal(response.status, 200);
        const containsAlice = JSON.stringify(await response.json()).includes(marker);
        assert.equal(containsAlice, index === 0, 'Dano export crossed owner boundary');
      }
      if (rotateUserKey) rotation = { userId: boundUsers[0].userId,
        oldKey: boundUsers[0].key, marker, uri };
    }
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
  if (rotateUserKey) {
    assert(rotation, 'REAL_USER_KEY_BINDING_MISSING');
    const response = await fetch(`${process.env.DANO_FIXTURE_MEMORY_BASE_URL}/api/v1/admin/accounts/${memoryAccountId}/users/${rotation.userId}/key`, {
      method: 'POST', headers: { 'X-API-Key': process.env.DANO_FIXTURE_MEMORY_MANAGEMENT_KEY },
    });
    assert.equal(response.status, 200, 'REAL_USER_KEY_ROTATION_FAILED');
    const newKey = (await response.json()).result?.user_key;
    assert(newKey && newKey !== rotation.oldKey, 'REAL_USER_KEY_UNCHANGED');
    const readUrl = `${process.env.DANO_FIXTURE_MEMORY_BASE_URL}/api/v1/content/read?uri=${encodeURIComponent(rotation.uri)}&raw=true`;
    const oldRead = await fetch(readUrl, {
      headers: { 'X-API-Key': rotation.oldKey },
    });
    assert([401, 403].includes(oldRead.status), `OLD_USER_KEY_STILL_ACTIVE_${oldRead.status}`);
    const rotatedRead = await fetch(readUrl, { headers: { 'X-API-Key': newKey } });
    assert.equal(rotatedRead.status, 200, 'NEW_USER_KEY_CONTENT_READ_FAILED');
    assert((await rotatedRead.text()).includes(rotation.marker), 'NEW_USER_KEY_CONTENT_MISSING');
    const helper = spawn('/usr/bin/setpriv', ['--reuid', String(hostUid), '--regid', String(hostGid),
      '--clear-groups', process.execPath, process.env.DANO_FIXTURE_ROTATE_HELPER ?? '/replace-key.mjs',
      options.memoryConfigDirectory, options.hostStateRoot, memoryAccountId, rotation.userId],
    { env: { PATH: process.env.PATH, DANO_FIXTURE_SERVER: serverDir }, stdio: ['pipe', 'ignore', 'ignore'] });
    const replaced = new Promise((resolve, reject) => {
      helper.once('error', reject); helper.once('close', code => resolve(code));
    });
    helper.stdin.end(`${newKey}\n`);
    assert.equal(await replaced, 0, 'PROTECTED_USER_KEY_REPLACEMENT_FAILED');

    const restarted = spawn(process.execPath, [join(serverDir, 'protected-main.js'), profilePath],
      { env: environment, stdio: ['ignore', 'inherit', 'inherit'] });
    let restartStopped = false;
    const restartServing = new Promise((resolve, reject) => {
      restarted.once('error', reject);
      restarted.once('close', code => { restartStopped = true; resolve(code ?? 1); });
    });
    try {
      const restartDeadline = Date.now() + 40000;
      let restartHealthy = false;
      while (Date.now() < restartDeadline && !restartStopped) {
        restartHealthy = await fetch(`${origin}/api/health`).then(r => r.ok).catch(() => false);
        if (restartHealthy) break;
        await delay(100);
      }
      assert(restartHealthy, 'PROTECTED_HOST_RESTART_FAILED');
      for (let index = 0; index < primaryUsers.length; index++) {
        const id = primaryUsers[index];
        const created = await fetch(`${origin}/api/clients`, { method: 'POST',
          headers: { authorization: `Bearer ${token(id)}`, 'content-type': 'application/json' }, body: '{}' });
        assert.equal(created.status, 201, 'RESTORED_USER_CLIENT_FAILED');
        const client = (await created.json()).client;
        const exported = await fetch(`${origin}/api/clients/${client.id}/memory/export`, {
          headers: { authorization: `Bearer ${token(id)}` },
        });
        assert.equal(exported.status, 200, 'RESTORED_USER_MEMORY_EXPORT_FAILED');
        assert.equal(JSON.stringify(await exported.json()).includes(rotation.marker), index === 0,
          'RESTORED_USER_MEMORY_ISOLATION_FAILED');
      }
    } finally {
      restarted.kill('SIGTERM');
      assert.equal(await restartServing, 0, 'PROTECTED_HOST_RESTART_SHUTDOWN_FAILED');
    }
  }
  console.log(JSON.stringify({ actualHttpHost: true, cliEntrypoint: useCli, hostNonRoot: true, twoWorkerIdentities: true,
    exclusiveSupervisor: true, searchNonRoot: true, memorySettingsVerified: withMemory,
    memoryHttpBoundaryVerified: withMemory, memoryHttpBoundaryProbes,
    memoryFailureVerified: memoryFailure,
    sequentialCapacityVerified: capacityMode,
    shutdownMode: crashHost ? 'host-killed' : crashSearch ? 'search-killed' : 'graceful',
    shutdownReclaimsChildren: true, browserVerified: false, modelVerified: realService,
    realMemoryServiceVerified: realMemoryService, realMemoryContentIsolationVerified: realMemoryService,
    danoHostRestartAfterRotationVerified: rotateUserKey }));
} finally {
  stop.abort();
  await serving.catch(() => {});
  if (useCli) await rm(profilePath);
  await rm(root, { recursive: true });
}
