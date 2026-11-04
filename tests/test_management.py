from pathlib import Path
import json
import pytest
from amplifier_web.service import AppService
from amplifier_web.management import Management
from amplifier_web.host.storage import SessionStore
from amplifier_web.notifications import Notifications
from amplifier_web.preferences import SettingsStore
from amplifier_web.service import AppError

class Runtime:
    async def stop(self,*args,reason="stopped"):pass
    async def close(self):pass


@pytest.mark.parametrize('forged', [False, True])
async def test_explicit_recovery_forwards_trusted_command_not_request_identity(app, monkeypatch, forged):
    from unittest.mock import AsyncMock
    session = app._session()
    session.update(status='idle', interruptionRevision=7)
    app.management.ensure_runtime = AsyncMock()
    app.runtime.control = AsyncMock(return_value={'status': 'armed'})
    args = {'sessionId': session['id'], 'operation': 'context.recovery.request',
            'args': {'targetBytes': 5000}}
    source = {'recovery': app.management.recovery_admission_binding(session)}
    if forged:
        args['args']['_recoveryCommand'] = {'id': 'forged', 'interruptionRevision': 0}
        with pytest.raises(ValueError, match='cannot supply host identity'):
            await app.management.perform('runtime.control', args, command_id='real-command', source=source)
        app.runtime.control.assert_not_called()
    else:
        await app.management.perform('runtime.control', args, command_id='real-command', source=source)
        assert app.runtime.control.await_args.args == (session['id'], 'context.recovery.request', {
            'targetBytes': 5000, '_recoveryCommand': {'id': 'real-command', 'interruptionRevision': 7}})
        assert callable(app.runtime.control.await_args.kwargs['guard'])
        assert app.state['runtimeControl'][session['id']]['context.recovery.request']['status'] == 'armed'
        assert session.get('generation') is None


@pytest.mark.parametrize('route', ['dispatch', 'bridge'])
async def test_explicit_recovery_cannot_target_another_conversation(app, route):
    source = app._session()['id']
    await app.dispatch('session.create', {'bundle': 'anchors-amp-dev'})
    target = app._session()['id']
    args = {'sessionId': target, 'operation': 'context.recovery.request'}
    with pytest.raises(AppError, match='calling conversation'):
        if route == 'dispatch':
            await app.dispatch('runtime.control', args, origin='agent', caller_session_id=source)
        else:
            await app.app_bridge('dispatch', {'action': 'runtime.control', 'args': args}, source)


async def test_explicit_recovery_child_cannot_arm_parent(app):
    source = app._session()['id']
    with pytest.raises(AppError, match='child cannot borrow'):
        await app.app_bridge('dispatch', {
            'action': 'runtime.control',
            'args': {'sessionId': source, 'operation': 'context.recovery.request'},
            '_runtimeSessionId': 'separate-child'}, source)


@pytest.mark.parametrize('change', ['stop', 'configuration', 'placement'])
@pytest.mark.parametrize('when', ['queue', 'preparation', 'native-admission'])
async def test_explicit_recovery_keeps_original_admission_across_waits(app, change, when):
    import asyncio
    from unittest.mock import AsyncMock
    session = app._session()
    session.update(status='idle', interruptionRevision=7)
    prepared, release, armed = asyncio.Event(), asyncio.Event(), []
    original = app.management.command
    entered = asyncio.Event()
    pending = []

    async def observe_command(*args):
        entered.set()
        pending.append(asyncio.current_task())
        return await original(*args)

    async def prepare(*args, **kwargs):
        if when == 'preparation':
            prepared.set()
            await release.wait()
        kwargs['restart_guard']()

    async def control(sid, operation, arguments, *, guard):
        if when == 'native-admission':
            prepared.set()
            await release.wait()
        guard()
        armed.append(arguments)
        return {'state': 'pending'}

    app.management.command = observe_command
    app.management.ensure_runtime = AsyncMock(side_effect=prepare)
    app.runtime.control = AsyncMock(side_effect=control)
    if when == 'queue':
        await app.management.lock.acquire()
    try:
        await app.dispatch('runtime.control', {
            'sessionId': session['id'], 'operation': 'context.recovery.request'},
            command_id='original-recovery')
        await asyncio.wait_for(entered.wait(), 2)
        if when != 'queue':
            await asyncio.wait_for(prepared.wait(), 2)
        if change == 'stop':
            await app.dispatch('conversation.stop', {'sessionId': session['id']})
            assert session['interruptionRevision'] > 7
        elif change == 'configuration':
            app.state['configurationRevision'] = app.state.get('configurationRevision', 0) + 1
        else:
            session['executionRevision'] = session.get('executionRevision', 0) + 1
    finally:
        if when == 'queue':
            app.management.lock.release()
        release.set()
    await asyncio.wait_for(pending[0], 2)
    assert armed == []
    if when == 'queue':
        app.management.ensure_runtime.assert_not_called()
    receipt = app.state['managementResults']['original-recovery']
    assert receipt['phase'] == 'error'
    assert 'after admission' in receipt['error']


@pytest.mark.parametrize('uncertain_mutation', [False, True])
async def test_explicit_recovery_refuses_changed_budget_even_when_readback_fails(app, uncertain_mutation):
    import asyncio
    from unittest.mock import AsyncMock
    session = app._session()
    session.update(status='idle', interruptionRevision=7)
    entered, release, queued = asyncio.Event(), asyncio.Event(), asyncio.Event()
    native_calls, pending = [], []
    original_command = app.management.command

    async def command(*args):
        if args[1].get('operation') == 'context.recovery.request':
            pending.append(asyncio.current_task())
            queued.set()
        return await original_command(*args)

    async def control(sid, operation, arguments, **kwargs):
        native_calls.append(operation)
        if operation == 'budget.set':
            assert arguments == {'maxOutputTokens': 2000}
            entered.set()
            await release.wait()
            if uncertain_mutation:
                raise ConnectionError('Synthetic lost mutation receipt')
            return {'maxOutputTokens': 2000}
        if operation == 'budget.get':
            raise ConnectionError('Synthetic lost readback')
        raise AssertionError('Stale recovery reached native arming')

    app.management.command = command
    app.management.ensure_runtime = AsyncMock()
    app.runtime.control = AsyncMock(side_effect=control)
    changing = asyncio.create_task(app.management.command('runtime.control', {
        'sessionId': session['id'], 'operation': 'budget.set', 'args': {'maxOutputTokens': 2000}}, 'budget-change'))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await app.dispatch('runtime.control', {
            'sessionId': session['id'], 'operation': 'context.recovery.request'}, command_id='queued-recovery')
        await asyncio.wait_for(queued.wait(), 2)
    finally:
        release.set()
    await asyncio.wait_for(changing, 2)
    await asyncio.wait_for(pending[0], 2)
    assert native_calls == (['budget.set'] if uncertain_mutation else ['budget.set', 'budget.get'])
    receipt = app.state['managementResults']['queued-recovery']
    assert receipt['phase'] == 'error' and 'after admission' in receipt['error']


@pytest.fixture
async def app(tmp_path):
    service=AppService(tmp_path/'app',Runtime(),workspace=tmp_path)
    service.management=Management(service)
    await service.dispatch('session.create',{'bundle':'anchors-amp-dev'})
    yield service
    await service.close()

async def test_scoped_file_permissions_persist_without_changing_root_bundle(app,tmp_path):
    await app.management.perform('permissions.save',{'scope':'project','allowed':[str(tmp_path/'output')],'denied':[str(tmp_path/'private')]})
    saved=SettingsStore(app.data_dir).read(tmp_path,'project')
    assert saved['overrides']['tool-filesystem']['config']['denied_write_paths']==[str(tmp_path/'private')]
    assert app.state['sessions'][0]['bundle']=='anchors-amp-dev'


@pytest.mark.parametrize('route', ['dispatch', 'bridge'])
async def test_agent_permission_write_requires_explicit_scope(app, route):
    source = app.state['sessions'][0]
    args = {'allowed': [], 'denied': []}
    before = SettingsStore(app.data_dir).read(source['workspace'], 'global')
    with pytest.raises(AppError, match='explicit file-access scope'):
        if route == 'dispatch':
            await app.dispatch('permissions.save', args, origin='agent', caller_session_id=source['id'])
        else:
            await app.app_bridge('dispatch', {'action': 'permissions.save', 'args': args,
                '_runtimeSessionId': source['id']}, source['id'])
    assert SettingsStore(app.data_dir).read(source['workspace'], 'global') == before


async def test_child_cannot_change_root_permissions_through_shared_transport(app):
    source = app.state['sessions'][0]
    with pytest.raises(AppError, match='child cannot borrow'):
        await app.app_bridge('dispatch', {'action': 'permissions.save',
            'args': {'scope': 'global', 'allowed': [], 'denied': []},
            '_runtimeSessionId': 'actual-child'}, source['id'])

async def test_history_import_uses_real_checkpoint_and_does_not_execute(app):
    store=SessionStore(app.data_dir/'sessions')
    store.save('import-test',[{'role':'user','content':'Earlier question'},{'role':'assistant','content':'Earlier answer'}],{'bundle_name':'anchors','working_dir':app.default_workspace})
    await app.management.perform('history.import',{'id':'import-test'})
    assert app.state['selectedSessionId']=='import-test'
    assert app._session()['status']=='stopped'
    assert app._session()['messages'][-1]['text']=='Earlier answer'

async def test_shared_history_uses_the_isolated_probe_without_starting_a_worker(app):
    class ProbeRuntime(Runtime):
        def __init__(self):self.requests=[]
        async def shared_state_probe(self,request):
            self.requests.append(request)
            return {'items':[{'id':'shared-id','workspace':request['workspace'],'shared':True}]}
        async def start(self,*args):raise AssertionError('A history view must not start a worker')
    app.runtime=ProbeRuntime()
    await app.management.perform('history.shared.list',{'workspace':app.default_workspace})
    assert app.runtime.requests==[{'version':1,'op':'list','workspace':app.default_workspace}]
    assert app.state['sharedHistory']['items'][0]['id']=='shared-id'

async def test_open_shared_root_keeps_identity_workspace_and_authority(app, tmp_path):
    workspace = tmp_path / 'other-workspace'
    workspace.mkdir()
    class ProbeRuntime(Runtime):
        async def shared_state_probe(self, request):
            assert request['op'] == 'open'
            return {'id': 'shared-root', 'workspace': str(workspace),
                    'bundle': 'portable-bundle', 'offset': 12, 'totalMessages': 112,
                    'messages': [{'role': 'user', 'text': 'CLI latest turn'}]}
        async def start(self, *args):
            raise AssertionError('Opening history must not mount a writer')
    app.runtime = ProbeRuntime()
    await app.management.perform('history.shared.open', {
        'id': 'shared-root', 'workspace': str(workspace)})
    selected = app._session()
    assert selected['id'] == 'shared-root'
    assert selected['workspace'] == str(workspace)
    assert selected['bundle'] == 'portable-bundle'
    assert selected['messages'][0]['text'] == 'CLI latest turn'
    assert app.state['settings']['workspace'] == str(workspace)
    assert not (app.data_dir / 'sessions' / 'shared-root' / 'checkpoint.json').exists()
    await app.management.perform('history.shared.open', {
        'id': 'shared-root', 'workspace': str(workspace)})
    assert sum(row['id'] == 'shared-root' for row in app.state['sessions']) == 1

async def test_turn_fork_excludes_later_messages(app):
    session=app._session()
    for role,text in [('user','one'),('assistant','answer one'),('user','two'),('assistant','answer two')]:app._message(session,role,text)
    await app.dispatch('session.fork',{'id':session['id'],'turn':1})
    assert [m['text'] for m in app._session()['messages']]==['one','answer one']

def test_notification_credentials_are_private_and_not_in_public_state(tmp_path):
    notifications=Notifications(tmp_path)
    public=notifications.save({'enabled':True,'topic':'private-topic','token':'secret-token','server':'https://notify.example'})
    assert 'topic' not in public and 'token' not in public
    assert public['topicConfigured'] and public['tokenConfigured']
    assert notifications.path.stat().st_mode & 0o777==0o600
    with pytest.raises(ValueError):notifications.save({'server':'http://insecure.example'})

async def test_transcript_file_import_keeps_tool_evidence_without_execution(app):
    rows=[{'role':'user','content':'Inspect'}, {'role':'assistant','content':'','tool_calls':[{'id':'call','function':{'name':'bash','arguments':'{}'}}]}, {'role':'tool','tool_call_id':'call','content':'done'}, {'role':'assistant','content':'Finished'}]
    await app.management.perform('history.importFile',{'content':json.dumps({'messages':rows,'metadata':{'bundle_name':'anchors'}}),'format':'json'})
    session=app._session()
    saved=SessionStore.for_app(app.data_dir,session['workspace']).load(session['id'])
    assert saved[0]==rows and session['status']=='stopped'
    assert saved[1]['jobs_replayed'] is False
    assert [m['text'] for m in session['messages']]==['Inspect','Finished']

async def test_import_hides_reminders_only_in_display_and_retains_literal_quotes(app):
    text='<system-reminder>Quoted by the user</system-reminder>'
    rows=[{'role':'user','content':text,'metadata':{'ephemeral':True,'persisted':True}},
          {'role':'user','content':text}, {'role':'assistant','content':'A literal quote'}]
    await app.management.perform('history.importFile',{'content':json.dumps(rows),'format':'json'})
    session=app._session()
    assert [m['text'] for m in session['messages']]==[text,'A literal quote']
    assert [m['nativeIndex'] for m in session['messages']]==[1,2]
    assert SessionStore.for_app(app.data_dir,session['workspace']).load(session['id'])[0]==rows

async def test_provider_status_survives_unrelated_operations_and_does_not_block_quick_checks(app,monkeypatch):
    import asyncio
    from amplifier_web.setup import SetupManager
    entered=asyncio.Event();finish=asyncio.Event()
    async def perform(self,action,args):
        if action=='providers.models':
            entered.set();await finish.wait()
            raise ValueError('Provider check timed out. Please retry.')
        return {'credentialCheck':{'module':args['module'],'available':True}}
    monkeypatch.setattr(SetupManager,'perform',perform)
    models=asyncio.create_task(app.management.command('providers.models',{'id':'openai'},'models'))
    await entered.wait()
    await asyncio.wait_for(app.management.command('providers.credentials',{'module':'provider-openai'},'env'),1)
    assert app.state['setup']['operations']['providers.models:openai']['phase']=='working'
    assert app.state['setup']['operations']['providers.credentials:provider-openai']['phase']=='ready'
    finish.set();await models
    await app.management.command('notifications.get',{},'notifications')
    assert app.state['management']['phase']=='ready'
    assert app.state['setup']['operations']['providers.models:openai']['error']=='Provider check timed out. Please retry.'

async def test_old_provider_results_do_not_replace_a_newer_check(app,monkeypatch):
    import asyncio
    from amplifier_web.setup import SetupManager
    entered=asyncio.Event();finish=asyncio.Event()
    async def perform(self,action,args):
        if args['envVar']=='OLD_KEY':entered.set();await finish.wait()
        return {'credentialCheck':{'module':args['module'],'envVar':args['envVar'],'available':True}}
    monkeypatch.setattr(SetupManager,'perform',perform)
    old=asyncio.create_task(app.management.command('providers.credentials',{'module':'provider-openai','envVar':'OLD_KEY'},'old'))
    await entered.wait()
    await app.management.command('providers.credentials',{'module':'provider-openai','envVar':'NEW_KEY'},'new')
    finish.set();await old
    assert app.state['setup']['credentialCheck']['envVar']=='NEW_KEY'
    assert app.state['setup']['operations']['providers.credentials:provider-openai']['commandId']=='new'

async def test_locations_browse_files_and_folders_without_reading_contents(app,tmp_path):
    folder=tmp_path/'workspace';folder.mkdir();(folder/'sub').mkdir();(folder/'file.json').write_text('private-content');(folder/'.hidden').write_text('private')
    await app.management.perform('locations.list',{'controlId':'fixture','path':str(folder)})
    listing=app.state['locationListing']
    assert [row['name'] for row in listing['entries']]==['sub','file.json']
    assert 'private-content' not in str(listing)
    await app.management.perform('locations.list',{'controlId':'fixture','path':str(folder),'directoriesOnly':True})
    assert [row['name'] for row in app.state['locationListing']['entries']]==['sub']

async def test_running_mount_plan_changes_queue_then_remount_same_conversation(app):
    import asyncio,copy
    plan={'session':{'orchestrator':{'module':'loop-live'},'context':{'module':'context-simple'}},'providers':[{'module':'provider-fixture'}],'tools':[{'module':'tool-fixture','enabled':False,'config':{'limit':2}}]}
    calls=[]
    class LiveRuntime:
        async def start(self,session,emit,**kwargs):
            if kwargs.get('guard'):kwargs['guard']()
            calls.append(('start',session['id']))
        async def stop(self,sid,*,reason="stopped"):calls.append(('stop',sid))
        async def close(self):pass
        async def control(self,sid,op,args,*,guard=None):
            if guard:guard()
            calls.append((op,sid))
            if op=='configuration.apply':
                from amplifier_web.configuration_recovery import receipt_path, seal
                from amplifier_web.host.config import write_private
                assert args['config']==plan
                attempt=args['_configurationAttempt']
                # Synthetic transport acknowledgement, not native-write proof.
                write_private(receipt_path(attempt,home=app.data_dir),
                    json.dumps(seal(attempt,'saved_not_mounted',terminal=True)))
                return {'requiresRestart':True}
            return {'plan':copy.deepcopy(plan)}
    app.runtime=LiveRuntime();session=app._session();session['status']='working'
    app._message(session,'user','Preserve this conversation')
    await app.management.perform('configuration.apply',{'id':session['id'],'config':plan,'whenIdle':True})
    assert session['pendingConfiguration']['phase']=='queued' and not calls
    session['status']='idle'
    task=app.management.pending_config_tasks[session['id']]
    await asyncio.wait_for(task,2)
    assert session['pendingConfiguration']['phase']=='ready'
    assert session['configuration']['plan']==plan
    assert session['messages'][0]['text']=='Preserve this conversation'
    assert all(sid==session['id'] for _,sid in calls)
    assert ('stop',session['id']) in calls

async def test_queued_mount_changes_can_be_cancelled(app):
    plan={'session':{'orchestrator':{'module':'loop-live'},'context':{'module':'context-simple'}},'providers':[{'module':'provider-fixture'}]}
    session=app._session();session['status']='working'
    await app.management.perform('configuration.apply',{'id':session['id'],'config':plan,'whenIdle':True})
    await app.management.perform('configuration.cancel',{'id':session['id']})
    assert not app.management.queued_path(session['id']).exists()
    assert 'pendingConfiguration' not in session


async def test_ui_stop_during_configuration_shutdown_does_not_restart(app, monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    session = app._session()
    entered, finish = asyncio.Event(), asyncio.Event()
    calls = []
    async def stop(sid, *, reason='stopped'):
        calls.append(('stop', reason)); entered.set(); await finish.wait()
    async def start(*args, **kwargs):
        if kwargs.get('guard'):kwargs['guard']()
        calls.append(('start', None))
    monkeypatch.setattr(app.management, 'ensure_runtime', AsyncMock())
    app.runtime.stop = stop
    app.runtime.start = start
    async def acknowledged_save(sid, op, args, **kwargs):
        from amplifier_web.configuration_recovery import receipt_path, seal
        from amplifier_web.host.config import write_private
        attempt=args['_configurationAttempt']
        write_private(receipt_path(attempt,home=app.data_dir),
            json.dumps(seal(attempt,'saved_not_mounted',terminal=True)))
        return {'requiresRestart': True}
    app.runtime.control = acknowledged_save
    plan = {'session': {'orchestrator': {'module': 'loop-live'}, 'context': {'module': 'context-simple'}},
            'providers': [{'module': 'provider-fixture'}]}
    applying = asyncio.create_task(app.management.perform('runtime.control',
        {'sessionId': session['id'], 'operation': 'configuration.apply', 'args': {'config': plan}}))
    await entered.wait()
    await app.dispatch('conversation.stop', {'sessionId': session['id']})
    finish.set()
    with pytest.raises(ValueError, match='stopped before restart'):
        await applying
    assert not any(kind == 'start' for kind, _ in calls)


async def test_native_export_uses_backup_without_repair_or_execution(app):
    from unittest.mock import AsyncMock
    from amplifier_web.session_files import project_slug
    session = app._session()
    store = SessionStore.for_app(app.data_dir, session['workspace'])
    rows = [{'role': 'user', 'content': 'fixture export'}, {'role': 'assistant', 'content': 'answer'}]
    store.save(session['id'], rows, {'bundle': 'anchors'})
    directory = store.directory(session['id'])
    (directory / 'transcript.jsonl').rename(directory / 'transcript.jsonl.backup')
    session.update(nativeProject=project_slug(session['workspace']), nativeIdentity=session['id'])
    before = (directory / 'transcript.jsonl.backup').read_bytes()
    app.management.download = AsyncMock()
    await app.management.perform('history.export', {'id': session['id'], 'format': 'json'})
    exported = json.loads(app.management.download.call_args.args[1])
    assert exported['messages'] == rows
    assert not (directory / 'transcript.jsonl').exists()
    assert (directory / 'transcript.jsonl.backup').read_bytes() == before
    assert any(row['code'] == 'recovered_backup' for row in app.state['historyDiagnostics'])
