/** Real clear coordinator with synthetic owners/scopes; not browser acceptance. */
import assert from 'node:assert/strict';
import { readFile, mkdtemp, writeFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
const [modulePath, configPath] = process.argv.slice(2);
const { OwnerMemoryClient, MemoryDelivery, FileStateStore, MemoryGovernanceBarrier, MemoryClearCoordinator } = await import(pathToFileURL(modulePath));
const config = JSON.parse(await readFile(configPath, 'utf8'));
assert(['127.0.0.1', 'localhost'].includes(config.server.host));
const baseUrl = `http://${config.server.host}:${config.server.port}`;
const root = await mkdtemp('/private/tmp/dano476-clear-coordinator-');
const accountId = `clear-${randomUUID().slice(0, 8)}`;
async function admin(path, body) {
  const response = await fetch(`${baseUrl}/api/v1${path}`, { method: 'POST', redirect: 'error', signal: AbortSignal.timeout(30000),
    headers: { 'X-API-Key': config.server.root_api_key, 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  assert.equal(response.status, 200); const data = await response.json(); assert.equal(data.status, 'ok'); return data.result;
}
await admin('/admin/accounts', { account_id: accountId, admin_user_id: 'admin' });
const keys = {};
for (const userId of ['alice', 'bob']) keys[userId] = (await admin(`/admin/accounts/${accountId}/users`, { user_id: userId, role: 'user' })).user_key;
await writeFile(`${root}/credentials.json`, JSON.stringify({ accountId, keys }), { mode: 0o600 });
function runtime(userId, scope = null, sharedStore) {
  const owner = { accountId, userId };
  const client = new OwnerMemoryClient({ owner, scope, baseUrl, apiKey: keys[userId], timeoutMs: 90000 });
  const inspect = client.inspect.bind(client); const errors = new Set();
  client.inspect = async operation => {
    try { return await inspect(operation); }
    catch (error) {
      const code = /^MEMORY_[A-Z_]+$/.test(error.message) ? error.message : 'UNCLASSIFIED_TRANSPORT_ERROR';
      if (!errors.has(code)) { errors.add(code); console.log(JSON.stringify({ phase: 'inspect-error', scope, code })); }
      throw error;
    }
  };
  const store = sharedStore ?? new FileStateStore({ owner, directory: `${root}/${userId}`, policyVersion: 'clear-probe' });
  const delivery = new MemoryDelivery({ store, transport: client, maxPayloadBytes: 8192 });
  return { client, store, delivery, scope };
}
const alice = runtime('alice'), project = runtime('alice', 'project-a', alice.store), bob = runtime('bob');
await alice.delivery.enable('clear-probe'); await bob.delivery.enable('clear-probe');
async function save(runtime, entryId, title) {
  return runtime.delivery.save({ sessionId: entryId, entryId, branchId: entryId, contentVersion: 'v1' },
    `我的长期偏好：每月报告总结标题固定为“${title}”。`, runtime.scope);
}
async function ready(runtime, operation) {
  for (const deadline = Date.now() + 180000; Date.now() < deadline;) {
    await runtime.delivery.advance(operation.id);
    const current = (await runtime.store.read()).operations[operation.id];
    assert(!['failed', 'blocked'].includes(current.phase));
    if (current.phase === 'ready') return current;
    await delay(500);
  }
  throw new Error('REAL_EXTRACTION_TIMEOUT');
}
const projectOp = await save(project, 'project-source', '杉溪项目小结');
const bobOp = await save(bob, 'bob-source', '榆湾用户小结');
await Promise.all([ready(project, projectOp), ready(bob, bobOp)]);
assert(!JSON.stringify(await alice.client.recall('每月报告总结标题', 10)).includes('杉溪项目小结'));
const old = await save(alice, 'old-global-source', '青岚旧版小结');
await alice.delivery.advance(old.id); await alice.delivery.advance(old.id); await alice.delivery.advance(old.id);
const processing = (await alice.store.read()).operations[old.id];
assert.equal(processing.phase, 'processing');
const wasInFlight = !await alice.client.writerSettled(processing);
assert(wasInFlight, 'Fixture requires an actual in-flight extraction');
const job = await new MemoryGovernanceBarrier(alice.store).begin({ kind: 'clear', scope: null });
await alice.delivery.pause(); // Management clear must still work while paused.
const coordinator = new MemoryClearCoordinator(alice.store, alice.client);
let complete = false, sawPending = false;
for (const deadline = Date.now() + 180000; Date.now() < deadline;) {
  const result = await coordinator.advance(job.id);
  if (result.status === 'complete') { complete = true; break; }
  sawPending = true; await delay(500);
}
assert(complete, 'REAL_CLEAR_TIMEOUT');
assert.equal(await alice.client.sessionExists(old.remoteSessionId), false);
const hits = await alice.client.recall('每月报告总结标题', 10);
assert(!JSON.stringify(hits).includes('青岚旧版小结'));
assert(JSON.stringify(await project.client.recall('每月报告总结标题', 10)).includes('杉溪项目小结'));
assert(JSON.stringify(await bob.client.recall('每月报告总结标题', 10)).includes('榆湾用户小结'));
await alice.delivery.enable('clear-probe');
const replay = await alice.delivery.save({ sessionId: 'fork', entryId: 'old-global-source', branchId: 'fork', contentVersion: 'v1' }, '青岚旧版小结');
assert.equal(replay.errorCode, 'MEMORY_SOURCE_REVOKED');
await ready(alice, await save(alice, 'new-explicit-source', '青岚旧版小结'));
assert(JSON.stringify(await alice.client.recall('每月报告总结标题', 10)).includes('青岚旧版小结'));
assert.equal((await coordinator.advance(job.id)).status, 'complete');
assert(JSON.stringify(await alice.client.recall('每月报告总结标题', 10)).includes('青岚旧版小结'));
const result = { root, realInFlightExtraction: wasInFlight, sawPending, clearWhilePaused: true,
  sourceRemoved: true, forgottenRecallAbsent: true, projectPreserved: true, otherUserPreserved: true,
  oldForkBlocked: true, explicitResaveAllowed: true, completedJobDoesNotDeleteNewVersion: true, browserVerified: false };
await writeFile(`${root}/result.json`, JSON.stringify(result), { mode: 0o600 }); console.log(JSON.stringify(result));
