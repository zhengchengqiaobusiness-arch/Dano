// Called only as host UID by the disposable supervisor fixture. The new USER
// key comes from stdin, never argv, environment, logs or a repository file.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const [configDirectory, hostStateRoot, accountId, userId] = process.argv.slice(2);
assert([configDirectory, hostStateRoot, accountId, userId].every(Boolean)
  && !process.stdin.isTTY, 'FIXTURE_KEY_REPLACEMENT_ARGUMENTS_INVALID');
const chunks = [];
let size = 0;
for await (const chunk of process.stdin) {
  size += chunk.length;
  assert(size <= 16385, 'FIXTURE_USER_KEY_TOO_LARGE');
  chunks.push(chunk);
}
const bytes = Buffer.concat(chunks);
let key = bytes.toString('utf8');
if (key.endsWith('\n')) key = key.slice(0, -1);
bytes.fill(0);
chunks.forEach(chunk => chunk.fill(0));
assert(key && !/[\s\x00-\x1f\x7f]/.test(key), 'FIXTURE_USER_KEY_INVALID');

const config = JSON.parse(await readFile(join(configDirectory, 'memory-service.json'), 'utf8'));
assert(config.accountId === accountId && config.baseUrl?.startsWith('http://openviking:'),
  'FIXTURE_OWNER_CONFIG_MISMATCH');
const response = await fetch(`${config.baseUrl}/health`, {
  redirect: 'error', signal: AbortSignal.timeout(config.requestTimeoutMs),
  headers: { 'X-API-Key': key },
});
assert.equal(response.status, 200, 'FIXTURE_NEW_KEY_REJECTED');
const identity = await response.json();
assert(identity.role === 'user' && identity.account_id === accountId
  && identity.user_id === userId, 'FIXTURE_NEW_KEY_OWNER_MISMATCH');
const { MemoryCredentialStore } = await import(pathToFileURL(join(
  process.env.DANO_FIXTURE_SERVER ?? '/app/dist/server', 'bridge/memory-credential-store.js')).href);
const owner = { accountId, userId };
const store = new MemoryCredentialStore({
  directory: join(hostStateRoot, 'memory-service', 'credentials'),
  encryptionKey: Buffer.from(config.encryptionKey, 'hex'),
  keyVersion: config.encryptionKeyVersion,
});
assert(await store.read(owner), 'FIXTURE_OLD_KEY_MISSING');
await store.write(owner, key);
assert.equal(await new MemoryCredentialStore({
  directory: join(hostStateRoot, 'memory-service', 'credentials'),
  encryptionKey: Buffer.from(config.encryptionKey, 'hex'),
  keyVersion: config.encryptionKeyVersion,
}).read(owner), key, 'FIXTURE_NEW_KEY_NOT_DURABLE');
key = '';
