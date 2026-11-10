"""Prepared test sources preserve edits and recover whole-directory replacement."""
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
                with self.assertRaisesRegex(ValueError, 'Edited prepared client tests retained'):
                    delivery.verify_development(self.source)
                self.assertEqual(snapshot(self.source), before)

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
        with self.assertRaisesRegex(ValueError, 'Expected regular prepared test source'):
            delivery.verify_development(self.source)
        self.assertTrue(target.is_symlink())
        self.assertEqual(external.read_text(), 'external content')

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
