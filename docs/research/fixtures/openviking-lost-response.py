"""Real-service lost-response reconciliation in a single-writer synthetic Session.

Run once with RUN_DIR prepare, then in a new process with RUN_DIR reconcile.
Discards successful mutation responses before the adapter observes their body.
Does not simulate server crashes, concurrent writers or expired task receipts.
"""
import json
import os
import sys
import time
import uuid
from pathlib import Path
import httpx

run = Path(sys.argv[1])
phase = sys.argv[2]
config = json.loads((run / 'ov.conf').read_text())
owner = json.loads((run / 'probe-state.json').read_text())
assert config['server']['host'] in ('127.0.0.1', 'localhost')
assert owner['account'].startswith('model-')
client = httpx.Client(base_url=f"http://127.0.0.1:{config['server']['port']}/api/v1",
                     headers={'X-API-Key': owner['key']}, timeout=120, trust_env=False)
receipt = run / 'lost-response-receipt.json'


def request(method, path, body=None, params=None, discard=False):
    response = client.request(method, path, json=body, params=params)
    assert response.status_code == 200, f'HTTP {response.status_code}'
    if discard:
        return None  # no task/message ID or response body reaches the adapter
    return response.json()['result']


def save(state):
    with receipt.open('w') as stream:
        json.dump(state, stream)
        stream.flush()
        os.fsync(stream.fileno())


if phase == 'prepare':
    sid = request('POST', '/sessions', {'auto_commit_policy': None})['session_id']
    state = {'sid': sid, 'source': 'loss-' + uuid.uuid4().hex, 'phase': 'message_unknown'}
    save(state)
    request('POST', f'/sessions/{sid}/messages', {
        'role': 'user', 'content': '请记住：我喜欢在技术文档末尾列出验收检查项。',
        'source_message_ids': [state['source']],
    }, discard=True)
    print(json.dumps({'responseDiscarded': True, 'restartRequired': True}))
elif phase == 'reconcile':
    state = json.loads(receipt.read_text())
    sid = state['sid']
    assert state['phase'] == 'message_unknown'
    context = request('GET', f'/sessions/{sid}/context')
    assert state['source'] in json.dumps(context), 'Message receipt not proven; do not retry'
    metadata = request('GET', f'/sessions/{sid}')
    assert metadata['message_count'] == 1
    state['phase'] = 'commit_unknown'
    save(state)
    request('POST', f'/sessions/{sid}/commit', {'keep_recent_count': 0}, discard=True)
    print(json.dumps({'messageReconciledAfterProcessRestart': True,
                      'messageCount': 1, 'commitResponseDiscarded': True}))
elif phase == 'finish':
    state = json.loads(receipt.read_text())
    assert state['phase'] == 'commit_unknown'
    tasks = request('GET', '/tasks', params={
        'task_type': 'session_commit', 'resource_id': state['sid'], 'limit': 200,
    })
    assert len(tasks) == 1, 'Ambiguous task receipt; do not repeat commit'
    task_id = tasks[0]['task_id']
    for _ in range(60):
        task = request('GET', f'/tasks/{task_id}')
        if task['status'] in ('completed', 'failed', 'cancelled'):
            assert task['status'] == 'completed', 'Task did not complete'
            break
        time.sleep(3)
    else:
        raise TimeoutError('Extraction not completed')
    found = request('POST', '/search/find', {
        'query': '技术文档末尾的验收检查项偏好',
        'target_uri': 'viking://user/alice/memories', 'limit': 10,
    })
    assert '验收检查项' in json.dumps(found, ensure_ascii=False)
    state['phase'] = 'ready'
    state['task_id'] = task_id
    save(state)
    print(json.dumps({'commitReconciledAfterProcessRestart': True,
                      'oneTask': True, 'extractedFactRecalled': True,
                      'concurrentWriterAndExpiredReceiptVerified': False}))
else:
    raise ValueError('Unknown phase')
