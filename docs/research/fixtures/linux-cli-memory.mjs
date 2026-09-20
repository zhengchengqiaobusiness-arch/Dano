// Full standard-entry acceptance via ordinary pi's public RPC CLI mode.
import assert from 'node:assert/strict';
import { mkdir, chown, chmod, writeFile, readFile, readdir } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { createInterface } from 'node:readline';
const root = '/tmp/pi-openviking-memory-acceptance';
const hostUid = 1000, workerUid = 65534, groupId = 1000;
await mkdir(root, { mode: 0o711 });
for (const name of ['agent', 'state', 'workspace']) {
  const path = `${root}/${name}`;
  await mkdir(path, { mode: name === 'workspace' ? 0o770 : 0o700 });
  await chown(path, name === 'workspace' ? workerUid : hostUid, groupId);
  await chmod(path, name === 'workspace' ? 0o770 : 0o700);
}
for (const [name, source] of [['models.json', process.argv[2]], ['memory-connection.json', process.argv[3]]]) {
  await writeFile(`${root}/agent/${name}`, await readFile(source), { mode: 0o600 });
  await chown(`${root}/agent/${name}`, hostUid, groupId);
}
await writeFile(`${root}/agent/settings.json`, JSON.stringify({ retry: { enabled: false }, compaction: { enabled: false } }), { mode: 0o600 });
await chown(`${root}/agent/settings.json`, hostUid, groupId);
const profile = { workspace: `${root}/workspace`, agentDir: `${root}/agent`, stateDir: `${root}/state`,
  installationDir: '/app', piPackageContext: fileURLToPath(new URL('../package.json', import.meta.url)), privilegeGuard: '/usr/bin/setpriv',
  hostUid, hostGid: groupId, workerUid, workerGid: groupId, path: '/usr/local/bin:/usr/bin:/bin',
  startupTimeoutMs: 15000, operationTimeoutMs: 10000, maxConcurrentOperations: 4, maxResultBytes: 1048576,
  hostModule: fileURLToPath(new URL('./cli-memory-host.mjs', import.meta.url)), shutdownTimeoutMs: 20000 };
const profileFile = '/etc/pi-openviking-memory-acceptance.json';
await writeFile(profileFile, JSON.stringify(profile), { mode: 0o600 });
const env = { ...process.env };
for (const key of ['HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy']) delete env[key];
const child = spawn(process.execPath, [fileURLToPath(new URL('./cli.js', import.meta.resolve('@josephyoung/pi-openviking/launcher'))), profileFile,
  '--offline', '--mode', 'rpc', '--provider', 'cestc', '--model', 'qwen35', '--thinking', 'off'],
  { env, stdio: ['pipe', 'pipe', 'pipe'] });
const events = []; let errors = '', exitCode, nextId = 0, confirmed = false;
child.on('exit', code => { exitCode = code; });
child.stderr.on('data', data => { errors += data; });
const lines = createInterface({ input: child.stdout });
lines.on('line', line => {
  let event; try { event = JSON.parse(line); } catch { return; }
  events.push(event);
  if (event.type === 'extension_ui_request' && event.method === 'confirm') {
    assert.equal(event.title, '启用长期记忆');
    confirmed = true;
    child.stdin.write(JSON.stringify({ type: 'extension_ui_response', id: event.id, confirmed: true })+'\n');
  }
});
const wait = async (predicate, timeoutMs = 90000) => {
  const until = Date.now()+timeoutMs;
  while (Date.now()<until) {
    const result = await predicate(); if (result) return result;
    assert.equal(exitCode, undefined, `CLI ended early; stderr bytes ${errors.length}`);
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  throw new Error('CLI_ACCEPTANCE_TIMEOUT');
};
async function command(type, data = {}) {
  const id = `acceptance-${++nextId}`;
  child.stdin.write(JSON.stringify({ id, type, ...data })+'\n');
  const response = await wait(()=>events.find(e=>e.type==='response' && e.id===id));
  assert(response.success, `RPC ${type} failed`); return response;
}
async function model(message) {
  const from = events.length;
  await command('prompt', { message });
  await wait(()=>events.slice(from).some(e=>e.type==='agent_end'));
  return events.slice(from);
}
async function state() {
  const files = (await readdir(`${root}/state`)).filter(name=>name.endsWith('.json'));
  if (!files.length) return undefined;
  return JSON.parse(await readFile(`${root}/state/${files[0]}`,'utf8'));
}
try {
  await command('get_state');
  await command('prompt', { message: '/memory status' });
  await wait(()=>events.some(e=>e.type==='extension_ui_request' && e.method==='notify' && /未授权/.test(e.message)));
  const initial = await state();
  assert(!initial?.authorization.enabled && !initial?.authorization.automaticCollection);
  await command('prompt', { message: '/memory enable' });
  await wait(async()=> (await state())?.authorization.enabled);
  assert(confirmed);
  assert.equal((await state()).authorization.automaticCollection, false);
  console.log(JSON.stringify({ stage: 'explicit-consent', defaultOff: true, separateCollectionConsent: true }));
  const saved = await model('请记住我的稳定偏好：所有验收报告都以“松风验收”作为标题，正文使用简体中文。请调用 memory_save 保存。');
  assert(saved.some(e=>e.type==='tool_execution_end' && e.toolName==='memory_save' && e.result?.details?.status==='queued'));
  const operation = await wait(async()=>Object.values((await state())?.operations ?? {}).find(o=>o.phase==='ready'),180000);
  assert(operation.memoryUris.length>0);
  console.log(JSON.stringify({ stage: 'saved-ready', operationId: operation.id }));
  const showFrom = events.length;
  await command('prompt', { message: `/memory show ${operation.id}` });
  await wait(()=>events.slice(showFrom).some(e=>e.type==='extension_ui_request' && e.method==='notify' && /松风验收/.test(e.message) && /来源会话/.test(e.message)));
  await command('new_session');
  const recalled = await model('我的验收报告标题和正文语言有什么稳定偏好？');
  const assistant = recalled.filter(e=>e.type==='message_end' && e.message?.role==='assistant')
    .flatMap(e=>e.message.content).filter(c=>c.type==='text').map(c=>c.text).join('\n');
  assert(assistant.includes('松风验收') && assistant.includes('简体中文'), 'New session did not recall saved preference');
  await command('prompt', { message: '/memory pause' });
  await wait(async()=>!(await state()).authorization.enabled);
  const connection = JSON.parse(await readFile(process.argv[3],'utf8'));
  assert(!JSON.stringify(events).includes(connection.apiKey) && !errors.includes(connection.apiKey));
  const evidence = { standardCliRpc: true, realService: true, explicitConsent: true, ready: true,
    viewContentAndSource: true, newSessionRecall: true, pause: true, automaticCollectionUnapproved: true,
    credentialAbsentFromEvents: true, operationId: operation.id, tokenizerRevision: '60d8d70770c6776ff598c94bb586a859a38244f1' };
  await writeFile('/evidence/result.json',JSON.stringify(evidence,null,2),{mode:0o600});
  console.log(JSON.stringify(evidence));
} finally {
  child.stdin.end();
  const timer = setTimeout(()=>child.kill('SIGKILL'),10000);
  if (exitCode===undefined) await new Promise(resolve=>child.once('exit',resolve));
  clearTimeout(timer);
}
