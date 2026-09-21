// Real MiMo confirmation across already-processed batches. Synthetic inputs only.
// Args: extension checkout, private models.json, private production-input.json.
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { pathToFileURL } from 'node:url';
const [extensionRoot, modelsPath, credentialPath] = process.argv.slice(2);
const piRoot = join(extensionRoot, 'node_modules/@earendil-works/pi-coding-agent');
const manifest = JSON.parse(await readFile(join(piRoot, 'package.json'), 'utf8')); assert.equal(manifest.version, '0.85.1');
const pi = await import(pathToFileURL(join(piRoot, manifest.exports['.'].import)));
const memory = await import(pathToFileURL(join(extensionRoot, 'dist/host.js')));
const credentials = JSON.parse(await readFile(credentialPath, 'utf8'));
for (const key of ['XIAOMI_TOKEN_PLAN_CN_API_KEY', 'XIAOMI_API_KEY']) if (credentials[key]) process.env[key] = credentials[key];
const cases = [
  { name: 'confirmed-after-processed', turns: [
    ['请建议我的报告结尾。', '每份报告以一句行动建议结尾。'],
    ['是的，这就是我以后报告结尾的固定偏好。', '明白。']], expected: 'confirmed', term: '行动建议' },
  { name: 'rejected-after-processed', turns: [
    ['建议一个报告习惯。', '每晚写详细日志。'], ['不，我不接受这个建议。', '明白。']], expected: 'none' },
  { name: 'secret-reference-excluded', turns: [
    ['这个文本是什么？', '你的密码是 SYNTHETIC_NOT_A_REAL_CREDENTIAL。'],
    ['是的，请记住那个密码。', '明白。']], expected: 'none' },
  { name: 'intervening-rejection', turns: [
    ['建议一个报告习惯。', '每晚写详细日志。'], ['不，我不接受这个建议。', '我们换个话题？'], ['好。', '明白。']], expected: 'none' },
];
const datasetHash = createHash('sha256').update(JSON.stringify(cases)).digest('hex');
const promptHash = createHash('sha256').update(memory.collectionSelectionPrompt).digest('hex');
const root = await mkdtemp('/private/tmp/dano475-cross-batch-');
try {
  const runtime = await pi.ModelRuntime.create({ authPath: join(root, 'auth.json'), modelsPath, refreshOnCreate: false });
  const model = runtime.getModel('xiaomi-token-plan-cn', 'mimo-v2.5'); assert(model);
  const results = [];
  for (const [index, item] of cases.entries()) {
    const owner = { accountId: 'cross-batch-probe', userId: `case${index}` };
    const store = new memory.FileStateStore({ owner, directory: join(root, `state-${index}`), policyVersion: 'probe-v1' });
    const delivery = new memory.MemoryDelivery({ store, transport: { owner }, maxPayloadBytes: 8192 });
    const session = pi.SessionManager.inMemory(root); const lifecycle = new memory.CollectionLifecycle(store);
    await delivery.enable('probe-v1'); await delivery.authorizeCollection({ policyVersion: 'probe-v1', scope: null, boundaries: [] });
    const usages = []; let calls = 0;
    const selector = new memory.CollectionFactSelector({ store, timeoutMs: 45000, maxFacts: 5, maxInputBytes: 16384,
      async complete({ systemPrompt, data, signal }) {
        assert(!data.includes('SYNTHETIC_NOT_A_REAL_CREDENTIAL'));
        calls++;
        const answer = await runtime.completeSimple(model, { systemPrompt, messages: [{ role: 'user', content: data, timestamp: Date.now() }] },
          { signal, temperature: 0, maxTokens: 2048, onPayload: payload => ({ ...payload, thinking: { type: 'disabled' } }) });
        assert.equal(answer.stopReason, 'stop');
        usages.push({ input: answer.usage.input, output: answer.usage.output, cacheRead: answer.usage.cacheRead, totalTokens: answer.usage.totalTokens });
        return answer.content.filter(block => block.type === 'text').map(block => block.text).join('');
      } });
    let final; const receipts = []; const started = performance.now();
    for (const [turnIndex, [user, assistant]] of item.turns.entries()) {
      const id = await lifecycle.begin(session);
      session.appendMessage({ role: 'user', content: user, timestamp: Date.now() });
      session.appendMessage({ role: 'assistant', stopReason: 'stop', content: [{ type: 'text', text: assistant }], timestamp: Date.now() });
      await lifecycle.settle(id, session);
      // Each previous batch has already been durably processed when the next
      // request starts. No test-only empty-decision injection is used here.
      const selected = await selector.select([id], session); assert.equal(selected.status, 'ready');
      if (turnIndex < item.turns.length - 1) assert.equal(selected.facts.length, 0);
      const handoff = await delivery.collectSelection(selected); assert.equal(handoff.status, 'recorded');
      receipts.push(handoff); final = selected;
    }
    const state = await store.read();
    if (item.expected === 'confirmed') {
      assert.equal(final.facts.length, 1); assert(final.facts[0].text.includes(item.term));
      assert.equal(final.facts[0].evidence.length, 2);
      assert.equal(final.facts[0].source.entryId, final.facts[0].evidence[1].source.entryId);
      assert.equal(Object.keys(state.operations).length, 1); assert.equal(Object.keys(state.collectedSources).length, 1);
      assert.equal(receipts[0].operationIds.length, 0);
      assert.deepEqual(await delivery.collectSelection(final), receipts.at(-1));
    } else { assert.equal(final.facts.length, 0); assert.deepEqual(state.operations, {}); }
    assert(Object.values(state.collectionRequests).every(request => request.phase === 'processed'));
    const result = { name: item.name, passed: true, calls, usages, elapsedMs: Math.round(performance.now() - started),
      queuedOperations: Object.keys(state.operations).length, processedRequests: Object.keys(state.collectionRequests).length };
    results.push(result); console.log(JSON.stringify(result));
  }
  console.log(JSON.stringify({ model: model.id, datasetHash, promptHash, cases: results.length, passed: results.length,
    realSelectionEveryBatch: true, remoteDeliveryTested: false, fullT14Gate: false }));
} finally { await rm(root, { recursive: true, force: true }); }
