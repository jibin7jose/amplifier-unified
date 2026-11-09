"""Cheap authoring checks; never acquire or run the hosted public graph locally."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('public_adapter', ROOT / 'scripts/qualify_public_selected_source.py')
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class PublicSelectedHarness(unittest.TestCase):
    def test_matching_source_sha_restricts_branch_to_retention_in_both_orders(self):
        from amplifier_web.selected_runtime_dependencies import selected
        from amplifier_web.runtime_source_alignment import verify_checked_selection
        from amplifier_web.runtime_policy_reconciliation import selected_policies
        from amplifier_web.runtime_environment import ProtectedRuntimeSource
        url, pin = 'https://example.invalid/dependency', 'a'*40
        root = ('[project]\nname="routing"\ndependencies=["dependency @ git+' + url + '@main"]\n').encode()
        restriction = ('[project]\nname="skills"\ndependencies=[]\n[tool.uv.sources]\n'
                       'dependency={git="' + url + '",rev="' + pin + '"}\n').encode()
        installed = SimpleNamespace(metadata={'Name': 'dependency'}, version='1', requires=[],
            read_text=lambda _: json.dumps({'url': url, 'vcs_info': {'vcs': 'git', 'commit_id': pin}}))
        results = []
        for order in ([('routing', root), ('skills', restriction)], [('skills', restriction), ('routing', root)]):
            with self.subTest(order=[name for name, _ in order]):
                packages = selected(dict(order), distributions=[installed])
                source = packages['dependency']
                self.assertEqual(source['status'], 'explicit-pin')
                self.assertEqual(source['ref'], pin)
                self.assertEqual(source['revision'], pin)
                self.assertTrue(source['manifestSha256'])
                report = {'packages': packages, 'batchPaths': {'routing': '/selected/root'}}
                checked = {'dependency': {'url': url, 'ref': 'main', 'revision': pin}}
                verify_checked_selection(checked, report, exact=True)
                checked['dependency']['revision'] = 'b'*40
                with self.assertRaises(ProtectedRuntimeSource):
                    verify_checked_selection(checked, report)
                recovered, _ = selected_policies(
                    {'dependency': {'url': url, 'subdirectory': '', 'current': pin}}, report)
                self.assertEqual(recovered, {})
                results.append(source)
        self.assertEqual(len(results), 2)
        if len(results) == 2:
            self.assertEqual(results[0], results[1])
        for change in ('tag', 'abbreviated', 'fork', 'subdirectory', 'conditional', 'different-sha'):
            with self.subTest(protected=change):
                altered = restriction.decode()
                if change == 'tag':
                    altered = altered.replace('rev=', 'tag=')
                elif change == 'abbreviated':
                    altered = altered.replace(pin, pin[:12])
                elif change == 'fork':
                    altered = altered.replace(url, url + '-fork')
                elif change == 'subdirectory':
                    altered = altered.replace('rev=', 'subdirectory="other",rev=')
                elif change == 'conditional':
                    altered = altered.replace('rev=', 'marker="python_version < \'1\'",rev=')
                else:
                    altered = altered.replace(pin, 'b'*40)
                packages = selected({'routing': root, 'skills': altered.encode()}, distributions=[installed])
                self.assertEqual(packages['dependency']['status'], 'selected-dependency-unresolved')

    def test_unselected_compatible_source_restriction_does_not_hide_selected_dependency(self):
        from amplifier_web.selected_runtime_dependencies import selected
        url = 'https://example.invalid/dependency'
        requirement = '[project]\nname="routing"\ndependencies=["dependency @ git+' + url + '@main"]\n'
        installed = SimpleNamespace(metadata={'Name': 'dependency'}, version='1', requires=[],
            read_text=lambda _: json.dumps({'url': url, 'vcs_info': {'vcs': 'git', 'commit_id': 'a'*40}}))
        for kind, ref, status in [('branch', 'main', 'selected'), ('rev', 'main', 'selected'),
                                  ('tag', 'main', 'selected-dependency-unresolved'),
                                  ('rev', 'b'*40, 'selected-dependency-unresolved')]:
            with self.subTest(kind=kind, ref=ref):
                other = ('[project]\nname="skills"\ndependencies=[]\n[tool.uv.sources]\n'
                         'dependency={git="' + url + '",' + kind + '="' + ref + '"}\n')
                result = selected({'routing': requirement.encode(), 'skills': other.encode()},
                                  distributions=[installed])
                self.assertEqual(result['dependency']['status'], status)
                self.assertEqual(selected({'skills': other.encode()}, distributions=[installed]), {})

    def test_cleanup_handles_readonly_sources_without_following_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            outer = Path(directory)
            work = outer / 'work'; work.mkdir()
            protected = work / 'immutable'; protected.mkdir()
            (protected / 'file').write_text('owned')
            unrelated = outer / 'unrelated'; unrelated.mkdir()
            (unrelated / 'keep').write_text('keep')
            (work / 'alias').symlink_to(unrelated, target_is_directory=True)
            protected.chmod(0o555)
            adapter.remove_owned_work(work)
            self.assertFalse(work.exists())
            self.assertEqual((unrelated / 'keep').read_text(), 'keep')

    def test_environment_drops_accounts_and_preserves_only_exact_loop_transport(self):
        with patch.dict(os.environ, {'PATH': '/bin', 'ANTHROPIC_API_KEY': 'do-not-forward',
                                    'GITHUB_TOKEN': 'do-not-forward', 'SSH_AUTH_SOCK': '/private'}, clear=True):
            value = adapter.clean_environment(Path('/owned/work'), Path('/owned/loop'))
        self.assertNotIn('ANTHROPIC_API_KEY', value)
        self.assertNotIn('GITHUB_TOKEN', value)
        self.assertNotIn('SSH_AUTH_SOCK', value)
        self.assertEqual(value['GIT_CONFIG_COUNT'], '1')
        self.assertEqual(value['GIT_CONFIG_VALUE_0'], 'https://github.com/microsoft/amplifier-module-loop-live')
        self.assertEqual(value['UV_NO_GITHUB_FAST_PATH'], '1')
        self.assertNotIn('UV_OFFLINE', value)
        self.assertNotIn('UV_FIND_LINKS', value)

    def test_junit_retains_outcomes_without_private_failure_text(self):
        with tempfile.TemporaryDirectory() as directory:
            raw, safe = Path(directory) / 'raw.xml', Path(directory) / 'safe.xml'
            raw.write_text('<testsuites><testsuite name="fixed" tests="2" failures="1" errors="0" skipped="0">'
                           '<testcase name="a" time="1"><failure message="private">private source</failure></testcase>'
                           '<testcase name="b" time="2"/></testsuite></testsuites>')
            adapter.bounded_junit(raw, safe)
            content = safe.read_text()
            self.assertNotIn('private source', content)
            root = ET.fromstring(content)
            self.assertEqual(root.find('testsuite').get('failures'), '1')
            self.assertEqual(len(root.findall('.//testcase')), 2)

    def test_lane_is_only_explicit_manual_and_reuses_existing_read_only_job(self):
        dispatch = yaml.load((ROOT / '.github/workflows/python-checks.yml').read_text(), Loader=yaml.BaseLoader)
        job = dispatch['jobs']['updater-public-selected-source']
        self.assertIn("github.event_name == 'workflow_dispatch'", job['if'])
        self.assertIn("inputs.suite == 'updater-public-selected-source'", job['if'])
        self.assertIn("inputs.suite != 'updater-public-selected-source'", dispatch['jobs']['python']['if'])
        self.assertEqual(job['uses'], './.github/workflows/updater-omitted-ref.yml')
        workflow = yaml.load((ROOT / '.github/workflows/updater-omitted-ref.yml').read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(workflow['permissions'], {'contents': 'read'})
        self.assertEqual(workflow['on']['workflow_call']['inputs']['public_selected_source']['default'], 'false')
        for path in (ROOT / '.github/workflows').glob('*.yml'):
            if path.name not in {'python-checks.yml', 'updater-omitted-ref.yml'}:
                self.assertNotIn('updater-public-selected-source', path.read_text())

    def test_fixture_has_no_production_module_or_inventory_replacement(self):
        path = ROOT / 'tests/fixtures/public_selected_source_probe.py'
        source = path.read_text(); ast.parse(source)
        for forbidden in ('fixture_core(', 'fixture_provider(', 'repack_registry_wheels(',
                          "'manifest_path',", "'validate',", "'inventory_sources',", 'manager.inventory ='):
            self.assertNotIn(forbidden, source)
        self.assertIn('runtime_environment.manifest_path().read_bytes()', source)
        self.assertIn('await manager.check(fresh=True)', source)
        self.assertIn('await manager.install()', source)
        self.assertIn('verify_recorded(final, stage)', source)
        self.assertIn('await manager.rollback()', source)
        self.assertIn('http://127.0.0.1:9', source)


class PinnedCollectorBoundary(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_forwards_owner_and_preserves_refused_or_recorded_state(self):
        from amplifier_web import deployment, runtime_environment as runtime
        from amplifier_web.selected_runtime_dependencies import refresh
        from unittest.mock import AsyncMock
        from urllib.parse import urlsplit
        for boundary in ('forward', 'refuse', 'recorded'):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as directory:
                home = Path(directory) / 'app'
                stage = home / 'updates/releases' / ('d' * 32)
                stage.mkdir(parents=True)
                project = home / 'runtime/candidate'; project.mkdir(parents=True)
                url, old, new = 'https://example.invalid/dependency', 'a'*40, 'b'*40
                path = project / 'pyproject.toml'
                path.write_text('[project]\nname="worker"\nversion="1"\n'
                    '[tool.uv.sources]\ndependency={git="' + url + '",rev="' + old + '"}\n')
                pointer = home / 'updates/active.json'
                pointer.write_text('{"current":"previous"}')
                before, active = path.read_bytes(), pointer.read_bytes()
                if boundary == 'recorded':
                    (stage / 'runtime.lock').write_text('retained-lock')
                writes = []
                original = deployment.write_private
                def private(target, text, *, owned_root=None):
                    self.assertEqual(owned_root, home)
                    writes.append(target)
                    if boundary == 'refuse':
                        raise PermissionError('private boundary refused')
                    original(target, text, owned_root=owned_root)
                process = AsyncMock(return_value='')
                with patch.object(deployment, 'write_private', private), patch(
                        'amplifier_web.updates.process', process), patch('shutil.which', return_value='/fixed/uv'), patch.object(
                        runtime, 'locked_sources', return_value={'dependency': urlsplit(url + '?rev=' + new + '#' + new)}):
                    args = (SimpleNamespace(home=home), stage, project, {'dependency': {
                        'sourceKind': 'dependency', 'url': url, 'ref': 'main', 'subdirectory': '',
                        'previousRevision': old, 'revision': new}})
                    if boundary == 'forward':
                        await refresh(*args)
                        self.assertIn(new, path.read_text())
                        self.assertEqual(process.await_count, 2)
                    else:
                        with self.assertRaises(PermissionError if boundary == 'refuse' else ValueError):
                            await refresh(*args)
                        self.assertEqual(path.read_bytes(), before)
                        process.assert_not_awaited()
                self.assertEqual(writes, [] if boundary == 'recorded' else [path])
                self.assertEqual(pointer.read_bytes(), active)
                if boundary == 'recorded':
                    self.assertEqual((stage / 'runtime.lock').read_text(), 'retained-lock')

    async def test_verified_pinned_root_keeps_its_dependency_constraint(self):
        import subprocess
        from amplifier_web.runtime_policy_reconciliation import Collector
        from amplifier_web.runtime_source_alignment import verify_checked_selection
        from amplifier_web.runtime_environment import ProtectedRuntimeSource
        from amplifier_web.host.config import HostConfig
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory) / 'candidate'
            root = stage / 'foundation/cache/root'
            root.mkdir(parents=True)
            url, child = 'https://example.invalid/root', 'https://example.invalid/dependency'
            (root / 'pyproject.toml').write_text('[project]\nname="root"\nversion="1"\n'
                'dependencies=["dependency @ git+' + child + '@' + 'a'*40 + '"]\n')
            def git(*args):
                return subprocess.check_output(['git', *args], cwd=root, text=True,
                                               stderr=subprocess.DEVNULL).strip()
            git('init', '-b', 'main')
            git('config', 'user.name', 'Fixture'); git('config', 'user.email', 'fixture@example.invalid')
            git('remote', 'add', 'origin', url); git('add', '.'); git('commit', '-m', 'Pinned declaration')
            pin = git('rev-parse', 'HEAD')
            (root / '.amplifier_cache_meta.json').write_text(json.dumps(
                {'git_url': url, 'ref': pin, 'commit': pin}))
            config = HostConfig(stage, stage, {}, stage / 'foundation')
            collector = Collector(); collector.observe('git+' + url + '@' + pin, config)
            installed = SimpleNamespace(metadata={'Name': 'dependency'}, version='1', requires=[],
                read_text=lambda _: json.dumps({'url': child, 'vcs_info': {'vcs': 'git', 'commit_id': 'b'*40}}))
            with patch('importlib.metadata.distributions', return_value=[installed]), patch.dict(
                    os.environ, {'AMPLIFIER_SOURCE_STORE': ''}):
                report = await collector.report(SimpleNamespace(names={'root': root}), config)
            self.assertEqual(report['packages']['root']['status'], 'explicit-pin')
            self.assertEqual(report['packages']['dependency']['status'], 'selected-dependency-unresolved')
            with self.assertRaises(ProtectedRuntimeSource):
                verify_checked_selection({'dependency': {'url': child, 'ref': 'main',
                                          'revision': 'b'*40}}, report, exact=True)


if __name__ == '__main__':
    unittest.main()
