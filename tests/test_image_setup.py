import copy
import pytest

from amplifier_web.setup import SetupManager


@pytest.fixture
def manager(tmp_path, monkeypatch):
    value = SetupManager(tmp_path / 'app')
    async def no_routing(workspace): pass
    monkeypatch.setattr(value, 'ensure_routing_catalog', no_routing)
    async def forbidden(*args, **kwargs):
        pytest.fail('The retired setup action must not discover or contact a provider')
    monkeypatch.setattr(value, 'cached_probe', forbidden)
    monkeypatch.setattr(value, 'probe', forbidden)
    return value


async def save(manager, workspace, identity='one', image=None):
    config = {'default_model': 'kept-chat-model', 'reasoning_effort': 'high',
              'api_key': '${FIXTURE_IMAGE_CREDENTIAL}', 'opaque': {'keep': True}}
    if image is not None: config['image_generation'] = image
    await manager.perform('providers.save', {'workspace': str(workspace), 'id': identity,
        'module': 'provider-openai', 'config': config})
    return config


@pytest.mark.asyncio
@pytest.mark.parametrize('scope', ['global', 'project', 'local'])
@pytest.mark.parametrize('payload', [
    {'id': 'one', 'enabled': True, 'model': 'image-fixture'},
    {'id': 'one', 'enabled': True, 'model': 'auto', 'exclusive': True},
    {'id': 'one', 'enabled': False},
    {'id': 'one', 'enabled': False, 'exclusive': True},
    {'mode': 'off'},
    {'mode': 'automatic'},
    {'mode': 'selected', 'id': 'one', 'model': 'pinned'},
])
async def test_removed_image_setup_action_refuses_without_writes(manager, tmp_path, monkeypatch, scope, payload):
    await save(manager, tmp_path, image={'enabled': True, 'id': 'custom-backend',
                                       'model': 'pinned', 'timeout': 420})
    await save(manager, tmp_path, 'other', {'enabled': False, 'model': 'other-pin'})
    manager.store.update(tmp_path, 'global', lambda settings: settings.update(
        config={**settings['config'], 'tools': [{'module': 'tool-image', 'config': {'allow_paid': False}}]},
        web_bundles={'excluded': ['kept-disabled-feature']}, routing={'matrix': 'kept'}))
    for location in ('project', 'local'):
        manager.store.update(tmp_path, location, lambda settings:
                             settings.update(image_generation={'mode': 'off'}))
    paths = [manager.store.path(tmp_path, location) for location in ('global', 'project', 'local')]
    before = {path: path.read_bytes() for path in paths}
    effective = copy.deepcopy(manager.config(tmp_path).settings)
    calls = []

    def forbidden_write(*args, **kwargs):
        calls.append((args, kwargs))
        pytest.fail('The retired image setup action must not enter a settings writer')

    monkeypatch.setattr(manager.store, 'update', forbidden_write)
    monkeypatch.setattr(manager, '_provider_mutation', forbidden_write)
    with pytest.raises(ValueError, match='Unknown setup operation'):
        await manager.perform('providers.configureImages', {
            'workspace': str(tmp_path), 'scope': scope, **payload})
    assert calls == []
    assert {path: path.read_bytes() for path in paths} == before
    assert manager.config(tmp_path).settings == effective


@pytest.mark.asyncio
async def test_removed_image_setup_cannot_create_fresh_settings(manager, tmp_path, monkeypatch):
    paths = [manager.store.path(tmp_path, scope) for scope in ('global', 'project', 'local')]
    assert all(not path.exists() for path in paths)

    def forbidden_write(*args, **kwargs):
        pytest.fail('Retired setup must not create settings, even for an off request')

    monkeypatch.setattr(manager.store, 'update', forbidden_write)
    with pytest.raises(ValueError, match='Unknown setup operation'):
        await manager.perform('providers.configureImages', {'workspace': str(tmp_path), 'mode': 'off'})
    assert all(not path.exists() for path in paths)


@pytest.mark.asyncio
async def test_removed_action_is_absent_from_public_schema_and_refused_by_dispatch(tmp_path):
    from amplifier_web.service import ACTION_DEFINITIONS, AppError, AppService
    from test_service import Runtime

    assert 'providers.configureImages' not in ACTION_DEFINITIONS
    assert 'providers.imageModels' in ACTION_DEFINITIONS
    assert 'providers.save' in ACTION_DEFINITIONS
    app = AppService(tmp_path / 'app', Runtime(), workspace=tmp_path)
    try:
        with pytest.raises(AppError, match='Unknown action'):
            await app.dispatch('providers.configureImages', {'mode': 'off'})
    finally:
        await app.close()


@pytest.mark.asyncio
async def test_provider_inventory_has_no_replacement_image_selector(manager, tmp_path):
    previous = await save(manager, tmp_path, image={'enabled': True, 'id': 'custom', 'model': 'pin'})
    path = manager.store.path(tmp_path, 'global')
    before = path.read_bytes()
    result = await manager.perform('providers.list', {'workspace': str(tmp_path)})
    assert 'imagePreference' not in result
    assert 'imageSetupCompletion' not in result
    assert result['providers'][0]['config']['image_generation'] == previous['image_generation']
    assert result['providers'][0]['config']['default_model'] == previous['default_model']
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_image_probe_never_uses_chat_discovery_or_generation(monkeypatch):
    from amplifier_web import provider_probe
    class Provider:
        def get_info(self): return {}
        def get_image_generation_info(self): return {'schemaVersion': 1, 'configKey': 'image_generation'}
        async def list_image_models(self): return [{'id': 'image-fixture', 'entitlement': 'unverified'}]
        async def list_models(self): pytest.fail('Image choices must not use the chat catalog')
        async def complete(self, *args): pytest.fail('Discovery must not call a model')
    async def schema(*args, **kwargs): return {'fields': []}
    async def close(*args): pass
    monkeypatch.setattr(provider_probe, 'provider_class', lambda module: Provider)
    monkeypatch.setattr(provider_probe, 'construct_schema_provider', lambda *args: Provider())
    monkeypatch.setattr(provider_probe, 'construct_provider', lambda *args: Provider())
    monkeypatch.setattr(provider_probe, 'config_schema', schema)
    monkeypatch.setattr(provider_probe, 'close_provider', close)
    result = await provider_probe.query({'module': 'provider-fixture', 'action': 'providers.imageModels'})
    assert result['imageModels'] == [{'id': 'image-fixture', 'entitlement': 'unverified'}]
    assert result['imageModelsSupported'] is True


@pytest.mark.asyncio
async def test_shared_image_catalog_is_attributed_to_requested_connection(tmp_path, monkeypatch):
    manager = SetupManager(tmp_path / 'app')
    for identity in ['first', 'second']:
        await save(manager, tmp_path, identity)
    calls = []
    async def probe(action, args, workspace):
        calls.append(args['id'])
        return {'imageModelsProviderId': args['id'], 'imageModelsSupported': True,
                'imageModels': [{'id': 'fixture'}]}
    monkeypatch.setattr(manager, 'probe', probe)
    first = await manager.cached_probe('providers.imageModels', {'id': 'first'}, tmp_path)
    second = await manager.cached_probe('providers.imageModels', {'id': 'second'}, tmp_path)
    assert first['imageModelsProviderId'] == 'first'
    assert second['imageModelsProviderId'] == 'second'
    assert second['imageModels'] == first['imageModels']
    assert calls == ['first']
