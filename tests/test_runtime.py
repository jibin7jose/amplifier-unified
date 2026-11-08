"""Process contract tests use an explicit fixture, never assert live model success."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

import pytest

from amplifier_web.runtime import RuntimeManager, normalize_event
from amplifier_web.runtime_worker import Worker
from amplifier_web.shared_state import ActivationGate

FIXTURE = r'''
import json,sys
for line in sys.stdin:
    data=json.loads(line); op=data['op']
    if op=='start':
        root=data['session']['id']
        if data['session']['id']=='slow':
            print(json.dumps({'type':'runtime.progress','phase':'bundle-preparation','detail':'Loading configured modules.'}),flush=True)
        else:
            print(json.dumps({'type':'runtime.ready','report':{'resumed':True,'session_id':root,'owner_session_id':data['session'].get('ownerSessionId')}}),flush=True)
            if root=='native-root':
                print(json.dumps({'type':'generation.started','session_id':'child','root_session_id':root}),flush=True)
    elif op=='send':
        print(json.dumps({'type':'input.delivered','input_id':data['input_id']}),flush=True)
        for identity in (root,root+'-child'):
            print(json.dumps({'type':'execution.event','event':{'id':identity+'-call','kind':'llm','phase':'completed','sessionId':identity,'rootSessionId':root}}),flush=True)
        print(json.dumps({'op':'bridge','id':'bridge1','operation':'get_state','args':{}}),flush=True)
        print(json.dumps({'op':'reply','id':data['id'],'result':{'accepted':True}}),flush=True)
    elif op=='bridge.result':
        print(json.dumps({'type':'assistant.message','text':str(data.get('result',{}).get('revision'))}),flush=True)
    elif op=='stop':
        break
    else:
        print(json.dumps({'op':'reply','id':data['id'],'error':'Not available'}),flush=True)
'''


@pytest.mark.parametrize('boundary', ['admission', 'status', 'qualification', 'spawn'])
async def test_restart_authority_is_rechecked_after_startup_waits(tmp_path, monkeypatch, boundary):
    """A queued restart cannot mount after Stop/revocation; reap any spawned child."""
    writes = tmp_path/'controls.jsonl'
    fixture = "import json,sys\nfor line in sys.stdin:\n open(sys.argv[1], 'a').write(line)\n if json.loads(line)['op']=='stop': break\n"
    manager = RuntimeManager(command=[sys.executable, '-u', '-c', fixture, str(writes)],
        startup_timeout=2, retention={'prewarm_on_select': False})
    manager.home = tmp_path/'app'
    session = {'id':'guarded', 'workspace':str(tmp_path), 'bundle':'fixture'}
    entered, release = asyncio.Event(), asyncio.Event()
    revoked, created = False, []
    real_spawn = asyncio.create_subprocess_exec
    def guard():
        if revoked: raise ValueError('Restart no longer authorized')
    async def emit(kind, payload):
        if boundary == 'status' and kind == 'runtime.status' and not entered.is_set():
            entered.set(); await release.wait()
    async def spawn(*args, **kwargs):
        proc = await real_spawn(*args, **kwargs)
        created.append(proc)
        if boundary == 'spawn': entered.set(); await release.wait()
        return proc
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    if boundary == 'qualification':
        command = manager.command
        manager.command = None
        monkeypatch.setattr(manager, '_command', lambda *args, **kwargs: command)
        async def qualify(*args, **kwargs):
            entered.set(); await release.wait()
            return None
        monkeypatch.setattr('amplifier_web.runtime_profiles.ensure', qualify)
    if boundary == 'admission':
        lock = manager._locks.setdefault(session['id'], asyncio.Lock())
        await lock.acquire()
    task = asyncio.create_task(manager.start(session, emit, guard=guard))
    try:
        if boundary == 'admission':
            await asyncio.sleep(0)
            assert not task.done()
        else: await asyncio.wait_for(entered.wait(), 2)
        revoked = True
        if boundary == 'admission': lock.release()
        else: release.set()
        with pytest.raises((ValueError, RuntimeError)):
            await asyncio.wait_for(task, 5)
        commands = [json.loads(line)['op'] for line in writes.read_text().splitlines()] if writes.exists() else []
        assert commands == (['stop'] if boundary == 'spawn' else [])  # No startup/configuration or input admission.
        assert len(created) == (1 if boundary == 'spawn' else 0)
        assert all(proc.returncode is not None for proc in created)
        assert not manager.workers and not manager._preparations
        assert not list((manager.home/'updates/worker-leases').glob('*.json'))
    finally:
        release.set()
        if boundary == 'admission' and lock.locked(): lock.release()
        await asyncio.gather(task, return_exceptions=True)
        await manager.close()


@pytest.mark.parametrize('cancellation', ['none', 'heartbeat', 'heartbeat-and-reap', 'lease-error'])
async def test_startup_heartbeat_cleanup_keeps_process_ownership(tmp_path, monkeypatch, cancellation):
    """A real child must be registered or reaped before startup relinquishes it."""
    writes = tmp_path / 'controls.jsonl'
    fixture = r'''
import json,sys
for line in sys.stdin:
    with open(sys.argv[1], 'a') as output:
        output.write(line)
    command = json.loads(line)
    if command['op'] == 'start':
        print(json.dumps({'type': 'runtime.ready', 'report': {}}), flush=True)
    elif command['op'] == 'stop':
        break
'''
    manager = RuntimeManager(command=[sys.executable, '-u', '-c', fixture, str(writes)],
                             startup_timeout=3, retention={'prewarm_on_select': False})
    manager.home = tmp_path / 'app'
    session = {'id': 'cleanup-owner', 'workspace': str(tmp_path), 'bundle': 'fixture'}
    created, heartbeats, attempted, events = [], [], [], []
    child_lease = None
    lease_removals = []
    lease_error = OSError('fixture lease acquisition failure')
    outer = None
    injected = []
    checking = True
    loop = asyncio.get_running_loop()
    previous_factory = loop.get_task_factory()
    real_spawn = asyncio.create_subprocess_exec
    real_write = manager._write
    from amplifier_web import generation_leases
    real_acquire = generation_leases.acquire
    real_unlink = Path.unlink

    def tracked_acquire(home, generation, pid):
        nonlocal child_lease
        if created and pid == created[0].pid and cancellation == 'lease-error':
            raise lease_error
        result = real_acquire(home, generation, pid)
        if created and pid == created[0].pid:
            child_lease = result
        return result

    def tracked_unlink(path, *args, **kwargs):
        if checking and path == child_lease:
            assert created[0].returncode is not None
            lease_removals.append(path)
        return real_unlink(path, *args, **kwargs)

    async def tracked_write(row, data):
        attempted.append(data['op'])
        return await real_write(row, data)

    class PreparationHeartbeat(asyncio.Task):
        def cancel(self, msg=None):
            if not injected and cancellation.startswith('heartbeat') and checking:
                assert outer is not None and len(created) == 1
                assert created[0].returncode is None
                # Select the cancellation boundary without replacing the
                # heartbeat coroutine, subprocess, or manager's cleanup.
                injected.append('heartbeat')
                loop.call_soon(outer.cancel)
            return super().cancel(msg)

    def task_factory(loop, coro, **kwargs):
        if coro.cr_code is RuntimeManager._preparation_progress.__code__:
            task = PreparationHeartbeat(coro, loop=loop, **kwargs)
            heartbeats.append(task)
            return task
        if previous_factory:
            return previous_factory(loop, coro, **kwargs)
        return asyncio.Task(coro, loop=loop, **kwargs)

    async def tracked_spawn(*args, **kwargs):
        proc = await real_spawn(*args, **kwargs)
        created.append(proc)
        real_wait = proc.wait

        async def tracked_wait():
            if checking and cancellation == 'heartbeat-and-reap' and 'reap' not in injected:
                # The real reaper owns this wait. Cancel its caller again,
                # while retaining the lease until exit has been confirmed.
                assert child_lease is not None and child_lease.is_file()
                injected.append('reap')
                loop.call_soon(outer.cancel)
                await asyncio.sleep(0)
            return await real_wait()

        monkeypatch.setattr(proc, 'wait', tracked_wait)
        return proc

    async def emit(kind, payload):
        events.append((kind, payload))

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', tracked_spawn)
    monkeypatch.setattr(generation_leases, 'acquire', tracked_acquire)
    monkeypatch.setattr(Path, 'unlink', tracked_unlink)
    monkeypatch.setattr(manager, '_write', tracked_write)
    loop.set_task_factory(task_factory)
    try:
        outer = asyncio.create_task(manager.start(session, emit))
        if cancellation == 'none':
            await asyncio.wait_for(asyncio.shield(outer), 5)
            assert manager.workers[session['id']]['process'] is created[0]
            assert manager.workers[session['id']]['generation_lease'] == child_lease
            assert list((manager.home / 'updates/worker-leases').glob('*.json')) == [child_lease]
            assert attempted == ['start']
            assert [json.loads(line)['op'] for line in writes.read_text().splitlines()] == ['start']
            await asyncio.wait_for(manager.stop(session['id']), 5)
        elif cancellation == 'lease-error':
            from amplifier_web.runtime import RuntimeStartupError
            with pytest.raises(RuntimeStartupError) as failure:
                await asyncio.wait_for(asyncio.shield(outer), 5)
            assert failure.value.__cause__ is lease_error
            assert any(kind == 'runtime.error' for kind, _ in events)
            assert not attempted and not writes.exists()
            assert child_lease is None
        else:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(outer), 5)
            assert outer.cancelled()
            assert injected == (['heartbeat', 'reap'] if cancellation == 'heartbeat-and-reap' else ['heartbeat'])
            assert attempted == []  # Not even a startup write was attempted.
            assert not writes.exists()
        assert len(created) == 1 and created[0].returncode is not None
        assert lease_removals == ([] if child_lease is None else [child_lease])
        assert len(heartbeats) == 1 and heartbeats[0].done()
        assert not manager.workers and not manager._preparations
        assert not list((manager.home / 'updates/worker-leases').glob('*.json'))
        assert not manager.has_pending_operations()
        # Repeated stop/close cannot be needed to finish pre-registration cleanup.
        await asyncio.wait_for(manager.stop(session['id']), 5)
        await asyncio.wait_for(manager.close(), 5)
        assert not manager.workers and not manager._preparations
    finally:
        checking = False
        loop.set_task_factory(previous_factory)
        if outer and not outer.done():
            outer.cancel()
            await asyncio.gather(outer, return_exceptions=True)
        for proc in created:
            if proc.returncode is None:
                proc.kill()
            await asyncio.wait_for(proc.wait(), 5)
        await asyncio.wait_for(manager.close(), 5)


class NormalizationTests(unittest.TestCase):
    def test_child_identity_remains_distinct_from_parent(self):
        kind,payload=normalize_event({'type':'child.updated','sessionId':'child','status':'idle','agent':'foundation:explorer'},'parent')
        self.assertEqual(kind,'worker.updated')
        self.assertEqual(payload['sessionId'],'parent')
        self.assertEqual(payload['id'],'child')
        self.assertEqual(payload['name'],'foundation:explorer')

    def test_private_provider_events_are_not_published(self):
        self.assertIsNone(normalize_event({'type':'provider.request','secret':'sensitive'},'parent'))
        self.assertIsNone(normalize_event({'type':'analysis.delta','text':'private'},'parent'))

    def test_assistant_retains_input_identity(self):
        self.assertEqual(normalize_event({'type':'assistant.message','text':'hello'},'p','turn')[1]['inputId'],'turn')

    def test_child_generation_retains_identity_and_root_provenance(self):
        kind, payload = normalize_event({'type': 'generation.started', 'session_id': 'child',
            'root_session_id': 'root'}, 'root')
        self.assertEqual(kind, 'runtime.generation')
        self.assertEqual((payload['sessionId'], payload['rootSessionId']), ('child', 'root'))

    def test_assistant_keeps_public_message_provenance_and_known_event_time(self):
        _, payload = normalize_event({'type':'assistant.message', 'text':'progress', 'generation_id':'g',
            'message_id':'m', 'event_id':'e', 'sequence':12, 'time':123.5, 'reasoning':'private'}, 'parent')
        self.assertEqual(payload['runtimeMessage'], {'messageId':'m', 'eventId':'e', 'sequence':12})
        self.assertEqual(payload['createdAt'],123.5)
        self.assertTrue(payload['timestampKnown'])
        self.assertNotIn('reasoning',payload)

    def test_assistant_does_not_invent_provenance_or_accept_invalid_times(self):
        for value in (None, 'yesterday', True, float('nan'), float('inf'), -1):
            _, payload = normalize_event({'type':'assistant.message', 'text':'progress', 'sequence':1, 'time':value}, 'parent')
            self.assertNotIn('runtimeMessage',payload)
            self.assertNotIn('createdAt',payload)

    def test_background_activity_reports_waiting_without_exposing_extra_fields(self):
        kind, payload = normalize_event({'type':'runtime.activity','phase':'waiting-workers',
            'detail':'Waiting for 5 delegated tasks to report back.', 'activeWorkers':5,
            'reasoning':'private', 'request':{'secret':'private'}}, 'parent')
        self.assertEqual(kind, 'runtime.status')
        self.assertEqual(payload['status'], 'working')
        self.assertTrue(payload['activityOnly'])
        self.assertEqual(payload['phase'], 'waiting-workers')
        self.assertEqual(payload['activeWorkers'], 5)
        self.assertNotIn('reasoning', payload)
        self.assertNotIn('request', payload)

    def test_queued_worker_is_not_claimed_to_have_started(self):
        payload=normalize_event({'type':'job.queued','job_id':'j','agent':'foundation:explorer'},'p')[1]
        self.assertEqual(payload['status'],'queued')
        self.assertEqual(payload['name'],'foundation:explorer')

    def test_worker_cancellation_is_not_reported_as_completion(self):
        self.assertEqual(normalize_event({'type':'job.cancel_requested','job_id':'j'},'p')[1]['status'],'stopping')
        self.assertEqual(normalize_event({'type':'job.cancelled','job_id':'j'},'p')[1]['status'],'cancelled')


class WorkerActivityTests(unittest.TestCase):
    def test_activation_gate_rejects_a_callback_from_before_park(self):
        gate = ActivationGate()
        first = gate.activate()
        stale_context = gate.bind(first)
        gate.reset(stale_context)
        gate.release(first)
        second = gate.activate()
        current_context = gate.bind(second)
        try:
            gate.check_current()
            with self.assertRaisesRegex(RuntimeError, "released or superseded"):
                gate.check(first)
        finally:
            gate.reset(current_context)

    def test_waiting_counts_actual_pending_jobs_and_preserves_agent_identity(self):
        worker=Worker()
        loop=SimpleNamespace(jobs={
            'a':{'task':Mock(done=lambda:False),'tool_call':{'arguments':{'agent':'foundation:explorer','instruction':'private'}}},
            'b':{'task':Mock(done=lambda:True)},
        })
        worker.session=SimpleNamespace(coordinator=SimpleNamespace(get=lambda name:loop))
        with patch('amplifier_web.runtime_worker.publish') as publish:
            worker.observe({'type':'job.queued','job_id':'a'})
            event=publish.call_args.args[0]
            self.assertEqual(event['agent'],'foundation:explorer')
            self.assertNotIn('instruction',event)
            worker.observe({'type':'generation.finished'})
            activity=publish.call_args.args[0]
            self.assertEqual(activity['phase'],'waiting-workers')
            self.assertEqual(activity['activeWorkers'],1)


@pytest.mark.asyncio
async def test_worker_parking_releases_the_real_shared_handle_and_reacquires_unchanged(tmp_path):
    shared = pytest.importorskip("amplifier_foundation.session.shared_state")
    from amplifier_web.shared_state import configuration_stamp

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    worker = Worker()
    worker.workspace = workspace
    worker.home = tmp_path / "home"
    worker.home.mkdir()
    worker.runtime = SimpleNamespace(session_id="warm-session", queued_inputs=0,
                                     inbox=asyncio.Queue(), generation=None)
    worker.shared_store = shared.SharedSessionStore(workspace, "warm-session", root=tmp_path / "shared")
    worker.shared_store_stamp = shared.file_stamp
    worker.shared_handle = worker.shared_store.acquire(app="amplifier-unified", fixture=True)
    worker.activation_gate = ActivationGate()
    first = worker.activation_gate.activate()
    worker.activation = first
    from amplifier_web.session_files import sessions_dir
    native = sessions_dir(workspace) / "warm-session" / "transcript.jsonl"
    native.parent.mkdir(parents=True)
    native.write_text('{"role":"user","content":"before checkpoint"}\n')
    worker.config_inputs = (str(native),)
    worker.parked_history_stamp = worker.history_stamp()
    worker.parked_config_stamp = configuration_stamp(
        workspace, "warm-session", worker.home, shared.file_stamp, extra_paths=worker.config_inputs)
    checkpoint_calls = []
    async def checkpoint(status):
        checkpoint_calls.append(status)
        native.write_text('{"role":"user","content":"own completed checkpoint"}\n')
    worker.session = SimpleNamespace(coordinator=SimpleNamespace(
        get=lambda name: None,
        get_capability=lambda name: (
            checkpoint
            if name == "live.checkpoint" else None)))

    with patch("amplifier_web.runtime_worker.publish"):
        await worker.park(activation=first)
        assert worker.parked
        assert not worker.shared_handle
        await worker.acquire_for_mutation()

    assert checkpoint_calls == ["completed"]
    assert not worker.parked
    assert worker.shared_handle.active
    with pytest.raises(RuntimeError, match="released or superseded"):
        worker.activation_gate.check(first)
    worker.shared_handle.release()


@pytest.mark.parametrize(('event', 'status'), [
    ('generation.finished', 'completed'),
    ('generation.failed', 'error'),
    ('generation.detached', 'interrupted'),
])
async def test_worker_parking_checkpoints_observed_root_generation_outcome(tmp_path, event, status):
    shared = pytest.importorskip("amplifier_foundation.session.shared_state")
    worker = Worker()
    worker.workspace, worker.home = tmp_path / "workspace", tmp_path / "home"
    worker.workspace.mkdir()
    worker.home.mkdir()
    worker.runtime = SimpleNamespace(session_id="outcome-session", queued_inputs=0,
                                     inbox=asyncio.Queue(), generation=None)
    worker.shared_store = shared.SharedSessionStore(worker.workspace, "outcome-session", root=tmp_path / "shared")
    worker.shared_store_stamp = shared.file_stamp
    worker.shared_handle = worker.shared_store.acquire(app="amplifier-unified", fixture=True)
    worker.activation_gate = ActivationGate()
    worker.activation = worker.activation_gate.activate()
    worker.config_inputs = ()
    worker.parked_history_stamp = worker.history_stamp()
    worker.parked_config_stamp = None
    checkpoint_calls = []
    async def checkpoint(outcome):
        checkpoint_calls.append(outcome)
    worker.session = SimpleNamespace(coordinator=SimpleNamespace(
        get=lambda name: None,
        get_capability=lambda name: checkpoint if name == "live.checkpoint" else None))
    try:
        with patch("amplifier_web.runtime_worker.publish"):
            worker.observe({'type': event})
            await worker.park(activation=worker.activation)
        assert checkpoint_calls == [status]
    finally:
        if worker.shared_handle:
            worker.shared_handle.release()


def test_child_generation_does_not_change_root_checkpoint_outcome():
    worker = Worker()
    worker.runtime = SimpleNamespace(session_id='root')
    with patch('amplifier_web.runtime_worker.publish'):
        worker.observe({'type': 'generation.finished', 'session_id': 'root'})
        worker.observe({'type': 'generation.failed', 'session_id': 'child', 'root_session_id': 'root'})
    assert worker.root_generation_outcome == 'completed'


@pytest.mark.parametrize(('event', 'expected'), [
    ('generation.failed', 'error'),
    ('generation.detached', 'interrupted'),
])
async def test_terminal_root_execution_checkpoints_observed_outcome_during_worker_cleanup(event, expected):
    worker = Worker()
    worker.runtime = SimpleNamespace(session_id='root')
    worker.workspace = Path.cwd()
    worker.activation_gate = ActivationGate()
    worker.activation = worker.activation_gate.activate()
    worker.shared_handle = SimpleNamespace(release=lambda: None)
    checkpoint_calls = []
    async def checkpoint(status):
        checkpoint_calls.append(status)
    async def cleanup():
        return None
    async def close():
        return None
    async def read():
        await asyncio.Event().wait()
    worker.session = SimpleNamespace(
        coordinator=SimpleNamespace(get=lambda name: None,
            get_capability=lambda name: checkpoint if name == 'live.checkpoint' else None),
        cleanup=cleanup)
    worker.read = read
    with patch('amplifier_web.runtime_worker.publish'):
        worker.observe({'type': event, 'session_id': 'root'})
    worker.controls = SimpleNamespace(close=close)
    async def fail():
        raise RuntimeError('ContextLengthError: terminal root failure')
    worker.execution = asyncio.create_task(fail())
    await asyncio.gather(worker.execution, return_exceptions=True)
    with patch('amplifier_web.runtime_worker.publish'):
        worker.executed(worker.execution)
        await worker.run()
    assert checkpoint_calls == [expected]
    assert worker.shared_handle is None


async def test_terminal_cleanup_does_not_checkpoint_completed_or_yielding_outcomes():
    async def run(outcome, yielding=False):
        worker = Worker()
        worker.runtime = SimpleNamespace(session_id='root')
        worker.workspace = Path.cwd()
        worker.activation_gate = ActivationGate()
        worker.activation = worker.activation_gate.activate()
        worker.shared_handle = SimpleNamespace(release=lambda: None)
        worker.ownership = SimpleNamespace(yielding=yielding, registration=None)
        calls = []
        async def checkpoint(status):
            calls.append(status)
        async def cleanup():
            return None
        async def read():
            await asyncio.Event().wait()
        worker.session = SimpleNamespace(
            coordinator=SimpleNamespace(get_capability=lambda name: checkpoint if name == 'live.checkpoint' else None),
            cleanup=cleanup)
        worker.read = read
        worker.root_generation_outcome = outcome
        worker.shutdown.set()
        await worker.run()
        return calls
    assert await run('completed') == []
    assert await run('interrupted', yielding=True) == []


@pytest.mark.asyncio
async def test_parked_worker_rechecks_native_cli_change_before_admitting_input(tmp_path):
    shared = pytest.importorskip("amplifier_foundation.session.shared_state")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    worker = Worker()
    worker.workspace, worker.home = workspace, tmp_path / "home"
    worker.home.mkdir()
    worker.runtime = SimpleNamespace(session_id="warm-session", queued_inputs=0,
                                     inbox=asyncio.Queue(), generation=None)
    worker.shared_store = shared.SharedSessionStore(workspace, "warm-session", root=tmp_path / "shared")
    worker.shared_store_stamp = shared.file_stamp
    worker.shared_handle = worker.shared_store.acquire(app="amplifier-unified", fixture=True)
    worker.activation_gate = ActivationGate()
    worker.activation = worker.activation_gate.activate()
    messages = [{"role": "user", "content": "first"}]
    worker.shared_handle.write(messages, bundle="anchors", metadata={"fixture": True})
    from amplifier_web.session_files import sessions_dir
    native = sessions_dir(workspace) / "warm-session" / "transcript.jsonl"
    native.parent.mkdir(parents=True)
    native.write_text(json.dumps(messages[0]) + "\n")
    worker.config_inputs = (str(native),)
    checkpoint_calls, remount_calls = [], []
    async def checkpoint(status):
        checkpoint_calls.append(status)
    worker.session = SimpleNamespace(coordinator=SimpleNamespace(
        get=lambda name: None,
        get_capability=lambda name: checkpoint if name == "live.checkpoint" else None))
    async def remount():
        from amplifier_foundation.session.history import SessionHistoryStore
        remount_calls.append(SessionHistoryStore(native.parent).load_messages())
    worker.remount = remount
    try:
        with patch("amplifier_web.runtime_worker.publish"):
            await worker.park(activation=worker.activation)
            native.write_text(native.read_text() + '{"role":"assistant","content":"new CLI turn"}\n')
            after_cli = native.read_bytes()
            await worker.acquire_for_mutation()
        assert len(remount_calls) == 1
        assert remount_calls[0] == messages + [{"role": "assistant", "content": "new CLI turn"}]
        assert checkpoint_calls == ["completed"]
        assert worker.runtime.inbox.empty()
        assert native.read_bytes() == after_cli
        assert worker.shared_handle.read()["messages"] == messages
    finally:
        if worker.shared_handle:
            worker.shared_handle.release()


class PublicActivityHookTests(unittest.IsolatedAsyncioTestCase):
    async def test_child_retries_show_public_identity_and_count_without_error_body(self):
        worker=Worker();worker.runtime=SimpleNamespace(session_id='parent')
        callbacks={}
        capabilities={'web.worker_run':'child-run', 'live.children':SimpleNamespace(rows={'child':{'agent':'foundation:explorer','callId':'tool-1','runId':'child-run'}})}
        coordinator=SimpleNamespace(session_id='child',
            get_capability=capabilities.get,
            register_capability=lambda key,value:capabilities.update({key:value}),
            hooks=SimpleNamespace(register=lambda event,fn,**kw:callbacks.update({event:fn})))
        with patch.dict(sys.modules, {'amplifier_core':SimpleNamespace(HookResult=lambda:None)}):
            worker.install_activity(coordinator)
        with patch('amplifier_web.runtime_worker.publish') as publish:
            await callbacks['provider:retry']('provider:retry',{'attempt':2,'max_retries':5,'error_message':'private endpoint credential detail'})
            event=publish.call_args.args[0]
            self.assertEqual(event['workerId'],'child')
            self.assertEqual(event['phase'],'retrying')
            self.assertEqual(event['retryAttempt'],2)
            self.assertNotIn('error_message',event)
            kind,payload=normalize_event(event,'parent')
            self.assertEqual(kind,'worker.updated')
            self.assertEqual(payload['callId'],'tool-1')
            self.assertEqual(payload['runId'],'child-run')
            self.assertTrue(payload['activityOnly'])
            self.assertNotIn('status',payload)
            self.assertEqual(payload['retryMax'],5)
            self.assertNotIn('private',json.dumps(payload))

    async def test_naming_provider_hooks_keep_telemetry_without_busy_notices(self):
        from amplifier_web.execution_events import CALL_PURPOSE
        worker=Worker();worker.runtime=SimpleNamespace(session_id='parent');worker.telemetry=Mock()
        callbacks={};capabilities={}
        coordinator=SimpleNamespace(session_id='parent',get_capability=capabilities.get,
            get=lambda _: {},register_capability=lambda key,value:capabilities.update({key:value}),
            hooks=SimpleNamespace(register=lambda event,fn,**kw:callbacks.update({event:fn})))
        with patch.dict(sys.modules, {'amplifier_core':SimpleNamespace(HookResult=lambda:None)}):
            worker.install_activity(coordinator)
        token=CALL_PURPOSE.set({'label':'Session naming','turnId':'finished'})
        try:
            with patch('amplifier_web.runtime_worker.publish') as publish:
                await callbacks['provider:retry']('provider:retry',{'attempt':2,'max_retries':3})
                publish.assert_not_called()
                worker.telemetry.hook.assert_called_once()
        finally:CALL_PURPOSE.reset(token)


class ProcessContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        worker=Path(self.temp.name)/'fixture.py';worker.write_text(FIXTURE)
        self.events=[]
        self.bridges=[]
        self.received=asyncio.Event()
        self.generation_received=asyncio.Event()
        async def bridge(op,args,sid):
            self.bridges.append((op,args,sid));return {'revision':7}
        async def emit(kind,payload):
            self.events.append((kind,payload))
            if kind=='assistant.message': self.received.set()
            if kind=='runtime.generation': self.generation_received.set()
        self.emit=emit
        self.manager=RuntimeManager(bridge,command=[sys.executable,str(worker)],startup_timeout=3)
        self.session={'id':'fixture-session','workspace':self.temp.name}

    async def asyncTearDown(self):
        await self.manager.close()
        self.temp.cleanup()

    async def test_send_and_app_bridge_do_not_deadlock_reader(self):
        result=await self.manager.send(self.session,'hello','input-1',self.emit)
        self.assertTrue(result['accepted'])
        await asyncio.wait_for(self.received.wait(),3)
        self.assertEqual(self.bridges, [('get_state', {'_runtimeOwnerId': self.manager.workers['fixture-session']['runtimeOwnerId']}, 'fixture-session')])
        message=next(p for k,p in self.events if k=='assistant.message')
        self.assertEqual(message['text'],'7')
        self.assertEqual(message['inputId'],'input-1')

    async def test_native_identity_restores_worker_but_ui_bridge_and_root_events_use_app_alias(self):
        self.session.update(runtimeSessionId='native-root', nativeIdentity='older-native-alias', ownerSessionId='foreign')
        await self.manager.send(self.session, 'continue native history', 'input-1', self.emit)
        await asyncio.wait_for(self.received.wait(), 3)
        self.assertEqual(list(self.manager.workers), ['fixture-session'])
        ready = next(p for k,p in self.events if k == 'runtime.status' and p['status'] == 'ready')
        self.assertEqual(ready['report']['session_id'], 'native-root')
        self.assertEqual(ready['report']['owner_session_id'], 'fixture-session')
        self.assertEqual(ready['sessionId'], 'fixture-session')
        self.assertEqual(self.bridges, [('get_state', {'_runtimeOwnerId': self.manager.workers['fixture-session']['runtimeOwnerId']}, 'fixture-session')])
        root, child = [p for k,p in self.events if k == 'execution.event']
        self.assertEqual((root['sessionId'], root['rootSessionId']), ('fixture-session', 'fixture-session'))
        self.assertEqual((child['sessionId'], child['rootSessionId']), ('native-root-child', 'fixture-session'))
        message = next(p for k,p in self.events if k == 'assistant.message')
        self.assertEqual(message['sessionId'], 'fixture-session')

    async def test_child_generation_keeps_its_identity_while_root_is_mapped_to_app_alias(self):
        self.session['runtimeSessionId'] = 'native-root'
        await self.manager.start(self.session, self.emit)
        await asyncio.wait_for(self.generation_received.wait(), 3)
        generation = next(payload for kind, payload in self.events if kind == 'runtime.generation')
        self.assertEqual((generation['sessionId'], generation['rootSessionId']), ('child', 'fixture-session'))

    async def test_native_identity_alias_is_used_when_runtime_id_is_not_set(self):
        self.session['nativeIdentity'] = 'native-root'
        await self.manager.start(self.session, self.emit)
        ready = next(p for k,p in self.events if k == 'runtime.status' and p['status'] == 'ready')
        self.assertEqual(ready['report']['session_id'], 'native-root')
        self.assertEqual(ready['report']['owner_session_id'], 'fixture-session')

    async def test_parallel_start_reuses_single_worker(self):
        await asyncio.gather(*(self.manager.start(self.session,self.emit) for _ in range(4)))
        self.assertEqual(len(self.manager.workers),1)
        self.assertEqual(sum(k=='runtime.status' and p['status']=='starting' for k,p in self.events),1)

    async def test_bundle_replacement_is_one_shot_and_cannot_mutate_a_live_worker(self):
        replacement = {'bundle': 'new-root', 'fingerprint': 'reviewed'}
        self.session['bundleReplacement'] = replacement
        await self.manager.start(self.session, self.emit)
        row = self.manager.workers[self.session['id']]
        self.assertNotIn('bundleReplacement', row['start_session'])
        process = row['process']
        with self.assertRaisesRegex(RuntimeError, 'already has a worker'):
            await self.manager.start(self.session, self.emit)
        self.assertIs(row['process'], process)
        self.assertIsNone(process.returncode)

    async def test_second_send_keeps_the_same_worker_process(self):
        await self.manager.send(self.session, 'first', 'input-1', self.emit)
        process = self.manager.workers[self.session['id']]['process']
        await self.manager.send(self.session, 'second', 'input-2', self.emit)
        self.assertIs(self.manager.workers[self.session['id']]['process'], process)
        self.assertIsNone(process.returncode)
        self.assertEqual(sum(k == 'runtime.status' and p['status'] == 'starting'
                             for k, p in self.events), 1)

    async def test_stop_terminates_process_and_clears_owner(self):
        await self.manager.start(self.session,self.emit)
        process=self.manager.workers[self.session['id']]['process']
        await asyncio.gather(*(self.manager.stop(self.session['id']) for _ in range(3)))
        self.assertIsNotNone(process.returncode)
        self.assertFalse(self.manager.workers)
        self.assertEqual(self.events[-1][1]['status'],'stopped')

    async def test_runtime_operation_failure_is_returned(self):
        await self.manager.start(self.session,self.emit)
        with self.assertRaisesRegex(RuntimeError,'Not available'):
            await self.manager.approval(self.session['id'],'missing','allow')

    async def test_cold_start_reports_progress_and_bounded_timeout_without_send(self):
        self.manager.startup_timeout = 0.12
        self.manager.progress_interval = 0.02
        with self.assertRaisesRegex(RuntimeError, 'This attempt did not send your message') as failure:
            await self.manager.send({'id':'slow'}, 'must not be sent', 'pending-input', self.emit)
        self.assertIn('stopped before accepting your message', str(failure.exception.__cause__))
        progress = [p for k,p in self.events if k=='runtime.status' and p.get('phase')=='bundle-preparation']
        self.assertGreaterEqual(len(progress), 2)
        self.assertTrue(all(p['status']=='starting' and 'elapsedSeconds' in p for p in progress))
        self.assertFalse(self.bridges)
        self.assertFalse(self.manager.workers)
        self.assertFalse(any(k=='assistant.message' for k,_ in self.events))

    async def test_dead_worker_start_fails_without_fabricated_response(self):
        broken=RuntimeManager(command=[sys.executable,'-c','raise SystemExit(3)'],startup_timeout=3)
        try:
            with self.assertRaisesRegex(RuntimeError,'exited'):
                await broken.start({'id':'bad'},self.emit)
            self.assertFalse(any(k=='assistant.message' for k,_ in self.events))
        finally:
            await broken.close()

    async def test_start_write_disconnect_preserves_worker_exit_and_cleans_up(self):
        for error_type in (BrokenPipeError, ConnectionResetError):
            with self.subTest(error=error_type.__name__):
                events = []
                async def emit(kind, data):
                    events.append((kind, data))
                broken = RuntimeManager(command=[sys.executable, '-c', 'raise SystemExit(3)'], startup_timeout=3)
                async def disconnected_write(row, data):
                    self.assertEqual(data['op'], 'start')
                    await row['process'].wait()
                    raise error_type('Connection lost')
                try:
                    with patch.object(broken, '_write', disconnected_write):
                        with self.assertRaisesRegex(RuntimeError, 'worker exited.*code 3'):
                            await broken.start({'id': 'dead-write'}, emit)
                    self.assertFalse(broken.workers)
                    errors = [data for kind, data in events if kind == 'runtime.error']
                    self.assertEqual(len(errors), 1)
                    self.assertEqual(errors[0]['exitCode'], 3)
                    self.assertFalse(any(kind in ('assistant.message', 'input.delivered') for kind, _ in events))
                finally:
                    await broken.close()

    async def test_start_write_disconnect_cannot_admit_a_ready_worker(self):
        async def disconnected_write(row, data):
            if data['op'] == 'start':
                row['ready'].set_result(None)
            raise ConnectionResetError('Connection lost')
        try:
            with patch.object(self.manager, '_write', disconnected_write):
                with self.assertRaisesRegex(RuntimeError, 'connection closed during startup'):
                    await self.manager.start(self.session, self.emit)
            self.assertFalse(self.manager.workers)
            self.assertFalse(self.bridges)
            self.assertFalse(any(kind == 'assistant.message' for kind, _ in self.events))
        finally:
            await self.manager.close()


if __name__=='__main__': unittest.main()


class LargeTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_worker_reader_roundtrips_large_bridge_sized_message(self):
        from amplifier_web.runtime_protocol import MAX_MESSAGE_BYTES
        script='''
import asyncio
from amplifier_web.runtime_worker import Worker,configure_protocol,publish
class Probe(Worker):
 async def command(self,data):
  if data.get('op')=='echo':publish({'op':'reply','payload':data['payload']})
  else:await super().command(data)
configure_protocol()
asyncio.run(Probe().run())
'''
        proc=await asyncio.create_subprocess_exec(sys.executable,'-c',script,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,limit=MAX_MESSAGE_BYTES)
        try:
            from amplifier_web.attachments import MAX_ENCODED_BYTES, MAX_REQUEST_BYTES
            self.assertGreater(MAX_MESSAGE_BYTES, MAX_REQUEST_BYTES)
            payload='x'*MAX_ENCODED_BYTES
            async def write():
                proc.stdin.write((json.dumps({'op':'echo','payload':payload})+'\n').encode());await proc.stdin.drain()
            send=asyncio.create_task(write())
            response=json.loads(await asyncio.wait_for(proc.stdout.readline(),10))
            await send
            self.assertEqual(response['payload'],payload)
            self.assertIsNone(proc.returncode)
            proc.stdin.write(b'{"op":"stop"}\n');await proc.stdin.drain()
            self.assertEqual(await asyncio.wait_for(proc.wait(),3),0)
        finally:
            if proc.returncode is None:proc.kill();await proc.wait()

    async def test_reported_worker_failure_is_not_overwritten_by_exit_code(self):
        # Receive startup before reporting its outcome. Exiting before reading
        # stdin races the unrelated start write instead of exercising the reader.
        fixture="import json,sys;assert json.loads(sys.stdin.readline())['op']=='start';print(json.dumps({'type':'runtime.ready'}),flush=True);print(json.dumps({'type':'runtime.error','error':'Specific protocol failure','errorType':'ProtocolError'}),flush=True)"
        manager=RuntimeManager(command=[sys.executable,'-c',fixture]);events=[]
        async def emit(kind,data):events.append((kind,data))
        try:
            await manager.start({'id':'probe'},emit)
            await asyncio.wait_for(manager.workers['probe']['reader'],3)
            errors=[data['error'] for kind,data in events if kind=='runtime.error']
            self.assertEqual(errors,['Specific protocol failure'])
            self.assertEqual([data['errorType'] for kind,data in events if kind=='runtime.error'],['ProtocolError'])
        finally:await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['metadata', 'backup', 'removed', 'unreadable'])
async def test_parked_native_history_invalidation_keeps_authority_and_releases_on_refusal(tmp_path, change):
    from amplifier_foundation.session.shared_state import SharedSessionStore, file_stamp
    from amplifier_web.session_files import sessions_dir
    from amplifier_web.shared_state import configuration_stamp
    worker = Worker()
    worker.workspace = tmp_path
    worker.home = tmp_path / 'home'
    worker.home.mkdir()
    worker.runtime = SimpleNamespace(session_id='fixture')
    worker.shared_store = SharedSessionStore(tmp_path, 'fixture', root=tmp_path / 'locks')
    worker.shared_store_stamp = file_stamp
    worker.activation_gate = ActivationGate()
    native = sessions_dir(tmp_path) / 'fixture'
    native.mkdir(parents=True)
    (native / 'transcript.jsonl').write_text('{"role":"user","content":"fixture"}\n')
    (native / 'metadata.json').write_text('{"bundle":"anchors"}')
    worker.parked_history_stamp = worker.history_stamp()
    worker.parked_config_stamp = configuration_stamp(tmp_path, 'fixture', worker.home, file_stamp)
    worker.parked = True
    remounted = []
    async def remount():
        remounted.append(True)
    worker.remount = remount
    if change == 'metadata':
        (native / 'metadata.json').write_text('{"bundle":"new-native-bundle"}')
    elif change == 'backup':
        (native / 'transcript.jsonl').rename(native / 'transcript.jsonl.backup')
    elif change == 'removed':
        (native / 'transcript.jsonl').unlink()
    else:
        worker.history_stamp = Mock(side_effect=OSError('stat unavailable'))
    try:
        if change in {'removed', 'unreadable'}:
            with pytest.raises((RuntimeError, OSError)):
                await worker.acquire_for_mutation()
            assert not remounted
            other = worker.shared_store.acquire(app='other')
            other.release()
        else:
            await worker.acquire_for_mutation()
            assert remounted == [True] and worker.shared_handle.active
            assert worker.shared_store.read() is None
    finally:
        if worker.shared_handle:
            worker.shared_handle.release()


@pytest.mark.parametrize('termination',['stop','crash'])
async def test_confirmed_process_exit_settles_independent_naming(tmp_path,termination):
    from amplifier_web.service import AppService
    from amplifier_web.execution import ensure_turn
    worker=tmp_path/'background.py'
    worker.write_text('''
import json,os,sys,time
for line in sys.stdin:
    data=json.loads(line)
    if data['op']=='start':
        sid=data['session']['id']
        print(json.dumps({'type':'runtime.ready','report':{}}),flush=True)
        print(json.dumps({'type':'execution.event','event':{'id':'naming','kind':'llm','lifecycle':'background','label':'Renaming fixture','phase':'running','sessionId':sid,'rootSessionId':sid,'turnId':'turn','startedAt':time.time()}}),flush=True)
        print(json.dumps({'type':'session.idle'}),flush=True)
    elif data['op']=='stop':break
    elif data['op']=='crash':os._exit(7)
''')
    app=AppService(tmp_path/'app',workspace=tmp_path)
    manager=RuntimeManager(command=[sys.executable,str(worker)],startup_timeout=3)
    idle=asyncio.Event();ended=asyncio.Event();events=[]
    try:
        await app.dispatch('session.create',{})
        session=app._session();ensure_turn(session,'turn')
        async def emit(kind,payload):
            events.append((kind,payload));await app.on_runtime_event(kind,payload)
            if kind=='runtime.status' and payload.get('status')=='idle':idle.set()
            if kind=='runtime.ended':ended.set()
        await manager.start(session,emit);await asyncio.wait_for(idle.wait(),3)
        row=session['execution']['nodes'][0];process=manager.workers[session['id']]['process']
        assert process.returncode is None and row['phase']=='running' and not row.get('endedAt')
        assert row['aggregateUsage']['costPendingCalls']==1
        if termination=='stop':await manager.stop(session['id'])
        else:await manager._write(manager.workers[session['id']],{'op':'crash'})
        await asyncio.wait_for(ended.wait(),3)
        assert process.returncode is not None
        assert row['phase']==('stopped' if termination=='stop' else 'interrupted') and row['endedAt']>=row['startedAt']
        assert row['aggregateUsage']['costPendingCalls']==row['aggregateUsage']['tokenPendingCalls']==0
        assert len([event for event in events if event[0]=='runtime.ended'])==1
    finally:
        await manager.close();await app.close()


async def test_cancelled_process_exit_delivery_remains_retryable():
    from amplifier_web.execution import ensure_turn,ingest,finish_background
    session={};ensure_turn(session,'turn')
    ingest(session,{'id':'naming','kind':'llm','lifecycle':'background','phase':'running','startedAt':1})
    waiting=asyncio.Event();release=asyncio.Event();attempts=0
    async def emit(kind,payload):
        nonlocal attempts
        attempts+=1;waiting.set();await release.wait()
        finish_background(session,payload['backgroundCallIds'],payload['status'])
    row={'emit':emit,'backgroundCalls':{'naming'}}
    manager=RuntimeManager(command=[sys.executable,'-c','pass'])
    try:
        reader=asyncio.create_task(manager._execution_ended('root',row,'stopped'))
        await waiting.wait()
        # _stop_row cancels and gathers the reader before retrying delivery.
        reader.cancel();await asyncio.gather(reader,return_exceptions=True)
        assert not row.get('executionEnded')
        assert session['execution']['nodes'][0]['phase']=='running'
        release.set();await manager._execution_ended('root',row,'stopped')
        assert row['executionEnded'] is True
        assert session['execution']['nodes'][0]['phase']=='stopped'
        assert session['execution']['aggregateUsage']['costPendingCalls']==0
        await manager._execution_ended('root',row,'stopped')
        assert attempts==2
    finally:await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [OSError("checkpoint disk failure"), asyncio.CancelledError()])
async def test_terminal_checkpoint_failure_still_cleans_up_and_releases_ownership(failure):
    worker = Worker()
    worker.runtime = SimpleNamespace(session_id="root")
    worker.activation_gate = ActivationGate()
    worker.activation = worker.activation_gate.activate()
    steps = []

    async def checkpoint(status):
        steps.append(("checkpoint", status))
        raise failure

    async def registration_close():
        steps.append("registration")

    async def controls_close():
        steps.append("controls")

    async def cleanup():
        steps.append("session")

    async def read():
        await asyncio.Event().wait()

    worker.ownership = SimpleNamespace(yielding=False, registration=SimpleNamespace(close=registration_close))
    worker.controls = SimpleNamespace(close=controls_close)
    worker.session = SimpleNamespace(
        coordinator=SimpleNamespace(get_capability=lambda name: checkpoint if name == "live.checkpoint" else None),
        cleanup=cleanup)
    worker.shared_handle = SimpleNamespace(release=lambda: steps.append("release"))
    worker.root_generation_outcome = "error"
    worker.read = read
    worker.shutdown.set()
    with pytest.raises(type(failure)):
        await worker.run()
    assert steps == [("checkpoint", "error"), "registration", "controls", "session", "release"]
    assert worker.shared_handle is None
    assert not worker.terminal_checkpointed


async def test_naming_does_not_hold_command_lock_or_require_idle(monkeypatch):
    from unittest.mock import AsyncMock
    worker = Worker()
    started, release, sent = asyncio.Event(), asyncio.Event(), asyncio.Event()
    worker.acquire_for_mutation = AsyncMock()
    worker.bind_activation = lambda: None
    worker.activation_gate = SimpleNamespace(reset=lambda token: None)
    worker.park = AsyncMock()
    worker.session = object()
    worker.execution = object()
    worker.controls = SimpleNamespace(require_idle=Mock(side_effect=AssertionError('Naming must run during work')))
    async def suggest():
        started.set()
        await release.wait()
        return {'name': 'Concurrent name'}
    worker.naming = SimpleNamespace(suggest=suggest)
    dispatch = worker._command_serial
    async def serial(data):
        if data['op'] == 'send': sent.set()
        else: await dispatch(data)
    worker._command_serial = serial
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', lambda event: None)
    task = asyncio.create_task(worker.command({'id': 'naming', 'op': 'control', 'operation': 'session.naming'}))
    await asyncio.wait_for(started.wait(), 1)
    assert worker.operation_controls == 1 and not worker.command_lock.locked()
    await asyncio.wait_for(worker.command({'id': 'input', 'op': 'send'}), .5)
    assert sent.is_set() and not task.done()
    release.set()
    await task
    assert worker.operation_controls == 0


async def test_naming_reuses_worker_without_replacing_foreground_emitter(tmp_path):
    runtime = RuntimeManager(tmp_path)
    ready = asyncio.get_running_loop().create_future()
    ready.set_result({})
    original, naming = Mock(), Mock()
    runtime.workers['active'] = {'process': SimpleNamespace(returncode=None), 'ready': ready, 'emit': original}
    await runtime.start({'id': 'active'}, naming, preserve_emit=True)
    assert runtime.workers['active']['emit'] is original
    await runtime.start({'id': 'active'}, naming)
    assert runtime.workers['active']['emit'] is naming
    runtime.workers.clear()
    await runtime.close()


@pytest.mark.parametrize('exit_code', [7, 143])
async def test_unexpected_worker_exit_retains_type_and_exit_code(exit_code):
    manager = RuntimeManager(command=[sys.executable, '-c', f"import json,sys;sys.stdin.readline();print(json.dumps({{'type':'runtime.ready'}}),flush=True);sys.stdin.readline();raise SystemExit({exit_code})"])
    events = []
    async def emit(kind, data): events.append((kind, data))
    try:
        await manager.start({'id': 'exit-probe'}, emit)
        # Trigger the post-startup exit only after the command channel is ready.
        process = manager.workers['exit-probe']['process']
        process.stdin.write(b'exit\n')
        await process.stdin.drain()
        await asyncio.wait_for(manager.workers['exit-probe']['reader'], 3)
        errors = [data for kind, data in events if kind == 'runtime.error']
        assert len(errors) == 1
        assert errors[0]['errorType'] == 'WorkerExitedError'
        assert errors[0]['exitCode'] == exit_code
    finally:
        await manager.close()


async def test_untyped_worker_error_does_not_override_generation_error_type():
    manager = RuntimeManager(command=[sys.executable, '-c', "import json,sys;sys.stdin.readline();print(json.dumps({'type':'runtime.ready'}),flush=True);sys.stdin.readline();print(json.dumps({'type':'runtime.error','error':'Untyped error'}),flush=True)"])
    events = []
    async def emit(kind, data): events.append((kind, data))
    try:
        await manager.start({'id': 'untyped-probe'}, emit)
        process = manager.workers['untyped-probe']['process']
        process.stdin.write(b'error\n')
        await process.stdin.drain()
        await asyncio.wait_for(manager.workers['untyped-probe']['reader'], 3)
        errors = [data for kind, data in events if kind == 'runtime.error']
        assert len(errors) == 1
        assert 'errorType' not in errors[0]
    finally:
        await manager.close()


@pytest.mark.parametrize('reported_session', ['identity-owner', 'different-owner'])
async def test_ready_worker_identity_is_private_and_bound_to_native_session(reported_session):
    script = (
        "import json,os,sys;sys.stdin.readline();"
        "print(json.dumps({'type':'runtime.ready','report':{'public':True},"
        f"'workerIdentity':{{'sessionId':{reported_session!r},'pid':os.getpid()}}}}),flush=True);"
        "sys.stdin.readline()"
    )
    manager = RuntimeManager(command=[sys.executable, '-c', script])
    events = []
    async def emit(kind, data): events.append((kind, data))
    try:
        await manager.start({'id': 'app-owner', 'runtimeSessionId': 'identity-owner'}, emit)
        row = manager.workers['app-owner']
        assert row.get('native_worker_pid') == (
            row['process'].pid if reported_session == 'identity-owner' else None)
        assert all('workerIdentity' not in value and 'native_worker_pid' not in value for _, value in events)
        assert next(value['report'] for kind, value in events if value.get('status') == 'ready') == {'public': True}
    finally:
        await manager.close()


@pytest.fixture
async def windows_runtime_model(tmp_path, monkeypatch):
    """Drive the real manager Windows branch; NOT native kernel evidence."""
    from amplifier_web import runtime, windows_jobs
    from unittest.mock import AsyncMock
    class Job:
        def __init__(self):
            self.identity = SimpleNamespace(guard_pid=10, payload_pid=20)
            self.stdin = self
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()
            self.done = asyncio.Event()
            self.tree_empty = self.io_finalized = False
            self.output_discarded = False
            self.writes, self.verified = [], []
            self.ignore_stop = self.reject_identity = self.bad_ready = False
            self.code = 0
        def write(self, raw):
            data = json.loads(raw)
            self.writes.append(data)
            if data['op'] == 'start':
                identity = {'sessionId': data['session']['id'], 'pid': 999 if self.bad_ready else 30}
                self.stdout.feed_data(runtime.encode_message(
                    {'type': 'runtime.ready', 'workerIdentity': identity, 'report': {}}))
            elif data['op'] == 'send':
                self.stdout.feed_data(runtime.encode_message(
                    {'type': 'input.delivered', 'input_id': data['input_id']}))
                self.stdout.feed_data(runtime.encode_message(
                    {'op': 'reply', 'id': data['id'], 'result': {'accepted': True}}))
            elif data['op'] == 'stop' and not self.ignore_stop:
                self.finish()
        async def drain(self): pass
        def verify_member(self, pid, created):
            self.verified.append((pid, created))
            if self.reject_identity or (pid, created) != (30, 300):
                raise OSError('not in retained job')
        def finish(self):
            if not self.done.is_set():
                self.tree_empty = self.io_finalized = True
                self.stdout.feed_eof()
                self.stderr.feed_eof()
                self.done.set()
        async def wait(self):
            await self.done.wait()
            return windows_jobs.ProcessResult(self.code, 0, 0, self.output_discarded)
        async def close(self):
            self.output_discarded = True
            self.finish()
            return await self.wait()
    job = Job()
    spawn = AsyncMock(return_value=job)
    monkeypatch.setattr(windows_jobs.WindowsOwnedProcess, 'start', spawn)
    monkeypatch.setattr(runtime, '_WINDOWS', True)
    monkeypatch.setattr(runtime, '_WINDOWS_STOP_SECONDS', .03)
    pids = []
    from amplifier_web import generation_leases
    real_acquire = generation_leases.acquire
    monkeypatch.setattr('amplifier_web.generation_leases.acquire',
                        lambda home, generation, pid: pids.append(pid))
    manager = RuntimeManager(command=[sys.executable, '-c', 'unused'],
                             startup_timeout=1, retention={'prewarm_on_select': False})
    manager.home = tmp_path / 'app'
    events = []
    async def emit(kind, data): events.append((kind, data))
    def identify():
        job.stdout.feed_data(runtime.encode_message(
            {'type': 'runtime.identity', 'pid': 30, 'created': 300}))
    return SimpleNamespace(manager=manager, job=job, spawn=spawn, pids=pids,
                           events=events, emit=emit, identify=identify, real_acquire=real_acquire)


@pytest.mark.parametrize('forced', [False, True])
async def test_windows_runtime_identity_transport_and_stop_model(windows_runtime_model, forced):
    model = windows_runtime_model
    model.identify()
    model.job.stderr.feed_data(b'e' * 200_000)  # no newline, larger than pipe watermark
    model.job.ignore_stop = forced
    try:
        await model.manager.start({'id': 'windows-model'}, model.emit)
        row = model.manager.workers['windows-model']
        assert row['process'].pid == row['native_worker_pid'] == 30
        assert 20 not in model.pids and model.pids[-1] == 30  # never lease uv
        assert model.job.verified == [(30, 300), (30, 300)]
        assert not hasattr(row['process'], 'kill') and not hasattr(row['process'], 'wait')
        model.spawn.assert_awaited_once()
        assert model.spawn.call_args.kwargs['trusted_python'] == sys._base_executable
        await model.manager.send({'id': 'windows-model'}, 'é' * 90_000, 'input', model.emit)
        await asyncio.wait_for(model.manager.stop('windows-model'), 1)
        assert model.job.done.is_set() and model.job.tree_empty and model.job.io_finalized
        assert model.job.output_discarded == forced
        assert not model.manager.workers
        assert any(kind == 'runtime.ended' for kind, _ in model.events)
        assert len(row['stderr']) <= 30
    finally:
        await model.manager.close()


@pytest.mark.parametrize('failure', ['member', 'ready', 'missing', 'cancel', 'stop'])
async def test_windows_runtime_identity_failure_never_admits_input(windows_runtime_model, failure):
    model = windows_runtime_model
    if failure not in {'missing', 'cancel', 'stop'}:
        model.identify()
    model.job.reject_identity = failure == 'member'
    model.job.bad_ready = failure == 'ready'
    task = asyncio.create_task(model.manager.start({'id': 'identity-failure'}, model.emit))
    try:
        if failure in {'cancel', 'stop'}:
            while not model.spawn.await_count:
                await asyncio.sleep(0)
            while not model.manager._preparations['identity-failure'].get('task'):
                await asyncio.sleep(0)
            if failure == 'cancel':
                task.cancel()
            else:
                await model.manager.stop('identity-failure')
        with pytest.raises((RuntimeError, asyncio.CancelledError)):
            await asyncio.wait_for(task, 2)
        assert model.job.done.is_set()
        assert not model.manager.workers and not model.manager._preparations
        assert not any(kind == 'runtime.status' and data.get('status') == 'ready'
                       for kind, data in model.events)
        assert not any(data['op'] == 'send' for data in model.job.writes)
        if failure != 'ready':
            assert not model.job.writes and 30 not in model.pids
    finally:
        await model.manager.close()


@pytest.mark.parametrize('stage', ['identity', 'spawn'])
@pytest.mark.parametrize(('tree_empty', 'io_finalized'), [(False, False), (True, False), (False, True)])
async def test_windows_failed_preparation_cleanup_retains_durable_generation_protection(
        windows_runtime_model, monkeypatch, stage, tree_empty, io_finalized):
    from unittest.mock import AsyncMock
    import os
    from amplifier_web import generation_leases, runtime_profiles, updates, windows_jobs
    model = windows_runtime_model
    monkeypatch.setattr(generation_leases, 'acquire', model.real_acquire)
    monkeypatch.setattr(generation_leases, '_process_exists', lambda pid: pid == os.getpid())
    monkeypatch.setattr(updates, 'active_release', lambda home: {'current': 'original-source'})
    monkeypatch.setattr(runtime_profiles, 'ensure', AsyncMock(return_value='qualified-source'))
    command = model.manager.command
    model.manager.command = None
    monkeypatch.setattr(model.manager, '_command', lambda *args, **kwargs: command)
    model.job.tree_empty = tree_empty
    model.job.io_finalized = io_finalized
    cleanup_error = windows_jobs.CleanupError(model.job)
    model.job.close = AsyncMock(side_effect=cleanup_error)
    if stage == 'spawn':
        model.spawn.side_effect = cleanup_error
    else:
        model.identify()
        model.job.reject_identity = True
    try:
        with pytest.raises((RuntimeError, windows_jobs.CleanupError)):
            await model.manager.start({'id': 'failed-cleanup'}, model.emit)
        # These are real durable leases, not a call-count model. No generation
        # deletion is attempted. Both host references must survive an unverified
        # worker identity, even if the job reports tree-empty but not finalized I/O.
        assert generation_leases.references(model.manager.home) == {'original-source', 'qualified-source'}
        paths = list((model.manager.home / 'updates/worker-leases').glob('*.json'))
        assert len(paths) == 2
        assert all(json.loads(path.read_text())['pid'] == os.getpid() for path in paths)
        retained = model.manager._preparations['failed-cleanup']
        assert retained['windows_job'] is model.job and retained['cleanup_error'] is cleanup_error
        assert set(retained['generation_reservations']) == set(paths)
        assert not model.manager.workers
        assert not model.job.writes
        assert any(kind == 'runtime.error' and 'cleanup is unconfirmed' in data['error']
                   for kind, data in model.events)
        assert not any(kind == 'runtime.ended' for kind, _ in model.events)
        for request in (model.manager.start({'id': 'failed-cleanup'}, model.emit),
                        model.manager.stop('failed-cleanup')):
            with pytest.raises(RuntimeError, match='cleanup is unconfirmed'):
                await request
        assert model.spawn.await_count == 1
        assert model.job.close.await_count == (0 if stage == 'spawn' else 1)
        await model.manager.close()
        assert model.manager._preparations['failed-cleanup'] is retained
        assert all(path.exists() for path in paths)
        assert not any(kind == 'runtime.ended' for kind, _ in model.events)
    finally:
        # Only settle this fake's task, not production recovery. Real failed
        # owners/leases deliberately require separate confirmed reconciliation.
        model.job.finish()
        await model.manager.close()


@pytest.mark.parametrize('cleanup_fails', [False, True])
@pytest.mark.parametrize('stopping', [False, True])
async def test_windows_communication_error_settles_children_only_after_confirmed_cleanup(
        windows_runtime_model, monkeypatch, cleanup_fails, stopping):
    from unittest.mock import AsyncMock
    from amplifier_web import runtime, windows_jobs
    model = windows_runtime_model
    original_lines = runtime._BoundedWorkerLines
    monkeypatch.setattr(runtime, '_BoundedWorkerLines', lambda stream: original_lines(stream, limit=256))
    model.identify()
    await model.manager.start({'id': 'protocol-failure'}, model.emit)
    row = model.manager.workers['protocol-failure']
    row['backgroundCalls'] = {'background-2', 'background-1'}
    children = {'native-child': 'working', 'background-1': 'working', 'background-2': 'working'}
    original_emit = row['emit']
    async def emit(kind, data):
        if kind == 'runtime.ended':
            assert model.job.tree_empty and model.job.io_finalized
            assert data['runtimeOwnerId'] == row['runtimeOwnerId']
            assert data['backgroundCallIds'] == ['background-1', 'background-2']
            for identity in children:
                children[identity] = data['status']
        await original_emit(kind, data)
    row['emit'] = emit
    if stopping:
        row['closing'] = True
        row['stop_reason'] = 'stopped'
    cleanup_error = windows_jobs.CleanupError(model.job)
    if cleanup_fails:
        model.job.close = AsyncMock(side_effect=cleanup_error)
    model.job.stdout.feed_data(b'x' * 257)  # real bounded-line failure after readiness
    try:
        if cleanup_fails:
            with pytest.raises(windows_jobs.CleanupError):
                await asyncio.wait_for(asyncio.shield(row['reader']), 1)
            assert row['process'].returncode is None
            assert not row.get('executionEnded')
            assert all(status == 'working' for status in children.values())
            assert not any(kind == 'runtime.ended' for kind, _ in model.events)
            assert model.manager.workers['protocol-failure'] is row
        else:
            await asyncio.wait_for(asyncio.shield(row['reader']), 1)
            assert row['process'].returncode is not None and row['executionEnded']
            assert all(status == ('stopped' if stopping else 'interrupted') for status in children.values())
            ended = [data for kind, data in model.events if kind == 'runtime.ended']
            assert len(ended) == 1
            await model.manager.stop('protocol-failure')
            assert len([kind for kind, _ in model.events if kind == 'runtime.ended']) == 1
        if not stopping:
            assert any(kind == 'runtime.error' for kind, _ in model.events)
    finally:
        if cleanup_fails:
            # The fake remains retained until this test-only task teardown.
            model.job.finish()
            await row['windows_owner'].completion
            # A failed reader remains diagnostic evidence, not a new test error.
            row['closing'] = True
        await model.manager.close()


async def test_windows_protocol_lines_over_watermark_bounded_and_eof():
    from amplifier_web.runtime import _BoundedWorkerLines
    stream = asyncio.StreamReader(limit=64 * 1024)
    stream.feed_data(b'x' * 160_000 + b'\nnext\n')
    stream.feed_eof()
    lines = _BoundedWorkerLines(stream, limit=200_000)
    assert await lines.readline() == b'x' * 160_000 + b'\n'
    assert await lines.readline() == b'next\n'
    assert await lines.readline() == b''
    for content in (b'x' * 11, b'x' * 10 + b'\n'):
        source = asyncio.StreamReader()
        source.feed_data(content)
        source.feed_eof()
        with pytest.raises(ValueError, match='transport limit'):
            await _BoundedWorkerLines(source, limit=10).readline()


@pytest.mark.parametrize('outcome', ['success', 'invalid', 'cancel'])
async def test_windows_shared_probe_uses_owned_job_and_settles_pipes(windows_runtime_model, outcome):
    from unittest.mock import AsyncMock
    model = windows_runtime_model
    written = []
    model.job.stdin = SimpleNamespace(write=written.append, drain=AsyncMock(), close=lambda: None)
    if outcome != 'cancel':
        response = {'ok': True, 'result': {'status': 'read-only'}} if outcome == 'success' else []
        model.job.stdout.feed_data(json.dumps(response).encode() + b'\n')
        model.job.finish()
    task = asyncio.create_task(model.manager.shared_state_probe({'operation': 'inspect'}))
    try:
        if outcome == 'cancel':
            while not written:
                await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif outcome == 'invalid':
            with pytest.raises(RuntimeError, match='invalid response'):
                await task
        else:
            assert await task == {'status': 'read-only'}
        assert model.job.tree_empty and model.job.io_finalized
        assert json.loads(written[0]) == {'operation': 'inspect'}
        assert model.spawn.call_args.args[0][-1].endswith('shared_state_probe.py')
        assert model.spawn.call_args.kwargs['trusted_python'] == sys._base_executable
    finally:
        await model.manager.close()


async def test_windows_owner_cannot_report_exit_without_zero_tree_and_io_proof(windows_runtime_model):
    from amplifier_web.runtime import _WindowsWorkerOwner
    model = windows_runtime_model
    owner = _WindowsWorkerOwner(model.job)
    model.job.done.set()  # A root-exit report alone is not successful cleanup.
    with pytest.raises(RuntimeError, match='cleanup is unconfirmed'):
        await owner.wait()
    assert owner.process.returncode is None
    assert owner.process.pid is None


@pytest.mark.skipif(sys.platform != 'win32', reason='native Windows runtime Job integration')
@pytest.mark.parametrize('forced', [False, True])
async def test_native_windows_runtime_worker_identity_pipes_and_job_cleanup(tmp_path, monkeypatch, forced):
    from amplifier_web import runtime
    # Exercise the actual Worker.run identity/ThreadedPipeReader/shutdown path,
    # with only the provider/bundle-dependent command body replaced.
    code = r'''
import asyncio,json,os,sys,subprocess
sys.path.insert(0, sys.argv[1])
from amplifier_web.runtime_worker import Worker,configure_protocol,publish
class Fixture(Worker):
    async def command(self, data):
        if data['op']=='start':
            self.session_id=data['session']['id']
            os.write(2,b'e'*200000)
            publish({'type':'runtime.ready','workerIdentity':{'sessionId':self.session_id,'pid':os.getpid()},'report':{}})
        elif data['op']=='send':
            publish({'type':'input.delivered','input_id':data['input_id']})
            publish({'type':'assistant.message','text':data['text']})
            publish({'op':'reply','id':data['id'],'result':{'accepted':True}})
        elif data['op']=='stop' and sys.argv[2]=='normal':
            # A descendant keeps both output pipes open. Root exit must still
            # settle the job rather than waiting indefinitely for pipe EOF.
            subprocess.Popen([sys.executable,'-I','-S','-c','import time;time.sleep(30)'])
            self.shutdown.set()
configure_protocol()
asyncio.run(Fixture().run())
'''
    manager = RuntimeManager(command=[sys.executable, '-I', '-c', code,
        str(Path(__file__).resolve().parents[1]), 'forced' if forced else 'normal'],
        startup_timeout=15, retention={'prewarm_on_select': False})
    manager.home = tmp_path / 'native-app'
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(manager.home))
    events = []
    async def emit(kind, data): events.append((kind, data))
    monkeypatch.setattr(runtime, '_WINDOWS_STOP_SECONDS', .2 if forced else 15)
    try:
        await manager.start({'id': 'native-job'}, emit)
        row = manager.workers['native-job']
        owner = row['windows_owner']
        assert row['native_worker_pid'] == row['process'].pid
        assert row['process'].pid != owner.job.identity.guard_pid
        owner.job.verify_member(row['process'].pid, owner.created)
        await manager.send({'id': 'native-job'}, 'Native UTF-8 é ' * 12000, 'native-input', emit)
        await asyncio.wait_for(manager.stop('native-job'), 20)
        assert owner.job.tree_empty and owner.job.io_finalized
        assert owner.result.active_processes == 0
        assert not manager.workers
        assert any(kind == 'runtime.ended' for kind, _ in events)
        assert not any(kind == 'runtime.error' for kind, _ in events)
    finally:
        await manager.close()


@pytest.mark.skipif(sys.platform != 'win32', reason='native Windows runtime startup cancellation')
async def test_native_windows_runtime_cancel_before_identity_reaps_job(tmp_path, monkeypatch):
    from amplifier_web import windows_jobs
    manager = RuntimeManager(command=[sys.executable, '-I', '-S', '-c',
        'import time;time.sleep(30)'], startup_timeout=15,
        retention={'prewarm_on_select': False})
    manager.home = tmp_path / 'cancel-app'
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(manager.home))
    captured, events = [], []
    original = windows_jobs.WindowsOwnedProcess.start
    async def spawn(*args, **kwargs):
        owner = await original(*args, **kwargs)
        captured.append(owner)
        return owner
    monkeypatch.setattr(windows_jobs.WindowsOwnedProcess, 'start', spawn)
    async def emit(kind, data): events.append((kind, data))
    task = asyncio.create_task(manager.start({'id': 'cancel-native'}, emit))
    try:
        async with asyncio.timeout(15):
            while not captured or not manager._preparations['cancel-native'].get('task'):
                await asyncio.sleep(.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 15)
        assert captured[0].tree_empty and captured[0].io_finalized
        assert not manager.workers and not manager._preparations
        assert not list((manager.home / 'updates/worker-leases').glob('*.json'))
        assert not any(kind == 'runtime.status' and data.get('status') == 'ready'
                       for kind, data in events)
    finally:
        await manager.close()


def _default_runtime_probe_module():
    """Read the standalone fixture without importing the unfinished trial harness."""
    import importlib.util
    path = Path(__file__).parent / 'fixtures/windows_default_runtime_probe.py'
    spec = importlib.util.spec_from_file_location('default_runtime_probe_contract', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _default_runtime_provider_module(monkeypatch):
    from types import ModuleType
    fixture = _default_runtime_probe_module()
    module = ModuleType('amplifier_module_provider_default_fixture')
    exec(compile(fixture.PROVIDER, '<scripted-default-provider>', 'exec'), module.__dict__)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return module


async def test_default_runtime_probe_provider_metadata_and_core_contract(tmp_path, monkeypatch):
    from amplifier_core import ChatRequest, Message, ProviderInfo
    from amplifier_core.testing import MockCoordinator
    from amplifier_core.validation.provider import ProviderValidator
    from amplifier_web.provider_environment import (
        construct_provider, construct_schema_provider, provider_class, provider_config_schema)
    from amplifier_web.provider_isolation import UnavailableProvider, require_selected
    module = _default_runtime_provider_module(monkeypatch)
    config = {'project': str(tmp_path / 'project'), 'app': str(tmp_path / 'app'),
              'proof': str(tmp_path / 'proof.json'), 'appHashes': {}, 'imports': {}}
    # Same prepare_manager metadata route as the cold worker: actual schema
    # construction with config={} BEFORE a mount coordinator exists.
    assert provider_class('provider-default-fixture') is module.Provider
    assert await provider_config_schema(
        'provider-default-fixture', config=config, environment={}) == {'fields': []}
    metadata_provider = construct_schema_provider(module.Provider, config, environment={})
    setup_provider = construct_provider(module.Provider, config)
    assert metadata_provider.coordinator is None and metadata_provider.config == {}
    assert setup_provider.coordinator is None and setup_provider.config == config
    assert isinstance(metadata_provider.get_info(), ProviderInfo)
    # Real Core validator/mount contract, not a signature-only or fake-Core pass.
    result = await asyncio.wait_for(
        ProviderValidator().validate(module.__name__, config=config), 5)
    assert result.passed and result.checks and all(check.passed for check in result.checks)
    assert {check.name for check in result.checks} >= {
        'protocol_compliance', 'provider_get_info', 'provider_list_models',
        'provider_complete', 'provider_parse_tool_calls'}
    coordinator = MockCoordinator()
    await module.mount(coordinator, config)
    provider = coordinator.get('providers')['fixture']
    assert provider.coordinator is coordinator and provider.config == config
    assert not isinstance(provider, UnavailableProvider)
    require_selected(coordinator)
    assert provider.get_info().defaults['model'] == 'fixture'
    assert await provider.list_models() == []
    assert provider.parse_tool_calls(SimpleNamespace(tool_calls=[])) == []
    # Core's test-session branch must not execute worker-provenance file I/O.
    response = await provider.complete(ChatRequest(
        messages=[Message(role='user', content='DEFAULT_COMMAND_TURN')]))
    assert response.content[0].text == 'Fixture validation'
    assert not tuple(tmp_path.iterdir())


async def test_default_runtime_probe_legacy_provider_metadata_refuses_selection(monkeypatch):
    from amplifier_core.testing import MockCoordinator
    from amplifier_web.provider_environment import provider_config_schema
    from amplifier_web.provider_isolation import (
        ProviderIsolation, ProviderUnavailable, UnavailableProvider, require_selected)
    module = _default_runtime_provider_module(monkeypatch)

    def legacy(self, coordinator, config):
        self.coordinator, self.config = coordinator, config

    # Sensitivity control for the exact old constructor, without changing the
    # maintained fixture or bypassing the real metadata/isolation boundary.
    monkeypatch.setattr(module.Provider, '__init__', legacy)
    with pytest.raises(TypeError, match='coordinator') as caught:
        await provider_config_schema('provider-default-fixture', config={}, environment={})
    isolation = ProviderIsolation()
    spec = {'module': 'provider-default-fixture', 'config': {}}
    failure = isolation.record(spec, 'provider_schema_failed', error=caught.value)
    assert failure['diagnostic'] == {
        'stage': 'schema', 'category': 'schema', 'errorType': 'TypeError'}
    coordinator = MockCoordinator()
    await coordinator.mount(
        'providers', UnavailableProvider(spec, failure), name='default-fixture')
    with pytest.raises(ProviderUnavailable) as refused:
        require_selected(coordinator)
    assert refused.value.failures[0]['reason_code'] == 'provider_schema_failed'
    assert refused.value.failures[0]['diagnostic'] == failure['diagnostic']


def test_default_runtime_probe_keeps_real_command_and_bounded_evidence():
    import ast
    fixture = _default_runtime_probe_module()
    tree = ast.parse(Path(fixture.__file__).read_text(encoding='utf-8'))
    managers = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name) and node.func.id == 'RuntimeManager']
    assert len(managers) == 1 and not managers[0].args
    assert {keyword.arg for keyword in managers[0].keywords} == {'startup_timeout', 'retention'}
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                   and node.func.attr in {'setattr', 'setitem', 'patch'} for node in ast.walk(tree))
    compile(fixture.PROVIDER, '<scripted-default-provider>', 'exec')
    empty = {'schema': 2, 'status': 'FAIL', 'stage': 'preflight',
             'checks': dict.fromkeys(fixture.CHECKS, False), 'wheelSha256': None,
             'manifestSha256': None, 'lockSha256': None, 'appHashes': {}, 'workerImports': {},
             'outerJob': {'status': 'unknown', 'timedOut': False, 'activeProcesses': None,
                          'treeEmpty': False, 'ioFinalized': False, 'outputDiscarded': False}}
    fixture.validate_report(empty)
    for patch in ({'status': 'PASS'}, {'error': 'private exception'},
                  {'stage': 'private-path'}, {'lockSha256': 'not-a-sha256'},
                  {'workerImports': {'unknown-package': {}}}):
        with pytest.raises(AssertionError):
            fixture.validate_report({**empty, **patch})


def test_default_runtime_probe_native_parent_has_no_redirector_timeout_kill():
    import ast
    import inspect
    assert inspect.iscoroutinefunction(test_native_windows_default_runtime_command_cold_prepare_turn_and_stop)
    body = ast.parse(inspect.getsource(test_native_windows_default_runtime_command_cold_prepare_turn_and_stop))
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                   and node.func.attr in {'run', 'communicate', 'kill', 'terminate'}
                   for node in ast.walk(body))


def test_default_runtime_probe_fixture_home_is_private_before_environment_and_imports(tmp_path, monkeypatch):
    import ast
    from contextlib import contextmanager
    from amplifier_web import windows_files
    fixture = _default_runtime_probe_module()
    root = tmp_path / 'scratch'
    keys = {'HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'AMPLIFIER_WEB_HOME',
            'AMPLIFIER_HOME', 'AMPLIFIER_UNIFIED_IMPORT_HOME', 'AMPLIFIER_SESSION_STATE_HOME',
            'AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME'}
    environment = {key: 'must-not-be-inherited' for key in keys}
    environment.update(PATH='preserve-uv-path', UV_CACHE_DIR=str(root / 'uv-cache'))
    original = environment.copy()
    created = []

    @contextmanager
    def directory(path, *, owned_root, create):
        assert owned_root == root and create is True and path.is_relative_to(root)
        assert environment == original  # No app can use partly prepared homes.
        created.append(path)
        yield

    monkeypatch.setattr(fixture, 'os', SimpleNamespace(environ=environment))
    monkeypatch.setattr(windows_files, 'directory', directory)
    fixture.prepare_fixture_home(root)
    assert set(created) == {root / name for name in (
        'app', 'shared', 'workspace', 'user', 'user/roaming', 'user/local',
        'user/cache', 'ownership', 'captures')}
    assert all(Path(environment[key]).is_relative_to(root) for key in keys)
    assert environment['HOME'] == environment['USERPROFILE'] == str(root / 'user')
    assert environment['PATH'] == original['PATH']
    assert environment['UV_CACHE_DIR'] == original['UV_CACHE_DIR']
    assert not root.exists()  # Only the kernel boundary was modeled.
    tree = ast.parse(Path(fixture.__file__).read_text(encoding='utf-8'))
    journey = next(node for node in tree.body
                   if isinstance(node, ast.AsyncFunctionDef) and node.name == 'journey')
    setup = next(node for node in journey.body if isinstance(node, ast.Expr)
                 and isinstance(node.value, ast.Call)
                 and getattr(node.value.func, 'id', None) == 'prepare_fixture_home')
    assert all(setup.lineno < node.lineno for node in journey.body
               if isinstance(node, (ast.Import, ast.ImportFrom)))


def test_default_runtime_probe_unsafe_fixture_home_is_not_repaired(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from amplifier_web import windows_files
    fixture = _default_runtime_probe_module()
    environment = {'PATH': 'preserve-uv-path'}
    error = PermissionError('unsafe existing private fixture path')
    entered = []

    @contextmanager
    def directory(path, *, owned_root, create):
        entered.append((path, owned_root, create))
        raise error
        yield

    monkeypatch.setattr(fixture, 'os', SimpleNamespace(environ=environment))
    monkeypatch.setattr(windows_files, 'directory', directory)
    with pytest.raises(PermissionError) as caught:
        fixture.prepare_fixture_home(tmp_path)
    assert caught.value is error and environment == {'PATH': 'preserve-uv-path'}
    assert entered == [(tmp_path / 'app', tmp_path, True)]
    assert not tuple(tmp_path.iterdir())


@pytest.mark.parametrize('prepared', [False, True])
async def test_default_runtime_probe_fresh_app_import_requires_isolated_home(tmp_path, prepared):
    """Finite import-only child: no AppService instance, runtime, uv or listener."""
    import os
    import subprocess
    source = Path(__file__).resolve().parents[1]
    temporary = tmp_path / 'temp'
    temporary.mkdir()
    script = r'''
import importlib.util, json, ntpath, os, sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
source, root, prepared = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3] == "True"
sys.path.insert(0, str(source))
assert not any(key in os.environ for key in (
    "HOME", "USERPROFILE", "HOMEPATH", "HOMEDRIVE", "APPDATA", "LOCALAPPDATA"))
# Native Windows uses this directly. On the Linux author host, model ONLY its
# ~ expansion with the actual CPython ntpath function; Path.home/imports remain
# real. POSIX pwd fallback would otherwise hide the Windows missing-home defect.
if os.name != "nt":
    original = os.path.expanduser
    os.path.expanduser = lambda value: (
        ntpath.expanduser(value) if os.fspath(value).startswith("~") else original(value))
blocked = []
def guard(event, args):
    if event in {"socket.bind", "socket.connect", "socket.getaddrinfo", "socket.sendto",
                 "socket.gethostbyname", "socket.gethostbyaddr", "socket.getnameinfo",
                 "subprocess.Popen", "os.system", "os.posix_spawn"}:
        blocked.append(event)
        raise AssertionError("network/process effect prohibited")
    targets, directory_fd = [], None
    if event == "open":
        mode, flags = args[1:3]
        if ((isinstance(mode, str) and any(char in mode for char in "wax+"))
                or (isinstance(flags, int) and flags &
                    (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))):
            targets = [args[0]]
    elif event in {"os.mkdir", "os.remove", "os.rmdir", "os.chmod"}:
        targets = [args[0]]
        directory_fd = args[1] if event in {"os.remove", "os.rmdir"} else args[2]
    elif event in {"os.link", "os.symlink", "os.rename"}:
        targets = list(args[:2])
    for target in targets:
        if isinstance(target, int):
            continue
        path = Path(target)
        if not path.is_absolute() and directory_fd not in {None, -1}:
            # filelock's import-time temporary probe removes relative to its
            # owned directory fd on POSIX. Resolve that fd, not the source cwd.
            assert os.name == "posix"
            info = os.fstat(directory_fd)
            parent = next(candidate for candidate in (root.parent, *root.parent.rglob("*"))
                          if candidate.is_dir() and
                          (candidate.stat().st_dev, candidate.stat().st_ino) == (info.st_dev, info.st_ino))
            path = parent / path
        assert path.absolute().is_relative_to(root.parent), "write escaped test scratch"
sys.addaudithook(guard)
spec = importlib.util.spec_from_file_location(
    "cold_home_import_fixture", source / "tests/fixtures/windows_default_runtime_probe.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
assert "amplifier_web.service" not in sys.modules
assert not (root / "app/runtime").exists()
if prepared:
    from amplifier_web import windows_files
    @contextmanager
    def model_directory(path, *, owned_root, create):
        # Models native creation only. Test is about real import/home semantics,
        # not an ACL pass; actual NTFS validation stays in native CI.
        assert owned_root == root and create and path.is_relative_to(root)
        path.mkdir(parents=True, exist_ok=True)
        yield
    with patch.object(windows_files, "directory", model_directory):
        fixture.prepare_fixture_home(root)
    assert Path.home() == root / "user"
try:
    from amplifier_web.service import AppService
except RuntimeError as error:
    assert not prepared and ntpath.expanduser("~") == "~"
    modules = []
    tb = error.__traceback__
    while tb:
        modules.append(tb.tb_frame.f_globals.get("__name__", ""))
        tb = tb.tb_next
    assert any(name.startswith("pathlib") for name in modules)
    assert any(name in {"amplifier_foundation.session.finder",
                        "amplifier_web.status_protocol.reader"} for name in modules)
    result = "missing-home-RuntimeError"
else:
    assert prepared and AppService.__module__ == "amplifier_web.service"
    assert Path.home() == root / "user"
    from amplifier_web.status_protocol.reader import DEFAULT_DIRECTORY
    assert DEFAULT_DIRECTORY == root / "user/.amplifier/emitter"
    assert not DEFAULT_DIRECTORY.exists()  # No status/app state was opened.
    result = "actual-AppService-import-PASS"
assert not blocked
assert not (root / "app/runtime").exists() and not (root / "app/app.sqlite3").exists()
print(json.dumps({"result": result, "networkProcessAttempts": blocked}))
'''
    keep = {'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'PATH'}
    environment = {key: value for key, value in os.environ.items() if key.upper() in keep}
    environment.update(TEMP=str(temporary), TMP=str(temporary), TMPDIR=str(temporary))
    argv = [sys.executable, '-I', '-B', '-c', script, str(source),
            str(tmp_path / 'cold-scratch'), str(prepared)]
    if sys.platform == 'win32':
        fixture = _default_runtime_probe_module()
        outcome = await fixture.run_owned_probe(
            argv, cwd=tmp_path, env=environment, scratch=tmp_path, timeout=15)
        if fixture.FAILED_OUTER_OWNERS:
            # Keep the helper's existing owner/scratch records alive after this
            # test fails. A later parameter must not replace an uncertain owner.
            sys.modules[fixture.__name__ + '_retained_import_' + str(id(fixture))] = fixture
        outer = outcome['outerJob']
        assert (outcome['succeeded'] and outcome['tasksSettled']
                and outer['status'] == 'confirmed' and not outer['timedOut']
                and outer['activeProcesses'] == 0 and outer['treeEmpty']
                and outer['ioFinalized'] and not outer['outputDiscarded']), (
                    'Bounded cold-home import or owned cleanup failed')
        output = outcome['stdout']
    else:
        child = subprocess.run(
            argv, cwd=tmp_path, env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        assert child.returncode == 0, 'Bounded cold-home import regression failed'
        output = child.stdout
    assert json.loads(output) == {
        'result': 'actual-AppService-import-PASS' if prepared else 'missing-home-RuntimeError',
        'networkProcessAttempts': []}


@pytest.mark.parametrize('prepared', [False, True])
@pytest.mark.parametrize('mode', ['normal', 'timeout', 'cleanup-unknown', 'io-unknown', 'discarded'])
async def test_default_runtime_probe_home_import_windows_owns_deadline_and_cleanup(
        tmp_path, monkeypatch, prepared, mode):
    import subprocess
    from types import FunctionType
    from unittest.mock import AsyncMock
    from amplifier_web import windows_jobs
    fixture = _default_runtime_probe_module()
    expected = {'result': 'actual-AppService-import-PASS' if prepared else 'missing-home-RuntimeError',
                'networkProcessAttempts': []}

    class Job:
        state = 'running'
        tree_empty = io_finalized = output_discarded = False

        def __init__(self):
            self.stdin = SimpleNamespace(close=Mock())
            self.stdout, self.stderr = asyncio.StreamReader(), asyncio.StreamReader()
            self.stdout.feed_data(json.dumps(expected).encode())
            self.stdout.feed_eof()
            self.stderr.feed_eof()
            self.done = asyncio.Event()
            self.closes = 0
            if mode not in {'timeout', 'cleanup-unknown'}:
                self.finish()

        def finish(self):
            self.tree_empty, self.io_finalized = True, mode != 'io-unknown'
            self.output_discarded = mode == 'discarded'
            self.done.set()

        async def wait(self):
            await self.done.wait()
            return windows_jobs.ProcessResult(0, 0, 0, self.output_discarded)

        async def close(self):
            self.closes += 1
            if mode == 'cleanup-unknown':
                self.state = 'cleanup_failed'
                raise windows_jobs.CleanupError(self)
            if not self.done.is_set():
                self.finish()
            return await self.wait()

    job = Job()
    start = AsyncMock(return_value=job)
    monkeypatch.setattr(windows_jobs.WindowsOwnedProcess, 'start', start)
    monkeypatch.setattr(subprocess, 'run', Mock(
        side_effect=AssertionError('Windows import test used redirector-only subprocess.run')))
    deadlines = []

    def bounded_timeout(seconds):
        deadlines.append(seconds)
        assert seconds == 15
        return asyncio.timeout(.01)  # Exercise the real helper's timeout path.

    monkeypatch.setattr(fixture, 'asyncio', SimpleNamespace(
        **{name: getattr(asyncio, name) for name in (
            'create_task', 'gather', 'shield')}, timeout=bounded_timeout))
    original = test_default_runtime_probe_fresh_app_import_requires_isolated_home
    windows_branch = FunctionType(original.__code__, {
        **original.__globals__,
        'sys': SimpleNamespace(platform='win32', executable=sys.executable, modules=sys.modules),
        '_default_runtime_probe_module': lambda: fixture,
    })
    try:
        if mode == 'normal':
            await asyncio.wait_for(windows_branch(tmp_path, prepared), 1)
        else:
            with pytest.raises(AssertionError, match='import or owned cleanup failed'):
                await asyncio.wait_for(windows_branch(tmp_path, prepared), 1)
        assert deadlines == [15] and job.closes == 1
        job.stdin.close.assert_called_once_with()
        start.assert_awaited_once()
        argv = start.await_args.args[0]
        assert argv[0] == sys.executable and argv[1:4] == ['-I', '-B', '-c']
        assert 'from amplifier_web.service import AppService' in argv[4]
        assert argv[-1] == str(prepared)
        assert start.await_args.kwargs['trusted_python'] == sys._base_executable
        assert start.await_args.kwargs['cleanup_timeout'] == 5
        assert start.await_args.kwargs['cwd'] == tmp_path
        environment = start.await_args.kwargs['env']
        assert not {'HOME', 'USERPROFILE', 'HOMEPATH', 'HOMEDRIVE', 'APPDATA', 'LOCALAPPDATA'} & {
            name.upper() for name in environment}
        assert environment['TEMP'] == environment['TMP'] == environment['TMPDIR'] == str(tmp_path / 'temp')
        retained = [key for key, module in tuple(sys.modules.items())
                    if key.startswith(fixture.__name__ + '_retained_import_') and module is fixture]
        if mode in {'cleanup-unknown', 'io-unknown'}:
            assert fixture.FAILED_OUTER_OWNERS == [{'owner': job, 'scratch': tmp_path}]
            assert len(retained) == 1  # Helper + its existing ownership evidence survive.
        else:
            assert not fixture.FAILED_OUTER_OWNERS and not retained
        assert (tmp_path / 'temp').is_dir() and not (tmp_path / 'cold-scratch').exists()
    finally:
        # Only the inert model's fake owner is retired here. Real uncertain
        # native owners in the maintained test are never dropped or retried.
        for key, module in tuple(sys.modules.items()):
            if key.startswith(fixture.__name__ + '_retained_import_') and module is fixture:
                del sys.modules[key]


@pytest.mark.parametrize('boundary', [
    'runtime_preflight', 'input_paths', 'private_root', 'private_log',
    'journey_imports', 'installed_direct_url',
])
def test_default_runtime_probe_preflight_failure_facts_distinguish_boundaries(monkeypatch, boundary):
    from contextlib import nullcontext
    import io
    from amplifier_web import windows_files
    fixture = _default_runtime_probe_module()
    monkeypatch.setitem(sys.modules, fixture.__name__, fixture)
    published, opened = [], []

    class InertPath:
        def __truediv__(self, name):
            return self

        def exists(self):
            return boundary == 'input_paths'

        def is_file(self):
            return True

        def open(self, *args, **kwargs):
            opened.append(True)
            if boundary == 'private_log':
                raise PermissionError(13, 'private path/token must not be copied')
            return nullcontext(io.StringIO())

    def directory(*args, **kwargs):
        if boundary == 'private_root':
            raise PermissionError(13, 'private owner data must not be copied')
        return nullcontext()

    async def journey(root, wheel, report):
        fixture.checkpoint(report, boundary)
        if boundary == 'journey_imports':
            raise ModuleNotFoundError('private module name')
        raise AssertionError('private wheel/installed bytes')

    def run(coroutine):
        try:
            coroutine.send(None)
        except StopIteration as done:
            return done.value
        finally:
            coroutine.close()
        raise AssertionError('unexpected asynchronous I/O')

    parser = SimpleNamespace(add_argument=lambda *a, **kw: None,
                             parse_args=lambda: SimpleNamespace(root=InertPath(), wheel=InertPath()))
    monkeypatch.setattr(fixture, 'argparse', SimpleNamespace(ArgumentParser=lambda: parser))
    monkeypatch.setattr(fixture, 'sys', SimpleNamespace(
        platform='other' if boundary == 'runtime_preflight' else 'win32',
        version_info=(3, 13), modules=sys.modules))
    monkeypatch.setattr(fixture, 'os', SimpleNamespace(environ={'GITHUB_ACTIONS': 'true'}))
    monkeypatch.setattr(fixture, 'struct', SimpleNamespace(calcsize=lambda _: 8))
    monkeypatch.setattr(fixture, 'asyncio', SimpleNamespace(run=run))
    monkeypatch.setattr(fixture, 'journey', journey)
    monkeypatch.setattr(windows_files, 'directory', directory)
    monkeypatch.setattr(fixture, 'redirect_stdout', lambda _: nullcontext())
    monkeypatch.setattr(fixture, 'redirect_stderr', lambda _: nullcontext())
    monkeypatch.setattr(fixture, 'print', published.append, raising=False)
    assert fixture.main() == 1
    report = json.loads(published[0])
    fixture.validate_inner_report(report)
    assert report['stage'] == 'preflight' and not any(report['checks'].values())
    assert report['diagnostic']['checkpoint'] == boundary
    error = report['diagnostic']['errors'][0]
    assert error['class'] == ('PermissionError' if boundary in {'private_root', 'private_log'}
                              else 'ModuleNotFoundError' if boundary == 'journey_imports'
                              else 'AssertionError')
    if boundary in {'private_root', 'private_log'}:
        assert error['errno'] == 13
    assert error['frames'] and error['frames'][0]['source'] == 'cold_main'
    assert 'private' not in json.dumps(error).replace('private_root', '')
    assert bool(opened) == (boundary in {'private_log', 'journey_imports', 'installed_direct_url'})


@pytest.mark.parametrize('identity', [False, True])
async def test_default_runtime_probe_startup_distinguishes_identity_from_ready_timeout(
        windows_runtime_model, tmp_path, monkeypatch, identity):
    """Real host start/identity/write/wait, with only the existing inert job."""
    fixture = _default_runtime_probe_module()
    monkeypatch.setitem(sys.modules, fixture.__name__, fixture)
    from amplifier_web import runtime as runtime_module, worker_diagnostics
    async def inert_diagnostic_write(function, *args, **kwargs):
        assert function is worker_diagnostics.save_startup_failure
        return None  # This host model must not start a real diagnostic thread.
    monkeypatch.setattr(runtime_module, 'asyncio', SimpleNamespace(
        **{name: value for name, value in vars(asyncio).items() if name != 'to_thread'},
        to_thread=inert_diagnostic_write))
    model = windows_runtime_model
    model.manager.startup_timeout = .02
    if identity:
        model.identify()
    original_write = model.job.write

    def not_ready(raw):
        data = json.loads(raw)
        if data['op'] == 'start':
            model.job.writes.append(data)
            model.job.stdout.feed_data(json.dumps({
                'type': 'runtime.progress', 'phase': 'bundle-preparation',
                'detail': 'private URL and token must not enter the diagnostic',
            }).encode() + b'\n')
        else:
            original_write(raw)

    monkeypatch.setattr(model.job, 'write', not_ready)
    report = fixture.empty_inner_report()
    fixture.checkpoint(report, 'runtime_start')
    try:
        with pytest.raises(RuntimeError) as caught:
            await fixture.observe_default_start(
                model.manager, {'id': 'cold-start-model'}, model.emit, report, tmp_path)
        facts = fixture.failure_facts(caught.value, 'runtime_start')
        fixture.validate_diagnostic(facts)
        sources = {frame['source'] for row in facts['errors'] for frame in row['frames']}
        assert {'cold_start', 'runtime_start', 'runtime_start_locked'} <= sources
        assert ('runtime_identify' in sources) is not identity
        assert facts['errors'][0]['class'] == ('RuntimeError' if identity else 'other')
        assert any(row['class'] == 'TimeoutError' for row in facts['errors'])
        startup = report['startup']
        assert startup['diagnosticStatus'] == 'PASS'
        assert startup['verifiedWorkerObserved'] is identity
        assert ('bundle-preparation' in startup['phases']) is identity
        assert not any(startup[key] for key in (
            'projectManifestExists', 'lockExists', 'venvPythonExists'))
        fixture.validate_inner_report(report)
        assert 'private' not in json.dumps(report)
        assert model.job.done.is_set() and model.job.tree_empty and model.job.io_finalized
        assert not model.manager.workers and not model.manager._preparations
        assert not any(data['op'] == 'send' for data in model.job.writes)
    finally:
        await model.manager.close()


@pytest.mark.parametrize('outcome', ['return', 'error', 'cancel', 'callback-error', 'callback-cancel'])
@pytest.mark.parametrize('observer_fault', [False, True])
async def test_default_runtime_probe_start_observer_preserves_original_behavior(
        tmp_path, outcome, observer_fault):
    fixture = _default_runtime_probe_module()
    report = fixture.empty_inner_report()
    token = object()
    error = (asyncio.CancelledError('private cancellation') if 'cancel' in outcome
             else ValueError('private startup error'))
    callbacks = []

    class Rows:
        def get(self, *args):
            raise OSError('private diagnostic fault')

    class Runtime:
        workers = Rows() if observer_fault else {}

        async def start(self, session, emit):
            assert session == {'id': 'fixed-synthetic-session'}
            assert await emit('runtime.status', {'phase': 'private-path-or-symbol'}) is token
            assert await emit('runtime.status', {'phase': 'session-mount'}) is token
            assert await emit('runtime.status', {'phase': 'session-mount'}) is token
            if outcome == 'return':
                return token
            raise error

    async def emit(kind, data):
        callbacks.append((kind, data))
        if outcome.startswith('callback-'):
            raise error
        return token

    for name in ('pyproject.toml', 'uv.lock', '.venv/Scripts/python.exe'):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'synthetic existence only, not provenance')
    lock_bytes = b'version = 1\n'
    (tmp_path / 'uv.lock').write_bytes(lock_bytes)
    call = fixture.observe_default_start(
        Runtime(), {'id': 'fixed-synthetic-session'}, emit, report, tmp_path)
    if outcome == 'return':
        assert await call is token
    else:
        with pytest.raises(type(error)) as caught:
            await call
        assert caught.value is error
    assert len(callbacks) == (1 if outcome.startswith('callback-') else 3)
    assert callbacks[0][1]['phase'] == 'private-path-or-symbol'
    facts = report['startup']
    assert facts['diagnosticStatus'] == ('FAIL' if observer_fault else 'PASS')
    assert facts['phases'] == ([] if observer_fault or outcome.startswith('callback-') else ['session-mount'])
    assert all(facts[key] for key in ('projectManifestExists', 'lockExists', 'venvPythonExists')) is not observer_fault
    assert report['lockObservation'] == {'status': 'present', 'sha256': fixture.digest(lock_bytes)}
    fixture.validate_inner_report(report)
    assert 'private' not in json.dumps(report)


@pytest.mark.parametrize('case', [
    'verified', 'not-loaded', 'missing', 'no-vcs', 'bad-commit', 'bad-json',
    'bad-version', 'foreign-origin', 'editable', 'oversized',
])
def test_default_runtime_probe_early_foundation_metadata_is_worker_owned(monkeypatch, case):
    fixture = _default_runtime_probe_module()
    host = Path('/synthetic-worker-prefix')
    origin = host / 'site-packages/amplifier_foundation/__init__.py'
    commit = 'b' * 40  # Deliberately not the host's historical a49c22 revision.
    raw = json.dumps({'url': 'https://private-host/credential-must-not-leak',
                      'vcs_info': {'vcs': 'git', 'commit_id': commit}})
    if case == 'no-vcs':
        raw = None
    elif case == 'bad-commit':
        raw = json.dumps({'vcs_info': {'vcs': 'git', 'commit_id': 'private-branch'}})
    elif case == 'bad-json':
        raw = 'private invalid JSON'
    elif case == 'editable':
        raw = json.dumps({'dir_info': {'editable': True}})
    elif case == 'oversized':
        raw = ' ' * 8193
    modules = {} if case == 'not-loaded' else {
        'amplifier_foundation': SimpleNamespace(__file__=str(
            Path('/foreign/__init__.py') if case == 'foreign-origin' else origin))}
    monkeypatch.setattr(fixture, 'sys', SimpleNamespace(modules=modules, prefix=str(host)))
    def distribution(name):
        assert name == 'amplifier-foundation'
        if case == 'missing':
            raise fixture.metadata.PackageNotFoundError()
        return SimpleNamespace(version='private version prose' if case == 'bad-version' else '1.2.3',
                               locate_file=lambda _: origin, read_text=lambda _: raw)
    monkeypatch.setattr(fixture.metadata, 'distribution', distribution)
    expected = ('verified' if case == 'verified' else 'not_loaded' if case == 'not-loaded'
                else 'missing' if case == 'missing' else 'no_vcs' if case == 'no-vcs' else 'invalid')
    value = fixture.worker_foundation()
    assert value == {'status': expected,
                     'version': '1.2.3' if expected in {'verified', 'no_vcs'} else None,
                     'commit': commit if expected == 'verified' else None}
    assert 'private' not in json.dumps(value)
    facts = {'diagnosticStatus': 'PASS', 'observed': True, 'taskDone': False,
             'awaiting': [], 'errors': [], 'foundation': value}
    fixture.validate_worker_startup(facts)
    for patch in ({'status': 'private-status'}, {'version': 'private version'},
                  {'commit': 'private-branch'}, {'url': 'private-host'},
                  {'status': 'verified', 'commit': None}):
        with pytest.raises(AssertionError):
            fixture.validate_worker_startup({**facts, 'foundation': {**value, **patch}})


@pytest.mark.parametrize('kind', ['timeout', 'called', 'timeout-subclass', 'called-subclass', 'os'])
def test_default_runtime_probe_exact_subprocess_error_identity_has_no_payload(kind):
    import subprocess
    fixture = _default_runtime_probe_module()
    class PrivateTimeout(subprocess.TimeoutExpired): pass
    class PrivateCalled(subprocess.CalledProcessError): pass
    error = {
        'timeout': subprocess.TimeoutExpired('private-command', 35, output=b'private', stderr=b'private'),
        'called': subprocess.CalledProcessError(17, 'private-command', output=b'private', stderr=b'private'),
        'timeout-subclass': PrivateTimeout('private-command', 35),
        'called-subclass': PrivateCalled(17, 'private-command'),
        'os': OSError(22, 'private-path'),
    }[kind]
    value = fixture.failure_facts(error, 'runtime_start')
    fixture.validate_diagnostic(value)
    row = value['errors'][0]
    assert row['class'] == {'timeout': 'TimeoutExpired', 'called': 'CalledProcessError',
                            'os': 'OSError'}.get(kind, 'other')
    assert ('returncode' in row) is (kind == 'called')
    if kind == 'called':
        assert row['returncode'] == 17
    assert 'private' not in json.dumps(value)


@pytest.mark.parametrize('case', ['present', 'missing', 'invalid', 'oversized'])
def test_default_runtime_probe_failed_start_lock_has_independent_status(tmp_path, case):
    fixture = _default_runtime_probe_module()
    raw = b'version = 1\n[[package]]\nname = "amplifier-foundation"\nversion = "1.0.0"\n'
    if case != 'missing':
        (tmp_path / 'uv.lock').write_bytes(
            raw if case == 'present' else b'\xff' if case == 'invalid' else b'x' * (4 * 1024 * 1024 + 1))
    report = fixture.empty_inner_report()
    fixture.observe_lock(tmp_path, report)
    assert report['lockObservation'] == {
        'status': case if case in {'present', 'missing'} else 'invalid',
        'sha256': fixture.digest(raw) if case == 'present' else None}
    assert report['lockSha256'] is None and not any(report['checks'].values())
    fixture.validate_inner_report(report)
    fixture.validate_report({**report, 'schema': 2, 'outerJob': {
        'status': 'confirmed', 'activeProcesses': 0, 'treeEmpty': True,
        'ioFinalized': True, 'timedOut': False, 'outputDiscarded': False}})
    for patch in ({'path': 'private'}, {'sha256': 'private'},
                  {'status': 'private-state'}, {'status': 'present', 'sha256': None}):
        with pytest.raises(AssertionError):
            fixture.validate_inner_report({**report, 'lockObservation': {**report['lockObservation'], **patch}})


def test_default_runtime_probe_thread_diagnostic_total_budget_and_schema(tmp_path):
    fixture = _default_runtime_probe_module()
    source = max(fixture.CODE_LABELS, key=len)
    frame = {'source': source, 'offset': 10**6}
    error = {'class': 'CalledProcessError', 'frames': [frame] * 5, 'returncode': -(2**31)}
    child = {'module': 'hooks-session-naming', 'taskDone': True, 'cancelled': False,
             'awaiting': [frame] * 3, 'errors': [{**error, 'frames': [frame] * 2}]}
    operation = {'operation': 'receipt_record', 'module': 'hooks-session-naming',
                 'queued': 10**6, 'entered': 10**6, 'failed': 10**6, 'returned': 0,
                 'errors': [{**error, 'frames': []}]}
    facts = {'diagnosticStatus': 'PASS', 'observed': True, 'taskDone': True,
             'awaiting': [frame] * 5, 'errors': [error] * 4,
             'activationChildren': [child] * 4, 'activationTruncated': True,
             'foundation': {'status': 'verified', 'version': '1' * 100, 'commit': 'a' * 64},
             'codeHashes': {name: 'b' * 64 for name in list(fixture.CODE_LABELS | fixture.THREAD_OPERATIONS)[:16]},
             'threadOperations': [operation] * 8, 'operationsTruncated': True}
    fixture.validate_worker_startup(facts)
    raw = json.dumps(facts)
    assert len(raw.encode()) <= fixture.WORKER_DIAGNOSTIC_BYTES == 16 * 1024
    path = tmp_path / 'private-observation.json'
    path.write_text(raw, encoding='utf-8')
    assert fixture.read_worker_startup(path) == facts
    report = {**fixture.empty_inner_report(), 'workerStartup': facts}
    fixture.validate_report({**report, 'schema': 2, 'outerJob': {
        'status': 'confirmed', 'activeProcesses': 0, 'treeEmpty': True,
        'ioFinalized': True, 'timedOut': False, 'outputDiscarded': False}})
    invalid = [
        {'threadOperations': [operation] * 9}, {'operationsTruncated': 1},
        {'threadOperations': [{**operation, 'operation': 'private-symbol'}]},
        {'threadOperations': [{**operation, 'module': 'private-module'}]},
        {'threadOperations': [{**operation, 'output': 'private'}]},
        {'threadOperations': [{**operation, 'queued': True}]},
        {'threadOperations': [{**operation, 'entered': 10**6 + 1}]},
        {'threadOperations': [{**operation, 'queued': 0}]},
        {'threadOperations': [{**operation, 'returned': 1}]},
        {'threadOperations': [{**operation, 'errors': [error]}]},
        {'threadOperations': [{**operation, 'errors': [{'class': 'TimeoutExpired', 'frames': [], 'returncode': 7}]}]},
        {'codeHashes': {'private-file.py': 'b' * 64}},
        {'codeHashes': {name: 'b' * 64 for name in list(fixture.CODE_LABELS)[:17]}},
        {'codeHashes': {'worker_start': 'private-contents'}},
    ]
    for patch in invalid:
        with pytest.raises(AssertionError):
            fixture.validate_worker_startup({**facts, **patch})
    path.write_bytes(b' ' * (fixture.WORKER_DIAGNOSTIC_BYTES + 1))
    assert fixture.read_worker_startup(path)['diagnosticStatus'] == 'FAIL'


def test_default_runtime_probe_loaded_code_fingerprint_is_path_independent_and_exact(monkeypatch):
    fixture = _default_runtime_probe_module()
    def original():
        def nested():
            return 1
        return nested
    clone = original.__code__.replace(co_filename='private-machine-path.py')
    assert fixture.code_fingerprint(clone) == fixture.code_fingerprint(original.__code__)
    changed = original.__code__.replace(co_consts=(*original.__code__.co_consts, 'changed'))
    assert fixture.code_fingerprint(changed) != fixture.code_fingerprint(original.__code__)
    monkeypatch.setattr(fixture, 'sys', SimpleNamespace(modules={'synthetic': SimpleNamespace(parent=original)}))
    assert fixture.loaded_code('synthetic', 'parent.<nested>') is original().__code__
    assert fixture.loaded_code('synthetic', 'parent.<unknown>') is None


@pytest.mark.parametrize('case', [
    'queued', 'returned', 'timeout', 'called', 'unknown', 'clone',
    'outsider', 'nested', 'cancel', 'observer-fault', 'saturation', 'callable',
    'nested-remote', 'cancel-entered', 'truncation', 'lifecycle-wired',
])
async def test_default_runtime_probe_thread_operation_phases_without_real_executor(
        tmp_path, monkeypatch, case):
    """Use original CPython to_thread code, but a queued inert executor."""
    import contextvars
    import functools
    import subprocess
    from types import FunctionType
    fixture = _default_runtime_probe_module()
    loop = asyncio.get_running_loop()
    submitted, invoked = [], []
    token = object()
    original_error = (subprocess.TimeoutExpired('private-command', 35, output=b'private')
                      if case == 'timeout' else subprocess.CalledProcessError(
                          23, 'private-command', output=b'private'))
    selected_context = contextvars.ContextVar('synthetic-operation-context')

    def operation():
        invoked.append(selected_context.get())
        if case in {'timeout', 'called'}:
            raise original_error
        return token

    def unknown():
        return operation()

    function = unknown if case == 'unknown' else operation
    if case == 'callable':
        class Opaque:
            @property
            def __code__(self):
                raise AssertionError('must not inspect unknown callable attributes')
            def __call__(self):
                return operation()
        function = Opaque()
    if case == 'clone':
        function = FunctionType(operation.__code__.replace(co_filename='private-clone.py'),
                                operation.__globals__, closure=operation.__closure__)
        assert function.__code__ == operation.__code__ and function.__code__ is not operation.__code__
    def submit(executor, callback):
        assert executor is None
        future = loop.create_future()
        submitted.append((callback, future))
        return future
    # No patch to asyncio.to_thread: only this test callable's globals select
    # an inert executor. The ORIGINAL code object and locals proxy still run.
    thread = FunctionType(asyncio.to_thread.__code__, {
        'events': SimpleNamespace(get_running_loop=lambda: SimpleNamespace(run_in_executor=submit)),
        'contextvars': contextvars, 'functools': functools})

    async def complete_io(fn):
        task = asyncio.create_task(thread(fn))
        return await asyncio.shield(task)

    async def remote_revision():
        def read():
            return operation()
        return await thread(read)

    lifecycle_calls = []
    if case == 'lifecycle-wired':
        from contextlib import contextmanager
        @contextmanager
        def lifecycle(function, publish):
            # Parent/worker model executes the executor callback on this same
            # thread. Independently checked real thread-local hook is covered
            # by original-bodies tests, not installed over the root profiler.
            lifecycle_calls.append(function)
            publish({'status': 'PASS', 'timeoutSeen': False, 'events': {}, 'codeHashes': {}})
            yield
        monkeypatch.setattr(fixture, 'observe_shared_subprocess', lifecycle)

    operation_functions = []
    if case == 'truncation':
        def successful():
            return token
        for i in range(9):
            code = successful.__code__.replace(co_consts=(*successful.__code__.co_consts, i))
            operation_functions.append(FunctionType(code, successful.__globals__, closure=successful.__closure__))
        names = list(fixture.THREAD_OPERATIONS)
        monkeypatch.setattr(fixture, 'THREAD_OPERATIONS', {
            name: ('synthetic_ops', 'f' + str(i)) for i, name in enumerate(names)})

    class Activator:
        async def activate(self, module_name, source_uri):
            if case == 'truncation':
                for fn in operation_functions:
                    assert await thread(fn) is token
                return token
            if case in {'nested-remote', 'lifecycle-wired'}:
                return await remote_revision()
            if case == 'nested':
                return await complete_io(function)
            if case == 'saturation':
                def extra():
                    return token
                for _ in range(3):
                    await thread(extra)
                return token
            return await thread(function)
        async def activate_all(self, modules):
            tasks = [self.activate(name, 'private-source') for name in (
                ['loop-live', 'context-simple', 'hooks-session-naming', 'secret-name']
                if case == 'saturation' else ['loop-live'])]
            return await asyncio.gather(*tasks, return_exceptions=True)
    owner = Activator()
    class Worker:
        async def start(self):
            return await owner.activate_all([])
    mock_modules = {
        **sys.modules, '__main__': SimpleNamespace(Worker=Worker),
        'asyncio.threads': SimpleNamespace(to_thread=thread),
        'amplifier_foundation.sources.shared': SimpleNamespace(
            complete_io=complete_io, clean_checkout=operation, remote_revision=remote_revision),
        'amplifier_foundation.modules.activator': SimpleNamespace(ModuleActivator=Activator),
    }
    if case == 'truncation':
        mock_modules['synthetic_ops'] = SimpleNamespace(**{
            'f' + str(i): fn for i, fn in enumerate(operation_functions[:8])})
    monkeypatch.setattr(fixture, 'sys', SimpleNamespace(modules=mock_modules,
        getprofile=sys.getprofile, setprofile=sys.setprofile))
    path = tmp_path / 'privately-created-observation.json'
    path.write_text('{}', encoding='utf-8')
    attempts = []
    class FaultOnce:
        def open(self, *args, **kwargs):
            attempts.append(True)
            if len(attempts) == 1:
                raise OSError('private observer failure')
            return path.open(*args, **kwargs)
    worker, outside = Worker(), None
    marker = selected_context.set('inherited-context')
    assert sys.getprofile() is None
    fixture.install_worker_start_observer(FaultOnce() if case == 'observer-fault' else path)
    try:
        worker.start_task = asyncio.create_task(worker.start())
        async with asyncio.timeout(1):
            while not submitted:
                await asyncio.sleep(0)
        await asyncio.sleep(0)
        facts = fixture.read_worker_startup(path)
        row = facts['threadOperations'][0]
        label = ('other' if case in {'unknown', 'clone', 'callable', 'saturation'}
                 else 'receipt_reuse' if case == 'truncation'
                 else 'shared_remote' if case in {'nested-remote', 'lifecycle-wired'} else 'shared_clean')
        assert row == {'module': 'loop-live', 'operation': label,
                       'queued': 1, 'entered': 0, 'returned': 0, 'failed': 0, 'errors': []}
        if label != 'other' and case != 'truncation':
            expected_code = remote_revision.__code__.co_consts[1] if case in {'nested-remote', 'lifecycle-wired'} else operation.__code__
            assert facts['codeHashes'][label] == fixture.code_fingerprint(expected_code)
        if case == 'outsider':
            outside = asyncio.create_task(thread(operation))
            async with asyncio.timeout(1):
                while len(submitted) < 2:
                    await asyncio.sleep(0)
            assert fixture.read_worker_startup(path)['threadOperations'] == [row]
        if case in {'saturation', 'truncation'}:
            index = 0
            async with asyncio.timeout(1):
                while not worker.start_task.done():
                    while index < len(submitted):
                        callback, future = submitted[index]
                        future.set_result(callback())
                        index += 1
                    await asyncio.sleep(0)
            assert await worker.start_task == [token] * (4 if case == 'saturation' else 1)
            await asyncio.sleep(0)
            final = fixture.read_worker_startup(path)
            assert len(final['threadOperations']) == (4 if case == 'saturation' else 8)
            assert all(row['queued'] == row['entered'] == row['returned'] == (3 if case == 'saturation' else 1)
                       and row['failed'] == 0 for row in final['threadOperations'])
            assert final['operationsTruncated'] is (case == 'truncation')
            fixture.validate_worker_startup(final)
        elif case in {'queued', 'cancel'}:
            worker.start_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await worker.start_task
            assert not invoked
        else:
            callback, future = submitted[0]
            try:
                result = callback()  # Model executor entry in this same thread.
            except type(original_error) as error:
                assert error is original_error
                future.set_exception(error)
            else:
                assert result is token
                future.set_result(result)
            if case == 'cancel-entered':
                # The underlying callable finished, but its caller can still
                # cancel before receipt. Counters are not admission/success.
                worker.start_task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await worker.start_task
                results = [token]
            else:
                results = await worker.start_task
            assert results == [original_error] if case in {'timeout', 'called'} else results == [token]
            await asyncio.sleep(0)
            final = fixture.read_worker_startup(path)
            row = final['threadOperations'][0]
            assert row['queued'] == row['entered'] == 1
            assert row['failed'] == (case in {'timeout', 'called'})
            assert row['returned'] == (case not in {'timeout', 'called'})
            assert invoked == ['inherited-context']
            if case in {'timeout', 'called'}:
                assert row['errors'][0]['class'] == type(original_error).__name__
                assert row['errors'][0]['frames'] == []
                assert ('returncode' in row['errors'][0]) is (case == 'called')
            assert 'private' not in json.dumps(final)
            assert final['diagnosticStatus'] == ('FAIL' if case in {'observer-fault', 'nested-remote'} else 'PASS')
            if case == 'nested-remote':
                assert row['subprocessLifecycle']['status'] == 'refused'
            elif case == 'lifecycle-wired':
                assert len(lifecycle_calls) == 1
                assert lifecycle_calls[0].__code__ is remote_revision.__code__.co_consts[1]
                assert row['subprocessLifecycle'] == {
                    'status': 'PASS', 'timeoutSeen': False, 'events': {}, 'codeHashes': {}}
            else:
                assert 'subprocessLifecycle' not in row and not lifecycle_calls
            fixture.validate_worker_startup(final)
        await asyncio.sleep(0)
        assert sys.getprofile() is None
    finally:
        sys.setprofile(None)
        selected_context.reset(marker)
        for task in (worker.start_task, outside):
            if task and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (worker.start_task, outside) if task), return_exceptions=True)


@pytest.mark.parametrize('mode', [
    'normal', 'timeout-drained', 'drain-held', 'first-held', 'called-error',
    'kill-error', 'cancel', 'wait-held', 'equal-run-clone', 'equal-read-clone', 'foreign-process',
])
def test_default_runtime_probe_shared_subprocess_lifecycle_original_bodies(tmp_path, monkeypatch, mode):
    import inspect
    import subprocess
    import textwrap
    from types import FunctionType
    fixture = _default_runtime_probe_module()
    snapshots, checkpoints, calls = [], {}, []
    clock = [0]
    token = object()
    timeout = subprocess.TimeoutExpired('private argv', 35, output=b'private output')
    cancelled = asyncio.CancelledError('private cancellation')
    kill_error = PermissionError(5, 'private PID or handle')
    class Held(BaseException): pass
    held = Held()

    class Process:
        returncode = 0
        stdin, stdout, stderr = None, token, None
        _communication_started = False
        args = 'private argv'

        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def _remaining_time(self, endtime):
            return None if endtime is None else endtime - clock[0]
        def _communicate(self, input, endtime, orig_timeout):
            calls.append(('communicate', orig_timeout))
            if orig_timeout == 35:
                checkpoints['first'] = snapshots[-1]
                # Same method code, different object AND unrelated caller.
                # These must not be mistaken for this run's process lifecycle.
                other = Process()
                other.wait()
                if mode == 'first-held':
                    clock[0] = 901
                    checkpoints['first-held'] = snapshots[-1]
                    raise held
                if mode == 'cancel':
                    raise cancelled
                if mode not in {'normal', 'called-error', 'wait-held', 'equal-run-clone', 'equal-read-clone', 'foreign-process'}:
                    clock[0] = 35
                    raise timeout
            else:
                checkpoints['drain'] = snapshots[-1]
                if mode == 'drain-held':
                    clock[0] = 901
                    checkpoints['drain-held'] = snapshots[-1]
                    raise held
            return ('synthetic', None)
        def kill(self):
            calls.append(('kill', None))
            if mode == 'kill-error':
                raise kill_error
        def wait(self, timeout=None):
            if mode == 'wait-held' and self is not process_other:
                # Only the retained original communicator has a trusted parent.
                if snapshots[-1]['events'].get('wait'):
                    checkpoints['wait-held'] = snapshots[-1]
                    raise held
            return 0
        def poll(self):
            return 7 if mode == 'called-error' else 0

    process_other = Process()
    # No monkeypatch of the stdlib. Execute its actual maintained function
    # bodies with inert process/OS globals; no native handle or thread exists.
    globals_ = {'Popen': Process, 'TimeoutExpired': subprocess.TimeoutExpired,
                'CalledProcessError': subprocess.CalledProcessError, '_mswindows': True,
                'PIPE': -1, '_time': lambda: clock[0], 'CompletedProcess': lambda *args: args}
    exec(compile(inspect.getsource(subprocess.run), '<actual-stdlib-run-model>', 'exec'), globals_)
    if mode == 'foreign-process':
        import ast
        # Negative discriminator: inject an inert method call in the SAME run
        # frame, while its retained 'process' is a different object. All other
        # cases execute the unchanged stdlib run body above.
        tree = ast.parse(inspect.getsource(subprocess.run))
        body = next(node for node in ast.walk(tree) if isinstance(node, ast.With))
        body.body[:0] = ast.parse('Popen().wait()').body
        exec(compile(ast.fix_missing_locations(tree), '<foreign-process-model>', 'exec'), globals_)
    exec(compile(textwrap.dedent(inspect.getsource(subprocess.Popen.communicate)),
                 '<actual-communicate-model>', 'exec'), globals_)
    Process.communicate = globals_['communicate']
    api = SimpleNamespace(run=globals_['run'], Popen=Process, TimeoutExpired=subprocess.TimeoutExpired)
    monkeypatch.setattr(fixture, 'subprocess', api)
    monkeypatch.setattr(fixture, 'time', SimpleNamespace(monotonic=lambda: clock[0]))

    def read():
        return api.run(['private git argv'], stdout=-1, text=True, timeout=35, check=True)

    def unrelated():
        return api.run(['another private argv'], stdout=-1, text=True, timeout=35, check=True)

    invoked_read = read
    if mode == 'equal-read-clone':
        code = read.__code__.replace(co_filename='equal-but-foreign-read.py')
        assert code == read.__code__ and code is not read.__code__
        invoked_read = FunctionType(code, read.__globals__, closure=read.__closure__)
    assert sys.getprofile() is None
    expected_error = (timeout if mode == 'timeout-drained' else
                      held if mode in {'drain-held', 'first-held', 'wait-held'} else
                      cancelled if mode == 'cancel' else kill_error if mode == 'kill-error' else None)
    try:
        with fixture.observe_shared_subprocess(read, snapshots.append):
            if mode == 'equal-run-clone':
                original = api.run
                code = original.__code__.replace(co_filename='equal-but-foreign-run.py')
                assert code == original.__code__ and code is not original.__code__
                api.run = FunctionType(code, original.__globals__, argdefs=original.__defaults__)
                api.run.__kwdefaults__ = original.__kwdefaults__
            if mode == 'normal':
                assert unrelated()[1] == 0
                assert snapshots[-1]['events'] == {}
            if expected_error is not None:
                with pytest.raises(type(expected_error)) as caught:
                    invoked_read()
                assert caught.value is expected_error
            elif mode == 'called-error':
                with pytest.raises(subprocess.CalledProcessError) as caught:
                    invoked_read()
                assert caught.value.returncode == 7
            else:
                assert invoked_read()[1:] == (0, 'synthetic', None)
        assert sys.getprofile() is None
        final = snapshots[-1]
        fixture.validate_subprocess_lifecycle(final)
        assert final['status'] == 'PASS'
        assert set(final['codeHashes']) == {'run', 'communicate', 'kill', 'wait'}
        assert Process.communicate is globals_['communicate']
        if mode != 'equal-run-clone':
            assert api.run is globals_['run']  # Observer replaced no method.
        assert 'private' not in json.dumps(snapshots)
        if mode in {'equal-run-clone', 'equal-read-clone'}:
            assert final['events'] == {}  # Equality is not loaded-code identity.
            return
        assert final['events']['run']['entered'] == final['events']['run']['exited'] == 1
        first = checkpoints['first']['events']['first_communicate']
        assert first['entered'] == 1 and first['exited'] == 0 and first['exitBucket'] is None
        if mode in {'timeout-drained', 'drain-held'}:
            drain = checkpoints['drain']['events']
            assert checkpoints['drain']['timeoutSeen']
            assert drain['kill']['entered'] == drain['kill']['exited'] == 1
            assert drain['first_communicate']['exited'] == 1
            assert drain['drain_communicate'] == {
                'entered': 1, 'exited': 0, 'entryBucket': 3, 'exitBucket': None}
            assert final['events']['drain_communicate']['exitBucket'] == (7 if mode == 'drain-held' else 3)
        if mode == 'first-held':
            assert not checkpoints['first-held']['timeoutSeen']
            assert 'kill' not in checkpoints['first-held']['events']
            assert 'drain_communicate' not in final['events']
        if mode == 'wait-held':
            assert checkpoints['wait-held']['events']['wait']['exited'] == 0
        if mode == 'foreign-process':
            assert final['events']['wait']['entered'] == final['events']['wait']['exited'] == 1
        assert final['timeoutSeen'] is (mode in {'timeout-drained', 'drain-held', 'kill-error'})
        assert ('drain_communicate' in final['events']) is (mode in {'timeout-drained', 'drain-held'})
    finally:
        sys.setprofile(None)  # Only this synthetic in-process observer.


@pytest.mark.parametrize('fault', ['existing', 'install', 'fingerprint', 'publish', 'replacement'])
def test_default_runtime_probe_lifecycle_refusal_and_fault_preserve_original(monkeypatch, fault):
    fixture = _default_runtime_probe_module()
    snapshots = []
    error, token = ValueError('private original error'), object()
    previous = lambda *args: None
    replacement = lambda *args: None
    state = [previous if fault == 'existing' else None]
    def setprofile(value):
        if fault == 'install':
            raise RuntimeError('private installation fault')
        state[0] = value  # Inert model: no actual profiler installed.
    monkeypatch.setattr(fixture, 'sys', SimpleNamespace(getprofile=lambda: state[0], setprofile=setprofile))
    if fault == 'fingerprint':
        monkeypatch.setattr(fixture, 'code_fingerprint', Mock(side_effect=RuntimeError('private hash fault')))
    publications = []
    def publish(value):
        publications.append(value)
        if fault == 'publish' and len(publications) == 1:
            raise RuntimeError('private publisher fault')
        snapshots.append(value)
    def read():
        raise error
    with pytest.raises(ValueError) as caught:
        with fixture.observe_shared_subprocess(read, publish):
            if fault == 'replacement':
                state[0] = replacement
            raise error
    assert caught.value is error
    assert snapshots[-1]['status'] == ('refused' if fault == 'existing' else 'FAIL')
    assert state[0] is (previous if fault == 'existing' else replacement if fault == 'replacement' else None)
    # A refused observer does not run tools, change outcomes or overwrite hooks.
    with fixture.observe_shared_subprocess(read, lambda value: None):
        result = token
    assert result is token


def test_default_runtime_probe_lifecycle_closed_schema_and_existing_budget(tmp_path):
    fixture = _default_runtime_probe_module()
    event = {'entered': 10**6, 'exited': 10**6, 'entryBucket': 8, 'exitBucket': 8}
    lifecycle = {'status': 'PASS', 'timeoutSeen': True,
                 'events': dict.fromkeys(fixture.SUBPROCESS_EVENTS, event),
                 'codeHashes': dict.fromkeys(('run', 'communicate', 'kill', 'wait'), 'f' * 64)}
    operation = {'module': 'context-simple', 'operation': 'shared_remote', 'queued': 1,
                 'entered': 1, 'returned': 0, 'failed': 1,
                 'errors': [{'class': 'TimeoutExpired', 'frames': []}],
                 'subprocessLifecycle': lifecycle}
    frame = {'source': max(fixture.CODE_LABELS, key=len), 'offset': 10**6}
    error = {'class': 'CalledProcessError', 'frames': [frame] * 5, 'returncode': -(2**31)}
    facts = {'diagnosticStatus': 'PASS', 'observed': True, 'taskDone': True,
             'awaiting': [frame] * 5, 'errors': [error] * 4,
             'activationChildren': [{'module': 'hooks-session-naming', 'taskDone': True,
                'cancelled': False, 'awaiting': [frame] * 3,
                'errors': [{**error, 'frames': [frame] * 2}]}] * 4,
             'activationTruncated': True, 'threadOperations': [operation] * 8,
             'operationsTruncated': True,
             'codeHashes': dict.fromkeys(list(fixture.CODE_LABELS)[:16], 'e' * 64),
             'foundation': {'status': 'verified', 'version': '1' * 100, 'commit': 'b' * 64}}
    fixture.validate_worker_startup(facts)
    raw = json.dumps(facts)
    assert len(raw.encode()) <= fixture.WORKER_DIAGNOSTIC_BYTES == 16 * 1024
    path = tmp_path / 'private-observation.json'
    path.write_text(raw, encoding='utf-8')
    assert fixture.read_worker_startup(path) == facts
    for patch in ({'status': 'private'}, {'timeoutSeen': 1}, {'pid': 99},
                  {'events': {'private-callee': event}},
                  {'events': {'run': {**event, 'entered': True}}},
                  {'events': {'run': {**event, 'exited': 10**6 + 1}}},
                  {'events': {'run': {**event, 'entryBucket': 9}}},
                  {'events': {'run': {**event, 'exitBucket': 'private'}}},
                  {'events': {'run': {**event, 'exitBucket': None}}},
                  {'events': {'run': {**event, 'entryBucket': 8, 'exitBucket': 3}}},
                  {'events': {'run': {**event, 'stdout': 'private'}}},
                  {'codeHashes': {'private-path': 'f' * 64}},
                  {'codeHashes': {'run': 'not-hash'}},
                  {'timeoutSeen': False},
                  {'events': {}}):
        with pytest.raises(AssertionError):
            fixture.validate_subprocess_lifecycle({**lifecycle, **patch})
    with pytest.raises(AssertionError):
        fixture.validate_worker_startup({**facts, 'threadOperations': [
            {**operation, 'operation': 'receipt_reuse'}]})


@pytest.mark.parametrize('cloned', [False, True])
@pytest.mark.parametrize('outcome', ['return', 'error', 'cancel'])
async def test_default_runtime_probe_worker_await_observer_is_exact_and_outcome_neutral(
        tmp_path, monkeypatch, cloned, outcome):
    from types import FunctionType
    fixture = _default_runtime_probe_module()
    release, entered = asyncio.Event(), asyncio.Event()
    token, original_error = object(), ValueError('private configured module failure')

    async def prepare_manager():
        entered.set()
        await release.wait()
        if outcome == 'error':
            raise original_error
        return token

    class Worker:
        async def start(self):
            try:
                return await prepare_manager()
            finally:
                if outcome == 'cancel':
                    await asyncio.sleep(0)  # Like the real worker's async cleanup.

    loaded = Worker.start
    if cloned:
        code = loaded.__code__.replace(co_filename='private-equal-code-clone.py')
        assert code == loaded.__code__ and code is not loaded.__code__
        method = FunctionType(code, loaded.__globals__, closure=loaded.__closure__)
    else:
        method = loaded
    path = tmp_path / 'privately-created-observation.json'
    path.write_text('{}', encoding='utf-8')
    # Model only the loaded-code table. The hook and real CPython coroutine
    # suspension/profile semantics execute unchanged; no runtime/job is launched.
    monkeypatch.setattr(fixture, 'sys', SimpleNamespace(
        modules={**sys.modules, '__main__': SimpleNamespace(Worker=Worker),
                 'amplifier_web.host.session': SimpleNamespace(prepare_manager=prepare_manager)},
        getprofile=sys.getprofile, setprofile=sys.setprofile))
    assert sys.getprofile() is None
    owner = Worker()
    fixture.install_worker_start_observer(path)
    try:
        owner.start_task = asyncio.create_task(method(owner))
        await asyncio.wait_for(entered.wait(), .5)
        await asyncio.sleep(0)  # Only allow the already queued observer callback.
        facts = fixture.read_worker_startup(path)
        if cloned:
            assert facts['diagnosticStatus'] == 'FAIL' and not facts['observed']
        else:
            assert facts['diagnosticStatus'] == 'PASS' and facts['observed'] and not facts['taskDone']
            assert [frame['source'] for frame in facts['awaiting']] == ['worker_start', 'manager_prepare']
            assert all(type(frame['offset']) is int for frame in facts['awaiting'])
            assert 'private' not in json.dumps(facts)
        if outcome == 'cancel':
            owner.start_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await owner.start_task
        else:
            release.set()
            if outcome == 'return':
                assert await owner.start_task is token
            else:
                with pytest.raises(ValueError) as caught:
                    await owner.start_task
                assert caught.value is original_error
        await asyncio.sleep(0)
        if not cloned:
            final = fixture.read_worker_startup(path)
            assert final['taskDone']
            if outcome == 'error':
                assert final['errors'][0]['class'] == 'ValueError'
                assert {frame['source'] for frame in final['errors'][0]['frames']} == {
                    'worker_start', 'manager_prepare'}
            else:
                assert final['errors'] == []
            if outcome == 'cancel':
                assert [frame['source'] for frame in final['awaiting']] == ['worker_start', 'manager_prepare']
            assert sys.getprofile() is None
    finally:
        sys.setprofile(None)  # Only this test's in-process synthetic profiler.
        if not owner.start_task.done():
            owner.start_task.cancel()
        await asyncio.gather(owner.start_task, return_exceptions=True)


@pytest.mark.parametrize('stage,outcome,cloned,count', [
    ('resolve', 'cancel', False, 2), ('install', 'cancel', False, 2),
    ('resolve', 'return', False, 2), ('install', 'return', False, 2),
    ('resolve', 'child-error', False, 2), ('resolve', 'cancel', True, 2),
    ('resolve', 'cancel', False, 6),
])
async def test_default_runtime_probe_activation_children_are_gather_scoped_and_bounded(
        tmp_path, monkeypatch, stage, outcome, cloned, count):
    from types import FunctionType, MethodType
    from amplifier_foundation.modules import activator
    from amplifier_foundation.sources.resolver import SimpleSourceResolver
    fixture = _default_runtime_probe_module()
    release, entered, outsider_release = asyncio.Event(), set(), asyncio.Event()
    original_error = PermissionError(13, 'private module path/URL/account')

    class Handler:
        def can_handle(self, parsed):
            return True

        async def resolve(self, parsed, cache_dir):
            if 'outsider' in parsed.path:
                await outsider_release.wait()
            elif stage == 'resolve':
                entered.add(parsed.path)
                await release.wait()
                if outcome == 'child-error' and 'module-0' in parsed.path:
                    raise original_error
                if outcome == 'child-error':
                    await outsider_release.wait()  # Keep the gather pending.
            return SimpleNamespace(active_path=Path(parsed.path))

    resolver = object.__new__(SimpleSourceResolver)
    resolver.base_path, resolver.cache_dir, resolver._handlers = tmp_path, tmp_path / 'cache', [Handler()]
    owner = object.__new__(activator.ModuleActivator)
    owner._resolver, owner._activated, owner.install_deps, owner.strict = resolver, set(), stage == 'install', True
    owner.dependency_batch, owner.refresh_dependencies = None, False
    owner.install_python = sys.executable

    async def inert_thread(function, *args, **kwargs):
        # Drive the REAL _install_dependencies await without starting a thread,
        # install, process or reading install state. This models only that wait.
        entered.add(function.__self__.source.name)
        await release.wait()
        return True

    monkeypatch.setattr(activator, 'asyncio', SimpleNamespace(gather=asyncio.gather, to_thread=inert_thread))
    monkeypatch.setattr(sys, 'path', list(sys.path))
    loaded_activate = activator.ModuleActivator.activate
    if cloned:
        code = loaded_activate.__code__.replace(co_filename='private-equal-activate-clone.py')
        assert code is not loaded_activate.__code__ and code == loaded_activate.__code__
        owner.activate = MethodType(FunctionType(
            code, loaded_activate.__globals__, argdefs=loaded_activate.__defaults__), owner)
    modules = [{'module': ('loop-live' if i == 0 else 'context-simple' if i == 1
                           else 'private-module-name'),
                'source': str(tmp_path / f'module-{i}')} for i in range(count)]

    class Worker:
        async def start(self):
            try:
                return await owner.activate_all(modules)
            finally:
                await asyncio.sleep(0)  # Must not overwrite pre-cancel waits.

    path = tmp_path / 'privately-created-observation.json'
    path.write_text('{}', encoding='utf-8')
    monkeypatch.setattr(fixture, 'sys', SimpleNamespace(
        modules={**sys.modules, '__main__': SimpleNamespace(Worker=Worker)},
        getprofile=sys.getprofile, setprofile=sys.setprofile))
    assert sys.getprofile() is None
    worker, outsiders = Worker(), []
    fixture.install_worker_start_observer(path)
    try:
        worker.start_task = asyncio.create_task(worker.start())
        async with asyncio.timeout(1):
            while len(entered) < count:
                await asyncio.sleep(0)
        # Same activate code AND same activator, but not in the trusted gather.
        outsiders.append(asyncio.create_task(owner.activate(
            'hooks-session-naming', str(tmp_path / 'outsider'))))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        facts = fixture.read_worker_startup(path)
        assert facts['diagnosticStatus'] == 'PASS' and facts['observed']
        assert 'module_activate' in {frame['source'] for frame in facts['awaiting']}
        if cloned:
            assert 'activationChildren' not in facts
        else:
            children = facts['activationChildren']
            assert len(children) == min(count, 4) and facts['activationTruncated'] is (count > 4)
            assert {child['module'] for child in children} <= {'loop-live', 'context-simple', 'other'}
            expected = 'source_resolve' if stage == 'resolve' else 'module_install'
            assert all(expected in {frame['source'] for frame in child['awaiting']} for child in children)
            assert all(not child['taskDone'] and not child['cancelled'] for child in children)
            assert 'private' not in json.dumps(facts) and 'outsider' not in json.dumps(facts)
            fixture.validate_worker_startup(facts)
        if outcome in {'return', 'child-error'}:
            release.set()
            if outcome == 'return':
                results = await asyncio.wait_for(worker.start_task, 1)
                assert set(results) == {row['module'] for row in modules}
            else:
                async with asyncio.timeout(1):
                    while not fixture.read_worker_startup(path).get('activationChildren', [{}])[0].get('errors'):
                        await asyncio.sleep(0)
                child = fixture.read_worker_startup(path)['activationChildren'][0]
                assert child['taskDone'] and child['errors'][0]['class'] == 'PermissionError'
                assert child['errors'][0]['errno'] == 13
                worker.start_task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await worker.start_task
        else:
            worker.start_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await worker.start_task
        await asyncio.sleep(0)
        final = fixture.read_worker_startup(path)
        assert final['taskDone'] and final['diagnosticStatus'] == 'PASS'
        if not cloned:
            assert [row['awaiting'] for row in final['activationChildren']] == [
                row['awaiting'] for row in facts['activationChildren']]
            assert all(row['taskDone'] for row in final['activationChildren'])
            assert 'hooks-session-naming' not in json.dumps(final)
        assert sys.getprofile() is None
    finally:
        sys.setprofile(None)
        for task in [worker.start_task, *outsiders]:
            if not task.done():
                task.cancel()
        await asyncio.gather(worker.start_task, *outsiders, return_exceptions=True)


def test_default_runtime_probe_worker_observer_never_replaces_existing_profiler(tmp_path, monkeypatch):
    fixture = _default_runtime_probe_module()
    path = tmp_path / 'privately-created-observation.json'
    path.write_text('{}', encoding='utf-8')
    setprofile = Mock(side_effect=AssertionError('must not replace an existing profiler'))
    monkeypatch.setattr(fixture, 'sys', SimpleNamespace(getprofile=lambda: object(), setprofile=setprofile))
    fixture.install_worker_start_observer(path)
    assert fixture.read_worker_startup(path) == {
        'diagnosticStatus': 'FAIL', 'observed': False, 'taskDone': False, 'awaiting': [], 'errors': []}
    setprofile.assert_not_called()


def test_default_runtime_probe_uninstrumented_entry_has_no_worker_hook_path():
    import ast
    fixture = _default_runtime_probe_module()
    tree = ast.parse(Path(fixture.__file__).read_text(encoding='utf-8'))
    functions = {node.name: node for node in tree.body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert 'worker_startup_observer' not in functions
    # Measurement helpers remain only for historical diagnostic-schema/model
    # checks. Recursively inspect the actual fixture entry's local call graph.
    pending, visited = ['main'], set()
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        visited.add(name)
        node = functions[name]
        pending.extend(child.id for child in ast.walk(node)
                       if isinstance(child, ast.Name) and child.id in functions and child.id not in visited)
        assert not any(isinstance(child, ast.Call)
                       and getattr(child.func, 'attr', '') in {'setprofile', 'settrace'}
                       for child in ast.walk(node))
        assert not any(isinstance(child, ast.Constant) and isinstance(child.value, str)
                       and any(value in child.value for value in (
                           'sitecustomize', 'startup-observer', 'worker-startup.json', 'PYTHONPATH'))
                       for child in ast.walk(node))
    assert {'journey', 'observe_default_start', 'observe_lock', 'installed_host'} <= visited
    assert not visited & {'install_worker_start_observer', 'observe_shared_subprocess',
                          'loaded_code', 'code_fingerprint', 'worker_foundation', 'read_worker_startup'}
    journey = functions['journey']
    calls = [node for node in ast.walk(journey) if isinstance(node, ast.Call)]
    manager = [node for node in calls if getattr(node.func, 'id', None) == 'RuntimeManager']
    assert len(manager) == 1 and not manager[0].args
    assert {keyword.arg for keyword in manager[0].keywords} == {'startup_timeout', 'retention'}
    assert next(keyword.value.value for keyword in manager[0].keywords
                if keyword.arg == 'startup_timeout') == 900
    # The parent keeps its approved explicit Python, finite deadline and owned
    # Job. The native entry still strips inherited PYTHONPATH rather than
    # treating a successful instrumented result as normal-source acceptance.
    import inspect
    native = ast.parse(inspect.getsource(
        test_native_windows_default_runtime_command_cold_prepare_turn_and_stop))
    runs = [node for node in ast.walk(native) if isinstance(node, ast.Call)
            and getattr(node.func, 'attr', None) == 'run_owned_probe']
    assert len(runs) == 1
    assert next(keyword.value.value for keyword in runs[0].keywords if keyword.arg == 'timeout') == 1200


@pytest.mark.parametrize('failure', [None, 'start', 'cancel', 'not-ready', 'identity', 'provenance', 'cleanup'])
async def test_default_runtime_probe_uninstrumented_journey_keeps_real_acceptance(
        tmp_path, monkeypatch, failure):
    """Actual maintained journey with inert app/kernel, never a Windows pass."""
    import builtins
    import os
    from contextlib import nullcontext
    from types import FunctionType
    fixture = _default_runtime_probe_module()
    root = tmp_path / 'scratch'
    app, project = root / 'installed-app', root / 'app/runtime/project'
    manifest, wheel = tmp_path / 'manifest.toml', tmp_path / 'candidate.whl'
    manifest.write_bytes(b'[project]\nname = "synthetic-runtime"\n')
    wheel.write_bytes(b'synthetic audited-wheel boundary')
    hashes = dict.fromkeys(fixture.APP_FILES, 'a' * 64)
    imports = {name: {'version': '1.0', 'commit': None, 'sha256': 'b' * 64}
               for name in fixture.IMPORTS}
    environment = dict(os.environ)
    writes, events, calls = {}, [], []
    error = asyncio.CancelledError() if failure == 'cancel' else RuntimeError('original startup failure')
    ready = asyncio.get_running_loop().create_future()
    job = SimpleNamespace(identity=SimpleNamespace(guard_pid=7), tree_empty=False,
                          io_finalized=False, verify_member=Mock())
    owner = SimpleNamespace(job=job, process=SimpleNamespace(pid=11), created=123, result=None)
    row = {'windows_owner': owner, 'process': owner.process, 'ready': ready,
           'generation': None, 'source_generation': None, 'native_worker_pid': 11, 'parked': True}
    session = {'id': 'synthetic-cold-session'}
    class Runtime:
        command = None
        def __init__(self, **kwargs):
            assert kwargs == {'startup_timeout': 900, 'retention': {'prewarm_on_select': False}}
            self.workers, self._preparations = {}, {}
            calls.append(self)
        async def start(self, selected, emit):
            assert selected is session and not project.exists()  # Fresh until real startup boundary.
            assert dict(os.environ) == environment and sys.getprofile() is None
            await emit('runtime.status', {'phase': 'runtime-setup'})
            if failure in {'start', 'cancel'}:
                raise error
            for name in ('pyproject.toml', 'uv.lock', '.venv/Scripts/python.exe'):
                target = project / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(manifest.read_bytes() if name == 'pyproject.toml' else
                    b'version = 1\n' if name == 'uv.lock' else b'synthetic Python')
            with (project / 'uv.lock').open('a', encoding='utf-8') as stream:
                for name in dict.fromkeys(fixture.IMPORTS.values()):
                    stream.write('[[package]]\nname = "' + name + '"\nversion = "1.0"\nsource = {registry = "https://example.invalid/simple"}\n')
            self.workers[session['id']] = row
            if failure != 'not-ready':
                ready.set_result({})
            if failure == 'identity':
                row['native_worker_pid'] = 99
            await emit('runtime.status', {'phase': 'ready'})
        def _command(self):
            return ['synthetic-uv.exe', 'run', '--project', str(project),
                    '--python', '3.13', 'python', str(app / 'runtime_worker.py')]
        @staticmethod
        def project_path(command):
            return command[command.index('--project') + 1]
        async def stop(self, sid):
            assert sid == session['id']
            self.workers.clear()
            owner.result = SimpleNamespace(active_processes=1 if failure == 'cleanup' else 0)
            job.tree_empty = job.io_finalized = failure != 'cleanup'
    class Service:
        def __init__(self, home, *, runtime, workspace):
            assert home == root / 'app' and workspace == root / 'workspace'
            self.runtime = runtime
        async def app_bridge(self, *args):
            raise AssertionError('inert journey must never invoke app bridge')
        async def dispatch(self, action, args, **kwargs):
            events.append(action)
            if action == 'conversation.send':
                proof = {'pid': 11, 'imports': {key: dict(value) for key, value in imports.items()},
                         'appHashes': hashes}
                if failure == 'provenance':
                    proof['imports']['amplifier_foundation']['version'] = '2.0'
                (root / 'worker-proof.json').write_text(json.dumps(proof), encoding='utf-8')
        def _session(self, *args):
            return session
        async def on_runtime_event(self, kind, data):
            assert dict(os.environ) == environment
        async def close(self):
            await self.runtime.stop(session['id'])
    def write_private(path, value, *, owned_root):
        assert path.is_relative_to(owned_root)
        assert path.suffix != '.pth' and path.name != 'sitecustomize.py'
        assert 'startup-observer' not in str(path)
        writes[path] = value
    async def ensure(home, generation, selected):
        assert home == root / 'app' and generation is None and selected is session
        return None
    modules = {
        'amplifier_web.builtin_behaviors': SimpleNamespace(DEFAULT_BEHAVIORS=('excluded',)),
        'amplifier_web.deployment': SimpleNamespace(write_private=write_private),
        'amplifier_web.runtime': SimpleNamespace(RuntimeManager=Runtime),
        'amplifier_web.runtime_environment': SimpleNamespace(
            manifest_path=lambda: manifest, project_path=lambda _: project),
        'amplifier_web.runtime_profiles': SimpleNamespace(ensure=ensure),
        'amplifier_web.service': SimpleNamespace(AppService=Service),
        'amplifier_web.session_files': SimpleNamespace(sessions_dir=lambda _: root / 'history'),
        'amplifier_web.windows_files': SimpleNamespace(directory=lambda *args, **kwargs: nullcontext()),
        'amplifier_foundation.session.history': SimpleNamespace(SessionHistoryStore=lambda _: SimpleNamespace(
            load_messages=lambda: [{'role': 'assistant', 'content': 'Default command café verified'}])),
    }
    def imports_only(name, *args, **kwargs):
        assert name in modules
        return modules[name]  # No app imports, service/database or native launch.
    def inert_home(selected):
        assert selected == root and not project.exists()
    globals_ = {**fixture.__dict__, 'prepare_fixture_home': inert_home,
                'installed_host': lambda *args: (app, hashes),
                '__builtins__': {**vars(builtins), '__import__': imports_only}}
    for name in ('worker_startup_observer', 'read_worker_startup',
                 'install_worker_start_observer', 'observe_shared_subprocess'):
        globals_[name] = Mock(side_effect=AssertionError('retired worker hook reached: ' + name))
    actual = FunctionType(fixture.journey.__code__, globals_)
    report = fixture.empty_inner_report()
    if failure is None:
        await actual(root, wheel, report)
        assert report['status'] == 'PASS' and all(report['checks'].values())
        fixture.validate_inner_report(report)
        job.verify_member.assert_called_once_with(11, 123)
    else:
        expected = asyncio.CancelledError if failure == 'cancel' else RuntimeError if failure == 'start' else AssertionError
        with pytest.raises(expected) as caught:
            await actual(root, wheel, report)
        if failure in {'start', 'cancel'}:
            assert caught.value is error
        assert report['status'] == 'FAIL'
    assert len(calls) == 1 and calls[0].command is None and not calls[0].workers
    assert 'workerStartup' not in report
    assert dict(os.environ) == environment and sys.getprofile() is None
    assert set(writes) == {root / 'shared/settings.yaml',
                          root / 'provider/amplifier_module_provider_default_fixture/__init__.py',
                          root / 'bundle.yaml'}
    for name in ('worker_startup_observer', 'read_worker_startup',
                 'install_worker_start_observer', 'observe_shared_subprocess'):
        globals_[name].assert_not_called()
    assert events == (['session.create', 'conversation.send']
                      if failure in {None, 'provenance', 'cleanup'} else ['session.create'])


def test_default_runtime_probe_uninstrumented_pass_rejects_historical_injected_acceptance():
    fixture = _default_runtime_probe_module()
    inner = {'schema': 1, 'status': 'PASS', 'stage': 'complete',
             'checks': dict.fromkeys(fixture.CHECKS, True), 'wheelSha256': 'a' * 64,
             'manifestSha256': 'b' * 64, 'lockSha256': 'c' * 64,
             'appHashes': dict.fromkeys(fixture.APP_FILES, 'd' * 64),
             'workerImports': {name: {'version': '1.0', 'commit': None, 'sha256': 'e' * 64}
                               for name in fixture.IMPORTS}}
    fixture.validate_inner_report(inner)  # No injected facts required for normal startup.
    old_facts = {'diagnosticStatus': 'PASS', 'observed': True, 'taskDone': True,
                 'errors': [], 'awaiting': [{'source': 'worker_start', 'offset': 82}]}
    old = {**inner, 'workerStartup': old_facts}
    with pytest.raises(AssertionError):
        fixture.validate_inner_report(old)
    old['status'] = 'FAIL'
    fixture.validate_inner_report(old)
    assert fixture.parse_inner_output(json.dumps(old)) == old
    assert old['status'] == 'FAIL'  # Passive validation never promotes old evidence.


@pytest.mark.parametrize('fault', [
    None, 'missing-path', 'wrong-name', 'outside', 'case-sibling', 'symlink',
    'missing-file', 'unsafe', 'close', 'oversize', 'malformed', 'missing-facts', 'extra-field',
])
def test_default_runtime_probe_exact_private_startup_receipt(tmp_path, monkeypatch, fault):
    from contextlib import contextmanager
    from amplifier_web import windows_files
    fixture = _default_runtime_probe_module()
    home = tmp_path / 'app'
    name = 'startup-' + 'a' * 32 + '.log'
    path = home / 'logs/workers' / name
    if fault == 'wrong-name':
        path = path.with_name('other.log')
    if fault == 'outside':
        path = tmp_path / 'other/logs/workers' / name
    if fault == 'case-sibling':
        path = tmp_path / 'App/logs/workers' / name
    facts = {'errors': [{'class': 'ModuleActivationError'}, {'class': 'TimeoutExpired'}],
             'truncated': False}
    header = {'startupFailureFacts': facts, 'failure': 'private failure text URL path SID token'}
    if fault == 'missing-facts':
        header.pop('startupFailureFacts')
    if fault == 'extra-field':
        facts['errors'][1]['stderr'] = 'private text'
    raw = json.dumps(header).encode() + b'\n\nraw private stderr never uploaded'
    if fault == 'malformed':
        raw = b'{bad json\n\nprivate'
    if fault == 'oversize':
        raw = b'x' * 60_001
    opened, reads, closed = [], [], []
    @contextmanager
    def directory(parent, *, owned_root, create):
        assert parent == home / 'logs/workers' and owned_root == home and create is False
        opened.append(parent)
        if fault in {'symlink', 'unsafe'}:
            raise PermissionError('native refuses reparse/unsafe ACL without reading')
        def read(selected, *, limit):
            reads.append(selected)
            assert selected == name and limit == 60_000
            if fault == 'missing-file':
                raise FileNotFoundError('missing selected receipt')
            return raw
        try:
            yield SimpleNamespace(read_private=read)
        finally:
            closed.append(True)
            if fault == 'close':
                raise OSError('native handle close not confirmed')
    monkeypatch.setattr(windows_files, 'directory', directory)
    error = RuntimeError('original')
    if fault != 'missing-path':
        error.diagnostic_path = path
    result = fixture.read_startup_failure(home, error)
    expected = 'present' if fault is None else 'missing' if fault in {
        'missing-path', 'missing-file', 'missing-facts'} else 'invalid'
    assert result == {'status': expected, 'facts': facts if fault is None else None}
    assert 'private' not in json.dumps(result)
    if fault in {'missing-path', 'wrong-name', 'outside', 'case-sibling'}:
        assert not opened and not reads
    elif fault in {'symlink', 'unsafe'}:
        assert opened and not reads
    else:
        assert reads == [name] and closed == [True]
    assert not home.exists()  # Fake native boundary; no real path/ACL mutation.
    report = fixture.empty_inner_report()
    report['startupFailureFacts'] = result
    fixture.validate_inner_report(report)
    assert fixture.parse_inner_output(json.dumps(report)) == report and report['status'] == 'FAIL'


@pytest.mark.parametrize('outcome', ['raise', 'cancel', 'read-fault'])
async def test_default_runtime_probe_private_receipt_observation_preserves_original(
        tmp_path, monkeypatch, outcome):
    fixture = _default_runtime_probe_module()
    error = asyncio.CancelledError('original') if outcome == 'cancel' else RuntimeError('original')
    error.diagnostic_path = tmp_path / 'selected-private-receipt'
    def observe(home, selected):
        assert home == tmp_path and selected is error
        if outcome == 'read-fault':
            raise asyncio.CancelledError('diagnostic failure cannot replace original')
        return {'status': 'missing', 'facts': None}
    monkeypatch.setattr(fixture, 'read_startup_failure', observe)
    async def start(session, emit):
        raise error
    runtime = SimpleNamespace(home=tmp_path, workers={}, start=start)
    async def emit(*args): pass
    report = fixture.empty_inner_report()
    with pytest.raises(type(error)) as caught:
        await fixture.observe_default_start(runtime, {'id': 'synthetic'}, emit, report, tmp_path)
    assert caught.value is error and report['status'] == 'FAIL'
    assert report['startupFailureFacts'] == {
        'status': 'invalid' if outcome == 'read-fault' else 'missing', 'facts': None}
    assert report['lockObservation'] == {'status': 'missing', 'sha256': None}
    fixture.validate_inner_report(report)


def test_default_runtime_probe_private_receipt_facts_schema_and_diagnostic_byte_audit(tmp_path, monkeypatch):
    from amplifier_web import worker_diagnostics as diagnostics
    from amplifier_web.host import config
    fixture = _default_runtime_probe_module()
    writes = []
    monkeypatch.setattr(config, 'app_home', lambda: tmp_path)
    monkeypatch.setattr(config, 'write_private', lambda path, raw, **kwargs: writes.append(raw))
    capture = diagnostics.StartupCapture()
    error = RuntimeError('private original')
    error.__cause__ = fixture.subprocess.CalledProcessError(128, ['secret command'], stderr='private stderr')
    try:
        selected = capture.save(error, 'synthetic')
    finally:
        capture.close()
    assert selected is not None
    facts = json.loads(writes[0].partition('\n\n')[0])['startupFailureFacts']
    fixture.validate_startup_failure_facts(facts)
    assert facts == {'errors': [{'class': 'RuntimeError'}, {'class': 'CalledProcessError', 'returncode': 128}],
                     'truncated': False}
    assert 'worker_diagnostics.py' in fixture.APP_FILES  # Host AND scripted worker use this audit.
    for invalid in (
        {**facts, 'path': 'private'}, {**facts, 'errors': []}, {**facts, 'errors': facts['errors'] * 3},
        {**facts, 'truncated': 1}, {**facts, 'errors': [{'class': 'PrivateError'}]},
        {**facts, 'errors': [{'class': 'TimeoutExpired', 'returncode': 128}]},
        {**facts, 'errors': [{'class': 'other', 'errno': 13}]},
        {**facts, 'errors': [{'class': 'CalledProcessError', 'returncode': True}]},
        {**facts, 'errors': [{'class': 'PermissionError', 'errno': 2**32}]},
    ):
        with pytest.raises(AssertionError):
            fixture.validate_startup_failure_facts(invalid)
    # A previously valid seven-file FAIL stays readable; a new PASS must bind
    # all eight installed files and must not carry failure-only supplemental facts.
    old = fixture.empty_inner_report()
    old['appHashes'] = {name: 'a' * 64 for name in fixture.APP_FILES if name != 'worker_diagnostics.py'}
    fixture.validate_inner_report(old)
    current = {**old, 'status': 'PASS', 'stage': 'complete',
        'checks': dict.fromkeys(fixture.CHECKS, True), 'wheelSha256': 'b' * 64,
        'manifestSha256': 'c' * 64, 'lockSha256': 'd' * 64,
        'workerImports': {name: {'version': '1.0', 'commit': None, 'sha256': 'e' * 64}
                          for name in fixture.IMPORTS}}
    with pytest.raises(AssertionError):
        fixture.validate_inner_report(current)
    current['appHashes'] = {name: 'a' * 64 for name in fixture.APP_FILES}
    fixture.validate_inner_report(current)
    for value in ({'status': 'present', 'facts': facts}, {'status': 'missing', 'facts': None}):
        with pytest.raises(AssertionError):
            fixture.validate_inner_report({**current, 'startupFailureFacts': value})


async def test_default_runtime_probe_worker_observer_write_fault_cannot_become_success(tmp_path, monkeypatch):
    fixture = _default_runtime_probe_module()
    path = tmp_path / 'privately-created-observation.json'
    path.write_text('{}', encoding='utf-8')
    entered, release = asyncio.Event(), asyncio.Event()
    class Worker:
        async def start(self):
            entered.set()
            await release.wait()
            return 7
    attempts = []
    class FaultOnce:
        def open(self, *args, **kwargs):
            attempts.append(True)
            if len(attempts) == 1:
                raise OSError('private observation write failure')
            return path.open(*args, **kwargs)
    monkeypatch.setattr(fixture, 'sys', SimpleNamespace(
        modules={**sys.modules, '__main__': SimpleNamespace(Worker=Worker)},
        getprofile=sys.getprofile, setprofile=sys.setprofile))
    assert sys.getprofile() is None
    owner = Worker()
    fixture.install_worker_start_observer(FaultOnce())
    try:
        owner.start_task = asyncio.create_task(owner.start())
        await asyncio.wait_for(entered.wait(), .5)
        await asyncio.sleep(0)
        assert fixture.read_worker_startup(path)['diagnosticStatus'] == 'FAIL'
        release.set()
        assert await owner.start_task == 7  # Diagnostic fault cannot alter start.
        await asyncio.sleep(0)
        assert fixture.read_worker_startup(path)['diagnosticStatus'] == 'FAIL'
        assert sys.getprofile() is None
    finally:
        sys.setprofile(None)
        if not owner.start_task.done():
            owner.start_task.cancel()
        await asyncio.gather(owner.start_task, return_exceptions=True)


def test_default_runtime_probe_worker_observation_is_bounded_and_closed(tmp_path):
    fixture = _default_runtime_probe_module()
    path = tmp_path / 'privately-created-observation.json'
    valid = {'diagnosticStatus': 'PASS', 'observed': True, 'taskDone': False, 'errors': [],
             'awaiting': [{'source': 'manager_prepare', 'offset': 112}]}
    report = {**fixture.empty_inner_report(), 'workerStartup': valid}
    fixture.validate_inner_report(report)
    assert fixture.parse_inner_output(json.dumps(report)) == report
    for value in ({**valid, 'privatePath': 'private'}, {**valid, 'observed': 123},
                  {**valid, 'awaiting': [{'source': 'private-callee', 'offset': 112}]},
                  {**valid, 'awaiting': valid['awaiting'] * 6},
                  {**valid, 'awaiting': [{'source': 'manager_prepare', 'offset': -1}]}):
        with pytest.raises(AssertionError):
            fixture.validate_inner_report({**report, 'workerStartup': value})
        path.write_text(json.dumps(value), encoding='utf-8')
        assert fixture.read_worker_startup(path)['diagnosticStatus'] == 'FAIL'
    path.write_bytes(b' ' * 4097)
    assert fixture.read_worker_startup(path)['diagnosticStatus'] == 'FAIL'
    path.unlink()
    assert fixture.read_worker_startup(path)['diagnosticStatus'] == 'FAIL'


def test_default_runtime_probe_activation_child_schema_privacy_and_total_bound(tmp_path):
    fixture = _default_runtime_probe_module()
    # Longest permitted labels/numbers and maximum rows must still fit the
    # original private-read bound. No new log channel or output limit is added.
    source = max(fixture.CODE_LABELS, key=len)
    frame = {'source': source, 'offset': 10**6}
    error = {'class': 'NotImplementedError', 'frames': [frame] * 5,
             'errno': -(2**31), 'winerror': 2**32 - 1}
    child = {'module': 'hooks-session-naming', 'taskDone': True, 'cancelled': False,
             'awaiting': [frame] * 3, 'errors': [{**error, 'frames': [frame] * 2}]}
    facts = {'diagnosticStatus': 'PASS', 'observed': True, 'taskDone': True,
             'awaiting': [frame] * 5, 'errors': [error] * 4,
             'activationChildren': [child] * 4, 'activationTruncated': True}
    fixture.validate_worker_startup(facts)
    raw = json.dumps(facts, sort_keys=True)
    assert len(raw.encode()) <= 4096
    path = tmp_path / 'private-observation.json'
    path.write_text(raw, encoding='utf-8')
    assert fixture.read_worker_startup(path) == facts
    report = {**fixture.empty_inner_report(), 'workerStartup': facts}
    fixture.validate_inner_report(report)
    fixture.validate_report({**report, 'schema': 2, 'outerJob': {
        'status': 'confirmed', 'activeProcesses': 0, 'treeEmpty': True,
        'ioFinalized': True, 'timedOut': False, 'outputDiscarded': False}})
    for patch in ({'activationChildren': [child] * 5}, {'activationTruncated': 1},
                  {'activationChildren': [{**child, 'module': 'private-module'}]},
                  {'activationChildren': [{**child, 'source': 'private-url'}]},
                  {'activationChildren': [{**child, 'taskDone': 123}]},
                  {'activationChildren': [{**child, 'cancelled': 'private'}]},
                  {'activationChildren': [{**child, 'awaiting': [frame] * 4}]},
                  {'activationChildren': [{**child, 'errors': [error] * 2}]},
                  {'activationChildren': [{**child, 'errors': [error]}]}):
        value = {**facts, **patch}
        with pytest.raises(AssertionError):
            fixture.validate_worker_startup(value)
        path.write_text(json.dumps(value), encoding='utf-8')
        assert fixture.read_worker_startup(path)['diagnosticStatus'] == 'FAIL'
    for missing in ('activationChildren', 'activationTruncated'):
        with pytest.raises((AssertionError, KeyError)):
            fixture.validate_worker_startup({key: value for key, value in facts.items() if key != missing})


def test_default_runtime_probe_startup_snapshot_schema_is_closed():
    fixture = _default_runtime_probe_module()
    valid = {'diagnosticStatus': 'PASS', 'verifiedWorkerObserved': False,
             'phases': ['runtime-setup', 'bundle-preparation'],
             'projectManifestExists': True, 'lockExists': False, 'venvPythonExists': False}
    report = {**fixture.empty_inner_report(), 'startup': valid}
    fixture.validate_inner_report(report)
    assert fixture.parse_inner_output(json.dumps(report)) == report
    fixture.validate_report({**report, 'schema': 2, 'outerJob': {
        'status': 'confirmed', 'activeProcesses': 0, 'treeEmpty': True,
        'ioFinalized': True, 'timedOut': False, 'outputDiscarded': False}})
    for patch in ({'diagnosticStatus': 'private-exception'}, {'privatePath': 'private'},
                  {'verifiedWorkerObserved': 1234}, {'phases': ['private-phase']},
                  {'phases': ['ready', 'ready']}, {'phases': ['ready'] * 100},
                  {'lockExists': 1}, {'stderr': 'private-log'}):
        with pytest.raises(AssertionError):
            fixture.validate_inner_report({**report, 'startup': {**valid, **patch}})


@pytest.mark.parametrize('fault,checkpoint', [
    (None, 'installed_members'), ('distribution', 'installed_distribution'),
    ('direct-url', 'installed_direct_url'), ('editable', 'installed_direct_url'),
    ('origin', 'installed_origin'), ('payload', 'installed_members'),
    ('metadata-origin', 'installed_members'), ('missing-member', 'installed_members'),
])
def test_default_runtime_probe_installed_audit_checkpoints_do_not_bypass_audit(
        tmp_path, monkeypatch, fault, checkpoint):
    from contextlib import nullcontext
    fixture = _default_runtime_probe_module()
    monkeypatch.setitem(sys.modules, fixture.__name__, fixture)
    host = tmp_path / 'host'
    app, wheel = host / 'amplifier_web', tmp_path / 'candidate.whl'
    members = {name: ('synthetic:' + name).encode() for name in (
        *fixture.APP_FILES, 'runtime_deps/pyproject.toml', 'runtime_deps/compatibility.txt',
        'runtime_deps/features.toml')}
    for name, raw in members.items():
        path = app / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    if fault == 'payload':
        (app / 'runtime.py').write_bytes(b'incorrect installed bytes')
    fake_app = SimpleNamespace(__file__=str(
        (tmp_path / 'foreign/__init__.py') if fault == 'origin' else app / '__init__.py'))
    monkeypatch.setitem(sys.modules, 'amplifier_web', fake_app)
    monkeypatch.setattr(fixture, 'sys', SimpleNamespace(prefix=str(host), modules=sys.modules))

    def distribution(name):
        assert name == 'amplifier-unified'
        if fault == 'distribution':
            raise fixture.metadata.PackageNotFoundError('private missing distribution')
        return SimpleNamespace(
            read_text=lambda _: json.dumps({
                'url': 'file:///wrong.whl' if fault == 'direct-url' else wheel.resolve().as_uri(),
                'dir_info': {'editable': fault == 'editable'}}),
            locate_file=lambda name: tmp_path / name if fault == 'metadata-origin' else host / name)

    def archive_read(name):
        assert name.startswith('amplifier_web/')
        if fault == 'missing-member':
            raise KeyError('private wheel member')
        return members[name.removeprefix('amplifier_web/')]

    monkeypatch.setattr(fixture.metadata, 'distribution', distribution)
    monkeypatch.setattr(fixture.zipfile, 'ZipFile',
                        lambda _: nullcontext(SimpleNamespace(read=archive_read)))
    report = fixture.empty_inner_report()
    if fault:
        with pytest.raises(Exception) as caught:
            fixture.installed_host(wheel, report)
        facts = fixture.failure_facts(caught.value, report['diagnostic']['checkpoint'])
        fixture.validate_diagnostic(facts)
        assert any(frame['source'] == 'cold_installed_host'
                   for frame in facts['errors'][0]['frames'])
    else:
        actual_app, hashes = fixture.installed_host(wheel, report)
        assert actual_app == app
        assert hashes == {name: fixture.digest(members[name]) for name in fixture.APP_FILES}
    assert report['diagnostic']['checkpoint'] == checkpoint
    assert not report['checks']['installedHost']  # Only journey may grant this.


@pytest.mark.parametrize('cloned', [False, True])
def test_default_runtime_probe_diagnostic_code_requires_exact_identity(monkeypatch, cloned):
    from types import FunctionType
    fixture = _default_runtime_probe_module()
    monkeypatch.setitem(sys.modules, fixture.__name__, fixture)
    original = fixture.installed_host
    code = original.__code__.replace(co_filename='different-source.py')
    assert code is not original.__code__ and code == original.__code__
    candidate = (FunctionType(code, original.__globals__, original.__name__,
                              original.__defaults__) if cloned else original)

    def unavailable(_):
        raise ValueError('synthetic metadata failure; do not publish text')

    monkeypatch.setattr(fixture.metadata, 'distribution', unavailable)
    report = fixture.empty_inner_report()
    with pytest.raises(ValueError) as caught:
        candidate(Path('synthetic-candidate.whl'), report)
    facts = fixture.failure_facts(caught.value, 'installed_distribution')
    fixture.validate_diagnostic(facts)
    frames = facts['errors'][0]['frames']
    if cloned:
        assert frames == []  # Structurally equal code is not the loaded function.
    else:
        assert len(frames) == 1 and frames[0]['source'] == 'cold_installed_host'
        assert type(frames[0]['offset']) is int


@pytest.mark.parametrize('raw,checkpoint', [
    (b'', 'parent_output_json'), (b'private non-JSON output', 'parent_output_json'),
    (b'\xff', 'parent_output_json'), (b'[]', 'parent_output_schema'),
    (b'null', 'parent_output_schema'), (b'{"status":"private"}', 'parent_output_schema'),
])
def test_default_runtime_probe_output_failure_is_not_inferred_inner_preflight(raw, checkpoint):
    fixture = _default_runtime_probe_module()
    report = fixture.parse_inner_output(raw)
    fixture.validate_inner_report(report)
    assert report['status'] == 'FAIL'
    assert report['diagnostic']['checkpoint'] == checkpoint
    assert report['diagnostic']['errors']
    assert 'private' not in json.dumps(report)
    valid = fixture.empty_inner_report()
    assert fixture.parse_inner_output(json.dumps(valid)) == valid
    failure = fixture.failure_facts(PermissionError(5, 'private'), 'outer_run')
    assert fixture.parse_inner_output(b'', outer_failure=failure)['diagnostic'] == failure


def test_default_runtime_probe_diagnostic_privacy_bounds_and_schema(monkeypatch):
    fixture = _default_runtime_probe_module()

    class PrivateError(RuntimeError):
        def __str__(self):
            raise AssertionError('must not stringify private exception')

        def __repr__(self):
            raise AssertionError('must not repr private exception')

    error = PrivateError()
    cursor = error
    for _ in range(10):
        cursor.__cause__ = PrivateError()
        cursor = cursor.__cause__
    cursor.__cause__ = error
    facts = fixture.failure_facts(error, 'private_root')
    assert len(facts['errors']) == 4
    assert all(row == {'class': 'other', 'frames': []} for row in facts['errors'])
    fixture.validate_diagnostic(facts)
    bad = [
        {**facts, 'checkpoint': 'private-path'},
        {**facts, 'error': 'private prose'},
        {'checkpoint': 'private_root', 'errors': [{'class': 'PrivateError', 'frames': []}]},
        {'checkpoint': 'private_root', 'errors': [{'class': 'OSError', 'errno': True, 'frames': []}]},
        {'checkpoint': 'private_root', 'errors': [{'class': 'OSError', 'frames': [
            {'source': 'private_symbol', 'offset': 1}]}]},
    ]
    for value in bad:
        with pytest.raises(AssertionError):
            fixture.validate_inner_report({**fixture.empty_inner_report(), 'diagnostic': value})
    # Observation itself can fail. It still cannot escape the outcome path or
    # authorize success; there is no private stringification as a fallback.
    def broken(*args):
        raise PrivateError()
    monkeypatch.setattr(fixture, '_failure_facts', broken)
    fallback = fixture.parse_inner_output(b'')
    fixture.validate_inner_report(fallback)
    assert fallback['status'] == 'FAIL'
    assert fallback['diagnostic'] == {
        'checkpoint': 'parent_output_json', 'errors': [{'class': 'other', 'frames': []}]}


def test_default_runtime_probe_admission_diagnostic_is_closed_and_preserves_unknowns():
    fixture = _default_runtime_probe_module()
    for message, reason in fixture.ADMISSION_REASONS.items():
        facts = fixture.failure_facts(RuntimeError(message), 'input_admission')
        fixture.validate_diagnostic(facts)
        assert facts['errors'][0]['reason'] == reason
        assert message not in json.dumps(facts)
    for args in [('SECRET /private/provider payload',), (), ('Session is not ready', 'SECRET')]:
        facts = fixture.failure_facts(RuntimeError(*args), 'input_admission')
        assert facts['errors'][0]['reason'] == 'other'
        assert 'SECRET' not in json.dumps(facts)
        fixture.validate_diagnostic(facts)
    class Untrusted(RuntimeError):
        def __str__(self):
            raise AssertionError('must not stringify')
    facts = fixture.failure_facts(Untrusted('SECRET'), 'input_admission')
    assert facts['errors'] == [{'class': 'other', 'frames': []}]
    for patch in [{'reason': 'SECRET'}, {'reason': True}, {'reason': 'worker_not_ready', 'class': 'ValueError'}]:
        with pytest.raises(AssertionError):
            fixture.validate_diagnostic({'checkpoint': 'input_admission',
                'errors': [{'class': 'RuntimeError', 'frames': [], **patch}]})


async def test_default_runtime_probe_diagnostic_fault_keeps_failed_outer_owner(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from amplifier_web import windows_jobs
    fixture = _default_runtime_probe_module()
    job = SimpleNamespace(state='cleanup_failed', tree_empty=False, io_finalized=False,
                          output_discarded=False, close=AsyncMock())
    monkeypatch.setattr(windows_jobs.WindowsOwnedProcess, 'start',
                        AsyncMock(side_effect=windows_jobs.CleanupError(job)))
    def broken(*args):
        raise RuntimeError('private observer failure')
    monkeypatch.setattr(fixture, '_failure_facts', broken)
    outcome = await fixture.run_owned_probe(
        ['C:/fixture/python.exe', 'probe.py'], cwd=tmp_path, env={}, scratch=tmp_path)
    assert not outcome['succeeded'] and outcome['tasksSettled']
    assert outcome['outerJob']['status'] == 'unknown'
    assert fixture.FAILED_OUTER_OWNERS == [{'owner': job, 'scratch': tmp_path}]
    job.close.assert_not_awaited()  # Never retry a settled failed native launch.
    fixture.validate_diagnostic(outcome['failure'])
    assert outcome['failure']['errors'] == [{'class': 'other', 'frames': []}]


@pytest.mark.parametrize('mode', ['normal', 'timeout', 'cleanup-unknown', 'io-unknown', 'overflow'])
async def test_default_runtime_probe_outer_job_deadline_and_cleanup(tmp_path, monkeypatch, mode):
    from unittest.mock import AsyncMock
    from amplifier_web import windows_jobs
    fixture = _default_runtime_probe_module()
    class Job:
        def __init__(self):
            self.stdout, self.stderr = asyncio.StreamReader(), asyncio.StreamReader()
            self.stdin = SimpleNamespace(close=lambda: None)
            self.done = asyncio.Event()
            self.tree_empty = self.io_finalized = False
            self.output_discarded = False
            self.closes = 0
            self.state = 'running'
        def finish(self, forced=False):
            self.tree_empty, self.io_finalized = True, mode != 'io-unknown'
            self.output_discarded = forced
            self.stdout.feed_eof()
            self.stderr.feed_eof()
            self.done.set()
        async def wait(self):
            await self.done.wait()
            return windows_jobs.ProcessResult(0, 0, 0, self.output_discarded)
        async def close(self):
            self.closes += 1
            if mode == 'cleanup-unknown':
                self.state = 'cleanup_failed'
                raise windows_jobs.CleanupError(self)
            if not self.done.is_set():
                self.finish(forced=True)
            return await self.wait()
    job = Job()
    job.stdout.feed_data(b'x' * (129 if mode == 'overflow' else 100))
    job.stderr.feed_data(b'private' * 50_000)  # drain, but retain only a bounded tail
    if mode == 'normal':
        job.finish()
    spawn = AsyncMock(return_value=job)
    monkeypatch.setattr(windows_jobs.WindowsOwnedProcess, 'start', spawn)
    scratch = tmp_path / 'retained-scratch'
    scratch.mkdir()
    marker = scratch / 'private.log'
    marker.write_bytes(b'preserve on failure')
    outcome = await asyncio.wait_for(fixture.run_owned_probe(
        ['C:/host/Scripts/python.exe', '-I', 'probe.py'], cwd=tmp_path, env={'SYNTHETIC': '1'},
        scratch=scratch, timeout=.03, output_limit=128), 1)
    assert spawn.await_args.kwargs['trusted_python'] == sys._base_executable
    assert spawn.await_args.kwargs['cleanup_timeout'] == 5
    assert job.closes == 1
    assert len(outcome['stdout']) <= 128 and len(outcome['stderr']) <= 128
    assert outcome['succeeded'] == (mode == 'normal')
    assert (outcome['failure'] is None) == (mode == 'normal')
    if outcome['failure'] is not None:
        fixture.validate_diagnostic(outcome['failure'])
    assert outcome['outerJob']['timedOut'] == (mode in {'timeout', 'cleanup-unknown', 'io-unknown'})
    confirmed = mode in {'normal', 'timeout', 'overflow'}
    assert (outcome['outerJob']['status'] == 'confirmed') == confirmed
    assert marker.read_bytes() == b'preserve on failure'
    if confirmed:
        assert outcome['outerJob']['activeProcesses'] == 0
        assert outcome['outerJob']['treeEmpty'] and outcome['outerJob']['ioFinalized']
        assert not fixture.FAILED_OUTER_OWNERS
    else:
        assert fixture.FAILED_OUTER_OWNERS == [{'owner': job, 'scratch': scratch}]
        assert not outcome['succeeded']
    # No pending wait/read task survives the finite parent operation, even when
    # cleanup proof is absent. Retention above is evidence, not a retry.
    assert outcome['tasksSettled']


async def test_default_runtime_probe_retains_owner_when_start_cleanup_is_unknown(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from amplifier_web import windows_jobs
    fixture = _default_runtime_probe_module()
    job = SimpleNamespace(state='cleanup_failed', tree_empty=False, io_finalized=False,
                          output_discarded=False, close=AsyncMock())
    spawn = AsyncMock(side_effect=windows_jobs.CleanupError(job))
    monkeypatch.setattr(windows_jobs.WindowsOwnedProcess, 'start', spawn)
    outcome = await fixture.run_owned_probe(['C:/python.exe', 'probe.py'],
        cwd=tmp_path, env={}, scratch=tmp_path, timeout=.03)
    assert not outcome['succeeded'] and outcome['tasksSettled']
    assert outcome['outerJob'] == {
        'status': 'unknown', 'timedOut': False, 'activeProcesses': None,
        'treeEmpty': False, 'ioFinalized': False, 'outputDiscarded': False}
    assert fixture.FAILED_OUTER_OWNERS == [{'owner': job, 'scratch': tmp_path}]
    job.close.assert_not_awaited()  # A settled failed native start is not replayed.
    assert outcome['failure']['checkpoint'] == 'outer_run'
    fixture.validate_diagnostic(outcome['failure'])


def test_default_runtime_probe_public_pass_requires_outer_cleanup_proof():
    fixture = _default_runtime_probe_module()
    inner = {'schema': 1, 'status': 'PASS', 'stage': 'complete',
             'checks': dict.fromkeys(fixture.CHECKS, True), 'wheelSha256': 'a' * 64,
             'manifestSha256': 'b' * 64, 'lockSha256': 'c' * 64,
             'appHashes': dict.fromkeys(fixture.APP_FILES, 'd' * 64),
             'workerImports': {name: {'version': '1.0', 'commit': None, 'sha256': 'e' * 64}
                               for name in fixture.IMPORTS}}
    fixture.validate_inner_report(inner)
    with pytest.raises(AssertionError):
        fixture.validate_report(inner)  # child report alone is not outer cleanup
    outer = {'status': 'confirmed', 'timedOut': False, 'activeProcesses': 0,
             'treeEmpty': True, 'ioFinalized': True, 'outputDiscarded': False}
    public = {**inner, 'schema': 2, 'outerJob': outer}
    fixture.validate_report(public)
    for patch in ({'status': 'unknown'}, {'timedOut': True}, {'activeProcesses': 1},
                  {'treeEmpty': False}, {'ioFinalized': False}, {'outputDiscarded': True}):
        with pytest.raises(AssertionError):
            fixture.validate_report({**public, 'outerJob': {**outer, **patch}})


@pytest.mark.parametrize('mismatch', [None, 'version', 'commit', 'ui-use-version', 'ui-use-commit'])
def test_default_runtime_probe_binds_imports_to_real_lock(tmp_path, mismatch):
    fixture = _default_runtime_probe_module()
    rows, imports = [], {}
    for package, distribution in fixture.IMPORTS.items():
        source = 'registry = "https://pypi.org/simple"' if package == 'amplifier_core' else (
            'git = "https://example.invalid/module#' + 'a' * 40 + '"')
        if distribution not in {fixture.IMPORTS[name] for name in imports}:
            rows.append('[[package]]\nname = "' + distribution + '"\nversion = "2.0.2"\nsource = {' + source + '}\n')
        imports[package] = {'version': '2.0.2', 'commit': None if package == 'amplifier_core' else 'a' * 40}
    (tmp_path / 'uv.lock').write_text('\n'.join(rows), encoding='utf-8')
    if mismatch == 'version':
        imports['amplifier_foundation']['version'] = '2.0.3'
    elif mismatch == 'commit':
        imports['amplifier_foundation']['commit'] = 'b' * 40
    elif mismatch == 'ui-use-version':
        imports['amplifier_ui_use']['version'] = '2.0.3'
    elif mismatch == 'ui-use-commit':
        imports['amplifier_module_tool_ui_use']['commit'] = 'b' * 40
    if mismatch:
        with pytest.raises(AssertionError):
            fixture.verify_worker_lock(tmp_path, imports)
    else:
        fixture.verify_worker_lock(tmp_path, imports)


@pytest.mark.skipif(sys.platform != 'win32', reason='native installed default-command preparation')
async def test_native_windows_default_runtime_command_cold_prepare_turn_and_stop(tmp_path):
    """CI setup must provide the installed candidate Python+wheel, not a worker.

    This opt-in node permits public dependency resolution/builds in the dedicated
    GitHub setup job. It does not install anything on the local author host.
    Never upload its private scratch/log files. The optional report is bounded.
    """
    import os
    import shutil
    import uuid
    python = os.environ.get('AMPLIFIER_WINDOWS_DEFAULT_RUNTIME_PYTHON')
    wheel = os.environ.get('AMPLIFIER_WINDOWS_DEFAULT_RUNTIME_WHEEL')
    if not python or not wheel:
        pytest.skip('dedicated installed candidate Python/wheel setup was not supplied')
    assert os.environ.get('GITHUB_ACTIONS') == 'true', 'default graph installation is CI-only'
    python, wheel = Path(python).resolve(strict=True), Path(wheel).resolve(strict=True)
    assert python.is_file() and wheel.suffix == '.whl'
    uv = shutil.which('uv')
    assert uv, 'CI must provision uv before this node'
    # Compact, canonical existing TEMP ancestor avoids accidental MAX_PATH
    # failure from pytest's long test-name directory; no private tree is
    # ordinary-mkdir'ed before the native creation boundary.
    root = Path(tempfile.gettempdir()).resolve() / ('dr-' + uuid.uuid4().hex[:12])
    # Explicitly do not forward provider/GitHub/CI secrets, user Amplifier state,
    # PYTHONPATH, UV_* overrides, proxy values, or the pytest host's venv.
    keep = {'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'PATH', 'TEMP', 'TMP'}
    env = {key: value for key, value in os.environ.items() if key.upper() in keep}
    env.update(GITHUB_ACTIONS='true', PYTHONIOENCODING='utf-8', PYTHONUTF8='1',
               GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               UV_CACHE_DIR=str(root / 'uv-cache'), UV_NO_PROGRESS='1', UV_NO_CONFIG='1')
    fixture = _default_runtime_probe_module()
    outcome = await fixture.run_owned_probe(
        [str(python), '-I', '-B', str(Path(fixture.__file__).resolve()),
         '--root', str(root), '--wheel', str(wheel)], cwd=tmp_path.resolve(),
        env=env, scratch=root, timeout=1200)
    # Do not expose raw subprocess output or private logs in a pytest failure.
    report = fixture.parse_inner_output(
        outcome['stdout'], outer_failure=outcome.get('failure'))
    report = {**report, 'schema': 2, 'outerJob': outcome['outerJob']}
    if not outcome['succeeded']:
        report['status'] = 'FAIL'
    fixture.validate_report(report)
    # Keep any captured failure bytes private; never upload raw logs or remove
    # scratch after uncertain cleanup. A failed writer does not imply cleanup
    # and must not prevent publication of the parent's bounded unknown result.
    if root.is_dir():
        from amplifier_web.windows_files import directory
        try:
            with directory(root, owned_root=root) as pinned:
                pinned.publish_private('outer-stderr.log', outcome['stderr'])
        except OSError:
            report['status'] = 'FAIL'  # No unsafe-file fallback or ACL repair.
    destination = os.environ.get('AMPLIFIER_WINDOWS_DEFAULT_RUNTIME_REPORT')
    if destination:
        # Explicit new report destination only; never replace an older result.
        with Path(destination).open('x', encoding='utf-8') as stream:
            json.dump(report, stream, sort_keys=True)
    assert outcome['succeeded'] and report['status'] == 'PASS', (
        'Native default-command preparation failed at bounded stage ' + report['stage'])
    assert all(report['checks'].values())
