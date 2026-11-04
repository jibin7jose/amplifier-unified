"""Cross-application configuration contract, without a CLI runtime dependency."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from filelock import FileLock
import pytest
import yaml

from amplifier_web.bundles import BundleManager
from amplifier_web.builtin_behaviors import APPENDED_DEFAULTS
from amplifier_web.host.config import load_config
from amplifier_web.host.session import _apply_settings
from amplifier_web.preferences import SettingsStore
from amplifier_web.service import AppService
from amplifier_web.shared_settings import read_settings, settings_paths
from amplifier_web.shared_state import configuration_stamp


def test_cli_scope_contract_and_live_edits(tmp_path):
    store = SettingsStore(tmp_path / 'app')
    workspace = tmp_path / 'work'; workspace.mkdir()
    store.update(workspace, 'global', lambda s: s.update(
        bundle={'active': 'global', 'app': ['global-behavior']},
        config={'providers': [
            {'id': 'one', 'module': 'provider-test', 'config': {'model': 'global', 'api_key': '${KEY}'}},
            {'id': 'two', 'module': 'provider-test', 'config': {'model': 'second'}}]},
        modules={'tools': [{'module': 'tool-global'}]}, voice={'preferred_model': 'global-voice'},
        unrelated={'future_tui': True}))
    store.update(workspace, 'project', lambda s: s.update(
        bundle={'active': 'project', 'app': ['project-behavior']},
        config={'providers': [{'id': 'one', 'module': 'provider-test', 'config': {'model': 'project'}}]},
        modules={'tools': [{'module': 'tool-project'}]}))
    store.update(workspace, 'local', lambda s: s.update(bundle={'active': 'local'}))
    store.update(workspace, 'session', lambda s: s.update(
        bundle={'active': 'session'}, voice={'preferred_model': 'session-voice'}), session_id='existing')
    config = load_config(workspace, home=store.home, session_id='existing')
    assert config.active_bundle == 'session'
    assert config.app_bundles == ['project-behavior', *APPENDED_DEFAULTS]
    assert config.providers[0]['config'] == {'model': 'project', 'api_key': '${KEY}'}
    assert len(config.providers) == 2
    assert config.settings['modules']['tools'] == [{'module': 'tool-project'}]
    assert config.settings['voice']['preferred_model'] == 'session-voice'
    assert config.settings['unrelated']['future_tui']
    assert config.settings_file == store.shared_home / 'settings.yaml'
    assert not (store.home / 'config/settings.yaml').exists()
    store.path(workspace, 'local').write_text('bundle: {active: edited-in-cli}\n')
    assert load_config(workspace, home=store.home).active_bundle == 'edited-in-cli'
    store.path(workspace, 'local').unlink()
    assert load_config(workspace, home=store.home).active_bundle == 'project'


def test_shared_writer_uses_cli_lock_and_preserves_other_fields_and_permissions(tmp_path):
    store = SettingsStore(tmp_path / 'app')
    path = store.path(tmp_path, 'project')
    path.parent.mkdir(); path.parent.chmod(0o755)
    path.write_text('future_app: {enabled: true}\n'); path.chmod(0o644)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with FileLock(str(path) + '.lock'):
            future = pool.submit(store.update, tmp_path, 'project', lambda s: s.update(voice={'preferred_model': 'model'}))
            # Simulate a CLI transaction holding the same lock.
            path.write_text('future_app: {enabled: true, edited: true}\n')
        future.result(timeout=5)
    assert yaml.safe_load(path.read_text()) == {'future_app': {'enabled': True, 'edited': True}, 'voice': {'preferred_model': 'model'}}
    assert path.stat().st_mode & 0o777 == 0o644
    assert path.parent.stat().st_mode & 0o777 == 0o755


def test_shared_routing_and_session_changes_invalidate_mount(tmp_path):
    paths = settings_paths(tmp_path, session_id='chat')
    def stamp(path):
        return path.read_bytes() if path.is_file() else path.exists()
    def snapshot():
        return configuration_stamp(tmp_path, 'chat', tmp_path / 'app', stamp)
    before = snapshot()
    paths['session'].parent.mkdir(parents=True)
    paths['session'].write_text('routing: {matrix: custom}\n')
    after = snapshot(); assert before != after
    routing = paths['global'].parent / 'routing'; routing.mkdir()
    matrix = routing / 'custom.yaml'; matrix.write_text('roles: {}\n')
    newer = snapshot(); assert after != newer
    matrix.write_text('roles: {general: {}}\n')
    assert newer != snapshot()


def test_shared_module_overrides_and_routing_apply_without_app_paths(tmp_path):
    from amplifier_foundation import Bundle
    bundle = Bundle(name='shared-settings', hooks=[{'module': 'hooks-routing'}],
        session={'context': {'module': 'context-simple', 'config': {'other': True}},
                 'orchestrator': {'module': 'loop-streaming'}})
    config = SimpleNamespace(settings={'overrides': {
        'context-simple': {'config': {'token_meter': 'actual'}},
        'loop-streaming': {'config': {'max_turns': 25}}}},
        providers=[], workspace=tmp_path)
    _apply_settings(bundle, config)
    assert bundle.session['context']['config'] == {'other': True, 'token_meter': 'actual'}
    assert bundle.session['orchestrator']['config']['max_turns'] == 25
    assert bundle.hooks[0]['config']['custom_routing_dirs'][-1] == str(settings_paths(tmp_path)['global'].parent / 'routing')
    assert all('.amplifier-unified' not in p for p in bundle.hooks[0]['config']['custom_routing_dirs'])


def test_cli_bundle_edits_override_stale_ui_metadata():
    settings = {'bundle': {'app': ['new', 'old'], 'added': {'custom': 'new-source'}},
        'web_bundles': {'entries': [
            {'id': 'old-id', 'uri': 'old', 'name': 'Old', 'role': 'behavior', 'enabled': False},
            {'id': 'removed', 'uri': 'removed', 'name': 'Removed', 'role': 'behavior', 'enabled': True}],
            'excluded': ['old']}}
    rows = BundleManager.entries(settings)
    assert [r['uri'] for r in rows] == ['new', 'old', *APPENDED_DEFAULTS, 'new-source']
    assert rows[1]['id'] == 'old-id' and rows[1]['enabled']


@pytest.mark.asyncio
async def test_voice_and_default_bundle_roundtrip_shared_files(tmp_path):
    work = tmp_path / 'work'; work.mkdir()
    other = tmp_path / 'other'; other.mkdir()
    store = SettingsStore(tmp_path / 'app')
    store.update(work, 'global', lambda s: s.update(bundle={'active': 'shared'}, voice={'preferred_model': 'gpt-realtime-2.1'}, future={'keep': True}))
    store.update(other, 'local', lambda s: s.update(bundle={'active': 'workspace-specific'}))
    service = AppService(tmp_path / 'app', workspace=str(work))
    try:
        assert service.get_state()['settings']['bundle'] == 'shared'
        assert service._new_session({'workspace': str(other)})['bundle'] == 'workspace-specific'
        await service.dispatch('settings.update', {'patch': {'preferredVoice': 'gpt-live-1', 'bundle': 'updated'}})
        settings = read_settings(work)
        assert settings['voice']['preferred_model'] == 'gpt-live-1'
        assert settings['bundle']['active'] == 'updated' and settings['future']['keep']
        store.update(work, 'global', lambda s: s.update(voice={'preferred_model': 'gpt-realtime-2.1'}))
        assert service.browser_state()['settings']['preferredVoice'] == 'gpt-realtime-2.1'
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_legacy_voice_migrates_once_without_overwriting_shared_values(tmp_path):
    service = AppService(tmp_path / 'app', workspace=str(tmp_path))
    service.state.pop('sharedVoiceMigration')
    service.state['settings']['preferredVoice'] = 'gpt-realtime-2.1'
    service._save()
    await service.close()
    service = AppService(tmp_path / 'app', workspace=str(tmp_path))
    assert read_settings(tmp_path)['voice']['preferred_model'] == 'gpt-realtime-2.1'
    await service.close()
    store = SettingsStore(tmp_path / 'app')
    def remove_voice(settings):
        settings.pop("voice")
    store.update(tmp_path, "global", remove_voice)
    service = AppService(tmp_path / 'app', workspace=str(tmp_path))
    try:
        assert 'voice' not in read_settings(tmp_path)
        assert service.get_state()['settings']['preferredVoice'] == 'gpt-live-1'
    finally:
        await service.close()


def test_file_managed_keys_do_not_become_explicit_worker_environment(tmp_path, monkeypatch):
    from amplifier_web.host.config import _KEY_FILE_VALUES, _load_keys, worker_environment
    monkeypatch.setenv('SHARED_EXPLICIT_TEST', 'from-launch')
    monkeypatch.delenv('SHARED_FILE_TEST', raising=False)
    path = tmp_path / 'keys.env'
    path.write_text('SHARED_EXPLICIT_TEST=from-file\nSHARED_FILE_TEST=first\n')
    previous = dict(_KEY_FILE_VALUES)
    try:
        _load_keys(path)
        env = worker_environment()
        assert env['SHARED_EXPLICIT_TEST'] == 'from-launch'
        assert 'SHARED_FILE_TEST' not in env
        path.write_text('SHARED_FILE_TEST=changed\n')
        _load_keys(path)
        import os
        assert os.environ['SHARED_FILE_TEST'] == 'changed'
        path.unlink()
        _load_keys(path)
        assert 'SHARED_FILE_TEST' not in os.environ
    finally:
        _KEY_FILE_VALUES.clear()
        _KEY_FILE_VALUES.update(previous)


def test_migration_preserves_shared_authority_and_runs_once(tmp_path):
    from amplifier_web.settings_migration import migrate_settings
    from amplifier_web.shared_settings import atomic_write
    home, work = tmp_path / 'app', tmp_path / 'work'
    work.mkdir()
    store = SettingsStore(home)
    store.update(work, 'global', lambda s: s.update(bundle={'active': 'shared', 'added': {'same': 'shared-source'}},
        config={'providers': [{'id': 'shared', 'module': 'provider-test'}]}))
    atomic_write(home / 'config/settings.yaml', yaml.safe_dump({'bundle': {'active': 'old', 'added': {'same': 'old-source', 'extra': 'extra-source'}},
        'config': {'providers': [{'id': 'old', 'module': 'provider-old'}]}, 'routing': {'matrix': 'old'}}))
    atomic_write(store.shared_home / 'keys.env', 'SHARED_KEY=authoritative\n')
    atomic_write(home / 'config/keys.env', 'SHARED_KEY=stale\nMISSING_KEY=retained\n')
    atomic_write(work / '.amplifier-unified/settings.local.yaml', 'bundle: {added: {local: local-source}}\n')
    atomic_write(home / 'config/routing/custom.yaml', 'roles: {}\n')
    old = (home / 'config/settings.yaml').read_bytes()
    migrate_settings(home, [work])
    settings = read_settings(work)
    assert settings['bundle']['active'] == 'shared'
    assert settings['bundle']['added'] == {'same': 'shared-source', 'extra': 'extra-source', 'local': 'local-source'}
    assert [p['id'] for p in settings['config']['providers']] == ['shared']
    assert 'routing' not in settings
    assert (store.shared_home / 'keys.env').read_text() == 'SHARED_KEY=authoritative\nMISSING_KEY=retained\n'
    assert (store.shared_home / 'routing/custom.yaml').exists()
    assert (home / 'config/settings.yaml').read_bytes() == old
    assert list((home / 'backups').rglob('*-settings.yaml'))
    store.update(work, 'global', lambda s: s['bundle']['added'].pop('extra') and None)
    migrate_settings(home, [work])
    assert 'extra' not in read_settings(work)['bundle']['added']


def test_migration_bootstraps_only_absent_shared_settings(tmp_path):
    from amplifier_web.settings_migration import migrate_settings
    from amplifier_web.shared_settings import atomic_write
    home = tmp_path / 'app'
    original = {'bundle': {'active': 'custom'}, 'provider_order': ['second', 'first'],
        'config': {'providers': [{'id': name, 'module': 'provider-test'} for name in ('first', 'second')]},
        '_migration': {'version': 1}}
    atomic_write(home / 'config/settings.yaml', yaml.safe_dump(original))
    migrate_settings(home, [tmp_path])
    settings = read_settings(tmp_path)
    assert settings['bundle']['active'] == 'custom'
    assert '_migration' not in settings and 'provider_order' not in settings
    assert [p['config']['priority'] for p in settings['config']['providers']] == [2, 1]
    # An intentionally empty shared file is still an explicit shared choice.
    other = tmp_path / 'other-app'
    atomic_write(other / 'config/settings.yaml', yaml.safe_dump(original))
    store = SettingsStore(other)
    store.path(tmp_path, 'global').write_text('{}\n')
    migrate_settings(other, [tmp_path])
    assert read_settings(tmp_path) == {}
