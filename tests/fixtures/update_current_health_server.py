"""Real Settings HTTP/SSE with captured attempt history and synthetic current graph."""
import asyncio
import copy
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
from aiohttp import web
from settings_ui_server import main as settings_app


async def main(home):
    app=await settings_app(home);service=app['service'];manager=service.update_manager
    from amplifier_web import update_health, runtime_qualification
    from amplifier_web.runtime_profiles import configuration_key
    from amplifier_web.host.config import read_config
    from amplifier_web.update_diagnostics import UpdateDiagnostics
    saved=json.loads((Path(__file__).parent/'update-current-health-captured060.json').read_text())
    get=lambda key:saved['observedState'][key]['value']
    generation=saved['active']['current'];stage=manager.directory/'releases'/generation;stage.mkdir(parents=True)
    source=saved['generations'][generation]
    (manager.directory/'active.json').write_text(json.dumps(saved['active']))
    project=home/'runtime'/('q-'+'a'*32);project.mkdir(parents=True)
    (project/'.venv').symlink_to(Path(sys.prefix),target_is_directory=True)
    graph=runtime_qualification.installed_graph(project)
    assert any(row['name']=='amplifier-foundation' for row in graph)
    config=read_config(home,home=home,global_only=True)
    records={**{name:source[name] for name in ['plan.json','validated.json']},
        'runtime-project.json':{'project':project.name},'runtime-installed.json':graph,
        'profiles-qualified.json':{'profiles':['work'],'configuration':configuration_key(config.settings,home,stage)}}
    for name,value in records.items():(stage/name).write_text(json.dumps(value))
    (stage/'runtime.lock').write_text('fixture-immutable-lock');(project/'uv.lock').write_text('fixture-immutable-lock')
    state=service.state['updates'];state.update(phase='checked',error=None,detail='Component check complete.',available=0,
        release=generation,pendingRelease=None,pendingApp=None,pendingRestart=None,pendingReplacement=None,
        sequence=get('/updates/sequence'),applicationResult=get('/updates/applicationResult'),
        application={'current':'0.60.0','runningRevision':get('/updates/applicationResult')['revision'],'status':'current','releaseNotes':[]},items=[],
        diagnostics={'events':[],'activeFailures':get('/updates/diagnostics/activeFailures'),
            'completedBatches':list(get('/updates/diagnostics/completedBatches').values())})
    manager.diagnostics=UpdateDiagnostics(manager)
    await update_health.refresh(manager);assert manager._active_update_evidence
    original=copy.deepcopy(state['diagnostics']['activeFailures'])
    await manager.publish()
    async def transition(request):
        if request.headers.get('Authorization')!='Bearer fixture-browser-control-token':raise web.HTTPForbidden()
        mode=(await request.json())['mode']
        if mode=='failure':
            manager.diagnostics.begin('ecosystem','b'*32,tier='other',components=['amplifier-foundation'])
            manager.diagnostics.record('ecosystem-source-preflight','failed',package='amplifier-foundation',reason='runtime-source-constraint')
            await manager.publish(phase='error',error='Current source conflict; installed components kept.')
        elif mode=='uncertain':await manager.publish(pendingReplacement={'unqualified':True},phase='activating')
        elif mode=='current':
            state['diagnostics']['activeFailures']=copy.deepcopy(original);state['diagnostics'].pop('lastFailure',None)
            await manager.publish(phase='checked',error=None,pendingReplacement=None)
        elif mode == 'partial':
            from amplifier_web.update_partial import adopted_items, public_results
            rows = [{'id': name, 'label': 'Fixture ' + name.upper(), 'kind': 'bundle / module',
                     'status': 'update', 'current': 'a'*40, 'latest': 'b'*40, 'ref': 'main',
                     'usage': 'configured', 'updateTier': 'included'} for name in 'abc']
            receipt = {'results': {name: {'status': status, 'revision': ('b' if name == 'b' else 'a')*40}
                       for name, status in {'a': 'failed', 'b': 'awaiting-adoption', 'c': 'dependency-held'}.items()}}
            await manager.publish(phase='installed', error=None, available=0,
                sequence={'stage': 'complete', 'install': False, 'included': {'status': 'attention', 'issues': 2}},
                items=adopted_items(rows, receipt), componentResults=public_results(receipt, adopted=True))
        elif mode.startswith('notes-'):
            application = dict(state['application'])
            application.pop('releaseNotesWarning', None)
            application.pop('releaseNotes', None)
            if mode=='notes-empty': application['releaseNotes']=[]
            elif mode=='notes-error': application['releaseNotesWarning']='Release notes could not be refreshed. Showing saved release history.'
            elif mode=='notes-next': application['current']='0.60.1'
            elif mode=='notes-ready':
                application['releaseNotes']=[{'version':application['current'],'title':'Fixture release notes',
                    'changes':['Exact installed-version notes arrived.'],'notices':[]}]
            elif mode!='notes-missing': raise web.HTTPBadRequest()
            await manager.publish(application=application)
        else:raise web.HTTPBadRequest()
        return web.json_response({'status':state['currentStatus'],'historyPreserved':all(k in state['diagnostics']['activeFailures'] for k in original)})
    async def observations(request):
        return web.json_response({'status':state['currentStatus'],'history':state['diagnostics']['activeFailures'],
            'originalHistoryUnchanged':state['diagnostics']['activeFailures']==original,'paidCalls':0,'liveInstallCalls':0})
    app.router.add_post('/fixture/current-update',transition);app.router.add_get('/fixture/current-update',observations)
    return app


async def serve():
    with tempfile.TemporaryDirectory(prefix='update-health-ui-') as tmp:
        app=await main(Path(tmp));runner=web.AppRunner(app);await runner.setup();stopped=asyncio.Event();loop=asyncio.get_running_loop()
        for sig in [signal.SIGTERM,signal.SIGINT]:loop.add_signal_handler(sig,stopped.set)
        try:
            site=web.TCPSite(runner,'127.0.0.1',0);await site.start();url='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1]);app['allowed_origins']|={url}
            import amplifier_web
            print(json.dumps({'url':url,'package':amplifier_web.__file__}),flush=True);await stopped.wait()
        finally:await runner.cleanup()

if __name__=='__main__':asyncio.run(serve())
