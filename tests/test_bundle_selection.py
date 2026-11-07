import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from amplifier_web.bundle_selection import BundleTransaction, defaults, prepare_switch
from amplifier_web.host.storage import SessionStore
from amplifier_web.preferences import SettingsStore
from amplifier_web.service import AppService, AppError
from amplifier_web.management import Management
from amplifier_web.session_store import fork_session
from configuration_admin import authorized_admin


async def test_scoped_defaults_persist_and_do_not_retarget_existing_chats(tmp_path):
    home=tmp_path/'app'; workspace=tmp_path/'work'; workspace.mkdir()
    settings=SettingsStore(home)
    settings.update(workspace,'global',lambda s:s.update(bundle={'active':'anchors'},unrelated={'keep':True}))
    app=AppService(home,workspace=workspace)
    try:
        await app.dispatch('session.create',{})
        original=app._session()['id']
        admin=await authorized_admin(app,original,effects=['bundles'],scopes=['global'])
        await admin('dispatch',{'action':'bundle.default','args':{'scope':'app','bundle':'work'}})
        assert app._new_session({})['bundle']=='work'
        assert settings.read(workspace)['bundle']['active']=='anchors'
        assert app._session(original)['bundle']=='anchors'
        await app.dispatch('bundle.default',{'scope':'workspace','bundle':'anchors-amp-dev'})
        assert app._new_session({})['bundle']=='anchors-amp-dev'
        assert defaults(home,workspace,'work')['source']=='workspace'
        await app.dispatch('bundle.default',{'scope':'workspace','bundle':None})
        assert app._new_session({})['bundle']=='work'
        await app.dispatch('bundle.default',{'scope':'shared','bundle':'anchors-work'})
        assert app._new_session({})['bundle']=='work'
        assert settings.read(workspace)['unrelated']=={'keep':True}
    finally: await app.close()
    app=AppService(home,workspace=workspace)
    try:
        assert app._new_session({})['bundle']=='work'
        await app.dispatch('bundle.default',{'scope':'app','bundle':None})
        assert app._new_session({})['bundle']=='anchors-work'
    finally: await app.close()


def seed(home,workspace):
    store=SessionStore.for_app(home,workspace)
    messages=[{'role':'system','content':'Old instructions'}, {'role':'user','content':'Keep this history'},
              {'role':'assistant','content':'Done','tool_calls':[{'id':'call','name':'bash','arguments':{}}]},
              {'role':'tool','tool_call_id':'call','name':'bash','content':'actual result'}]
    store.save('source',messages,{'bundle_name':'anchors'},preserve_system=True)
    local=home/'sessions/source';local.mkdir(parents=True)
    (local/'configuration.json').write_text('{"tools":[{"module":"old-tool"}]}')
    (local/'control-state.json').write_text(json.dumps({'selection':{'instance':'test','model':'chosen','effort':'high'},
                                                      'goal':None,'mode':'old','budget':{'contextTokens':2}}))
    return store,messages


def test_transition_recovers_all_original_files_after_interruption(tmp_path):
    home=tmp_path/'app';store,messages=seed(home,tmp_path)
    transaction=BundleTransaction(home,tmp_path,'source')
    before={key:path.read_bytes() if path.exists() else None for key,path in transaction.paths.items()}
    transaction.begin();transaction.select('work')
    assert store.load('source')[1]['bundle_name']=='work'
    assert store.load('source')[0]==messages[1:]
    assert not (transaction.local/'configuration.json').exists()
    assert json.loads((transaction.local/'control-state.json').read_text())=={'selection':{'instance':'test','model':'chosen','effort':'high'},'goal':None}
    assert BundleTransaction(home,tmp_path,'source').restore()
    assert {key:path.read_bytes() if path.exists() else None for key,path in transaction.paths.items()}==before
    assert not transaction.path.exists()


def test_fork_with_bundle_keeps_tools_voice_and_original_but_replaces_instructions(tmp_path):
    home=tmp_path/'app';store,messages=seed(home,tmp_path)
    before=(store.directory('source')/'transcript.jsonl').read_bytes()
    source={'id':'source','status':'idle','workspace':str(tmp_path),'bundle':'anchors',
            'messages':[{'role':'user','text':'Keep this history','nativeIndex':1},
                        {'role':'assistant','text':'Spoken response','voiceId':'voice','via':'call'}]}
    result=fork_session(home,source,'fork',bundle='work')
    saved,meta=store.load('fork')
    assert saved[:3]==messages[1:]
    assert 'Spoken response' in saved[-1]['content']
    assert meta['bundle_name']=='work' and not meta['preserve_system']
    assert result['messages'][0]['nativeIndex']==0
    assert not (home/'sessions/fork/configuration.json').exists()
    assert (store.directory('source')/'transcript.jsonl').read_bytes()==before


@pytest.mark.parametrize('reviewed', [False, True])
async def test_prepare_switch_checkpoints_without_loading_candidate_or_mutating_history(tmp_path, monkeypatch, reviewed):
    from unittest.mock import AsyncMock
    home = tmp_path/'app'; store, messages = seed(home, tmp_path)
    result = {'bundle': 'work', 'fingerprint': 'same', 'selection': None, 'modelCompatible': True}
    async def inspect(*args): return result.copy(), object()
    monkeypatch.setattr('amplifier_web.bundle_selection.inspect_bundle', inspect)
    checkpoint = AsyncMock()
    worker = SimpleNamespace(controls=SimpleNamespace(require_idle=lambda: None, checkpoint=checkpoint),
        workspace=tmp_path, bundle_preview={**result, 'previewId': 'reviewed'})
    before = {key: path.read_bytes() if path.exists() else None
              for key, path in BundleTransaction(home, tmp_path, 'source').paths.items()}
    args = {'bundle': 'work', **({'previewId': 'reviewed'} if reviewed else {})}
    assert (await prepare_switch(worker, args))['replacement'] == {**result, 'resetModel': False}
    checkpoint.assert_awaited_once()
    assert {key: path.read_bytes() if path.exists() else None
            for key, path in BundleTransaction(home, tmp_path, 'source').paths.items()} == before
    assert store.load('source')[0] == messages
    assert not (home/'sessions/source/bundle-transition.json').exists()


class Runtime:
    def __init__(self): self.calls=[];self.preview=None;self.fail=False;self.starts=[];self.stops=[]
    async def start(self,session,emit, *, guard=None):
        if guard is not None: guard()
        self.starts.append(copy.deepcopy(session))
        if self.fail and session.get('bundleReplacement'): raise ValueError('Load failed; original restored')
        await emit('runtime.status',{'sessionId':session['id'],'status':'ready'})
    async def stop(self,*args,reason="stopped"): self.stops.append((args, reason))
    async def close(self): pass
    async def control(self,sid,operation,args):
        self.calls.append((sid,operation,args))
        if operation=='bundle.preview':
            self.preview={'previewId':'token','bundle':args['bundle'],'fingerprint':'same','selection':None,'modelCompatible':True,'changes':{}}
            return self.preview
        if operation=='bundle.prepare':
            return {'replacement': {'bundle': args['bundle'], 'fingerprint': 'same', 'selection': None, 'modelCompatible': True, 'resetModel': args.get('resetModel',False)}}
        if operation=='history.snapshot': return {'messages':[{'role':'user','content':'keep'}]}
        if operation=='configuration.inspect':return {'plan':{}}
        return {'providers':[]}

async def settled(app):
    for _ in range(200):
        if not app.tasks: return
        await asyncio.sleep(.005)
    raise AssertionError('operation did not settle')

async def saved_preview_fixture(*args, **kwargs):
    return {'bundle': args[2], 'fingerprint': 'same', 'selection': None,
            'modelCompatible': True, 'changes': {}}


async def test_agent_preview_switch_failure_and_duplicate_actions(tmp_path, monkeypatch):
    monkeypatch.setattr('amplifier_web.draft_defaults.resolve_defaults', saved_preview_fixture)
    runtime=Runtime();app=AppService(tmp_path,runtime=runtime,workspace=tmp_path);app.management=Management(app)
    try:
        await app.dispatch('session.create',{'bundle':'anchors'})
        sid=app._session()['id'];app._session()['messages']=[{'role':'user','text':'keep'}]
        admin=await authorized_admin(app,sid,effects=['bundles'],scopes=['session'])
        await app.app_bridge('dispatch',{'action':'bundle.preview','args':{'sessionId':sid,'bundle':'work'}},sid)
        await settled(app)
        assert app._session()['bundlePreview']['bundle']=='work'
        runtime.fail=True
        assert not runtime.starts and not runtime.calls
        args={'sessionId':sid,'bundle':'work','previewId':app._session()['bundlePreview']['previewId']}
        await admin('dispatch',{'action':'bundle.switch','args':args})
        await settled(app)
        assert app._session()['bundle']=='anchors'
        assert app._session()['bundleChange']['phase']=='error'
        assert not app._session()['configurationBusy']
        runtime.fail=False
        await app.dispatch('bundle.switch',args,command_id='switch-once');await settled(app)
        await app.dispatch('bundle.switch',args,command_id='switch-once');await settled(app)
        assert app._session()['bundle']=='work'
        assert len([row for row in runtime.calls if row[1]=='bundle.prepare'])==2
        assert app._session()['messages']==[{'role':'user','text':'keep'}]
        app._session()['status']='working'
        await app.dispatch('bundle.preview',{'sessionId':sid,'bundle':'anchors'})
        await settled(app)
        assert app.state['actionStatus']['bundle.preview']['phase']=='ready'
    finally: await app.close()

@pytest.mark.parametrize('reviewed',[False,True])
async def test_agent_fork_is_independent_and_duplicate_does_not_repeat(tmp_path,reviewed,monkeypatch):
    monkeypatch.setattr('amplifier_web.draft_defaults.resolve_defaults', saved_preview_fixture)
    runtime=Runtime();app=AppService(tmp_path,runtime=runtime,workspace=tmp_path);app.management=Management(app)
    try:
        await app.dispatch('session.create',{'bundle':'anchors'})
        source=app._session();sid=source['id']
        admin=await authorized_admin(app,sid,effects=['bundles'],scopes=['session'])
        source['messages']=[{'id':'user','role':'user','text':'keep'}]
        if reviewed:
            await app.app_bridge('dispatch',{'action':'bundle.preview','args':{'sessionId':sid,'bundle':'work'}},sid)
            await settled(app)
        payload={'action':'bundle.fork','id':'fork-once','args':{'sessionId':sid,'bundle':'work',**({'previewId':source['bundlePreview']['previewId']} if reviewed else {})}}
        await admin('dispatch',payload);await settled(app)
        child=app._session();assert child['id']!=sid and child['bundle']=='work'
        assert child['messages']==[{'id':'user','role':'user','text':'keep','nativeIndex':0}]
        assert source['bundle']=='anchors' and source['messages']==[{'id':'user','role':'user','text':'keep'}]
        await admin('dispatch',payload);await settled(app)
        assert len(app.state['sessions'])==2
        assert child['forkTranscript']['jobsReplayed'] is False
    finally: await app.close()


@pytest.mark.parametrize('operation', ['bundle.switch', 'bundle.prepare'])
async def test_generic_runtime_action_cannot_bypass_root_switch_policy(tmp_path, operation):
    runtime=Runtime();app=AppService(tmp_path,runtime=runtime,workspace=tmp_path);app.management=Management(app)
    try:
        await app.dispatch('session.create',{})
        sid=app._session()['id']
        await app.dispatch('runtime.control',{'sessionId':sid,'operation':operation,'args':{'bundle':'work'}})
        await settled(app)
        assert app.state['actionStatus']['runtime.control']['phase']=='error'
        assert not runtime.calls
    finally: await app.close()


async def test_unavailable_model_requires_explicit_reset_before_any_mutation(tmp_path,monkeypatch):
    home=tmp_path/'app';store,messages=seed(home,tmp_path)
    inspected={'bundle':'work','fingerprint':'same','selection':{'instance':'missing','model':'chosen'},'modelCompatible':False}
    async def inspect(*args):return inspected.copy(),object()
    monkeypatch.setattr('amplifier_web.bundle_selection.inspect_bundle',inspect)
    worker=SimpleNamespace(controls=SimpleNamespace(require_idle=lambda:None),workspace=tmp_path,
                           bundle_preview={**inspected,'previewId':'token'})
    result=await prepare_switch(worker,{'bundle':'work'})
    assert result['requiresModelChoice'] and not result['preview']['modelCompatible']
    assert worker.bundle_preview['previewId']==result['preview']['previewId']
    assert store.load('source')[0]==messages
    assert not (home/'sessions/source/bundle-transition.json').exists()


async def test_agent_direct_switch_reports_model_choice_then_retries_explicitly(tmp_path):
    class ModelRuntime(Runtime):
        async def control(self,sid,operation,args):
            if operation=='bundle.prepare' and not args.get('resetModel'):
                self.calls.append((sid,operation,args))
                return {'requiresModelChoice':True,'preview':{'bundle':'work','previewId':'model-choice','modelCompatible':False}}
            return await super().control(sid,operation,args)
    runtime=ModelRuntime();app=AppService(tmp_path,runtime=runtime,workspace=tmp_path);app.management=Management(app)
    try:
        await app.dispatch('session.create',{'bundle':'anchors'})
        sid=app._session()['id']
        app._session()['selection']={'instance':'old','model':'pinned'}
        admin=await authorized_admin(app,sid,effects=['bundles'],scopes=['session'])
        await admin('dispatch',{'action':'bundle.switch','args':{'sessionId':sid,'bundle':'work'}})
        await settled(app)
        assert app._session()['bundle']=='anchors'
        assert app._session()['bundlePreview']['previewId']=='model-choice'
        assert app._session()['bundleChange']['phase']=='error'
        assert app._session()['selection']['model']=='pinned'
        assert not app._session()['configurationBusy']
        await admin('dispatch',{'action':'bundle.switch','args':{'sessionId':sid,'bundle':'work','previewId':'model-choice','resetModel':True}})
        await settled(app)
        assert app._session()['bundle']=='work'
        assert 'selection' not in app._session()
        assert not any(op=='bundle.preview' for _,op,_ in runtime.calls)
    finally: await app.close()


async def test_stale_reviewed_preview_rejected_before_checkpoint(tmp_path,monkeypatch):
    checked={'bundle':'work','fingerprint':'new','selection':None,'modelCompatible':True}
    async def inspect(*args):return checked,object()
    monkeypatch.setattr('amplifier_web.bundle_selection.inspect_bundle',inspect)
    worker=SimpleNamespace(controls=SimpleNamespace(require_idle=lambda:None),workspace=tmp_path,
                           bundle_preview={**checked,'fingerprint':'old','previewId':'token'})
    with pytest.raises(ValueError,match='Preview it again'):
        await prepare_switch(worker,{'bundle':'work','previewId':'token'})


def test_resolved_root_is_one_use_and_rejects_changed_settings(tmp_path):
    from amplifier_web.host.session import ResolvedRoot
    from amplifier_web.host.config import HostConfig
    config=HostConfig(tmp_path,tmp_path,{'bundle':{'active':'work'}},tmp_path/'cache')
    root=(object(),object(),'resolved')
    candidate=ResolvedRoot(copy.deepcopy(config),'work',root)
    changed=copy.deepcopy(config);changed.settings['bundle']['active']='other'
    with pytest.raises(ValueError,match='changed'):candidate.take(changed,'work')
    with pytest.raises(ValueError,match='changed'):candidate.take(config,'other')
    assert candidate.take(config,'work') is root
    with pytest.raises(ValueError,match='changed'):candidate.take(config,'work')


@pytest.mark.parametrize('separate_checkout', [False, True])
async def test_inspected_bundle_binds_effective_execution_checkout(tmp_path, monkeypatch, separate_checkout):
    from unittest.mock import AsyncMock
    from amplifier_web.bundle_selection import inspect_bundle
    from amplifier_web.host.config import HostConfig
    history = tmp_path / 'history'; history.mkdir()
    checkout = tmp_path / 'checkout'; checkout.mkdir()
    effective = checkout if separate_checkout else history
    capabilities = {'session.working_dir': str(checkout)} if separate_checkout else {}
    config = HostConfig(tmp_path, history, {}, tmp_path / 'cache')
    plan = {'session': {'orchestrator': {'module': 'loop-live'}, 'context': {'module': 'context-simple'}},
            'providers': [{'module': 'provider-fixture'}]}
    root = (object(), SimpleNamespace(to_mount_plan=lambda: copy.deepcopy(plan)), 'resolved')
    load = AsyncMock(return_value=root)
    monkeypatch.setattr('amplifier_web.host.config.load_config', lambda *a, **kw: config)
    monkeypatch.setattr('amplifier_web.host.session.load_root_bundle', load)
    controls = SimpleNamespace(session=SimpleNamespace(session_id='fixture',
        coordinator=SimpleNamespace(get_capability=capabilities.get)), selection=None,
        configuration=lambda: {'plan': plan}, state_path=lambda: tmp_path / 'control-state.json')
    _, receipt = await inspect_bundle(controls, history, 'work')
    load.assert_awaited_once_with(config, 'work', execution_workspace=effective)
    assert config.workspace == history
    with pytest.raises(ValueError, match='changed'):
        receipt.take(config, 'work', execution_workspace=tmp_path / 'different-checkout')
    assert receipt.root is root  # A failed comparison cannot consume the reviewed plan.
    assert receipt.take(config, 'work', execution_workspace=effective / '.') is root
    with pytest.raises(ValueError, match='changed'):
        receipt.take(config, 'work', execution_workspace=effective)


@pytest.mark.parametrize('compatible', [False, True])
async def test_unavailable_bundle_can_be_previewed_and_replaced_without_starting_it(tmp_path, monkeypatch, compatible):
    runtime = Runtime()
    starts = []
    async def start(session, emit, *, guard=None):
        if guard is not None: guard()
        starts.append(copy.deepcopy(session))
        assert session['bundle'] == 'work'
        assert session['bundleReplacement']['bundle'] == 'work'
        await emit('runtime.status', {'sessionId': session['id'], 'status': 'ready'})
    runtime.start = start
    async def resolve(*args, **kwargs):
        assert args[2] == 'work'
        assert kwargs['session_id']
        return {'bundle': 'work', 'fingerprint': 'fresh', 'selection': {'instance': 'test'},
                'modelCompatible': compatible, 'changes': {}, 'recovery': True}
    monkeypatch.setattr('amplifier_web.draft_defaults.resolve_defaults', resolve)
    app = AppService(tmp_path, runtime=runtime, workspace=tmp_path)
    app.management = Management(app)
    from amplifier_web.bundle_actions import perform
    try:
        # Seed directly so setup itself does not prepare the deliberately broken root.
        source = app._new_session({'bundle': 'missing-root'})
        source.update(status='error', errorType='RuntimeStartupError', error='Missing root',
                      failure={'category': 'worker_startup'}, selection={'instance': 'test'})
        app.state['sessions'].append(source)
        args = {'sessionId': source['id'], 'bundle': 'work'}
        await perform(app.management, 'bundle.preview', args)
        assert not starts and not runtime.calls
        preview = source['bundlePreview']
        if not compatible:
            with pytest.raises(ValueError, match='pinned model'):
                await perform(app.management, 'bundle.switch', args)
            assert not starts and source['bundle'] == 'missing-root'
            args['resetModel'] = True
        else:
            args['previewId'] = preview['previewId']
        await perform(app.management, 'bundle.switch', args)
        assert len(starts) == 1
        assert source['bundle'] == 'work'
        assert source['bundleChange']['phase'] == 'ready'
        assert not source['configurationBusy']
        assert not any(op in {'bundle.switch', 'bundle.preview'} for _, op, _ in runtime.calls)
    finally:
        await app.close()


async def test_replacement_resolves_characteristics_without_loading_saved_root(tmp_path, monkeypatch):
    from amplifier_web.bundle_selection import inspect_replacement, begin_replacement
    home = tmp_path/'app'
    store, messages = seed(home, tmp_path)
    loaded = []
    plan = {'session': {'orchestrator': {'module': 'loop-live'}, 'context': {'module': 'context-simple'}},
            'providers': [{'module': 'provider-test', 'instance_id': 'test'}]}
    async def root(config, chosen, **kwargs):
        assert chosen == 'new-root'
        loaded.append(chosen)
        return None, SimpleNamespace(to_mount_plan=lambda: copy.deepcopy(plan)), chosen
    monkeypatch.setattr('amplifier_web.host.session.load_root_bundle', root)
    checked, _ = await inspect_replacement(home, tmp_path, 'source', 'new-root')
    assert checked['modelCompatible'] and checked['selection']['model'] == 'chosen'
    config = {'id': 'source', 'bundleReplacement': checked}
    journal, resolved = await begin_replacement(home, tmp_path, config)
    assert store.load('source')[1]['bundle_name'] == 'new-root'
    assert store.load('source')[0] == messages[1:]
    assert json.loads((home/'sessions/source/control-state.json').read_text())['selection']['model'] == 'chosen'
    journal.restore()
    assert store.load('source')[0] == messages
    plan['providers'][0]['instance_id'] = 'other'
    with pytest.raises(ValueError, match='configuration changed'):
        await begin_replacement(home, tmp_path, config)
    assert store.load('source')[1]['bundle_name'] == 'anchors'
    checked, _ = await inspect_replacement(home, tmp_path, 'source', 'new-root')
    assert not checked['modelCompatible']
    with pytest.raises(ValueError, match='pinned model'):
        await begin_replacement(home, tmp_path, {'id': 'source', 'bundleReplacement': checked})
    journal, _ = await begin_replacement(home, tmp_path, {'id': 'source', 'bundleReplacement': {**checked, 'resetModel': True}})
    assert 'selection' not in json.loads((home/'sessions/source/control-state.json').read_text())
    journal.commit()
    assert store.load('source')[1]['bundle_name'] == 'new-root'
    assert loaded and set(loaded) == {'new-root'}


@pytest.mark.parametrize('interruption', ['after-prepare', 'during-stop'])
async def test_stopped_conversation_is_not_restarted_by_bundle_switch(tmp_path, interruption):
    from amplifier_web.bundle_actions import perform
    runtime = Runtime(); app = AppService(tmp_path, runtime=runtime, workspace=tmp_path)
    app.management = Management(app)
    source = app._new_session({'bundle':'anchors'}); app.state['sessions'].append(source)
    control, stop = runtime.control, runtime.stop
    async def controlled(sid, operation, args):
        result = await control(sid, operation, args)
        if operation == 'bundle.prepare' and interruption == 'after-prepare':
            source['interruptionRevision'] = 1
        return result
    async def stopped(*args, **kwargs):
        await stop(*args, **kwargs)
        if interruption == 'during-stop': source['interruptionRevision'] = 1
    runtime.control, runtime.stop = controlled, stopped
    try:
        with pytest.raises(ValueError, match='stopped before changing bundles'):
            await perform(app.management, 'bundle.switch', {'sessionId':source['id'],'bundle':'work'})
        assert len(runtime.starts)==1 and not runtime.starts[0].get('bundleReplacement')
        assert source['bundle']=='anchors' and not source['configurationBusy']
    finally: await app.close()


def test_real_bundle_switch_isolates_module_revisions_and_preserves_history(tmp_path):
    import os
    import subprocess
    import sys
    python = os.environ.get('UNIFIED_RUNTIME_PYTHON')
    if not python:
        pytest.skip('Set UNIFIED_RUNTIME_PYTHON for the owned Core/Foundation bundle-switch integration')
    result = subprocess.run([sys.executable, str(Path(__file__).parent/'fixtures/bundle_switch_probe.py'), str(tmp_path/'probe')],
        capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"fresh_worker_per_revision": true' in result.stdout
    assert '"failed_mount_diagnostic": "provider_mount_failed"' in result.stdout
    assert '"model_calls": 0' in result.stdout


@pytest.mark.parametrize('serialized', [False, True])
def test_probe_snapshot_serializes_with_native_checkpoint(tmp_path, monkeypatch, serialized):
    """Force the actual cross-process writer race, including its negative control."""
    import queue
    import subprocess
    import sys
    import threading
    from amplifier_foundation.session import history
    from fixtures.bundle_switch_probe import saved_history

    store = SessionStore(tmp_path / 'sessions')
    messages = [{'role': 'user', 'content': 'Keep the saved conversation'}]
    store.save('source', messages, {'status': 'idle'})
    writer = subprocess.Popen([sys.executable, '-c', '''
import sys
from pathlib import Path
from filelock import FileLock, Timeout
from amplifier_foundation.session.history import SessionHistoryStore
directory = Path(sys.argv[1])
store = SessionHistoryStore(directory)
rows = store.load_messages()
metadata = store.load_metadata()
print('ready', flush=True)
assert sys.stdin.readline().strip() == 'checkpoint'
try:
    with FileLock(directory.parent / f'.{directory.name}.metadata.lock', timeout=0):
        pass
except Timeout:
    print('blocked', flush=True)
else:
    print('unlocked', flush=True)
# Use the real portable checkpoint writer, including transcript replacement
# with unchanged content. The runtime's checkpoint saves share this lock.
store.save(rows, {**metadata, 'status': 'completed'}, merge_metadata=True)
print('saved', flush=True)
''', str(store.directory('source'))], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)
    receipts = queue.Queue()
    def read_receipts():
        for line in writer.stdout:
            receipts.put(line.strip())
        receipts.put(None)
    reader = threading.Thread(target=read_receipts, daemon=True)
    reader.start()

    def receipt():
        line = receipts.get(timeout=5)
        assert line is not None, 'Checkpoint writer exited before its receipt'
        return line

    original = history._read_messages
    reads = 0
    def concurrent_checkpoint(path, **kwargs):
        nonlocal reads
        rows = original(path, **kwargs)
        reads += 1
        writer.stdin.write('checkpoint\n'); writer.stdin.flush()
        assert receipt() == ('blocked' if serialized else 'unlocked')
        if not serialized:
            assert receipt() == 'saved'
        return rows

    try:
        assert receipt() == 'ready'
        with monkeypatch.context() as patch:
            patch.setattr(history, '_read_messages', concurrent_checkpoint)
            if serialized:
                rows, metadata = saved_history(store, 'source')
                assert rows == messages and metadata['status'] == 'idle'
            else:
                with pytest.raises(ValueError, match='saved session changed while it was being read'):
                    store.load('source')
        if serialized:
            assert receipt() == 'saved'
        assert writer.wait(timeout=5) == 0, writer.stderr.read()
        assert reads == 1  # No retry of the guarded snapshot.
        rows, metadata = saved_history(store, 'source')
        assert rows == messages and metadata['status'] == 'completed'
    finally:
        if writer.poll() is None:
            writer.kill(); writer.wait(timeout=5)
        reader.join(timeout=5)
        assert not reader.is_alive()


@pytest.mark.parametrize('revocation', ['after-prepare', 'during-stop'])
async def test_revoked_bundle_authority_cannot_restart_worker(tmp_path, monkeypatch, revocation):
    from amplifier_web.bundle_actions import perform
    revoked = False
    def check():
        if revoked: raise ValueError('Configuration authority revoked')
    monkeypatch.setattr('amplifier_web.configuration_authority.check_current', check)
    runtime = Runtime(); app = AppService(tmp_path, runtime=runtime, workspace=tmp_path)
    app.management = Management(app)
    source = app._new_session({'bundle':'anchors'}); app.state['sessions'].append(source)
    control, stop = runtime.control, runtime.stop
    async def controlled(sid, operation, args):
        nonlocal revoked
        result = await control(sid, operation, args)
        if operation == 'bundle.prepare' and revocation == 'after-prepare': revoked = True
        return result
    async def stopped(*args, **kwargs):
        nonlocal revoked
        await stop(*args, **kwargs)
        if revocation == 'during-stop': revoked = True
    runtime.control, runtime.stop = controlled, stopped
    try:
        with pytest.raises(ValueError, match='authority revoked'):
            await perform(app.management, 'bundle.switch', {'sessionId':source['id'],'bundle':'work'})
        assert len(runtime.starts)==1 and not runtime.starts[0].get('bundleReplacement')
        assert source['bundle']=='anchors' and not source['configurationBusy']
    finally: await app.close()


async def test_becoming_busy_during_bundle_resolution_cannot_checkpoint(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    busy = False
    def idle():
        if busy: raise ValueError('Current turn is busy')
    async def inspect(*args):
        nonlocal busy
        busy = True
        return {'bundle':'work','fingerprint':'same','selection':None,'modelCompatible':True}, object()
    monkeypatch.setattr('amplifier_web.bundle_selection.inspect_bundle', inspect)
    checkpoint = AsyncMock()
    worker = SimpleNamespace(controls=SimpleNamespace(require_idle=idle, checkpoint=checkpoint), workspace=tmp_path)
    with pytest.raises(ValueError, match='busy'):
        await prepare_switch(worker, {'bundle':'work'})
    checkpoint.assert_not_awaited()


@pytest.mark.parametrize('boundary', ['history', 'service-lock'])
async def test_restart_guard_rechecks_after_history_and_service_lock_waits(tmp_path, boundary):
    runtime = Runtime(); app = AppService(tmp_path, runtime=runtime, workspace=tmp_path)
    management = Management(app)
    source = app._new_session({'bundle': 'anchors'})
    source.update(historyManaged=True, historyLoaded=False)
    app.state['sessions'].append(source)
    entered, release = asyncio.Event(), asyncio.Event()
    revoked = False
    async def load(_):
        entered.set()
        if boundary == 'history': await release.wait()
    app.history.ensure_loaded = load
    def guard():
        if revoked: raise ValueError('Restart no longer authorized')
    if boundary == 'service-lock': await app.lock.acquire()
    task = asyncio.create_task(management.ensure_runtime(copy.deepcopy(source),
        bundle_replacement={'bundle':'work'}, restart_guard=guard))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        revoked = True
        if boundary == 'history': release.set()
        else: app.lock.release()
        with pytest.raises(ValueError, match='no longer authorized'):
            await asyncio.wait_for(task, 2)
        assert not runtime.starts
        assert source['historyManaged'] and source['bundle'] == 'anchors'
    finally:
        release.set()
        if app.lock.locked(): app.lock.release()
        await asyncio.gather(task, return_exceptions=True)
        await app.close()
