// Disposable real-service credential rotation probe. Never print USER keys.
import assert from 'node:assert/strict';
import { randomBytes, randomUUID } from 'node:crypto';
import { mkdtemp, rm } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';

const origin = process.env.DANO_FIXTURE_MEMORY_BASE_URL;
const managementKey = process.env.DANO_FIXTURE_MEMORY_MANAGEMENT_KEY;
const serverDir = process.env.DANO_FIXTURE_SERVER ?? '/app/dist/server';
assert(origin?.startsWith('http://openviking:') && managementKey,
  'ISOLATED_REAL_SERVICE_REQUIRED');

const { MemoryCredentialStore } = await import(pathToFileURL(join(serverDir,
  'bridge/memory-credential-store.js')).href);

const accountId = `rotation_${randomUUID().replaceAll('-', '')}`;
const owner = { accountId, userId: 'alice' };
const root = await mkdtemp('/tmp/dano-real-key-rotation-');
async function request(method, path, key, body) {
  return fetch(`${origin}${path}`, {
    method, redirect: 'error', signal: AbortSignal.timeout(30000),
    headers: { 'X-API-Key': key, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
}

async function result(method, path, key, body) {
  const response = await request(method, path, key, body);
  assert.equal(response.status, 200, `REAL_SERVICE_${method}_${path.split('/')[2]}_${response.status}`);
  return (await response.json()).result;
}

async function verifyOwnerKey(key) {
  const response = await request('GET', '/health', key);
  assert.equal(response.status, 200, 'USER_KEY_REJECTED');
  const identity = await response.json();
  assert.equal(identity.role, 'user', 'USER_ROLE_MISMATCH');
  assert.equal(identity.account_id, owner.accountId, 'USER_ACCOUNT_MISMATCH');
  assert.equal(identity.user_id, owner.userId, 'USER_OWNER_MISMATCH');
}

try {
  await result('POST', '/api/v1/admin/accounts', managementKey,
    { account_id: accountId, admin_user_id: 'admin' });
  const usersPath = `/api/v1/admin/accounts/${accountId}/users`;
  const original = (await result('POST', usersPath, managementKey,
    { user_id: owner.userId, role: 'user' })).user_key;
  const bob = (await result('POST', usersPath, managementKey,
    { user_id: 'bob', role: 'user' })).user_key;
  const options = { directory: join(root, 'credentials'), encryptionKey: randomBytes(32),
    keyVersion: 'rotation-v1' };
  const store = new MemoryCredentialStore(options);
  await store.write(owner, original);
  await verifyOwnerKey(await new MemoryCredentialStore(options).read(owner));
  const sessionId = (await result('POST', '/api/v1/sessions', original,
    { auto_commit_policy: null })).session_id;
  await result('POST', `/api/v1/sessions/${sessionId}/messages`, original,
    { role: 'user', content: 'Synthetic rotation source',
      source_message_ids: [`rotation_${randomUUID()}`] });

  const rotated = (await result('POST', `${usersPath}/${owner.userId}/key`, managementKey)).user_key;
  assert.notEqual(rotated, original, 'REMOTE_KEY_NOT_ROTATED');
  const oldStatus = (await request('GET', `/api/v1/sessions/${sessionId}`, original)).status;
  assert([401, 403].includes(oldStatus), 'OLD_USER_KEY_STILL_ACTIVE');
  await assert.rejects(verifyOwnerKey(await store.read(owner)), 'revoked persisted USER key accepted');
  await assert.rejects(verifyOwnerKey(bob), 'foreign USER key accepted');
  assert.equal(await store.read(owner), original, 'foreign key changed stored owner credential');
  await verifyOwnerKey(rotated);
  await store.write(owner, rotated);
  const reopenedKey = await new MemoryCredentialStore(options).read(owner);
  assert.equal(reopenedKey, rotated, 'encrypted store did not reload rotated USER key');
  await verifyOwnerKey(reopenedKey);
  const session = await result('GET', `/api/v1/sessions/${sessionId}`, reopenedKey);
  assert.equal(session.created_by_user_id, owner.userId);
  assert.equal(session.message_count, 1);
  console.log(JSON.stringify({ realService: true, syntheticOwners: 2,
    oldKeyRevoked: true, staleStoredCredentialRejected: true, foreignOwnerKeyRejected: true,
    encryptedStoreReopenedWithNewKey: true, sameSessionReadAfterRotation: true,
    danoIdentityServiceRestartVerified: false, modelExtractionVerified: false,
    browserVerified: false }));
} finally {
  await rm(root, { recursive: true });
}
