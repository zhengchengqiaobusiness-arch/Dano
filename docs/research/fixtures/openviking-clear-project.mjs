/** Follow-up to the isolated clear-coordinator run; only synthetic accounts. */
import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
const [modulePath, configPath, root] = process.argv.slice(2);
assert(root?.startsWith('/private/tmp/dano476-clear-coordinator-'));
const initial = JSON.parse(await readFile(`${root}/result.json`, 'utf8'));
assert(initial.explicitResaveAllowed && initial.otherUserPreserved && initial.projectPreserved);
const { OwnerMemoryClient, FileStateStore, MemoryGovernanceBarrier, MemoryClearCoordinator } = await import(pathToFileURL(modulePath));
const credentials = JSON.parse(await readFile(`${root}/credentials.json`, 'utf8'));
assert(/^clear-[a-f0-9-]+$/.test(credentials.accountId));
const config = JSON.parse(await readFile(configPath, 'utf8'));
assert(['localhost', '127.0.0.1'].includes(config.server.host));
const baseUrl = `http://${config.server.host}:${config.server.port}`;
function client(userId, scope = null) {
  return new OwnerMemoryClient({ owner: { accountId: credentials.accountId, userId }, scope,
    baseUrl, apiKey: credentials.keys[userId], timeoutMs: 90000 });
}
const project = client('alice', 'project-a');
const store = new FileStateStore({ owner: project.owner, directory: `${root}/alice`, policyVersion: 'clear-probe' });
const existingJobs = Object.values((await store.read()).governance?.jobs ?? {});
const pending = existingJobs.filter(job => job.kind === 'clear' && job.scope === 'project-a' && job.phase !== 'complete');
assert(pending.length <= 1);
const job = pending[0] ?? await new MemoryGovernanceBarrier(store).begin({ kind: 'clear', scope: 'project-a' });
const coordinator = new MemoryClearCoordinator(store, project);
let complete = false;
for (const deadline = Date.now() + 120000; Date.now() < deadline;) {
  if ((await coordinator.advance(job.id)).status === 'complete') { complete = true; break; }
  await delay(500);
}
assert(complete);
assert(!JSON.stringify(await project.recall('每月报告总结标题', 10)).includes('杉溪项目小结'));
assert(JSON.stringify(await client('alice').recall('每月报告总结标题', 10)).includes('青岚旧版小结'));
assert(JSON.stringify(await client('bob').recall('每月报告总结标题', 10)).includes('榆湾用户小结'));
const result = { projectClearComplete: true, globalNewVersionPreserved: true, otherUserPreserved: true, browserVerified: false };
await writeFile(`${root}/project-clear-result.json`, JSON.stringify(result), { mode: 0o600 });
console.log(JSON.stringify(result));
