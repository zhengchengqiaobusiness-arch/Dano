// Exercise real pi local-package install and print-mode lifecycle.
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, writeFile, rm } from 'node:fs/promises';
import { join, dirname, resolve } from 'node:path';
import { spawn } from 'node:child_process';

const piRoot = join(dirname(process.argv[2]), 'node_modules/@earendil-works/pi-coding-agent');
const cli = join(piRoot, 'dist/cli.js');
const root = await mkdtemp('/private/tmp/dano465-cli.');
const agentDir = join(root, 'agent');
const workspace = join(root, 'workspace');
const packageDir = join(root, 'package');
const eventsPath = join(root, 'events.jsonl');
const env = { ...process.env, PI_CODING_AGENT_DIR: agentDir };
async function run(args) {
  const child = spawn(process.execPath, [cli, ...args], { cwd: workspace, env,
    stdio: ['ignore', 'pipe', 'pipe'] });
  let stdout = '', stderr = '';
  child.stdout.on('data', chunk => { stdout += chunk; });
  child.stderr.on('data', chunk => { stderr += chunk; });
  const code = await new Promise((resolve, reject) => {
    child.on('error', reject); child.on('close', resolve);
  });
  // Do not print provider responses/configuration on failure.
  assert.equal(code, 0, `CLI ${args[0]} failed; stdout bytes ${stdout.length}, stderr bytes ${stderr.length}`);
  return stdout;
}
try {
  await Promise.all([mkdir(agentDir), mkdir(workspace), mkdir(packageDir)]);
  const config = await readFile(process.argv[3]);
  await writeFile(join(agentDir, 'models.json'), config, { mode: 0o600 });
  const [provider, definition] = Object.entries(JSON.parse(config).providers)[0];
  assert(definition.models?.[0]?.id, 'Requires a configured model');
  await writeFile(join(packageDir, 'package.json'), JSON.stringify({
    name: 'dano465-install-probe', version: '0.0.0', private: true,
    type: 'module', pi: { extensions: ['./index.mjs'] },
  }));
  const hooks = ['session_start', 'before_agent_start', 'context', 'turn_end', 'agent_end', 'session_shutdown'];
  await writeFile(join(packageDir, 'index.mjs'), `import { appendFileSync } from 'node:fs';
export default pi => { for (const type of ${JSON.stringify(hooks)}) pi.on(type, event => {
appendFileSync(${JSON.stringify(eventsPath)}, JSON.stringify({type:event.type,reason:event.reason})+'\\n');
}); };`);
  await run(['install', packageDir]);
  const settings = JSON.parse(await readFile(join(agentDir, 'settings.json'), 'utf8'));
  const matchesPackage = entry => resolve(agentDir, typeof entry === 'string' ? entry : entry.source) === packageDir;
  assert(settings.packages.some(matchesPackage));
  const output = await run(['--no-session', '--no-tools', '--provider', provider,
    '--model', definition.models[0].id, '--thinking', 'off', '-p', '这是合成测试，请只回复 CLI_MEMORY_OK。']);
  assert(output.includes('CLI_MEMORY_OK'), 'Expected synthetic model response missing');
  const events = (await readFile(eventsPath, 'utf8')).trim().split('\n').map(JSON.parse);
  for (const hook of hooks) assert(events.some(event => event.type === hook), `Missing ${hook}`);
  await run(['remove', packageDir]);
  const removed = JSON.parse(await readFile(join(agentDir, 'settings.json'), 'utf8'));
  assert(!(removed.packages ?? []).some(matchesPackage));
  console.log(JSON.stringify({ localPackageInstall: true, realCliModelTurn: true,
    installedHooksExecuted: events, localPackageRemove: true, registryPublicationVerified: false }, null, 2));
} finally { await rm(root, { recursive: true }); }
