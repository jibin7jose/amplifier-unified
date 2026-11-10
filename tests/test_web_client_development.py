"""Generated view refresh preserves edits and restores only its managed paths."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('web_client_development', ROOT / 'scripts/web_client_development.py')
development = importlib.util.module_from_spec(spec)
spec.loader.exec_module(development)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)


def snapshot(root):
    return {path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in root.rglob('*') if path.is_file()}


class DevelopmentViewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='client-view-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.frontend = self.root / 'frontend'
        self.source = self.root / 'selected'
        for name in development.VIEW_FILES:
            write(self.source / name, 'selected ' + name)
        for name in development.VIEW_DIRECTORIES:
            write(self.source / name / 'old.txt', 'selected ' + name)
        for name in ('tests/authored.mjs', 'scripts/authored.mjs', 'node_modules/dependency/index.js'):
            write(self.frontend / name, 'keep ' + name)
        mock = patch.object(development, 'command')
        mock.start()
        self.addCleanup(mock.stop)

    def prepare(self):
        development.prepare_view(self.root, self.source)

    def test_initial_top_level_experiments_are_preserved(self):
        for name in development.VIEW_FILES:
            with self.subTest(name=name):
                path = self.frontend / name
                write(path, 'unrecorded experiment')
                before = snapshot(self.frontend)
                with self.assertRaisesRegex(ValueError, 'Unrecorded client experiment'):
                    self.prepare()
                self.assertEqual(snapshot(self.frontend), before)
                path.unlink()

    def test_refresh_replaces_only_managed_paths_and_unchanged_view_is_not_rewritten(self):
        authored = snapshot(self.frontend)
        self.prepare()
        first = snapshot(self.frontend)
        self.prepare()
        self.assertEqual(snapshot(self.frontend), first)
        (self.source / 'src/old.txt').unlink()
        write(self.source / 'src/new.txt', 'new selected source')
        write(self.source / 'index.html', 'new index')
        self.prepare()
        development.verify_view(self.root, self.source)
        current = snapshot(self.frontend)
        self.assertNotIn('src/old.txt', current)
        self.assertEqual(current['src/new.txt'][0], b'new selected source')
        self.assertEqual({name: current[name] for name in authored}, authored)
        self.assertFalse(list(self.frontend.glob('.web-client-view-*')))

    def test_recorded_and_unrecorded_edits_prevent_every_write(self):
        self.prepare()
        for name in ('src/old.txt', 'index.html', 'scripts/build-identity.mjs', 'public/new.txt'):
            path = self.frontend / name
            previous = path.read_bytes() if path.exists() else None
            with self.subTest(name=name):
                write(path, 'developer experiment')
                before = snapshot(self.frontend)
                with self.assertRaisesRegex(ValueError, 'experiment|Edited'):
                    self.prepare()
                self.assertEqual(snapshot(self.frontend), before)
            path.write_bytes(previous) if previous is not None else path.unlink()

    def test_mid_install_failure_and_cancellation_restore_exact_prior_view(self):
        self.prepare()
        write(self.source / 'src/old.txt', 'new source')
        before = snapshot(self.frontend)
        rename = Path.rename
        for error in (OSError('injected install failure'), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                def fail(path, target):
                    if path.name == 'public' and path.parent.name == 'ready':
                        raise error
                    return rename(path, target)
                with patch.object(Path, 'rename', fail):
                    with self.assertRaises(type(error)):
                        self.prepare()
                self.assertEqual(snapshot(self.frontend), before)
                self.assertFalse(list(self.frontend.glob('.web-client-view-*')))

    def test_failed_rollback_keeps_previous_files_and_blocks_accidental_retry(self):
        self.prepare()
        before = snapshot(self.frontend)
        write(self.source / 'src/old.txt', 'new source')
        rename = Path.rename
        def fail(path, target):
            if path.parent.name == 'ready' and path.name == 'public':
                raise OSError('install failed')
            if path.parent.name == 'previous' and path.name == 'src':
                raise OSError('rollback failed')
            return rename(path, target)
        with patch.object(Path, 'rename', fail):
            with self.assertRaisesRegex(ValueError, 'recovery.json'):
                self.prepare()
        stage, = self.frontend.glob('.web-client-view-*')
        plan = json.loads((stage / 'recovery.json').read_text())
        self.assertIn('src', plan['previousPaths'])
        self.assertEqual((stage / 'previous/src/old.txt').read_bytes(), before['src/old.txt'][0])
        self.assertEqual((stage / 'displaced/src/old.txt').read_text(), 'new source')
        retained = snapshot(self.frontend)
        with self.assertRaisesRegex(ValueError, 'Unfinished client view refresh'):
            self.prepare()
        self.assertEqual(snapshot(self.frontend), retained)

    def test_edit_during_failure_is_retained_with_previous_view_for_recovery(self):
        self.prepare()
        write(self.source / 'src/old.txt', 'new source')
        rename = Path.rename
        def fail(path, target):
            if path.parent.name == 'ready' and path.name == 'public':
                write(self.frontend / 'src/old.txt', 'late developer edit')
                raise OSError('install failed after edit')
            return rename(path, target)
        with patch.object(Path, 'rename', fail):
            with self.assertRaisesRegex(ValueError, 'recovery.json'):
                self.prepare()
        stage, = self.frontend.glob('.web-client-view-*')
        self.assertEqual((self.frontend / 'src/old.txt').read_text(), 'late developer edit')
        self.assertEqual((stage / 'previous/src/old.txt').read_text(), 'selected src')

    def test_linked_managed_file_is_preserved(self):
        self.prepare()
        target = self.root / 'external'
        target.write_bytes((self.frontend / 'index.html').read_bytes())
        (self.frontend / 'index.html').unlink()
        try:
            (self.frontend / 'index.html').symlink_to(target)
        except OSError:
            self.skipTest('Creating a symlink is unavailable on this host')
        with self.assertRaisesRegex(ValueError, 'Unexpected generated client path'):
            self.prepare()
        self.assertEqual(target.read_bytes(), (self.source / 'index.html').read_bytes())


if __name__ == '__main__':
    unittest.main()
