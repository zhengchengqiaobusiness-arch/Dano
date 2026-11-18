import json
import tempfile
import time
import unittest
from pathlib import Path
from release import Release, publish_document
import hashlib


class FakeRelease(Release):
    def __init__(self, directory, fail=None):
        self.calls = []
        self.fail = fail
        self.m = {}
        self.receipts = Path(directory)
        self.receipt = {'acceptance': 'not_requested'}
        self.started = time.monotonic()
        self.timings = {}
        self.switched = False
        self.phase = 'preflight'

    def prepare(self):
        self.calls.append('preflight')
        if self.fail == 'preflight':
            raise RuntimeError('IMAGE_PROVENANCE_MISMATCH')
        self.candidate_id = 'sha256:candidate'

    def switch(self):
        self.switched = True
        self.calls.append('switch')
        if self.fail == 'switch':
            raise RuntimeError('COMMAND_FAILED')

    def verify(self, image):
        self.calls.append('health-and-smoke')
        if self.fail == 'smoke':
            raise RuntimeError('COMMAND_FAILED')

    def finish(self):
        self.calls.append('cleanup')
        if self.fail == 'cleanup':
            raise RuntimeError('COMMAND_FAILED')

    def publish_document(self):
        self.calls.append('document-publication')
        if self.fail == 'document-publication':
            raise RuntimeError('DOCUMENT_HASH_MISMATCH')
        self.receipt['documentPublication'] = {'status': 'synced'}

    def rollback(self):
        self.calls.append('rollback')
        if self.fail == 'rollback':
            raise RuntimeError('COMMAND_FAILED')


class TransactionTests(unittest.TestCase):
    def run_release(self, fail=None):
        with tempfile.TemporaryDirectory() as directory:
            release = FakeRelease(directory, fail)
            result = release.execute()
            receipt = json.loads((Path(directory) / 'receipt.json').read_text())
            return result, release.calls, receipt

    def test_finishes_after_machine_checks_without_acceptance_wait(self):
        result, calls, receipt = self.run_release()
        self.assertEqual(result, 0)
        self.assertEqual(calls, ['preflight', 'switch', 'health-and-smoke', 'document-publication', 'cleanup'])
        self.assertEqual(receipt['status'], 'deployed')
        self.assertEqual(receipt['acceptance'], 'not_requested')

    def test_preflight_failure_never_switches_service(self):
        result, calls, receipt = self.run_release('preflight')
        self.assertEqual(result, 1)
        self.assertNotIn('switch', calls)
        self.assertEqual(receipt['status'], 'failed_before_switch')
        self.assertEqual(receipt['error'], 'IMAGE_PROVENANCE_MISMATCH')

    def test_switch_and_smoke_failures_roll_back(self):
        for failure in ['switch', 'smoke']:
            result, calls, receipt = self.run_release(failure)
            self.assertEqual(result, 1)
            self.assertEqual(calls[-1], 'rollback')
            self.assertEqual(receipt['status'], 'rolled_back')

    def test_cleanup_failure_does_not_roll_back_healthy_deployment(self):
        result, calls, receipt = self.run_release('cleanup')
        self.assertEqual(result, 0)
        self.assertNotIn('rollback', calls)
        self.assertEqual(receipt['status'], 'deployed')
        self.assertEqual(receipt['cleanup'], 'pending')

    def test_document_failure_reports_incomplete_without_traffic_rollback(self):
        result, calls, receipt = self.run_release('document-publication')
        self.assertEqual(result, 1)
        self.assertNotIn('rollback', calls)
        self.assertEqual(receipt['status'], 'deployed')
        self.assertEqual(receipt['documentPublication']['status'], 'failed')
        self.assertEqual(receipt['cleanup'], 'complete')

    def test_rollback_failure_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            release = FakeRelease(directory, 'rollback')
            def failed_switch():
                release.switched = True
                raise RuntimeError('COMMAND_FAILED')
            release.switch = failed_switch
            self.assertEqual(release.execute(), 1)
            receipt = json.loads((Path(directory) / 'receipt.json').read_text())
            self.assertEqual(receipt['status'], 'rollback_failed')

# Run the real preparation/rollback methods against a synthetic Docker boundary.
# No socket, live config, secrets or running service is used by these fixtures.
from unittest.mock import patch
import os
import datetime


class DocumentTests(unittest.TestCase):
    def test_creates_readable_document_and_preserves_other_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / 'source.md'
            data = '表单指南\n'.encode('utf8')
            source.write_bytes(data)
            target = root / 'doc' / 'guide.md'
            digest = hashlib.sha256(data).hexdigest()
            publish_document(source, target, digest)
            self.assertEqual(target.read_bytes(), data)
            self.assertEqual(target.stat().st_mode & 0o777, 0o644)
            other = target.parent / 'other.md'
            other.write_text('keep')
            target.write_text('old')
            publish_document(source, target, digest)
            self.assertEqual(target.read_bytes(), data)
            self.assertEqual(other.read_text(), 'keep')

    def test_hash_failure_preserves_existing_document(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.md'
            target = Path(directory) / 'guide.md'
            source.write_text('new')
            target.write_text('old')
            with self.assertRaisesRegex(RuntimeError, 'DOCUMENT_HASH_MISMATCH'):
                publish_document(source, target, '0' * 64)
            self.assertEqual(target.read_text(), 'old')

    def test_rejects_symlink_file_and_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / 'source.md'
            source.write_text('source')
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            link = root / 'link.md'
            link.symlink_to(source)
            parent = root / 'linked'
            parent.symlink_to(root, target_is_directory=True)
            for target in [link, parent / 'guide.md']:
                with self.assertRaisesRegex(RuntimeError, 'DOCUMENT_TARGET_INVALID'):
                    publish_document(source, target, digest)

    def test_replace_failure_preserves_old_document_and_removes_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory).resolve() / 'source.md'
            target = Path(directory).resolve() / 'guide.md'
            source.write_text('new')
            target.write_text('old')
            with patch('release.os.replace', side_effect=OSError('fixture')):
                with self.assertRaises(OSError):
                    publish_document(source, target, hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(target.read_text(), 'old')
            self.assertEqual(list(Path(directory).glob('.guide.md.*')), [])

    def test_receipt_proves_exact_source_commit_and_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / 'question-guide.md'
            source.write_bytes(b'guide\n')
            release = Release.__new__(Release)
            release.work = root
            publication = dict(sourceCommit='a' * 40, sourcePath='docs/guide.md',
                               targetPath=str(root / 'doc' / 'guide.md'),
                               sha256=hashlib.sha256(source.read_bytes()).hexdigest())
            release.m = dict(targetSha='a' * 40, documentPublication=publication)
            release.receipt = {}
            release.publish_document()
            self.assertEqual(release.receipt['documentPublication'], dict(publication, status='synced'))
            release.m['targetSha'] = 'b' * 40
            with self.assertRaisesRegex(RuntimeError, 'DOCUMENT_COMMIT_MISMATCH'):
                release.publish_document()


class CommandRelease(Release):
    def __init__(self, directory, drift=False, wrong_revision=False, overlay=False):
        root = Path(directory)
        profile = json.loads((Path(__file__).parent / 'profile.json').read_text())
        profile.update(deployDirectory=str(root), receiptDirectory=str(root / 'receipts'))
        for name in profile['composeFiles']:
            (root / name).write_text('services: {}')
        (root / '.env').write_text('DANO_IMAGE=old-image\nTEST_PRIVATE_VALUE=synthetic-kept\n')
        if overlay:
            (root / 'docker-compose.product-name.json').write_text('{}')
        manifest = dict(profile=profile, attemptId='00000000-0000-4000-8000-000000000001',
                        targetSha='a' * 40, version='1.2.3', image='prepared-image',
                        checkedAt=datetime.datetime.utcnow().isoformat() + 'Z')
        super().__init__(manifest, root)
        self.calls = []
        self.drift = drift
        self.wrong_revision = wrong_revision
        self.projections = 0

    def app(self):
        return {'Id': 'app-id', 'Image': 'sha256:previous', 'RestartCount': 0,
                'State': {'Health': {'Status': 'healthy'}}, 'Config': {'Labels': {'com.docker.compose.project.config_files': ','.join(str(self.directory / file) for file in self.compose_files)}}}

    def containers(self):
        return [self.app(), {'Id': 'nginx-id', 'Image': 'nginx-image'}]

    def docker_json(self, *args):
        if args[:2] == ('image', 'inspect'):
            return [{'Id': 'sha256:candidate', 'Config': {'Labels': {
                'org.opencontainers.image.revision': ('b' if self.wrong_revision else 'a') * 40,
            }}}]
        return [{'Id': args[-1] + '-id', 'State': {'Running': True}}]

    def projection(self, running_app=None):
        self.projections += 1
        return {'rejectedHashSources': 0, 'services': {'app': {
            'image': 'old-image', 'heimdallProtection': 'enabled',
            'mounts': [{'target': '/unexpected'}] if self.drift and self.projections > 1 else []}}}

    def command(self, args, capture=False, **kwargs):
        self.calls.append(args)
        if capture and 'package-versions/package.json' in args[-1]:
            return json.dumps({'version': '1.2.3', 'assets': ['index.js', 'index.css']})
        if capture and 'resolveProductName' in args[-1]:
            return json.dumps({'productName': 'Fixture assistant'})
        return None


class PreparationTests(unittest.TestCase):
    def test_smoke_manifest_comes_from_the_verified_image_including_rollback(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryFile() as lock:
            release = CommandRelease(directory)
            with patch.dict(os.environ, {'DANO_DEPLOY_LOCK_FD': str(lock.fileno())}):
                release.prepare()
            release.image_artifacts = lambda image: {'assets': [image + '.js', image + '.css']}
            release.verify('sha256:previous')
            smoke = next(call for call in release.calls if '/smoke.mjs' in call)
            value = next(arg for arg in smoke if arg.startswith('DANO_SMOKE_EXPECTED_ASSETS_JSON='))
            self.assertEqual(json.loads(value.split('=', 1)[1]),
                             ['sha256:previous.js', 'sha256:previous.css'])

    def test_changes_only_image_and_checks_system_identity(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryFile() as lock:
            release = CommandRelease(directory)
            with patch.dict(os.environ, {'DANO_DEPLOY_LOCK_FD': str(lock.fileno())}):
                release.prepare()
            self.assertEqual((Path(directory) / '.env').read_text(),
                             'DANO_IMAGE=sha256:candidate\nTEST_PRIVATE_VALUE=synthetic-kept\n')
            self.assertEqual(release.receipt['productName'], 'Fixture assistant')
            self.assertTrue(any('--expected-product-name' in call for call in release.calls))

    def test_wrong_revision_never_changes_env_or_starts_a_service(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryFile() as lock:
            release = CommandRelease(directory, wrong_revision=True)
            original = (Path(directory) / '.env').read_bytes()
            with patch.dict(os.environ, {'DANO_DEPLOY_LOCK_FD': str(lock.fileno())}):
                self.assertEqual(release.execute(), 1)
            self.assertEqual((Path(directory) / '.env').read_bytes(), original)
            self.assertFalse(any('up' in call for call in release.calls))

    def test_compose_drift_restores_env_before_any_switch(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryFile() as lock:
            release = CommandRelease(directory, drift=True)
            original = (Path(directory) / '.env').read_bytes()
            with patch.dict(os.environ, {'DANO_DEPLOY_LOCK_FD': str(lock.fileno())}):
                self.assertEqual(release.execute(), 1)
            self.assertEqual((Path(directory) / '.env').read_bytes(), original)
            self.assertFalse(any('up' in call for call in release.calls))


class FinalizationTests(unittest.TestCase):
    def test_final_receipt_failure_keeps_backups_and_never_rolls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            release = FakeRelease(directory)
            record = release.record
            def fail_deployed(status):
                if status == 'deployed':
                    raise OSError('synthetic write failure')
                record(status)
            release.record = fail_deployed
            self.assertEqual(release.execute(), 1)
            self.assertNotIn('rollback', release.calls)
            self.assertNotIn('cleanup', release.calls)

    def test_deployed_receipt_exists_before_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            release = FakeRelease(directory)
            def check_receipt():
                receipt = json.loads((Path(directory) / 'receipt.json').read_text())
                self.assertEqual(receipt['status'], 'deployed')
                self.assertEqual(receipt['cleanup'], 'pending')
            release.finish = check_receipt
            self.assertEqual(release.execute(), 0)

    def test_product_overlay_is_included(self):
        with tempfile.TemporaryDirectory() as directory:
            release = CommandRelease(directory, overlay=True)
            self.assertIn('docker-compose.product-name.json', release.compose)

    def test_system_restore_failure_still_recovers_previous_app(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryFile() as lock:
            release = CommandRelease(directory)
            with patch.dict(os.environ, {'DANO_DEPLOY_LOCK_FD': str(lock.fileno())}):
                release.prepare()
            release.switched = True
            original = release.command
            def fail_system(args, **kwargs):
                if 'restore' in args:
                    raise RuntimeError('COMMAND_FAILED')
                return original(args, **kwargs)
            release.command = fail_system
            with self.assertRaisesRegex(RuntimeError, 'ROLLBACK_INCOMPLETE'):
                release.rollback()
            self.assertTrue(any('up' in call for call in release.calls))
            self.assertEqual(release.receipt['recovery'], {'env': 'restored', 'system': 'failed', 'app': 'healthy'})


class LiveConfigurationTests(unittest.TestCase):
    def test_unapplied_compose_environment_is_rejected_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            release = CommandRelease(directory)
            release.command = lambda *args, **kwargs: json.dumps({'services': {'app': {'environment': {'FIXTURE_VALUE': 'new'}}}})
            with self.assertRaisesRegex(RuntimeError, 'LIVE_CONFIG_DRIFT'):
                Release.projection(release, {'Config': {'Env': ['FIXTURE_VALUE=old']}})
            self.assertFalse(release.env_changed)


if __name__ == '__main__':
    unittest.main()
