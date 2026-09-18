// Real pi runtime replacement and model-event probe using synthetic prompts.
// Arguments: Dano app package.json, existing configured models.json.
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, writeFile, rm } from 'node:fs/promises';
import { appendFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { pathToFileURL } from 'node:url';

const piRoot = join(dirname(process.argv[2]), 'node_modules/@earendil-works/pi-coding-agent');
const pkg = JSON.parse(await readFile(join(piRoot, 'package.json'), 'utf8'));
const pi = await import(pathToFileURL(join(piRoot, pkg.exports['.'].import)));
const root = await mkdtemp('/private/tmp/dano465-lifecycle.');
const hooks = ['session_start', 'before_agent_start', 'context', 'turn_end',
  'agent_end', 'session_before_fork', 'session_tree', 'session_shutdown'];
const configuredProviders = Object.keys(JSON.parse(await readFile(process.argv[3], 'utf8')).providers ?? {});
try {
  const results = [];
  for (const mode of ['standard', 'host']) {
    const cwd = join(root, mode), agentDir = join(root, `${mode}-agent`);
    await mkdir(cwd); await mkdir(agentDir);
    await writeFile(join(agentDir, 'models.json'), await readFile(process.argv[3]), { mode: 0o600 });
    const log = join(root, `${mode}-events.jsonl`);
    const recorder = (event, ctx) => appendFileSync(log, JSON.stringify({
      type: event.type, reason: event.reason, sessionId: ctx.sessionManager.getSessionId(),
      leafId: ctx.sessionManager.getLeafId(),
    }) + '\n');
    const standard = join(root, `${mode}.mjs`);
    await writeFile(standard, `import { appendFileSync } from 'node:fs';
export default pi => { for (const type of ${JSON.stringify(hooks)})
pi.on(type, (event, ctx) => { appendFileSync(${JSON.stringify(log)}, JSON.stringify({
type: event.type, reason: event.reason, sessionId: ctx.sessionManager.getSessionId(),
leafId: ctx.sessionManager.getLeafId() }) + '\\n'); }); };`);
    const factory = async ({ cwd, agentDir, sessionManager, sessionStartEvent }) => {
      const services = await pi.createAgentSessionServices({ cwd, agentDir,
        settingsManager: pi.SettingsManager.inMemory({ compaction: { enabled: false }, retry: { enabled: false } }),
        resourceLoaderOptions: { noExtensions: true, noSkills: true, noPromptTemplates: true,
          noThemes: true, noContextFiles: true,
          ...(mode === 'standard' ? { additionalExtensionPaths: [standard] } :
            { extensionFactories: [{ name: 'lifecycle-probe', factory(api) {
              for (const hook of hooks) api.on(hook, recorder);
            } }] }),
        },
      });
      const available = await services.modelRuntime.getAvailable();
      const model = available.find(model => configuredProviders.includes(model.provider));
      assert(model, 'Configured probe provider unavailable');
      const created = await pi.createAgentSessionFromServices({ services, sessionManager,
        sessionStartEvent, model, noTools: 'all', thinkingLevel: 'off' });
      assert.equal(created.extensionsResult.errors.length, 0);
      return { ...created, services, diagnostics: services.diagnostics };
    };
    const runtime = await pi.createAgentSessionRuntime(factory, { cwd, agentDir,
      sessionManager: pi.SessionManager.create(cwd, join(root, `${mode}-sessions`)) });
    const extensionErrors = [];
    const bindings = { onError: error => extensionErrors.push(error) };
    try {
      runtime.setRebindSession(session => session.bindExtensions(bindings));
      await runtime.session.bindExtensions(bindings);
      await runtime.session.prompt('这是合成验证，请只回复 LIFECYCLE_OK。');
      const entries = runtime.session.sessionManager.getEntries();
      const user = entries.find(entry => entry.type === 'message' && entry.message.role === 'user');
      const assistant = entries.find(entry => entry.type === 'message' && entry.message.role === 'assistant');
      assert(user && assistant && assistant.message.stopReason !== 'error', 'Real model turn failed');
      const originalId = runtime.session.sessionManager.getSessionId();
      assert.equal((await runtime.session.navigateTree(user.id, { summarize: false })).cancelled, false);
      assert.equal((await runtime.fork(user.id, { position: 'at' })).cancelled, false);
      assert.notEqual(runtime.session.sessionManager.getSessionId(), originalId);
      assert(runtime.session.sessionManager.getEntries().some(entry => entry.id === user.id));
      await runtime.session.reload();
      assert(runtime.session.sessionManager.getEntries().some(entry => entry.id === user.id));
      await runtime.newSession();
    } finally { await runtime.dispose(); }
    const events = (await readFile(log, 'utf8')).trim().split('\n').map(JSON.parse);
    assert.equal(extensionErrors.length, 0, 'Extension hook reported an error');
    for (const type of hooks) {
      assert(events.some(event => event.type === type), `Missing ${mode} ${type}`);
    }
    assert(events.some(event => event.type === 'session_start' && event.reason === 'reload'));
    results.push({ mode, realModelTurn: true, forkPreservesSourceEntryId: true,
      runtimeReplacementVerified: true, events: events.map(({ type, reason }) => ({ type, reason })),
      treeNavigationVerified: true, cliPackageInstallationVerified: false });
  }
  console.log(JSON.stringify({ piVersion: pkg.version, results }, null, 2));
} finally { await rm(root, { recursive: true }); }
