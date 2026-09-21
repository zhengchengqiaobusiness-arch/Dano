// Real locked pi + configured model probe. No OpenViking delivery is claimed.
// Args: independent extension checkout, private models.json, private production-input.json.
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, writeFile, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const extensionRoot = process.argv[2];
const piRoot = join(extensionRoot, 'node_modules/@earendil-works/pi-coding-agent');
const piPackage = JSON.parse(await readFile(join(piRoot, 'package.json'), 'utf8'));
assert.equal(piPackage.version, '0.85.1', 'Revalidate lifecycle on a different pi version');
const pi = await import(pathToFileURL(join(piRoot, piPackage.exports['.'].import)));
const memory = await import(pathToFileURL(join(extensionRoot, 'dist/host.js')));
const privateConfig = JSON.parse(await readFile(process.argv[4], 'utf8'));
for (const key of ['XIAOMI_TOKEN_PLAN_CN_API_KEY', 'XIAOMI_API_KEY']) {
  if (privateConfig[key]) process.env[key] = privateConfig[key];
}
const root = await mkdtemp('/private/tmp/dano475-settlement-');
let session;
try {
  const cwd = join(root, 'workspace'), agentDir = join(root, 'agent');
  await mkdir(cwd); await mkdir(agentDir);
  await writeFile(join(agentDir, 'models.json'), await readFile(process.argv[3]), { mode: 0o600 });
  const owner = { accountId: 'settlement-probe', userId: 'synthetic' };
  const stateStore = new memory.FileStateStore({ owner, directory: join(root, 'private'), policyVersion: 'probe-v1' });
  // This probe has no active tools and deliberately tests no remote memory calls.
  const client = { owner, async recall() { return []; } };
  const delivery = new memory.MemoryDelivery({ store: stateStore, transport: client, maxPayloadBytes: 8192 });
  const manager = pi.SessionManager.create(cwd, join(root, 'sessions'));
  await delivery.enable('probe-v1');
  await delivery.authorizeCollection({ policyVersion: 'probe-v1', scope: null,
    boundaries: [{ sessionId: manager.getSessionId(), entryId: manager.getLeafId(), branchId: manager.getLeafId() }] });
  const host = memory.createOpenVikingExtension({ owner, client, stateStore,
    assertToolIsolation: async () => {}, wakeDelivery() {},
    policy: { maxPayloadBytes: 8192, recallTimeoutMs: 100, recallTokenBudget: 100,
      recallLimit: 1, minimumScore: 0.5, countTokens: text => text.length } });
  const observations = [];
  const services = await pi.createAgentSessionServices({ cwd, agentDir,
    settingsManager: pi.SettingsManager.inMemory({ compaction: { enabled: false }, retry: { enabled: false } }),
    resourceLoaderOptions: { noExtensions: true, noSkills: true, noPromptTemplates: true,
      noThemes: true, noContextFiles: true, extensionFactories: [{ name: 'collection-probe', factory(api) {
        host(api);
        for (const type of ['before_agent_start', 'turn_end', 'agent_end', 'agent_settled']) {
          api.on(type, async (_event, ctx) => {
            const state = await stateStore.read();
            observations.push({ type, entries: ctx.sessionManager.getBranch().filter(entry => entry.type === 'message').length,
              requests: Object.values(state.collectionRequests ?? {}).map(request => ({ id: request.id, phase: request.phase })) });
          });
        }
      } }] },
  });
  const model = services.modelRuntime.getModel('xiaomi-token-plan-cn', 'mimo-v2.5');
  assert(model, 'Configured MiMo model missing');
  const created = await pi.createAgentSessionFromServices({ services, sessionManager: manager,
    model, noTools: 'all', thinkingLevel: 'off' });
  session = created.session;
  assert.equal(created.extensionsResult.errors.length, 0);
  const errors = [];
  await session.bindExtensions({ onError: error => errors.push(error) });
  for (let i = 0; i < 2; i++) {
    await session.prompt(`Synthetic lifecycle probe ${i + 1}. Reply only SETTLEMENT_OK.`);
  }
  assert.equal(errors.length, 0, 'Extension lifecycle error');
  const requests = Object.values((await stateStore.read()).collectionRequests ?? {});
  assert.equal(requests.length, 2);
  assert(requests.every(request => request.phase === 'settled'));
  assert.equal(new Set(requests.flatMap(request => request.sourceEntries)).size,
    requests.reduce((count, request) => count + request.sourceEntries.length, 0));
  assert.deepEqual(observations.filter(item => item.type === 'before_agent_start').map(item => item.entries), [0, 2]);
  assert(observations.filter(item => ['turn_end', 'agent_end'].includes(item.type)).every(item => item.requests.at(-1).phase === 'running'));
  assert(observations.filter(item => item.type === 'agent_settled').every(item => item.requests.at(-1).phase === 'settled'));
  assert.deepEqual((await stateStore.read()).operations, {});
  console.log(JSON.stringify({ piVersion: piPackage.version, model: 'mimo-v2.5', realAgentSession: true, completedRequests: requests.length,
    sourceCounts: requests.map(request => request.sourceEntries.length), observations,
    remoteMemoryDeliveryTested: false, privacySelectionTested: false }, null, 2));
} finally {
  if (session) await session.dispose();
  await rm(root, { recursive: true, force: true });
}
