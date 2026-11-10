"""Focused offline assembly, cache ownership and failed-install recovery checks."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('web_client_build', ROOT / 'scripts/web_client_build.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) if isinstance(value, dict) else value)


class ClientBuildTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='web-client-build-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / '.build/web-client'
        self.selected = self.root / 'components/web-client.source.json'
        self.record = {'schema': 1, 'kind': 'amplifier-web-client-source',
                       'source': {'repository': 'https://github.com/microsoft/amplifier-client-web',
                                  'revision': 'a' * 40}, 'inputsSha256': 'b' * 64,
                       'delivery': {'type': 'directory', 'path': 'components/web-client'}}
        self.lock = {'schema': 1, 'kind': 'unified-web-build-inputs',
                     'source': {'repository': 'https://github.com/microsoft/amplifier-unified',
                                'revision': 'c' * 40},
                     'inputs': {'catalog.json': {'sha256': hashlib.sha256(b'catalog').hexdigest(),
                                                'bytes': 7}}}
        write(self.selected, self.record)
        write(self.root / 'catalog.json', 'catalog')
        write(self.source / '.client-source.json', self.record)
        write(self.source / 'build-inputs.lock.json', self.lock)
        write(self.source / 'src/main.js', 'selected')
        write(self.source / 'tests/example.test.mjs', '// preserved test')
        write(self.source / '.client-development.json', build.delivery.development_inventory(self.source))
        self.commands = []

        def command(*args, **kwargs):
            self.commands.append(args)
            if str(args[1]).endswith('client-source.mjs'):
                if (Path(args[1]).parents[1] / 'src/main.js').read_text() != 'selected':
                    raise subprocess.CalledProcessError(1, args, stderr='source edited')
            if str(args[1]).endswith('prepare-inputs.mjs'):
                source = Path(args[1]).parents[1]
                shutil.copytree(Path(args[2]), source / '.build-inputs')
            if '--input-type=module' in args:
                if 'buildIdentity' in args[3]:
                    return subprocess.CompletedProcess(args, 0, json.dumps({'version': '0.2.0', 'id': 'client'}))
                prepared = Path(args[-1])
                self.assertEqual((prepared / 'catalog.json').read_text(), 'catalog')
            return subprocess.CompletedProcess(args, 0, '')

        mock = patch.object(build, 'command', side_effect=command)
        mock.start()
        self.addCleanup(mock.stop)

    def test_equal_selection_retains_prepared_source_and_uses_offline_inputs(self):
        before = (self.source / 'src/main.js').stat().st_mtime_ns
        build.prepare(self.root)
        build.prepare(self.root)
        self.assertEqual((self.source / 'src/main.js').stat().st_mtime_ns, before)
        self.assertEqual(sum(str(args[1]).endswith('prepare-inputs.mjs') for args in self.commands), 1)
        self.assertFalse(list((self.root / '.build').glob('.web-client-inputs-*')))

    def test_changed_shared_input_preserves_prior_output(self):
        build.prepare(self.root)
        write(self.root / 'catalog.json', 'changed')
        with self.assertRaisesRegex(ValueError, 'Shared client input changed'):
            build.prepare(self.root)
        self.assertEqual((self.source / '.build-inputs/catalog.json').read_text(), 'catalog')

    def test_edited_source_is_never_overwritten_even_after_selection_changes(self):
        write(self.source / 'src/main.js', 'experiment')
        self.record['source']['revision'] = 'd' * 40
        write(self.selected, self.record)
        with patch.object(build.delivery, 'prepare') as prepare:
            with self.assertRaises(subprocess.CalledProcessError):
                build.prepare(self.root)
            prepare.assert_not_called()
        self.assertEqual((self.source / 'src/main.js').read_text(), 'experiment')

    def test_clean_previous_selection_is_replaced_only_after_input_verification(self):
        self.record['source']['revision'] = 'd' * 40
        write(self.selected, self.record)

        def prepare(root, record, destination, **kwargs):
            shutil.copytree(self.source, destination)
            shutil.copyfile(record, destination / '.client-source.json')

        with patch.object(build.delivery, 'prepare', side_effect=prepare):
            build.prepare(self.root)
        self.assertEqual(build.read(self.source / '.client-source.json'), self.record)
        self.assertFalse(list((self.root / '.build').glob('.web-client-*')))

    def package(self):
        package = self.root / 'package'
        write(package / 'static/build.json', {'version': '0.2.0', 'id': 'client'})
        write(package / 'static/index.html', 'new client')
        component = {'schema': 1, 'kind': 'unified-web-static-artifact', 'source': self.record['source'],
                     'sharedInputs': {'source': self.lock['source'], 'lockSha256': hashlib.sha256(
                         (self.source / 'build-inputs.lock.json').read_bytes()).hexdigest()},
                     'pairedServer': {**self.lock['source'], 'transport': 'current-http-sse',
                                      'qualification': 'unqualified'},
                     'build': {'version': '0.2.0', 'id': 'client'},
                     'assets': build.inventory(package / 'static')}
        write(package / 'component.json', component)
        return package, component

    def test_artifact_verification_binds_assets_source_and_historical_peer(self):
        build.prepare(self.root)
        package, component = self.package()
        self.assertEqual(build.verify_artifact(self.root, self.source, component,
                                              package / 'static'), component)
        component['pairedServer']['revision'] = 'f' * 40
        with self.assertRaisesRegex(ValueError, 'provenance'):
            build.verify_artifact(self.root, self.source, component, package / 'static')

    def test_build_packages_selected_source_with_historical_peer_not_unrelated_sources(self):
        write(self.root / 'unrelated-source/main.js', 'unrelated developer experiment')
        (self.root / 'amplifier_web').mkdir()
        original = build.command
        package, component = self.package()
        calls = []

        def command(*args, **kwargs):
            calls.append(args)
            if args[0] == 'npm':
                self.assertEqual(args, ('npm', 'run', 'build', '--prefix', self.source))
                self.assertFalse((self.source / 'node_modules').exists())
                return subprocess.CompletedProcess(args, 0, '')
            if str(args[1]).endswith('package-client.mjs'):
                self.assertEqual(args[3], self.lock['source']['revision'])
                self.assertEqual(args[4:], ('--source-record', self.source / '.client-source.json'))
                shutil.copytree(package, Path(args[2]))
                return subprocess.CompletedProcess(args, 0, '')
            return original(*args, **kwargs)

        with patch.object(build, 'command', side_effect=command):
            self.assertEqual(build.build(self.root), component)
        self.assertEqual(build.verify(self.root), component)
        self.assertEqual((self.root / 'unrelated-source/main.js').read_text(), 'unrelated developer experiment')
        self.assertTrue(any(str(args[1]).endswith('package-client.mjs') for args in calls))

    def test_windows_resolves_npm_cmd_before_starting_build(self):
        # Stop at process handoff: a Linux test must not claim Windows execution.
        with patch.object(build.sys, 'platform', 'win32'), \
                patch.object(build.shutil, 'which', return_value='C:/Program Files/nodejs/npm.cmd') as which, \
                patch.object(build, 'prepare', return_value=self.source), \
                patch.object(build, 'command', side_effect=RuntimeError('handoff')) as command:
            with self.assertRaisesRegex(RuntimeError, 'handoff'):
                build.build(self.root)
        which.assert_called_once_with('npm')
        command.assert_called_once_with('C:/Program Files/nodejs/npm.cmd', 'run', 'build',
                                        '--prefix', self.source, timeout=300)

    def test_install_has_one_fixed_self_description_and_rejects_extra_assets(self):
        build.prepare(self.root)
        package, component = self.package()
        write(self.root / 'amplifier_web/static/index.html', 'previous')
        build.install(self.root, package)
        self.assertEqual(build.verify(self.root), component)
        write(self.root / 'amplifier_web/static/unrecorded.js', 'extra')
        with self.assertRaisesRegex(ValueError, 'assets'):
            build.verify(self.root)

    def test_failed_install_restores_previous_assets(self):
        package, _ = self.package()
        static = self.root / 'amplifier_web/static'
        write(static / 'index.html', 'previous')
        rename = Path.rename

        def fail(path, target):
            if path.name == 'ready':
                raise OSError('cannot install')
            return rename(path, target)

        with patch.object(Path, 'rename', fail):
            with self.assertRaisesRegex(OSError, 'cannot install'):
                build.install(self.root, package)
        self.assertEqual((static / 'index.html').read_text(), 'previous')

    def test_failed_rollback_retains_recovery_directory(self):
        package, _ = self.package()
        static = self.root / 'amplifier_web/static'
        write(static / 'index.html', 'previous')
        rename = Path.rename

        def fail(path, target):
            if path.name in {'ready', 'source'}:
                raise OSError('rename unavailable')
            return rename(path, target)

        with patch.object(Path, 'rename', fail):
            with self.assertRaisesRegex(ValueError, 'Restore previous client output') as error:
                build.install(self.root, package)
        previous = Path(str(error.exception).split(' from ', 1)[1])
        self.assertEqual((previous / 'index.html').read_text(), 'previous')


if __name__ == '__main__':
    unittest.main()
