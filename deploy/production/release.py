"""Fixed-host image deployment. Python 3.6 compatible; private output stays on host."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import tempfile


def publish_document(source, target, expected_hash):
    """Atomically publish one public document, never follow destination symlinks."""
    data = Path(source).read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_hash:
        raise RuntimeError('DOCUMENT_HASH_MISMATCH')
    target = Path(target)
    if not target.is_absolute():
        raise RuntimeError('DOCUMENT_TARGET_INVALID')
    for directory in reversed((target.parent,) + tuple(target.parent.parents)):
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            raise RuntimeError('DOCUMENT_TARGET_INVALID')
        if not directory.exists():
            directory.mkdir(mode=0o755)
            os.chmod(str(directory), 0o755)
    if target.parent.is_symlink() or target.is_symlink() or (target.exists() and not target.is_file()):
        raise RuntimeError('DOCUMENT_TARGET_INVALID')
    pending = None
    try:
        with tempfile.NamedTemporaryFile(dir=str(target.parent), prefix='.' + target.name + '.', delete=False) as output:
            pending = output.name
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
            os.fchmod(output.fileno(), 0o644)
        os.replace(pending, str(target))
        pending = None
        if hashlib.sha256(target.read_bytes()).hexdigest() != expected_hash:
            raise RuntimeError('DOCUMENT_READBACK_FAILED')
    finally:
        if pending is not None:
            os.unlink(pending)
    return expected_hash


class Release:
    def __init__(self, manifest, work):
        self.m = manifest
        self.p = manifest['profile']
        self.work = Path(work)
        self.directory = Path(self.p['deployDirectory'])
        self.receipts = Path(self.p['receiptDirectory']) / manifest['attemptId']
        self.receipts.mkdir(mode=0o700, parents=True)
        self.env_file = self.directory / '.env'
        self.backup = self.receipts / 'env.before'
        self.compose = ['docker', 'compose']
        self.compose_files = self.p['composeFiles'] + [file for file in self.p.get('optionalComposeFiles', []) if (self.directory / file).is_file()]
        for file in self.compose_files:
            self.compose += ['-f', file]
        self.compose += ['--env-file', '.env']
        self.old_image = None
        self.switched = False
        self.system_backup = False
        self.env_changed = False
        self.image_override = None
        self.phase = 'preflight'
        self.started = time.monotonic()
        self.timings = {}
        self.receipt = {key: manifest[key] for key in ['attemptId', 'targetSha', 'version', 'image']}
        self.receipt['baseUrl'] = self.p['baseUrl']
        self.receipt['acceptance'] = 'not_requested'
        self.receipt['browserSmoke'] = 'pending'
        self.receipt['ci'] = manifest.get('ci')
        self.receipt['documentPublication'] = dict(manifest.get('documentPublication', {}), status='pending')

    def command(self, args, capture=False, input_data=None, timeout=None):
        with (self.receipts / 'commands.log').open('ab') as log:
            env = dict(os.environ)
            if self.image_override:
                env["DANO_IMAGE"] = self.image_override
            result = subprocess.run(args, cwd=str(self.directory), input=input_data, env=env,
                                    stdout=subprocess.PIPE if capture else log, stderr=log,
                                    timeout=timeout or self.p['commandTimeoutSeconds'])
        if result.returncode:
            raise RuntimeError('COMMAND_FAILED')
        return result.stdout.decode('utf8') if capture else None

    def docker_json(self, *args):
        return json.loads(self.command(['docker'] + list(args), capture=True))

    def containers(self):
        ids = self.command(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + self.p['composeProject']], capture=True).split()
        return self.docker_json('inspect', *ids) if ids else []

    def app(self):
        matches = [c for c in self.containers() if c['Config']['Labels'].get('com.docker.compose.service') == self.p['appService']]
        if len(matches) != 1:
            raise RuntimeError('APP_TOPOLOGY_MISMATCH')
        return matches[0]

    def run_app(self, args, user='1000:1000', capture=False):
        return self.command(self.compose + ['run', '--rm', '-T', '--interactive=false', '--no-deps', '--user', user,
                                           '--entrypoint', 'node', self.p['appService']] + args, capture=capture)

    def projection(self, running_app=None):
        # Resolved environment is consumed on-host and never included in a receipt.
        raw = self.command(self.compose + ['config', '--format', 'json'], capture=True)
        if running_app is not None:
            live = dict(item.split('=', 1) for item in running_app['Config'].get('Env', []) if '=' in item)
            configured = json.loads(raw)['services'][self.p['appService']].get('environment', {})
            if any((value is not None and live.get(key) != str(value)) or
                   (value is None and key in live) for key, value in configured.items()):
                raise RuntimeError('LIVE_CONFIG_DRIFT')
        mounts = []
        for root in self.p['nginxHashRoots']:
            mounts += ['-v', root + ':' + root + ':ro']
        filtered = self.command(['docker', 'run', '--rm', '-i', '--network', 'none', '--entrypoint', 'node',
                                 '-e', 'DANO_NGINX_HASH_ROOTS_JSON=' + json.dumps(self.p['nginxHashRoots']),
                                 '-v', str(self.work / 'compose-filter.mjs') + ':/filter.mjs:ro'] + mounts +
                                [self.old_image, '/filter.mjs'], capture=True, input_data=raw.encode('utf8'))
        value = json.loads(filtered)
        if value['rejectedHashSources']:
            raise RuntimeError('COMPOSE_PROFILE_DRIFT')
        return value

    def image_artifacts(self, image):
        return json.loads(self.command(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'node', image, '-e',
            'const fs=require("fs");const p=JSON.parse(fs.readFileSync("/app/package-versions/package.json"));const h=fs.readFileSync("/app/dist/web/index.html","utf8");console.log(JSON.stringify({version:p.version,assets:[...h.matchAll(/assets\\/([^" ]+\\.(?:js|css))/g)].map(x=>x[1])}));'], capture=True))

    def prepare(self):
        if not os.environ.get('DANO_DEPLOY_LOCK_FD'):
            raise RuntimeError('DEPLOY_LOCK_REQUIRED')
        os.fstat(int(os.environ['DANO_DEPLOY_LOCK_FD']))
        if not self.directory.is_dir() or self.directory.is_symlink() or self.env_file.is_symlink():
            raise RuntimeError('DEPLOY_DIRECTORY_INVALID')
        for file in self.compose_files:
            if not (self.directory / file).is_file():
                raise RuntimeError('COMPOSE_PROFILE_DRIFT')
        app = self.app()
        configured = app['Config']['Labels'].get('com.docker.compose.project.config_files', '').split(',')
        if set(configured) != set(str(self.directory / file) for file in self.compose_files):
            raise RuntimeError('COMPOSE_PROFILE_DRIFT')
        if app['State'].get('Health', {}).get('Status') != 'healthy':
            raise RuntimeError('BASELINE_UNHEALTHY')
        self.old_image = app['Image']
        self.before_containers = {c['Id']: c['Image'] for c in self.containers() if c['Id'] != app['Id']}
        self.neighbors = {}
        for name in self.p['preservedContainers']:
            c = self.docker_json('inspect', name)[0]
            if not c['State']['Running']:
                raise RuntimeError('NEIGHBOR_UNHEALTHY')
            self.neighbors[name] = c['Id']
        try:
            candidate = self.docker_json('image', 'inspect', self.m['image'])[0]
        except RuntimeError:
            raise RuntimeError('PREPARED_IMAGE_UNAVAILABLE')
        labels = candidate['Config'].get('Labels') or {}
        if labels.get('org.opencontainers.image.revision') != self.m['targetSha'] or labels.get('org.opencontainers.image.version', self.m['version']) != self.m['version']:
            raise RuntimeError('IMAGE_PROVENANCE_MISMATCH')
        self.candidate_id = candidate['Id']
        self.receipt['imageId'] = self.candidate_id
        self.receipt['previousImageId'] = self.old_image
        packaged = self.image_artifacts(self.candidate_id)
        if packaged['version'] != self.m['version'] or not packaged['assets']:
            raise RuntimeError('IMAGE_ARTIFACT_MISMATCH')
        self.receipt['assets'] = packaged['assets']
        before = self.projection(app)
        if before['services'][self.p['appService']]['heimdallProtection'] == 'disabled':
            raise RuntimeError('SANDBOX_PROTECTION_DISABLED')
        shutil.copy2(str(self.env_file), str(self.backup))
        os.chmod(str(self.backup), 0o600)
        lines = self.env_file.read_text(encoding='utf8').splitlines()
        keys = [i for i, line in enumerate(lines) if line.startswith('DANO_IMAGE=')]
        if len(keys) != 1:
            raise RuntimeError('IMAGE_ENV_INVALID')
        lines[keys[0]] = 'DANO_IMAGE=' + self.candidate_id
        pending = self.receipts / 'env.pending'
        pending.write_text('\n'.join(lines) + '\n', encoding='utf8')
        os.chmod(str(pending), 0o600)
        os.replace(str(pending), str(self.env_file))
        self.env_changed = True
        self.image_override = self.candidate_id
        after = self.projection()
        after['services'][self.p['appService']]['image'] = before['services'][self.p['appService']]['image']
        if before != after:
            raise RuntimeError('COMPOSE_PROFILE_DRIFT')
        self.run_app(['./dist/server/main.js', '--validate-config'])
        self.run_app(['./dist/server/bridge/sandbox-preflight.js'], user='0:0')
        if any(mount.get('target') == '/etc/dano-memory' for mount in before['services'][self.p['appService']]['mounts']):
            self.run_app(['./deploy/memory-models.mjs', 'preflight', '/etc/dano-memory'])
        identity = self.run_app(['--input-type=module', '-e',
            'import {readFile} from "node:fs/promises";import {resolveProductName} from "./apps/dano/runtime/product-name.mjs";const c=JSON.parse(await readFile(process.env.DANO_CONFIG_PATH||"/app/dano.config.json","utf8"));console.log(JSON.stringify({productName:resolveProductName(process.env.DANO_PRODUCT_NAME,c.productName)}));'], capture=True)
        product_name = json.loads(identity)['productName']
        self.receipt['productName'] = product_name
        # sync checks the effective configured identity before writing SYSTEM.
        self.run_app(['./deploy/system-prompt.mjs', 'backup', self.m['attemptId']])
        self.system_backup = True
        for action in ['sync', 'check']:
            self.run_app(['./deploy/system-prompt.mjs', action, '--expected-product-name', product_name])
        checked = datetime.datetime.strptime(self.m['checkedAt'][:19], '%Y-%m-%dT%H:%M:%S')
        if (datetime.datetime.utcnow() - checked).total_seconds() > 300:
            raise RuntimeError('UPSTREAM_PROOF_EXPIRED')

    def switch(self):
        self.switched = True
        self.command(self.compose + ['up', '-d', '--no-build', '--no-deps', '--force-recreate', self.p['appService']])

    def wait_for_app(self, expected_image):
        deadline = time.monotonic() + self.p['healthTimeoutSeconds']
        while time.monotonic() < deadline:
            app = self.app()
            if app['Image'] != expected_image:
                raise RuntimeError('RUNNING_IMAGE_MISMATCH')
            if app['State'].get('Health', {}).get('Status') == 'healthy':
                break
            time.sleep(2)
        else:
            raise RuntimeError('HEALTH_TIMEOUT')
        if app['RestartCount'] or app['State'].get('OOMKilled'):
            raise RuntimeError('APP_RESTARTED_OR_OOM')
        return app

    def smoke(self, expected_image, exposure):
        self.command(['docker', 'run', '--rm', '--network', 'host', '--entrypoint', 'node',
                      '-e', 'DANO_SMOKE_BASE_URL=' + self.p['baseUrl'],
                      '-e', 'DANO_SMOKE_EXPECTED_ASSETS_JSON=' + json.dumps(self.image_artifacts(expected_image)['assets']),
                      '-e', 'DANO_EXPOSURE_MODE=' + exposure, '-v', str(self.work / 'smoke.mjs') + ':/smoke.mjs:ro',
                      expected_image, '/smoke.mjs'], timeout=self.p['smokeTimeoutSeconds'])

    def verify(self, expected_image):
        app = self.wait_for_app(expected_image)
        for action in [['-t'], ['-s', 'reload']]:
            self.command(self.compose + ['exec', '-T', self.p['nginxService'], 'nginx'] + action)
        self.smoke(expected_image, 'https')
        site_status = self.command(['curl', '--disable', '--fail', '--silent', '--show-error', '--max-time', '15',
                                    '--output', '/dev/null', '--write-out', '%{http_code}',
                                    self.p['baseUrl'] + 'web/'], capture=True)
        if len(site_status) != 3 or not site_status.isdigit() or not site_status.startswith('2'):
            raise RuntimeError('SITE_SMOKE_NOT_SUCCESSFUL')
        current = {c['Id']: c['Image'] for c in self.containers() if c['Id'] != app['Id']}
        if current != self.before_containers:
            raise RuntimeError('ADJACENT_SERVICE_CHANGED')
        for name, container_id in self.neighbors.items():
            c = self.docker_json('inspect', name)[0]
            if c['Id'] != container_id or not c['State']['Running']:
                raise RuntimeError('ADJACENT_SERVICE_CHANGED')
        self.receipt['containerId'] = app['Id']
        self.receipt['runningImageId'] = expected_image
        self.receipt['restartCount'] = app['RestartCount']
        self.receipt['health'] = 'healthy'
        self.receipt['smoke'] = 'passed'

    def rollback(self):
        errors = []
        recovery = {}
        if self.env_changed:
            try:
                pending = self.receipts / 'env.restore'
                shutil.copy2(str(self.backup), str(pending))
                os.replace(str(pending), str(self.env_file))
                recovery['env'] = 'restored'
            except Exception:
                errors.append('env')
                recovery['env'] = 'failed'
        self.image_override = self.old_image
        if self.system_backup:
            try:
                self.run_app(['./deploy/system-prompt.mjs', 'restore', self.m['attemptId']])
                recovery['system'] = 'restored'
            except Exception:
                errors.append('system')
                recovery['system'] = 'failed'
        if self.switched:
            try:
                self.command(self.compose + ['up', '-d', '--no-build', '--no-deps', '--force-recreate', self.p['appService']])
                self.verify(self.old_image)
                recovery['app'] = 'healthy'
            except Exception:
                errors.append('app')
                recovery['app'] = 'failed'
        self.receipt['recovery'] = recovery
        if errors:
            raise RuntimeError('ROLLBACK_INCOMPLETE')

    def finish(self):
        self.run_app(['./deploy/system-prompt.mjs', 'discard', self.m['attemptId']])
        self.backup.unlink()

    def publish_document(self):
        publication = self.m['documentPublication']
        if publication['sourceCommit'] != self.m['targetSha']:
            raise RuntimeError('DOCUMENT_COMMIT_MISMATCH')
        publish_document(self.work / 'question-guide.md', publication['targetPath'], publication['sha256'])
        self.receipt['documentPublication'] = dict(publication, status='synced')

    def record(self, status):
        self.receipt.update(status=status, phase=self.phase, timings=self.timings,
                            durationSeconds=round(time.monotonic() - self.started, 3),
                            checkedAt=datetime.datetime.utcnow().isoformat() + 'Z')
        pending = self.receipts / 'receipt.pending'
        with pending.open('w', encoding='utf8') as output:
            output.write(json.dumps(self.receipt, ensure_ascii=True, indent=2))
            output.flush()
            os.fsync(output.fileno())
        os.replace(str(pending), str(self.receipts / 'receipt.json'))

    def execute(self):
        try:
            for name, action in [('preflight', self.prepare), ('switch', self.switch), ('health-and-smoke', lambda: self.verify(self.candidate_id))]:
                self.phase = name
                started = time.monotonic()
                self.record('running')
                action()
                self.timings[name] = round(time.monotonic() - started, 3)
        except Exception as error:
            known = ['COMMAND_FAILED', 'APP_TOPOLOGY_MISMATCH', 'DEPLOY_LOCK_REQUIRED', 'DEPLOY_DIRECTORY_INVALID', 'COMPOSE_PROFILE_DRIFT', 'LIVE_CONFIG_DRIFT', 'BASELINE_UNHEALTHY', 'NEIGHBOR_UNHEALTHY', 'PREPARED_IMAGE_UNAVAILABLE', 'IMAGE_PROVENANCE_MISMATCH', 'IMAGE_ARTIFACT_MISMATCH', 'SANDBOX_PROTECTION_DISABLED', 'IMAGE_ENV_INVALID', 'UPSTREAM_PROOF_EXPIRED', 'RUNNING_IMAGE_MISMATCH', 'HEALTH_TIMEOUT', 'APP_RESTARTED_OR_OOM', 'ADJACENT_SERVICE_CHANGED', 'SITE_SMOKE_NOT_SUCCESSFUL']
            self.receipt['error'] = str(error) if str(error) in known else 'DEPLOY_STEP_FAILED'
            failed_phase = self.phase
            try:
                self.rollback()
                self.phase = failed_phase
                self.record('rolled_back' if self.switched else 'failed_before_switch')
            except Exception:
                self.phase = failed_phase
                self.record('rollback_failed')
            return 1

        # Health/smoke already passed. Persist that fact before deleting backups;
        # finalization/receipt failures must never enter the traffic rollback path.
        self.phase = 'complete'
        self.receipt['cleanup'] = 'pending'
        try:
            self.record('deployed')
        except Exception:
            return 1
        document_failed = False
        started = time.monotonic()
        try:
            self.publish_document()
        except Exception as error:
            document_failed = True
            code = str(error) if str(error) in ['DOCUMENT_COMMIT_MISMATCH', 'DOCUMENT_HASH_MISMATCH', 'DOCUMENT_TARGET_INVALID', 'DOCUMENT_READBACK_FAILED'] else 'DOCUMENT_PUBLICATION_FAILED'
            self.receipt['documentPublication'] = dict(self.m.get('documentPublication', {}), status='failed', errorCode=code)
        self.timings['document-publication'] = round(time.monotonic() - started, 3)
        try:
            self.finish()
            self.receipt['cleanup'] = 'complete'
        except Exception:
            pass
        try:
            self.record('deployed')
        except Exception:
            pass  # The durable deployed receipt above still records cleanup pending.
        return 1 if document_failed else 0


if __name__ == '__main__':
    os.umask(0o077)
    with open(sys.argv[1], encoding='utf8') as source:
        manifest = json.load(source)
    sys.exit(Release(manifest, sys.argv[2]).execute())
