/** Real service transport probe only; this does not prove the governance barrier or browser flow. */
import assert from 'node:assert/strict';
import { readFile, mkdtemp, writeFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
const [modulePath, configPath] = process.argv.slice(2);
const { OwnerMemoryClient, MemoryDelivery, FileStateStore } = await import(pathToFileURL(modulePath));
const config = JSON.parse(await readFile(configPath, 'utf8'));
assert(['127.0.0.1', 'localhost'].includes(config.server.host));
const baseUrl = `http://${config.server.host}:${config.server.port}`;
const root = await mkdtemp('/private/tmp/dano476-governance-transport-');
const accountId = `governance-${randomUUID().slice(0, 8)}`;
async function admin(path, body) {
  const response = await fetch(`${baseUrl}/api/v1${path}`, { method: 'POST', redirect: 'error',
    signal: AbortSignal.timeout(30000), headers: { 'X-API-Key': config.server.root_api_key, 'Content-Type': 'application/json' },
    body: JSON.stringify(body) });
  assert.equal(response.status, 200); const data = await response.json(); assert.equal(data.status, 'ok'); return data.result;
}
await admin('/admin/accounts', { account_id: accountId, admin_user_id: 'admin' });
const { user_key: apiKey } = await admin(`/admin/accounts/${accountId}/users`, { user_id: 'alice', role: 'user' });
const owner = { accountId, userId: 'alice' };
const client = new OwnerMemoryClient({ owner, baseUrl, apiKey, timeoutMs: 90000 });
const store = new FileStateStore({ owner, directory: root, policyVersion: 'governance-probe' });
const delivery = new MemoryDelivery({ store, transport: client, maxPayloadBytes: 8192 });
await delivery.enable('governance-probe');
const op = await delivery.save({ sessionId: 'synthetic', entryId: 'entry-1', branchId: 'entry-1', contentVersion: 'v1' },
  '我的长期偏好：每月财务审查报告的结论标题固定为“杉露财务小结”。');
let ready;
for (const deadline = Date.now() + 180000; Date.now() < deadline;) {
  await delivery.advance(op.id); const current = (await store.read()).operations[op.id];
  assert(!['failed', 'blocked'].includes(current.phase));
  if (current.phase === 'ready') { ready = current; break; }
  await delay(500);
}
assert(ready, 'REAL_EXTRACTION_TIMEOUT'); assert.equal(ready.memoryUris.length, 1);
const uri = ready.memoryUris[0], before = await client.readMemory(uri);
assert(before.includes('杉露财务小结'));
const after = before.replaceAll('杉露财务小结', '晴竹财务结语');
await client.replaceMemory(uri, after);
assert.equal(await client.readMemory(uri), after);
await client.removeMemory(uri); await client.removeMemory(uri);
await assert.rejects(client.readMemory(uri));
await client.removeSource(ready); await client.removeSource(ready);
assert.equal(await client.sessionExists(ready.remoteSessionId), false);
const result = { root, realModelExtraction: true, replacementReadBack: true, documentDeletionConfirmed: true,
  sourceDeletionConfirmed: true, repeatedDeletionSucceeded: true, governanceBarrierTested: false, browserVerified: false };
await writeFile(`${root}/result.json`, JSON.stringify(result), { mode: 0o600 });
console.log(JSON.stringify(result));
