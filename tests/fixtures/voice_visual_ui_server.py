"""Production UI/capture service; synthetic voice, runtime and provider metadata."""
import asyncio
import copy
import json
import os
import signal
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from empty_host_ui_server import Runtime


async def main(home):
    os.environ.update(AMPLIFIER_HOME=str(home/'shared'),AMPLIFIER_WEB_HOME=str(home/'app'),AMPLIFIER_SESSION_STATE_HOME=str(home/'sessions'))
    from configured_browser import seed_connection
    seed_connection(home / 'shared')
    from aiohttp import web

    # Management and its UI actions stay real. This media fixture does not
    # qualify bundle acquisition or provider discovery: resolve at the same
    # metadata seam used by the other synthetic browser fixtures, before startup.
    from amplifier_web import draft_defaults, probe_process
    from amplifier_web.bundle_selection import defaults
    from amplifier_web.setup import SetupManager
    discovery = {'mode': 'synthetic-metadata', 'calls': {
        'configuration.defaults': 0, 'configuration.catalog': 0,
        'providers.models': 0, 'providers.schema': 0},
        'refused': {'probe.spawn': 0, 'configuration.replacement': 0,
                    'providers.models': 0, 'providers.schema': 0,
                    'providers.imageModels': 0, 'providers.test': 0,
                    'providers.testMessage': 0, 'naming.complete': 0, 'unknown-operation': 0}}
    metadata = {'module': 'provider-fixture',
                'info': {'display_name': 'Synthetic fixture provider', 'defaults': {'model': 'fixture-model'}},
                'configSchema': {'fields': []}}
    models = [{'id': 'fixture-model'}]
    def refuse(operation):
        # Fixed operation names/counts only: no command, URL, credentials or args.
        key = operation if operation in discovery['refused'] else 'unknown-operation'
        discovery['refused'][key] += 1
        raise AssertionError('Unexpected acquisition/discovery in synthetic voice fixture: ' + key)
    async def forbidden_acquisition(*args, **kwargs):
        refuse('probe.spawn')
    async def resolve(home, workspace, bundle=None, app_bundle=None, *, global_only=False,
                      session_id=None, catalog=False, refresh=False, replacement=None, desired_bundle=False):
        discovery['calls']['configuration.catalog' if catalog else 'configuration.defaults'] += 1
        if replacement is not None:
            refuse('configuration.replacement')
        chosen = bundle or defaults(home, workspace, app_bundle, global_only=global_only)['effective']
        manager = SetupManager(home, global_only=global_only, allow_missing_workspace=True)
        key = manager.catalog_key({'id': 'fixture'}, workspace)
        result = {'bundle': chosen, 'globalOnly': global_only,
                  'providers': [{'id': 'fixture', 'sharedCatalogKey': key, 'info': copy.deepcopy(metadata['info'])}],
                  'effective': {'instance': 'fixture', 'model': 'fixture-model'}}
        if catalog:
            result['catalogs'] = {key: {'phase': 'ready', 'models': copy.deepcopy(models),
                                        'metadata': copy.deepcopy(metadata), 'supported': True}}
        return result
    async def probe(manager, action, args, workspace):
        if (action not in discovery['calls']
                or action not in {'providers.models', 'providers.schema'}
                or args.get('id', 'fixture') != 'fixture'
                or args.get('module', 'provider-fixture') != 'provider-fixture'):
            refuse(action)
        discovery['calls'][action] += 1
        return {'models': copy.deepcopy(models), 'providerMetadata': copy.deepcopy(metadata),
                'modelsSupported': True}
    draft_defaults.resolve_defaults = resolve
    SetupManager.probe = probe
    # Guard both the module's earlier imported alias and future imports. No
    # subprocess is launched or silently swallowed if this boundary is crossed.
    draft_defaults.spawn = probe_process.spawn = forbidden_acquisition

    from amplifier_web.server import create_app
    runtime=Runtime();app=await create_app(home/'app',workspace=str(home),runtime=runtime,voice=False,background_updates=False,preload_providers=False)
    service=app['service'];runtime.service=service;app['control_token']='fixture-browser-control-token'
    if '--onboarding-complete' in sys.argv:
        # Configured voice journeys can start after onboarding. Otherwise delayed
        # provider discovery can open first-use Settings over the voice controls.
        await service.dispatch('view.update', {'patch': {'setupJourney': 'done'}})
    class Manager:
        api_key=True
        call=None
        ends=[]
        sequence=0
        async def end(self,identity=None,*,attempt_id=None,expected_call=None,**kwargs):
            call=self.call
            if call and ((identity and identity!=call.id) or (attempt_id and attempt_id!=call.attempt_id) or (expected_call and expected_call is not call)):
                raise ValueError('This call is no longer active.')
            if call:
                self.ends.append({'id':identity,'attemptId':attempt_id})
                call.closed=True
                await service.set_voice_status({'status':'ended','id':call.id,'attemptId':call.attempt_id,'finalized':True})
            return {'closed':True,'finalized':True,'workContinues':True}
    service.voice_service=Manager()
    class SyntheticNative:
        calls=[]
        async def run(self, operation):
            self.calls.append(operation)
            if operation=='status': return {'available':True,'status':'ready','permission':'granted'}
            return {'image':'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC',
                    'capturedAt':time.time(),'window':{'id':'fixture-42','title':'Synthetic native window','application':'Fixture application','bounds':[0,0,1,1]}}
    service.voice_visual.native=SyntheticNative()
    original_visual = service.computer_visual.for_client
    def computer_visual(*args, **kwargs):
        visual = original_visual(*args, **kwargs)
        visual.native = service.voice_visual.native
        return visual
    service.computer_visual.for_client = computer_visual
    async def config(request):return web.json_response({'available':True})
    async def connect(request):
        data=await request.json();sid=data['sessionId']
        manager=service.voice_service
        if manager.call and not manager.call.closed:return web.json_response({'error':'A call is already active.','code':'call_active'},status=409)
        manager.sequence+=1
        call=SimpleNamespace(id='fixture-call-'+str(manager.sequence),attempt_id=data['attemptId'],session_id=sid,closed=False,closing=False,client_id=service.clients.current.get())
        service.voice_service.call=call
        await service.set_voice_status({'id':call.id,'attemptId':call.attempt_id,'sessionId':sid,'status':'connecting','finalized':False})
        return web.json_response({'id':call.id,'sessionId':sid,'sdp':'synthetic-answer','provider':'synthetic','model':'Synthetic voice fixture'})
    async def ready(request):await service.set_voice_status({'status':'connected'});return web.json_response({'ready':True})
    async def end(request):
        data=await request.json()
        if not data.get('id') and not data.get('attemptId'):return web.json_response({'error':'A call identity is required.'},status=400)
        return web.json_response(await service.voice_service.end(data.get('id'),attempt_id=data.get('attemptId')))
    async def inspect(request):
        return web.json_response({'discovery':discovery,'grant':service.voice_visual.grant,'receipts':list(service.voice_visual.receipts.values()),'sent':runtime.sent,'stopped':runtime.stopped,'ends':service.voice_service.ends,'nativeCalls':service.voice_visual.native.calls,'computerGrants':[v.grant for v in service.computer_visual.clients.values() if v.grant],'computerReceipts':[r for v in service.computer_visual.clients.values() for r in v.receipts.values()]})
    async def activity(request):
        data=await request.json();session=service._session(data['sessionId'])
        session['status']=data.get('status','idle');session['workers']=data.get('workers',[])
        service._publish()
        return web.json_response({'ok':True})
    async def transcript(request):
        data=await request.json()
        for row in data['messages']:
            await service.record_voice_transcript(row['role'],row['text'],voice_id='fixture-call',item_id=row['id'],session_id=data['sessionId'])
        return web.json_response({'ok':True})
    async def agent(request):
        payload=await request.json()
        return web.json_response(await service.app_bridge('dispatch',payload,payload.get('sessionId') or service.voice_service.call.session_id))
    app.router.add_get('/api/voice/config',config);app.router.add_post('/api/voice/connect',connect);app.router.add_post('/api/voice/ready',ready);app.router.add_post('/api/voice/end',end)
    app.router.add_get('/fixture',inspect);app.router.add_post('/fixture/agent',agent);app.router.add_post('/fixture/activity',activity);app.router.add_post('/fixture/transcript',transcript)
    runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start();url=f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    app['allowed_origins']=app['allowed_origins']|{url}
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    signals = (signal.SIGINT, signal.SIGTERM) if os.name == 'posix' else ()
    for sig in signals:loop.add_signal_handler(sig, stopped.set)
    print(json.dumps({'url':url}),flush=True)
    try:await stopped.wait()
    finally:
        try:await runner.cleanup()
        finally:
            for sig in signals:loop.remove_signal_handler(sig)
            print('VOICE_FIXTURE_DISCOVERY '+json.dumps(discovery),flush=True)
            assert not any(discovery['refused'].values()), 'Synthetic fixture encountered unexpected acquisition/discovery'
if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='voice-visual-fixture-') as home:asyncio.run(main(Path(home)))
