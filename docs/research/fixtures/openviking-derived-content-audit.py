"""Audit all publicly enumerable files in the synthetic deletion-barrier user.

Includes hidden summaries and overviews, with paginated per-directory listing.
Does not inspect or patch private service storage.
"""
import json
import io
import sys
import zipfile
from pathlib import Path
import httpx

run = Path(sys.argv[1])
config = json.loads((run / 'ov.conf').read_text())
state = json.loads((run / 'deletion-barrier-state.json').read_text())
assert config['server']['host'] in ('127.0.0.1', 'localhost')
assert state['account'].startswith('barrier-') and state['phase'] == 'deleted'
client = httpx.Client(base_url=f"http://127.0.0.1:{config['server']['port']}/api/v1",
                     headers={'X-API-Key': state['key']}, timeout=120, trust_env=False)


def get(path, params):
    response = client.get(path, params=params)
    assert response.status_code == 200, f'{path}: HTTP {response.status_code}'
    return response.json()['result']


pending = ['viking://user/alice']
directories, files = set(), set()
while pending:
    uri = pending.pop()
    if uri in directories:
        continue
    directories.add(uri)
    offset = 0
    while True:
        entries = get('/fs/ls', {'uri': uri, 'recursive': False, 'show_all_hidden': True,
                                'output': 'original', 'offset': offset, 'limit': 100})
        for entry in entries:
            child = entry['uri']
            assert child.startswith('viking://user/alice/'), 'Unexpected scope in listing'
            if entry['isDir']:
                pending.append(child)
            else:
                files.add(child)
        if len(entries) < 100:
            break
        offset += len(entries)
leaks = []
retained = []
hidden = 0
for uri in sorted(files):
    raw = get('/content/read', {'uri': uri, 'raw': True})
    text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
    if '目标和非目标' in text:
        leaks.append(uri)
    if '简体中文' in text:
        retained.append(uri)
    hidden += uri.rsplit('/', 1)[-1].startswith('.')
response = client.post('/pack/export', json={'uri': 'viking://user/alice', 'include_vectors': False})
assert response.status_code == 200
export_leaks = []
export_text_count = 0
with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
    assert archive.testzip() is None
    for name in archive.namelist():
        if name.endswith('/'):
            continue
        try:
            text = archive.read(name).decode('utf-8')
        except UnicodeDecodeError:
            continue
        if name.endswith('.json'):
            text = json.dumps(json.loads(text), ensure_ascii=False)
        export_text_count += 1
        if '目标和非目标' in text:
            export_leaks.append(name)
report = {'publicDirectoriesVisited': len(directories), 'publicFilesRead': len(files),
          'hiddenSummaryFilesRead': hidden, 'forgottenFactLocations': leaks,
          'unrelatedFactLocations': retained, 'privateStorageInspected': False,
          'exportTextEntriesRead': export_text_count, 'exportForgottenFactLocations': export_leaks,
          'semanticParaphraseAuditVerified': False}
(run / 'derived-content-audit-result.json').write_text(json.dumps(report))
print(json.dumps(report, ensure_ascii=False))
assert not leaks, 'Forgotten fact remains in public derived content'
assert not export_leaks, 'Forgotten fact remains in user export'
assert retained, 'Unrelated preference missing from public content'
assert hidden > 0, 'Hidden summaries were not audited'
