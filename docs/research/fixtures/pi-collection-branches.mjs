// Real pi fork/tree/reload with the installed published extension and real MiMo.
// Args: Dano app directory, private models.json, private production-input.json.
// No remote memory delivery or Dano browser coverage is claimed by this fixture.
import assert from 'node:assert/strict';
import { readFile, writeFile, mkdir, mkdtemp, chmod } from 'node:fs/promises';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const [appRoot, modelsPath, credentialsPath] = process.argv.slice(2);
const piRoot = join(appRoot, 'node_modules/@earendil-works/pi-coding-agent');
const piManifest = JSON.parse(await readFile(join(piRoot, 'package.json'), 'utf8'));
assert.equal(piManifest.version, '0.85.1');
const pi = await import(pathToFileURL(join(piRoot, piManifest.exports['.'].import)));
const extensionRoot = join(appRoot, 'node_modules/@josephyoung/pi-openviking');
const extensionManifest = JSON.parse(await readFile(join(extensionRoot, 'package.json'), 'utf8'));
assert.equal(extensionManifest.version, '0.1.3');
const memory = await import(pathToFileURL(join(extensionRoot, extensionManifest.exports['./host'].import)));
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
for (const key of ['XIAOMI_TOKEN_PLAN_CN_API_KEY', 'XIAOMI_API_KEY']) if (credentials[key]) process.env[key] = credentials[key];
const root = await mkdtemp('/private/tmp/dano475-real-branches-'); await chmod(root, 0o700);
const cwd = join(root, 'workspace'), agentDir = join(root, 'agent'), sessionRoot = join(root, 'sessions');
for (const path of [cwd, agentDir, sessionRoot]) await mkdir(path, { mode: 0o700 });
await writeFile(join(agentDir, 'models.json'), await readFile(modelsPath), { mode: 0o600 });
const owner = { accountId: 'synthetic-branches', userId: 'alice' };
const store = new memory.FileStateStore({ owner, directory: join(root, 'state'), policyVersion: 'branch-v1' });
const client = { owner, async recall() { return []; } };
const delivery = new memory.MemoryDelivery({ store, transport: client, maxPayloadBytes: 8192 });
const registry = new memory.CollectionSessionRegistry({ store, sessionRoot });
await delivery.enable('branch-v1');
await delivery.authorizeCollection({ policyVersion: 'branch-v1', scope: null, boundaries: [] });
const errors = []; let modelRuntime, model, modelCalls = 0;
const bindings = { onError: error => errors.push(error) };
const runtime = await pi.createAgentSessionRuntime(async ({ sessionManager, sessionStartEvent }) => {
  const extension = memory.createOpenVikingExtension({ owner, client, stateStore: store,
    async assertToolIsolation() {}, wakeDelivery() {},
    collection: { sessions: registry, policyVersion: 'branch-v1', lifecycleTimeoutMs: 5000, wake() {} },
    policy: { maxPayloadBytes: 8192, recallTimeoutMs: 100, recallTokenBudget: 100,
      recallLimit: 1, minimumScore: 0.5, countTokens: text => text.length } });
  const services = await pi.createAgentSessionServices({ cwd, agentDir,
    settingsManager: pi.SettingsManager.inMemory({ compaction: { enabled: false }, retry: { enabled: false } }),
    resourceLoaderOptions: { noExtensions: true, noSkills: true, noPromptTemplates: true, noThemes: true,
      noContextFiles: true, extensionFactories: [{ name: 'branch-probe', factory: extension }] } });
  modelRuntime = services.modelRuntime;
  model = modelRuntime.getModel('xiaomi-token-plan-cn', 'mimo-v2.5'); assert(model);
  const created = await pi.createAgentSessionFromServices({ services, sessionManager, sessionStartEvent,
    model, noTools: 'all', thinkingLevel: 'off' });
  assert.equal(created.extensionsResult.errors.length, 0);
  return { ...created, services, diagnostics: services.diagnostics };
}, { cwd, agentDir, sessionManager: pi.SessionManager.create(cwd, sessionRoot) });
const selector = new memory.CollectionFactSelector({ store, maxInputBytes: 16384, maxFacts: 5, timeoutMs: 45000,
  sensitiveValues: () => Object.values(credentials).filter(value => typeof value === 'string' && value.length >= 12),
  async complete({ systemPrompt, data, signal }) {
    modelCalls++;
    const response = await modelRuntime.completeSimple(model, { systemPrompt,
      messages: [{ role: 'user', content: data, timestamp: Date.now() }] }, {
      signal, temperature: 0, maxTokens: 2048, onPayload: payload => ({ ...payload, thinking: { type: 'disabled' } }),
    });
    assert.equal(response.stopReason, 'stop');
    return response.content.filter(block => block.type === 'text').map(block => block.text).join('');
  } });
const checkpoints = [];
async function turn(prompt, expectedFacts, expectedOperations) {
  await runtime.session.prompt(prompt);
  assert.equal(errors.length, 0, 'Lifecycle hook failed');
  const state = await store.read();
  const ids = Object.values(state.collectionRequests ?? {}).filter(r => r.phase === 'settled').map(r => r.id);
  assert.equal(ids.length, 1, 'Expected one new settled source request');
  const selection = await selector.select(ids, runtime.session.sessionManager);
  assert.equal(selection.status, 'ready'); assert.equal(selection.facts.length, expectedFacts);
  const receipt = await delivery.collectSelection(selection); assert.equal(receipt.status, 'recorded');
  const next = await store.read(); assert.equal(Object.keys(next.operations).length, expectedOperations);
  assert(Object.values(next.collectionRequests).every(r => r.phase === 'processed'));
  checkpoints.push({ facts: selection.facts.length, operations: Object.keys(next.operations).length });
}
try {
  runtime.setRebindSession(session => session.bindExtensions(bindings));
  await runtime.session.bindExtensions(bindings);
  await turn('我长期固定把技术周报的最后一节叫做「柳湾复盘」。只回复收到，不调用工具。', 1, 1);
  const ancestor = runtime.session.sessionManager.getEntries().find(e => e.type === 'message' && e.message.role === 'user');
  const assistant = runtime.session.sessionManager.getEntries().find(e => e.type === 'message' && e.message.role === 'assistant');
  assert(ancestor && assistant);
  const oldSession = runtime.session.sessionManager.getSessionId();
  assert.equal((await runtime.fork(assistant.id, { position: 'at' })).cancelled, false);
  assert.notEqual(runtime.session.sessionManager.getSessionId(), oldSession);
  assert(runtime.session.sessionManager.getEntry(ancestor.id));
  await turn('这是一句合成普通问候，不包含新的个人信息。只回复你好。', 0, 1);
  assert.equal((await runtime.session.navigateTree(ancestor.id, { summarize: false })).cancelled, false);
  await turn('我长期固定用简体中文写项目总结。只回复收到，不调用工具。', 1, 2);
  await runtime.session.reload();
  await turn('这是一句重新加载后的合成普通问候，不增加个人信息。只回复你好。', 0, 2);
  const state = await store.read();
  const sources = Object.values(state.operations).flatMap(o => o.collectionSources);
  assert.equal(sources.filter(s => s.entryId === ancestor.id).length, 1);
  assert.equal(new Set(sources.map(s => s.entryId)).size, 2);
  const report = { piVersion: piManifest.version, extensionVersion: extensionManifest.version,
    model: model.id, realFork: true, realTreeNavigation: true, realReload: true,
    sharedAncestorCollectedOnce: true, checkpoints, modelCalls,
    remoteMemoryDeliveryTested: false, browserVerified: false };
  await writeFile(join(root, 'result.json'), JSON.stringify(report, null, 2), { mode: 0o600 });
  console.log(JSON.stringify({ root, ...report }));
} finally { await runtime.dispose(); }
