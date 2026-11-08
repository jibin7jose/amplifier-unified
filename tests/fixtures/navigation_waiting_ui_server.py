"""Disposable saved-request navigation fixture. Never starts a provider."""
import asyncio
import json
import os
from pathlib import Path
import signal
import tempfile

from aiohttp import web
from configured_browser import seed_connection
from empty_host_ui_server import Runtime
from amplifier_web.server import create_app
from amplifier_web.navigation_waiting import input_boundary


async def main(home):
    os.environ.update(AMPLIFIER_HOME=str(home/'native'), AMPLIFIER_WEB_HOME=str(home/'app'),
                      AMPLIFIER_SESSION_STATE_HOME=str(home/'sessions'),
                      AMPLIFIER_UNIFIED_IMPORT_HOME=str(home/'legacy'))
    seed_connection(home/'native')
    folder=home/'workspace';folder.mkdir()
    runtime=Runtime()
    app=await create_app(home/'app',workspace=str(folder),runtime=runtime,
        voice=False,background_updates=False,preload_providers=False)
    app['control_token']='fixture-browser-control-token'
    service=app['service'];runtime.service=service;rows=[]
    for title in ('Release manager','Unified: Readiness','Second worker with a long descriptive title'):
        result=await service.dispatch('session.create',{'workspace':str(folder),'title':title,'bundle':'anchors'})
        row=service._session(result['result']['sessionId'])
        row.update(historyManaged=False,historyLoaded=True,messages=[
            {'id':row['id']+'-question','role':'user','text':'Synthetic saved work'}])
        rows.append(row)
    owner=rows[0]
    owner.update(collaborationGeneration={'id':'fixture-generation','inputIds':['fixture-input'],'terminal':True},
                 executionRevision=1)
    workspace_id=next(row['id'] for row in service.state['workspaces'] if row['path']==str(folder))
    await service.dispatch('workspace.rename',{'id':workspace_id,'name':'Unified: Launch Readiness'})
    await service.dispatch('workspace.pin',{'id':workspace_id,'pinned':True})
    for row in rows[:2]:
        await service.dispatch('session.pin',{'id':row['id'],'pinned':True})
    await service.dispatch('session.select',{'id':owner['id']})
    await service.dispatch('view.update',{'patch':{'setupJourney':'done','navPinned':True,'navChatScope':'all','panel':None}})

    def configure(mode):
        owner.update(status='idle',questions=[],approvals=[],error=None)
        owner['collaborationGeneration']['inputIds']=['fixture-input']
        task={'id':'fixture-task','revision':2,'status':'blocked','continuationEpoch':2,
              'dependencyWaitEpoch':1,'objective':'Wait for the exact reports',
              'blockProvenance':{'version':1,'origin':'agent','revision':2,'dependency':True}}
        owner['task']=task
        service.state.setdefault('runtimeControl',{})[owner['id']]={'task.get':{'task':task}}
        service.db.execute("DELETE FROM commands WHERE id LIKE 'fixture-wait-%'")
        for index, row in enumerate(rows[1:]):
            row['status']='working'
            if index and mode!='many':
                continue
            receipt={'commandAction':'coordination.send','requestId':f'fixture-wait-{index}',
                'sourceGenerationId':owner['collaborationGeneration']['id'],
                'sourceInputIds':list(owner['collaborationGeneration']['inputIds']),
                'protocol':2,'senderSessionId':owner['id'],'target':{'sessionId':row['id'],'title':row['title']},
                'delivery':'applied','subscription':{'taskId':task['id'],'taskContinuationEpoch':1,
                    'interruptionRevision':owner.get('interruptionRevision',0),'notificationVersion':2,
                    'supported':True,'open':True,'status':'waiting'}}
            if mode=='ready':
                receipt['response']={'status':'staged','outcome':'success'}
            service.db.execute('INSERT INTO commands VALUES(?,?,?)',
                (receipt['requestId'],'fixture',json.dumps(receipt)))
        if mode=='paused':
            task['status']='paused'
        elif mode=='stopped':
            owner['status']='stopped'
        elif mode=='question':
            owner['questions']=[{'id':'fixture-question','status':'pending','required':True,'revision':1,
                                 'question':'Choose the destination','prompt':'Choose the destination','options':[],
                                 'dependency':'Choose a destination before continuing','allowFreeText':True,
                                 'questionIds':[],'artifacts':[],'createdAt':1,'updatedAt':1}]
        elif mode=='recipient-error':
            rows[1].update(status='error',error='Synthetic worker failure')
        elif mode=='new-input':
            owner['collaborationGeneration']['inputIds'].append('new-human-input')
        service._publish_changes(sessions={row['id'] for row in rows})

    def auth(request):
        if request.headers.get('Authorization')!='Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()

    async def mode(request):
        auth(request)
        value=(await request.json())['mode']
        if value not in {'one','many','paused','stopped','ready','question','recipient-error','new-input'}:
            raise web.HTTPBadRequest()
        async with service.lock:
            configure(value)
        return web.json_response({'mode':value})

    configure('one')
    app.router.add_post('/fixture/wait',mode)
    runner=web.AppRunner(app);await runner.setup()
    site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
    url='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1]);app['allowed_origins']|={url}
    stop=asyncio.Event();loop=asyncio.get_running_loop()
    for sig in (signal.SIGTERM,signal.SIGINT):
        loop.add_signal_handler(sig,stop.set)
    print(json.dumps({'url':url,'ids':[row['id'] for row in rows],'path':str(folder)}),flush=True)
    try:
        await stop.wait()
    finally:
        await runner.cleanup()


if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='unified-waiting-ui-') as home:
        asyncio.run(main(Path(home)))