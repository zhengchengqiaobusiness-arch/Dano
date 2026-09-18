"""Real USER/account/actor-Peer boundary matrix using synthetic public content.

Run with isolated server run-directory. Reports observed statuses rather than
assuming that a caller-selected actor header is an authentication boundary.
"""
import concurrent.futures
import json
import secrets
import sys
from pathlib import Path
import httpx

run = Path(sys.argv[1])
config = json.loads((run / 'ov.conf').read_text())
assert config['server']['host'] in ('127.0.0.1', 'localhost')
base = f"http://127.0.0.1:{config['server']['port']}/api/v1"
root = config['server']['root_api_key']


def call(method, path, key, body=None, params=None, actor=None, extra=None):
    headers = {'X-API-Key': key}
    if actor is not None:
        headers['X-OpenViking-Actor-Peer'] = actor
    headers.update(extra or {})
    return httpx.request(method, base + path, headers=headers, json=body,
                         params=params, timeout=180, trust_env=False)


def ok(method, path, key, body=None, params=None, actor=None):
    response = call(method, path, key, body, params, actor)
    assert response.status_code == 200, f'{method} {path} HTTP {response.status_code}'
    return response.json()['result']


account = 'peer-' + secrets.token_hex(6)
other_account = 'peer-' + secrets.token_hex(6)
for aid in (account, other_account):
    ok('POST', '/admin/accounts', root, {'account_id': aid, 'admin_user_id': 'admin'})
def user(aid, uid):
    return ok('POST', f'/admin/accounts/{aid}/users', root, {'user_id': uid, 'role': 'user'})['user_key']
alice, bob, other_alice = user(account, 'alice'), user(account, 'bob'), user(other_account, 'alice')
uris = {peer: f'viking://user/alice/peers/{peer}/memories/preference.md' for peer in ('project-a', 'project-b')}
for peer, uri in uris.items():
    ok('POST', '/content/write', alice, {'uri': uri,
       'content': f'用户在 {peer} 中偏好精简技术文档。', 'mode': 'create', 'wait': True, 'timeout': 120})
assert 'project-b' in ok('GET', '/content/read', alice, params={'uri': uris['project-b']})

cases = [
    ('owner-project', alice, 'project-b', None),
    ('different-actor-peer', alice, 'project-a', None),
    ('no-actor-header', alice, None, None),
    ('different-user', bob, 'project-b', None),
    ('same-user-id-other-account', other_alice, 'project-b', None),
    ('forged-account-and-user', bob, 'project-b', {'X-OpenViking-Account': account, 'X-OpenViking-User': 'alice'}),
]
def probe(case):
    label, key, actor, extra = case
    response = call('GET', '/content/read', key, params={'uri': uris['project-b']}, actor=actor, extra=extra)
    return {'case': label, 'status': response.status_code,
            'syntheticOtherProjectExposed': response.status_code == 200 and 'project-b' in response.text}
with concurrent.futures.ThreadPoolExecutor(max_workers=len(cases)) as pool:
    reads = list(pool.map(probe, cases))
search = call('POST', '/search/find', alice, {
    'query': '技术文档偏好', 'target_uri': 'viking://user/alice/peers/project-b/memories',
    'limit': 10,
}, actor='project-a')
write = call('POST', '/content/write', alice, {'uri': uris['project-b'],
    'content': 'SYNTHETIC_CROSS_PEER_WRITE', 'mode': 'replace', 'wait': True, 'timeout': 120}, actor='project-a')
after = ok('GET', '/content/read', alice, params={'uri': uris['project-b']})
report = {'concurrentReads': reads,
          'crossPeerTargetSearchStatus': search.status_code,
          'crossPeerTargetSearchExposesContent': search.status_code == 200 and 'project-b' in search.text,
          'crossPeerWriteStatus': write.status_code,
          'crossPeerWriteChangedContent': 'SYNTHETIC_CROSS_PEER_WRITE' in after,
          'trustedHostScopeBindingVerified': False}
(run / 'peer-boundary-result.json').write_text(json.dumps(report))
print(json.dumps(report, indent=2))
