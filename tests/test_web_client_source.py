"""Distribution source delivery, using real disposable Git repositories.

The client's verifier/build are qualified separately in the owned DTU.
Run: python -m unittest discover -s tests -p test_web_client_source.py
"""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('web_client_source', ROOT / 'scripts/web_client_source.py')
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)


class WebClientSourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='web-client-delivery-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / 'unified'
        self.repo.mkdir()
        self.git(self.repo, 'init', '-q')
        self.source = self.repo / 'components/web-client'
        self.source.mkdir(parents=True)
        (self.source / 'scripts').mkdir()
        (self.source / 'scripts/client-source.mjs').write_text('// The client owns verification.\n')
        (self.source / 'README.md').write_text('Maintained by the client repository.\n')
        self.git(self.repo, 'add', '.')
        self.commit(self.repo)
        self.record = {'schema': 1, 'kind': 'amplifier-web-client-source',
                       'source': {'repository': 'https://github.com/microsoft/amplifier-client-web',
                                  'revision': 'a' * 40}, 'inputsSha256': 'b' * 64,
                       'delivery': {'type': 'directory', 'path': 'components/web-client'}}
        self.record_path = self.repo / 'components/web-client.source.json'
        self.save_record()
        self.output = self.root / 'prepared'
        actual_command = delivery.command
        self.verifications = []

        def command(*args, **kwargs):
            if args[0] == 'test-node':
                self.verifications.append(args)
                self.assertTrue(Path(args[1]).is_file())
                self.assertEqual(json.loads(Path(args[3]).read_text()), self.record)
                return subprocess.CompletedProcess(args, 0, '', '')
            return actual_command(*args, **kwargs)

        wrapper = patch.object(delivery, 'command', side_effect=command)
        wrapper.start()
        self.addCleanup(wrapper.stop)

    @staticmethod
    def git(root, *args):
        return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.PIPE).decode().strip()

    def commit(self, root):
        self.git(root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 'commit', '-qm', 'Source fixture')
        return self.git(root, 'rev-parse', 'HEAD')

    def save_record(self):
        self.record_path.write_text(json.dumps(self.record))

    def prepare(self):
        return delivery.prepare(self.repo, self.record_path, self.output, node='test-node')

    def test_directory_copies_tracked_source_and_record_without_build_state(self):
        (self.source / 'node_modules').mkdir()
        (self.source / 'node_modules/private').write_text('local only')
        (self.source / 'dist').mkdir()
        (self.source / 'dist/index.html').write_text('generated')
        (self.repo / 'unrelated-secret').write_text('local only')
        result = self.prepare()
        self.assertEqual(result['source'], self.record['source'])
        self.assertEqual(sorted(str(p.relative_to(self.output)) for p in self.output.rglob('*') if p.is_file()),
                         ['.client-source.json', 'README.md', 'scripts/client-source.mjs'])
        self.assertEqual(len(self.verifications), 1)
        self.assertFalse(list(self.root.glob('.web-client-source-*')))

    def test_repository_delivery_uses_exact_commit_after_copy_is_removed(self):
        canonical = self.root / 'canonical'
        delivery.copy_tracked(self.source, canonical)
        self.git(canonical, 'init', '-q')
        self.git(canonical, 'add', '.')
        selected = self.commit(canonical)
        (canonical / 'README.md').write_text('A later, unselected version')
        self.git(canonical, 'add', '.')
        self.commit(canonical)
        import shutil
        shutil.rmtree(self.source)
        self.record['source']['revision'] = selected
        self.record['delivery'] = {'type': 'repository'}
        self.save_record()

        def local_download(*args, **kwargs):
            self.assertEqual(args[-1], selected)
            self.assertEqual(args[-2], self.record['source']['repository'] + '.git')
            return delivery.command(*args[:-2], canonical, args[-1], capture=True, **kwargs)

        with patch.object(delivery, 'download_command', side_effect=local_download):
            self.prepare()
        self.assertEqual((self.output / 'README.md').read_text(), 'Maintained by the client repository.\n')
        self.assertFalse((self.output / '.git').exists())

    def test_failed_verification_removes_only_owned_staging(self):
        sentinel = self.root / 'existing-work'
        sentinel.write_text('keep')
        actual_command = delivery.command

        def reject(*args, **kwargs):
            if args[0] == 'test-node':
                raise subprocess.CalledProcessError(1, args, stderr='Source differs')
            return actual_command(*args, **kwargs)

        with patch.object(delivery, 'command', side_effect=reject):
            with self.assertRaises(subprocess.CalledProcessError):
                self.prepare()
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob('.web-client-source-*')))
        self.assertEqual(sentinel.read_text(), 'keep')

    def test_existing_output_and_external_source_are_preserved(self):
        self.output.mkdir()
        (self.output / 'work').write_text('keep')
        with self.assertRaisesRegex(ValueError, 'new output'):
            self.prepare()
        self.assertEqual((self.output / 'work').read_text(), 'keep')
        self.output = self.root / 'new-output'
        self.record['delivery']['path'] = '../canonical'
        self.save_record()
        with self.assertRaisesRegex(ValueError, 'repository-relative'):
            self.prepare()

    def test_tracked_link_is_not_followed(self):
        (self.source / 'link').symlink_to(self.record_path)
        self.git(self.repo, 'add', 'components/web-client/link')
        with self.assertRaisesRegex(ValueError, 'regular source file'):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_git_free_export_copies_only_canonical_production_manifest(self):
        source = self.root / 'export'
        (source / 'scripts').mkdir(parents=True)
        (source / 'scripts/client-source.mjs').write_text('// Canonical manifest owner')
        (source / 'package.json').write_text('{}')
        (source / 'unrelated-private-file').write_text('Do not copy')
        actual_command = delivery.command

        def command(*args, **kwargs):
            if args[0] == 'manifest-node':
                self.assertIn('selectedInputs', args[3])
                return subprocess.CompletedProcess(args, 0,
                    json.dumps(['package.json', 'scripts/client-source.mjs']))
            return actual_command(*args, **kwargs)

        with patch.object(delivery, 'command', side_effect=command):
            delivery.copy_tracked(source, self.output, node='manifest-node')
        self.assertEqual(sorted(str(p.relative_to(self.output)) for p in self.output.rglob('*')
                                if p.is_file()), ['package.json', 'scripts/client-source.mjs'])


if __name__ == '__main__':
    unittest.main()
