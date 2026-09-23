/** Follow-up recall check for the synthetic fact-split run. */
import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
const [modulePath, configPath, root] = process.argv.slice(2);
assert(root?.startsWith('/private/tmp/dano476-fact-split-'));
const { OwnerMemoryClient, FileStateStore } = await import(pathToFileURL(modulePath));
const config = JSON.parse(await readFile(configPath, 'utf8'));
const { accountId, apiKey } = JSON.parse(await readFile(`${root}/credentials.json`, 'utf8'));
const owner = { accountId, userId: 'alice' };
const client = new OwnerMemoryClient({ owner, baseUrl: `http://${config.server.host}:${config.server.port}`,
  apiKey, timeoutMs: 90000 });
const state = await new FileStateStore({ owner, directory: `${root}/state`, policyVersion: 'fact-split' }).read();
assert.equal(Object.values(state.operations).filter(operation => operation.phase === 'ready').length, 1);
const documents = await Promise.all((await client.listMemoryDocuments()).map(uri => client.readMemory(uri)));
const oldAbsent = !documents.join('\n').includes('茉莉');
const unrelatedPresent = documents.join('\n').includes('星期二');
const oldRecallAbsent = !JSON.stringify(await client.recall('最喜欢的茶是什么', 10)).includes('茉莉');
const unrelatedRecallPresent = JSON.stringify(await client.recall('每周例会安排在哪一天', 10)).includes('星期二');
assert(oldAbsent && unrelatedPresent && oldRecallAbsent && unrelatedRecallPresent);
const result = { oldAbsent, unrelatedPresent, oldRecallAbsent, unrelatedRecallPresent };
await writeFile(`${root}/recall-result.json`, JSON.stringify(result), { mode: 0o600 });
console.log(JSON.stringify(result));
