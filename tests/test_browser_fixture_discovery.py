"""The geometry fixture must not start external discovery/preparation."""
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from amplifier_web import draft_defaults, probe_process
from amplifier_web.runtime import RuntimeManager
from amplifier_web.server import create_app
from amplifier_web.session_files import amplifier_home
from amplifier_web.setup import SetupManager


@pytest.fixture
def configured_browser(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parent / 'fixtures'))
    import configured_browser
    return configured_browser


@pytest.mark.parametrize('managed', [False, True], ids=['workspace', 'managed'])
async def test_synthetic_discovery_uses_production_owners_without_preparation(
        tmp_path, monkeypatch, configured_browser, managed):
    configured_browser.seed_connection(amplifier_home())
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    home = tmp_path / 'app'
    command = Mock(side_effect=AssertionError('Unexpected runtime preparation'))
    spawn = AsyncMock(side_effect=AssertionError('Unexpected external probe'))
    monkeypatch.setattr(RuntimeManager, '_command', command)
    monkeypatch.setattr(draft_defaults, 'spawn', spawn)
    monkeypatch.setattr(probe_process, 'spawn', spawn)
    original_defaults = draft_defaults.resolve_defaults
    original_probe = SetupManager.probe
    runtime = RuntimeManager()

    with configured_browser.synthetic_discovery():
        app = await create_app(home, workspace=str(workspace), runtime=runtime,
                               voice=False, background_updates=False)
        service = app['service']
        try:
            args = {'location': {'kind': 'managed'}} if managed else {'workspace': str(workspace)}
            await service.management.command('configuration.defaults', args)
            key = json.dumps(['', '', 'managed'] if managed else [str(workspace), ''],
                             separators=(',', ':'))
            defaults = service.state['draftDefaults'][key]
            assert defaults['phase'] == 'ready'
            assert defaults['effective'] == {'instance': 'fixture', 'model': 'fixture-model'}

            manager = SetupManager(home, catalog=service.management.provider_catalog,
                                   global_only=managed)
            path = str(home if managed else workspace)
            models = await manager.perform('providers.models', {'id': 'fixture', 'workspace': path})
            assert models['models'] == [{'id': 'fixture-model'}]
            assert models['modelsProviderId'] == 'fixture'
            schema = await manager.perform('providers.schema',
                                           {'module': 'provider-fixture', 'workspace': path})
            assert schema['providerMetadata']['configSchema'] == {'fields': []}

            # Retain the real configured roster and shared catalog, including
            # the catalog=True branch of the original defaults owner.
            catalog = await draft_defaults.resolve_defaults(
                home, path, catalog=True, global_only=managed,
                catalog_cache=service.management.provider_catalog)
            assert [row['id'] for row in catalog['providers']] == ['fixture']
            entry = catalog['catalogs'][catalog['providers'][0]['sharedCatalogKey']]
            assert entry['phase'] == 'ready'
            assert entry['models'] == [{'id': 'fixture-model'}]
            assert runtime.workers == {} and runtime._preparations == {}
            assert service.state['sessions'] == []
            assert not (home / 'foundation').exists()
        finally:
            # Close while patches are still installed, as the browser fixture does.
            await service.close()

    assert draft_defaults.resolve_defaults is original_defaults
    assert SetupManager.probe is original_probe
    command.assert_not_called()
    spawn.assert_not_called()
    assert runtime.workers == {} and runtime._preparations == {}
    assert not (home / 'foundation').exists()


def test_synthetic_discovery_restores_after_failure(configured_browser):
    original_defaults = draft_defaults.resolve_defaults
    original_probe = SetupManager.probe
    with pytest.raises(RuntimeError, match='fixture failed'):
        with configured_browser.synthetic_discovery():
            raise RuntimeError('fixture failed')
    assert draft_defaults.resolve_defaults is original_defaults
    assert SetupManager.probe is original_probe


@pytest.mark.parametrize('action,args', [
    ('providers.test', {'id': 'fixture'}),
    ('providers.models', {'id': 'other'}),
    ('providers.models', {'id': 'fixture', 'module': 'provider-other'}),
    ('providers.schema', {'module': 'provider-other'}),
])
async def test_synthetic_probe_rejects_unowned_requests(configured_browser, action, args):
    with configured_browser.synthetic_discovery():
        with pytest.raises(AssertionError):
            await SetupManager.probe(None, action, args, '/unused')