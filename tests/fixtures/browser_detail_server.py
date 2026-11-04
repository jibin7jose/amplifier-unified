"""Disposable heavy conversation browser fixture. No model providers or user data."""
import asyncio, copy, hashlib, json, os, signal, sys, tempfile, time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT))
from aiohttp import web
from amplifier_web.server import create_app
from amplifier_web.setup import SetupManager
from amplifier_web.host.config import write_private
import amplifier_web
import yaml

if os.environ.get('AMPLIFIER_TEST_PACKAGE_ROOT'):
    assert Path(amplifier_web.__file__).resolve().parent == Path(os.environ['AMPLIFIER_TEST_PACKAGE_ROOT']).resolve(), 'Browser fixture must import the exact installed package'

async def fixture_probe(self, action, args, workspace):
    return {"models":[{"id":"fixture-model"}],"modelsProviderId":args.get("id"),"modelsSupported":True}
SetupManager.probe = fixture_probe
async def fixture_routing(self, workspace): pass
SetupManager.ensure_routing_catalog = fixture_routing

class Runtime:
    def __init__(self): self.starts=0; self.sends=0; self.stops=[]; self.worker_stops=[]; self.controls=[]
    async def start(self, session, emit):
        self.starts+=1
        await emit('runtime.status', {'sessionId':session['id'], 'status':'ready'})
    async def send(self, session, text, input_id, emit):
        self.sends+=1
        await emit('assistant.message', {'sessionId':session['id'], 'text':'Synthetic reply.', 'inputId':input_id})
        await emit('runtime.status', {'sessionId':session['id'], 'status':'idle'})
    async def control(self, sid, operation, args):
        self.controls.append({'sessionId':sid,'operation':operation,'args':args})
        if operation=='native.status':
            return {'supported':False,'compactAvailable':False,'reason':'This fixture model has no native context support.'}
        return {}
    async def stop(self, sid, *, reason="stopped"): self.stops.append(sid)
    async def stop_worker(self, sid, worker_id): self.worker_stops.append([sid, worker_id])
    async def close(self): pass

async def main():
    worker_diagnostics = None
    if os.environ.get('AMPLIFIER_TEST_WORKER_DIAGNOSTICS') == '1':
        from browser_worker_diagnostics import WorkerDiagnostics
        worker_diagnostics = WorkerDiagnostics()
    with tempfile.TemporaryDirectory(prefix='unified-issue-repro-') as directory:
        temp=Path(directory)
        os.environ.update(AMPLIFIER_HOME=str(temp/'native'), AMPLIFIER_WEB_HOME=str(temp/'app'),
            AMPLIFIER_UNIFIED_IMPORT_HOME=str(temp/'legacy'), AMPLIFIER_SESSION_STATE_HOME=str(temp/'session-state'))
        alpha=temp/'Alpha'; beta=temp/'Beta'; alpha.mkdir(); beta.mkdir()
        provider_settings={'config':{'providers':[{'module':'provider-fixture','id':'fixture','config':{'default_model':'fixture-model'}}]}}
        write_private(temp/'native'/'settings.yaml',yaml.safe_dump(provider_settings))
        app=await create_app(temp/'app', workspace=alpha, runtime=Runtime(), voice=False,
            background_updates=False, preload_providers=False)
        app['control_token']='fixture-detail-token'
        service=app['service']
        # Stop periodic scans while keeping explicit native-history reads usable.
        if service.history.task:
            service.history.task.cancel()
            await asyncio.gather(service.history.task, return_exceptions=True)
            service.history.task = None
        # Keep the selected-chat Work reader active. Its current-window receipt
        # is part of the browser contract, including after a turn becomes idle.
        await service.dispatch('session.create', {'title':'Alpha conversation','workspace':str(alpha)})
        aid=service.state['selectedSessionId']
        await service.dispatch('session.create', {'title':'Beta conversation','workspace':str(beta)})
        bid=service.state['selectedSessionId']
        await service.dispatch('session.select', {'id':aid})
        for i, row in enumerate(service.state['sessions']):
            row.update(status='idle', historyLoaded=True, deferRuntimeUntilInteraction=True,
                messages=[{'id':f'answer-{i}', 'role':'assistant','text':'A saved response.', 'via':'chat', 'createdAt':time.time()}])
        service.state['view'].update(navPinned=True, navExpanded=True, setupJourney='done')
        service.state.setdefault('setup',{}).update(providers=SetupManager(temp/'app').provider_rows(alpha),providersLoadedAt=time.time(),providersWorkspace=str(alpha))
        service._publish()
        baseline=copy.deepcopy(service.state)
        def log_hook(session_id, event, data, at=None):
            from amplifier_web.event_log_view import event_path
            row=service._session(aid)
            path=event_path(row,session_id);path.parent.mkdir(parents=True,exist_ok=True)
            with path.open('a') as stream:
                stream.write(json.dumps({'event':event,'timestamp':at or time.time(),'data':{'session_id':session_id,**data}})+'\n')
        class LoggedEvents:
            def __init__(self, identity, emit):
                from amplifier_web.execution_events import ExecutionEvents
                self.observer=ExecutionEvents(identity,emit)
            @property
            def turn_id(self):return self.observer.turn_id
            @turn_id.setter
            def turn_id(self,value):self.observer.turn_id=value
            def hook(self,sid,event,data):
                recorded=self.observer.hook(sid,event,data);log_hook(sid,event,recorded or data)
            def lifecycle(self,event):self.observer.lifecycle(event)
        async def control(request):
            args=await request.json(); op=args.get('op')
            if op == 'inspect-work-diagnostics':
                # Failure-only observation: do not publish, refresh or start a worker.
                return web.json_response({'workProjection': copy.deepcopy(service.event_log_view.windows.status),
                    'workers': worker_diagnostics.snapshot() if worker_diagnostics else []})
            if op == 'inspect-work-source':
                # A read-only oracle: do not publish a frame or rebuild usage.
                async with service.lock:
                    return web.json_response({'sessions':[{key:copy.deepcopy(row.get(key)) for key in ('id','messages','execution')} for row in service._state['sessions']],
                        'runtimeStarts':service.runtime.starts,'runtimeSends':service.runtime.sends,
                        'delegateCapture':getattr(service,'fixture_delegate_capture',None)})
            if op == 'agent-bridge':
                try:
                    result = await service.app_bridge(args['operation'], args.get('args', {}), args['sessionId'])
                    return web.json_response(result)
                except ValueError as error:
                    return web.json_response({'error': str(error)}, status=400)
            if op=='setup':
                write_private(temp/'native'/'settings.yaml',yaml.safe_dump(provider_settings if args.get('connected') else {'config':{'providers':[]}}))
                manager=SetupManager(temp/'app')
                workspace = Path(service._session(args['sessionId'])['workspace']) if args.get('sessionId') else alpha
                service.state['setup'].update(providers=manager.provider_rows(workspace),providersLoadedAt=time.time(),providersWorkspace=str(workspace))
                service.state['view']['setupJourney']=args.get('journey','done')
            elif op=='native-history':
                from amplifier_web.session_files import project_slug
                native=temp/'native'/'projects'/project_slug(alpha)/'sessions'/'paging-fixture'
                native.mkdir(parents=True)
                (native/'metadata.json').write_text(json.dumps({'session_id':'paging-fixture',
                    'working_dir':str(alpha),'name':'Live saved history','bundle':'bundle:anchors'}))
                (native/'transcript.jsonl').write_text(''.join(json.dumps({'role':'user' if i%2==0 else 'assistant',
                    'content':f'Saved message {i}\n\n'+('A retained paragraph. '*25)})+'\n' for i in range(305)))
                native_receipt = {name: hashlib.sha256((native/name).read_bytes()).hexdigest()
                                  for name in ('metadata.json', 'transcript.jsonl')}
                service.fixture_native_receipt = native_receipt
                service.fixture_native_path = native
                if args.get('unreadable'):
                    (native/'transcript.jsonl').chmod(0)
                await service.history.refresh()
                # Refresh discovers dormant entries in the directory. Only the
                # explicitly requested chat joins the resident working set.
                row=service._session('paging-fixture', hydrate=False)
                if not args.get('unreadable'):
                    await service.history.load(row['id'])
                row.update(status='ready',preparation={'status':'active'})
                service.state.update(selectedSessionId=row['id'],selectedWorkspaceId=row['workspaceId'])
            elif op=='native-readable':
                native=service.fixture_native_path
                (native/'transcript.jsonl').chmod(0o600)
                return web.json_response({'original': service.fixture_native_receipt,
                    'current': {name: hashlib.sha256((native/name).read_bytes()).hexdigest()
                                for name in ('metadata.json', 'transcript.jsonl')}})
            elif op=='live-response':
                row=service._session(args['id'])
                # Match actual send admission: live callbacks belong to the
                # active web conversation, not the passive native-file reader.
                row['historyManaged']=False
                await service.on_runtime_event('runtime.status',{'sessionId':row['id'],'status':'working'})
                await service.on_runtime_event('assistant.message',{'sessionId':row['id'],'text':'A new live response.'})
                await service.on_runtime_event('assistant.delta',{'sessionId':row['id'],'text':'Still writing...'})
            elif op=='reset':
                revision=service.state['revision']
                service.state.clear();service.state.update(copy.deepcopy(baseline));service.state['revision']=revision
            elif op=='patch':
                service.state['updates'].update(args.get('updates', {}))
                service.state['view'].update(args.get('view',{}))
                for sid,patch in args.get('sessions',{}).items(): service._session(sid).update(patch)
            elif op=='attention-priority':
                updates = service.state['updates']
                diagnostics = service.update_manager.diagnostics
                step = args.get('step', 'seed')
                if step == 'seed':
                    updates.update(phase='checked', error=None, items=[], available=0,
                        application={'id':'application','kind':'app','current':'1.0.0','latest':'v1.0.0',
                            'status':'current','releaseNotes':[{'version':'1.0.0','title':'Synthetic release',
                                'changes':['Optional improvements.'],'notices':[
                                    {'id':f'news-{i}','title':f'Optional news {i}',
                                     'detail':'Informational release guidance.',
                                     'action':'No action is required for ordinary browser use.'} for i in range(53)]}]})
                    service.state['releaseNoticeStartVersion'] = '1.0.0'
                    service.state['feedback']['requests'] = [
                        {'requestId':f'sent-{i}','status':'submitted','title':'Synthetic feedback',
                         'message':'Feedback sent.'} for i in range(3)]
                elif step in {'fail', 'distinct'}:
                    diagnostics.begin('ecosystem')
                    diagnostics.record('ecosystem-validation', 'failed', reason='runtime-resolution-mismatch',
                        component='amplifier-module-loop-live' if step=='fail' else 'amplifier-module-hooks-approval',
                        expectedRevision='a'*40, observedRevision='b'*40, errorType='ValueError')
                    await service.update_manager.publish(phase='error', error='Component update preparation failed. Current components remain active.')
                elif step == 'check':
                    updates.update(phase='checked', error=None)
                elif step in {'uncertainty', 'unknown-repeat', 'overflow-repeat'}:
                    if step == 'uncertainty':
                        # Real owner events: fill the bounded summary then recover
                        # its matched groups. Overflow remains, as does unknown scope.
                        for i in range(33):
                            diagnostics.begin('ecosystem', components=[f'amplifier-test-{i}'])
                            diagnostics.record('ecosystem-validation', 'failed', component=f'amplifier-test-{i}')
                        diagnostics.begin('ecosystem', components=[f'amplifier-test-{i}' for i in range(33)])
                        diagnostics.record('ecosystem-activation', 'succeeded')
                    if step == 'overflow-repeat':
                        for i in range(33):
                            diagnostics.begin('ecosystem', components=[f'amplifier-later-{i}'])
                            diagnostics.record('ecosystem-validation', 'failed', component=f'amplifier-later-{i}')
                        diagnostics.begin('ecosystem', components=[f'amplifier-later-{i}' for i in range(33)])
                        diagnostics.record('ecosystem-activation', 'succeeded')
                    else:
                        diagnostics.begin('application', 'a'*40)
                        diagnostics.record('candidate-install', 'failed', errorType='ValueError')
                    diagnostics.begin('application', 'a'*40, application_extras=[])
                    diagnostics.record('restart-ack', 'succeeded')
                    updates.update(phase='installed', error=None)
                elif step == 'recover':
                    diagnostics.begin('ecosystem', components=['amplifier-module-loop-live','amplifier-module-hooks-approval'])
                    diagnostics.clear_failure()
                    diagnostics.record('ecosystem-activation', 'succeeded')
                    updates.update(phase='installed', error=None)
                service._save_changes(globals={'updates','feedback','releaseNoticeStartVersion'})
            elif op=='interim-start':
                row=service._session(aid);now=time.time()-10
                removed = {message['id'] for message in row['messages']}
                # This fixture replaces the conversation, unlike a real next
                # turn. Retire only reveal requests for those removed messages.
                # Otherwise reconnect replays the earlier test's stale reveal.
                for client_id, client in service.clients.records.items():
                    focus = client.get('view', {}).get('messageFocus') or {}
                    if focus.get('sessionId') == aid and focus.get('messageId') in removed:
                        client['view'].pop('messageFocus', None)
                        service.clients.dirty.add(client_id)
                row.update(status='working',messages=[],execution={'nodes':[],'turns':[]},generations=[],workers=[])
                generation=row['fixtureGeneration']=f'interim-{time.time_ns()}'
                detailed = args.get('details') is True
                input_id = row['fixtureInputId'] = generation if detailed else 'interim-input'
                service._message(row,'user','Investigate the synthetic issue.','chat',inputId=input_id)
                from amplifier_web.execution import ensure_turn
                ensure_turn(row,input_id)
                if detailed:
                    from amplifier_web.execution_events import ExecutionEvents
                    from amplifier_web.session_files import append_event
                    observed=[]
                    observer=ExecutionEvents(aid,observed.append)
                    observer.lifecycle({'type':'input.delivered','input_id':input_id,'source':'user'})
                await service.on_runtime_event('runtime.generation',{'sessionId':aid,'rootSessionId':aid,
                    'event':'generation.started','generation_id':generation})
                for index,text in enumerate(['Checking the first detail.','Checking the second detail.'],1):
                    await service.on_runtime_event('assistant.message',{'sessionId':aid,'inputId':input_id,
                        'generationId':generation,'text':text,'runtimeMessage':{'sequence':index},
                        'createdAt':time.time() if detailed else now+index*2,'timestampKnown':True})
                    if detailed:
                        for event in ('tool:pre','tool:post'):
                            data={'session_id':aid,'tool_call_id':f'{generation}-call-{index}',
                                  'tool_name':'read_file','timestamp':time.time(),
                                  'tool_input':{'file_path':f'sample-{index}.txt','note':'界𝄞'*10000+f':INPUT-{index}'}}
                            if event=='tool:post':data['result']='雪𝄞'*10000+f':OUTPUT-{index}'
                            canonical=observer.hook(aid,event,data)
                            append_event(row['workspace'],aid,event,canonical)
                            await service.on_runtime_event('execution.event',observed[-1]['event'])
                        at=time.time()
                        await service.on_runtime_event('execution.event',{'sessionId':aid,'rootSessionId':aid,
                            'id':f'{generation}-model-{index}','turnId':input_id,'kind':'llm',
                            'label':f'Model step {index}','provider':'fixture','model':f'fixture-{index}',
                            'producerId':'fixture-producer','revision':1,'liveObservation':True,
                            'phase':'completed','startedAt':at,'endedAt':at,'usage':{'inputTokens':1,'outputTokens':1}})
                    else:
                        await service.on_runtime_event('execution.event',{'sessionId':aid,'rootSessionId':aid,
                            'id':f'interim-call-{index}','turnId':input_id,'kind':'tool','label':'read_file','phase':'completed',
                            'startedAt':now+index*2+.1,'endedAt':now+index*2+1,'input':{'file_path':'sample.txt'},'output':'Checked.'})
                if args.get('long'):
                    row['messages'][1]['text'] += '\n\n' + ('A retained progress paragraph.\n\n'*100)
            elif op in {'interim-finals','interim-finish'}:
                row=service._session(aid);generation=row['fixtureGeneration'];input_id=row['fixtureInputId']
                staged=row.get('fixtureStagedFinalGeneration')==generation
                if op=='interim-finals' and (args.get('generationId')!=generation or staged):
                    raise web.HTTPConflict(text='Final messages require the current unstaged fixture generation.')
                # A runtime may deliver its final messages before it reports the
                # generation complete. Stage them once so a reading test can bind
                # its pre-completion position to the actual rendered tail.
                if not staged:
                    for index,text in enumerate(['The final conclusion.','The final next step.'],10):
                        await service.on_runtime_event('assistant.message',{'sessionId':aid,'inputId':input_id,
                            'generationId':generation,'text':text,'runtimeMessage':{'sequence':index}})
                if op=='interim-finals':
                    row['fixtureStagedFinalGeneration']=generation
                else:
                    row.pop('fixtureStagedFinalGeneration',None)
                    await service.on_runtime_event('runtime.generation',{'sessionId':aid,'rootSessionId':aid,
                        'event':'generation.finished','generation_id':generation,'input_ids':[input_id],
                        'text':'The final conclusion.The final next step.','active_job_ids':[],
                        'finalMessageRefs':{'version':1,'generationId':generation,'sequences':[10,11]}})
                    await service.on_runtime_event('runtime.status',{'sessionId':aid,'status':'idle'})
            elif op=='interim-saved-view':
                from amplifier_web.session_projection import stored_execution
                row=service._session(aid)
                # Presentation-only persistence transform; the backend test
                # separately exercises actual service recreation and cold reads.
                row['execution']=json.loads(json.dumps(stored_execution(row['execution'])))
            elif op=='interim-error':
                await service.on_runtime_event('runtime.generation',{'sessionId':aid,'rootSessionId':aid,
                    'event':'generation.failed','generation_id':service._session(aid)['fixtureGeneration'],'input_ids':['interim-input']})
                await service.on_runtime_event('runtime.error',{'sessionId':aid,'error':'Synthetic interruption.'})
            elif op=='interim-media':
                from inline_media_samples import animation
                data,_=animation();path=alpha/'interim.gif';path.write_bytes(data)
                message=service._session(aid)['messages'][1]
                await service.dispatch('outputs.attachMedia',{'sessionId':aid,'messageId':message['id'],
                    'title':'Retained interim animation','path':'interim.gif','expectedSha256':hashlib.sha256(data).hexdigest()})
            elif op=='heavy':
                for sid,count in [(aid,args.get('messages',520)),(bid,args.get('otherMessages',549))]:
                    row=service._session(sid)
                    row.update(status='working' if args.get('active',False) else 'idle',messages=[{
                        'id':f'{sid}-m-{i}','role':'user' if i%2==0 else 'assistant','via':'chat','createdAt':1700000000+i,
                        'text':f'## Retained message {i}\n\n'+('A paragraph with **formatting** and [a link](https://example.com).\n\n'*100)[:args.get('chars',4000)]
                    } for i in range(count)])
                    row['execution']={'turns':[{'id':'turn','phase':'completed','startedAt':1700000000,'anchorMessageId':row['messages'][0]['id']}],
                        'nodes':[{'id':f'node-{i}','turnId':'turn','kind':'tool','phase':'completed','label':'Synthetic tool result',
                        'summary':'Synthetic tool result', 'output':'x'*args.get('nodeBytes',10000)} for i in range(args.get('nodes',0))]}
                for index, count in enumerate(args.get('extraMessageCounts', [])):
                    row=copy.deepcopy(service._session(aid))
                    identity=f'extra-{index}'
                    row.update(id=identity,title=f'Additional conversation {index}',status='idle',nativeIdentity=None,runtimeSessionId=None,
                        messages=[{'id':f'{identity}-m-{i}','role':'assistant','text':'Retained text. '*350,'createdAt':1700000000+i} for i in range(count)],execution={'turns':[],'nodes':[]})
                    service.state['sessions'].append(row)
            elif op == 'historical-delegate-record':
                # Historical UI uses a bounded canonical root-event page, not
                # the in-memory live tree fabricated by the earlier scenario.
                from amplifier_web.event_log_view import event_path
                from amplifier_web.work_windows import WorkWindows
                row = service._session(aid)
                event_path(row, aid).unlink(missing_ok=True)
                log_hook(aid, 'tool:post', {'tool_call_id': 'delegate-one',
                    'tool_name': 'delegate', 'result': {'success': True,
                    'output': {'response': 'The exact delivered async answer.'}}}, 1791409140)
                log_hook(aid, 'llm:response', {'model': 'historical-model',
                    'usage': {'total_tokens': 999999, 'cost_usd': 999}}, 1791409141)
                await service.event_log_view.windows.close()
                service.event_log_view.windows = WorkWindows(service)
                await service.event_log_view.windows.read(aid)
            elif op == 'bounded-tool-records':
                from amplifier_web.execution_events import ExecutionEvents
                from amplifier_web.execution import ensure_turn
                from amplifier_web.runtime import normalize_event
                from amplifier_web.event_log_view import event_path
                from amplifier_web.work_windows import WorkWindows
                row = service._session(aid)
                event_path(row, aid).unlink(missing_ok=True)
                now = time.time()
                row.update(status='idle', workers=[], messages=[{'id':'tool-detail-question',
                    'role':'user', 'text':'Inspect older and delegated saved tool details',
                    'createdAt':now-10}], execution={'nodes':[], 'turns':[]})
                ensure_turn(row, 'tool-details')
                row['execution']['turns'][0].update(anchorMessageId='tool-detail-question',
                    startedAt=now-9, phase='completed')
                emitted=[]
                events=ExecutionEvents(aid, emitted.append)
                events.turn_id='tool-details'
                async def call(sid, identity, name, arguments, result, *, legacy=False):
                    for event, data in [
                        ('tool:pre', {'tool_call_id':identity,'tool_name':name,'tool_input':arguments}),
                        ('tool:post', {'tool_call_id':identity,'tool_name':name,'tool_input':arguments,'result':result})]:
                        data=events.hook(sid,event,data) or data
                        public=emitted[-1]['event']
                        if legacy:
                            data.pop('unified_tool_observation_id',None)
                            public.pop('toolObservationId',None)
                        log_hook(sid,event,data,public.get('endedAt') or public['startedAt'])
                    for event in emitted:
                        kind,data=normalize_event(event,aid)
                        await service.on_runtime_event(kind,data)
                    emitted.clear()
                await call(aid,'older-exact','bash',{'command':'printf older-exact-command\n'+'# retained older code\n'*90+'# OLDER-EXACT-INPUT-END'},
                    {'success':True,'output':{'stdout':'OLDER-EXACT-RESULT-END','returncode':0}})
                await call(aid,'older-legacy','app_control',{'action':'workspace.inspect','note':'LEGACY-INPUT-END'},
                    {'success':True,'output':'LEGACY-OUTPUT-END'},legacy=True)
                await call(aid,'older-delegate','delegate',
                    {'agent':'reviewer','instruction':'Review retained request. '*90+'DELEGATE-REQUEST-END'},
                    {'success':True,'output':'Saved review result. '*90+'DELEGATE-RESPONSE-END'})
                events.lifecycle({'type':'child.updated','sessionId':'details-child','parentSessionId':aid,
                    'callId':'older-delegate','agent':'reviewer','status':'completed'})
                for event in emitted:
                    kind,data=normalize_event(event,aid)
                    await service.on_runtime_event(kind,data)
                emitted.clear()
                event_path(row,'details-child').unlink(missing_ok=True)
                await call('details-child','child-exact','tool_exec',
                    {'code':'print("child command")\n'+'# retained line\n'*90+'# CHILD-CODE-END'},
                    {'success':False,'output':{'stderr':'Child validation failed','returncode':2,
                        'stdout':'Child full output. '*90+'CHILD-OUTPUT-END'}})
                for index in range(140):
                    log_hook(aid,'tool:post',{'tool_call_id':'newer-unrelated-'+str(index),
                        'tool_name':'unrelated','result':'later observation'},now+index)
                legacy=next(node for node in row['execution']['nodes']
                    if node.get('toolCallId')=='older-legacy')
                log_hook(aid,'tool:post',{'tool_call_id':'older-legacy',
                    'tool_name':'app_control','result':'LEGACY-RECENT-OUTPUT'},legacy['endedAt'])
                # The synthetic runtime events above also queue diagnostic
                # capture records. Settle them before minting the initial
                # signed window, so setup cannot append behind its receipt.
                await service.diagnostics.flush()
                assert not service.diagnostics.storage_error
                await service.event_log_view.windows.close()
                service.event_log_view.windows=WorkWindows(service)
                await service.event_log_view.windows.read(aid)
                def no_complete_projection(*args,**kwargs):
                    raise AssertionError('Tool details must use bounded event windows only')
                service.event_log_view.read=no_complete_projection
            elif op == 'bounded-provider-records':
                from types import SimpleNamespace
                from amplifier_core import HookRegistry, HookResult
                from amplifier_web.event_log_view import event_path
                from amplifier_web.execution_events import ExecutionEvents
                from amplifier_web.execution import ensure_turn
                from amplifier_web.provider_recording import install_request_redaction
                from amplifier_web.runtime import normalize_event
                from amplifier_web.work_windows import WorkWindows
                row = service._session(aid)
                event_path(row, aid).unlink(missing_ok=True)
                now = time.time()
                # Two bounded saved pages exercise the enclosing collapsed
                # Work group, not just a standalone observation turn.
                for i in range(140):
                    log_hook(aid, 'tool:post', {'tool_call_id': 'saved-' + str(i),
                        'tool_name': 'Earlier saved check', 'result': {'success': True}}, now - 30 + i / 100)
                row.update(status='idle', messages=[{'id': 'recording-question', 'role': 'user',
                    'text': 'Inspect the saved provider calls', 'createdAt': now - 10}],
                    execution={'nodes': [], 'turns': []})
                ensure_turn(row, 'recordings')
                row['execution']['turns'][0].update(anchorMessageId='recording-question', startedAt=now-9,
                    phase='completed')
                for i in range(105):
                    row['execution']['nodes'].append({'id': 'earlier-' + str(i), 'kind': 'tool',
                        'turnId': 'recordings', 'label': 'Earlier check', 'phase': 'completed',
                        'startedAt': now - 8, 'endedAt': now - 7})
                if args.get('retention'):
                    row['execution']['nodes'] = []
                    emitted_tools=[]
                    tools=LoggedEvents(aid,emitted_tools.append);tools.turn_id='recordings'
                    arguments={'code':'// retained tool input\n'*100+'TOOL-INPUT-END'}
                    for event in ('tool:pre','tool:post'):
                        data={'tool_call_id':'retained-tool','tool_name':'tool_exec','tool_input':arguments}
                        if event=='tool:post':
                            data['result']={'success':True,'output':'retained output\n'*100+'TOOL-OUTPUT-END'}
                        tools.hook(aid,event,data)
                    for event in emitted_tools:
                        kind,data=normalize_event(event,aid);await service.on_runtime_event(kind,data)
                hooks, capabilities, emitted = HookRegistry(), {}, []
                coordinator = SimpleNamespace(hooks=hooks, get_capability=capabilities.get,
                    register_capability=lambda key, value: capabilities.update({key: value}))
                install_request_redaction(coordinator, directory=event_path(row, aid).parent)
                pending = []
                async def capture(event, data):
                    pending.append((event, copy.deepcopy(data), time.time()))
                    return HookResult()
                for event in ('llm:request', 'llm:response'):
                    hooks.register(event, capture, priority=100)
                class Provider:
                    def get_info(self): return {'id': 'test', 'defaults': {'model': 'bounded-recording'}}
                    async def complete(self, request):
                        await hooks.emit('llm:request', {'provider': 'test', 'model': 'bounded-recording',
                            'request_id': 'bounded-attempt', 'raw': {'instructions': 'RECORDED-REQUEST',
                            'largeDiagnostic': 'x' * 1_100_000, 'end': 'FULL-RECORDING-END'}})
                        await hooks.emit('llm:response', {'provider': 'test', 'model': 'bounded-recording',
                            'request_id': 'bounded-attempt', 'raw': {'text': 'RECORDED-RESPONSE'}})
                        return {'usage': {'input_tokens': 2500, 'output_tokens': 103}}
                events = ExecutionEvents(aid, emitted.append)
                events.turn_id = 'recordings'
                await events.instrument_provider(aid, Provider()).complete(SimpleNamespace(model='bounded-recording'))
                for event in emitted:
                    kind, data = normalize_event(event, aid)
                    await service.on_runtime_event(kind, data)
                # Legacy bytes are intact but have no exact host-call receipt.
                # The browser must expose their own observation, never infer a
                # live association merely from matching model/time/usage.
                for name, value in (('request', 'LEGACY-REQUEST'), ('response', 'LEGACY-RESPONSE')):
                    pending.append(('llm:' + name, {'provider': 'test', 'model': 'legacy-recording',
                        'request_id': 'legacy-attempt', 'raw': {'text': value}}, time.time()))
                await service.on_runtime_event('execution.event', {'id': 'llm:legacy', 'kind': 'llm',
                    'sessionId': aid, 'rootSessionId': aid, 'turnId': 'recordings', 'producerId': 'legacy-fixture',
                    'revision': 1, 'liveObservation': True, 'phase': 'completed', 'label': 'Legacy model call',
                    'provider': 'test', 'model': 'legacy-recording', 'startedAt': now, 'endedAt': time.time(),
                    'usage': {'totalTokens': 12}})
                service.fixture_provider_records = pending
                if not args.get('delayCapture'):
                    for event, data, at in pending:
                        log_hook(aid, event, data, at)
                    service.fixture_provider_records = []
                await service.event_log_view.windows.close()
                service.event_log_view.windows = WorkWindows(service)
                await service.event_log_view.windows.read(aid)
                def no_complete_projection(*args, **kwargs):
                    raise AssertionError('This fixture must use bounded event windows only')
                service.event_log_view.read = no_complete_projection
            elif op == 'append-retention-records':
                count=args.get('count',1)
                assert type(count) is int and 0<count<=201
                for index in range(count):
                    log_hook(aid,'tool:post',{'tool_call_id':f'unrelated-{time.time_ns()}-{index}',
                        'tool_name':'Newer saved work','result':{'success':True}})
                await service.event_log_view.windows.read(aid)
            elif op == 'flush-provider-records':
                # Only canonical bytes arrive: no execution event can wake an
                # already open completed call on behalf of the product.
                for event, data, at in service.fixture_provider_records:
                    log_hook(aid, event, data, at)
                service.fixture_provider_records = []
                await service.event_log_view.windows.read(aid)
            elif op in {'canonical-work','canonical-append'}:
                from amplifier_web.event_log_view import event_path
                row=service._session(aid);base=1700000000
                async def observed(kind, identity, start, end, **fields):
                    # Model the host's current observations as well as the
                    # native log. Historical-only events intentionally use a
                    # separate bounded window and have no authoritative turns.
                    await service.on_runtime_event('execution.event', {
                        'id':identity,'kind':kind,'sessionId':aid,'rootSessionId':aid,
                        'turnId':'canonical','producerId':'fixture-live','revision':2,
                        'liveObservation':True,'phase':'completed','startedAt':base+start,
                        'endedAt':base+end,**fields})
                async def tool(call,name,arguments,result,start,end):
                    log_hook(aid,'tool:pre',{'tool_call_id':call,'tool_name':name,'tool_input':arguments},base+start)
                    log_hook(aid,'tool:post',{'tool_call_id':call,'tool_name':name,'result':result},base+end)
                    await observed('tool',f'tool:{aid}:{call}',start,end,toolCallId=call,label=name)
                if op=='canonical-work':
                    event_path(row,aid).unlink(missing_ok=True)
                    row.update(status='idle',workers=[],messages=[
                        {'id':'before','role':'user','text':'Review the change','createdAt':base},
                        {'id':'interim','role':'assistant','text':'The first check passed. I am inspecting the remaining files.','createdAt':base+10},
                        {'id':'final','role':'assistant','text':'The review is complete.','createdAt':base+20}],execution={'nodes':[],'turns':[]})
                    from amplifier_web.execution import ensure_turn
                    ensure_turn(row,'canonical')
                    row['execution']['turns'][0].update(startedAt=base,anchorMessageId='before',phase='completed')
                    await tool('brief','bash',{'command':'git diff --check'},{'success':True,'output':{'stdout':'','stderr':'','returncode':0}},1,2)
                    raw={'model':'fixture','instructions':'Owner-recorded instructions',
                         'input':[{'role':'user','content':'Recorded message '+str(i)+' '+('content '*100)} for i in range(100)],
                         'tools':[{'name':'read_file'},{'name':'bash'}], 'reasoning':{'effort':'high'}, 'max_output_tokens':4096}
                    from amplifier_web.provider_payloads import save
                    from amplifier_web.provider_recording import redact_request
                    raw['largeDiagnostic'] = 'x' * 1_100_000
                    raw['end'] = 'FULL-RECORDING-END'
                    saved = save(event_path(row,aid).parent, raw)
                    preview, limited = redact_request(raw)
                    log_hook(aid,'provider:request',{'kind':'llm','id':'model-first','sessionId':aid,'model':'fixture','provider':'test','startedAt':base+3,'phase':'running'},base+3)
                    log_hook(aid,'llm:request',{'request_id':'first','provider':'test','model':'fixture','message_count':100,'has_instructions':True,'raw':preview,'provider_payloads':{'raw':saved},'request_capture':{'redacted':True,'truncated':limited}},base+3)
                    log_hook(aid,'llm:response',{'request_id':'first','provider':'test','model':'fixture','duration_ms':1000,'raw_response':{'output':[{'type':'message','content':[{'type':'output_text','text':'Recorded response content'}]}],'status':'completed'},'usage':{'input_tokens':10,'output_tokens':2,'cost_usd':.001}},base+4)
                    log_hook(aid,'llm:response',{'kind':'llm','id':'model-first','sessionId':aid,'model':'fixture','provider':'test','startedAt':base+3,'endedAt':base+4,'phase':'completed','usage':{'inputTokens':10,'outputTokens':2,'totalTokens':12,'costUsd':.001,'costType':'reported'}},base+4)
                    await observed('llm','model-first',3,4,label='Model call',provider='test',model='fixture',usage={'inputTokens':10,'outputTokens':2,'totalTokens':12,'costUsd':.001,'costType':'reported'})
                    for count,start in [(15,11),(16,13)]:
                        await tool('read'+str(count),'read_file',{'file_path':str(count)+'.txt'},
                             {'success':True,'output':{'content':'\n'.join('Line '+str(i) for i in range(1,count+1))}},start,start+1)
                    await tool('owned','app_control',{'action':'inspect','authorization':'Bearer owner-recorded-value'},
                         {'success':True,'output':{'value':'The full result is already here'}},15,16)
                    log_hook(aid,'llm:response',{'request_id':'second','model':'fixture','duration_ms':1000,'usage':{'input_tokens':20,'output_tokens':3,'cost_usd':.002}},base+18)
                    log_hook(aid,'llm:response',{'kind':'llm','id':'model-second','sessionId':aid,'model':'fixture','startedAt':base+17,'endedAt':base+18,'phase':'completed','usage':{'inputTokens':20,'outputTokens':3,'totalTokens':23,'costUsd':.002,'costType':'reported'}},base+18)
                    await observed('llm','model-second',17,18,label='Model call',model='fixture',usage={'inputTokens':20,'outputTokens':3,'totalTokens':23,'costUsd':.002,'costType':'reported'})
                else:
                    await tool('after-final','bash',{'command':'git status --short'},{'success':True,'output':'Clean'},21,22)
                    log_hook(aid,'provider:request',{'kind':'llm','id':'model-small','sessionId':aid,'model':'fixture','startedAt':base+23,'phase':'running'},base+23)
                    log_hook(aid,'llm:request',{'request_id':'small','model':'fixture','raw':{'model':'fixture','input':'Small recorded request'}},base+23)
                    log_hook(aid,'llm:response',{'request_id':'small','model':'fixture','raw':'Small recorded response… [truncated]', 'request_capture':{'redacted':True,'truncated':True}},base+24)
                    log_hook(aid,'llm:response',{'kind':'llm','id':'model-small','sessionId':aid,'model':'fixture','startedAt':base+23,'endedAt':base+24,'phase':'completed'},base+24)
                    await observed('llm','model-small',23,24,label='Model call',model='fixture')
                await service.event_log_view.refresh(aid)
            elif op=='chat-plan':
                row=service._session(aid)
                call='plan-'+str(time.time_ns())
                items=args.get('items',[{'content':'Find decisions','activeForm':'Finding decisions','status':'completed'},
                    {'content':'Draft the team update','activeForm':'Drafting the team update','status':'in_progress'},
                    {'content':'Prepare a document to share','activeForm':'Preparing a document','status':'pending'}])
                log_hook(aid,'tool:pre',{'tool_call_id':call,'tool_name':'todo','tool_input':{'action':'update','todos':items}})
                log_hook(aid,'tool:post',{'tool_call_id':call,'tool_name':'todo','result':{'success':True,'output':{'status':'updated','count':len(items)}}})
                await service.event_log_view.refresh(aid)
                row['execution']['currentTurnId']=row['execution']['plan']['turnId']
                row['status']=args.get('status','working')
            elif op=='delegate-observer':
                # Real host metadata observer and stamped canonical fixture
                # records; never patch exchange fields or invoke full replay.
                from amplifier_web.execution import ensure_turn
                from amplifier_web.runtime import normalize_event
                row=service._session(aid);now=time.time()
                row.update(status='idle',workers=[],messages=[
                    {'id':'observer-question','role':'user','text':'Inspect the recorded delegate exchange','createdAt':now-1}],
                    execution={'turns':[],'nodes':[]})
                ensure_turn(row,'observer-turn')
                row['execution']['turns'][0].update(startedAt=now,anchorMessageId='observer-question')
                emitted=[];events=LoggedEvents(aid,emitted.append);events.turn_id='observer-turn'
                arguments={'agent':'review','instruction':args['instruction']}
                events.hook(aid,'tool:pre',{'tool_call_id':'observer-delegate','tool_name':'delegate','tool_input':arguments})
                log_hook(aid,'delegate:agent_spawned',{'tool_call_id':'observer-delegate','parent_session_id':aid,'sub_session_id':'observer-child'})
                events.lifecycle({'type':'child.updated','sessionId':'observer-child','parentSessionId':aid,
                    'callId':'observer-delegate','runId':'observer-run','agent':'review','status':'running'})
                child_input={'file_path':'/fixture/observed.txt'}
                events.hook('observer-child','tool:pre',{'tool_call_id':'observer-read','tool_name':'read_file','tool_input':child_input})
                events.hook('observer-child','tool:post',{'tool_call_id':'observer-read','tool_name':'read_file',
                    'tool_input':child_input,'result':{'success':True,'output':'OBSERVER CHILD RESULT'}})
                events.hook(aid,'tool:post',{'tool_call_id':'observer-delegate','tool_name':'delegate','tool_input':arguments,
                    'result':{'success':True,'output':{'response':args['reply'],'session_id':'observer-child'}}})
                events.lifecycle({'type':'child.updated','sessionId':'observer-child','parentSessionId':aid,
                    'callId':'observer-delegate','runId':'observer-run','agent':'review','status':'completed','report':'SUMMARY-DECOY'})
                if args.get('olderRecords') is True:
                    from amplifier_web.event_window import MAX_ITEMS
                    # Establish the explicit older-page prerequisite before
                    # publishing host metadata to an already-open browser.
                    # Otherwise a ready recent window can correctly supply the
                    # exact fields without a separate saved-tool lookup.
                    for index in range(MAX_ITEMS):
                        log_hook(aid,'tool:post',{'tool_call_id':f'observer-later-{index}',
                            'tool_name':'unrelated','result':'Later saved observation'})
                for event in emitted:
                    normalized=normalize_event(event,aid)
                    if normalized:
                        await service.on_runtime_event(*normalized)
                row['execution']['turns'][0].update(phase='completed',endedAt=time.time())
                if args.get('olderRecords') is True:
                    # The replay above queues additional diagnostic capture.
                    # Mint the signed page only after that setup is durable.
                    from amplifier_web.event_log_view import event_path
                    def stamp():
                        info=event_path(row,aid).stat()
                        return {'device':str(info.st_dev),'inode':str(info.st_ino),
                            'size':info.st_size,'mtimeNs':str(info.st_mtime_ns),'ctimeNs':str(info.st_ctime_ns)}
                    before_flush=stamp()
                    queued=len(service.diagnostics.pending)
                    await service.diagnostics.flush()
                    assert not service.diagnostics.storage_error
                    after_flush=stamp()
                    await service.event_log_view.windows.read(aid)
                    service.fixture_delegate_capture={
                        'beforeFlush':before_flush,'afterFlush':after_flush,
                        'queuedBeforeFlush':queued,'queuedAfterFlush':len(service.diagnostics.pending),
                        'observations':[{'id':node['id'],'toolCallId':node.get('toolCallId'),
                            'toolObservationId':node.get('toolObservationId')}
                            for node in row['execution']['nodes'] if node.get('kind')=='tool']}
                assert all('input' not in node and 'output' not in node for node in row['execution']['nodes'])
            elif op=='inspection':
                from amplifier_web.execution import ensure_turn
                from amplifier_web.execution_events import ExecutionEvents
                from amplifier_web.runtime import normalize_event
                row=service._session(aid);now=time.time()
                row.update(status='working',messages=[{'id':'question','role':'user','text':'Inspect this work','createdAt':now-5},
                    {'id':'long-answer','role':'assistant','text':'# Complete response\n\n'+('A visible paragraph with **formatting**.\n\n'*450)+'COMPLETE-RESPONSE-END','createdAt':now}],execution={'turns':[],'nodes':[]})
                ensure_turn(row,'inspect');row['execution']['turns'][0].update(startedAt=now-3,anchorMessageId='question')
                emitted=[];events=LoggedEvents(aid,emitted.append);events.turn_id='inspect'
                events.hook(aid,'tool:pre',{'tool_call_id':'inspect-tool','tool_name':'fixture.read','tool_input':{'action':'inspect fixture','path':'/fixture/public.txt','authorization':'Bearer owned-log-value','text':'input '*150}})
                events.hook(aid,'tool:post',{'tool_call_id':'inspect-tool','result':{'success':True,'output':'Result line.\n'*2000+'RESULT-END','url':'https://example.com/artifact'}})
                for event in emitted:
                    kind,data=normalize_event(event,aid);await service.on_runtime_event(kind,data)
                # Match ExecutionEvents.publish: current observations remain
                # live evidence when the native Work window is reconciled.
                model={'id':'live-model','kind':'llm','sessionId':aid,'rootSessionId':aid,
                    'turnId':'inspect','label':'Model call','phase':'running','startedAt':now-2,
                    'producerId':'inspection-fixture','revision':1,'liveObservation':True}
                service.fixture_inspection_model=model
                log_hook(aid,'provider:request',model,now-2)
                await service.on_runtime_event('execution.event',model)
            elif op=='inspection-actions':
                from amplifier_web.execution_events import ExecutionEvents
                from amplifier_web.runtime import normalize_event
                emitted=[];events=LoggedEvents(aid,emitted.append);events.turn_id='inspect'
                examples=[
                    ('command','bash',{'command':'python -m pytest retirement/test_retire.py -q'},
                     {'success':True,'output':{'stdout':'............ [100%]\n12 passed in 0.21s','stderr':'','returncode':0}}),
                    ('patch','apply_patch',{'patch':'*** Begin Patch\n*** Update File: retirement/test_retire.py\n@@ -40,2 +40,3 @@\n def test_retry():\n-    retry(operation)\n+    result = retry(operation)\n+    assert result.status == "committed"\n*** End Patch'},
                     {'success':True,'output':'Updated retirement/test_retire.py'}),
                    ('read','read_file',{'file_path':'retirement/test_retire.py','offset':40,'limit':3},
                     {'success':True,'output':{'content':'40  def test_retry():\n41      result = retry(operation)\n42      assert result.status == "committed"'}}),
                    ('tasks','todo',{'todos':[{'content':'Inspect original behavior','status':'completed'},{'content':'Review the patch','status':'in_progress'}]},
                     {'success':True,'output':{'completed':1,'in_progress':1,'count':2}}),
                    ('app','app_control',{'action':'session.rename','title':'Retry operation ID check'},
                     {'success':True,'output':{'title':'Retry operation ID check'}}),
                    ('empty','bash',{'command':'git diff --check'},
                     {'success':True,'output':{'stdout':'','stderr':'','returncode':0}}),
                    ('failed','bash',{'command':'python failing_test.py'},
                     {'success':False,'output':{'stdout':'','stderr':'AssertionError: expected committed','returncode':1},'error':{'message':'Exit code 1'}}),
                    ('code','tool_exec',{'description':'a long saved description '*50,
                      'code':'const result = await tools.check({name: "fixture"});\n'+ '// retained code\n'*80+'// CODE-END'},
                     {'success':False,'output':{'stdout':'long retained output\n'*100,
                       'stderr':'Traceback\nFixtureError: exact failure detail','exit_code':9}}),
                    ('malformed','todo',{'todos':[{'content':{'unexpected':'shape'},'status':42}]},
                     {'success':False,'output':'Invalid task input'}),
                    ('delegate','delegate',{'agent':'code-reviewer','instruction':'Review the retry test'},
                     {'success':True,'output':'No findings.'}),
                ]
                for identity,name,arguments,result in examples:
                    events.hook(aid,'tool:pre',{'tool_call_id':identity,'tool_name':name,'tool_input':arguments})
                    events.hook(aid,'tool:post',{'tool_call_id':identity,'tool_name':name,'tool_input':arguments,'result':result})
                log_hook(aid,'delegate:agent_spawned',{'tool_call_id':'delegate','sub_session_id':'review-worker','agent':'code-reviewer'})
                events.lifecycle({'type':'child.updated','sessionId':'review-worker','parentSessionId':aid,'callId':'delegate','agent':'code-reviewer','status':'completed','report':'No findings. The assertion checks committed status.'})
                events.hook('review-worker','tool:pre',{'tool_call_id':'nested','tool_name':'bash','tool_input':{'command':'git diff --check'}})
                events.hook('review-worker','tool:post',{'tool_call_id':'nested','tool_name':'bash','result':{'success':True,'output':{'stdout':'','stderr':'','returncode':0}}})
                for event in emitted:
                    kind,data=normalize_event(event,aid);await service.on_runtime_event(kind,data)
                await service.on_runtime_event('execution.event',{'id':'legacy','kind':'tool','turnId':'inspect','label':'legacy.tool','phase':'completed','summary':'Tool completed. Expand any delegated actions below for their progress and results.'})
            elif op=='inspection-previews':
                from amplifier_web.runtime import normalize_event
                emitted=[];events=LoggedEvents(aid,emitted.append);events.turn_id='inspect'
                examples=json.loads((Path(__file__).parent/'tool-preview-cases.json').read_text())
                examples += [
                    {'name':'long-preview','tool':'custom_lookup','input':{'query':'Find the project schedule and next steps. '*150+'QUERY-END'}},
                    {'name':'nested-failure','tool':'tool_exec','input':{'code':'const result = await tools.inspect(); text(result);'},
                     'result':{'success':False,'error':{'message':'One or more nested calls did not confirm success'},'output':json.dumps({'error':{'code':'nested_call_failed','message':'One or more nested calls did not confirm success'},'calls':[{'name':'inspect','status':'unknown'}],'notes':'retained detail '*1200+'RESULT-END'},separators=(',',':'))}},
                ]
                for example in examples:
                    identity='preview-'+example['name'];name=example['tool'];arguments=example['input']
                    events.hook(aid,'tool:pre',{'tool_call_id':identity,'tool_name':name,'tool_input':arguments})
                    events.hook(aid,'tool:post',{'tool_call_id':identity,'tool_name':name,'tool_input':arguments,
                        'result':example.get('result',{'success':True,'output':{'matched':3,'next':None}})})
                for event in emitted:
                    kind,data=normalize_event(event,aid);await service.on_runtime_event(kind,data)
            elif op=='inspection-finish':
                now=time.time()
                model={**service.fixture_inspection_model,'revision':2,'phase':'completed','endedAt':now,
                    'usage':{'inputTokens':100,'outputTokens':20,'totalTokens':120,'costUsd':.004,'costType':'reported'}}
                log_hook(aid,'llm:response',model,now)
                await service.on_runtime_event('execution.event',model)
                await service.on_runtime_event('runtime.status',{'sessionId':aid,'status':'idle'})
            elif op=='many':
                template=copy.deepcopy(service._session(aid)); now=time.time()
                service.state['sessions'].extend([{**copy.deepcopy(template),'id':f'library-{i}','title':f'Library conversation {i:03}',
                    'createdAt':now-i,'recentActivityAt':now-i,'messages':[],'nativeIdentity':None,'runtimeSessionId':None}
                    for i in range(args.get('count',205))])
            if op.startswith('inspection'):
                await service.event_log_view.refresh(aid)
            service._publish()
            return web.json_response({'alpha':aid,'beta':bid,'revision':service.state['revision'], 'state':service.get_state(),
                'runtimeStarts':service.runtime.starts,'runtimeSends':service.runtime.sends,
                'delegateCapture':getattr(service,'fixture_delegate_capture',None),
                'runtimeStops':service.runtime.stops,'workerStops':service.runtime.worker_stops,'runtimeControls':service.runtime.controls,
                'retainedSessions':len(service.state['sessions']),
                'retainedMessages':sum(len(row.get('messages',[])) for row in service.state['sessions']),
                'canonicalBytes':len(json.dumps(service.state['sessions']).encode())})
        app.router.add_post('/api/fixture/control',control)
        runner=web.AppRunner(app);await runner.setup()
        site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
        port=site._server.sockets[0].getsockname()[1]
        app['allowed_origins']=app['allowed_origins']|{f'http://127.0.0.1:{port}'}
        from amplifier_web.auth import new_session
        print(json.dumps({'port':port,'alpha':aid,'beta':bid,'cookie':new_session(app['session_secret'])}),flush=True)
        stopped=asyncio.Event()
        for sig in [signal.SIGTERM,signal.SIGINT]:asyncio.get_running_loop().add_signal_handler(sig,stopped.set)
        await stopped.wait();await runner.cleanup()
    if worker_diagnostics:
        await worker_diagnostics.close()

if __name__ == '__main__':
    asyncio.run(main())
