"""Reproduce the macOS isolated-service stop/copy/hash snapshot procedure.

Usage: python openviking-offline-snapshot.py SOURCE DESTINATION NEW_PORT [--stop-source]
SOURCE must be a task-owned dano-465-* loopback run. Requires permission to
inspect/stop its exact listener when --stop-source is explicitly supplied.
"""
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from probe_state import write_private_json

source, destination = map(lambda value: Path(value).resolve(), sys.argv[1:3])
port = int(sys.argv[3])
assert source.name.startswith('dano-465-') and destination.name.startswith('dano-465-')
assert source.parent == Path('/private/tmp') and destination.parent == source.parent
assert not destination.exists() and source != destination
config = json.loads((source / 'ov.conf').read_text())
assert config['server']['host'] in ('127.0.0.1', 'localhost')
assert Path(config['storage']['workspace']).resolve().is_relative_to(source)
assert port != config['server']['port']
with socket.socket() as check:
    check.bind(('127.0.0.1', port))
listing = subprocess.run(['/usr/sbin/lsof', '-t', f"-iTCP:{config['server']['port']}",
                          '-sTCP:LISTEN'], capture_output=True, text=True)
assert listing.returncode in (0, 1)
assert not listing.stderr.strip(), 'Cannot verify listener ownership'
pids = listing.stdout.split()
if pids:
    assert '--stop-source' in sys.argv[4:] and len(pids) == 1
    pid = int(pids[0])
    command = subprocess.check_output(['/bin/ps', '-p', str(pid), '-o', 'command='], text=True)
    assert 'openviking-server' in command and str(source / 'ov.conf') in command
    os.kill(pid, signal.SIGTERM)
    for _ in range(150):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(.1)
    else:
        raise RuntimeError('Source process not confirmed stopped; copy prohibited')
# Check for a process using this config even if it has not bound the port yet.
processes = subprocess.check_output(['/bin/ps', '-axo', 'command='], text=True).splitlines()
assert not any('openviking-server' in command and str(source / 'ov.conf') in command for command in processes)
shutil.copytree(source, destination, symlinks=True)
destination.chmod(0o700)
manifest = {}
for path in source.rglob('*'):
    if path.is_symlink():
        assert os.readlink(path) == os.readlink(destination / path.relative_to(source))
    elif path.is_file():
        relative = str(path.relative_to(source))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == hashlib.sha256((destination / relative).read_bytes()).hexdigest()
        manifest[relative] = digest
write_private_json(destination / 'snapshot-manifest.json', manifest)
config['storage']['workspace'] = str(destination / Path(config['storage']['workspace']).relative_to(source))
config['server']['port'] = port
write_private_json(destination / 'ov.conf', config)
write_private_json(destination / 'snapshot-evidence.json', {
    'sourceStoppedBeforeCopy': True, 'allRunFilesHashMatched': len(manifest),
    'dataFilesHashMatched': sum(name.startswith('data/') for name in manifest),
    'hostStateIncluded': True, 'manifest': 'snapshot-manifest.json',
})
print(json.dumps({'sourceStoppedBeforeCopy': True, 'allRunFilesHashMatched': len(manifest)}))
