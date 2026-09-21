// Actual pi request hooks -> SIGKILL -> source recovery -> real configured model selection.
// No OpenViking delivery or browser consent is claimed here.
// Args: Dano app directory, private models.json, private production-input.json.
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { fork } from 'node:child_process';
import { once } from 'node:events';
import { setTimeout as delay } from 'node:timers/promises';
const [appRoot, modelsPath, credentialPath, mode, existingRoot] = process.argv.slice(2);
const piRoot = join(appRoot, 'node_modules/@earendil-works/pi-coding-agent');
const extensionRoot = join(appRoot, 'node_modules/@josephyoung/pi-openviking');
const appManifest = JSON.parse(await readFile(join(appRoot, 'package.json'), 'utf8'));
const extensionManifest = JSON.parse(await readFile(join(extensionRoot, 'package.json'), 'utf8'));
assert.equal(extensionManifest.version, appManifest.dependencies['@josephyoung/pi-openviking']);
const manifest = JSON.parse(await readFile(join(piRoot, 'package.json'), 'utf8'));
assert.equal(manifest.version, appManifest.dependencies['@earendil-works/pi-coding-agent']);
const pi = await import(pathToFileURL(join(piRoot, manifest.exports['.'].import)));
const memory = await import(pathToFileURL(join(extensionRoot, 'dist/host.js')));
const secrets = JSON.parse(await readFile(credentialPath, 'utf8'));
for (const key of ['XIAOMI_TOKEN_PLAN_CN_API_KEY', 'XIAOMI_API_KEY']) if (secrets[key]) process.env[key] = secrets[key];
const root = existingRoot ?? await mkdtemp('/private/tmp/dano475-host-restart-');
const owner = { accountId: 'host-restart-probe', userId: 'synthetic' };
const store = new memory.FileStateStore({ owner, directory: join(root, 'state'), policyVersion: 'probe-v1' });
const client = { owner, async recall() { return []; } };
const delivery = new memory.MemoryDelivery({ store, transport: client, maxPayloadBytes: 8192 });
const registry = new memory.CollectionSessionRegistry({ store, sessionRoot: join(root, 'sessions') });
if (mode === 'chat') {
  const cwd = join(root, 'workspace'), agentDir = join(root, 'agent');
  await mkdir(cwd); await mkdir(agentDir, { mode: 0o700 }); await mkdir(join(root, 'sessions'), { mode: 0o700 });
  await writeFile(join(agentDir, 'models.json'), await readFile(modelsPath), { mode: 0o600 });
  const manager = pi.SessionManager.create(cwd, join(root, 'sessions'));
  await registry.register(manager);
  await delivery.enable('probe-v1');
  await delivery.authorizeCollection({ policyVersion: 'probe-v1', scope: null, boundaries: await registry.boundaries() });
  let wakes = 0;
  const extension = memory.createOpenVikingExtension({ owner, client, stateStore: store,
    async assertToolIsolation() {}, wakeDelivery() {},
    collection: { sessions: registry, lifecycleTimeoutMs: 5000, wake() { wakes++; } },
    policy: { maxPayloadBytes: 8192, recallTimeoutMs: 100, recallTokenBudget: 100,
      recallLimit: 1, minimumScore: 0.5, countTokens: text => text.length } });
  const services = await pi.createAgentSessionServices({ cwd, agentDir,
    settingsManager: pi.SettingsManager.inMemory({ compaction: { enabled: false }, retry: { enabled: false } }),
    resourceLoaderOptions: { noExtensions: true, noSkills: true, noPromptTemplates: true, noThemes: true,
      noContextFiles: true, extensionFactories: [{ name: 'collection-host-restart', factory: extension }] } });
  const model = services.modelRuntime.getModel('xiaomi-token-plan-cn', 'mimo-v2.5'); assert(model);
  const { session, extensionsResult } = await pi.createAgentSessionFromServices({ services, sessionManager: manager,
    model, noTools: 'all', thinkingLevel: 'off' });
  assert.equal(extensionsResult.errors.length, 0);
  const errors = []; await session.bindExtensions({ onError: error => errors.push(error) });
  await session.prompt('我以后写报告固定使用简体中文，并且喜欢短句。请仅回复“明白”。');
  await session.prompt('我的密码是 SYNTHETIC_NOT_A_REAL_CREDENTIAL。请仅回复“明白”。');
  assert.equal(errors.length, 0); assert.equal(wakes, 2);
  const state = await store.read();
  assert.equal(Object.keys(state.collectionRequests).length, 2);
  assert(Object.values(state.collectionRequests).every(request => request.phase === 'settled'));
  assert.equal(Object.keys(state.collectionSessionFiles).length, 1);
  assert.deepEqual(state.operations, {});
  setInterval(() => {}, 1000);
  process.send({ settled: true, wakes });
  await new Promise(() => {});
} else {
  const child = fork(fileURLToPath(import.meta.url), [appRoot, modelsPath, credentialPath, 'chat', root], { stdio: ['ignore', 'ignore', 'pipe', 'ipc'] });
  let stderr = ''; child.stderr.on('data', data => { stderr += data; });
  const exited = once(child, 'exit'); let receipt;
  try {
    receipt = await Promise.race([once(child, 'message', { signal: AbortSignal.timeout(120000) }).then(([message]) => message),
      exited.then(() => { throw new Error('Chat process exited before durable settlement'); })]);
    assert(receipt.settled);
  } finally { child.kill('SIGKILL'); }
  assert.equal((await exited)[1], 'SIGKILL'); assert.equal(stderr, '');
  // New process/runtime has no live session manager, viewer or extension context.
  const runtime = await pi.ModelRuntime.create({ authPath: join(root, 'selection-auth.json'), modelsPath, refreshOnCreate: false });
  const model = runtime.getModel('xiaomi-token-plan-cn', 'mimo-v2.5'); assert(model);
  let modelCalls = 0, wakes = 0; const usages = [];
  const selector = new memory.CollectionFactSelector({ store, maxInputBytes: 16384, maxFacts: 5, timeoutMs: 45000,
    sensitiveValues: () => Object.values(secrets).filter(value => typeof value === 'string' && value.length >= 12),
    async complete({ systemPrompt, data, signal }) {
      assert(!data.includes('SYNTHETIC_NOT_A_REAL_CREDENTIAL'));
      modelCalls++;
      const answer = await runtime.completeSimple(model, { systemPrompt, messages: [{ role: 'user', content: data, timestamp: Date.now() }] },
        { signal, temperature: 0, maxTokens: 2048, onPayload: payload => ({ ...payload, thinking: { type: 'disabled' } }) });
      assert.equal(answer.stopReason, 'stop');
      usages.push({ input: answer.usage.input, output: answer.usage.output, cacheRead: answer.usage.cacheRead, totalTokens: answer.usage.totalTokens });
      return answer.content.filter(block => block.type === 'text').map(block => block.text).join('');
    } });
  const scheduler = new memory.CollectionScheduler({ store, delivery, selector,
    resolveSession: (id, signal) => registry.resolveSession(id, signal), pollIntervalMs: 100, mergeWindowMs: 200,
    maxWaitMs: 1000, workTimeoutMs: 50000, leaseMs: 110000, initialBackoffMs: 1000, maxBackoffMs: 2000,
    maxAttempts: 2, maxRequestsPerBatch: 10, wakeDelivery() { wakes++; } });
  scheduler.start(); let state;
  try {
    const deadline = Date.now() + 110000;
    while (Date.now() < deadline) {
      state = await store.read();
      if (Object.values(state.collectionRequests).every(request => request.phase === 'processed')) break;
      assert(!Object.values(state.collectionRequests).some(request => request.phase === 'selection_failed'));
      await delay(100);
    }
    assert(Object.values(state.collectionRequests).every(request => request.phase === 'processed'));
  } finally { await scheduler.stop(); }
  assert.equal(modelCalls, 1); assert.equal(wakes, 1);
  const operations = Object.values(state.operations); assert.equal(operations.length, 1);
  assert.equal(operations[0].collectionSources.length, 1);
  assert(operations[0].payload.includes('简体中文')); assert(operations[0].payload.includes('短句'));
  assert(!operations[0].payload.includes('SYNTHETIC_NOT_A_REAL_CREDENTIAL'));
  const report = { realAgentSession: true, model: model.id, durableSettledBeforeSigkill: 2,
    restartWithoutViewer: true, sessionRecoveredFromOwnerRegistry: true, originalSourceOnly: true,
    secretExcludedBeforeSelectionModel: true, modelCalls, usages, queuedOperations: 1, factSources: 1,
    remoteMemoryDeliveryTested: false, browserVerified: false };
  await writeFile(join(root, 'result.json'), JSON.stringify(report, null, 2), { mode: 0o600 });
  console.log(JSON.stringify({ root, ...report }));
}
