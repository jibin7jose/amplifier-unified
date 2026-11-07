"""Host-wide catalog reuse requires equivalent workspace/config/credential/source."""
import asyncio
from collections import Counter
import copy
import json

import pytest

from amplifier_web.management import Management
from amplifier_web.preferences import SettingsStore
from amplifier_web.provider_catalog import ProviderCatalog, configuration_key, model_key
from amplifier_web.service import AppService
from amplifier_web.setup import SetupManager


class Runtime:
    def __init__(self):self.calls=Counter();self.rows=[];self.revision='first'
    async def close(self):pass
    async def start(self,*args):pass
    async def control(self,sid,operation,args):
        if operation=='configuration.providers':return {'providers':self.rows,'catalogRevision':self.revision}
        self.calls[args['instance']]+=1
        return {'models':[{'id':'mounted-model'}],'supported':True}


@pytest.fixture
async def app(tmp_path):
    app=AppService(tmp_path/'app',Runtime(),workspace=tmp_path)
    app.management=Management(app)
    row=app._new_session({'workspace':str(tmp_path)})
    app.state['sessions']=[row];app.state['selectedSessionId']=row['id']
    yield app
    await app.close()


async def configure(app,monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','fixture-secret')
    SettingsStore(app.data_dir).update(app.default_workspace,'global',lambda values:values.update(config={'providers':[
        {'id':'setup-name','module':'provider-openai','source':'fixture-source','config':{'api_key':'${OPENAI_API_KEY}','default_model':'fixture-model'}}]}))
    manager=SetupManager(app.data_dir,catalog=app.management.provider_catalog)
    shared=manager.catalog_key({'id':'setup-name'},app.default_workspace)
    row={'id':'mounted-name','module':'provider-openai','sharedCatalogKey':shared,'catalogKey':'fallback',
         'info':{'display_name':'Fixture'},'configSchema':{'fields':[]}}
    app.runtime.rows=[row]
    return manager,row


async def test_settings_models_and_metadata_are_reused_by_mounted_controls(app,monkeypatch):
    manager,row=await configure(app,monkeypatch)
    probes=[]
    async def probe(self,action,args,workspace):
        probes.append(action)
        return {'models':[{'id':'setup-model'}],'modelsProviderId':args['id'],
                'providerMetadata':{'module':'provider-openai','info':row['info'],'configSchema':row['configSchema']}}
    monkeypatch.setattr(SetupManager,'probe',probe)
    await manager.perform('providers.models',{'id':'setup-name','workspace':app.default_workspace})
    await manager.perform('providers.schema',{'id':'setup-name','workspace':app.default_workspace})
    sid=app.state['selectedSessionId']
    await app.management.command('runtime.control',{'sessionId':sid,'operation':'configuration.providerModels','args':{'instance':row['id']}},'read')
    assert app.runtime.calls=={}
    assert probes==['providers.models']
    assert app.state['runtimeControl'][sid]['configuration.providerModels']['models']==[{'id':'setup-model'}]
    assert 'modelCatalogs' not in app.state['runtimeControl'][sid]


@pytest.mark.parametrize('source', ['configured', 'static', 'simulated'])
async def test_mounted_publication_keeps_origin_on_ready_empty_and_failure(app,monkeypatch,source):
    manager,row=await configure(app,monkeypatch)
    sid=app.state['selectedSessionId']
    mode=['ready']
    async def control(session_id,operation,args):
        if operation=='configuration.providers':
            return {'providers':[row],'catalogRevision':'first'}
        if mode[0]=='error':raise ValueError('refresh failure')
        return {'models':[] if mode[0]=='empty' else [{'id':'Local/Alias'}],
                'modelsSupported':True,'modelsProvenance':source}
    monkeypatch.setattr(app.runtime,'control',control)
    await app.management.runtime_models(sid,row,'first')
    key=model_key(row['sharedCatalogKey'])
    before=app.management.provider_catalog.entry(key)
    assert before['provenance']==source and not before['stale']
    mode[0]='error'
    with pytest.raises(ValueError):
        await app.management.runtime_models(sid,row,'first',refresh=True)
    failed=app.management.provider_catalog.entry(key)
    assert failed['provenance']==source and failed['stale'] and failed['phase']=='error'
    assert failed['loadedAt']==before['loadedAt'] and failed['models']==before['models']
    mode[0]='empty'
    await app.management.runtime_models(sid,row,'first',refresh=True)
    empty=app.management.provider_catalog.entry(key)
    assert empty['provenance']==source and empty['models']==[] and not empty['stale']


async def test_runtime_refresh_updates_shared_models_and_schema(app,monkeypatch):
    manager,row=await configure(app,monkeypatch)
    async def unexpected(*args):raise AssertionError('Equivalent catalog must reuse mounted result')
    monkeypatch.setattr(SetupManager,'probe',unexpected)
    sid=app.state['selectedSessionId']
    await app.management.runtime_models(sid,row,'first')
    result=await manager.perform('providers.models',{'id':'setup-name','workspace':app.default_workspace})
    assert result['models']==[{'id':'mounted-model'}] and result['modelsProviderId']=='setup-name'
    assert (await manager.perform('providers.schema',{'id':'setup-name','workspace':app.default_workspace}))['providerMetadata']['info']==row['info']
    await app.management.command('runtime.control',{'sessionId':sid,'operation':'configuration.providerModels','args':{'instance':row['id'],'refresh':True}},'refresh')
    assert app.runtime.calls=={row['id']:2}
    assert app.management.provider_catalog.fresh(model_key(row['sharedCatalogKey']))


@pytest.mark.parametrize('source', ['configured', 'static', 'simulated', 'provider-api'])
async def test_mounted_listing_origin_and_connection_test_are_distinct(tmp_path, source):
    from types import SimpleNamespace
    from amplifier_web.runtime_controls import RuntimeControls
    calls=[]
    class Provider:
        name='fixture'
        def get_info(self):return {'config_fields':[]}
        def get_model_list_info(self):return {'version':1,'source':source}
        async def list_models(self):
            calls.append('list')
            return [{'id':'Exact/Alias'}]
    provider=Provider()
    coordinator=SimpleNamespace(config={'providers':[]},session_state={},
        get=lambda name: {'one':provider} if name=='providers' else
            SimpleNamespace(_select_provider=lambda providers:provider),
        get_capability=lambda name:None, register_capability=lambda *args:None)
    controls=RuntimeControls(SimpleNamespace(coordinator=coordinator,config={},session_id='fixture'),
        SimpleNamespace(generation=None,queued_inputs=0))
    try:
        metadata=await controls.perform('configuration.providers',{})
        row=metadata['providers'][0]
        assert row['supports']['models'] is True
        assert row['supports']['test'] is (source=='provider-api')
        result=await controls.perform('configuration.providerModels',{'instance':'one'})
        assert result['models']==[{'id':'Exact/Alias'}] and result['modelsProvenance']==source
        if source=='provider-api':
            assert (await controls.perform('configuration.providerTest',{'instance':'one'}))['reachable']
            assert calls==['list','list']
        else:
            with pytest.raises(ValueError,match='API model listing'):
                await controls.perform('configuration.providerTest',{'instance':'one'})
            assert calls==['list']
    finally:await controls.close()


async def test_explicit_mounted_reads_reuse_only_the_requested_catalog(app):
    now=[1000]
    cache=ProviderCatalog(ttl=10,clock=lambda:now[0])
    app.management.provider_catalog=cache
    rows=[{'id':str(i),'catalogKey':'key-'+str(i)} for i in range(14)]
    sid=app.state['selectedSessionId']
    await app.management.runtime_models(sid,rows[0],'first')
    assert app.runtime.calls=={'0':1}
    await app.management.runtime_models(sid,rows[0],'first')
    assert app.runtime.calls=={'0':1}
    now[0]+=11
    await app.management.runtime_models(sid,rows[0],'first')
    assert app.runtime.calls=={'0':2}


async def test_old_mount_completion_cannot_replace_new_mount_catalog(app):
    entered=asyncio.Event();finish=asyncio.Event();sid=app.state['selectedSessionId']
    original=app.runtime.control
    async def slow(sid,operation,args):
        if args.get('instance')=='old':entered.set();await finish.wait()
        return await original(sid,operation,args)
    app.runtime.control=slow
    old=asyncio.create_task(app.management.runtime_models(sid,{'id':'old','catalogKey':'old'},'old'))
    await entered.wait()
    await app.management.runtime_models(sid,{'id':'new','catalogKey':'new'},'new')
    finish.set();await old
    assert app.management.provider_catalog.peek(('mounted','new'))['models']==[{'id':'mounted-model'}]
    assert 'modelCatalogs' not in app.state.get('runtimeControl',{}).get(sid,{})


def test_configuration_identity_materializes_secrets_but_isolates_inputs(tmp_path,monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','private-first')
    reference={'api_key':'${OPENAI_API_KEY}','default_model':'model'}
    base=configuration_key(tmp_path,'provider-openai',reference,'source')
    assert base==configuration_key(tmp_path,'provider-openai',{'api_key':'private-first','default_model':'model'},'source')
    assert base==configuration_key(tmp_path,'provider-openai',{**reference,'priority':20},'source')
    assert base!=configuration_key(tmp_path/'other','provider-openai',reference,'source')
    assert base!=configuration_key(tmp_path,'provider-openai',reference,'other-source')
    assert base!=configuration_key(tmp_path,'provider-openai',{**reference,'base_url':'https://other.invalid'},'source')
    assert base!=configuration_key(tmp_path,'provider-openai',{**reference,'default_model':'other'},'source')
    monkeypatch.setenv('OPENAI_API_KEY','private-rotated')
    assert base!=configuration_key(tmp_path,'provider-openai',reference,'source')
    token=tmp_path/'credential.json';token.write_text('first')
    before=configuration_key(tmp_path,'provider-fixture',{'token_file_path':str(token)},'source')
    token.write_text('second')
    assert before!=configuration_key(tmp_path,'provider-fixture',{'token_file_path':str(token)},'source')
    assert 'private' not in base and len(base)==64
    assert configuration_key(tmp_path,'provider-fixture',{'access_token':'x'*8000},'source')
    assert configuration_key(tmp_path,'provider-fixture',{'token_file_path':'x'*8000},'source')


async def test_worker_advertises_digest_for_actual_mounted_config(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from amplifier_web.runtime_controls import RuntimeControls
    from amplifier_web.provider_catalog import mounted_catalog_keys
    monkeypatch.setenv('OPENAI_API_KEY','fixture-secret')
    home=tmp_path/'app'
    receipt=home/'updates/baseline-runtime';receipt.mkdir(parents=True)
    (receipt/'runtime-installed.json').write_text(json.dumps([{
        'name':'amplifier-module-provider-openai','version':'1.0',
        'directUrl':{'url':'https://github.com/microsoft/amplifier-module-provider-openai',
            'vcs_info':{'vcs':'git','requested_revision':'a'*40,'commit_id':'a'*40}}}]))
    (receipt/'runtime-sources.json').write_text(json.dumps({'amplifier-module-provider-openai':{
        'url':'https://github.com/microsoft/amplifier-module-provider-openai','ref':'main'}}))
    SettingsStore(home).update(tmp_path,'global',lambda settings:settings.update(config={'providers':[
        {'id':'setup','module':'provider-openai','config':{'api_key':'${OPENAI_API_KEY}','default_model':'fixture-model'}}]}))
    manager=SetupManager(home)
    config={'api_key':'fixture-secret','default_model':'fixture-model'}
    provider=SimpleNamespace(get_info=lambda:SimpleNamespace(defaults={'model':'fixture-model'}))
    providers={'mounted':provider}
    loop=SimpleNamespace(root_provider=None,_select_provider=lambda rows:provider)
    plan={'providers':[{'instance_id':'mounted','module':'provider-openai','config':config}]}
    sources={'mounted':'git+https://github.com/microsoft/amplifier-module-provider-openai@main'}
    capabilities={'web.provider_catalog':{'home':str(home),'workspace':str(tmp_path),'sources':sources,
        'keys':mounted_catalog_keys(home,tmp_path,plan['providers'],sources)}}
    coordinator=SimpleNamespace(config=plan,session_state={},
        get=lambda name:{'providers':providers,'orchestrator':loop}.get(name),
        get_capability=capabilities.get,register_capability=lambda *args:None)
    controls=RuntimeControls(SimpleNamespace(session_id='fixture',coordinator=coordinator,config=plan),SimpleNamespace(generation=None,queued_inputs=0))
    result=await controls.perform('configuration.providers')
    row=result['providers'][0]
    assert row['sharedCatalogKey']==manager.catalog_key({'id':'setup'},str(tmp_path))
    assert 'fixture-secret' not in str(result)
    previous=row['sharedCatalogKey']
    monkeypatch.setenv('OPENAI_API_KEY','rotated-secret')
    rotated=await controls.perform('configuration.providers')
    assert 'sharedCatalogKey' not in rotated['providers'][0]
    assert manager.catalog_key({'id':'setup'},str(tmp_path))!=previous
    monkeypatch.setenv('OPENAI_API_KEY','fixture-secret')
    (home/'updates').mkdir(parents=True,exist_ok=True)
    (home/'updates/active.json').write_text('{"current":"11111111111111111111111111111111"}')
    upgraded=await controls.perform('configuration.providers')
    assert 'sharedCatalogKey' not in upgraded['providers'][0]
    assert manager.catalog_key({'id':'setup'},str(tmp_path))!=previous
    await controls.close()


def test_implicit_unqualified_and_local_override_do_not_match_bundle_source(tmp_path):
    source='git+https://github.com/microsoft/amplifier-module-provider-openai@main'
    def key(source=None):return configuration_key(tmp_path,'provider-openai',{},source,home=tmp_path)
    assert key()!=key(source)
    receipt=tmp_path/'updates/baseline-runtime';receipt.mkdir(parents=True)
    (receipt/'runtime-installed.json').write_text(json.dumps([{
        'name':'amplifier-module-provider-openai','version':'1.0',
        'directUrl':{'url':'file:///fixture/local-provider','dir_info':{'editable':True}}}]))
    assert key()!=key(source)


async def test_delayed_models_reject_changed_identity_with_same_revision(app):
    sid=app.state['selectedSessionId'];entered=asyncio.Event();finish=asyncio.Event()
    app.runtime.rows=[{'id':'fixture','sharedCatalogKey':'before','catalogKey':'fallback'}]
    original=app.runtime.control
    async def delayed(sid,operation,args):
        if operation=='configuration.providerModels':entered.set();await finish.wait()
        return await original(sid,operation,args)
    app.runtime.control=delayed
    task=asyncio.create_task(app.management.command('runtime.control',{
        'sessionId':sid,'operation':'configuration.providerModels','args':{'instance':'fixture'}},'read'))
    await entered.wait();app.runtime.rows=[{'id':'fixture','catalogKey':'fallback'}];finish.set();await task
    assert 'configuration.providerModels' not in app.state.get('runtimeControl',{}).get(sid,{})


async def test_persistent_catalog_is_invalidated_by_runtime_receipt_change(tmp_path):
    home=tmp_path/'app';cache=ProviderCatalog(home/'catalog.json')
    identity=configuration_key(tmp_path,'provider-fixture',{},'same-branch',home=home)
    await cache.get(model_key(identity),lambda:asyncio.sleep(0,result={'models':[{'id':'old'}]}))
    receipt=home/'updates/baseline-runtime';receipt.mkdir(parents=True)
    (receipt/'runtime-installed.json').write_text('{"provider-fixture":"new-commit"}')
    newer=configuration_key(tmp_path,'provider-fixture',{},'same-branch',home=home)
    restarted=ProviderCatalog(home/'catalog.json')
    assert newer!=identity and restarted.peek(model_key(newer)) is None


async def test_rotated_credential_file_never_relabels_mounted_provider(tmp_path):
    from types import SimpleNamespace
    from amplifier_web.runtime_controls import RuntimeControls
    from amplifier_web.provider_catalog import mounted_catalog_keys
    token=tmp_path/'credential.json';token.write_text('old-token')
    row={'instance_id':'fixture','module':'provider-fixture','config':{'token_file_path':str(token)}}
    sources={'fixture':'source'}
    captured={'home':str(tmp_path),'workspace':str(tmp_path),'sources':sources,
              'keys':mounted_catalog_keys(tmp_path,tmp_path,[row],sources)}
    provider=SimpleNamespace(get_info=lambda:SimpleNamespace(defaults={}))
    coordinator=SimpleNamespace(config={'providers':[row]},session_state={},
        get=lambda name:{'providers':{'fixture':provider},'orchestrator':SimpleNamespace(root_provider=None,_select_provider=lambda rows:provider)}.get(name),
        get_capability=lambda name:captured if name=='web.provider_catalog' else None,register_capability=lambda *args:None)
    controls=RuntimeControls(SimpleNamespace(session_id='file-token',coordinator=coordinator,config=coordinator.config),SimpleNamespace(generation=None,queued_inputs=0))
    assert 'sharedCatalogKey' in (await controls.perform('configuration.providers'))['providers'][0]
    token.write_text('new-token')
    assert 'sharedCatalogKey' not in (await controls.perform('configuration.providers'))['providers'][0]
    await controls.close()


def test_connection_identity_shares_absolute_settings_but_isolates_nested_relative_files(tmp_path):
    one=tmp_path/'one';two=tmp_path/'two';one.mkdir();two.mkdir()
    config={'base_url':'https://fixture.invalid','api_key':'synthetic'}
    assert configuration_key(one,'provider-test',config)==configuration_key(two,'provider-test',config)
    relative={**config,'auth':{'token_file':'token.txt'}}
    (one/'token.txt').write_text('synthetic-one');(two/'token.txt').write_text('synthetic-two')
    first=configuration_key(one,'provider-test',relative)
    assert first!=configuration_key(two,'provider-test',relative)
    (one/'token.txt').write_text('changed-fixture')
    assert first!=configuration_key(one,'provider-test',relative)
