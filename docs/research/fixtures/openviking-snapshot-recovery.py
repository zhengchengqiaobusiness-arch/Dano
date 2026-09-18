"""Verify stopped-directory snapshot recovery and post-snapshot revocations.

Run after copying the stopped isolated service and starting a separate copy.
Both arguments must be synthetic run directories. Rotates only probe keys.
"""
import json
import sys
from pathlib import Path
import httpx
from probe_state import write_private_json

source, snapshot = map(Path, sys.argv[1:3])
evidence = json.loads((snapshot / 'snapshot-evidence.json').read_text())
assert evidence['sourceStoppedBeforeCopy'] and evidence['dataFilesHashMatched'] > 0
source_config = json.loads((source / 'ov.conf').read_text())
destination_config = json.loads((snapshot / 'ov.conf').read_text())
assert source_config['server']['port'] != destination_config['server']['port']
for config in (source_config, destination_config):
    assert config['server']['host'] in ('127.0.0.1', 'localhost')
state = json.loads((snapshot / 'server-crash-state.json').read_text())
assert state['account'].startswith('crash-')
key = state['key']


def call(config, method, path, credential, body=None, params=None):
    base = f"http://127.0.0.1:{config['server']['port']}/api/v1"
    return httpx.request(method, base + path, headers={'X-API-Key': credential},
                         json=body, params=params, timeout=120, trust_env=False)


def ok(config, method, path, credential, body=None, params=None):
    response = call(config, method, path, credential, body, params)
    assert response.status_code == 200, f'{method} {path}: HTTP {response.status_code}'
    return response.json()['result']


for config in (source_config, destination_config):
    session = ok(config, 'GET', f"/sessions/{state['sid']}", key)
    tasks = ok(config, 'GET', '/tasks', key, params={'resource_id': state['sid'], 'task_type': 'session_commit'})
    assert len(tasks) == 1 and tasks[0]['status'] == 'completed'
    archive_id = tasks[0]['result']['archive_uri'].rstrip('/').split('/')[-1]
    archive = ok(config, 'GET', f"/sessions/{state['sid']}/archives/{archive_id}", key)
    assert state['source'] in json.dumps(archive)
search_body = {'query': '技术报告的验证环境和失败条件',
               'target_uri': 'viking://user/alice/memories', 'limit': 10}
found = ok(destination_config, 'POST', '/search/find', key, search_body)
uri = next(hit['uri'] for hit in found['memories'] if hit['uri'].endswith('.md') and '失败条件' in json.dumps(hit, ensure_ascii=False))
# Simulate revocation/deletion after the backup, recorded outside its snapshot.
record = {'account': state['account'], 'user': 'alice', 'uri': uri, 'revokeKey': True}
record_path = source / 'post-snapshot-revocation.json'
record_path.write_text(json.dumps(record))
ok(source_config, 'DELETE', '/fs', key, params={'uri': uri, 'recursive': False, 'wait': True})
new_source_key = ok(source_config, 'POST', f"/admin/accounts/{state['account']}/users/alice/key",
                    source_config['server']['root_api_key'])['user_key']
source_state = {**state, 'key': new_source_key}
source_state_path = source / 'server-crash-state.json'
write_private_json(source_state_path, source_state)
assert call(source_config, 'GET', '/content/read', key, params={'uri': uri}).status_code == 401
assert call(destination_config, 'GET', '/content/read', key, params={'uri': uri}).status_code == 200
# Reapply external records before releasing recovered service to callers.
record = json.loads(record_path.read_text())
new_destination_key = ok(destination_config, 'POST',
    f"/admin/accounts/{record['account']}/users/{record['user']}/key",
    destination_config['server']['root_api_key'])['user_key']
ok(destination_config, 'DELETE', '/fs', new_destination_key,
   params={'uri': record['uri'], 'recursive': False, 'wait': True})
assert call(destination_config, 'GET', '/content/read', key, params={'uri': uri}).status_code == 401
assert call(destination_config, 'GET', '/content/read', new_destination_key, params={'uri': uri}).status_code == 404
after = ok(destination_config, 'POST', '/search/find', new_destination_key, search_body)
assert '失败条件' not in json.dumps(after, ensure_ascii=False)
report = {'originalUserKeyRestoredByFullSnapshot': True, 'taskAndArchiveRestored': True,
          'postSnapshotRevocationRequired': True, 'revocationAndDeletionReapplied': True,
          'oldKeyRejectedAndDeletedFactAbsentAfterReplay': True,
          'liveConcurrentSnapshotVerified': False, 'pendingQueueSnapshotVerified': False}
(snapshot / 'full-snapshot-result.json').write_text(json.dumps(report))
print(json.dumps(report))
