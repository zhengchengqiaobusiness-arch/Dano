// Real pi loader probe; this is not the finished memory extension or a model test.
import { mkdtemp, mkdir, writeFile, readFile, rm } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { pathToFileURL } from 'node:url';
import assert from 'node:assert/strict';

const appPackage = process.argv[2];
if (!appPackage) throw new Error('Pass the Dano app package.json path');
const piRoot = join(dirname(appPackage), 'node_modules/@earendil-works/pi-coding-agent');
const pkg = JSON.parse(await readFile(join(piRoot, 'package.json'), 'utf8'));
const { DefaultResourceLoader, SettingsManager } = await import(
  pathToFileURL(join(piRoot, pkg.exports['.'].import)).href
);
const root = await mkdtemp('/private/tmp/dano-465-pi-entrypoints.');
const hooks = ['session_start', 'before_agent_start', 'context', 'turn_end',
  'agent_end', 'session_before_fork', 'session_tree', 'session_shutdown'];
try {
  const cwd = join(root, 'workspace');
  const agentDir = join(root, 'agent');
  await mkdir(cwd);
  await mkdir(agentDir);
  const standardPath = join(root, 'standard.mjs');
  await writeFile(standardPath, `export default function(pi) {
    for (const event of ${JSON.stringify(hooks)}) pi.on(event, async () => {});
  }\n`);
  const common = { cwd, agentDir, settingsManager: SettingsManager.inMemory(),
    noExtensions: true, noSkills: true, noPromptTemplates: true,
    noThemes: true, noContextFiles: true };
  const standard = new DefaultResourceLoader({ ...common,
    additionalExtensionPaths: [standardPath] });
  const host = new DefaultResourceLoader({ ...common,
    extensionFactories: [{ name: 'memory-probe', factory(pi) {
      for (const event of hooks) pi.on(event, async () => {});
    } }] });
  const results = [];
  for (const [mode, loader] of [['standard', standard], ['host', host]]) {
    await loader.reload();
    const loaded = loader.getExtensions();
    assert.equal(loaded.errors.length, 0, JSON.stringify(loaded.errors));
    assert.equal(loaded.extensions.length, 1);
    const registered = [...loaded.extensions[0].handlers.keys()];
    assert.deepEqual(registered, hooks);
    await loader.reload();
    assert.equal(loader.getExtensions().extensions.length, 1);
    results.push({ mode, registeredHooks: registered, extensionsAfterReload: 1 });
  }
  console.log(JSON.stringify({ piVersion: pkg.version, results,
    eventExecutionVerified: false, modelCallVerified: false }, null, 2));
} finally {
  await rm(root, { recursive: true });
}
