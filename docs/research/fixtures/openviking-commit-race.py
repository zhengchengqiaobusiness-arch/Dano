"""Concurrent real commits for one synthetic source, with task/archive checks."""
import concurrent.futures
import json
import secrets
import sys
import threading
import time
from pathlib import Path
import httpx

run = Path(sys.argv[1])
config = json.loads((run / 'ov.conf').read_text())
assert config['server']['host'] in ('127.0.0.1', 'localhost')
base = f"http://127.0.0.1:{config['server']['port']}/api/v1"


def request(method, path, key, body=None, params=None):
    return httpx.request(method, base + path, headers={'X-API-Key': key}, json=body,
                         params=params, timeout=120, trust_env=False)


def ok(method, path, key, body=None, params=None):
    response = request(method, path, key, body, params)
    assert response.status_code == 200, f'{method} HTTP {response.status_code}'
    return response.json()['result']


root = config['server']['root_api_key']
account = 'commit-race-' + secrets.token_hex(6)
ok('POST', '/admin/accounts', root, {'account_id': account, 'admin_user_id': 'admin'})
key = ok('POST', f'/admin/accounts/{account}/users', root,
         {'user_id': 'alice', 'role': 'user'})['user_key']
sid = ok('POST', '/sessions', key, {'auto_commit_policy': None})['session_id']
source = secrets.token_hex(16)
ok('POST', f'/sessions/{sid}/messages', key, {'role': 'user', 'source_message_ids': [source],
   'content': '请记住：我的技术报告必须附带可复现的验证步骤。'})
barrier = threading.Barrier(6)
def commit(_):
    barrier.wait()
    response = request('POST', f'/sessions/{sid}/commit', key, {'keep_recent_count': 0})
    body = response.json()
    result = body.get('result') or {}
    return {'httpStatus': response.status_code, 'status': result.get('status'),
            'taskId': result.get('task_id'), 'reason': result.get('reason'),
            'errorCode': (body.get('error') or {}).get('code')}
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
    outcomes = list(pool.map(commit, range(6)))
report = {'concurrentCommitCount': 6, 'outcomes': outcomes}
(run / 'commit-race-result.json').write_text(json.dumps(report))
print(json.dumps(report), flush=True)
tasks = ok('GET', '/tasks', key, params={'resource_id': sid, 'task_type': 'session_commit'})
assert len(tasks) == 1, f'Expected one extraction task, got {len(tasks)}'
task_id = tasks[0]['task_id']
for _ in range(90):
    task = ok('GET', f'/tasks/{task_id}', key)
    if task['status'] in ('completed', 'failed', 'cancelled'):
        assert task['status'] == 'completed'
        break
    time.sleep(2)
else:
    raise TimeoutError('Concurrent commit did not finish')
archive_id = task['result']['archive_uri'].rstrip('/').split('/')[-1]
archive = ok('GET', f'/sessions/{sid}/archives/{archive_id}', key)
assert source in json.dumps(archive)
search = ok('POST', '/search/find', key, {'query': '技术报告可复现的验证步骤偏好',
            'target_uri': 'viking://user/alice/memories', 'limit': 10})
assert '验证步骤' in json.dumps(search, ensure_ascii=False)
report.update({'oneExtractionTask': True, 'taskCompleted': True,
               'sourceInCompletedArchive': True, 'syntheticFactRecalled': True,
               'interleavedNewMessageRaceVerified': False})
(run / 'commit-race-result.json').write_text(json.dumps(report))
print(json.dumps(report))
