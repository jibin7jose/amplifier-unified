import asyncio
from copy import deepcopy
import pytest
from amplifier_web.service import AppService
from amplifier_web.management import Management
from amplifier_web.conversation_models import browse, select, request_browse


@pytest.fixture
async def app(tmp_path):
    service=AppService(tmp_path/'app',workspace=tmp_path)
    await service.dispatch('session.create',{'title':'Fixture'})
    service.management=Management(service)
    yield service
    await service.close()


async def test_catalog_browse_uses_shared_models_without_loading_a_conversation(app,monkeypatch):
    from amplifier_web import draft_defaults
    async def resolve(*args,**kwargs):
        return {'providers':[{'id':'one','sharedCatalogKey':'account-one'}],
                'effective':{'instance':'one','model':'model','effort':'high'},
                'catalogs':{'account-one':{'phase':'ready','models':[{'id':'model'}],
                    'metadata':{'info':{},'configSchema':{'fields':[]}}}}}
    monkeypatch.setattr(draft_defaults,'resolve_defaults',resolve)
    session=app._session(app.state['selectedSessionId'])
    before=deepcopy(session)
    await browse(app.management,session)
    assert session==before
    control=app.state['runtimeControl'][session['id']]['configuration.catalog']
    assert control['effective']['effort']=='high'
    assert 'models' not in control['providers'][0]
    assert app.state['modelCatalogs']['account-one']['models']==[{'id':'model'}]
    assert app.runtime is None


async def test_selection_is_saved_without_mounting_or_mutating_context(app):
    session=app._session(app.state['selectedSessionId'])
    wanted={'instance':'one','model':'new','effort':'high'}
    await select(app.management,session,wanted)
    assert session['selection']==session['pendingModelSelection']==wanted
    assert session['status']=='idle' and not session.get('configurationBusy')
    assert app.runtime is None
    session['status']='working'
    with pytest.raises(ValueError,match='Finish active work'):
        await select(app.management,session,{**wanted,'model':'different'})
    assert session['selection']==wanted


async def test_catalog_discovery_does_not_hold_the_command_queue(app,monkeypatch):
    from amplifier_web import draft_defaults
    started=asyncio.Event();release=asyncio.Event();calls=[]
    async def resolve(*args,**kwargs):
        calls.append(1);started.set();await release.wait()
        return {'providers':[],'catalogs':{}}
    monkeypatch.setattr(draft_defaults,'resolve_defaults',resolve)
    session=app._session(app.state['selectedSessionId'])
    await request_browse(app.management,session)
    await asyncio.wait_for(started.wait(),1)
    await request_browse(app.management,session)
    await asyncio.wait_for(app.dispatch('session.pin',{'id':session['id'],'pinned':True}),1)
    assert len(calls)==1
    release.set()
    await asyncio.gather(*app.management.conversation_catalog_requests.values())


async def test_mounted_connection_cache_miss_resolves_and_hit_does_not_probe(app,monkeypatch):
    import yaml
    from amplifier_web.host.config import write_private
    from amplifier_web.session_files import amplifier_home
    from amplifier_web.setup import SetupManager
    session=app._session(app.state['selectedSessionId'])
    session['runtimeReport']={'effective_selection':{'instance':'one','model':'model','effort':'high'},
        'provider_choices':[{'id':'one','sharedCatalogKey':'account-one','model':'model','effort':'high'}]}
    calls=[]
    write_private(amplifier_home()/'settings.yaml', yaml.safe_dump({'config':{'providers':[
        {'id':'one','module':'provider-fixture','config':{}}]}}))
    async def probe(*args,**kwargs):
        calls.append(kwargs)
        return {'models':[{'id':'model'}], 'modelsSupported':True,
                'providerMetadata':{'info':{},'configSchema':{'fields':[]}}}
    monkeypatch.setattr(SetupManager,'probe',probe)
    await browse(app.management,session)
    assert len(calls)==1
    def no_write(*args):raise AssertionError('Cache hits must not rewrite catalog files')
    monkeypatch.setattr(app.management.provider_catalog,'put',no_write)
    await browse(app.management,session)
    assert len(calls)==1


async def test_next_send_applies_pending_selection_before_message_and_preserves_failure():
    from amplifier_web.runtime import RuntimeManager, RuntimeStartupError
    from unittest.mock import AsyncMock
    manager=RuntimeManager()
    wanted={'instance':'one','model':'new','effort':'high'}
    session={'id':'chat','pendingModelSelection':wanted}
    order=[]
    async def start(config,emit):
        assert config['selection']==wanted and config['replaceSavedSelection'] is True
        order.append('start')
    async def control(sid,operation,args):
        assert operation=='provider.select' and args==wanted
        order.append('select')
    async def emit(kind,data):
        assert kind=='runtime.modelSelectionApplied' and data['selection']==wanted
        order.append('applied')
    manager.start=start;manager.control=control
    await manager._start_for_input(session,emit)
    assert order==['start','select','applied']
    manager.control=AsyncMock(side_effect=ValueError('Unsupported model'))
    acknowledged=AsyncMock()
    with pytest.raises(RuntimeStartupError):await manager._start_for_input(session,acknowledged)
    acknowledged.assert_not_awaited()
    assert session['pendingModelSelection']==wanted


async def test_configuration_change_recomposes_before_reusing_old_mounted_catalog(app,monkeypatch):
    from amplifier_web import draft_defaults
    from amplifier_web.provider_catalog import model_key
    session=app._session(app.state['selectedSessionId'])
    session['runtimeReport']={'provider_choices':[{'id':'old','sharedCatalogKey':'old'}]}
    app.management.provider_catalog.put(model_key('old'),{'models':[], 'providerMetadata':{'info':{}}})
    app.state['configurationRevision']=1
    async def resolve(*args,**kwargs):return {'providers':[{'id':'new'}],'catalogs':{}}
    monkeypatch.setattr(draft_defaults,'resolve_defaults',resolve)
    await browse(app.management,session)
    assert app.state['runtimeControl'][session['id']]['configuration.catalog']['providers']==[{'id':'new'}]
    await browse(app.management,session)
    assert app.state['runtimeControl'][session['id']]['configuration.catalog']['providers']==[{'id':'new'}]


async def test_inflight_catalog_keeps_new_revision_and_refresh_demand(app,monkeypatch):
    from amplifier_web import draft_defaults
    entered=asyncio.Event();finish=asyncio.Event();calls=[]
    async def resolve(*args,**kwargs):
        if not kwargs.get('catalog'):
            return {'effective':{'instance':'resolved','model':'default'}}
        calls.append((app.state.get('configurationRevision',0),kwargs['refresh']))
        if len(calls)==1:
            entered.set()
            await finish.wait()
        return {'providers':[{'id':str(calls[-1][0])}],'catalogs':{}}
    monkeypatch.setattr(draft_defaults,'resolve_defaults',resolve)
    session=app._session()
    await request_browse(app.management,session)
    await asyncio.wait_for(entered.wait(),1)
    app.state['configurationRevision']=1
    await request_browse(app.management,session,refresh=True)
    finish.set()
    await asyncio.wait_for(asyncio.gather(*app.management.conversation_catalog_requests.values()),2)
    assert calls==[(0,False),(1,True)]
    result=app.state['runtimeControl'][session['id']]['configuration.catalog']
    assert result['configurationRevision']==1 and result['providers']==[{'id':'1'}]


async def test_pending_refresh_targets_survive_other_refresh_and_browse(app, monkeypatch):
    from amplifier_web import conversation_models
    entered,release=asyncio.Event(),asyncio.Event();calls=[]
    async def held(management,session,*,refresh=False,instance=None):
        calls.append((refresh,instance))
        if len(calls)==1:
            entered.set();await release.wait()
    monkeypatch.setattr(conversation_models,'browse',held)
    await request_browse(app.management,app._session())
    await entered.wait()
    await request_browse(app.management,app._session(),refresh=True,instance='one')
    await request_browse(app.management,app._session(),refresh=True,instance='two')
    await request_browse(app.management,app._session())
    await request_browse(app.management,app._session(),refresh=True,instance='one')
    release.set()
    await asyncio.gather(*app.management.conversation_catalog_requests.values())
    assert calls==[(False,None),(True,'one'),(True,'two')]


async def test_unpinned_display_and_refresh_never_prepare_bundle_defaults(app, monkeypatch):
    from amplifier_web import draft_defaults
    calls=[]
    async def resolve(*args,**kwargs):
        calls.append(kwargs.get('catalog',False))
        if kwargs.get('catalog'):
            return {'providers':[{'id':'one','sharedCatalogKey':'key'}],
                'catalogs':{'key':{'phase':'ready','models':[{'id':'real'}],'supported':True}}}
        raise AssertionError('Ordinary model display must not prepare the bundle')
    monkeypatch.setattr(draft_defaults,'resolve_defaults',resolve)
    await browse(app.management,app._session())
    await browse(app.management,app._session(),refresh=True)
    await browse(app.management,app._session())
    result=app.state['runtimeControl'][app._session()['id']]['configuration.catalog']
    assert result['effective']=={}
    assert 'resolved when you send' in result['defaultError']
    assert result['pinned'] is False
    assert app.state['modelCatalogs']['key']['models']==[{'id':'real'}]
    assert calls==[True,True,True]
    assert not app._session().get('selection')
