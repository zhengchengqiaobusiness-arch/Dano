"""Exercise the administrator's shell entry point against release artifacts."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class BundleTests(unittest.TestCase):
    def test_bundle_detects_changed_configuration_template(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / 'deployment'
            subprocess.run(['python3', str(ROOT / 'scripts/package-deploy.py'),
                            '--output', str(bundle), '--images',
                            str(ROOT / 'deploy/bundle/images.example.json')], check=True)
            manifest = json.loads((bundle / 'manifest.json').read_text())
            self.assertEqual(manifest['version'], json.loads((ROOT / 'package.json').read_text())['version'])
            for filename in ['deploy.sh', 'instance.example.json', 'dano.env.example', 'README.md']:
                self.assertTrue((bundle / filename).is_file())
            with (bundle / 'dano.env.example').open('a') as config:
                config.write('# changed\n')
            result = subprocess.run([str(bundle / 'deploy.sh'), '--verify-package'],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('dano.env.example', result.stderr)


    def test_installs_updates_recovers_and_rolls_back_through_shell(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            docker = root / 'docker'
            docker.write_text('#!' + sys.executable + '\n' + Path(__file__).with_name('fake_docker.py').read_text())
            docker.chmod(0o700)
            # Redirect the production host lock to the fixture and identify the
            # fixture as Linux. The shell, Python CLI and Docker argv are real.
            (root / 'sitecustomize.py').write_text(
                'import platform, os\n'
                'platform.system = lambda: "Linux"\n'
                'platform.machine = lambda: "x86_64"\n'
                '_open = os.open\n'
                'def open_lock(path, *args, **kwargs):\n'
                '    if str(path) == "/var/lock/dano-production-deploy.lock":\n'
                '        path = ' + repr(str(root / 'lock')) + '\n'
                '    return _open(path, *args, **kwargs)\n'
                'os.open = open_lock\n')
            cli_env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ['PATH'],
                           PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE='1')
            config = root / 'instance.json'
            control = root / 'control'
            runtime = root / 'runtime'
            config.write_text(json.dumps({'project': 'test-dano', 'controlDirectory': str(control),
                                         'runtimeDirectory': str(runtime), 'exposure': 'http',
                                         'baseUrl': 'http://localhost:38333/'}))
            env_file = root / 'dano.env'
            env_file.write_text('DANO_OAUTH_CLIENT_SECRET=synthetic-private\n')
            env_file.chmod(0o600)
            source = json.loads((ROOT / 'deploy/bundle/images.example.json').read_text())
            image_a = source['images']['app']['id']
            image_b = 'sha256:' + 'e' * 64
            version = json.loads((ROOT / 'package.json').read_text())['version']
            engine = root / 'engine.json'
            engine.write_text(json.dumps({'images': {item: {'version': version, 'revision': source['revision']}
                                                     for item in (image_a, image_b, source['images']['nginx']['id'])},
                                          'containers': []}))
            bundles = {}
            for name, image in (('a', image_a), ('b', image_b)):
                source['images']['app']['id'] = image
                metadata = root / 'images.json'
                metadata.write_text(json.dumps(source))
                bundle = root / name
                subprocess.run(['python3', str(ROOT / 'scripts/package-deploy.py'),
                                '--output', str(bundle), '--images', str(metadata)], check=True)
                bundles[name] = bundle

            def deploy(name, *extra, success=True):
                result = subprocess.run([str(bundles[name] / 'deploy.sh'), '--config', str(config),
                                         '--source', 'local', *extra],
                                        env=cli_env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0 if success else 1, result.stderr)
                self.assertNotIn('synthetic-private', result.stderr + result.stdout)
                if success:
                    return json.loads(Path(result.stdout.strip()).read_text())
                return result.stderr

            self.assertIn('DANO_OAUTH_CLIENT_ID', deploy('a', success=False))
            self.assertFalse(control.exists())
            with env_file.open('a') as stream:
                stream.write('DANO_NGINX_PORT=38333\n')
                for field in ('ISSUER', 'AUTHORIZATION_ENDPOINT', 'TOKEN_ENDPOINT', 'IDENTITY_ENDPOINT',
                              'API_ORIGIN', 'CLIENT_ID', 'SCOPE'):
                    stream.write('DANO_OAUTH_' + field + '=synthetic\n')
                stream.write('DANO_OAUTH_REDIRECT_URI=http://localhost:38333/api/auth/callback\n')
            self.assertEqual(deploy('a')['operation'], 'install')
            before = (control / '.env').read_bytes()
            self.assertEqual(deploy('a')['operation'], 'rerun')
            self.assertEqual((control / '.env').read_bytes(), before)
            state = json.loads(engine.read_text())
            engine.write_text(json.dumps(dict(state, failImage=image_b)))
            self.assertIn('rolled_back:', deploy('b', success=False))
            self.assertEqual(json.loads(engine.read_text())['current'], image_a)
            self.assertEqual((control / '.env').read_bytes(), before)
            state = json.loads(engine.read_text())
            state.pop('failImage')
            engine.write_text(json.dumps(state))
            self.assertEqual(deploy('b')['runningImageId'], image_b)
            self.assertEqual(deploy('b', '--rollback')['runningImageId'], image_a)
            self.assertEqual((control / '.env').read_bytes(), before)
