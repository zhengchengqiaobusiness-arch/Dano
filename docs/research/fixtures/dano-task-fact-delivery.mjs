// Dano's production broker/projector -> real MiMo -> real isolated OpenViking.
// The business HTTP service and pi turn events are synthetic, not OA/browser proof.
// Args: Dano app root, private models.json, private credentials.json, isolated ov.conf.
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, writeFile, chmod } from 'node:fs/promises';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';
import { createServer } from 'node:http';
import { randomBytes, randomUUID } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';

const [appRoot, modelsPath, credentialsPath, ovPath] = process.argv.slice(2);
const require = createRequire(join(appRoot, 'package.json'));
const { createJiti } = require('jiti');
const jiti = createJiti(import.meta.url);
const { CredentialBroker } = await jiti.import(join(appRoot, 'src/bridge/credential-broker.ts'));
const { MemoryTaskFacts } = await jiti.import(join(appRoot, 'src/bridge/memory-task-facts.ts'));
const { oauthUserId } = await jiti.import(join(appRoot, 'src/bridge/oauth-user-id.ts'));
async function installed(name, entry) {
  const root = join(appRoot, 'node_modules', name);
  const pkg = JSON.parse(await readFile(join(root, 'package.json'), 'utf8'));
  return { pkg, module: await import(pathToFileURL(join(root, pkg.exports[entry].import))) };
}
const { pkg: piPkg, module: pi } = await installed('@earendil-works/pi-coding-agent', '.');
const { pkg: memoryPkg, module: memory } = await installed('@josephyoung/pi-openviking', './host');
const appManifest = JSON.parse(await readFile(join(appRoot, 'package.json'), 'utf8'));
assert.equal(piPkg.version, appManifest.dependencies['@earendil-works/pi-coding-agent']);
assert.equal(memoryPkg.version, appManifest.dependencies['@josephyoung/pi-openviking']);
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
for (const key of ['XIAOMI_TOKEN_PLAN_CN_API_KEY', 'XIAOMI_API_KEY']) if (credentials[key]) process.env[key] = credentials[key];
const ov = JSON.parse(await readFile(ovPath, 'utf8'));
assert(['127.0.0.1', 'localhost'].includes(ov.server.host));
const baseUrl = `http://${ov.server.host}:${ov.server.port}`;
const root = await mkdtemp('/private/tmp/dano475-task-delivery-'); await chmod(root, 0o700);
const owner = { accountId: `task-${randomUUID().replaceAll('-', '').slice(0, 16)}`, userId: 'alice' };
async function admin(path, body) {
  const response = await fetch(`${baseUrl}/api/v1${path}`, { method: 'POST', redirect: 'error',
    signal: AbortSignal.timeout(30000), headers: { 'X-API-Key': ov.server.root_api_key, 'Content-Type': 'application/json' },
    body: JSON.stringify(body) });
  assert.equal(response.status, 200); const data = await response.json(); assert.equal(data.status, 'ok'); return data.result;
}
await admin('/admin/accounts', { account_id: owner.accountId, admin_user_id: 'admin' });
const apiKey = (await admin(`/admin/accounts/${owner.accountId}/users`, { user_id: owner.userId, role: 'user' })).user_key;
await writeFile(join(root, 'private-owner.json'), JSON.stringify({ owner, apiKey }), { mode: 0o600 });
const client = new memory.OwnerMemoryClient({ owner, baseUrl, apiKey, timeoutMs: 30000 });
const store = new memory.FileStateStore({ owner, directory: join(root, 'state'), policyVersion: 'task-delivery-v1' });
const delivery = new memory.MemoryDelivery({ store, transport: client, maxPayloadBytes: 8192 });
await delivery.enable('task-delivery-v1');
await delivery.authorizeCollection({ policyVersion: 'task-delivery-v1', scope: null, boundaries: [] });
const key = randomBytes(32);
const factOptions = { store, userId: oauthUserId('synthetic-oa-alice'), key, policyVersion: 'task-delivery-v1', timeoutMs: 5000,
  config: { maxResponseBytes: 8192, maxFactBytes: 4096, contracts: [{ id: 'weekly-report-template', method: 'POST', path: '/templates',
    success: { path: ['code'], equals: 0 }, actorPath: ['data', 'owner'], fields: [
      { label: 'template', path: ['data', 'template'], type: 'string' },
      { label: 'purpose', path: ['data', 'purpose'], type: 'string' },
    ] }] } };
const facts = new MemoryTaskFacts(factOptions);
let foreign = false, authorizedSends = 0, sends = 0;
const providerToken = randomBytes(24).toString('hex');
const server = createServer((req, res) => {
  sends++; if (req.headers.authorization === `Bearer ${providerToken}`) authorizedSends++;
  res.setHeader('Content-Type', 'application/json');
  res.end(JSON.stringify({ code: 0, data: { owner: foreign ? 'synthetic-oa-bob' : 'synthetic-oa-alice',
    template: 'REPORT-731', purpose: '用户已将该模板设为每周项目进展汇报的固定模板', private: 'PRIVATE_RAW_BODY' } }));
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const broker = new CredentialBroker({ providerApiOrigin: `http://127.0.0.1:${server.address().port}`,
  allowInsecureProviderApiOrigin: true, readCredential: async id => id === 'synthetic-login' ? { accessToken: providerToken } : null });
const sessionRoot = join(root, 'sessions'); await mkdir(sessionRoot, { mode: 0o700 });
const session = pi.SessionManager.create(root, sessionRoot);
let emit;
broker.observe('scope', { sessionId: session.getSessionId(), subscribe(listener) { emit = listener; return () => {}; } });
broker.queueAssistantTurn('scope', session.getSessionId(), 'synthetic-login');
emit({ type: 'message_start', message: { role: 'user', content: 'run', timestamp: Date.now() } });
emit({ type: 'turn_start' });
const tool = broker.createTool('scope', facts.capture.bind(facts));
const lifecycle = new memory.CollectionLifecycle(store);
const modelRuntime = await pi.ModelRuntime.create({ authPath: join(root, 'auth.json'), modelsPath, refreshOnCreate: false });
const model = modelRuntime.getModel('xiaomi-token-plan-cn', 'mimo-v2.5'); assert(model);
let calls = 0; const usage = [];
const verifier = new MemoryTaskFacts(factOptions); // Receipts must survive a new verifier instance.
const selector = new memory.CollectionFactSelector({ store, maxInputBytes: 16384, maxFacts: 5, timeoutMs: 45000,
  taskFacts: verifier.policy(), sensitiveValues: () => [providerToken, apiKey, ...Object.values(credentials).filter(v => typeof v === 'string' && v.length >= 12)],
  async complete({ systemPrompt, data, signal }) {
    assert(!data.includes('PRIVATE_RAW_BODY')); assert(!data.includes(providerToken)); assert(!data.includes('synthetic-oa-'));
    calls++; const started = performance.now();
    const response = await modelRuntime.completeSimple(model, { systemPrompt, messages: [{ role: 'user', content: data, timestamp: Date.now() }] },
      { signal, temperature: 0, maxTokens: 2048, onPayload: p => ({ ...p, thinking: { type: 'disabled' } }) });
    assert.equal(response.stopReason, 'stop');
    usage.push({ input: response.usage.input, output: response.usage.output, totalTokens: response.usage.totalTokens,
      elapsedMs: Math.round(performance.now() - started) });
    return response.content.filter(b => b.type === 'text').map(b => b.text).join('');
  } });
const results = [];
try {
  for (const name of ['confirmed-owned-result', 'foreign-actor', 'withdrawn-consent']) {
    foreign = name === 'foreign-actor';
    if (name === 'withdrawn-consent') await delivery.revokeCollection();
    const requestId = await lifecycle.begin(session);
    session.appendMessage({ role: 'user', content: '请按已确认的任务创建模板。', timestamp: Date.now() });
    const callId = `call-${name}`;
    session.appendMessage({ role: 'assistant', stopReason: 'toolUse', timestamp: Date.now(),
      content: [{ type: 'toolCall', id: callId, name: 'provider_request', arguments: { method: 'POST', path: '/templates' } }] });
    const response = await tool.execute(callId, { method: 'POST', path: '/templates' }, undefined, undefined, { sessionManager: session });
    assert.equal(response.details.status, 200); assert(!JSON.stringify(response).includes(providerToken));
    const toolEntry = session.appendMessage({ role: 'toolResult', toolName: 'provider_request', toolCallId: callId,
      content: response.content, details: response.details, isError: false, timestamp: Date.now() });
    session.appendMessage({ role: 'assistant', stopReason: 'stop', content: [{ type: 'text', text: '完成。' }], timestamp: Date.now() });
    if (name === 'withdrawn-consent') {
      assert(!requestId); assert.equal(response.details.danoTaskFacts.length, 0);
      results.push({ name, businessSucceeded: true, receiptCount: 0 }); continue;
    }
    await lifecycle.settle(requestId, session);
    const reopened = pi.SessionManager.open(session.getSessionFile(), sessionRoot);
    const selection = await selector.select([requestId], reopened); assert.equal(selection.status, 'ready');
    if (name === 'confirmed-owned-result') {
      assert(selection.facts.length > 0);
      assert(selection.facts.some(f => f.text.includes('REPORT-731')));
      assert(selection.facts.every(f => f.source.entryId === toolEntry && f.projection?.toolName === 'provider_request'));
    } else { assert.equal(selection.facts.length, 0); assert.equal(response.details.danoTaskFacts.length, 0); }
    assert.equal((await delivery.collectSelection(selection)).status, 'recorded');
    if (name === 'confirmed-owned-result') {
      const operations = Object.values((await store.read()).operations);
      assert(operations.length > 0);
      const deadline = Date.now() + 180000;
      for (const operation of operations) {
        while ((await store.read()).operations[operation.id].phase !== 'ready') {
          assert(Date.now() < deadline, 'REAL_OPENVIKING_DELIVERY_TIMEOUT');
          await delivery.advance(operation.id);
          const phase = (await store.read()).operations[operation.id].phase;
          assert(!['failed', 'blocked_by_pause'].includes(phase));
          if (phase !== 'ready') await delay(500);
        }
      }
      const recalled = await client.recall('每周项目进展汇报的固定模板', 10);
      assert(JSON.stringify(recalled).includes('REPORT-731'), 'Delivered task fact missing from real recall');
    }
    results.push({ name, selectedFacts: selection.facts.length, receiptCount: response.details.danoTaskFacts.length });
  }
  const state = await store.read();
  assert(!JSON.stringify(state).includes('PRIVATE_RAW_BODY')); assert.equal(sends, authorizedSends);
  const report = { root, piVersion: piPkg.version, extensionVersion: memoryPkg.version, model: model.id,
    syntheticBusinessProvider: true, actualOAContractVerified: false, browserVerified: false,
    nativePiSessionReopened: true, recreatedReceiptVerifier: true,
    realOpenVikingReadyAndRecall: true, results, modelCalls: calls, usage };
  await writeFile(join(root, 'result.json'), JSON.stringify(report, null, 2), { mode: 0o600 });
  console.log(JSON.stringify(report));
} finally {
  facts.close(); verifier.close(); key.fill(0);
  await new Promise(resolve => server.close(resolve));
}
