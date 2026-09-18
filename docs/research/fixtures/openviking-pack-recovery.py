"""Public export/backup/restore probe against an existing synthetic account.

Uses the real service, rotates its synthetic Alice key, deletes one memory,
restores the account backup, then reapplies the explicit deletion record.
This is a quiescent same-service recovery probe, not full disaster recovery.
"""
import io
import json
import os
import sys
import uuid
import zipfile
from pathlib import Path
import httpx

run = Path(sys.argv[1])
config = json.loads((run / 'ov.conf').read_text())
state_path = run / 'probe-state.json'
state = json.loads(state_path.read_text())
assert state['account'].startswith('model-')
assert config['server']['host'] in ('127.0.0.1', 'localhost')
client = httpx.Client(base_url=f"http://127.0.0.1:{config['server']['port']}/api/v1",
                     timeout=180, trust_env=False)


def call(method, path, key, body=None, params=None):
    response = client.request(method, path, headers={'X-API-Key': key}, json=body, params=params)
    if response.status_code != 200:
        error = response.json().get('error', {})
        message = str(error.get('message', ''))
        for secret in (key, config['server']['root_api_key']):
            message = message.replace(secret, '[redacted]')
        raise AssertionError(f'{method} {path}: HTTP {response.status_code}: {message[:500]}')
    return response


def result(method, path, key, body=None, params=None):
    return call(method, path, key, body, params).json()['result']


def inventory(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        assert archive.testzip() is None
        return names


root_key = config['server']['root_api_key']
account = state['account']
admin_key = result('POST', f'/admin/accounts/{account}/users/admin/key', root_key)['user_key']
alice = state['key']
found = result('POST', '/search/find', alice, {
    'query': '技术方案的语言和格式偏好', 'target_uri': 'viking://user/alice/memories', 'limit': 10,
})
uri = next(m['uri'] for m in found['memories'] if m['uri'].endswith('.md') and '/.' not in m['uri'])
original = result('GET', '/content/read', alice, params={'uri': uri, 'raw': True})
export = call('POST', '/pack/export', alice, {'uri': 'viking://user/alice/memories'}).content
export_names = inventory(export)
assert any(name.endswith('.md') for name in export_names)
assert not any('/bob/' in name for name in export_names)
bob_id = 'export-peer-' + uuid.uuid4().hex[:8]
bob = result('POST', f'/admin/accounts/{account}/users', root_key,
             {'user_id': bob_id, 'role': 'user'})['user_key']
denied = client.post('/pack/export', headers={'X-API-Key': bob},
                     json={'uri': 'viking://user/alice/memories'})
assert denied.status_code == 403
vector_backup = client.post('/pack/backup', headers={'X-API-Key': admin_key}, json={'include_vectors': True})
vector_snapshot_available = vector_backup.status_code == 200
if not vector_snapshot_available:
    assert vector_backup.status_code == 400
    assert 'incomplete OpenViking vector index snapshot' in vector_backup.text
backup = (vector_backup.content if vector_snapshot_available else
          call('POST', '/pack/backup', admin_key, {'include_vectors': False}).content)
backup_names = inventory(backup)
(run / 'synthetic-account-backup.ovpack').write_bytes(backup)
(run / 'synthetic-user-export.ovpack').write_bytes(export)

# Keep the deletion record outside the backup so recovery cannot roll it back.
record_path = run / 'pack-deletion-record.json'
with record_path.open('w') as stream:
    json.dump({'account': account, 'uri': uri, 'revoked': True}, stream)
    stream.flush()
    os.fsync(stream.fileno())
result('DELETE', '/fs', alice, params={'uri': uri, 'recursive': False, 'wait': True})
assert client.get('/content/read', headers={'X-API-Key': alice}, params={'uri': uri}).status_code == 404
new_key = result('POST', f'/admin/accounts/{account}/users/alice/key', root_key)['user_key']
state['key'] = new_key
with state_path.open('w') as stream:
    json.dump(state, stream)
    stream.flush()
    os.fsync(stream.fileno())
state_path.chmod(0o600)

upload = client.post('/resources/temp_upload', headers={'X-API-Key': admin_key},
                     files={'file': ('backup.ovpack', backup, 'application/zip')})
assert upload.status_code == 200, f'Upload HTTP {upload.status_code}'
upload_id = upload.json()['result']['temp_file_id']
result('POST', '/pack/restore', admin_key, {
    'temp_file_id': upload_id, 'on_conflict': 'overwrite',
    'vector_mode': 'require' if vector_snapshot_available else 'recompute',
})
restored = result('GET', '/content/read', new_key, params={'uri': uri, 'raw': True})
assert restored == original
assert client.get('/content/read', headers={'X-API-Key': alice}, params={'uri': uri}).status_code == 401
record = json.loads(record_path.read_text())
assert record['account'] == account and record['revoked']
result('DELETE', '/fs', new_key, params={'uri': record['uri'], 'recursive': False, 'wait': True})
assert client.get('/content/read', headers={'X-API-Key': new_key}, params={'uri': uri}).status_code == 404
after = result('POST', '/search/find', new_key, {
    'query': '技术方案的语言和格式偏好', 'target_uri': 'viking://user/alice/memories', 'limit': 20,
})
assert all(hit['uri'] != uri for hit in after['memories'])
report = {
    'userExportZipValid': True, 'crossUserExportDenied': True,
    'backupZipValid': True, 'exportEntryCount': len(export_names),
    'vectorSnapshotAvailable': vector_snapshot_available,
    'backupEntryCount': len(backup_names), 'deletedFileRestoredFromOldBackup': True,
    'rotatedOldKeyStillRejectedAfterRestore': True,
    'newKeyReadsRestoredOwnerData': True, 'deletionRecordReappliedToReadAndSearch': True,
    'cleanServerIdentityRecoveryVerified': False, 'inFlightBackupConsistencyVerified': False,
}
(run / 'pack-recovery-result.json').write_text(json.dumps(report))
print(json.dumps(report))
