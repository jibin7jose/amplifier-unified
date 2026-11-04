"""Synthetic Git preparation checks; no uv execution or remote/private fetches.

The command sequence follows uv 0.13.6 (329bf9448afd9f9088d14a4d57fbc61eb454f117),
crates/uv-git/src/git.rs: GitReference::BranchOrTag fetch and resolve. This proves
only the preparation boundary, not the cause of any historical CI failure.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('mirror_adapter', ROOT / 'scripts/qualify_omitted_ref.py')
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)


class MirrorPreparation(unittest.TestCase):
    def test_shallow_mirror_drops_named_ref_but_complete_history_keeps_pin(self):
        records = []
        facts = {'python': sys.version, 'commands': records, 'historicalCause': 'UNKNOWN',
                 'status': 'failed'}
        with tempfile.TemporaryDirectory(prefix='synthetic-mirror-') as directory:
            root = Path(directory)
            env = {'PATH': os.environ['PATH'], 'HOME': str(root), 'LANG': 'C',
                   'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
                   'GIT_ALLOW_PROTOCOL': 'file', 'GIT_TERMINAL_PROMPT': '0',
                   'GIT_AUTHOR_NAME': 'Synthetic', 'GIT_AUTHOR_EMAIL': 'fixture@example.invalid',
                   'GIT_COMMITTER_NAME': 'Synthetic', 'GIT_COMMITTER_EMAIL': 'fixture@example.invalid',
                   'GIT_AUTHOR_DATE': '2020-01-01T00:00:00+0000',
                   'GIT_COMMITTER_DATE': '2020-01-01T00:00:00+0000'}

            def git(cwd, *args, check=True):
                record = {'cwd': str(cwd), 'argv': ['git', *map(str, args)],
                          'returncode': None}
                records.append(record)
                try:
                    result = subprocess.run(record['argv'], cwd=cwd, env=env,
                                            capture_output=True, text=True, timeout=15)
                except (OSError, subprocess.TimeoutExpired) as error:
                    record['errorType'] = type(error).__name__
                    raise
                record.update(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
                if check:
                    self.assertEqual(result.returncode, 0, records[-1])
                return result

            try:
                facts['git'] = git(root, '--version').stdout.strip()
                source = root / 'source'
                git(root, 'init', '--initial-branch=main', source)
                for value in ('A', 'B'):
                    (source / 'payload').write_text(value)
                    git(source, 'add', 'payload')
                    git(source, 'commit', '-m', value)
                    facts[value] = git(source, 'rev-parse', 'HEAD').stdout.strip()
                self.assertNotEqual(facts['A'], facts['B'])
                self.assertEqual(git(source, 'rev-list', '--count', 'HEAD').stdout.strip(), '2')
                for kind, clone_options in [('shallow', ['--depth=1']), ('complete', [])]:
                    checkout, mirror, database = (root / (kind + suffix)
                                                  for suffix in ('-checkout', '-mirror.git', '-database'))
                    git(root, 'clone', *clone_options, '--branch', 'main', source.as_uri(), checkout)
                    git(checkout, 'checkout', '--detach', facts['B'])
                    # Same mirror construction as the adapter, including local path transport.
                    git(root, 'clone', '--bare', '--no-hardlinks', checkout, mirror)
                    git(root, '-C', mirror, 'update-ref', 'refs/heads/main', facts['B'])
                    expected_shallow = 'true' if kind == 'shallow' else 'false'
                    states = {name: git(root, '-C', path, 'rev-parse', '--is-shallow-repository').stdout.strip()
                              for name, path in [('checkout', checkout), ('mirror', mirror)]}
                    self.assertEqual(states, {'checkout': expected_shallow, 'mirror': expected_shallow})
                    with patch.dict(os.environ, env, clear=True):
                        if kind == 'shallow':
                            for path in (checkout, mirror):
                                with self.assertRaisesRegex(RuntimeError, 'must have complete history'):
                                    adapter.verified_git_source(path, facts['B'])
                        else:
                            self.assertEqual(adapter.verified_git_source(checkout, facts['B']),
                                             {'shallow': False, 'commit': facts['B']})
                            self.assertEqual(adapter.verified_git_source(mirror, facts['B'], 'refs/heads/main'),
                                             {'shallow': False, 'commit': facts['B']})
                            with self.assertRaisesRegex(RuntimeError, 'pin mismatch'):
                                adapter.verified_git_source(mirror, facts['A'], 'refs/heads/main')
                    # uv initializes a normal database, not a bare repository. It tries
                    # the tag fetch only if the branch fetch fails, then resolves separately.
                    database.mkdir()
                    git(database, 'init')
                    fetched = git(database, 'fetch', '--force', '--update-head-ok', mirror.as_uri(),
                                  '+refs/heads/main:refs/remotes/origin/main')
                    branch = git(database, 'rev-parse', 'origin/main^0', check=False)
                    tag = (git(database, 'rev-parse', 'refs/remotes/origin/tags/main^0', check=False)
                           if branch.returncode else None)
                    facts[kind] = {'states': states, 'fetchExit': fetched.returncode,
                                   'branchExit': branch.returncode, 'branch': branch.stdout.strip(),
                                   'tagExit': tag.returncode if tag else None}
                    if kind == 'shallow':
                        self.assertNotEqual(branch.returncode, 0)
                        self.assertNotEqual(tag.returncode, 0)
                    else:
                        self.assertEqual(branch.returncode, 0)
                        self.assertEqual(branch.stdout.strip(), facts['B'])
                        self.assertEqual(git(database, 'rev-list', '--count', 'origin/main').stdout.strip(), '2')
                facts['status'] = 'passed'
            finally:
                # Raw commands refer only to temporary synthetic data. Preserve them
                # even if a counterexample does not behave as predicted.
                output = os.environ.get('OMITTED_REF_MIRROR_EVIDENCE')
                if output:
                    Path(output).write_text(json.dumps(facts, indent=2) + '\n')

    def test_shallow_guard_refuses_unknown_status_and_changed_pin(self):
        for shallow, revision, message in [('true', 'a' * 40, 'complete history'),
                                           ('unknown', 'a' * 40, 'complete history'),
                                           ('false', 'b' * 40, 'pin mismatch')]:
            with self.subTest(shallow=shallow, revision=revision), \
                 patch.object(adapter.subprocess, 'check_output', side_effect=[shallow, revision]):
                with self.assertRaisesRegex(RuntimeError, message):
                    adapter.verified_git_source(Path('/synthetic/repo'), 'a' * 40)

    def test_seed_environment_keeps_canonical_source_and_scopes_fast_path_disable(self):
        inherited = {'PATH': '/synthetic/bin', 'UV_NO_GITHUB_FAST_PATH': '0',
                     'PYTHONPATH': '/unrelated', 'UV_PROJECT_ENVIRONMENT': '/unrelated',
                     'UV_OFFLINE': '1', 'UV_NO_INDEX': '1', 'KEEP': 'value'}
        with patch.dict(os.environ, inherited, clear=True):
            result = adapter.mirror_seed_environment(Path('/synthetic/cache'), Path('/synthetic/mirror.git'))
            self.assertEqual(dict(os.environ), inherited)
        self.assertEqual(result['UV_NO_GITHUB_FAST_PATH'], '1')
        self.assertEqual(result['GIT_CONFIG_KEY_0'], 'url.file:///synthetic/mirror.git.insteadOf')
        self.assertEqual(result['GIT_CONFIG_VALUE_0'], 'https://github.com/microsoft/' + adapter.LOOP)
        self.assertEqual(result['GIT_CONFIG_COUNT'], '1')
        self.assertEqual(result['KEEP'], 'value')
        for name in ('PYTHONPATH', 'UV_PROJECT_ENVIRONMENT', 'UV_OFFLINE', 'UV_NO_INDEX'):
            self.assertNotIn(name, result)
        raw = (ROOT / 'amplifier_web/runtime_deps/pyproject.toml').read_text()
        declaration = adapter.tomllib.loads(raw)['tool']['uv']['sources'][adapter.LOOP]
        self.assertEqual(declaration, {'git': 'https://github.com/microsoft/' + adapter.LOOP, 'rev': 'main'})

    def test_private_checkout_requests_complete_history_without_persisted_credentials(self):
        workflow = yaml.load((ROOT / '.github/workflows/updater-omitted-ref.yml').read_text(),
                             Loader=yaml.BaseLoader)
        checkout, = [step for step in workflow['jobs']['qualify']['steps']
                     if step.get('with', {}).get('repository') == 'microsoft/' + adapter.LOOP]
        self.assertEqual(checkout['with']['fetch-depth'], '0')
        self.assertEqual(checkout['with']['ref'], '${{ steps.pin.outputs.revision }}')
        self.assertEqual(checkout['with']['persist-credentials'], 'false')


if __name__ == '__main__':
    unittest.main()
