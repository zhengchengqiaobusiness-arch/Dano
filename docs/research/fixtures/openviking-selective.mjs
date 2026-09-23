/** Synthetic shared-document correction and forget against isolated OpenViking. */
import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
const [modulePath, configPath, root] = process.argv.slice(2);
assert(root?.startsWith('/private/tmp/dano476-clear-coordinator-'));
const credentials = JSON.parse(await readFile(`${root}/credentials.json`, 'utf8'));
const config = JSON.parse(await readFile(configPath, 'utf8'));
assert(['localhost', '127.0.0.1'].includes(config.server.host));
const { OwnerMemoryClient, FileStateStore, MemorySelectiveService } = await import(pathToFileURL(modulePath));
const owner = { accountId: credentials.accountId, userId: 'alice' };
const client = new OwnerMemoryClient({ owner, baseUrl: `http://${config.server.host}:${config.server.port}`,
  apiKey: credentials.keys.alice, timeoutMs: 90000 });
const store = new FileStateStore({ owner, directory: `${root}/alice`, policyVersion: 'clear-probe' });
const original = (await client.listMemoryDocuments()).find(uri => uri.includes('/preferences/') && uri.endsWith('.md'));
assert(original);
const originalContent = await client.readMemory(original);
const selected = originalContent.split('\n').find(line => line.includes('青岚旧版小结'));
assert(selected);
const unrelated = '- 项目外无关偏好：周报使用蓝色标题';
await client.replaceMemory(original, `${originalContent}\n${unrelated}`);
const service = new MemorySelectiveService(store, client);
async function finish(job) {
  for (const deadline = Date.now() + 180000; Date.now() < deadline;) {
    const result = await service.advance(job.id);
    if (result.status === 'complete') return;
    await delay(500);
  }
  throw new Error('SELECTIVE_TIMEOUT');
}
const revised = selected.replace('青岚旧版小结', '岚峰新版小结');
await finish(await service.begin({ kind: 'correct', memoryUri: original,
  selectedText: selected, replacementText: revised }));
const corrected = await client.readMemory(original);
assert(corrected.includes(revised) && corrected.includes(unrelated) && !corrected.includes(selected));
assert(!JSON.stringify(await client.recall('每月报告总结标题', 10)).includes('青岚旧版小结'));
await finish(await service.begin({ kind: 'forget', memoryUri: original, selectedText: revised }));
const retained = await client.readMemory(original);
assert(retained.includes(unrelated) && !retained.includes(revised));
assert(!JSON.stringify(await client.recall('每月报告总结标题', 10)).includes('岚峰新版小结'));
const result = { correctedDocument: true, forgottenFactAbsent: true, unrelatedLinePreserved: true,
  noOldRecall: true, browserVerified: false };
await writeFile(`${root}/selective-result.json`, JSON.stringify(result), { mode: 0o600 });
console.log(JSON.stringify(result));
