import asyncio
from collections import Counter
import pytest
import yaml
from amplifier_web.provider_catalog import ProviderCatalog
from amplifier_web.setup import SetupManager
from amplifier_web.host.config import write_private
from amplifier_web.session_files import amplifier_home


def settings(home,models=('first','second')):
    write_private(amplifier_home()/'settings.yaml',yaml.safe_dump({'config':{'providers':[{'id':str(i),'module':'provider-openai','config':{'base_url':'https://'+model+'.test','default_model':model,'api_key':'${CATALOG_TEST_KEY}'}} for i,model in enumerate(models)]}}))


async def test_catalog_single_flight_refresh_and_failure_recovery():
    cache=ProviderCatalog();calls=0
    async def load():
        nonlocal calls
        calls+=1;await asyncio.sleep(.01);return {'models':['one']}
    results=await asyncio.gather(*(cache.get('one',load) for _ in range(5)))
    assert calls==1
    results[0]['models'].append('changed')
    assert (await cache.get('one',load))['models']==['one']
    await cache.get('one',load,refresh=True);assert calls==2
    async def fail():raise ValueError('Unavailable')
    with pytest.raises(ValueError):await cache.get('failed',fail)
    with pytest.raises(ValueError):await cache.get('failed',load)
    assert (await cache.get('failed',load,refresh=True))['models']==['one']
    await cache.close()


async def test_only_changed_provider_configuration_is_refetched(tmp_path,monkeypatch):
    monkeypatch.setenv('CATALOG_TEST_KEY','first-private-value');settings(tmp_path)
    cache=ProviderCatalog();calls=Counter()
    async def probe(self,action,args,workspace):
        calls[args['id']]+=1
        return {'models':[{'id':args['id']}],'modelsProviderId':args['id']}
    monkeypatch.setattr(SetupManager,'probe',probe)
    async def get(identity):
        return await SetupManager(tmp_path,catalog=cache).perform('providers.models',{'id':identity,'workspace':str(tmp_path)})
    await asyncio.gather(get('0'),get('1'));await asyncio.gather(get('0'),get('1'))
    assert calls=={'0':1,'1':1}
    settings(tmp_path,('changed','second'))
    await asyncio.gather(get('0'),get('1'));assert calls=={'0':2,'1':1}
    monkeypatch.setenv('CATALOG_TEST_KEY','rotated-private-value')
    await asyncio.gather(get('0'),get('1'));assert calls=={'0':3,'1':2}
    # Credentials and their fingerprints never become public model results.
    assert 'private-value' not in str(await get('0'))
    await cache.close()


def catalogs_ready(entries,expected):
    errors={identity:row for identity,row in entries.items() if row.get('phase')=='error'}
    assert not errors,{'catalogErrors':errors,'expectedKeys':expected}
    return set(entries)==set(expected) and all(
        entries[identity].get('sharedCatalogKey')==key and entries[identity]['phase']=='ready'
        for identity,key in expected.items())


async def wait_catalogs(service,expected):
    for _ in range(100):
        entries=service.state.get('setup',{}).get('providerCatalogs',{})
        if catalogs_ready(entries,expected):return
        await asyncio.sleep(.01)
    pytest.fail(f'Catalogs did not finish for {expected}: {entries}')


async def test_startup_warms_all_providers_and_reuses_catalogs(tmp_path,monkeypatch):
    from amplifier_web.server import create_app
    monkeypatch.setenv('CATALOG_TEST_KEY','fixture');settings(tmp_path)
    calls=Counter()
    async def probe(self,action,args,workspace):
        calls[args['id']]+=1
        await asyncio.sleep(.005)
        return {'models':[{'id':'model-'+args['id']}],'modelsProviderId':args['id'],'providerMetadata':{'module':'provider-openai','info':{}}}
    monkeypatch.setattr(SetupManager,'probe',probe)
    class Runtime:
        async def close(self):pass
    app=await create_app(tmp_path,workspace=tmp_path,runtime=Runtime(),voice=False,background_updates=False)
    service=app['service']
    try:
        manager=SetupManager(tmp_path,catalog=service.management.provider_catalog)
        old={identity:manager.catalog_key({'id':identity},str(tmp_path)) for identity in ('0','1')}
        await wait_catalogs(service,old);assert calls=={'0':1,'1':1}
        await service.management.command('providers.list',{})
        await asyncio.sleep(.03);assert calls=={'0':1,'1':1}
        save_id='catalog-save'
        await service.management.command('providers.save',{'id':'0','module':'provider-openai','config':{'base_url':'https://changed.test'}},command_id=save_id)
        receipt=service.state['managementResults'][save_id]
        assert receipt=={'phase':'ready','error':None},receipt
        expected={identity:manager.catalog_key({'id':identity},str(tmp_path)) for identity in old}
        assert expected['0']!=old['0'] and expected['1']==old['1']
        # Save acknowledgement can precede its background refresh. Old ready
        # rows are not evidence for the newly saved provider configuration.
        await wait_catalogs(service,expected)
        assert calls=={'0':2,'1':1}
    finally:await service.close()


@pytest.mark.parametrize('refresh_fails',[False,True],ids=['delayed-ready','refresh-error'])
async def test_catalog_wait_requires_new_identity_and_rejects_refresh_error(tmp_path,monkeypatch,refresh_fails):
    from amplifier_web.server import create_app
    from amplifier_web.management import Management
    monkeypatch.setenv('CATALOG_TEST_KEY','fixture');settings(tmp_path)
    calls=Counter();hold_refresh=False;warm_task=None
    warm_entered,warm_release=asyncio.Event(),asyncio.Event()
    probe_entered,probe_release=asyncio.Event(),asyncio.Event()
    original=Management.warm_providers
    async def warm(self,manager,workspace,**kwargs):
        nonlocal warm_task
        if hold_refresh:
            warm_task=asyncio.current_task();warm_entered.set()
            await warm_release.wait()
        return await original(self,manager,workspace,**kwargs)
    monkeypatch.setattr(Management,'warm_providers',warm)
    async def probe(self,action,args,workspace):
        calls[args['id']]+=1
        if args['id']=='0' and calls['0']==2:
            probe_entered.set();await probe_release.wait()
            if refresh_fails:raise ValueError('Controlled catalog refresh failure')
        return {'models':[{'id':'model-'+args['id']}],'modelsProviderId':args['id']}
    monkeypatch.setattr(SetupManager,'probe',probe)
    class Runtime:
        async def close(self):pass
    app=await create_app(tmp_path,workspace=tmp_path,runtime=Runtime(),voice=False,background_updates=False)
    service=app['service']
    try:
        manager=SetupManager(tmp_path,catalog=service.management.provider_catalog)
        old={identity:manager.catalog_key({'id':identity},str(tmp_path)) for identity in ('0','1')}
        await wait_catalogs(service,old);assert calls=={'0':1,'1':1}
        hold_refresh=True
        await service.management.command('providers.save',{'id':'0','module':'provider-openai','config':{'base_url':'https://changed.test'}},command_id='delayed-save')
        receipt=service.state['managementResults']['delayed-save']
        assert receipt=={'phase':'ready','error':None},receipt
        expected={identity:manager.catalog_key({'id':identity},str(tmp_path)) for identity in old}
        assert expected['0']!=old['0'] and expected['1']==old['1']
        await asyncio.wait_for(warm_entered.wait(),1)
        entries=service.state['setup']['providerCatalogs']
        # The former generic-ready predicate passes here despite stale identity.
        assert len(entries)==2 and all(row['phase']=='ready' for row in entries.values())
        assert catalogs_ready(entries,old) and not catalogs_ready(entries,expected)
        assert calls=={'0':1,'1':1}
        warm_release.set();await asyncio.wait_for(probe_entered.wait(),1)
        entries=service.state['setup']['providerCatalogs']
        assert entries['0']['sharedCatalogKey']==expected['0'] and entries['0']['phase']=='working'
        assert entries['1']['sharedCatalogKey']==old['1'] and entries['1']['phase']=='ready'
        assert calls=={'0':2,'1':1} and not catalogs_ready(entries,expected)
        probe_release.set();await asyncio.wait_for(asyncio.shield(warm_task),1)
        if refresh_fails:
            with pytest.raises(AssertionError,match='catalogErrors'):
                await wait_catalogs(service,expected)
            assert service.state['setup']['providerCatalogs']['0']['phase']=='error'
        else:
            await wait_catalogs(service,expected)
        assert calls=={'0':2,'1':1}
    finally:
        warm_release.set();probe_release.set();await service.close()


async def test_remount_reuses_unchanged_provider_catalogs(tmp_path):
    from amplifier_web.service import AppService
    from amplifier_web.management import Management
    calls=Counter()
    class Runtime:
        async def close(self):pass
        async def control(self,sid,operation,args):
            calls[args['instance']]+=1
            return {'models':[{'id':args['instance']+'-model'}]}
    service=AppService(tmp_path,Runtime(),workspace=tmp_path);service.management=Management(service)
    await service.dispatch('session.create',{})
    sid=service.state['selectedSessionId']
    try:
        for row in [{'id':'one','catalogKey':'one-config'},{'id':'two','catalogKey':'two-config'}]:
            await service.management.runtime_models(sid,row,'first')
        assert calls=={'one':1,'two':1}
        for row in [{'id':'one','catalogKey':'changed-config'},{'id':'two','catalogKey':'two-config'}]:
            await service.management.runtime_models(sid,row,'second')
        assert calls=={'one':2,'two':1}
        assert 'modelCatalogs' not in service.state.get('runtimeControl',{}).get(sid,{})
    finally:await service.close()


async def test_changed_config_during_discovery_never_reuses_old_result(tmp_path,monkeypatch):
    settings(tmp_path);cache=ProviderCatalog();entered=asyncio.Event();finish=asyncio.Event()
    async def probe(self,action,args,workspace):
        entered.set();await finish.wait()
        return {'models':[{'id':'stale'}],'modelsProviderId':args['id']}
    monkeypatch.setattr(SetupManager,'probe',probe)
    manager=SetupManager(tmp_path,catalog=cache)
    old=('providers.models',manager.catalog_key({'id':'0'},str(tmp_path)))
    pending=asyncio.create_task(manager.perform('providers.models',{'id':'0','workspace':str(tmp_path)}))
    await entered.wait();settings(tmp_path,('changed','second'));finish.set()
    with pytest.raises(ValueError,match='configuration changed'):await pending
    assert old not in cache.entries
    await cache.close()


async def test_persistent_ttl_failed_refresh_and_private_file(tmp_path):
    now=[1000.0];path=tmp_path/'cache/catalog.json';calls=0
    async def load():
        nonlocal calls
        calls+=1
        return {'models':[{'id':'saved'}]}
    cache=ProviderCatalog(path,ttl=60,retry_after=60,clock=lambda:now[0])
    key=('providers.models','private-configuration-digest')
    await cache.get(key,load)
    assert path.stat().st_mode & 0o777 == 0o600
    restarted=ProviderCatalog(path,ttl=60,retry_after=60,clock=lambda:now[0])
    assert restarted.peek(key)['models'][0]['id']=='saved'
    await restarted.get(key,load);assert calls==1
    assert restarted.peek(('providers.models','different-account')) is None
    now[0]+=61
    assert not restarted.fresh(key)
    async def fail():raise ValueError('temporarily unavailable')
    with pytest.raises(ValueError):await restarted.get(key,fail)
    assert restarted.peek(key)['models'][0]['id']=='saved'
    now[0]+=61
    await restarted.get(key,load);assert calls==2
    assert restarted.fresh(key)
    path.write_text('{broken')
    assert ProviderCatalog(path).peek(key) is None


async def test_failed_refresh_inside_ttl_is_stale_and_retains_success_timestamp(tmp_path):
    now=[1000.0];key=('providers.models','identity')
    cache=ProviderCatalog(tmp_path/'catalog.json',ttl=60,clock=lambda:now[0])
    cache.put(key,{'models':[{'id':'prior'}],'modelsSupported':True,'modelsProvenance':'provider-api'})
    now[0]+=1
    async def fail():raise TimeoutError('synthetic')
    with pytest.raises(TimeoutError):await cache.get(key,fail,refresh=True)
    assert not cache.fresh(key)
    entry=cache.entry(key)
    assert entry['stale'] and entry['phase']=='error'
    assert entry['loadedAt']==1000 and entry['failedAt']==1001
    assert entry['models']==[{'id':'prior'}]
    restarted=ProviderCatalog(cache.path,ttl=60,clock=lambda:now[0])
    assert restarted.entry(key)==entry
    cache.put(key,{'models':[],'modelsSupported':True,'modelsProvenance':'provider-api'})
    assert cache.fresh(key) and not cache.entry(key)['stale']
    assert cache.entry(key)['models']==[]


async def test_provider_priority_does_not_invalidate_models(tmp_path,monkeypatch):
    settings(tmp_path)
    manager=SetupManager(tmp_path,catalog=ProviderCatalog())
    before=manager.catalog_key({'id':'0'},str(tmp_path))
    document=yaml.safe_load((amplifier_home()/'settings.yaml').read_text())
    document['config']['providers'][0]['config']['priority']=99
    write_private(amplifier_home()/'settings.yaml',yaml.safe_dump(document))
    assert manager.catalog_key({'id':'0'},str(tmp_path))==before
    document['config']['providers'][0]['config']['base_url']='https://other.example/v1'
    write_private(amplifier_home()/'settings.yaml',yaml.safe_dump(document))
    assert manager.catalog_key({'id':'0'},str(tmp_path))!=before


@pytest.mark.parametrize('field', ['model', 'default_model'])
def test_selection_defaults_change_catalog_not_account_identity(tmp_path, monkeypatch, field):
    from amplifier_web.provider_catalog import configuration_key, connection_key
    config={'api_key':'synthetic','base_url':'https://fixture.invalid'}
    before=configuration_key(tmp_path,'provider-fixture',config)
    assert before==configuration_key(tmp_path,'provider-fixture',{
        **config,'reasoning_effort':'high','priority':10})
    updated={**config,field:'changed'}
    assert before!=configuration_key(tmp_path,'provider-fixture',updated)
    assert connection_key(tmp_path,'provider-fixture',config)==connection_key(
        tmp_path,'provider-fixture',updated)
    indirect={**config,field:'${LIST_MODEL}'}
    monkeypatch.setenv('LIST_MODEL','changed')
    assert configuration_key(tmp_path,'provider-fixture',indirect)==configuration_key(
        tmp_path,'provider-fixture',updated)
    monkeypatch.setenv('LIST_MODEL','other')
    assert configuration_key(tmp_path,'provider-fixture',indirect)!=configuration_key(
        tmp_path,'provider-fixture',updated)
    assert before!=configuration_key(tmp_path,'provider-fixture',{**config,'api_key':'rotated'})
