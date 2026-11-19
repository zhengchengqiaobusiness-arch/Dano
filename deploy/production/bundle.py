"""Target-host deployment of an immutable Dano release bundle."""
import argparse
import base64
import copy
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.parse import urlsplit
import uuid

from release import ReleaseHost

DIGEST = r'sha256:[0-9a-f]{64}'
PINNED = r'[a-zA-Z0-9][a-zA-Z0-9./:_-]*@' + DIGEST
KEY = 'DANO_OAUTH_CREDENTIAL_KEY'
KEY_VERSION = KEY + '_VERSION'
CA_TARGET = '/run/dano-trust/ca.pem'
HOST_ARCH = {'x86_64': 'amd64', 'aarch64': 'arm64'}
REQUIRED_OAUTH = ('ISSUER', 'AUTHORIZATION_ENDPOINT', 'TOKEN_ENDPOINT', 'IDENTITY_ENDPOINT',
                  'API_ORIGIN', 'CLIENT_ID', 'CLIENT_SECRET', 'SCOPE', 'REDIRECT_URI')


class DeploymentError(RuntimeError):
    """Message contains public diagnostics only."""


def digest(path):
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(chunk)
    return checksum.hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding='utf8'))


def validate_images(metadata):
    if (metadata['architecture'] not in HOST_ARCH.values()
            or not re.fullmatch(r'[0-9a-f]{40}', metadata['revision'])
            or set(metadata['images']) != {'app', 'nginx'}):
        raise DeploymentError('Invalid release image metadata')
    for image in metadata['images'].values():
        if not re.fullmatch(DIGEST, image['id']) or not re.fullmatch(PINNED, image['ref']):
            raise DeploymentError('Image IDs and registry references must be pinned by digest')


def safe_relative(name):
    path = Path(name)
    return not path.is_absolute() and '..' not in path.parts and bool(path.parts)


def validate_archive(archive, images):
    """Require Docker save by image ID so load cannot retag a neighbor's image."""
    expected = {image['id'] for image in images.values()}
    try:
        with tarfile.open(archive, mode='r:') as stream:
            data = stream.getmember('manifest.json')
            if not data.isfile() or data.size > 64 * 1024:
                raise ValueError()
            records = json.load(stream.extractfile(data))
            found = set()
            for record in records:
                config = record['Config']
                if not safe_relative(config) or record.get('RepoTags'):
                    raise ValueError()
                member = stream.getmember(config)
                if not member.isfile() or member.size > 16 * 1024 * 1024:
                    raise ValueError()
                found.add('sha256:' + hashlib.sha256(stream.extractfile(member).read()).hexdigest())
            if found != expected or len(records) != len(expected):
                raise ValueError()
    except (tarfile.TarError, KeyError, ValueError, TypeError, AttributeError):
        raise DeploymentError('Offline archive must contain exactly the declared images saved by ID') from None


def verify_package(bundle):
    metadata = read_json(bundle / 'manifest.json')
    validate_images(metadata)
    if not re.fullmatch(r'\d+\.\d+\.\d+', metadata['version']):
        raise DeploymentError('Invalid package version')
    required = {'deploy.sh', 'bundle.py', 'release.py', 'smoke.mjs', 'README.md',
                'instance.example.json', 'dano.env.example', 'docker-compose.yml',
                'compose/standalone.yml', 'compose/http.yml', 'compose/https.yml', 'compose/trust.yml',
                'nginx/http.conf.template', 'nginx/https.conf.template'}
    if required - metadata['files'].keys():
        raise DeploymentError('Package manifest is incomplete')
    for directory in ('nginx', 'help'):
        actual = {str(path.relative_to(bundle)) for path in (bundle / directory).rglob('*') if path.is_file()}
        if actual - metadata['files'].keys():
            raise DeploymentError('Unlisted files under ' + directory)
    for name, expected in metadata['files'].items():
        if not safe_relative(name):
            raise DeploymentError('Invalid path in package manifest')
        path = bundle / name
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise DeploymentError('Package checksum mismatch: ' + name)
    if 'archiveSha256' in metadata:
        archive = bundle / 'images.tar'
        if archive.is_symlink() or not archive.is_file() or digest(archive) != metadata['archiveSha256']:
            raise DeploymentError('Package checksum mismatch: images.tar')
        validate_archive(archive, metadata['images'])
    return metadata


def store(path, contents):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.deploy-', delete=False, mode='w',
                                     encoding='utf8') as stream:
        pending = Path(stream.name)
    try:
        with pending.open('w', encoding='utf8') as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        pending.replace(path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        pending.unlink(missing_ok=True)


def json_store(path, value):
    store(path, json.dumps(value, indent=2) + '\n')


def secure_path(path):
    if not path.is_absolute() or path != path.resolve():
        raise DeploymentError('Use absolute paths without symlinks')
    return path


def compose_environment():
    # Instance configuration comes solely from the explicit Compose environment.
    environment = {key: value for key, value in os.environ.items()
                   if key in ('PATH', 'HOME', 'DOCKER_CONFIG', 'LANG', 'LC_ALL')}
    environment['DOCKER_HOST'] = 'unix:///var/run/docker.sock'
    return environment


@contextmanager
def lock(path):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DeploymentError('Deployment busy. Retry after the active command finishes.')
        yield
    finally:
        os.close(fd)


def command(args, cwd=None, capture=False, input_data=None, timeout=120, environment=None):
    result = subprocess.run(args, cwd=cwd, env=environment if environment is not None else compose_environment(), input=input_data,
                            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=timeout)
    if result.returncode:
        if args[:2] == ['docker', 'compose'] and '--env-file' in args:
            stage = args[args.index('--env-file') + 2]
            raise DeploymentError('Compose ' + stage + ' failed. Check the instance environment and topology.')
        raise DeploymentError('Command failed: ' + ' '.join(args[:2]))
    return result.stdout.decode('utf8') if capture else None


def config_errors(config):
    errors = []
    for name in ('project', 'controlDirectory', 'runtimeDirectory', 'baseUrl', 'exposure'):
        if not isinstance(config.get(name), str) or not config[name]:
            errors.append(name)
    if errors:
        return errors
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', config['project']):
        errors.append('project')
    try:
        url = urlsplit(config['baseUrl'])
        port = url.port
        if (url.scheme != config['exposure'] or not url.hostname or url.username or url.password
                or url.path not in ('', '/') or url.query or url.fragment or port == 0
                or config['exposure'] not in ('http', 'https')):
            errors.append('baseUrl / exposure')
    except ValueError:
        errors.append('baseUrl')
    for name in ('controlDirectory', 'runtimeDirectory'):
        path = Path(config[name])
        if not path.is_absolute() or path == path.parent or path != path.resolve():
            errors.append(name)
    control, runtime = (Path(config[name]) for name in ('controlDirectory', 'runtimeDirectory'))
    if control == runtime or control in runtime.parents or runtime in control.parents:
        errors.append('controlDirectory / runtimeDirectory must be separate')
    value = config.get('healthTimeoutSeconds', 180)
    if type(value) is not int or not 1 <= value <= 3600:
        errors.append('healthTimeoutSeconds')
    files = config.get('existingComposeFiles')
    if files is not None and (not isinstance(files, list) or not files
                              or not all(isinstance(item, str) and safe_relative(item) for item in files)):
        errors.append('existingComposeFiles')
    return errors


def env_names(text):
    # Compose parses dotenv quoting and interpolation. Inspect only assignments
    # under our control here, leaving values to Compose's parser.
    return set(re.findall(r'(?m)^\s*(?:export\s+)?([A-Za-z_][A-Za-z_0-9]*)\s*[=:]', text))


def set_image(text, image):
    lines = text.splitlines()
    indexes = [i for i, line in enumerate(lines) if line.startswith('DANO_IMAGE=')]
    if len(indexes) > 1 or ('DANO_IMAGE' in env_names(text) and not indexes):
        raise DeploymentError('Use one canonical DANO_IMAGE assignment')
    if indexes:
        lines[indexes[0]] = 'DANO_IMAGE=' + image
    else:
        lines.append('DANO_IMAGE=' + image)
    return '\n'.join(lines) + '\n'


class BundleRelease(ReleaseHost):
    def __init__(self, metadata, bundle, config, directory, files, operation):
        attempt = str(uuid.uuid4())
        self.config = config
        self.operation = operation
        self.exposure = config['exposure']
        self.legacy = bool(config.get('existingComposeFiles'))
        self.receipt_root = directory / 'releases'
        if self.receipt_root.is_symlink():
            raise DeploymentError('Invalid receipt directory')
        self.receipt_root.mkdir(mode=0o700, exist_ok=True)
        super().__init__({
            'attemptId': attempt, 'targetSha': metadata['revision'], 'version': metadata['version'],
            'image': metadata['images']['app']['id'],
            'profile': {'deployDirectory': str(directory), 'receiptDirectory': str(self.receipt_root),
                        'baseUrl': config['baseUrl'], 'composeProject': config['project'],
                        'appService': 'app', 'nginxService': 'nginx', 'composeFiles': files,
                        'healthTimeoutSeconds': config.get('healthTimeoutSeconds', 180),
                        'commandTimeoutSeconds': 120, 'smokeTimeoutSeconds': 90},
        }, bundle)
        self.compose[2:2] = ['-p', config['project']]
        self.receipt['operation'] = operation
        self.phase_started = time.monotonic()
        self.receipt.pop('documentPublication')
        self.receipt.pop('ci')

    def command(self, args, capture=False, input_data=None, timeout=None):
        return command(args, self.directory, capture, input_data, timeout or 120)

    def begin(self, phase):
        now = time.monotonic()
        self.timings[self.phase] = round(now - self.phase_started, 3)
        self.phase = phase
        self.phase_started = now
        self.record('running')

    def verify(self, expected_image, report=False):
        if report:
            self.begin('container-health')
        app = self.wait_for_app(expected_image)
        if report:
            self.receipt['health'] = 'healthy'
            self.begin('http-sse-smoke')
        self.command(self.compose + ['exec', '-T', 'nginx', 'nginx', '-t'])
        self.command(self.compose + ['exec', '-T', 'nginx', 'nginx', '-s', 'reload'])
        self.smoke(expected_image, self.exposure, self.config.get('trustedCaFile'))
        if hasattr(self, 'adjacent') and self.adjacent != self.adjacent_containers():
            raise DeploymentError('An adjacent Compose service changed during deployment')
        if report:
            self.receipt.update(containerId=app['Id'], runningImageId=expected_image,
                                health='healthy', smoke='passed')

    def adjacent_containers(self):
        return {item['Id']: item['Image'] for item in self.containers()
                if item['Config']['Labels'].get('com.docker.compose.service') != 'app'}

    def launch(self, first):
        args = ['up', '-d', '--no-build', '--pull', 'never']
        args += ['app', 'nginx'] if first else ['--no-deps', '--force-recreate', 'app']
        self.command(self.compose + args, timeout=self.p['healthTimeoutSeconds'])

    def settings(self, file):
        args = self.compose[:-2] + ['--env-file', str(file), 'config', '--format', 'json']
        return json.loads(self.command(args, capture=True))

    def run_app(self, args, user='1000:1000', capture=False):
        return self.command(self.compose + ['run', '--rm', '-T', '--interactive=false', '--no-deps',
                                           '--pull', 'never', '--user', user, '--entrypoint', 'node',
                                           'app'] + args, capture=capture)

    def gates(self, file, resolved):
        previous = self.compose[-1]
        self.compose[-1] = str(file)
        try:
            self.run_app(['./dist/server/main.js', '--validate-config'])
            self.run_app(['./dist/server/bridge/sandbox-preflight.js'], user='0:0')
            app = resolved['services']['app']
            if any(mount.get('target') == '/etc/dano-memory' for mount in app.get('volumes', [])):
                self.run_app(['./deploy/memory-models.mjs', 'preflight', '/etc/dano-memory'])
        finally:
            self.compose[-1] = previous

    def owned_containers(self):
        for container in self.containers():
            labels = container['Config']['Labels']
            files = labels.get('com.docker.compose.project.config_files', '').split(',')
            if (labels.get('com.docker.compose.project.working_dir') != str(self.directory)
                    or files != [str(self.directory / name) for name in self.compose_files]):
                raise DeploymentError('Compose project belongs to another deployment')

    def acquire_images(self, metadata, source, first, download=True):
        images = metadata['images']
        selected = {'app': images['app']}
        if first:
            selected['nginx'] = images['nginx']
        if download:
            if source == 'archive':
                archive = self.work / 'images.tar'
                if 'archiveSha256' not in metadata:
                    raise DeploymentError('Package has no offline archive')
                self.command(['docker', 'image', 'load', '--input', str(archive)], timeout=600)
            elif source == 'registry':
                for image in selected.values():
                    self.command(['docker', 'image', 'pull', image['ref']], timeout=600)
        for image in selected.values():
            reference = image['ref'] if source == 'registry' and download else image['id']
            actual = self.docker_json('image', 'inspect', reference)[0]
            if (actual['Id'] != image['id'] or actual['Architecture'] != metadata['architecture']
                    or actual['Os'] != 'linux'):
                raise DeploymentError('Image ID or architecture mismatch')
            if image is images['app']:
                labels = actual['Config'].get('Labels') or {}
                if labels.get('org.opencontainers.image.revision') != metadata['revision']:
                    raise DeploymentError('Dano image revision mismatch')
        packaged = self.image_artifacts(images['app']['id'])
        if packaged['version'] != metadata['version'] or not packaged['assets']:
            raise DeploymentError('Dano image version or asset manifest mismatch')
        self.receipt['imageId'] = images['app']['id']
        self.receipt['assets'] = packaged['assets']


def standalone_files(exposure):
    return ['docker-compose.yml', 'compose/standalone.yml', 'compose/' + exposure + '.yml']


def first_environment(config, contents, metadata, control, runtime, existing_key=None):
    names = env_names(contents)
    if bool(KEY in names) != bool(KEY_VERSION in names):
        raise DeploymentError('Configure the OAuth credential key and version together')
    missing = {'DANO_OAUTH_' + name for name in REQUIRED_OAUTH} - names
    if config['exposure'] == 'https':
        missing |= {'DANO_TLS_CERT_PATH', 'DANO_TLS_KEY_PATH'} - names
    if missing:
        raise DeploymentError('Missing deployment configuration: ' + ', '.join(sorted(missing)))
    if existing_key and KEY in names:
        raise DeploymentError('Omit credential key assignments when retrying installation')
    if existing_key:
        contents += '\n' + ''.join("{}='{}'\n".format(name, existing_key[name]) for name in (KEY, KEY_VERSION))
    elif KEY not in names:
        material = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip('=')
        contents += '\n{}={}\n{}=dano-deploy-v1\n'.format(KEY, material, KEY_VERSION)
    values = {
        'DANO_RUNTIME_DIR': runtime, 'DANO_HELP_DIR': control / 'help',
        'DANO_SECRETS_DIR': control / '.secrets',
        'DANO_NGINX_CONF': control / 'nginx' / (config['exposure'] + '.conf.template'),
        'DANO_NGINX_SHARED_DIR': control / 'nginx' / 'shared',
        'DANO_NGINX_IMAGE': metadata['images']['nginx']['id'],
    }
    if config.get('trustedCaFile'):
        values['DANO_TRUSTED_CA_FILE'] = config['trustedCaFile']
    if names.intersection(values) or 'DANO_IMAGE' in names:
        raise DeploymentError('Remove generated image and directory assignments from the input environment')
    return contents.rstrip() + '\n' + ''.join("{}='{}'\n".format(k, v) for k, v in values.items())


def validate_candidate(release, file, mode):
    resolved = release.settings(file)
    app = resolved['services']['app']
    env = app.get('environment', {})
    errors = [name for name in ('DANO_OAUTH_' + key for key in REQUIRED_OAUTH) if not env.get(name)]
    for name in ('DANO_OAUTH_ALLOW_INSECURE_SERVER_ENDPOINTS', 'FETCH_WEB_INSECURE_TLS'):
        if str(env.get(name, '')).lower() in ('1', 'true', 'yes', 'on'):
            errors.append(name)
    callback = urlsplit(env.get('DANO_OAUTH_REDIRECT_URI') or '')
    public = urlsplit(release.p['baseUrl'])
    if (callback.scheme, callback.netloc) != (public.scheme, public.netloc):
        errors.append('DANO_OAUTH_REDIRECT_URI / baseUrl')
    nginx = resolved['services']['nginx']
    if mode == 'https' and not release.legacy:
        tls_sources = {entry['target']: entry.get('source') for entry in nginx.get('volumes', [])}
        for name in ('fullchain.pem', 'privkey.pem'):
            source = tls_sources.get('/etc/nginx/tls/' + name)
            if not source or not Path(source).is_file():
                errors.append('TLS ' + name)
        if not errors:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            try:
                context.load_cert_chain(tls_sources['/etc/nginx/tls/fullchain.pem'],
                                        tls_sources['/etc/nginx/tls/privkey.pem'], password='')
            except (OSError, ssl.SSLError):
                errors.append('TLS certificate/key pair')
    if errors:
        raise DeploymentError('Invalid or missing deployment configuration: ' + ', '.join(sorted(set(errors))))
    return resolved


def check_ports(resolved):
    # Read kernel TCP LISTEN sockets rather than probing by binding a live port.
    listening = set()
    for path in (Path('/proc/net/tcp'), Path('/proc/net/tcp6')):
        if path.is_file():
            for line in path.read_text().splitlines()[1:]:
                fields = line.split()
                if fields[3] == '0A':
                    listening.add(int(fields[1].rsplit(':', 1)[1], 16))
    docker = command(['docker', 'ps', '-q'], capture=True).split()
    if docker:
        for container in json.loads(command(['docker', 'inspect'] + docker, capture=True)):
            for bindings in (container['NetworkSettings'].get('Ports') or {}).values():
                listening.update(int(binding['HostPort']) for binding in bindings or [])
    published = {int(port['published']) for port in resolved['services']['nginx'].get('ports', [])}
    conflicts = sorted(listening & published)
    if conflicts:
        raise DeploymentError('Published ports already in use: ' + ', '.join(map(str, conflicts)))


def instance_config(path):
    config = read_json(path)
    errors = config_errors(config)
    if errors:
        raise DeploymentError('Invalid or missing instance configuration: ' + ', '.join(errors))
    for name in ('controlDirectory', 'runtimeDirectory'):
        if any(char in config[name] for char in "\n\r'\0:,"):
            raise DeploymentError('Invalid characters in ' + name)
    if 'trustedCaFile' in config:
        try:
            ca = secure_path(Path(config['trustedCaFile']))
            if any(char in str(ca) for char in "\n\r'\0:,") or not ca.is_file():
                raise ValueError()
            ssl.create_default_context(cafile=str(ca))
        except (TypeError, ValueError, OSError):
            raise DeploymentError('trustedCaFile must point to a readable absolute PEM CA file') from None
    config['baseUrl'] = config['baseUrl'].rstrip('/') + '/'
    return config


def install_templates(bundle, control, files):
    for name in files:
        target = control / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundle / name, target)
    for name in ('nginx', 'help'):
        shutil.copytree(bundle / name, control / name)


def deploy(bundle, metadata, config_path, args):
    config = instance_config(config_path)
    validate_host(metadata)
    control = secure_path(Path(config['controlDirectory']))
    runtime = secure_path(Path(config['runtimeDirectory']))
    legacy = config.get('existingComposeFiles')
    expected_files = legacy or standalone_files(config['exposure'])
    if config.get('trustedCaFile') and not legacy:
        expected_files.append('compose/trust.yml')
    identity = {key: config[key] for key in ('project', 'baseUrl', 'exposure', 'runtimeDirectory')}
    identity['composeFiles'] = expected_files
    if config.get('trustedCaFile'):
        identity['trustedCaFile'] = config['trustedCaFile']
    # Share the lock with the established production entry point.
    with lock(Path('/var/lock/dano-production-deploy.lock')):
        state_file = control / '.deployment.json'
        if state_file.is_symlink():
            raise DeploymentError('Invalid instance state path')
        state = read_json(state_file) if state_file.is_file() else None
        if state and state['identity'] != identity:
            raise DeploymentError('Instance identity differs from the existing deployment')
        first_layout = not state and not legacy
        if first_layout:
            if args.rollback:
                raise DeploymentError('No deployment to roll back')
            if control.exists() or runtime.exists():
                raise DeploymentError('Use unused control and runtime directories for initial installation')
            existing = command(['docker', 'ps', '-aq', '--filter',
                                'label=com.docker.compose.project=' + config['project']], capture=True)
            resources = [resource for kind in ('volume', 'network') for resource in
                         command(['docker', kind, 'ls', '-q', '--filter',
                                  'label=com.docker.compose.project=' + config['project']], capture=True).split()]
            if existing.strip() or resources:
                raise DeploymentError('Compose project already owns resources')
            source = config_path.parent / config.get('environmentFile', 'dano.env')
            contents = first_environment(config, read_environment(args.retry_environment or source),
                                         metadata, control, runtime)
            control.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='.dano-install-', dir=control.parent) as work:
                staging = Path(work) / 'control'
                staging.mkdir()
                install_templates(bundle, staging, expected_files)
                (staging / '.secrets').mkdir()
                store(staging / '.env', set_image(contents, metadata['images']['app']['id']))
                release = BundleRelease(metadata, bundle, config, staging, expected_files, 'install')
                resolved = validate_candidate(release, release.env_file, release.exposure)
                check_ports(resolved)
                release.acquire_images(metadata, args.source, first=True)
                auth_preflight(resolved['services']['app'], metadata['images']['app']['id'], config.get('trustedCaFile'))
                state = {'identity': identity, 'current': None, 'previous': None, 'pending': None}
                json_store(staging / state_file.name, state)
                staging.rename(control)
        if not control.is_dir() or not (control / '.env').is_file():
            raise DeploymentError('Deploy Control Directory or .env is missing')
        for item in [control / '.env', state_file] + [control / name for name in expected_files]:
            if item.is_symlink() or (item != state_file and not item.is_file()):
                raise DeploymentError('Invalid managed configuration path')
        release = BundleRelease(metadata, bundle, config, control, expected_files, 'install')
        release.owned_containers()
        if state is None:
            app = release.app()
            if app['State'].get('Health', {}).get('Status') != 'healthy':
                raise DeploymentError('Existing instance must be healthy before enrollment')
            verify_live_mounts(release, app, release.settings(release.env_file))
            old = release.image_artifacts(app['Image'])
            if not old['assets']:
                raise DeploymentError('Existing app is not a supported Dano release')
            state = {'identity': identity, 'current': {'image': app['Image'], 'version': old['version']},
                     'previous': None, 'pending': None}
        if state['pending']:
            marker = release.receipt_root / state['pending'] / 'machine-verified.json'
            if marker.is_file():
                recovered = read_json(marker)
                if recovered['identity'] != identity or release.app()['Image'] != recovered['current']['image']:
                    raise DeploymentError('Verified checkpoint does not match the running deployment')
                json_store(state_file, recovered)
                state = recovered
        run_transaction(release, state, state_file, metadata, args, images_ready=first_layout)


def validate_host(metadata):
    if platform.system() != 'Linux' or HOST_ARCH.get(platform.machine()) != metadata['architecture']:
        raise DeploymentError('Run on a supported Linux host matching the manifest architecture')
    if not shutil.which('docker'):
        raise DeploymentError('Docker Engine and Compose v2 are required')
    command(['docker', 'info'])
    version = command(['docker', 'compose', 'version', '--short'], capture=True).strip()
    match = re.match(r'v?(\d+)\.(\d+)\.(\d+)', version)
    if not match or tuple(map(int, match.groups())) < (2, 24, 4):
        raise DeploymentError('Docker Compose v2.24.4 or newer is required')


def read_environment(path):
    path = secure_path(path.absolute())
    if not path.is_file() or path.stat().st_mode & 0o077:
        raise DeploymentError('Set environment file permissions to 0600')
    return path.read_text(encoding='utf8')


def auth_preflight(app, image, ca_file=None):
    values = {key: str(value) for key, value in app['environment'].items() if value is not None}
    environment = compose_environment()
    environment.update(values)
    args = ['docker', 'run', '--rm', '--pull', 'never', '--user', '1000:1000',
            '--entrypoint', 'node']
    if ca_file:
        args += ['-v', ca_file + ':' + CA_TARGET + ':ro']
    for key in values:
        args += ['--env', key]
    command(args + [image, './dist/server/main.js', '--validate-config'], environment=environment)


def verify_live_mounts(release, app, resolved):
    configured = resolved['services']['app'].get('volumes', [])
    mounts = {item['Destination']: item for item in app['Mounts']}
    for item in configured:
        target = item['target']
        live = mounts.get(target)
        if item.get('type') == 'volume':
            resource = resolved.get('volumes', {}).get(item.get('source'), {})
            expected = resource.get('name')
            actual = live.get('Name') if live else None
        else:
            expected = item.get('source')
            actual = live.get('Source') if live else None
        if not live or not expected or actual != expected or live['RW'] == bool(item.get('read_only')):
            raise DeploymentError('Running app mounts differ from the configured topology')
    runtime = mounts.get('/opt/dano/runtime-data')
    if not runtime or runtime.get('Source') != release.config['runtimeDirectory']:
        raise DeploymentError('Configured Runtime Data Directory differs from the running instance')


def topology(config):
    comparable = copy.deepcopy(config)
    app = comparable['services']['app']
    app.pop('image', None)
    app.pop('environment', None)
    return hashlib.sha256(json.dumps(comparable, sort_keys=True).encode()).hexdigest()


def restore_baseline(release, checkpoint, current):
    saved = (checkpoint / 'env.before').read_text(encoding='utf8')
    store(release.env_file, set_image(saved, current['image']))
    prompt_restored = True
    if (checkpoint / 'prompt.before').is_file():
        try:
            release.run_app(['./deploy/system-prompt.mjs', 'restore', checkpoint.name])
        except RuntimeError:
            prompt_restored = False
    release.launch(False)
    release.verify(current['image'])
    if not prompt_restored:
        raise DeploymentError('Previous app recovered but system prompt restore failed')


def run_transaction(release, state, state_file, metadata, args, images_ready=False):
    current = state['current']
    first = current is None
    target = state['previous'] if args.rollback else {
        'image': metadata['images']['app']['id'], 'version': metadata['version']}
    if args.retry_environment and current:
        raise DeploymentError('--retry-environment is only for an unfinished initial installation')
    txn = release.receipts / 'candidate.env'
    checkpoint = release.receipts / 'env.before'
    changed = False
    passed = False
    recovery_failed = False
    try:
        release.begin('recovery')
        if state['pending']:
            pending = state['pending']
            if current:
                restore_baseline(release, release.receipt_root / pending, current)
            state['pending'] = None
            json_store(state_file, state)
        if not target:
            raise DeploymentError('No previous deployment is available')
        release.receipt.update(operation=('rollback' if args.rollback else 'install' if first else
                                          'rerun' if current == target else 'update'),
                               previousVersion=current['version'] if current else None,
                               previousImageId=current['image'] if current else None,
                               version=target['version'], image=target['image'])
        if current:
            app = release.app()
            release.adjacent = release.adjacent_containers()
            if app['Image'] != current['image']:
                raise DeploymentError('Running Dano image differs from the managed deployment')
            if app['State'].get('Health', {}).get('Status') != 'healthy':
                raise DeploymentError('Current Dano service is unhealthy. Restore the baseline before updating.')
        release.begin('image-validation')
        if args.rollback:
            archived = release.image_artifacts(target['image'])
            if archived['version'] != target['version'] or not archived['assets']:
                raise DeploymentError('Rollback image is unavailable or invalid')
            labels = release.docker_json('image', 'inspect', target['image'])[0]['Config'].get('Labels') or {}
            revision = labels.get('org.opencontainers.image.revision')
            if not revision or not re.fullmatch(r'[0-9a-f]{40}', revision):
                raise DeploymentError('Rollback image revision is unavailable')
            release.receipt.update(targetSha=revision, imageId=target['image'], assets=archived['assets'])
        else:
            release.acquire_images(metadata, args.source, first, download=not images_ready)
        release.begin('configuration')
        original = release.env_file.read_text(encoding='utf8')
        inputs = original
        if args.rollback and target.get('checkpoint'):
            inputs = (release.receipt_root / target['checkpoint'] / 'env.before').read_text(encoding='utf8')
        if args.retry_environment and not images_ready:
            existing = release.settings(release.env_file)['services']['app']['environment']
            material = {key: existing[key] for key in (KEY, KEY_VERSION)}
            # The existing nginx image remains the owner of its local port.
            saved_nginx = release.settings(release.env_file)['services']['nginx']['image']
            metadata = copy.deepcopy(metadata)
            metadata['images']['nginx']['id'] = saved_nginx
            inputs = first_environment(release.config, read_environment(args.retry_environment), metadata,
                                       release.directory, Path(release.config['runtimeDirectory']), material)
        if args.environment:
            overrides = read_environment(args.environment)
            allowed = {'DANO_URL', 'DANO_TENANT_KEY', 'AGENT_WORLD_API_KEY'}
            configured_app = release.settings(release.env_file)['services']['app'].get('environment', {})
            allowed.update(key for key in configured_app if key.endswith(('_API_KEY', '_API_KEY_FILE')))
            unexpected = env_names(overrides) - allowed
            if unexpected:
                raise DeploymentError('Unsupported environment override: ' + ', '.join(sorted(unexpected)))
            inputs = original.rstrip() + '\n' + overrides
        store(txn, set_image(inputs, target['image']))
        resolved = validate_candidate(release, txn, release.exposure)
        if first and not release.containers():
            check_ports(resolved)
        if current:
            before = release.settings(release.env_file)
            verify_live_mounts(release, release.app(), before)
            if topology(before) != topology(resolved):
                raise DeploymentError('Candidate changes the existing Compose topology')
            if state.get('topology') and state['topology'] != topology(before):
                raise DeploymentError('Compose topology differs from the last successful deployment')
            live = release.app()['Config'].get('Env')
            if live is not None:
                running_env = dict(item.split('=', 1) for item in live if '=' in item)
                declared_env = before['services']['app'].get('environment', {})
                if any(value is not None and running_env.get(key) != str(value)
                       for key, value in declared_env.items()):
                    raise DeploymentError('Running application configuration differs from the current environment file')
        app_mounts = resolved['services']['app'].get('volumes', [])
        runtime_mount = next((item for item in app_mounts if item['target'] == '/opt/dano/runtime-data'), None)
        if not runtime_mount or runtime_mount.get('source') != release.config['runtimeDirectory']:
            raise DeploymentError('Runtime Data Directory differs from the configured Compose mount')
        if str(resolved['services']['app'].get('environment', {}).get('HEIMDALL_PROTECT_CONFIG_OVERLAY')) == '0':
            raise DeploymentError('Container sandbox configuration protection must be enabled')
        release.gates(txn, resolved)
        store(checkpoint, set_image(original, current['image']) if current else original)
        release.begin('switch')
        state['pending'] = release.m['attemptId']
        json_store(state_file, state)
        changed = True
        store(release.env_file, txn.read_text(encoding='utf8'))
        if release.legacy and current and current['image'] != target['image']:
            attempt = release.m['attemptId']
            release.run_app(['./deploy/system-prompt.mjs', 'backup', attempt])
            store(release.receipts / 'prompt.before', '')
            if args.rollback and target.get('systemBackup'):
                release.run_app(['./deploy/system-prompt.mjs', 'restore', target['checkpoint']])
            else:
                for action in ('sync', 'check'):
                    release.run_app(['./deploy/system-prompt.mjs', action])
        app = release.containers()
        if inputs != original or release.receipt['operation'] != 'rerun' or not any(
                item['Image'] == target['image'] and item['State'].get('Health', {}).get('Status') == 'healthy'
                for item in app):
            release.launch(first)
        release.verify(target['image'], report=True)
        passed = True
        release.timings[release.phase] = round(time.monotonic() - release.phase_started, 3)
        release.phase = 'complete'
        previous = state['previous']
        if current and (current['image'] != target['image'] or inputs != original):
            previous = dict(current, checkpoint=release.m['attemptId'],
                            systemBackup=(release.receipts / 'prompt.before').is_file())
        final_state = dict(state, current={key: target[key] for key in ('image', 'version')},
                           previous=previous, pending=None, topology=topology(resolved))
        json_store(release.receipts / 'machine-verified.json', final_state)
        json_store(state_file, final_state)
        release.record('deployed')
    except Exception as error:
        failed_phase = release.phase
        known = {'RUNNING_IMAGE_MISMATCH', 'HEALTH_TIMEOUT', 'APP_RESTARTED_OR_OOM', 'APP_TOPOLOGY_MISMATCH'}
        release.receipt['error'] = (str(error) if isinstance(error, DeploymentError)
                                    or (type(error) is RuntimeError and str(error) in known) else 'DEPLOY_STEP_FAILED')
        status = 'incomplete' if passed else 'failed'
        if changed and current and not passed:
            try:
                restore_baseline(release, release.receipts, current)
                json_store(state_file, dict(state, pending=None))
                status = 'rolled_back'
            except Exception:
                status = 'rollback_failed'
                recovery_failed = True
        if not passed:
            release.phase = failed_phase
            try:
                release.record(status)
            except OSError:
                status += ' (receipt write failed)'
        guidance = 'Run the deployment again to retry recovery.' if recovery_failed else 'See the deployment receipt for the failed phase.'
        raise DeploymentError('{}: {}. {} Receipt directory: {}'.format(status, release.receipt['error'],
                              guidance, release.receipts)) from None
    finally:
        txn.unlink(missing_ok=True)
    print(release.receipts / 'receipt.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--source', choices=['archive', 'registry', 'local'], default='archive')
    parser.add_argument('--rollback', action='store_true')
    parser.add_argument('--environment', type=Path, help='apply explicit application environment overrides')
    parser.add_argument('--retry-environment', type=Path, help='replace inputs after a failed first installation')
    parser.add_argument('--verify-package', action='store_true')
    args = parser.parse_args()
    if args.rollback and (args.environment or args.retry_environment):
        parser.error('Environment options cannot be used with --rollback')
    if args.environment and args.retry_environment:
        parser.error('Use one environment option at a time')
    try:
        metadata = verify_package(args.bundle)
        if args.verify_package:
            print('Verified Dano deployment bundle ' + metadata['version'])
            return 0
        if not args.config:
            raise DeploymentError('--config is required')
        deploy(args.bundle, metadata, args.config.absolute(), args)
        return 0
    except (DeploymentError, OSError, KeyError, TypeError, ValueError, subprocess.SubprocessError) as error:
        print(str(error) if isinstance(error, DeploymentError) else 'Deployment failed: invalid input or host I/O error',
              file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
