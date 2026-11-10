"""The source selector must not hide new owners or discard evidence boundaries.

Standalone stdlib/Git tooling tests; no application imports or runtime fixtures.
Run with: python -m unittest discover -s tests -p test_source_context.py
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/source_context.py'
spec = importlib.util.spec_from_file_location('source_context', SCRIPT)
selector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(selector)


class SourceContextTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='unified-source-context-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'source with spaces'
        self.root.mkdir()
        self.git('init', '-q')
        self.git('config', 'core.attributesFile', os.devnull)
        (self.root / '.gitattributes').write_bytes((ROOT / '.gitattributes').read_bytes())
        self.git('add', '.gitattributes')

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], stderr=subprocess.PIPE)

    def add(self, *names):
        for name in names:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('synthetic fixture\n')
        self.git('add', '--', *names)

    def cli(self, *args):
        import sys
        return subprocess.run([sys.executable, str(SCRIPT), '--root', str(self.root), *args],
                              capture_output=True, check=False)

    def test_authored_inputs_and_maintained_ledgers_are_not_generated(self):
        expected = {
            'amplifier_web/browser_entry.py': 'source',
            'amplifier_web/host/storage.py': 'source',
            'docs/shell/appearance-contract.md': 'documentation',
            'contracts/client-state.v1.md': 'contracts',
            'skills/example/SKILL.md': 'guidance',
            'tests/web/standard-ux-inventory-deltas.json': 'tests',
            'tests/web/standard-ux-completion-policy.json': 'tests',
            'tests/web/fixture-lifecycle.mjs': 'tests',
            'tests/web/package.json': 'configuration',
            'docs/validation/performance.md': 'documentation',
            'docs/validation/current-followup-20261015.md': 'documentation',
            'tests/fixtures/library-performance.md': 'documentation',
            'tests/fixtures/canonical_model_catalog_candidate.json': 'tests',
            'tests/fixtures/model_catalog_accepted.json': 'tests',
            'tests/fixtures/catalog_candidate_transports.py': 'tests',
            'amplifier_web/device_client/extension/observed-dom.js': 'source',
            'docs/workspaces/evidence/current-followup.json': 'documentation',
        }
        self.add(*expected)
        rows = {row['path']: row for row in selector.inventory(self.root)}
        for path, kind in expected.items():
            self.assertEqual(rows[path]['kind'], kind, path)
        result = self.cli('--format', 'json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(set(expected) <= {row['path'] for row in json.loads(result.stdout)})

    def test_generated_evidence_and_locks_are_opt_in_but_summary_accounts_for_them(self):
        names = ['amplifier_web/static/app.js', 'tests/web/standard-ux-controls.json',
                 'tests/web/standard-ux-inventory.json', 'tests/web/package-lock.json',
                 'assets/branding/icon.png', 'uv.lock',
                 'tests/fixtures/model_catalog_candidates.json',
                 'docs/workspaces/evidence/collaboration-fable-assets.json',
                 'docs/workspaces/evidence/collaboration-fable-results.json',
                 'docs/workspaces/evidence/collaboration-fable-runtime.json',
                 'docs/workspaces/evidence/collaboration-gaps-assets.json',
                 'docs/workspaces/evidence/collaboration-gaps-results.json']
        self.add(*names)
        default = json.loads(self.cli('--format', 'json').stdout)
        self.assertTrue(set(names).isdisjoint(row['path'] for row in default))
        complete = json.loads(self.cli('--format', 'json', '--all').stdout)
        self.assertTrue(set(names) <= {row['path'] for row in complete})
        summary = self.cli().stdout.decode()
        for kind in ('generated', 'evidence', 'assets', 'locks'):
            self.assertIn(kind, summary)
        self.assertIn('opt in', summary)
        for name in names:
            self.assertTrue((self.root / name).is_file())

    def test_copied_client_is_opt_in_but_its_selection_and_owner_stay_visible(self):
        guides = ['components/README.md', 'components/AGENTS.md', 'components/web-client.source.json']
        copied = ['components/web-client/README.md', 'components/web-client/package.json',
                  'components/web-client/src/app.js', 'components/web-client/public/pwa.js',
                  'components/web-client/tests/app.test.mjs']
        self.add(*guides, *copied)
        visible = {row['path'] for row in json.loads(self.cli('--format', 'json').stdout)}
        self.assertTrue(set(guides) <= visible)
        self.assertTrue(set(copied).isdisjoint(visible))
        selected = json.loads(self.cli('--format', 'json', '--all', '--under', 'components/web-client').stdout)
        self.assertEqual({row['path'] for row in selected}, set(copied))
        self.assertTrue(all(row['kind'] == 'generated' for row in selected))

    def test_unclassified_invalid_and_unset_attributes_remain_visible(self):
        self.add('future/owner.py', 'tests/web/invalid.mjs', 'tests/web/unset.mjs', 'contracts/current.md')
        with (self.root / '.gitattributes').open('a') as handle:
            handle.write('\n/tests/web/invalid.mjs source-context=typo\n/tests/web/unset.mjs !source-context\n')
        rows = json.loads(self.cli('--format', 'json').stdout)
        unknown = {row['path'] for row in rows if row['kind'] == 'unclassified'}
        self.assertEqual(unknown, {'future/owner.py', 'tests/web/invalid.mjs', 'tests/web/unset.mjs'})
        result = self.cli('--check', '--format', 'json', '--kind', 'contracts', '--under', 'contracts')
        self.assertEqual(result.returncode, 1)
        self.assertIn(b'3 unclassified', result.stderr)
        self.assertEqual([row['path'] for row in json.loads(result.stdout)], ['contracts/current.md'])

    def test_index_membership_unusual_names_and_exact_directory_prefixes(self):
        names = ['amplifier_web/host/a space.js', 'amplifier_web/host/caf\u00e9.js', 'amplifier_web/host-other/keep.js']
        self.add(*names)
        (self.root / 'amplifier_web/host/private-untracked.js').write_text('not collected')
        result = self.cli('--format', 'paths', '--null', '--under', 'amplifier_web/host/')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(result.stdout.split(b'\0')) - {b''}, {os.fsencode(n) for n in names[:-1]})
        rows = json.loads(self.cli('--format', 'json', '--under', names[0]).stdout)
        self.assertEqual([row['path'] for row in rows], [names[0]])

    @unittest.skipIf(os.name == 'nt', 'Windows forbids newline and quote filenames')
    def test_posix_filenames_round_trip_without_line_or_quote_parsing(self):
        names = ['amplifier_web/host/a\nline.js', 'amplifier_web/host/quoted\".js']
        self.add(*names)
        result = self.cli('--format', 'paths', '--null', '--under', 'amplifier_web/host')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(result.stdout.split(b'\0')) - {b''}, {os.fsencode(n) for n in names})
        rows = json.loads(self.cli('--format', 'json', '--under', 'amplifier_web/host').stdout)
        self.assertEqual({row['path'] for row in rows}, set(names))

    def test_missing_files_and_links_are_metadata_not_silently_dropped(self):
        self.add('tests/web/missing.mjs')
        (self.root / 'tests/web/missing.mjs').unlink()
        target = self.root.parent / 'outside.txt'
        target.write_text('outside the checkout')
        try:
            (self.root / 'tests/web/link.mjs').symlink_to(target)
        except OSError as error:
            self.skipTest(f'Cannot create symlinks: {error}')
        self.git('add', 'tests/web/link.mjs')
        rows = {row['path']: row for row in selector.inventory(self.root)}
        self.assertEqual(rows['tests/web/missing.mjs']['status'], 'missing')
        self.assertIsNone(rows['tests/web/missing.mjs']['bytes'])
        self.assertEqual(rows['tests/web/link.mjs']['status'], 'symlink')
        self.assertIsNone(rows['tests/web/link.mjs']['bytes'])

    def test_local_override_is_effective_and_visible(self):
        self.add('tests/web/local.mjs')
        (self.root / '.git/info/attributes').write_text('/tests/web/local.mjs source-context=unclassified\n')
        result = self.cli('--check', '--format', 'json')
        self.assertEqual(result.returncode, 1)
        rows = {row['path']: row for row in json.loads(result.stdout)}
        self.assertEqual(rows['tests/web/local.mjs']['kind'], 'unclassified')

    def test_empty_inventory_and_invalid_selection(self):
        self.git('rm', '--cached', '.gitattributes')
        self.assertEqual(selector.inventory(self.root), [])
        self.assertEqual(json.loads(self.cli('--format', 'json').stdout), [])
        for args in [('--under', '../outside'), ('--under', '/absolute'), ('--null',),
                     ('--all', '--kind', 'source')]:
            self.assertEqual(self.cli(*args).returncode, 2, args)


if __name__ == '__main__':
    unittest.main()
