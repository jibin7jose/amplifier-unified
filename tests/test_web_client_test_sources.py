"""Prepared authored sources preserve edits and recover whole-directory replacement."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('web_client_build', ROOT / 'scripts/web_client_build.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)
delivery = build.delivery


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def snapshot(path):
    return {p.relative_to(path).as_posix(): p.read_bytes()
            for p in path.rglob('*') if p.is_file()}


class PreparedClientTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / '.build/web-client'
        write(self.source / 'tests/meaningful.test.mjs', 'original assertions')
        self.record = delivery.development_inventory(self.source)
        write(self.source / '.client-development.json', json.dumps(self.record))

    def test_modified_added_and_deleted_tests_are_retained(self):
        target = self.source / 'tests/meaningful.test.mjs'
        for operation in ('edit', 'add', 'delete'):
            with self.subTest(operation=operation):
                target.write_text('original assertions')
                extra = self.source / 'tests/new.test.mjs'
                extra.unlink(missing_ok=True)
                if operation == 'edit':
                    target.write_text('new meaningful assertions')
                elif operation == 'add':
                    extra.write_text('new coverage')
                else:
                    target.unlink()
                before = snapshot(self.source)
                with self.assertRaisesRegex(ValueError, 'Edited prepared client source retained'):
                    delivery.verify_development(self.source)
                self.assertEqual(snapshot(self.source), before)

    def test_readme_edits_and_unknown_authored_members_are_preserved(self):
        readme = self.source / 'README.md'
        write(readme, 'original guidance')
        write(self.source / '.client-development.json', json.dumps(delivery.development_inventory(self.source)))
        for change in ('readme', 'new-note', 'new-nested-file'):
            with self.subTest(change=change):
                readme.write_text('original guidance')
                note = self.source / 'notes.txt'
                note.unlink(missing_ok=True)
                nested = self.source / 'notes/discovery.txt'
                nested.unlink(missing_ok=True)
                if change == 'readme':
                    readme.write_text('unsaved guidance')
                else:
                    write(note if change == 'new-note' else nested, 'new investigation')
                before = snapshot(self.source)
                with self.assertRaisesRegex(ValueError, 'Edited prepared client source retained'):
                    delivery.verify_development(self.source)
                self.assertEqual(snapshot(self.source), before)

    def test_selection_refresh_does_not_erase_a_readme_edit(self):
        source_record = {'schema': 1, 'kind': 'amplifier-web-client-source',
            'source': {'repository': 'https://github.com/microsoft/amplifier-client-web', 'revision': 'a' * 40},
            'inputsSha256': 'b' * 64, 'delivery': {'type': 'directory', 'path': 'components/web-client'}}
        write(self.source / '.client-source.json', json.dumps(source_record))
        write(self.source / 'README.md', 'original guidance')
        write(self.source / '.client-development.json', json.dumps(delivery.development_inventory(self.source)))
        source_record['source']['revision'] = 'c' * 40
        write(self.root / 'components/web-client.source.json', json.dumps(source_record))
        write(self.source / 'README.md', 'unsaved guidance')
        before = snapshot(self.source)
        with patch.object(build, 'verify_source'), patch.object(delivery, 'prepare') as prepare:
            with self.assertRaisesRegex(ValueError, 'Edited prepared client source retained'):
                build.prepare(self.root)
            prepare.assert_not_called()
        self.assertEqual(snapshot(self.source), before)

    def test_only_canonical_generated_public_members_are_ignored(self):
        write(self.source / 'public/authored.js', 'authored')
        generated = ['branding-fixture', 'worker-fixture.js']
        write(self.source / '.client-development.json', json.dumps(
            delivery.development_inventory(self.source, generated_public=generated)))
        write(self.source / 'public/branding-fixture/icon.png', 'generated')
        write(self.source / 'public/worker-fixture.js', 'generated')
        delivery.verify_development(self.source)
        write(self.source / 'public/authored.js', 'edited')
        with self.assertRaisesRegex(ValueError, 'Edited prepared client source retained'):
            delivery.verify_development(self.source)

    def test_dependencies_and_build_outputs_are_not_test_source(self):
        write(self.source / 'node_modules/tool/index.js', 'generated dependency')
        write(self.source / 'dist/index.html', 'generated artifact')
        before = snapshot(self.source)
        delivery.verify_development(self.source)
        self.assertEqual(snapshot(self.source), before)

    def test_linked_tests_are_never_followed_or_repaired(self):
        external = self.root / 'external'
        external.write_text('external content')
        target = self.source / 'tests/meaningful.test.mjs'
        target.unlink()
        try:
            target.symlink_to(external)
        except OSError:
            self.skipTest('Symlink creation unavailable')
        with self.assertRaisesRegex(ValueError, 'Expected regular prepared source'):
            delivery.verify_development(self.source)
        self.assertTrue(target.is_symlink())
        self.assertEqual(external.read_text(), 'external content')

    def test_unknown_directory_links_are_not_traversed(self):
        external = self.root / 'external-directory'
        write(external / 'private.txt', 'outside content')
        link = self.source / 'notes'
        try:
            link.symlink_to(external, target_is_directory=True)
        except OSError:
            self.skipTest('Symlink creation unavailable')
        with self.assertRaisesRegex(ValueError, 'Expected regular prepared source'):
            delivery.verify_development(self.source)
        self.assertEqual((external / 'private.txt').read_text(), 'outside content')
        self.assertTrue(link.is_symlink())

    def test_failed_replacement_restores_the_complete_previous_source(self):
        ready = self.root / 'ready'
        write(ready / 'tests/replacement.test.mjs', 'replacement assertions')
        before = snapshot(self.source)
        rename = Path.rename
        for failure in (OSError('replace refused'), KeyboardInterrupt()):
            def fail(path, destination):
                if path == ready:
                    raise failure
                return rename(path, destination)
            with self.subTest(failure=type(failure).__name__), patch.object(Path, 'rename', fail):
                with self.assertRaises(type(failure)):
                    build.replace_directory(ready, self.source)
            self.assertEqual(snapshot(self.source), before)
            self.assertFalse(list(self.source.parent.glob('.web-client-previous-*')))

    def test_failed_rollback_retains_recovery_and_prevents_an_accidental_retry(self):
        ready = self.root / 'ready'
        write(ready / 'tests/replacement.test.mjs', 'replacement assertions')
        before = snapshot(self.source)
        rename = Path.rename
        def fail(path, destination):
            if path == ready or path.name == 'source':
                raise OSError('publication or rollback refused')
            return rename(path, destination)
        with patch.object(Path, 'rename', fail), self.assertRaisesRegex(ValueError, 'Restore previous client'):
            build.replace_directory(ready, self.source)
        retained, = self.source.parent.glob('.web-client-previous-*')
        self.assertEqual(snapshot(retained / 'source'), before)
        # Preparation must stop before reading a selection or executing Node.
        with patch.object(delivery, 'selection', side_effect=AssertionError('must not inspect selection')):
            with self.assertRaisesRegex(ValueError, 'Previous client recovery retained'):
                build.prepare(self.root)
        self.assertEqual(snapshot(retained / 'source'), before)


if __name__ == '__main__':
    unittest.main()
