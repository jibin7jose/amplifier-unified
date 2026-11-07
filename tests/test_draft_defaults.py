import types
import asyncio
from pathlib import Path
import pytest
from amplifier_web.draft_defaults_probe import query


@pytest.mark.parametrize('scope', ['shared', 'project', 'local'])
@pytest.mark.parametrize('explicit', [False, True])
async def test_draft_precedence_matches_first_provider_dispatch(tmp_path, monkeypatch, scope, explicit):
    """Probe and native loop resolve the same synthetic settings, not a label copy."""
    import copy
    import yaml
    from amplifier_core import ProviderInfo
    from amplifier_core.message_models import ChatRequest
    from amplifier_foundation import Bundle
    from amplifier_module_loop_live.orchestrator import BundleLiveOrchestrator
    from amplifier_web.host import config, session
    from amplifier_web import provider_environment as env
    from amplifier_web.shared_settings import settings_paths
    from amplifier_web.runtime_controls import RuntimeControls

    workspace = tmp_path / 'project'; workspace.mkdir()
    shared = tmp_path / 'shared'; shared.mkdir()
    home = tmp_path / 'app'
    monkeypatch.setenv('AMPLIFIER_HOME', str(shared))
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(home))
    paths = settings_paths(workspace)
    rows = [
        {'module': 'provider-fixture', 'id': 'cli', 'config': {
            'priority': 1, 'default_model': 'cli-model', 'reasoning_effort': 'low'}},
        {'module': 'provider-fixture', 'id': 'unified', 'config': {
            'priority': 20, 'default_model': 'unified-model', 'reasoning_effort': 'high'}},
    ]
    paths['global'].write_text(yaml.safe_dump({'config': {'providers': rows}}))
    if scope != 'shared':
        paths[scope].parent.mkdir(exist_ok=True)
        paths[scope].write_text(yaml.safe_dump({'config': {'providers': [
            {'module': 'provider-fixture', 'id': 'unified', 'config': {'priority': 0}}
        ]}}))
    original = {name: path.read_bytes() for name, path in paths.items() if path.exists()}

    async def root(conf, bundle, **kwargs):
        # Only external bundle acquisition is replaced. Keep settings composition.
        loaded = Bundle.from_dict({'bundle': {'name': 'fixture'}})
        return None, session._apply_settings(loaded, conf), None

    class Provider:
        def __init__(self, config):
            self.config = copy.deepcopy(config)
            self.priority = config.get('priority', 100)
            self.default_model = config.get('default_model', 'module-default')
            self.calls = []
        def get_info(self):
            return ProviderInfo(id='fixture', display_name='Fixture', defaults={
                'model': self.default_model, 'reasoning_effort': self.config.get('reasoning_effort')})
        def get_config_schema(self):
            return {'fields': []}
        async def complete(self, request, **kwargs):
            self.calls.append({'model': kwargs.get('model') or request.model or self.default_model,
                'effort': kwargs.get('reasoning_effort') or request.reasoning_effort or self.config.get('reasoning_effort')})
            return 'synthetic response'

    monkeypatch.setattr(session, 'load_root_bundle', root)
    monkeypatch.setattr(env, 'provider_class', lambda module: Provider)
    for index in range(2):
        # A subsequent chat repeats inheritance; the previous chat is not a default.
        preview = await query({'home': str(home), 'workspace': str(workspace), 'bundle': 'fixture'})
        _, loaded, _ = await root(config.load_config(workspace, home=home), 'fixture')
        providers = {row['instance_id']: Provider(row['config']) for row in loaded.providers}
        loop = BundleLiveOrchestrator({})
        selection = {'instance': 'unified', 'model': 'explicit-model', 'effort': 'xhigh'} if explicit else None
        if selection:
            loop.root_provider = session.SelectedProvider(providers[selection['instance']], selection)
        plan = {'session': {}, 'providers': loaded.providers}
        coordinator = types.SimpleNamespace(config=plan, session_state={},
            get=lambda name: {'providers': providers, 'orchestrator': loop, 'context': None}.get(name),
            get_capability=lambda name: None, register_capability=lambda *args: None)
        controls = RuntimeControls(types.SimpleNamespace(session_id=f'chat-{index}', coordinator=coordinator, config=plan),
                                   types.SimpleNamespace(generation=None, queued_inputs=0))
        await controls.restore()
        effective = (await controls.perform('configuration.providers'))['effective']
        displayed = selection or preview['effective']
        assert effective == displayed
        selected = loop._select_provider(providers)
        await selected.complete(ChatRequest(messages=[]))
        dispatched = [(identity, call) for identity, provider in providers.items() for call in provider.calls]
        assert dispatched == [(displayed['instance'], {'model': displayed['model'], 'effort': displayed['effort']})]
        assert controls.selection == selection
        await controls.close()
    assert {name: paths[name].read_bytes() for name in original} == original


async def test_provider_changes_invalidate_defaults_and_discard_old_probe(tmp_path, monkeypatch):
    from amplifier_web.server import create_app
    from amplifier_web import draft_defaults
    from unittest.mock import AsyncMock
    started, finish = asyncio.Event(), asyncio.Event()
    async def resolve(*args, **kwargs):
        started.set()
        await finish.wait()
        return {'effective': {'instance': 'old', 'model': 'old-model'}}
    monkeypatch.setattr(draft_defaults, 'resolve_defaults', resolve)
    app = await create_app(tmp_path / 'app', workspace=str(tmp_path), voice=False, background_updates=False)
    service = app['service']
    monkeypatch.setattr(service.management, 'warm_providers', AsyncMock())
    service.state['draftDefaults'] = {'old': {'phase': 'ready', 'effective': {'model': 'old-model'}}}
    service.state['setup'] = {'providersRequestedWorkspace': str(tmp_path)}
    pending = asyncio.create_task(service.management.perform('configuration.defaults', {'workspace': str(tmp_path)}))
    try:
        await asyncio.wait_for(started.wait(), 2)
        previous = service.state.get('configurationRevision', 0)
        await service.management.invalidate_configuration()
        assert service.state.get('configurationRevision') == previous + 1
        assert service.state['draftDefaults'] == {}
        assert 'providersRequestedWorkspace' not in service.state['setup']
        finish.set()
        await pending
        assert service.state['draftDefaults'] == {}, 'A result from before the change must not restore stale choices'
    finally:
        finish.set()
        await pending
        await service.runtime.close()
        for task in list(service.tasks): task.cancel()

@pytest.mark.asyncio
async def test_composed_defaults_use_provider_priority_without_session_or_inference(tmp_path,monkeypatch):
    from amplifier_web.host import config,session
    from amplifier_web import provider_environment as env
    workspace=tmp_path/'not-created'
    conf=types.SimpleNamespace(workspace=tmp_path)
    monkeypatch.setattr(config,'load_config',lambda *a,**k:conf)
    async def root(c,bundle,**kwargs):
        assert bundle=='exact-saved';assert c.workspace==workspace
        return None,types.SimpleNamespace(providers=[
            {'module':'provider-test','id':'slow','config':{'priority':100,'model':'slow-model'}},
            {'module':'provider-test','instance_id':'fast','config':{'priority':1,'model':'saved-model','reasoning_effort':'high'}}]),None
    monkeypatch.setattr(session,'load_root_bundle',root)
    closed=[]
    class Provider:
        def __init__(self,config):self.config=config;self.priority=config.get('priority',100)
        def get_info(self):return {'id':'test','display_name':'Test','defaults':{'model':self.config.get('model')},'api_key':'private'}
        def get_config_schema(self):return {'fields':[{'id':'reasoning_effort','choices':['high'],'field_type':'choice','required':False},{'id':'api_key','field_type':'secret','required':False,'default':'private'}]}
        def list_models(self):raise AssertionError('No network model discovery')
        async def close(self):closed.append(self)
    monkeypatch.setattr(env,'provider_class',lambda module:Provider)
    result=await query({'home':str(tmp_path),'workspace':str(workspace),'bundle':'exact-saved'})
    assert result['effective']=={'instance':'fast','model':'saved-model','effort':'high'}
    assert len(closed)==4
    assert 'private' not in str(result)
    assert not workspace.exists()
    assert list(tmp_path.iterdir())==[]

@pytest.mark.asyncio
async def test_default_action_is_read_only_and_keyed_to_requested_draft(tmp_path,monkeypatch):
    from amplifier_web.server import create_app
    from amplifier_web import draft_defaults
    workspace=tmp_path/'workspace';workspace.mkdir()
    async def resolve(home,path,bundle,app_bundle):
        return {'bundle':bundle or 'work','providers':[],'effective':{'instance':'one','model':'actual'}}
    monkeypatch.setattr(draft_defaults,'resolve_defaults',resolve)
    app=await create_app(tmp_path/'app',workspace=str(workspace),voice=False,background_updates=False)
    service=app['service'];before=[s['id'] for s in service.state['sessions']]
    future=workspace/'future'
    try:
        await service.management.command('configuration.defaults',{'workspace':str(future),'bundle':''})
        import json
        key=json.dumps([str(future),''],separators=(',',':'))
        assert service.state['draftDefaults'][key]['effective']['model']=='actual'
        assert [s['id'] for s in service.state['sessions']]==before
        assert not future.exists()
    finally:
        await service.runtime.close()
        for task in list(service.tasks):task.cancel()


@pytest.mark.asyncio
async def test_managed_probe_skips_app_directory_project_settings(tmp_path, monkeypatch):
    import os
    from amplifier_web.host import session
    shared = tmp_path/'shared'; shared.mkdir()
    monkeypatch.setenv('AMPLIFIER_HOME', str(shared))
    (shared/'settings.yaml').write_text('bundle:\n  active: global-bundle\n')
    home=tmp_path/'app'; home.mkdir(); (home/'.amplifier').mkdir()
    (home/'.amplifier'/'settings.yaml').write_text('bundle:\n  active: parent-project\n')
    async def root(config, bundle, **kwargs):
        assert config.active_bundle == 'global-bundle' and config.global_only
        from amplifier_web.shared_settings import routing_dirs
        assert routing_dirs(config.workspace,global_only=config.global_only) == [shared/'routing']
        return None,types.SimpleNamespace(providers=[]),None
    monkeypatch.setattr(session,'load_root_bundle',root)
    await query({'home':str(home),'workspace':str(home),'bundle':'work','globalOnly':True})
    assert not (home/'chats').exists()


async def test_composed_catalog_shares_connection_models_across_workspaces(tmp_path,monkeypatch):
    import yaml
    from amplifier_web.host.config import write_private
    from amplifier_web.session_files import amplifier_home
    from amplifier_web.setup import SetupManager
    from amplifier_web.draft_defaults import resolve_defaults
    from amplifier_web.provider_catalog import ProviderCatalog
    write_private(amplifier_home()/'settings.yaml',yaml.safe_dump({'config':{'providers':[
        {'module':'provider-test','id':'connection','config':{'base_url':'https://fixture.invalid'}}]}}))
    calls=[]
    async def probe(*args,**kwargs):
        calls.append(1)
        return {'models':[{'id':'chosen','display_name':'Chosen model'}],'modelsSupported':True,
                'providerMetadata':{'info':{},'configSchema':{'fields':[]}}}
    monkeypatch.setattr(SetupManager,'probe',probe)
    cache=ProviderCatalog(tmp_path/'cache.json')
    first=await resolve_defaults(tmp_path,str(tmp_path/'one'),catalog=True,catalog_cache=cache)
    key=first['providers'][0]['sharedCatalogKey'];entry=first['catalogs'][key]
    second=await resolve_defaults(tmp_path,str(tmp_path/'two'),catalog=True,catalog_cache=cache)
    assert second['providers'][0]['sharedCatalogKey']==key
    assert second['catalogs'][key]['models']==entry['models']
    assert calls==[1]
    assert 'never-export' not in str(first)
    assert not (tmp_path/'one').exists() and not (tmp_path/'two').exists()

async def test_duplicate_draft_reads_share_probe_but_new_revision_does_not(tmp_path, monkeypatch):
    import json
    from amplifier_web.server import create_app
    from amplifier_web import draft_defaults
    from unittest.mock import AsyncMock
    started, finish = asyncio.Event(), asyncio.Event()
    calls = []
    async def resolve(*args, **kwargs):
        calls.append(args)
        started.set()
        await finish.wait()
        return {'effective': {'model': 'resolved'}}
    monkeypatch.setattr(draft_defaults, 'resolve_defaults', resolve)
    app = await create_app(tmp_path / 'app', workspace=str(tmp_path), voice=False, background_updates=False)
    service = app['service']
    monkeypatch.setattr(service.management, 'warm_providers', AsyncMock())
    args = {'workspace': str(tmp_path)}
    first = asyncio.create_task(service.management.perform('configuration.defaults', args))
    others = []
    try:
        await asyncio.wait_for(started.wait(), 2)
        others = [asyncio.create_task(service.management.perform('configuration.defaults', args)) for _ in range(3)]
        await asyncio.sleep(0)
        assert len(calls) == 1
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        assert len(service.management.draft_default_tasks) == 1, 'One cancelled reader must not cancel other readers'
        await service.management.invalidate_configuration()
        started.clear()
        others.append(asyncio.create_task(service.management.perform('configuration.defaults', args)))
        await asyncio.wait_for(started.wait(), 2)
        assert len(calls) == 2
        finish.set()
        await asyncio.gather(*others)
        assert service.state['draftDefaults'][json.dumps([str(tmp_path), ''], separators=(',', ':'))]['phase'] == 'ready'
        assert not service.management.draft_default_tasks
    finally:
        finish.set()
        await asyncio.gather(first, *others, return_exceptions=True)
        await service.runtime.close()
        for task in list(service.tasks): task.cancel()
