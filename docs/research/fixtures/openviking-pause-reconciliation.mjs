/** Real loopback OpenViking, published extension, synthetic owners only.
 * Arguments: installed extension dist/host.js, isolated ov.conf.
 * Injects a lost response AFTER the real service mutation succeeds.
 * This is transport evidence, not browser or model-selection evidence.
 */
import assert from 'node:assert/strict';
import { readFile, writeFile, mkdtemp, chmod } from 'node:fs/promises';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';

const [modulePath, configPath] = process.argv.slice(2);
const { FileStateStore, MemoryDelivery, OwnerMemoryClient } = await import(pathToFileURL(modulePath));
const config = JSON.parse(await readFile(configPath, 'utf8'));
assert(['localhost', '127.0.0.1'].includes(config.server.host));
const baseUrl = `http://${config.server.host}:${config.server.port}`;
const root = await mkdtemp('/private/tmp/dano475-pause-reconciliation-');
await chmod(root, 0o700);
const accountId = `pause-${randomUUID().replaceAll('-', '').slice(0, 16)}`;
async function admin(path, body) {
  const response = await fetch(`${baseUrl}/api/v1${path}`, {
    method: 'POST', redirect: 'error', signal: AbortSignal.timeout(30000),
    headers: { 'X-API-Key': config.server.root_api_key, 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  assert.equal(response.status, 200, 'Isolated account provisioning failed');
  const data = await response.json(); assert.equal(data.status, 'ok'); return data.result;
}
await admin('/admin/accounts', { account_id: accountId, admin_user_id: 'admin' });
const credentials = {};
for (const userId of ['alice', 'bob']) {
  credentials[userId] = (await admin(`/admin/accounts/${accountId}/users`, { user_id: userId, role: 'user' })).user_key;
}
await writeFile(join(root, 'credentials.json'), JSON.stringify({ accountId, credentials }), { mode: 0o600 });
function ownerRuntime(userId, label, transportFactory = client => client) {
  const owner = { accountId, userId };
  const client = new OwnerMemoryClient({ owner, baseUrl, apiKey: credentials[userId], timeoutMs: 30000 });
  const store = new FileStateStore({ owner, directory: join(root, label), policyVersion: 'pause-probe-v1' });
  const delivery = new MemoryDelivery({ store, transport: transportFactory(client), maxPayloadBytes: 8192 });
  return { client, store, delivery };
}
const source = label => ({ sessionId: label, entryId: label, branchId: label, contentVersion: 'v1' });
function intercept(client, method, callback) {
  return new Proxy(client, { get(target, key) {
    if (key === method) return callback;
    const value = Reflect.get(target, key); return typeof value === 'function' ? value.bind(target) : value;
  } });
}
function gate() {
  let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve };
}
async function ready(runtime, id) {
  const deadline = Date.now() + 180000;
  while (Date.now() < deadline) {
    await runtime.delivery.advance(id);
    const op = (await runtime.store.read()).operations[id];
    if (op.phase === 'ready') return op;
    assert(!['failed', 'blocked_by_pause'].includes(op.phase), 'Unexpected terminal phase');
    await delay(500);
  }
  throw new Error('REAL_SERVICE_READY_TIMEOUT');
}

// Pause wins before a send is claimed: not even a remote session is created.
let mutations = 0;
const unsent = ownerRuntime('alice', 'unsent', client => intercept(client, 'createSession', async id => {
  mutations++; return client.createSession(id);
}));
await unsent.delivery.enable('pause-probe-v1');
const queued = await unsent.delivery.save(source('unsent'), '我的合成验收周报标题固定为“竹汀周报”。');
await unsent.delivery.pause(); await unsent.delivery.advance(queued.id);
assert.equal(mutations, 0);
assert.equal((await unsent.store.read()).operations[queued.id].phase, 'blocked_by_pause');
assert.equal((await unsent.store.read()).operations[queued.id].payload, undefined);

// A real append has happened, then pause wins before its response is received.
const appended = gate(), releaseAppend = gate(); let appendCalls = 0;
const appending = ownerRuntime('alice', 'append', client => intercept(client, 'append', async operation => {
  appendCalls++; await client.append(operation); appended.resolve(); await releaseAppend.promise;
  throw new Error('SYNTHETIC_LOST_APPEND_RESPONSE');
}));
await appending.delivery.enable('pause-probe-v1');
const message = await appending.delivery.save(source('append'), '我的合成验收月报标题固定为“云杉月报”。');
await appending.delivery.advance(message.id);
const appendFlight = appending.delivery.advance(message.id);
await Promise.race([appended.promise, delay(35000).then(() => { throw new Error('APPEND_GATE_TIMEOUT'); })]);
await appending.delivery.pause(); releaseAppend.resolve(); await appendFlight;
assert.equal((await appending.store.read()).operations[message.id].phase, 'message_unknown');
const reopenedAppend = ownerRuntime('alice', 'append');
await reopenedAppend.delivery.advance(message.id);
assert.equal((await reopenedAppend.store.read()).operations[message.id].phase, 'blocked_by_pause');
assert.equal((await reopenedAppend.store.read()).operations[message.id].payload, undefined);
await reopenedAppend.delivery.enable('pause-probe-v1'); await reopenedAppend.delivery.advance(message.id);
assert.equal((await reopenedAppend.store.read()).operations[message.id].phase, 'blocked_by_pause');
assert.equal(appendCalls, 1);
assert.equal(await reopenedAppend.client.findCommit(message.remoteSessionId), null);

// A real commit is already in flight: pause cannot pretend it was cancelled.
const committed = gate(), releaseCommit = gate(); let commitCalls = 0;
const committing = ownerRuntime('alice', 'commit', client => intercept(client, 'commit', async id => {
  commitCalls++; await client.commit(id); committed.resolve(); await releaseCommit.promise;
  throw new Error('SYNTHETIC_LOST_COMMIT_RESPONSE');
}));
await committing.delivery.enable('pause-probe-v1');
const commitOp = await committing.delivery.save(source('commit'), '我的合成验收季报标题固定为“雪桐季报”。');
await committing.delivery.advance(commitOp.id); await committing.delivery.advance(commitOp.id);
const commitFlight = committing.delivery.advance(commitOp.id);
await Promise.race([committed.promise, delay(35000).then(() => { throw new Error('COMMIT_GATE_TIMEOUT'); })]);
await committing.delivery.pause(); releaseCommit.resolve(); await commitFlight;
assert.equal((await committing.store.read()).operations[commitOp.id].phase, 'commit_unknown');
const reopenedCommit = ownerRuntime('alice', 'commit');
const bob = ownerRuntime('bob', 'bob'); await bob.delivery.enable('pause-probe-v1');
const bobOp = await bob.delivery.save(source('bob'), '我的合成验收简报标题固定为“银杏简报”。');
await Promise.all([ready(reopenedCommit, commitOp.id), ready(bob, bobOp.id)]);
assert.equal((await reopenedCommit.store.read()).authorization.enabled, false);
assert.equal(commitCalls, 1);
assert.equal((await reopenedCommit.store.read()).operations[commitOp.id].payload, undefined);
const bobResults = JSON.stringify(await bob.client.recall('合成验收报告标题', 10));
assert(bobResults.includes('银杏简报')); assert(!bobResults.includes('雪桐季报'));
const report = { pauseBeforeClaimHasNoMutation: true, unsentBodyCleared: true,
  appendResponseLostThenReadOnlyReconciled: true, resumeDoesNotReplay: true,
  commitResponseLostThenReadyWhilePaused: true, appendCalls, commitCalls,
  concurrentBobReadyWithoutAliceFact: true, reopenedFileStores: true,
  processKillVerified: false, browserVerified: false };
await writeFile(join(root, 'result.json'), JSON.stringify(report, null, 2), { mode: 0o600 });
console.log(JSON.stringify({ root, ...report }));
