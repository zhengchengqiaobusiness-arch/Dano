"""Exercise public governance primitives on the synthetic extraction fixture.

Usage: python openviking-governance-primitives.py /path/to/isolated/run-directory
Requires a completed openviking-model-extraction.py run. Mutates only its
synthetic account. Makes real model requests. This diagnoses upstream behavior;
it does not implement or pass Dano's durable deletion/queue barrier.
"""

import json
import sys
import time
from pathlib import Path

import httpx

run = Path(sys.argv[1])
config = json.loads((run / 'ov.conf').read_text())
state = json.loads((run / 'probe-state.json').read_text())
assert config['server']['host'] in ('127.0.0.1', 'localhost')
assert state['account'].startswith('model-'), 'Requires synthetic fixture account'
hits = json.loads((run / 'search-result.json').read_text())
uri = next(m['uri'] for m in hits['memories']
           if m['uri'].endswith('.md') and '/.' not in m['uri'])
client = httpx.Client(
    base_url=f"http://{config['server']['host']}:{config['server']['port']}/api/v1",
    headers={'X-API-Key': state['key']}, timeout=120, trust_env=False,
)


def request(method, path, body=None, params=None):
    response = client.request(method, path, json=body, params=params)
    if response.status_code != 200:
        raise RuntimeError(f'{method} {path.split("/")[1]}: HTTP {response.status_code}')
    return response.json()['result']


def read():
    return request('GET', '/content/read', params={'uri': uri, 'raw': True})


def find():
    return request('POST', '/search/find', {
        'query': '技术方案的语言和格式偏好',
        'target_uri': 'viking://user/alice/memories', 'limit': 10,
    })


original = read()
assert original.count('所有技术方案都要写清目标和非目标。') == 1
corrected = original.replace('所有技术方案都要写清目标和非目标。',
                             '所有技术方案都要写清验收步骤。')
request('POST', '/content/write', {'uri': uri, 'content': corrected,
        'mode': 'replace', 'wait': True, 'timeout': 60})
after_correction = read()
correction_search = json.dumps(find(), ensure_ascii=False)
report = {
    'correction_read_has_new_fact': '验收步骤' in after_correction,
    'correction_read_has_no_old_fact': '目标和非目标' not in after_correction,
    'correction_retains_language': '简体中文' in after_correction,
    'correction_search_has_no_old_fact': '目标和非目标' not in correction_search,
}
assert all(report.values()), report
print(json.dumps(report), flush=True)

# Selectively remove the corrected requirement while preserving the second fact.
forgotten = after_correction.replace('- 所有技术方案都要写清验收步骤。\n', '')
assert '验收步骤' not in forgotten and '简体中文' in forgotten
request('POST', '/content/write', {'uri': uri, 'content': forgotten,
        'mode': 'replace', 'wait': True, 'timeout': 60})
request('DELETE', f"/sessions/{state['sessionId']}")
source_status = client.get(f"/sessions/{state['sessionId']}").status_code
after_forget = read()
forget_search = json.dumps(find(), ensure_ascii=False)
report.update({
    'source_session_removed': source_status == 404,
    'selective_forget_retains_language': '简体中文' in after_forget,
    'selective_forget_read_has_no_target': '验收步骤' not in after_forget,
    'selective_forget_search_has_no_target': '验收步骤' not in forget_search,
})
assert all(report.values()), report
print(json.dumps(report), flush=True)

# Deliberately bypass an adapter barrier to observe whether upstream alone
# rejects an old source. This is an old-queue replay, not a new user permission.
session = request('POST', '/sessions', {'auto_commit_policy': None})
sid = session['session_id']
request('POST', f'/sessions/{sid}/messages', {
    'role': 'user',
    'content': '请记住我的稳定偏好：所有技术方案都要写清目标和非目标，默认用简体中文。',
    'source_message_ids': ['synthetic-preference-1'],
})
commit = request('POST', f'/sessions/{sid}/commit', {'keep_recent_count': 0})
report['replay_session_id'] = sid
report['replay_task_id'] = commit['task_id']
(run / 'governance-result.json').write_text(json.dumps(report, ensure_ascii=False))
for _ in range(36):
    task = request('GET', f"/tasks/{commit['task_id']}")
    status = task['status']
    if status in ('completed', 'failed', 'cancelled'):
        assert status == 'completed', 'Replay extraction did not complete'
        recalled = json.dumps(find(), ensure_ascii=False)
        report['old_source_replay_resurrects_original_fact'] = '目标和非目标' in recalled
        report['adapter_deletion_barrier_verified'] = False
        (run / 'governance-result.json').write_text(json.dumps(report, ensure_ascii=False))
        print(json.dumps({k: v for k, v in report.items() if not k.endswith('_id')}), flush=True)
        break
    time.sleep(5)
else:
    raise TimeoutError('Replay extraction did not finish in the probe window')
