"""Shared selection inventory uses real host cache/action paths, no cached_probe shim."""
import asyncio
from collections import Counter
from copy import deepcopy

import pytest
import yaml

from amplifier_web.conversation_models import browse
from amplifier_web.host.config import write_private
from amplifier_web.management import Management
from amplifier_web.next_message_configuration import validate_selection
from amplifier_web.provider_catalog import ProviderCatalog, model_key
from amplifier_web.service import AppService
from amplifier_web.session_files import amplifier_home
from amplifier_web.setup import SetupManager


@pytest.fixture
async def catalog_app(tmp_path, monkeypatch):
    rows = [
        {'id': 'one', 'module': 'provider-fixture', 'config': {'base_url': 'https://one.test', 'default_model': 'not-discovered'}},
        {'id': 'two', 'module': 'provider-fixture', 'config': {'base_url': 'https://two.test', 'default_model': 'also-not-discovered'}},
        {'id': 'broken', 'module': 'provider-fixture', 'config': {'base_url': 'https://broken.test'}},
    ]
    write_private(amplifier_home() / 'settings.yaml', yaml.safe_dump({'config': {'providers': rows}}))
    service = AppService(tmp_path / 'app', workspace=tmp_path)
    service.management = Management(service)
    for title in ('first', 'second'):
        await service.dispatch('session.create', {'title': title})
        service._session()['selection']={'instance':'one','model':'one-real'}
    calls = Counter()
    state = {'failure': False, 'empty': False}
    async def probe(self, action, args, workspace):
        calls[args['id']] += 1
        await asyncio.sleep(.01)
        if args['id'] == 'broken' or state['failure']:
            raise ValueError('PRIVATE credential must not appear')
        return {'models': [] if state['empty'] else [{'id': args['id'] + '-real'}],
                'modelsSupported': True, 'modelsProvenance': 'provider-api',
                'providerMetadata': {'module': 'provider-fixture', 'info': {}, 'configSchema': {'fields': []}}}
    monkeypatch.setattr(SetupManager, 'probe', probe)
    yield service, calls, state, rows
    await service.close()


async def test_two_chats_and_settings_share_one_probe_and_no_fallback_inventory(catalog_app):
    service, calls, _, _ = catalog_app
    chats = list(service.state['sessions'])[-2:]
    chats[0]['selection'] = {'instance': 'one', 'model': 'saved-pin'}
    chats[1]['selection'] = {'instance': 'two', 'model': 'other-pin'}
    before = [deepcopy(row) for row in chats]
    manager = SetupManager(service.data_dir, catalog=service.management.provider_catalog)
    results = await asyncio.gather(browse(service.management, chats[0]), browse(service.management, chats[1]),
                                   manager.discover_catalog(service.default_workspace))
    assert calls == {'one': 1, 'two': 1, 'broken': 1}
    controls = [service.state['runtimeControl'][row['id']]['configuration.catalog'] for row in chats]
    assert controls[0]['providers'] == controls[1]['providers'] == results[2]['providers']
    assert [row['id'] for row in controls[0]['providers']] == ['one', 'two', 'broken']
    entries = results[2]['catalogs']
    by_id = {row['id']: entries[row['sharedCatalogKey']] for row in results[2]['providers']}
    assert by_id['one']['models'] == [{'id': 'one-real'}]
    assert by_id['two']['models'] == [{'id': 'two-real'}]
    assert by_id['broken']['phase'] == 'error'
    assert 'PRIVATE' not in str(by_id)
    assert [row['effective'] for row in controls] == [chat['selection'] for chat in chats]
    assert chats == before
    assert service.runtime is None


async def test_restart_age_reopen_failure_and_explicit_refresh(catalog_app):
    service, calls, state, _ = catalog_app
    manager = SetupManager(service.data_dir, catalog=service.management.provider_catalog)
    original = await manager.discover_catalog(service.default_workspace)
    key = model_key(original['providers'][0]['sharedCatalogKey'])
    now = service.management.provider_catalog.clock() + 10 * 86400
    restarted = ProviderCatalog(service.management.provider_catalog.path, clock=lambda: now)
    service.management.provider_catalog = restarted
    manager.catalog = restarted
    await manager.discover_catalog(service.default_workspace)
    assert calls == {'one': 1, 'two': 1, 'broken': 1}, 'Neither age nor restart retries discovery'
    state['failure'] = True
    failed = await manager.discover_catalog(service.default_workspace, refresh=True, instance='one')
    entry = failed['catalogs'][key[1]]
    assert entry['phase'] == 'error' and entry['models'] == [{'id': 'one-real'}]
    assert entry['loadedAt'] < entry['failedAt']
    await manager.discover_catalog(service.default_workspace)
    assert calls == {'one': 2, 'two': 1, 'broken': 1}
    state.update(failure=False, empty=True)
    fresh = await manager.discover_catalog(service.default_workspace, refresh=True, instance='one')
    assert fresh['catalogs'][key[1]]['phase'] == 'ready'
    assert fresh['catalogs'][key[1]]['models'] == []
    assert calls == {'one': 3, 'two': 1, 'broken': 1}


async def test_late_identity_change_discards_old_models(catalog_app, monkeypatch):
    service, _, _, rows = catalog_app
    manager = SetupManager(service.data_dir, catalog=service.management.provider_catalog)
    entered, release = asyncio.Event(), asyncio.Event()
    async def held(self, action, args, workspace):
        entered.set(); await release.wait()
        return {'models': [{'id': 'old-account'}], 'modelsSupported': True}
    monkeypatch.setattr(SetupManager, 'probe', held)
    old = model_key(manager.catalog_key({'id':'one'}, service.default_workspace))
    pending = asyncio.create_task(manager.discover_catalog(service.default_workspace))
    await entered.wait()
    rows[0]['config']['base_url'] = 'https://replacement.test'
    write_private(amplifier_home() / 'settings.yaml', yaml.safe_dump({'config': {'providers': rows}}))
    release.set()
    with pytest.raises(ValueError, match='changed during discovery'):
        await pending
    assert manager.catalog.peek(old) is None
    assert manager.catalog.peek(model_key(manager.catalog_key({'id':'one'}, service.default_workspace))) is None


@pytest.mark.parametrize('entry', [{'phase':'error'}, {'phase':'ready','supported':False}, {}, {'metadata':{}}])
def test_manual_model_does_not_need_invented_option_or_clear_unknown_effort(entry):
    catalog={'providers':[{'id':'one','sharedCatalogKey':'key'}], 'catalogs':{'key':entry}}
    selected={'instance':'one','model':'explicit-deployment','effort':'saved-effort'}
    before=deepcopy(selected)
    validate_selection(selected,catalog)
    assert selected==before
    with pytest.raises(ValueError,match='provider is unavailable'):
        validate_selection({**selected,'instance':'not-configured'},catalog)


def test_authentic_list_does_not_add_configured_default():
    row={'id':'one','sharedCatalogKey':'key','info':{'defaults':{'model':'default'}}}
    entry={'phase':'ready','supported':True,'models':[{'id':'real'}],'metadata':{'configSchema':{'fields':[]}}}
    validate_selection({'instance':'one','model':'default'}, {'providers':[row],'catalogs':{'key':entry}})
    assert entry['models']==[{'id':'real'}], 'Manual selection does not invent a catalog row'


async def test_cache_cancelled_waiter_does_not_cancel_shared_load_and_refresh_coalesces():
    cache=ProviderCatalog(); entered,release=asyncio.Event(),asyncio.Event(); calls=0
    async def load():
        nonlocal calls
        calls+=1;entered.set();await release.wait();return {'models':[]}
    first=asyncio.create_task(cache.get(('providers.models','same'),load,refresh=True))
    await entered.wait()
    second=asyncio.create_task(cache.get(('providers.models','same'),load,refresh=True))
    first.cancel()
    with pytest.raises(asyncio.CancelledError):await first
    release.set();assert await second=={'models':[]};assert calls==1
    await cache.close()


async def test_cancelled_reader_cannot_persist_changed_identity(catalog_app, monkeypatch):
    service, _, _, rows = catalog_app
    manager=SetupManager(service.data_dir,catalog=service.management.provider_catalog)
    entered, release=asyncio.Event(),asyncio.Event()
    async def probe(self,action,args,workspace):
        entered.set();await release.wait()
        return {'models':[{'id':'wrong-account'}]}
    monkeypatch.setattr(SetupManager,'probe',probe)
    key=model_key(manager.catalog_key({'id':'one'},service.default_workspace))
    reader=asyncio.create_task(manager.cached_probe('providers.models',{'id':'one'},service.default_workspace))
    await entered.wait()
    load=manager.catalog.pending[key]
    reader.cancel()
    with pytest.raises(asyncio.CancelledError):await reader
    rows[0]['config']['base_url']='https://changed.test'
    write_private(amplifier_home()/'settings.yaml',yaml.safe_dump({'config':{'providers':rows}}))
    release.set()
    from amplifier_web.provider_catalog import CatalogInvalidated
    with pytest.raises(CatalogInvalidated):await load
    assert manager.catalog.peek(key) is None
    assert ProviderCatalog(manager.catalog.path).peek(key) is None


async def test_queued_probe_checks_identity_before_capturing_request(catalog_app, monkeypatch):
    service, _, _, rows=catalog_app
    manager=SetupManager(service.data_dir,catalog=service.management.provider_catalog)
    manager.catalog.load_limit=asyncio.Semaphore(0)
    calls=[]
    async def probe(*args,**kwargs):calls.append(1);return {'models':[{'id':'wrong'}]}
    monkeypatch.setattr(SetupManager,'probe',probe)
    key=model_key(manager.catalog_key({'id':'one'},service.default_workspace))
    task=asyncio.create_task(manager.cached_probe('providers.models',{'id':'one'},service.default_workspace))
    await asyncio.sleep(0)
    rows[0]['config']['base_url']='https://different.test'
    write_private(amplifier_home()/'settings.yaml',yaml.safe_dump({'config':{'providers':rows}}))
    manager.catalog.load_limit.release()
    from amplifier_web.provider_catalog import CatalogInvalidated
    with pytest.raises(CatalogInvalidated):await task
    assert calls==[] and manager.catalog.peek(key) is None


async def test_cache_never_retains_raw_provider_failure_body(tmp_path):
    cache=ProviderCatalog(tmp_path/'catalog.json')
    async def bad():raise RuntimeError('https://secret-user:secret-password@private.test')
    with pytest.raises(RuntimeError):await cache.get(('providers.models','key'),bad)
    assert 'secret' not in cache.path.read_text()
    assert 'RuntimeError' in cache.entry(('providers.models','key'))['error']


async def test_shared_roster_marks_conversation_override_ineligible(catalog_app):
    import json
    service, _, _, rows=catalog_app
    chat=service._session()
    override=service.data_dir/'sessions'/chat['id']/'configuration.json'
    changed=deepcopy(rows);changed[0]['config']['base_url']='https://private-conversation.test'
    write_private(override,json.dumps({'providers':changed}))
    await browse(service.management,chat)
    result=service.state['runtimeControl'][chat['id']]['configuration.catalog']
    assert result['connectionBindings']['one']['eligible'] is False
    assert 'two' not in result['connectionBindings']
    assert [row['id'] for row in result['providers']]==['one','two','broken']
    assert 'private-conversation' not in str(result)
    assert json.loads(override.read_text())['providers']==changed


async def test_runtime_rejects_same_id_different_account_before_selecting():
    from types import SimpleNamespace
    from amplifier_web.runtime_controls import RuntimeControls
    loop=SimpleNamespace(root_provider=None)
    mounted={'one':object()}
    capabilities={'web.provider_catalog':{'connectionKeys':{'one':'mounted-account'}}}
    co=SimpleNamespace(config={},get=lambda name: {'providers':mounted,'orchestrator':loop}.get(name),
        get_capability=capabilities.get,register_capability=capabilities.__setitem__)
    controls=RuntimeControls(SimpleNamespace(coordinator=co,session_id='chat',config={}),
        SimpleNamespace(generation=None,queued_inputs=0))
    try:
        with pytest.raises(ValueError,match='mounted connection differs'):
            await controls.perform('provider.select',{'instance':'one','model':'same-model','expectedConnectionKey':'configured-account'})
        assert loop.root_provider is None
        with pytest.raises(ValueError,match='mounted connection differs'):
            await controls.perform('provider.verifyConnection',{'instance':'one','expectedConnectionKey':'configured-account'})
    finally:await controls.close()


async def test_discovery_applies_effective_overrides_without_adding_connections(catalog_app, monkeypatch):
    service, _, _, rows=catalog_app
    write_private(amplifier_home()/'settings.yaml',yaml.safe_dump({'config':{'providers':rows},
        'overrides':{'provider-fixture':{'config':{'extra':'discovery-option'}},
                     'one':{'config':{'base_url':'https://override.test'}},
                     'two':{'enabled':False},
                     'not-configured':{'config':{'base_url':'https://not-an-option.test'}}}}))
    manager=SetupManager(service.data_dir,catalog=service.management.provider_catalog)
    snapshots=[]
    async def probe(self,action,args,workspace):
        snapshots.append(self.probe_snapshot(action,args,workspace))
        return {'models':[],'modelsSupported':True}
    monkeypatch.setattr(SetupManager,'probe',probe)
    result=await manager.discover_catalog(service.default_workspace)
    assert [row['id'] for row in result['providers']]==['one','broken']
    assert {row['config']['base_url'] for row in snapshots}=={'https://override.test','https://broken.test'}
    assert all(row['config']['extra']=='discovery-option' for row in snapshots)
    assert result['providers'][0]['sharedCatalogKey']==manager.catalog_key({'id':'one'},service.default_workspace)


async def test_queued_selection_updates_configured_binding(catalog_app):
    from amplifier_web.conversation_models import select
    from amplifier_web.provider_catalog import configured_binding
    service, _, _, _=catalog_app
    chat=service._session()
    await select(service.management,chat,{'instance':'one','model':'one-real'})
    first=chat['modelConnectionKey']
    await select(service.management,chat,{'instance':'two','model':'two-real'})
    assert chat['modelConnectionKey']!=first
    assert chat['modelConnectionKey']==configured_binding(service.data_dir,chat['workspace'],'two')


@pytest.mark.parametrize('listing', ['recommendations','empty','failed'])
async def test_hidden_exact_pending_selection_survives_display_and_input_preparation(catalog_app, listing):
    from amplifier_web.conversation_models import select
    from amplifier_web.next_message_configuration import prepare
    service, _, state, _=catalog_app
    state.update(empty=listing=='empty',failure=listing=='failed')
    chat=service._session()
    chosen={'instance':'one','model':'not-recommended'}
    await select(service.management,chat,chosen)
    binding=chat['modelConnectionKey']
    revision=chat['nextMessageRevision']
    await browse(service.management,chat,refresh=True)
    prepared,guard=await prepare(service,chat)
    guard()
    assert chat['selection']==chat['pendingModelSelection']==prepared['selection']==chosen
    assert chat['modelConnectionKey']==binding==prepared['modelConnectionKey']
    assert chat['nextMessageRevision']==revision
    assert all(chosen['model'] not in [row['id'] for row in entry['models']]
               for entry in service.state['modelCatalogs'].values())
