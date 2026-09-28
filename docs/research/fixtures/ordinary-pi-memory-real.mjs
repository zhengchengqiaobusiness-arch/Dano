// Operator acceptance: published standard CLI against an isolated real service.
// This is separate from Dano's authenticated Browser proof.
import assert from 'node:assert/strict';
import { readFile, writeFile, rm } from 'node:fs/promises';
import { randomUUID, createHash } from 'node:crypto';
import { spawn } from 'node:child_process';

const config = JSON.parse(await readFile(process.argv[2], 'utf8'));
const modelsPath = process.argv[3];
const provider = process.env.DANO_FIXTURE_PROVIDER, id = process.env.DANO_FIXTURE_MODEL;
const binding = config.tokenizers.find(x => x.model.provider === provider && x.model.id === id);
assert(binding, 'Explicit model tokenizer required');
for (const asset of [binding.tokenizer, binding.config]) {
  assert.equal(createHash('sha256').update(await readFile(asset.path)).digest('hex'), asset.sha256);
}
assert(config.baseUrl.startsWith('http://openviking:'), 'Isolated real service required');
const accountId = `extension-test-${randomUUID()}`;
const owner = { accountId, userId: 'alice' };
const connectionPath = '/tmp/ordinary-pi-memory-connection.json';
const request = async (method, path, body) => {
  const response = await fetch(config.baseUrl + path, { method, redirect: 'error',
    signal: AbortSignal.timeout(30000), headers: { 'X-API-Key': config.managementKey,
      ...(body ? { 'Content-Type': 'application/json' } : {}) },
    ...(body ? { body: JSON.stringify(body) } : {}) });
  assert([200, ...(method === 'DELETE' ? [202] : [])].includes(response.status),
    `Isolated ${method} setup/cleanup failed (${response.status})`);
  return (await response.json()).result;
};
let created = false;
try {
  await request('POST', '/api/v1/admin/accounts', { account_id: accountId, admin_user_id: 'admin' });
  created = true;
  const user = await request('POST', `/api/v1/admin/accounts/${accountId}/users`,
    { user_id: owner.userId, role: 'user' });
  assert(user.user_key, 'USER credential missing');
  await writeFile(connectionPath, JSON.stringify({ owner, apiKey: user.user_key,
    baseUrl: config.baseUrl, model: binding.model, tokenizer: {
      path: binding.tokenizer.path, configPath: binding.config.path,
      revision: process.env.DANO_FIXTURE_TOKENIZER_REVISION,
    } }), { mode: 0o600 });
  const child = spawn(process.execPath,
    [new URL('./linux-cli-memory.mjs', import.meta.url).pathname, modelsPath, connectionPath],
    { stdio: ['ignore', 'pipe', 'pipe'], env: process.env });
  let output = '', errors = '';
  child.stdout.on('data', x => { output += x; });
  child.stderr.on('data', x => { errors += x; });
  const exit = await new Promise((resolve, reject) => { child.once('error', reject); child.once('exit', resolve); });
  assert(!output.includes(user.user_key) && !errors.includes(user.user_key));
  // Provider responses are private even on failure. Print stage/status only.
  if (exit !== 0) {
    await writeFile('/evidence/failure-private.log', errors, { mode: 0o600 });
    console.log(JSON.stringify({ standardCliAcceptance: false, exit,
      stdoutBytes: output.length, stderrBytes: errors.length }));
    process.exitCode = 1;
  } else {
    const result = JSON.parse(await readFile('/evidence/result.json', 'utf8'));
    console.log(JSON.stringify(result));
  }
} finally {
  if (created) {
    await request('DELETE', `/api/v1/admin/accounts/${accountId}/users/${owner.userId}`);
    let removed = false;
    for (let attempt = 0; attempt < 60; attempt++) {
      const users = await request('GET', `/api/v1/admin/accounts/${accountId}/users`);
      if (!users.some(x => x.user_id === owner.userId)) { removed = true; break; }
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    assert(removed, 'Synthetic USER cleanup remained pending');
    console.log(JSON.stringify({ syntheticUserCleanup: true, emptyTestAccountRetained: true }));
  }
  await rm(connectionPath, { force: true });
}
