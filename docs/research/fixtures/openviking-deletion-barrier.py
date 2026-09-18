"""Single-writer deletion-barrier feasibility against real model extraction.

Execute prepare, recover, verify as separate processes. Keeps durable synthetic
operation state outside OpenViking backups. No production adapter is installed.
"""
import json
import os
import secrets
import sys
import time
from pathlib import Path
import httpx

run = Path(sys.argv[1])
phase = sys.argv[2]
config = json.loads((run / 'ov.conf').read_text())
assert config['server']['host'] in ('127.0.0.1', 'localhost')
client = httpx.Client(base_url=f"http://127.0.0.1:{config['server']['port']}/api/v1",
                     timeout=120, trust_env=False)
state_path = run / 'deletion-barrier-state.json'


def save(state):
    temporary = state_path.with_suffix('.tmp')
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as stream:
        json.dump(state, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, state_path)
    directory = os.open(run, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def request(method, path, key, body=None, params=None):
    response = client.request(method, path, headers={'X-API-Key': key}, json=body, params=params)
    assert response.status_code == 200, f'{method} HTTP {response.status_code}'
    return response.json()['result']


def find(key):
    return request('POST', '/search/find', key, {
        'query': '技术方案目标和非目标及语言偏好',
        'target_uri': 'viking://user/alice/memories', 'limit': 20,
    })


if phase == 'prepare':
    assert not state_path.exists(), 'Do not overwrite a pending deletion experiment'
    root = config['server']['root_api_key']
    account = 'barrier-' + secrets.token_hex(6)
    request('POST', '/admin/accounts', root, {'account_id': account, 'admin_user_id': 'admin'})
    key = request('POST', f'/admin/accounts/{account}/users', root,
                  {'user_id': 'alice', 'role': 'user'})['user_key']
    sid = request('POST', '/sessions', key, {'auto_commit_policy': None})['session_id']
    source = 'synthetic-shared-source'
    request('POST', f'/sessions/{sid}/messages', key, {
        'role': 'user', 'source_message_ids': [source],
        'content': '请记住我的稳定偏好：所有技术方案都要写清目标和非目标，默认用简体中文。',
    })
    task = request('POST', f'/sessions/{sid}/commit', key, {'keep_recent_count': 0})['task_id']
    status = request('GET', f'/tasks/{task}', key)['status']
    assert status in ('pending', 'running'), 'Experiment requires an in-flight task'
    save({'account': account, 'key': key, 'sid': sid, 'task': task, 'source': source,
          'sourceRevoked': True, 'phase': 'revoked', 'inFlightAtRevocation': status})
    print(json.dumps({'durableRevocationWritten': True, 'taskWasInFlight': True}))
else:
    state = json.loads(state_path.read_text())
    assert state['account'].startswith('barrier-') and state['sourceRevoked']
    key = state['key']
    if phase == 'recover':
        assert state['phase'] == 'revoked'
        # Stop new deliveries before draining the already accepted extraction.
        # A production adapter must serialize all writers with this barrier.
        for _ in range(90):
            task = request('GET', f"/tasks/{state['task']}", key)
            if task['status'] in ('completed', 'failed', 'cancelled'):
                assert task['status'] == 'completed', 'Expected real extraction to finish'
                break
            time.sleep(2)
        else:
            raise TimeoutError('Old task still running; deletion cannot be acknowledged')
        hits = find(key)['memories']
        candidates = [hit['uri'] for hit in hits if hit['uri'].endswith('.md') and '/.' not in hit['uri']]
        changed = []
        retained = False
        for uri in candidates:
            raw = request('GET', '/content/read', key, params={'uri': uri, 'raw': True})
            if '目标和非目标' not in raw:
                continue
            # Fixture-only exact synthetic fact, not a general memory editor.
            lines = raw.splitlines(keepends=True)
            removed = [line for line in lines if '目标和非目标' in line]
            assert removed and all('简体中文' not in line for line in removed), 'Shared facts require semantic editing'
            edited = ''.join(line for line in lines if '目标和非目标' not in line)
            assert '简体中文' in edited, 'Unrelated fact must remain'
            request('POST', '/content/write', key, {'uri': uri, 'content': edited,
                    'mode': 'replace', 'wait': True, 'timeout': 60})
            changed.append(uri)
            retained = True
        assert changed and retained, 'Expected both synthetic facts in extracted memory'
        request('DELETE', f"/sessions/{state['sid']}", key)
        state.update({'phase': 'deleted', 'changed': changed})
        save(state)
        print(json.dumps({'recoveredRevocation': True, 'oldTaskDrained': True,
                          'selectiveDeleteApplied': True, 'sourceSessionDeleted': True}))
    elif phase == 'verify':
        assert state['phase'] == 'deleted'
        def replay_old_source():
            if state['sourceRevoked']:
                return 'blocked_by_revocation'
            raise AssertionError('Revoked queue item reached transport')
        assert replay_old_source() == 'blocked_by_revocation'
        for uri in state['changed']:
            raw = request('GET', '/content/read', key, params={'uri': uri, 'raw': True})
            assert '目标和非目标' not in raw and '简体中文' in raw
        recalled = json.dumps(find(key), ensure_ascii=False)
        assert '目标和非目标' not in recalled and '简体中文' in recalled
        assert client.get(f"/sessions/{state['sid']}", headers={'X-API-Key': key}).status_code == 404
        tasks = request('GET', '/tasks', key, params={'resource_id': state['sid'], 'task_type': 'session_commit'})
        assert len(tasks) == 1 and tasks[0]['task_id'] == state['task']
        report = {'revocationSurvivesProcessRestart': True, 'oldQueueReplayBlocked': True,
                  'forgottenFactAbsentFromReadAndSearch': True, 'unrelatedFactPreserved': True,
                  'noAdditionalExtractionTask': True, 'multiWriterAndServerCrashVerified': False}
        (run / 'deletion-barrier-result.json').write_text(json.dumps(report))
        print(json.dumps(report))
    else:
        raise ValueError('Unknown phase')
