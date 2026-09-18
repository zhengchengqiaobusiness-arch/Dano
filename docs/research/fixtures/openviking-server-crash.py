"""Prepare an in-flight commit, then observe it after an externally forced restart.

Only use against this task's isolated service. Never automatically resend a
message or commit. The supervisor, not this fixture, controls the server PID.
"""
import json
import os
import secrets
import sys
from pathlib import Path
import httpx

run = Path(sys.argv[1])
phase = sys.argv[2]
config = json.loads((run / 'ov.conf').read_text())
assert config['server']['host'] in ('127.0.0.1', 'localhost')
client = httpx.Client(base_url=f"http://127.0.0.1:{config['server']['port']}/api/v1",
                     timeout=120, trust_env=False)
state_path = run / 'server-crash-state.json'


def call(method, path, key, body=None, params=None):
    response = client.request(method, path, headers={'X-API-Key': key}, json=body, params=params)
    assert response.status_code == 200, f'{method} {path} HTTP {response.status_code}'
    return response.json()['result']


if phase == 'prepare':
    assert not state_path.exists(), 'Do not overwrite an unresolved crash probe'
    root = config['server']['root_api_key']
    account = 'crash-' + secrets.token_hex(6)
    call('POST', '/admin/accounts', root, {'account_id': account, 'admin_user_id': 'admin'})
    key = call('POST', f'/admin/accounts/{account}/users', root,
               {'user_id': 'alice', 'role': 'user'})['user_key']
    sid = call('POST', '/sessions', key, {'auto_commit_policy': None})['session_id']
    source = secrets.token_hex(12)
    call('POST', f'/sessions/{sid}/messages', key, {'role': 'user',
         'content': '请记住：每份技术报告都应说明验证环境和失败条件。', 'source_message_ids': [source]})
    with os.fdopen(os.open(state_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
        json.dump({'account': account, 'key': key, 'sid': sid, 'source': source}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    call('POST', f'/sessions/{sid}/commit', key, {'keep_recent_count': 0})
    tasks = call('GET', '/tasks', key, params={'resource_id': sid, 'task_type': 'session_commit'})
    assert len(tasks) == 1 and tasks[0]['status'] in ('pending', 'running')
    print(json.dumps({'inFlight': True, 'safeToInterruptSyntheticServer': True}), flush=True)
elif phase == 'observe':
    state = json.loads(state_path.read_text())
    assert state['account'].startswith('crash-')
    key = state['key']
    session = call('GET', f"/sessions/{state['sid']}", key)
    tasks = call('GET', '/tasks', key, params={'resource_id': state['sid'], 'task_type': 'session_commit'})
    context = call('GET', f"/sessions/{state['sid']}/context", key)
    archive_receipt = False
    for task in tasks:
        archive_uri = (task.get('result') or {}).get('archive_uri')
        if archive_uri and task['status'] == 'completed':
            archive_id = archive_uri.rstrip('/').split('/')[-1]
            archive = call('GET', f"/sessions/{state['sid']}/archives/{archive_id}", key)
            archive_receipt = archive_receipt or state['source'] in json.dumps(archive)
    search = call('POST', '/search/find', key, {'query': '技术报告的验证环境和失败条件',
                  'target_uri': 'viking://user/alice/memories', 'limit': 10})
    report = {'identityAndSessionSurvived': True, 'taskCount': len(tasks),
              'taskStatuses': [task['status'] for task in tasks],
              'activeMessageCount': session['message_count'],
              'sourceVisibleInContext': state['source'] in json.dumps(context),
              'sourceVisibleInCompletedArchive': archive_receipt,
              'syntheticFactRecalled': '失败条件' in json.dumps(search, ensure_ascii=False),
              'automaticallyRetriedByFixture': False}
    (run / 'server-crash-result.json').write_text(json.dumps(report))
    print(json.dumps(report))
else:
    raise ValueError('Unknown phase')
