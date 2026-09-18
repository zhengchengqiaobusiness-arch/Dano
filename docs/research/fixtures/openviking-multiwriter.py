"""POSIX single-host delivery-lock prototype with real-service reconciliation.

Crashes one worker after append and races six independent retry processes.
Uses only a fresh synthetic Session; does not claim multi-host coordination.
"""
import concurrent.futures
import fcntl
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path
import httpx

run = Path(sys.argv[1])
worker = len(sys.argv) > 2
config = json.loads((run / 'ov.conf').read_text())
assert config['server']['host'] in ('127.0.0.1', 'localhost')
base = f"http://127.0.0.1:{config['server']['port']}/api/v1"
state_path = run / 'multiwriter-state.json'
lock_path = run / 'multiwriter.lock'


def call(method, path, key, body=None, params=None):
    response = httpx.request(method, base + path, headers={'X-API-Key': key},
                             json=body, params=params, timeout=120, trust_env=False)
    assert response.status_code == 200, f'{method} HTTP {response.status_code}'
    return response.json()['result']


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


if worker:
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(state_path.read_text())
        assert state['account'].startswith('writers-')
        if state['revoked']:
            print('blocked_by_revocation')
            sys.exit(0)
        if state['delivered']:
            print('already_delivered')
            sys.exit(0)
        key, sid = state['key'], state['sid']
        context = call('GET', f'/sessions/{sid}/context', key)
        if state['source'] not in json.dumps(context):
            # Fresh dedicated Session, auto-commit disabled, one bounded message.
            # This absence test must not be generalized to truncated contexts.
            metadata = call('GET', f'/sessions/{sid}', key)
            assert metadata['message_count'] == 0, 'Unknown contents; refuse blind retry'
            call('POST', f'/sessions/{sid}/messages', key, {'role': 'user',
                 'content': '合成并发验证消息', 'source_message_ids': [state['source']]})
        if sys.argv[2] == '--crash-after-append':
            os._exit(77)  # release the OS lock without writing a delivery receipt
        state['delivered'] = True
        save(state)
        print('reconciled')
else:
    assert not state_path.exists(), 'Do not overwrite existing probe state'
    root = config['server']['root_api_key']
    account = 'writers-' + secrets.token_hex(6)
    call('POST', '/admin/accounts', root, {'account_id': account, 'admin_user_id': 'admin'})
    key = call('POST', f'/admin/accounts/{account}/users', root,
               {'user_id': 'alice', 'role': 'user'})['user_key']
    sid = call('POST', '/sessions', key, {'auto_commit_policy': None})['session_id']
    save({'account': account, 'key': key, 'sid': sid, 'source': secrets.token_hex(12),
          'delivered': False, 'revoked': False})
    def launch(flag):
        return subprocess.run([sys.executable, __file__, str(run), flag], capture_output=True, text=True)
    failed = launch('--crash-after-append')
    assert failed.returncode == 77
    assert not json.loads(state_path.read_text())['delivered']
    assert call('GET', f'/sessions/{sid}', key)['message_count'] == 1
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(launch, ['--retry'] * 6))
    assert all(result.returncode == 0 for result in results), 'Retry worker failed'
    assert sum(result.stdout.strip() == 'reconciled' for result in results) == 1
    assert call('GET', f'/sessions/{sid}', key)['message_count'] == 1
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(state_path.read_text())
        state['revoked'] = True
        save(state)
        call('DELETE', f'/sessions/{sid}', key)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        blocked = list(pool.map(launch, ['--retry'] * 6))
    assert all(result.returncode == 0 and result.stdout.strip() == 'blocked_by_revocation' for result in blocked)
    assert httpx.get(base + f'/sessions/{sid}', headers={'X-API-Key': key}, trust_env=False).status_code == 404
    report = {'workerCrashAfterAcceptedAppend': True, 'osLockReleasedAfterCrash': True,
              'independentRetryProcesses': 6, 'exactlyOneMessageAfterRetries': True,
              'concurrentRetriesBlockedAfterRevocation': True, 'deletedSessionNotRecreated': True,
              'multiHostAndLongContextVerified': False, 'commitConcurrencyVerified': False}
    (run / 'multiwriter-result.json').write_text(json.dumps(report))
    print(json.dumps(report))
