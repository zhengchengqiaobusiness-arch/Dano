// Run inside a Linux image with the same pi and Heimdall dependencies as Dano.
// Invokes real registered hooks and real tool definitions with synthetic data.
import { mkdtemp, mkdir, writeFile, readFile, symlink, rm } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { pathToFileURL } from 'node:url';
import assert from 'node:assert/strict';

assert.equal(process.platform, 'linux');
const appPackage = process.argv[2];
const appRoot = dirname(appPackage);
const piRoot = join(appRoot, 'node_modules/@earendil-works/pi-coding-agent');
const heimdallRoot = join(appRoot, 'node_modules/@josephyoung/pi-heimdall');
const piPkg = JSON.parse(await readFile(join(piRoot, 'package.json'), 'utf8'));
const heimdallPkg = JSON.parse(await readFile(join(heimdallRoot, 'package.json'), 'utf8'));
const root = await mkdtemp('/tmp/dano465-heimdall.');
const workspace = join(root, 'workspace');
const protectedDir = join(root, 'protected');
const agentDir = join(root, 'agent');
process.env.PI_CODING_AGENT_DIR = agentDir;
process.env.DANO465_SYNTHETIC_KEY = 'DANO465_ENV_MARKER';
const marker = 'DANO465_FILE_MARKER';
try {
  await mkdir(workspace);
  await mkdir(protectedDir, { mode: 0o700 });
  await mkdir(agentDir);
  const target = join(protectedDir, 'synthetic.txt');
  await writeFile(target, marker, { mode: 0o600 });
  await symlink(protectedDir, join(workspace, 'link'));
  await writeFile(join(agentDir, 'heimdall.json'), JSON.stringify({
    sandbox: { enabled: true, userNamespace: false,
      paths: { [protectedDir]: { mode: 'deny' } },
      env: { allow: ['PATH', 'HOME', 'LANG'], deny: [] } },
  }));
  const pi = await import(pathToFileURL(join(piRoot, piPkg.exports['.'].import)).href);
  const loader = new pi.DefaultResourceLoader({ cwd: workspace, agentDir,
    settingsManager: pi.SettingsManager.inMemory(), noExtensions: true,
    noSkills: true, noPromptTemplates: true, noThemes: true, noContextFiles: true,
    additionalExtensionPaths: [join(heimdallRoot, 'extensions/heimdall.ts')] });
  await loader.reload();
  const loaded = loader.getExtensions();
  assert.equal(loaded.errors.length, 0, JSON.stringify(loaded.errors));
  assert.equal(loaded.extensions.length, 1);
  const extension = loaded.extensions[0];
  const notices = [];
  const ctx = { cwd: workspace, hasUI: false,
    ui: { notify: message => notices.push(message), setStatus() {},
      theme: { fg: (_color, text) => text } } };
  for (const handler of extension.handlers.get('session_start') ?? []) {
    await handler({ type: 'session_start' }, ctx);
  }
  assert(notices.some(x => x === 'heimdall sandbox: active'), JSON.stringify(notices));
  const findings = [];
  for (const [channel, path] of [['absolute', target], ['symlink', 'link/synthetic.txt']]) {
    const event = { type: 'tool_call', toolName: 'read', toolCallId: 'synthetic-probe', input: { path } };
    let blocked = false;
    for (const handler of extension.handlers.get('tool_call') ?? []) {
      if ((await handler(event, ctx))?.block) { blocked = true; break; }
    }
    let exposed = false;
    if (!blocked) {
      const result = await pi.createReadToolDefinition(workspace).execute('synthetic-probe', { path });
      exposed = result.content.some(part => part.text?.includes(marker));
    }
    findings.push({ channel, blockedByRegisteredGuard: blocked, syntheticContentExposed: exposed });
  }
  console.log(JSON.stringify({ pi: piPkg.version, heimdall: heimdallPkg.version,
    sandboxActive: true, findings, fullModelPathVerified: false }, null, 2));
} finally {
  delete process.env.DANO465_SYNTHETIC_KEY;
  await rm(root, { recursive: true });
}
