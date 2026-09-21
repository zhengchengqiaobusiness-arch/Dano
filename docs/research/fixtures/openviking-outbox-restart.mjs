/** Real-service outbox recovery and owner isolation. Synthetic data only.
 * node fixture.mjs /absolute/extension/dist/host.js /isolated/ov.conf
 * Leaves a private evidence directory and synthetic remote account for audit.
 */
import assert from 'node:assert/strict';
import { fork } from 'node:child_process';
import { once } from 'node:events';
import { readFile, writeFile, mkdtemp } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { randomUUID } from 'node:crypto';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';

const [modulePath, configPath, mode, directory, accountId] = process.argv.slice(2);
assert(modulePath && configPath, 'Pass the extension host module and isolated ov.conf');
const { FileStateStore, MemoryDelivery, OwnerMemoryClient } = await import(pathToFileURL(modulePath));
const source = { sessionId: 'synthetic-chat', entryId: 'synthetic-entry', branchId: 'synthetic-branch', contentVersion: 'v1' };
const fact = '请记住：我的验收报告固定使用简体中文，报告末尾固定加上“晴川验收完毕”。';
const storeFor = (owner, root) => new FileStateStore({ owner, directory: root, policyVersion: 'probe-v1' });
if (mode === 'enqueue') {
  const owner = { accountId, userId: 'alice' };
  // This process has no client credentials or transport methods. Enqueue must be local.
  const delivery = new MemoryDelivery({ store: storeFor(owner, directory), transport: { owner }, maxPayloadBytes: 8192 });
  await delivery.enable('probe-v1');
  const operation = await delivery.save(source, fact);
  assert.equal(operation.phase, 'queued');
  setInterval(() => {}, 1000); // Keep the child alive until the parent injects SIGKILL.
  process.send({ queued: true, id: operation.id });
  await new Promise(() => {});
} else {
  const config = JSON.parse(await readFile(configPath, 'utf8'));
  assert(['127.0.0.1', 'localhost'].includes(config.server.host), 'Use an isolated loopback service');
  const baseUrl = `http://${config.server.host}:${config.server.port}`;
  const request = async (key, path, body, extra = {}) => {
    const response = await fetch(`${baseUrl}/api/v1${path}`, {
      method: body === undefined ? 'GET' : 'POST', redirect: 'error', signal: AbortSignal.timeout(30000),
      headers: { 'X-API-Key': key, 'Content-Type': 'application/json', ...extra },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    return { status: response.status, data: await response.json() };
  };
  const ok = async (...args) => {
    const result = await request(...args);
    assert.equal(result.status, 200, 'Expected successful isolated server request');
    assert.equal(result.data.status, 'ok');
    return result.data.result;
  };
  const account = `outbox-${randomUUID().replaceAll('-', '').slice(0, 16)}`;
  await ok(config.server.root_api_key, '/admin/accounts', { account_id: account, admin_user_id: 'admin' });
  const keys = {};
  for (const user of ['alice', 'bob']) {
    keys[user] = (await ok(config.server.root_api_key, `/admin/accounts/${account}/users`, { user_id: user, role: 'user' })).user_key;
  }
  const runRoot = await mkdtemp(join(tmpdir(), 'dano465-outbox-restart-'));
  const stateRoot = join(runRoot, 'alice-state');
  await writeFile(join(runRoot, 'credentials.json'), JSON.stringify({ account, keys }), { mode: 0o600 });
  const child = fork(fileURLToPath(import.meta.url), [modulePath, configPath, 'enqueue', stateRoot, account], { stdio: ['ignore', 'ignore', 'pipe', 'ipc'] });
  let stderr = '';
  child.stderr.on('data', data => { stderr += data; });
  const childExit = once(child, 'exit');
  let receipt;
  try {
    receipt = await Promise.race([
      once(child, 'message', { signal: AbortSignal.timeout(30000) }).then(([value]) => value),
      childExit.then(() => { throw new Error('Enqueue child exited before durable receipt'); }),
    ]);
    assert(receipt.queued);
  } finally { child.kill('SIGKILL'); }
  assert.equal((await childExit)[1], 'SIGKILL');
  assert.equal(stderr, '');
  const owner = userId => ({ accountId: account, userId });
  const client = userId => new OwnerMemoryClient({ owner: owner(userId), apiKey: keys[userId], baseUrl, timeoutMs: 30000 });
  const alice = client('alice'), bob = client('bob');
  const stateStore = storeFor(owner('alice'), stateRoot);
  assert.equal((await stateStore.read()).operations[receipt.id].phase, 'queued');
  await assert.rejects(storeFor(owner('bob'), stateRoot).read(), /INVALID_MEMORY_STATE/);
  assert.throws(() => new MemoryDelivery({ store: stateStore, transport: bob, maxPayloadBytes: 8192 }), /MEMORY_OWNER_MISMATCH/);
  const swapped = new OwnerMemoryClient({ owner: owner('alice'), apiKey: keys.bob, baseUrl, timeoutMs: 30000 });
  await assert.rejects(swapped.verifyIdentity(), /MEMORY_CREDENTIAL_OWNER_MISMATCH/);
  const delivery = new MemoryDelivery({ store: stateStore, transport: alice, maxPayloadBytes: 8192 });
  const deadline = Date.now() + 180000;
  let operation;
  while (Date.now() < deadline) {
    await delivery.advance(receipt.id);
    operation = (await stateStore.read()).operations[receipt.id];
    assert(!['failed', 'blocked', 'blocked_by_pause'].includes(operation.phase), `Unexpected delivery outcome: ${operation.phase}/${operation.errorCode}`);
    if (operation.phase === 'ready') break;
    await delay(operation.phase === 'processing' ? 2000 : 25);
  }
  assert.equal(operation.phase, 'ready');
  const found = await alice.recall('验收报告的语言和末尾固定文字', 5);
  assert(JSON.stringify(found).includes('晴川验收完毕'));
  assert.equal((await bob.recall('验收报告的语言和末尾固定文字', 5)).length, 0);
  const uri = operation.memoryUris[0];
  const before = await alice.readMemory(uri);
  const forged = { 'X-OpenViking-Account': account, 'X-OpenViking-User': 'alice' };
  const read = await request(keys.bob, `/content/read?uri=${encodeURIComponent(uri)}`, undefined, forged);
  const write = await request(keys.bob, '/content/write', { uri, content: 'SYNTHETIC_UNAUTHORIZED_REPLACE', mode: 'replace', wait: true }, forged);
  const search = await request(keys.bob, '/search/find', { query: '验收报告', target_uri: 'viking://user/alice/memories', limit: 5 }, forged);
  for (const response of [read, write, search]) assert([403, 404].includes(response.status));
  assert.equal(await alice.readMemory(uri), before);
  const duplicate = await delivery.save(source, fact);
  assert.equal(duplicate.id, receipt.id);
  assert.equal(duplicate.phase, 'ready');
  assert.equal(Object.keys((await stateStore.read()).operations).length, 1);
  const report = { durableEnqueueThenSigkill: true, reopenedOwnerState: true, wrongOwnerReplayRejected: true,
    swappedCredentialRejected: true, resumedReady: true, aliceRecallsFact: true, bobOwnScopeEmpty: true,
    foreignReadStatus: read.status, foreignWriteStatus: write.status, foreignSearchStatus: search.status,
    ownerContentUnchanged: true, repeatedSourceDidNotEnqueue: true, browserVerified: false };
  await writeFile(join(runRoot, 'result.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ runRoot, ...report }));
}
