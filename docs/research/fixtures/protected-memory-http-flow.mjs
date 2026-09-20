// Real model/OpenViking check through Dano HTTP/SSE. Synthetic JWT authentication
// is a protocol fixture and does not establish OAuth or browser acceptance.
import assert from 'node:assert/strict';
import http from 'node:http';
import { setTimeout as delay } from 'node:timers/promises';

export async function verifyMemoryHttpFlow({ origin, clients, token, memoryUnavailable = false }) {
  const [alice, bob] = clients;
  const events = [];
  let failure, request, nextId = 0;
  const wait = async (predicate, timeoutMs = 90000) => {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (failure) throw failure;
      const result = await predicate();
      if (result) return result;
      await delay(100);
    }
    throw new Error('DANO_REAL_MEMORY_FLOW_TIMEOUT');
  };
  const headers = entry => ({ authorization: `Bearer ${token(entry.id)}`, 'content-type': 'application/json' });
  const endpoint = `/api/clients/${alice.client.id}/memory/operations`;
  const read = async path => {
    const response = await fetch(origin + path, { headers: headers(alice) });
    assert.equal(response.status, 200);
    return response.json();
  };
  try {
    await new Promise((resolve, reject) => {
      request = http.get(origin + alice.eventsUrl, { headers: headers(alice) }, response => {
        if (response.statusCode !== 200) { reject(new Error('SSE_CONNECTION_FAILED')); return; }
        response.setEncoding('utf8'); let buffer = '';
        response.on('data', chunk => {
          buffer += chunk;
          for (let boundary; (boundary = buffer.indexOf('\n\n')) !== -1;) {
            const frame = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2);
            const data = frame.split(/\r?\n/).filter(line => line.startsWith('data: ')).map(line => line.slice(6)).join('\n');
            if (data) { try { events.push(JSON.parse(data)); } catch { failure = new Error('INVALID_SSE_JSON'); } }
          }
        });
        response.on('error', error => { failure = error; }); resolve();
      });
      request.on('error', reject);
    });
    const command = async (type, data = {}) => {
      const id = `memory-check-${++nextId}`;
      const response = await fetch(origin + alice.messagesUrl, { method: 'POST', headers: headers(alice),
        body: JSON.stringify({ type: 'command', payload: { id, type, ...data } }) });
      assert.equal(response.status, 202);
      const completed = await wait(() => events.find(event => event.type === 'response' && event.payload.id === id));
      assert.equal(completed.payload.success, true, `${type} command failed`);
      return completed.payload;
    };
    const turn = async message => {
      const from = events.length;
      await command('prompt', { message });
      await wait(() => events.slice(from).find(event => event.type === 'event' && event.payload.type === 'agent_end'));
      return events.slice(from).filter(event => event.type === 'event').map(event => event.payload);
    };
    await command('set_model', { provider: 'cestc', modelId: 'qwen35' });
    if (memoryUnavailable) {
      const completed = await turn('请只回复：普通聊天正常');
      const messages = completed.filter(event => event.type === 'agent_end').flatMap(event => event.messages ?? []);
      const text = messages.filter(message => message.role === 'assistant').flatMap(message => message.content ?? [])
        .filter(block => block.type === 'text').map(block => block.text).join('\n');
      assert(text.includes('普通聊天正常'), 'Ordinary model chat failed with unavailable memory');
      console.log(JSON.stringify({ danoHttpSse: true, realModel: true, memoryUnavailable: true,
        ordinaryChatVerified: true, browserVerified: false, oauthVerified: false }));
      return;
    }
    await turn('请记住我的稳定偏好：验收报告以“竹影验收”作为标题，正文使用简体中文。请调用 memory_save 保存。');
    const receipt = await wait(async () => (await read(endpoint)).items.find(item => item.phase === 'ready'), 180000);
    assert(receipt.source?.sessionId && receipt.source?.entryId && receipt.createdAt);
    const contentPath = `${endpoint}/${receipt.id}/content/0`;
    const content = await read(contentPath);
    assert(content.text.includes('竹影验收'));
    assert.equal((await fetch(origin + contentPath, { headers: headers(bob) })).status, 403);
    await command('new_session');
    await command('set_model', { provider: 'cestc', modelId: 'qwen35' });
    const recalled = await turn('我的验收报告标题和语言有什么稳定偏好？');
    const messages = recalled.filter(event => event.type === 'agent_end').flatMap(event => event.messages ?? []);
    const text = messages.filter(message => message.role === 'assistant').flatMap(message => message.content ?? [])
      .filter(block => block.type === 'text').map(block => block.text).join('\n');
    assert(text.includes('竹影验收') && text.includes('简体中文'), 'New Dano session did not recall synthetic facts');
    console.log(JSON.stringify({ danoHttpSse: true, realService: true, savedReady: true,
      sourceAndContent: true, foreignReadDenied: true, newSessionRecall: true, browserVerified: false, oauthVerified: false }));
  } finally { request?.destroy(); }
}
