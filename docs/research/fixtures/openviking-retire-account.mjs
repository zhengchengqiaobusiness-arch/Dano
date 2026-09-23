/** Isolated real-service proof that one USER key clears global and peer memory. */
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

const [modulePath, configPath, packagePath] = process.argv.slice(2);
assert(modulePath && configPath && packagePath);
const { OwnerMemoryClient } = await import(pathToFileURL(modulePath));
const require = createRequire(packagePath);
const { OpenVikingClient } = require('@openviking/sdk');
const config = JSON.parse(await readFile(configPath, 'utf8'));
assert(['localhost', '127.0.0.1'].includes(config.server.host));
const baseUrl = `http://${config.server.host}:${config.server.port}`;
const root = await mkdtemp('/private/tmp/dano476-retire-account-');
const accountId = `retire-${randomUUID().slice(0, 8)}`;
async function admin(path, body) {
  const response = await fetch(`${baseUrl}/api/v1${path}`, { method: 'POST', redirect: 'error',
    signal: AbortSignal.timeout(30000), headers: { 'X-API-Key': config.server.root_api_key,
      'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  assert.equal(response.status, 200);
  const data = await response.json(); assert.equal(data.status, 'ok'); return data.result;
}
await admin('/admin/accounts', { account_id: accountId, admin_user_id: 'admin' });
const apiKey = (await admin(`/admin/accounts/${accountId}/users`, { user_id: 'alice', role: 'user' })).user_key;
const owner = { accountId, userId: 'alice' };
const global = new OpenVikingClient({ baseUrl, apiKey, timeout: 90000 });
const peer = new OpenVikingClient({ baseUrl, apiKey, actorPeerId: 'project-a', timeout: 90000 });
const globalUri = 'viking://user/alice/memories/retirement-probe.md';
const peerUri = 'viking://user/alice/peers/project-a/memories/retirement-probe.md';
await global.write(globalUri, 'synthetic global fact', { mode: 'replace', wait: true });
await peer.write(peerUri, 'synthetic peer fact', { mode: 'replace', wait: true });
assert.equal(await global.read(globalUri), 'synthetic global fact');
assert.equal(await peer.read(peerUri), 'synthetic peer fact');
const sessionId = randomUUID();
await global.createSession({ sessionId });
assert(await global.sessionExists(sessionId));
const client = new OwnerMemoryClient({ owner, baseUrl, apiKey, timeoutMs: 90000 });
await client.clearOwnerData();
for (const uri of [globalUri, peerUri]) {
  await assert.rejects(() => global.stat(uri), error => error?.statusCode === 404);
}
assert.equal(await global.sessionExists(sessionId), false);
const result = { root, realService: true, globalCleared: true, peerCleared: true,
  sourceSessionCleared: true, browserVerified: false };
await writeFile(`${root}/result.json`, JSON.stringify(result, null, 2), { mode: 0o600 });
console.log(JSON.stringify(result));
