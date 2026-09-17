"""Probe an isolated real server with synthetic users; never print API keys.

Usage: python openviking-session-contract.py /path/to/isolated/ov.conf
Leaves the synthetic account on that isolated server for restart probes.
No embedding/LLM success is inferred from these session-only requests.
"""
import json
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

config = json.loads(Path(sys.argv[1]).read_text())
server = config['server']
assert server['host'] in ('127.0.0.1', 'localhost'), 'Use an isolated loopback server'
base = f"http://{server['host']}:{server['port']}"
root_key = server['root_api_key']
account = 'probe-' + uuid.uuid4().hex[:12]

def call(method, path, key=None, body=None, extra_headers=None):
    headers = {'X-API-Key': key} if key else {}
    headers.update(extra_headers or {})
    response = httpx.request(method, base + '/api/v1' + path,
                             headers=headers, json=body, timeout=30, trust_env=False)
    return response.status_code, response.json()

def result(method, path, key, body=None):
    status, payload = call(method, path, key, body)
    if status != 200:
        raise RuntimeError(f'{method} {path.split("/")[1]} failed: HTTP {status}')
    return payload['result']

result('POST', '/admin/accounts', root_key,
       {'account_id': account, 'admin_user_id': 'admin'})
keys = {}
for user in ('alice', 'bob'):
    keys[user] = result('POST', f'/admin/accounts/{account}/users', root_key,
                        {'user_id': user, 'role': 'user'})['user_key']

def create(user):
    return result('POST', '/sessions', keys[user], {'auto_commit_policy': None})

with ThreadPoolExecutor(max_workers=2) as pool:
    alice, bob = pool.map(create, ('alice', 'bob'))
sid = alice['session_id']
message = {'role': 'user', 'content': 'Synthetic preference: use concise answers.',
           'source_message_ids': ['synthetic-source-1']}
first = result('POST', f'/sessions/{sid}/messages', keys['alice'], message)
second = result('POST', f'/sessions/{sid}/messages', keys['alice'], message)
own = result('GET', f'/sessions/{sid}', keys['alice'])
foreign_status, _ = call('GET', f'/sessions/{sid}', keys['bob'])
forged_status, _ = call('GET', f'/sessions/{sid}', keys['bob'],
                        extra_headers={'X-OpenViking-Account': account,
                                       'X-OpenViking-User': 'alice'})
write_status, _ = call('POST', f'/sessions/{sid}/messages', keys['bob'], message)
unauth_status, _ = call('GET', f'/sessions/{sid}')
alice_after = result('GET', f'/sessions/{sid}', keys['alice'])
bob_same_id = result('GET', f'/sessions/{sid}', keys['bob']) if write_status == 200 else None
same_id_isolated = (bob_same_id is not None
                    and bob_same_id['uri'] != alice_after['uri']
                    and bob_same_id['created_by_user_id'] == 'bob'
                    and alice_after['created_by_user_id'] == 'alice'
                    and alice_after['message_count'] == own['message_count'])
context = result('GET', f'/sessions/{sid}/context', keys['alice'])
new_key = result('POST', f'/admin/accounts/{account}/users/alice/key', root_key)['user_key']
old_key_status, _ = call('GET', f'/sessions/{sid}', keys['alice'])
rotated = result('GET', f'/sessions/{sid}', new_key)
report = {
    'server_version': httpx.get(base + '/health', trust_env=False).json().get('version'),
    'concurrent_user_session_creation': True,
    'own_session_read': True,
    'foreign_session_status': foreign_status,
    'forged_identity_session_status': forged_status,
    'foreign_session_write_status': write_status,
    'unauthenticated_status': unauth_status,
    'same_id_write_creates_isolated_bob_session': same_id_isolated,
    'message_count_after_first_write': first['message_count'],
    'message_count_after_identical_source_replay': second['message_count'],
    'alice_unchanged_after_bob_write': alice_after['message_count'] == own['message_count'],
    'context_result_keys': sorted(context.keys()),
    'source_id_visible_in_context': 'synthetic-source-1' in json.dumps(context),
    'old_key_status_after_rotation': old_key_status,
    'rotated_key_same_owner': rotated['created_by_user_id'] == 'alice',
    'model_extraction_verified': False,
}
print(json.dumps(report, indent=2))
assert foreign_status in (403, 404)
assert forged_status in (403, 404)
assert write_status in (403, 404) or same_id_isolated
assert unauth_status in (401, 403)
assert old_key_status in (401, 403)
assert rotated['created_by_user_id'] == 'alice'
