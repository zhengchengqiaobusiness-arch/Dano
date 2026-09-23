/** Probe whether an old queued paraphrase survives selective correction. */
import assert from 'node:assert/strict';
import { readFile, mkdtemp, writeFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';

const [modulePath, configPath] = process.argv.slice(2);
const { OwnerMemoryClient, FileStateStore, MemoryDelivery, MemoryGovernanceService } =
  await import(pathToFileURL(modulePath));
const config = JSON.parse(await readFile(configPath, 'utf8'));
assert(['localhost', '127.0.0.1'].includes(config.server.host));
const baseUrl = `http://${config.server.host}:${config.server.port}`;
const root = await mkdtemp('/private/tmp/dano476-selective-paraphrase-');
const accountId = `para-${randomUUID().slice(0, 8)}`;
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
const store = new FileStateStore({ owner, directory: `${root}/state`, policyVersion: 'paraphrase' });
const delivery = new MemoryDelivery({ store, transport: client, maxPayloadBytes: 8192 });
await delivery.enable('paraphrase');
const source = entryId => ({ sessionId: entryId, entryId, branchId: entryId, contentVersion: 'v1' });
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
  '我的每月报告总结标题一直叫作“青岚旧版小结”。'));
const uri = target.memoryUris.find(value => value.endsWith('.md'));
assert(uri);
const original = await client.readMemory(uri);
const selectedText = original.split('\n').find(line => line.includes('青岚旧版小结'));
assert(selectedText);
const queued = await delivery.save(source('old-paraphrase'),
  '我长期把月报的收尾标题定为“青岚旧版小结”。');
assert.equal(queued.phase, 'queued');
const service = new MemoryGovernanceService(store, client, delivery);
const replacement = selectedText.replace('青岚旧版小结', '岚峰新版小结');
let progress = await service.correct(uri, selectedText, replacement);
for (const deadline = Date.now() + 180000; progress.status !== 'complete' && Date.now() < deadline;) {
  progress = await service.advancePending();
  if (progress.status !== 'complete') await delay(500);
}
const documents = await Promise.all((await client.listMemoryDocuments()).map(async uri => ({ uri, text: await client.readMemory(uri) })));
const oldInDocuments = documents.some(item => item.text.includes('青岚旧版小结'));
const oldInRecall = JSON.stringify(await client.recall('每月报告总结标题', 10)).includes('青岚旧版小结');
const result = { root, governanceComplete: progress.status === 'complete',
  oldInDocuments, oldInRecall, queuedPhase: (await store.read()).operations[queued.id].phase,
  documentCount: documents.length, browserVerified: false };
await writeFile(`${root}/result.json`, JSON.stringify(result), { mode: 0o600 });
console.log(JSON.stringify(result));
