/** Follow-up export probe on synthetic owners created by the clear fixture. */
import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
const [modulePath, configPath, root] = process.argv.slice(2);
assert(root?.startsWith('/private/tmp/dano476-clear-coordinator-'));
const credentials = JSON.parse(await readFile(`${root}/credentials.json`, 'utf8'));
assert(/^clear-[a-f0-9-]+$/.test(credentials.accountId));
const config = JSON.parse(await readFile(configPath, 'utf8'));
assert(['localhost', '127.0.0.1'].includes(config.server.host));
const { OwnerMemoryClient, FileStateStore, MemoryExportService } = await import(pathToFileURL(modulePath));
function service(userId, scope = null) {
  const owner = { accountId: credentials.accountId, userId };
  const transport = new OwnerMemoryClient({ owner, scope,
    baseUrl: `http://${config.server.host}:${config.server.port}`,
    apiKey: credentials.keys[userId], timeoutMs: 90000 });
  const store = new FileStateStore({ owner, directory: `${root}/${userId}`, policyVersion: 'clear-probe' });
  return new MemoryExportService(store, transport);
}
async function all(exporter) {
  const items = [];
  let cursor;
  for (let page = 0; page < 100; page++) {
    const result = await exporter.page({ limit: 1, cursor });
    items.push(...result.items);
    if (!result.nextCursor) return items;
    cursor = result.nextCursor;
  }
  throw new Error('EXPORT_PAGINATION_LIMIT');
}
const alice = await all(service('alice'));
const project = await all(service('alice', 'project-a'));
const bob = await all(service('bob'));
assert(alice.some(item => item.content.includes('青岚旧版小结')));
assert(!alice.some(item => item.content.includes('杉溪项目小结') || item.content.includes('榆湾用户小结')));
assert(!project.some(item => item.content.includes('杉溪项目小结') || item.content.includes('青岚旧版小结')));
assert(bob.some(item => item.content.includes('榆湾用户小结')));
assert(!bob.some(item => item.content.includes('青岚旧版小结') || item.content.includes('杉溪项目小结')));
assert(alice.some(item => item.sources.some(source => source.kind === 'explicit' && source.entryId === 'new-explicit-source')));
assert(!JSON.stringify({ alice, project, bob }).includes(credentials.keys.alice));
assert(!JSON.stringify({ alice, project, bob }).includes(credentials.keys.bob));
const result = { userScopedExport: true, projectScopedExport: true, paginated: true,
  sourceMetadata: true, noCredentials: true, browserVerified: false };
await writeFile(`${root}/export-result.json`, JSON.stringify(result), { mode: 0o600 });
console.log(JSON.stringify(result));
