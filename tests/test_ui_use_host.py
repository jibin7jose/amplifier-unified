import asyncio
from types import SimpleNamespace
import pytest
from aiohttp import web
from amplifier_web.ui_use import UIUse

class Connections:
    def listing(self):return []

@pytest.fixture
def host(tmp_path):
    service=SimpleNamespace(data_dir=tmp_path,_session=lambda sid:{'id':sid,'workspace':'/workspace/'+sid[0]})
    return UIUse(service,Connections())


async def test_browser_readiness_is_coalesced_off_the_service_event_loop(host, monkeypatch, tmp_path):
    import threading
    from amplifier_web import app_features
    binary = tmp_path / 'browser'
    binary.write_text('fixture'); binary.chmod(0o700)
    threads = []
    def probe(home):
        threads.append(threading.get_ident())
        return str(binary)
    monkeypatch.setattr(app_features, 'managed_browser_executable', probe)
    monkeypatch.setattr('amplifier_web.ui_use.importlib.util.find_spec', lambda _: True)
    await asyncio.gather(host.refresh_managed(), host.refresh_managed())
    await host.refresh_managed()
    assert len(threads) == 1 and threads[0] != threading.get_ident()
    assert host.managed_available()
    binary.unlink()
    assert not host.managed_available()

def test_preference_precedence_and_pinned_session(host):
    host.configure('a1',{'scope':'global','selection':'standard'})
    host.configure('a1',{'scope':'workspace','selection':'configured'})
    host.configure('a1',{'scope':'chat','selection':'standard'})
    assert host.effective('a1')['selection']=='standard'
    assert host.effective('a2')['selection']=='configured'
    assert host.effective('b1')['selection']=='standard'
    host.active['a1']='previous-device'
    assert host.status('a1')['target']=='previous-device'
    host.configure('a1',{'scope':'chat','target':'managed'})
    assert host.status('a1')['target']=='managed'

async def test_stop_survives_service_restart_and_blocks_before_browser_start(host):
    host.stop('a1',True)
    rebuilt=UIUse(host.service,host.connections)
    result=await rebuilt.call('a1','browser',{'action':'open','url':'https://example.com'})
    assert result['status']=='denied' and not rebuilt.browsers
    rebuilt.stop('a1',False)
    assert not UIUse(host.service,host.connections).stopped


def test_private_preferences_and_stop_use_the_service_owned_root(host, monkeypatch):
    from amplifier_web import ui_use
    original, writes = ui_use.write_private, []
    def private(path, text, *, owned_root):
        assert owned_root == host.service.data_dir
        writes.append(path)
        original(path, text, owned_root=owned_root)
    monkeypatch.setattr(ui_use, 'write_private', private)
    host.configure('a1', {'selection': 'standard'})
    host.stop('a1', True)
    rebuilt = UIUse(host.service, host.connections)
    assert rebuilt.effective('a1')['selection'] == 'standard'
    assert rebuilt.stopped == {'a1'}
    assert writes == [host.path, host.stop_path]


async def test_private_stop_refusal_still_blocks_live_input_and_cancels_pending(host, monkeypatch):
    from amplifier_web import ui_use
    cancelled = []
    host.connections.cancel = cancelled.append
    host.pending['a1'] = {'first', 'second'}
    def refuse(*args, **kwargs):
        raise PermissionError('Synthetic unsafe private root')
    monkeypatch.setattr(ui_use, 'write_private', refuse)
    with pytest.raises(PermissionError):
        host.stop('a1', True)
    assert set(cancelled) == {'first', 'second'}
    assert (await host.call('a1', 'browser', {'action': 'open', 'url': 'https://example.test'}))['status'] == 'denied'
    with pytest.raises(PermissionError):
        host.stop('a1', False)
    assert host.stopped == {'a1'}
    assert not host.browsers


async def test_live_preference_only_overrides_ordinary_selection_not_mounted_bindings(host):
    assert await host.call('a1','configuration',{'surface':'browser'})=={'stopped':False}
    host.configure('a1',{'scope':'chat','selection':'standard'})
    assert (await host.call('a1','configuration',{'surface':'computer'}))['selection']=={'mode':'standard'}
    host.configure('a1',{'scope':'chat','selection':'configured'})
    host.stop('a1',True)
    assert await host.call('a1','configuration',{'surface':'browser'})=={'stopped':True}

async def test_missing_explicit_device_never_moves_task_to_managed_browser(host):
    result=await host.call('a1','browser',{'action':'observe','target':'absent:tab'})
    assert result['status']=='blocked'
    assert result['reason']=='selected_device_unavailable'
    assert not host.browsers and not host.active

async def test_remote_unknown_effect_not_replayed(host):
    class Remote:
        def __init__(self):self.operations={};self.calls=0
        def lookup(self,identity):return self.operations.get(identity)
        def listing(self):return [{'id':'d','targets':[{'id':'t','ready':True,'revision':'r'}]}]
        def submit(self,body):
            self.calls+=1;row={**body,'status':'delivered'};self.operations[body['id']]=row;return row
        def refresh(self):
            for row in self.operations.values():row['status']='unknown'
        def receipt(self,row):return row
    host.connections=Remote()
    result=await host.remote('a1','d:t','browser.click',{'selector':'#submit'})
    assert result['status']=='unknown' and host.connections.calls==1
    assert not host.pending

def test_unavailable_target_preference_is_not_silently_saved(host):
    with pytest.raises(web.HTTPConflict):host.configure('a1',{'scope':'chat','target':'missing:tab'})
    assert host.effective('a1')['target']=='managed'


async def test_missing_managed_support_explains_available_recovery(host,monkeypatch):
    monkeypatch.setattr(host,'managed_available',lambda:False)
    assert host.targets()[0]['ready'] is False
    result=await host.call('a1','browser',{'action':'open','url':'https://example.com'})
    assert result['reason']=='managed_browser_unavailable' and not host.browsers

async def test_ui_routes_require_owner_and_csrf_and_chat_identity(aiohttp_client,tmp_path):
    from amplifier_web.auth import SESSION_COOKIE,new_session
    from amplifier_web.server import create_app
    from test_service import Runtime
    from test_device_connections import enrollment
    app=await create_app(tmp_path,workspace=tmp_path,runtime=Runtime(),preload_providers=False,voice=False,background_updates=False)
    client=await aiohttp_client(app);path='/api/ui-use/v1/'
    assert (await client.get(path+'state')).status==401
    cookie={'Cookie':SESSION_COOKIE+'='+new_session(app['session_secret'])}
    state=await (await client.get(path+'state',headers=cookie)).json()
    assert (await client.post(path+'preferences',headers=cookie,json={'scope':'global','selection':'standard'})).status==403
    headers={**cookie,'X-Connection-CSRF':state['csrf']}
    assert (await client.post(path+'preferences',headers=headers,json={'scope':'global','selection':'standard'})).status==200
    assert (await client.post(path+'stop',headers=headers,json={})).status==400
    device=enrollment(app['device_connections'])
    assert (await client.get(path+'state',headers={**cookie,'Authorization':'Bearer '+device['token']})).status==403


def test_reset_exposes_inherited_preference_without_changing_other_scope(host):
    host.configure('a1',{'scope':'global','selection':'standard'})
    host.configure('a1',{'scope':'chat','selection':'configured'})
    host.configure('a1',{'scope':'chat','reset':True})
    assert host.effective('a1')['selection']=='standard'
    assert host.preferences=={'global':{'selection':'standard'}}


async def test_stop_cancels_concurrent_view_and_input_without_losing_either(host):
    class Remote:
        def __init__(self):self.operations={};self.cancelled=[]
        def lookup(self,identity):return self.operations.get(identity)
        def listing(self):return [{'id':'d','targets':[{'id':'t','ready':True,'revision':'r'}]}]
        def submit(self,body):
            row={**body,'status':'delivered'};self.operations[body['id']]=row;return row
        def refresh(self):pass
        def receipt(self,row):return row
        def cancel(self,identity):self.cancelled.append(identity);self.operations[identity]['status']='unknown'
    host.connections=Remote()
    tasks=[asyncio.create_task(host.remote('a1','d:t',action,{})) for action in ['browser.task.act','browser.task.screenshot']]
    await asyncio.sleep(.01)
    assert len(host.pending['a1'])==2
    host.stop('a1',True)
    results=await asyncio.gather(*tasks)
    assert len(host.connections.cancelled)==2 and not host.pending
    assert all(row['status']=='unknown' for row in results)


def test_bundle_include_wires_available_executors_without_installing_or_overriding():
    from amplifier_web.host.ui_use_config import configure_bundle
    bundle=SimpleNamespace(tools=[{'module':'tool-computer-use'}])
    configure_bundle(bundle);assert bundle.tools==[{'module':'tool-computer-use'}]
    bundle.tools.append({'module':'tool-ui-use'})
    configure_bundle(bundle)
    assert bundle.tools[1]['config']['browser']['host_adapter'] is True
    assert bundle.tools[0]['config']['observed_control'] is True
    bundle.tools[0]['config']['observed_control']=False
    bundle.tools[1]['config']['browser']['host_adapter']=False
    configure_bundle(bundle)
    assert bundle.tools[0]['config']['observed_control'] is False
    assert bundle.tools[1]['config']['browser']['host_adapter'] is False
