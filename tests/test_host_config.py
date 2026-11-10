"""Standalone settings migration and loop policy contracts."""
import copy
import json
import os
from pathlib import Path
from contextlib import contextmanager
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from amplifier_web.builtin_behaviors import APPENDED_DEFAULTS
from amplifier_web.host.config import _KEY_FILE_VALUES, _load_keys, app_home, load_config, prepare_registry, merge, expand_environment, HostConfig, WORK_SOURCE, PRECONFIGURED_BUNDLES
from amplifier_web.host.session import live_plan, repair_interrupted_receipts, redact, _apply_settings
from amplifier_web.shared_state import configuration_paths


@contextmanager
def isolated_read_config_collaborators(workspace, shared):
    """Keep the chain under test away from release files, SQLite and settings."""
    with patch("amplifier_web.host.config.read_settings", return_value={}) as settings, \
            patch("amplifier_web.updates.foundation_home",
                  side_effect=AssertionError("explicit registry_home must avoid release reads")), \
            patch("amplifier_web.access_mode.read_mode", return_value="ask") as access:
        yield settings, access
        settings.assert_called_once_with(
            workspace.resolve(), shared_home=shared.resolve(), session_id=None,
            global_only=False, cache=None)


class HostSettingsTests(unittest.TestCase):
    def test_windows_runtime_writer_binds_app_home_not_destination_parent(self):
        from amplifier_web.host import config
        root, path = Path('app'), Path('outside/report.json')
        with patch.object(config, 'os', SimpleNamespace(name='nt')), \
                patch.object(config, 'app_home', return_value=root), \
                patch.object(config, '_write_private') as write:
            config.write_private(path, 'private')
            write.assert_called_once_with(path, 'private', owned_root=root)

    def test_windows_registry_creation_uses_private_boundary_before_mkdir(self):
        from amplifier_web.host import config
        from amplifier_web import windows_files
        root = Path('app')
        target = root / 'foundation'
        seen = []
        @contextmanager
        def directory(path, **kwargs):
            seen.append((path, kwargs))
            yield
        with patch.object(config, 'os', SimpleNamespace(name='nt')), \
                patch.object(windows_files, 'directory', directory), \
                patch.object(Path, 'mkdir', side_effect=AssertionError('ordinary mkdir')):
            config.prepare_registry(SimpleNamespace(home=root, registry_home=target))
        self.assertEqual(seen, [(target, {'owned_root': root, 'create': True})])

    def test_work_is_available_without_changing_default_or_overriding_user_sources(self):
        config = HostConfig(Path('/app'), Path('/workspace'), {}, Path('/registry'))
        self.assertEqual(config.active_bundle, 'work')
        self.assertEqual(config.registrations['work'], WORK_SOURCE)
        config.settings['bundle'] = {'added': {'work': 'file:///custom/work.md'}}
        self.assertEqual(config.registrations['work'], 'file:///custom/work.md')
        config.settings['sources'] = {'bundles': {'work': 'file:///override/work.md'}}
        self.assertEqual(config.registrations['work'], 'file:///override/work.md')

    def test_four_default_roots_preserve_selected_bundle_and_user_bindings(self):
        expected = {'anchors', 'anchors-amp-dev', 'work', 'work-amp-dev'}
        self.assertEqual(set(PRECONFIGURED_BUNDLES), expected)
        for name in expected:
            with self.subTest(name=name):
                settings = {'bundle': {'active': 'chosen-root'}}
                config = HostConfig(Path('/app'), Path('/workspace'), settings, Path('/registry'))
                self.assertEqual(config.registrations[name], PRECONFIGURED_BUNDLES[name])
                settings['bundle']['added'] = {name: 'file:///custom/root.md'}
                self.assertEqual(config.registrations[name], 'file:///custom/root.md')
                settings['sources'] = {'bundles': {name: 'file:///override/root.md'}}
                self.assertEqual(config.registrations[name], 'file:///override/root.md')
                self.assertEqual(config.active_bundle, 'chosen-root')

    def test_app_home_honors_the_data_directory_override(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
                os.environ, {"AMPLIFIER_WEB_DATA_DIR": directory}, clear=True):
            self.assertEqual(app_home(), Path(directory).resolve())

    @unittest.skipIf(os.name == "nt", "portable symlink/injected-Windows path; native junction case below")
    def test_windows_read_config_retains_original_home_for_private_io(self):
        from amplifier_web import deployment, windows_files
        from amplifier_web.host import config as host_config
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            unrelated = root / "unrelated"
            (unrelated / "config").mkdir(parents=True)
            sentinel = unrelated / "config/server.yaml"
            original = b"port: 9321\nextension: unrelated\n"
            sentinel.write_bytes(original)
            alias = root / "app-home"
            alias.symlink_to(unrelated, target_is_directory=True)
            workspace = root / "workspace"
            workspace.mkdir()
            shared = root / "shared"
            shared.mkdir()

            # Injection models the native pinned boundary's link refusal. The
            # native test below uses the real NTFS helper and a real junction.
            @contextmanager
            def pinned(path, *, owned_root, create=False):
                if owned_root.is_symlink():
                    raise PermissionError("original home is a reparse point")

                class Pinned:
                    def publish_private(self, name, content, *, replace=False):
                        (path / name).write_bytes(content)
                yield Pinned()

            with patch.object(host_config, "os", SimpleNamespace(
                    name="nt", environ={"AMPLIFIER_WEB_HOME": str(alias)})), \
                    patch.object(deployment, "_WINDOWS", True), \
                    patch.object(windows_files, "directory", pinned):
                for explicit_home in (None, alias):
                    with self.subTest(explicit_home=explicit_home), \
                            isolated_read_config_collaborators(workspace, shared) as (_, access):
                        config = host_config.read_config(
                            workspace, home=explicit_home, shared_home=shared,
                            registry_home=root / "registry")
                        with self.assertRaisesRegex(PermissionError, "reparse"):
                            deployment.write_private(
                                config.home / "config/server.yaml", b"must-not-replace",
                                owned_root=config.home)
                        self.assertEqual(config.home, alias)
                        self.assertEqual(access.call_args.args[0], alias)
                        self.assertEqual(sentinel.read_bytes(), original)

    @unittest.skipUnless(os.name == "posix", "POSIX home canonicalization")
    def test_posix_read_config_still_resolves_home(self):
        from amplifier_web.host import config as host_config
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / "target"
            target.mkdir()
            alias = root / "alias"
            alias.symlink_to(target, target_is_directory=True)
            with isolated_read_config_collaborators(root, root) as (_, access):
                config = host_config.read_config(
                    root, home=alias, shared_home=root, registry_home=root / "registry")
                self.assertEqual(config.home, target)
                self.assertEqual(access.call_args.args[0], target)

    def test_configuration_invalidation_tracks_shared_inputs_without_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            legacy = root / "legacy"
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / ".amplifier").mkdir()

            load_config(workspace, home=home, legacy_home=legacy)

            legacy_snapshots = home / "config" / "workspaces"
            self.assertFalse(legacy_snapshots.exists())
            paths = configuration_paths(workspace, "session-id", home)
            self.assertIn(Path(os.environ["AMPLIFIER_HOME"]) / "settings.yaml", paths)
            self.assertIn(workspace.resolve() / ".amplifier" / "settings.local.yaml", paths)
            self.assertFalse(any(legacy_snapshots in path.parents for path in paths))

    def test_configuration_inputs_are_stable_through_symlink_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            workspace=root/'workspace';workspace.mkdir()
            alias=root/'alias';alias.symlink_to(workspace,target_is_directory=True)
            home=root/'home';home.mkdir()
            home_alias=root/'home-alias';home_alias.symlink_to(home,target_is_directory=True)
            self.assertEqual(
                [path.resolve() for path in configuration_paths(alias, 'session-id', home_alias)],
                [path.resolve() for path in configuration_paths(workspace, 'session-id', home)])

    def test_keys_file_refreshes_a_value_it_previously_loaded(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            path = Path(directory) / "keys.env"
            path.write_text("AMPLIFIER_WEB_REFRESH_TEST=first\n")
            _KEY_FILE_VALUES.clear()
            _load_keys(path)
            path.write_text("AMPLIFIER_WEB_REFRESH_TEST=second\n")
            _load_keys(path)
            self.assertEqual(os.environ["AMPLIFIER_WEB_REFRESH_TEST"], "second")
            _KEY_FILE_VALUES.clear()

    def test_shared_settings_reload_while_runtime_cache_remains_owned(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);legacy=root/'legacy';home=root/'owned';workspace=root/'workspace'
            legacy.mkdir();workspace.mkdir();(workspace/'.amplifier').mkdir()
            (legacy/'settings.yaml').write_text(yaml.safe_dump({'bundle':{'active':'custom'},'config':{'providers':[{'module':'provider-test','id':'first','config':{'model':'a'}}]}}))
            (legacy/'keys.env').write_text('AMPLIFIER_MIGRATION_TEST_TOKEN="example test credential"\n')
            (legacy/'cache/module').mkdir(parents=True);(legacy/'cache/module/source.py').write_text('pass')
            (legacy/'registry.json').write_text(json.dumps({'version':1,'bundles':{'custom':{'uri':'git+https://example.org/custom','local_path':str(legacy/'cache/module')}}}))
            (workspace/'.amplifier/settings.yaml').write_text(yaml.safe_dump({'config':{'providers':[{'module':'provider-test','id':'first','config':{'model':'b'}},{'module':'provider-test','id':'second','config':{'model':'c'}}]}}))
            with patch.dict(os.environ, {}, clear=False):
                config=load_config(workspace,home=home,legacy_home=legacy)
                self.assertEqual(config.active_bundle,'custom')
                self.assertEqual([r['config']['model'] for r in config.providers],['b','c'])
                self.assertEqual(os.environ['AMPLIFIER_MIGRATION_TEST_TOKEN'],'example test credential')
                self.assertFalse((home/'config/keys.env').exists())
                self.assertFalse((home/'foundation').exists())
                prepare_registry(config)
                self.assertFalse((home/'foundation/registry.json').exists())
                self.assertFalse((home/'foundation/cache/module').exists())
                self.assertEqual((legacy/'cache/module/source.py').read_text(),'pass')
                self.assertEqual(json.loads((legacy/'registry.json').read_text())['bundles']['custom']['local_path'],str(legacy/'cache/module'))
                (legacy/'settings.yaml').write_text('bundle:\n  active: changed-externally\n')
                again=load_config(workspace,home=home,legacy_home=legacy)
                self.assertEqual(again.active_bundle,'changed-externally')

    def test_provider_instances_merge_by_instance_not_module(self):
        result=merge([{'module':'p','id':'a','config':{'secret':'retained','model':'old'}},{'module':'p','id':'b'}],
                     [{'module':'p','id':'a','config':{'model':'new'}}])
        self.assertEqual(len(result),2)
        self.assertEqual(result[0]['config'],{'secret':'retained','model':'new'})

    def test_legacy_private_settings_do_not_override_shared_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);home=root/'home';workspace=root/'project';legacy=root/'legacy'
            (home/'config').mkdir(parents=True);workspace.mkdir();(workspace/'.amplifier').mkdir()
            (home/'config/settings.yaml').write_text(yaml.safe_dump({'bundle':{'app':['added']},'web_bundles':{
                'entries':[{'uri':'added','role':'behavior','enabled':True},{'uri':'disabled','role':'behavior','enabled':False}],
                'excluded':['removed']}}))
            (workspace/'.amplifier/settings.yaml').write_text(yaml.safe_dump({'bundle':{'app':['disabled','removed','project-only']}}))
            (workspace/'.amplifier-unified').mkdir()
            (workspace/'.amplifier-unified/settings.local.yaml').write_text('local_setting: active\n')
            result=load_config(workspace,home=home,legacy_home=legacy)
            self.assertEqual(result.app_bundles,['disabled','removed','project-only',*APPENDED_DEFAULTS])
            self.assertNotIn('local_setting',result.settings)

    def test_legacy_provider_ids_map_to_core_instances_without_collapsing(self):
        bundle=SimpleNamespace(providers=[{'module':'p','instance_id':'one','config':{'model':'old'}}],tools=[],hooks=[],session={},agents={})
        providers=[{'module':'p','id':'one','config':{'model':'new'}},{'module':'p','id':'two'}]
        config=SimpleNamespace(settings={'config':{'providers':providers}},providers=providers)
        result=_apply_settings(bundle,config)
        self.assertEqual([p['instance_id'] for p in result.providers],['one','two'])
        self.assertEqual(result.providers[0]['config']['model'],'new')

    def test_environment_values_never_execute_shell_and_missing_is_explicit(self):
        with patch.dict(os.environ,{'AMPLIFIER_TEST_VALUE':'$(echo private)','AMPLIFIER_TEST_EMPTY':''},clear=False):
            self.assertEqual(expand_environment('${AMPLIFIER_TEST_VALUE}'),'$(echo private)')
            self.assertEqual(expand_environment('${AMPLIFIER_TEST_VALUE}',environment={'AMPLIFIER_TEST_VALUE':'injected'}),'injected')
            self.assertEqual(expand_environment('${AMPLIFIER_TEST_ABSENT:-fallback}'),'fallback')
            self.assertEqual(expand_environment('${AMPLIFIER_TEST_ABSENT:INFO}'),'INFO')
            self.assertEqual(expand_environment('${AMPLIFIER_TEST_ABSENT:}'),'')
            self.assertEqual(expand_environment('${AMPLIFIER_TEST_EMPTY:-fallback}'),'fallback')
            self.assertEqual(expand_environment('${AMPLIFIER_TEST_EMPTY:fallback}'),'')
            with self.assertRaisesRegex(ValueError,'AMPLIFIER_TEST_ABSENT'):
                expand_environment('${AMPLIFIER_TEST_ABSENT}')

    def test_apply_settings_defers_provider_config_but_expands_provider_source(self):
        bundle=SimpleNamespace(providers=[],tools=[],hooks=[],session={},agents={})
        providers=[{'module':'provider-test','id':'one','source':'${PROVIDER_SOURCE}','config':{'base_url':'${OPTIONAL_BASE_URL}'}}]
        config=SimpleNamespace(settings={'config':{'providers':providers}},providers=providers)
        with patch.dict(os.environ,{'PROVIDER_SOURCE':'source'},clear=False):
            result=_apply_settings(bundle,config)
            self.assertEqual(result.providers[0]['source'],'source')
            self.assertEqual(result.providers[0]['config']['base_url'],'${OPTIONAL_BASE_URL}')

    def test_unsupported_root_orchestrator_is_not_silently_replaced(self):
        with self.assertRaisesRegex(ValueError,'not compatible'):
            live_plan({'session':{'orchestrator':{'module':'custom-loop'}}})

    def test_loop_overlay_preserves_baseline_and_custom_finite_agent(self):
        original={'session':{'orchestrator':{'module':'loop-streaming','config':{'max_turns':10}}},
                  'agents':{'worker':{'session':{'orchestrator':{'module':'custom-finite'}}}}}
        adapted,replacements=live_plan(original)
        self.assertEqual(original['session']['orchestrator']['module'],'loop-streaming')
        self.assertEqual(adapted['session']['orchestrator']['module'],'loop-live')
        self.assertEqual(adapted['agents']['worker']['session']['orchestrator']['module'],'custom-finite')
        self.assertTrue(replacements)

    def test_live_plan_preserves_opt_in_background_policy(self):
        adapted, _ = live_plan({'session': {'orchestrator': {'module': 'loop-live', 'config': {'background_delegate': False}}}})
        self.assertFalse(adapted['session']['orchestrator']['config']['background_delegate'])

    def test_boundary_context_gets_archive_recovery_on_every_mount(self):
        original = {'session': {'orchestrator': {'module': 'loop-live'},
            'context': {'module': 'context-managed', 'config': {'engine': 'boundary'}}}}
        adapted, _ = live_plan(original)
        config = adapted['session']['context']['config']
        self.assertTrue(config['archive_recovery'])
        self.assertTrue(config['durable_checkpoints'])
        self.assertNotIn('archive_recovery', original['session']['context']['config'])

    def test_default_boundary_context_gets_host_checkpoint_policy(self):
        for context in ('context-managed', {'module': 'context-managed'},
                        {'module': 'context-managed', 'config': {}}):
            with self.subTest(context=context):
                original = {'session': {'orchestrator': 'loop-live', 'context': context},
                    'agents': {'worker': {'session': {'orchestrator': 'loop-streaming',
                        'context': context}}}}
                before = copy.deepcopy(original)
                adapted, _ = live_plan(original)
                for session in (adapted['session'], adapted['agents']['worker']['session']):
                    self.assertTrue(session['context']['config']['durable_checkpoints'])
                    self.assertTrue(session['context']['config']['archive_recovery'])
                    self.assertNotIn('engine', session['context']['config'])
                self.assertEqual(original, before)

    def test_boundary_policy_preserves_opt_out_and_invalid_engine(self):
        for config in ({'durable_checkpoints': False, 'archive_recovery': False},
                       {'engine': 'boundary', 'durable_checkpoints': False, 'archive_recovery': False}):
            adapted, _ = live_plan({'session': {'orchestrator': 'loop-live',
                'context': {'module': 'context-managed', 'config': config}}})
            self.assertEqual(adapted['session']['context']['config'], config)
        for engine in (None, False, '', 'legacy', 'unknown'):
            original = {'session': {'orchestrator': 'loop-live',
                'context': {'module': 'context-managed', 'config': {'engine': engine}}}}
            adapted, _ = live_plan(original)
            self.assertEqual(adapted['session']['context'], original['session']['context'])

    def test_imported_queued_receipt_is_history_not_replay(self):
        rows=[{'role':'tool','tool_call_id':'c','content':json.dumps({'status':'queued','call_id':'c','job_id':'j'})}]
        repaired=repair_interrupted_receipts(rows)
        self.assertEqual(json.loads(repaired[0]['content'])['status'],'interrupted')
        self.assertEqual(json.loads(rows[0]['content'])['status'],'queued')

    def test_receipt_repair_does_not_clone_unrelated_history_or_mutate_input(self):
        class Continuation(dict):
            def __deepcopy__(self, memo):
                raise AssertionError('Receipt repair must not clone retained provider payloads')

        queued = {'status': 'queued', 'call_id': 'c', 'job_id': 'j'}
        rows = [
            {'role': 'assistant', 'content': 'Retained answer', 'continuation': Continuation({'opaque': ['saved']})},
            {'role': 'tool', 'tool_call_id': 'c', 'content': json.dumps(queued), 'metadata': {'keep': True}},
            {'role': 'tool', 'tool_call_id': 'other', 'content': json.dumps(queued)},
            {'role': 'tool', 'content': 'Ordinary output'},
            {'role': 'tool', 'content': '["not a receipt"]'},
        ]
        before = json.loads(json.dumps(rows))
        repaired = repair_interrupted_receipts(rows)
        self.assertEqual(rows, before)
        self.assertEqual(repaired[:1] + repaired[2:], rows[:1] + rows[2:])
        self.assertEqual(repaired[1]['metadata'], rows[1]['metadata'])
        receipt = json.loads(repaired[1]['content'])
        self.assertEqual((receipt['status'], receipt['outcome'], receipt['effects']),
                         ('interrupted', 'unconfirmed', 'not_rolled_back'))
        self.assertEqual((receipt['call_id'], receipt['job_id']), ('c', 'j'))

    def test_report_redacts_nested_credentials(self):
        value=redact({'providers':[{'config':{'api_key':'private','default_model':'model'}}]})
        self.assertNotIn('private',json.dumps(value))
        self.assertEqual(value['providers'][0]['config']['default_model'],'model')


@unittest.skipUnless(os.name == "nt", "requires native Windows junctions, local NTFS and profile ACLs")
class NativeHomeBoundaryTests(unittest.TestCase):
    def test_update_recovery_private_read_rejects_declared_shared_home_junction(self):
        import shutil
        import subprocess
        import uuid
        from amplifier_web import windows_files
        from amplifier_web.host import config as host_config
        allocation = windows_files.address(Path.home() / (".unified-recovery-test-" + uuid.uuid4().hex))
        unrelated = allocation / "unrelated"
        alias = allocation / "shared-alias"
        with windows_files.directory(unrelated, owned_root=allocation, create=True) as pinned:
            original = b"FIXTURE_MISSING=must-not-be-read-through-alias\n"
            pinned.publish_private("keys.env", original)
        self.addCleanup(shutil.rmtree, allocation)
        result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(alias), str(unrelated)],
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.addCleanup(alias.rmdir)
        before = (unrelated / "keys.env").stat()
        with patch.dict(os.environ, {"AMPLIFIER_HOME": str(alias)}):
            with self.assertRaises(PermissionError):
                host_config.missing_environment_available("FIXTURE_MISSING")
        self.assertEqual((unrelated / "keys.env").read_bytes(), original)
        after = (unrelated / "keys.env").stat()
        self.assertEqual((before.st_ino, before.st_size, before.st_mtime_ns),
                         (after.st_ino, after.st_size, after.st_mtime_ns))

    def test_app_home_through_read_config_rejects_junction_without_changing_target(self):
        import shutil
        import subprocess
        import uuid
        from amplifier_web import deployment, windows_files
        from amplifier_web.host import config as host_config
        allocation = windows_files.address(Path.home() / (".unified-home-test-" + uuid.uuid4().hex))
        unrelated = allocation / "unrelated"
        alias = allocation / "app-home"
        with windows_files.directory(unrelated / "config", owned_root=allocation, create=True) as pinned:
            original = b"port: 9321\nextension: unrelated\n"
            pinned.publish_private("server.yaml", original)
        self.addCleanup(shutil.rmtree, allocation)
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", f'mklink /J "{alias}" "{unrelated}"'],
            capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.addCleanup(alias.rmdir)  # remove the junction itself before fixture cleanup
        sentinel = unrelated / "config/server.yaml"
        before = sentinel.stat()
        with patch.dict(os.environ, {"AMPLIFIER_WEB_HOME": str(alias)}):
            for explicit_home in (None, alias):
                with self.subTest(explicit_home=explicit_home), \
                        isolated_read_config_collaborators(allocation, allocation):
                    config = host_config.read_config(
                        allocation, home=explicit_home, shared_home=allocation,
                        registry_home=allocation / "registry")
                    with self.assertRaises(PermissionError):
                        deployment.write_private(
                            config.home / "config/server.yaml", b"must-not-replace",
                            owned_root=config.home)
                    self.assertEqual(config.home, alias)
                    self.assertEqual(sentinel.read_bytes(), original)
                    after = sentinel.stat()
                    self.assertEqual(
                        (after.st_ino, after.st_size, after.st_mtime_ns, after.st_mode),
                        (before.st_ino, before.st_size, before.st_mtime_ns, before.st_mode))
                    self.assertEqual([path.name for path in (unrelated / "config").iterdir()], ["server.yaml"])


if __name__=='__main__':unittest.main()
