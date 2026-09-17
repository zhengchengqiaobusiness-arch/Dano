import { mkdtemp, mkdir, writeFile, readFile, symlink, rm } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { join, dirname } from 'node:path';

const packageRoot = process.argv[2];
if (!packageRoot) throw new Error('Pass the Dano app package.json path');
const piRoot = join(dirname(packageRoot), 'node_modules/@earendil-works/pi-coding-agent');
const pkg = JSON.parse(await readFile(join(piRoot, 'package.json'), 'utf8'));
const pi = await import(pathToFileURL(join(piRoot, pkg.exports['.'].import)).href);
const root = await mkdtemp('/private/tmp/dano-465-boundary.');
const workspace = join(root, 'workspace');
const protectedDir = join(root, 'protected');
const marker = 'DANO_465_SYNTHETIC_MARKER';
const findings = [];
try {
  await mkdir(workspace);
  await mkdir(protectedDir, { mode: 0o700 });
  const target = join(protectedDir, 'synthetic.txt');
  await writeFile(target, marker, { mode: 0o600 });
  await symlink(protectedDir, join(workspace, 'link'));
  for (const [name, path] of [['absolute', target], ['symlink', 'link/synthetic.txt']]) {
    const tool = pi.createReadToolDefinition(workspace);
    const result = await tool.execute('probe', { path });
    findings.push({ channel: `read-${name}`, outsideWorkspaceAccessible: result.content.some(x => x.text?.includes(marker)) });
  }
  const write = pi.createWriteToolDefinition(workspace);
  await write.execute('probe', { path: target, content: 'SYNTHETIC_WRITE' });
  findings.push({ channel: 'write-absolute', outsideWorkspaceAccessible: await readFile(target, 'utf8') === 'SYNTHETIC_WRITE' });
  const edit = pi.createEditToolDefinition(workspace);
  await edit.execute('probe', { path: target, edits: [{ oldText: 'SYNTHETIC_WRITE', newText: 'SYNTHETIC_EDIT' }] });
  findings.push({ channel: 'edit-absolute', outsideWorkspaceAccessible: await readFile(target, 'utf8') === 'SYNTHETIC_EDIT' });
  console.log(JSON.stringify({ syntheticDataOnly: true, findings }, null, 2));
} finally {
  await rm(root, { recursive: true });
}
