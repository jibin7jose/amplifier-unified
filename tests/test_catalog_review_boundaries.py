"""Saved account bindings, host remedies and bounded canonical discovery."""
import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from amplifier_web.management import Management
from amplifier_web.preferences import SettingsStore
from amplifier_web.provider_catalog import (ProviderCatalog, configuration_key, connection_key,
                                           mounted_catalog_keys, public_failure)
from amplifier_web.runtime_controls import RuntimeControls
from amplifier_web.service import AppService
from amplifier_web.setup import SetupManager


def token_record(mode):
    return ({'auth_mode':'oauth', 'account_id':'synthetic-account-a',
             'expires_at':'2020-01-01T00:00:00+00:00'} if mode=='chatgpt_codex' else
            {'auth_mode':'chatgpt_plan','subject':'synthetic-account-a','client_id':'fixture-client',
             'expires_at':1, 'scopes':['model.request']}) | {
             'access_token':'synthetic-access-old','refresh_token':'synthetic-refresh-old'}


def configure(tmp_path, monkeypatch, mode='chatgpt_codex', count=1):
    monkeypatch.setenv('AMPLIFIER_HOME',str(tmp_path/'shared'))
    monkeypatch.setenv('HOME',str(tmp_path))
    home=tmp_path/'app'; path=tmp_path/'credentials.json'
    path.write_text(json.dumps(token_record(mode)))
    row={'id':'one','module':'provider-openai-chatgpt','source':'fixture-source',
         'config':{'auth_mode':mode,'token_file_path':str(path)}}
    SettingsStore(home).update(tmp_path,'global',lambda values:values.update(config={'providers':[
        row,*[{'id':f'unrelated-{n}','module':f'provider-fixture-{n}','config':{}} for n in range(count-1)]]}))
    return home,path,row


@pytest.mark.parametrize('mode',['chatgpt_codex','chatgpt_plan'])
async def test_saved_and_mounted_selection_survives_same_account_rotation(tmp_path,monkeypatch,mode):
    from amplifier_web.next_message_configuration import save,prepare
    home,path,row=configure(tmp_path,monkeypatch,mode)
    app=AppService(home,workspace=tmp_path); app.management=Management(app)
    await app.dispatch('session.create',{})
    session=app._session();session['status']='idle'
    selected={'instance':'one','model':'hidden/exact-id'}
    save(app,{'sessionId':session['id'],'expectedRevision':0,'selection':selected})
    expected=session['modelConnectionKey']
    sources={'one':'fixture-source'}; plan={'providers':[row]}
    captured={'home':str(home),'workspace':str(tmp_path),'sources':sources,
              'connectionKeys':mounted_catalog_keys(home,tmp_path,[row],sources,binding=True)}
    coordinator=SimpleNamespace(config=plan,session_state={},
        get=lambda name:{},get_capability=lambda name:captured if name=='web.provider_catalog' else None,
        register_capability=lambda *args:None)
    controls=RuntimeControls(SimpleNamespace(coordinator=coordinator,config=plan,session_id='fixture'),
                             SimpleNamespace(generation=None,queued_inputs=0))
    old_catalog=configuration_key(tmp_path,row['module'],row['config'])
    async def probe(self,action,args,workspace):
        return {'models':[], 'modelsSupported':True,'modelsProvenance':'provider-api'}
    monkeypatch.setattr(SetupManager,'probe',probe)
    tokens=json.loads(path.read_text());tokens.update(access_token='synthetic-access-new',
        refresh_token='synthetic-refresh-new',expires_at='2030-01-01T00:00:00+00:00' if mode=='chatgpt_codex' else 2000000000)
    path.write_text(json.dumps(tokens))
    try:
        resolved,guard=await prepare(app,copy.deepcopy(session));guard()
        assert resolved['modelConnectionKey']==expected
        assert resolved['selection']==selected
        assert configuration_key(tmp_path,row['module'],row['config'])!=old_catalog
        assert (await controls.perform('provider.verifyConnection',{'instance':'one','expectedConnectionKey':expected}))['verified']
        # New process/mount uses the same saved intent without adopting a new account.
        assert mounted_catalog_keys(home,tmp_path,[row],sources,binding=True)['one']==expected
        for contents in (json.dumps({**tokens,('account_id' if mode=='chatgpt_codex' else 'subject'):'different-account'}),
                         '{malformed', json.dumps({**tokens,'access_token':''}), None):
            if contents is None:path.unlink()
            else:path.write_text(contents)
            with pytest.raises(ValueError,match='credentials changed|mounted connection differs'):
                await controls.perform('provider.verifyConnection',{'instance':'one','expectedConnectionKey':expected})
            with pytest.raises(Exception,match='Next message settings were not applied'):
                await prepare(app,copy.deepcopy(session))
    finally:
        await controls.close();await app.close()


async def test_provider_owned_rotation_requeries_new_generation_without_stale_publication(tmp_path,monkeypatch):
    home,path,row=configure(tmp_path,monkeypatch)
    manager=SetupManager(home,catalog=ProviderCatalog());calls=[]
    old=manager.catalog_key({'id':'one'},tmp_path)
    async def probe(self,action,args,workspace):
        calls.append(self.catalog_key(args,workspace))
        if len(calls)==1:
            tokens=json.loads(path.read_text());tokens.update(access_token='new',refresh_token='rotated')
            path.write_text(json.dumps(tokens))
        return {'models':[{'id':str(len(calls))}],'modelsSupported':True,'modelsProvenance':'provider-api'}
    monkeypatch.setattr(SetupManager,'probe',probe)
    result=await manager.discover_catalog(tmp_path,instance='one')
    entry=next(iter(result['catalogs'].values()))
    assert len(calls)==2 and calls[0]==old and calls[1]!=old
    assert entry['models']==[{'id':'2'}] and entry['phase']=='ready'
    assert manager.catalog.peek(('providers.models',old)) is None


@pytest.mark.parametrize('count',[2,40])
async def test_send_preparation_does_not_query_unrelated_connections(tmp_path,monkeypatch,count):
    from amplifier_web.next_message_configuration import save,prepare
    home,path,row=configure(tmp_path,monkeypatch,count=count)
    app=AppService(home,workspace=tmp_path);app.management=Management(app)
    await app.dispatch('session.create',{});session=app._session();session['status']='idle'
    save(app,{'sessionId':session['id'],'expectedRevision':0,'selection':{'instance':'one','model':'manual-hidden'}})
    calls=[]
    async def probe(self,action,args,workspace):
        calls.append(args['id'])
        if args['id']!='one':raise AssertionError('Unrelated slow/failing provider was queried')
        return {'models':[], 'modelsSupported':False}
    monkeypatch.setattr(SetupManager,'probe',probe)
    try:
        result,guard=await prepare(app,copy.deepcopy(session));guard()
        assert result['selection']['model']=='manual-hidden'
        assert calls==['one']
        await prepare(app,copy.deepcopy(session))
        assert calls==['one']  # Warm send does not list again.
    finally:await app.close()


@pytest.mark.parametrize('state',['unsaved','disabled'])
async def test_safe_host_setup_failure_preserves_remedy(tmp_path,monkeypatch,state):
    home,path,row=configure(tmp_path,monkeypatch)
    if state=='disabled':
        SettingsStore(home).update(tmp_path,'global',lambda values:values['config']['providers'][0].update(enabled=False))
    manager=SetupManager(home)
    with pytest.raises(ValueError) as caught:
        await manager.cached_probe('providers.test',{'id':'absent' if state=='unsaved' else 'one'},tmp_path)
    message,diagnostic=public_failure(caught.value)
    assert ('Save this provider' if state=='unsaved' else 'Enable it') in message
    assert diagnostic.get('code')==state
    assert 'Refresh this connection to retry' not in message


async def test_targeted_picker_refresh_keeps_other_connections_visible(tmp_path,monkeypatch):
    home,path,row=configure(tmp_path,monkeypatch,count=3)
    manager=SetupManager(home);calls=[]
    async def probe(self,action,args,workspace):
        calls.append(args['id'])
        return {'models':[{'id':args['id']+'-model'}],'modelsSupported':True}
    monkeypatch.setattr(SetupManager,'probe',probe)
    before=await manager.discover_catalog(tmp_path)
    calls.clear()
    refreshed=await manager.discover_catalog(tmp_path,refresh=True,instance='one')
    assert [row['id'] for row in refreshed['providers']]==[row['id'] for row in before['providers']]
    assert calls==['one']
    assert set(refreshed['catalogs'])==set(before['catalogs'])


@pytest.mark.parametrize('source',['configured','static','simulated'])
async def test_local_catalog_test_returns_not_verifiable_without_listing(monkeypatch,source):
    from amplifier_web import provider_probe
    calls=[]
    class Provider:
        def __init__(self,config=None):pass
        def get_info(self):return {'defaults':{},'config_fields':[]}
        def get_model_list_info(self):return {'version':1,'source':source}
        async def list_models(self):calls.append('list');return [{'id':'exact/local'}]
    monkeypatch.setattr(provider_probe,'provider_class',lambda name:Provider)
    request={'action':'providers.models','module':'provider-fixture','config':{}}
    result=await provider_probe.query(request)
    assert result['supports']['test'] is False and result['models']==[{'id':'exact/local'}]
    with pytest.raises(ValueError) as caught:
        await provider_probe.query({**request,'action':'providers.test'})
    message,diagnostic=public_failure(caught.value)
    assert diagnostic.get('code')=='not-verifiable' and 'do not verify connectivity' in message
    assert calls==['list']


def test_host_timeout_and_provider_secrets_are_distinct():
    from amplifier_web.probe_diagnostics import HostProbeFailure
    message,diagnostic=public_failure(HostProbeFailure('timeout'))
    assert '90 seconds' in message and diagnostic['code']=='timeout'
    message,diagnostic=public_failure(ValueError('secret-value https://secret.invalid'))
    assert 'secret' not in message and 'secret' not in str(diagnostic)


async def test_provider_inventory_and_manual_selection_do_not_warm_catalogs(tmp_path):
    calls=[]
    class Runtime:
        async def start(self,*args,**kwargs):pass
        async def close(self):pass
        async def control(self,sid,operation,args,**kwargs):
            calls.append(operation)
            if operation=='provider.select':return {'selection':args}
            if operation=='configuration.providers':return {'providers':[{'id':'one','info':{},'configSchema':{}}]}
            if operation=='bundle.preview':return {'providers':[], 'bundle':'work'}
            pytest.fail('Unrequested provider listing: '+operation)
    app=AppService(tmp_path/'app',Runtime(),workspace=tmp_path);app.management=Management(app)
    await app.dispatch('session.create',{});sid=app.state['selectedSessionId']
    try:
        for operation,args in [('configuration.providers',{}),('provider.select',{'instance':'one','model':'hidden/manual'})]:
            await app.management.command('runtime.control',{'sessionId':sid,'operation':operation,'args':args},operation)
        await asyncio.sleep(.05)
        assert 'configuration.providerModels' not in calls
        assert 'modelCatalogs' not in app.state['runtimeControl'][sid]
        assert not app.management.provider_catalog.entries
    finally:await app.close()


def test_derived_provider_override_matches_host_and_worker(tmp_path,monkeypatch):
    from amplifier_foundation import Bundle
    from amplifier_web.host.config import load_config
    from amplifier_web.host.session import _apply_settings
    monkeypatch.setenv('AMPLIFIER_HOME',str(tmp_path/'shared'))
    home=tmp_path/'app'
    SettingsStore(home).update(tmp_path,'global',lambda values:values.update(
        config={'providers':[{'module':'provider-fixture','config':{'base_url':'https://old.invalid'}}]},
        overrides={'fixture':{'config':{'base_url':'https://new.invalid','model_filters':{}}}}))
    config=load_config(tmp_path,home=home)
    bundle=Bundle.from_dict({'bundle':{'name':'test'},'providers':[]})
    _apply_settings(bundle,config)
    assert bundle.providers[0]['config']==config.configured_connections[0]['config']
