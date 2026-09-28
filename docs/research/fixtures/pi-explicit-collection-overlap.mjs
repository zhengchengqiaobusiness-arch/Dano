// Real model probe over disposable native Pi records. No remote memory delivery.
// Args: independent extension root, private models.json, private credentials.json.
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { pathToFileURL } from 'node:url';
const [extensionRoot, modelsPath, credentialPath] = process.argv.slice(2);
const piRoot = join(extensionRoot, 'node_modules/@earendil-works/pi-coding-agent');
const pkg = JSON.parse(await readFile(join(piRoot, 'package.json'), 'utf8'));
assert.equal(pkg.version, '0.85.1');
const pi = await import(pathToFileURL(join(piRoot, pkg.exports['.'].import)));
const memory = await import(pathToFileURL(join(extensionRoot, 'dist/host.js')));
const credentials = JSON.parse(await readFile(credentialPath, 'utf8'));
const provider = process.env.DANO_FIXTURE_PROVIDER, modelId = process.env.DANO_FIXTURE_MODEL;
assert(provider && modelId);
const configured = JSON.parse(await readFile(modelsPath, 'utf8')).providers[provider];
const keyReference = /^\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))$/.exec(configured.apiKey);
assert(keyReference, 'An explicit private environment credential reference is required');
const keyName = keyReference[1] ?? keyReference[2];
assert(credentials[keyName]); process.env[keyName] = credentials[keyName];
const cases = [
  { name: 'browser-exact-fact',
    user: '我的合成验收报告有一个长期固定的格式偏好：最后一行总是写“寒江验收完成”。这次你只需回复“了解”。',
    saved: '我的合成验收报告有一个长期固定的格式偏好：最后一行总是写“寒江验收完成”。', expected: [] },
  { name: 'independent-fact-preserved', user: 'I prefer concise reports. I use metric units.',
    saved: 'I prefer concise reports.', expected: ['I use metric units.'] },
];
const root = await mkdtemp('/private/tmp/pi-explicit-overlap-');
const hash = text => createHash('sha256').update(text).digest('hex');
try {
  const runtime = await pi.ModelRuntime.create({ authPath: join(root, 'auth.json'), modelsPath, refreshOnCreate: false });
  const model = runtime.getModel(provider, modelId); assert(model);
  const results = [];
  for (const item of cases) for (let repetition = 1; repetition <= 3; repetition++) {
    const owner = { accountId: 'disposable-overlap-probe', userId: `${item.name}-${repetition}` };
    const store = new memory.FileStateStore({ owner, directory: join(root, owner.userId), policyVersion: 'probe-v1' });
    const delivery = new memory.MemoryDelivery({ store, transport: { owner }, maxPayloadBytes: 8192 });
    await delivery.enable('probe-v1');
    await delivery.authorizeCollection({ policyVersion: 'probe-v1', scope: null, boundaries: [] });
    const session = pi.SessionManager.inMemory(root), lifecycle = new memory.CollectionLifecycle(store);
    const requestId = await lifecycle.begin(session);
    const entryId = session.appendMessage({ role: 'user', content: item.user, timestamp: Date.now() });
    const entry = session.getEntry(entryId);
    const operation = await delivery.save({ sessionId: session.getSessionId(), entryId: `${entry.id}:${hash(item.saved)}`,
      branchId: entry.id, contentVersion: hash(JSON.stringify(entry)) }, item.saved);
    session.appendMessage({ role: 'assistant', stopReason: 'toolUse', timestamp: Date.now(),
      content: [{ type: 'toolCall', id: 'synthetic-save', name: 'memory_save', arguments: { content: item.saved } }] });
    session.appendMessage({ role: 'toolResult', toolCallId: 'synthetic-save', toolName: 'memory_save', isError: false,
      timestamp: Date.now(), content: [{ type: 'text', text: 'queued' }], details: { operationId: operation.id } });
    session.appendMessage({ role: 'assistant', stopReason: 'stop', timestamp: Date.now(), content: [{ type: 'text', text: '了解。' }] });
    await lifecycle.settle(requestId, session);
    let usage, modelResponse, exclusionPresent;
    const selector = new memory.CollectionFactSelector({ store, maxInputBytes: 16384, maxFacts: 5, timeoutMs: 45000,
      sensitiveValues: () => [credentials[keyName]],
      async complete({ systemPrompt, data, signal }) {
        exclusionPresent = JSON.parse(data).messages.some(message => message.role === 'explicit_memory' && message.text === item.saved);
        const answer = await runtime.completeSimple(model, { systemPrompt,
          messages: [{ role: 'user', content: data, timestamp: Date.now() }] }, {
          signal, maxTokens: 2048, temperature: 0, onPayload: payload => ({ ...payload, thinking: { type: 'disabled' } }),
        });
        assert.equal(answer.stopReason, 'stop');
        usage = { input: answer.usage.input, output: answer.usage.output, cacheRead: answer.usage.cacheRead, totalTokens: answer.usage.totalTokens };
        modelResponse = answer.content.filter(block => block.type === 'text').map(block => block.text).join('');
        assert(!modelResponse.includes(credentials[keyName])); return modelResponse;
      } });
    const selected = await selector.select([requestId], session);
    const passed = exclusionPresent && selected.status === 'ready'
      && JSON.stringify(selected.facts.map(fact => fact.text)) === JSON.stringify(item.expected);
    if (passed) {
      assert.equal((await delivery.collectSelection(selected)).status, 'recorded');
      assert.equal(Object.values((await store.read()).operations).filter(operation => operation.kind === 'automatic').length, item.expected.length ? 1 : 0);
    }
    const result = { name: item.name, repetition, passed, exclusionPresent, status: selected.status,
      modelResponse, selectedFacts: selected.status === 'ready' ? selected.facts.map(fact => fact.text) : undefined, usage };
    results.push(result); console.log(JSON.stringify(result));
  }
  console.log(JSON.stringify({ model: model.id, extensionVersion: JSON.parse(await readFile(join(extensionRoot, 'package.json'))).version,
    datasetHash: hash(JSON.stringify(cases)), promptHash: hash(memory.collectionSelectionPrompt),
    cases: results.length, passed: results.filter(result => result.passed).length, remoteDeliveryTested: false, fullAc04Gate: false }));
  assert(results.every(result => result.passed), 'Explicit/automatic overlap probe failed');
} finally { await rm(root, { recursive: true, force: true }); }
