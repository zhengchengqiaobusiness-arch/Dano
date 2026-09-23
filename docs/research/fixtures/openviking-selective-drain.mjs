/** Real pre-barrier unrelated outbox drain under a selective correction. */
import assert from 'node:assert/strict';
import { readFile, mkdtemp, writeFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
const [modulePath, configPath] = process.argv.slice(2);
const { OwnerMemoryClient, FileStateStore, MemoryDelivery, MemoryGovernanceService,
  MemoryExportService } = await import(pathToFileURL(modulePath));
const config = JSON.parse(await readFile(configPath, 'utf8'));
assert(['localhost', '127.0.0.1'].includes(config.server.host));
const baseUrl = `http://${config.server.host}:${config.server.port}`;
const root = await mkdtemp('/private/tmp/dano476-selective-drain-');
const accountId = `selective-${randomUUID().slice(0, 8)}`;
async function admin(path, body) {
  const response = await fetch(`${baseUrl}/api/v1${path}`, { method: 'POST', redirect: 'error',
    signal: AbortSignal.timeout(30000), headers: { 'X-API-Key': config.server.root_api_key,
      'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  assert.equal(response.status, 200);
  const data = await response.json(); assert.equal(data.status, 'ok'); return data.result;
}
await admin('/admin/accounts', { account_id: accountId, admin_user_id: 'admin' });
const apiKey = (await admin(`/admin/accounts/${accountId}/users`, { user_id: 'alice', role: 'user' })).user_key;
await writeFile(`${root}/credentials.json`, JSON.stringify({ accountId, apiKey }), { mode: 0o600 });
const owner = { accountId, userId: 'alice' };
const client = new OwnerMemoryClient({ owner, baseUrl, apiKey, timeoutMs: 90000 });
const store = new FileStateStore({ owner, directory: `${root}/state`, policyVersion: 'selective-drain' });
const delivery = new MemoryDelivery({ store, transport: client, maxPayloadBytes: 8192 });
await delivery.enable('selective-drain');
function source(entryId) { return { sessionId: entryId, entryId, branchId: entryId, contentVersion: 'v1' }; }
async function ready(operation) {
  for (const deadline = Date.now() + 180000; Date.now() < deadline;) {
    await delivery.advance(operation.id);
    const current = (await store.read()).operations[operation.id];
    if (current.phase === 'ready') return current;
    assert(!['failed', 'blocked'].includes(current.phase));
    await delay(500);
  }
  throw new Error('EXTRACTION_TIMEOUT');
}
const target = await ready(await delivery.save(source('target'),
  '我的长期偏好：每月报告总结标题固定为“青岚旧版小结”。'));
const uri = target.memoryUris.find(value => value.endsWith('.md'));
assert(uri);
const original = await client.readMemory(uri);
const selected = original.split('\n').find(line => line.includes('青岚旧版小结'));
assert(selected);
const unrelated = await delivery.save(source('unrelated'), '我的长期偏好：常用茶饮为乌龙茶。');
assert.equal(unrelated.phase, 'queued');
const service = new MemoryGovernanceService(store, client, delivery);
const revised = selected.replace('青岚旧版小结', '岚峰新版小结');
const receipt = await service.correct(uri, selected, revised);
let complete = receipt.status === 'complete';
for (const deadline = Date.now() + 180000; !complete && Date.now() < deadline;) {
  const progress = await service.advancePending();
  complete = progress?.status === 'complete';
  if (!complete) await delay(500);
}
assert(complete, 'SELECTIVE_DRAIN_TIMEOUT');
const final = await store.read();
assert.equal(final.operations[unrelated.id].phase, 'ready');
assert.equal(final.operations[unrelated.id].source.entryId, 'unrelated');
const corrected = await client.readMemory(uri);
assert(corrected.includes(revised) && !corrected.includes(selected));
assert(!JSON.stringify(await client.recall('每月报告总结标题', 10)).includes('青岚旧版小结'));
assert(JSON.stringify(await client.recall('常用茶饮', 10)).includes('乌龙茶'));
const page = await new MemoryExportService(store, client).page({ limit: 100 });
const edited = page.items.find(item => item.uri === uri);
assert(edited.sources.some(source => source.status === 'revoked' && source.entryId === 'target'));
assert(edited.revisions.some(version => version.kind === 'correct'));
assert(page.items.some(item => item.sources.some(source => source.status === 'current' && source.entryId === 'unrelated')));
const result = { root, queuedUnrelatedDrained: true, unrelatedMemoryPreserved: true,
  oldFactAbsent: true, correctionLineageExported: true, browserVerified: false };
await writeFile(`${root}/result.json`, JSON.stringify(result), { mode: 0o600 });
console.log(JSON.stringify(result));
