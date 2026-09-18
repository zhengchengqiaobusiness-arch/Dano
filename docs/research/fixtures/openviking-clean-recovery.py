"""Restore a real public backup into a separately started, empty service.

Arguments: source synthetic run directory, clean recovery run directory.
Recreates identity via admin APIs and replays the independent deletion record.
"""
import json
import sys
from pathlib import Path
import httpx

source, destination = map(Path, sys.argv[1:3])
config = json.loads((destination / 'ov.conf').read_text())
state = json.loads((source / 'probe-state.json').read_text())
deletion = json.loads((source / 'pack-deletion-record.json').read_text())
assert config['server']['host'] in ('127.0.0.1', 'localhost')
assert state['account'].startswith('model-') and deletion['account'] == state['account']
client = httpx.Client(base_url=f"http://127.0.0.1:{config['server']['port']}/api/v1",
                     timeout=180, trust_env=False)


def call(method, path, key, body=None, params=None):
    return client.request(method, path, headers={'X-API-Key': key}, json=body, params=params)


def ok(method, path, key, body=None, params=None):
    response = call(method, path, key, body, params)
    assert response.status_code == 200, f'{method} {path}: HTTP {response.status_code}'
    return response.json()['result']


root = config['server']['root_api_key']
old_key = state['key']
assert call('GET', '/content/read', old_key, params={'uri': deletion['uri']}).status_code == 401
created = call('POST', '/admin/accounts', root,
               {'account_id': state['account'], 'admin_user_id': 'admin'})
assert created.status_code in (200, 409)
if created.status_code == 200:
    admin = created.json()['result']['user_key']
else:
    admin = ok('POST', f"/admin/accounts/{state['account']}/users/admin/key", root)['user_key']
created_user = call('POST', f"/admin/accounts/{state['account']}/users", root,
                    {'user_id': 'alice', 'role': 'user'})
assert created_user.status_code in (200, 409)
alice = (created_user.json()['result']['user_key'] if created_user.status_code == 200 else
         ok('POST', f"/admin/accounts/{state['account']}/users/alice/key", root)['user_key'])
assert call('GET', '/content/read', alice, params={'uri': deletion['uri']}).status_code == 404
backup = (source / 'synthetic-account-backup.ovpack').read_bytes()
upload = client.post('/resources/temp_upload', headers={'X-API-Key': admin},
                     files={'file': ('backup.ovpack', backup, 'application/zip')})
assert upload.status_code == 200
ok('POST', '/pack/restore', admin, {'temp_file_id': upload.json()['result']['temp_file_id'],
   'on_conflict': 'overwrite', 'vector_mode': 'recompute'})
restored = ok('GET', '/content/read', alice, params={'uri': deletion['uri'], 'raw': True})
assert restored, 'Backup did not restore content'
assert call('GET', '/content/read', old_key, params={'uri': deletion['uri']}).status_code == 401
search_body = {'query': '技术文档的验收检查项偏好',
               'target_uri': 'viking://user/alice/memories', 'limit': 20}
before_delete = ok('POST', '/search/find', alice, search_body)
assert '验收检查项' in json.dumps(before_delete, ensure_ascii=False), 'Restored fact not searchable'
# Do not make the recovered account available until independent tombstones apply.
ok('DELETE', '/fs', alice, params={'uri': deletion['uri'], 'recursive': False, 'wait': True})
assert call('GET', '/content/read', alice, params={'uri': deletion['uri']}).status_code == 404
search = ok('POST', '/search/find', alice, search_body)
assert all(item['uri'] != deletion['uri'] for item in search['memories'])
assert '验收检查项' not in json.dumps(search, ensure_ascii=False), 'Deleted file fact remains searchable'
report = {'targetMissingBeforeRestore': True, 'identityRecreatedThroughPublicApi': True,
          'oldServiceUserKeyRejectedBeforeAndAfterRestore': True,
          'restoredContentAccessibleWithNewKey': True, 'deletionRecordReapplied': True,
          'deletedUriAbsentFromReadAndSearch': True, 'restoredFactSearchableBeforeDeletion': True,
          'deletedFactAbsentAfterReplay': True,
          'fullIdentitySnapshotAndQueueRecoveryVerified': False}
(destination / 'clean-recovery-result.json').write_text(json.dumps(report))
print(json.dumps(report))
