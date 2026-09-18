// Gate prototype: trusted adapter and native tools run under distinct Linux UIDs.
// Requires a root launcher in an isolated container, then drops host privileges.
// Uses only synthetic-account credentials; optional third argument is a private
// connection JSON containing account, key and the real OpenViking endpoint.
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, chmod, chown, writeFile, readFile, symlink, rm } from 'node:fs/promises';
import { join, dirname } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { randomUUID } from 'node:crypto';

if (process.argv[2] === '--worker') {
  const [appPackage, workspace, target, endpoint] = process.argv.slice(3);
  const piRoot = join(dirname(appPackage), 'node_modules/@earendil-works/pi-coding-agent');
  const pkg = JSON.parse(await readFile(join(piRoot, 'package.json'), 'utf8'));
  const pi = await import(pathToFileURL(join(piRoot, pkg.exports['.'].import)));
  await pi.createWriteToolDefinition(workspace).execute('allowed-write', { path: 'allowed.txt', content: 'WORKSPACE_OK' });
  const allowedRead = await pi.createReadToolDefinition(workspace).execute('allowed-read', { path: 'allowed.txt' });
  assert(allowedRead.content.some(x => x.text?.includes('WORKSPACE_OK')));
  const findings = [];
  for (const path of [target, 'link/credential']) {
    for (const [name, tool, args] of [
      ['read', pi.createReadToolDefinition(workspace), { path }],
      ['write', pi.createWriteToolDefinition(workspace), { path, content: 'modified' }],
      ['edit', pi.createEditToolDefinition(workspace), { path, edits: [{ oldText: 'secret', newText: 'modified' }] }],
    ]) {
      let denied = false;
      try { await tool.execute('boundary-probe', args); }
      catch (error) { denied = /EACCES|permission denied/i.test(String(error)); }
      findings.push({ channel: `${name}-${path === target ? 'absolute' : 'symlink'}`, denied });
    }
  }
  const bash = await pi.createBashToolDefinition(workspace).execute('shell-probe', {
    command: 'test -z "$MEMORY_SYNTHETIC_KEY" && test ! -r "$MEMORY_PROBE_TARGET" && echo ISOLATED',
  });
  findings.push({ channel: 'shell-file-and-environment', denied: bash.content.some(x => x.text?.trim() === 'ISOLATED') });
  findings.push({ channel: 'http-without-credential', denied: (await fetch(endpoint)).status === 401 });
  assert(findings.every(x => x.denied), JSON.stringify(findings));
  console.log(JSON.stringify({ workerUid: process.getuid(), workspaceReadWriteAllowed: true, findings }));
} else {
  assert.equal(process.platform, 'linux');
  assert.equal(process.getuid(), 0, 'Run only in a disposable root container');
  const root = await mkdtemp('/tmp/dano465-worker.');
  const workspace = join(root, 'workspace');
  const protectedDir = join(root, 'protected');
  const target = join(protectedDir, 'credential');
  const connection = process.argv[3] ? JSON.parse(await readFile(process.argv[3], 'utf8')) : null;
  if (connection) assert(connection.account.startsWith('barrier-'), 'Requires synthetic probe identity');
  const key = connection?.key ?? randomUUID();
  process.env.MEMORY_SYNTHETIC_KEY = key;
  const server = createServer((req, res) => {
    res.writeHead(req.headers.authorization === `Bearer ${key}` ? 200 : 401);
    res.end();
  });
  try {
    await chmod(root, 0o711);
    await chown(root, 1000, 1000);
    await mkdir(workspace);
    await chown(workspace, 65534, 1000);
    await chmod(workspace, 0o770);
    await mkdir(protectedDir, { mode: 0o700 });
    await chown(protectedDir, 1000, 1000);
    await writeFile(target, key, { mode: 0o600 });
    await chown(target, 1000, 1000);
    await symlink(protectedDir, join(workspace, 'link'));
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const endpoint = connection?.endpoint ?? `http://127.0.0.1:${server.address().port}`;
    assert.equal((await fetch(endpoint, { headers: { authorization: `Bearer ${key}` } })).status, 200);
    process.setgroups([]);
    const child = spawn(process.execPath, [fileURLToPath(import.meta.url), '--worker', process.argv[2], workspace, target, endpoint], {
      uid: 65534, gid: 1000, cwd: workspace,
      env: { PATH: process.env.PATH, HOME: workspace, MEMORY_PROBE_TARGET: target },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let output = '', errors = '';
    child.stdout.on('data', chunk => { output += chunk; });
    child.stderr.on('data', chunk => { errors += chunk; });
    const completion = new Promise((resolve, reject) => { child.on('error', reject); child.on('close', resolve); });
    process.setgid(1000);
    process.setuid(1000);
    const [code, authenticated] = await Promise.all([
      completion, fetch(endpoint, { headers: { authorization: `Bearer ${key}` } }),
    ]);
    assert.equal(authenticated.status, 200);
    assert.equal(code, 0, errors);
    assert.equal(await readFile(target, 'utf8'), key);
    console.log(JSON.stringify({ trustedHttpAccess: true, credentialUnchanged: true,
      trustedHostUid: process.getuid(),
      realOpenVikingHttpVerified: Boolean(connection),
      ...JSON.parse(output), productionIntegrationVerified: false }, null, 2));
  } finally {
    delete process.env.MEMORY_SYNTHETIC_KEY;
    await new Promise(resolve => server.close(resolve));
    await rm(root, { recursive: true });
  }
}
