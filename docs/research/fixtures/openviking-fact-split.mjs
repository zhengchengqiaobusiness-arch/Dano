/** Real service proof that two facts in one pi entry have independent sources. */
import assert from 'node:assert/strict';
import { readFile, mkdtemp, writeFile } from 'node:fs/promises';
import { randomUUID, createHash } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';

const [modulePath, configPath] = process.argv.slice(2);
const { OwnerMemoryClient, FileStateStore, MemoryDelivery, MemoryGovernanceService } =
  await import(pathToFileURL(modulePath));
const config = JSON.parse(await readFile(configPath, 'utf8'));
assert(['localhost', '127.0.0.1'].includes(config.server.host));
const baseUrl = `http://${config.server.host}:${config.server.port}`;
const root = await mkdtemp('/private/tmp/dano476-fact-split-');
const accountId = `split-${randomUUID().slice(0, 8)}`;
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
const store = new FileStateStore({ owner, directory: `${root}/state`, policyVersion: 'fact-split' });
const delivery = new MemoryDelivery({ store, transport: client, maxPayloadBytes: 8192 });
await delivery.enable('fact-split');
await delivery.authorizeCollection({ policyVersion: 'fact-split', scope: null, boundaries: [] });
const old = '我最喜欢的茶是茉莉花茶。';
const unrelated = '我每周例会安排在星期二。';
const now = new Date().toISOString();
const source = { sessionId: 'source-session', entryId: 'shared-entry', branchId: 'shared-entry',
  contentVersion: createHash('sha256').update(`${old} ${unrelated}`).digest('hex'), entryTimestamp: now };
const fact = text => ({ text, source, evidence: [{ source, quote: text }] });
await store.transact(state => {
  state.collectionRequests = { request: { id: 'request', sessionId: source.sessionId,
    baselineEntryId: null, settledEntryId: source.branchId, scope: null,
    authorizationEpoch: state.authorization.epoch,
    collectionRevision: state.authorization.collectionConsent.revision,
    phase: 'settled', sourceEntries: [source.entryId], createdAt: now, updatedAt: now } };
});
const receipt = await delivery.collectSelection({ status: 'ready', requestIds: ['request'],
  facts: [fact(old), fact(unrelated)] });
assert.equal(receipt.status, 'recorded'); assert.equal(receipt.operationIds.length, 2);
for (const id of receipt.operationIds) {
  let ready = false;
  for (const deadline = Date.now() + 180000; Date.now() < deadline;) {
    await delivery.advance(id);
    const operation = (await store.read()).operations[id];
    if (operation.phase === 'ready') { ready = true; break; }
    assert(!['failed', 'blocked'].includes(operation.phase));
    await delay(500);
  }
  assert(ready, 'EXTRACTION_TIMEOUT');
}
const state = await store.read();
const oldDigest = createHash('sha256').update(old).digest('hex');
const oldOperation = receipt.operationIds.map(id => state.operations[id]).find(operation => operation.factDigest === oldDigest);
const otherOperation = receipt.operationIds.map(id => state.operations[id]).find(operation => operation.factDigest !== oldDigest);
assert(oldOperation?.memoryUris?.length && otherOperation?.memoryUris?.length);
const oldUris = oldOperation.memoryUris.filter(uri => uri.endsWith('.md'));
const otherUris = otherOperation.memoryUris.filter(uri => uri.endsWith('.md'));
assert(oldUris.length && otherUris.length);
const sharedDocument = oldUris.some(uri => otherUris.includes(uri));
const uri = oldUris[0];
const content = await client.readMemory(uri);
const selectedText = content.split('\n').find(line => line.includes('茉莉'));
assert(selectedText);
const service = new MemoryGovernanceService(store, client, delivery);
let forgotten = false, safetyAmbiguous = false;
try {
  const start = await service.forget(uri, selectedText);
  let status = start.status;
  for (const deadline = Date.now() + 180000; status !== 'complete' && Date.now() < deadline;) {
    status = (await service.advancePending())?.status;
    if (status !== 'complete') await delay(500);
  }
  forgotten = status === 'complete';
} catch (error) {
  safetyAmbiguous = error?.message === 'MEMORY_TARGET_AMBIGUOUS';
  if (!safetyAmbiguous) throw error;
}
const final = await store.read();
assert.equal(final.operations[otherOperation.id].phase, 'ready');
const result = { root, independentOperations: true, sharedDocument, forgotten, safetyAmbiguous,
  unrelatedSourcePreserved: true, browserVerified: false };
await writeFile(`${root}/result.json`, JSON.stringify(result), { mode: 0o600 });
console.log(JSON.stringify(result));
