"""Owner-local update waiting across the real host/controller/continuation seams.

Only transport, the native generation/checkpoint signal, and listener health are
synthetic. No updater loop, HTTP listener, installer, provider, or restart runs.
The native goal-loop test is separate: this module proves which saved input is
admitted, and the objective/progress it receives, not another provider turn.
"""
import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web import __version__, update_drain as drain
from amplifier_web.auth import data_identity
from amplifier_web.handoff_boundary import inspect as inspect_boundary
from amplifier_web.runtime_worker import Worker
from amplifier_web.service import AppError, AppService
from amplifier_web.update_restart import target
from amplifier_web.updates import UpdateManager
from test_collaborative_workspaces import agent_action, finish as finish_peer, generation
from test_peer_creation_operation import commission, plan
from test_service import Runtime
from test_task_continuity import controls


REVISION = 'b' * 40
ATTEMPT = 'a' * 32
GENERATION = 'c' * 32
OBJECTIVE = 'Finish the retained report without repeating the completed upload'


class OwnerRuntime(Runtime):
    """In-process transport to real controls and the real worker boundary."""

    def __init__(self):
        super().__init__()
        self.workers = {}
        self.native = {}
        self.controllers = {}
        self.hooks = {}
        self.boundary_calls = []
        self.admission_calls = []
        self.admitted = []
        self.peer_admitted = []
        self.guard_reasons = []
        self.before_admission = None
        self.lose_boundary_ack = False

    def mount(self, sid):
        controller = controls(sid)
        coordinator = controller.coordinator
        coordinator.session_id = sid
        coordinator.loop.pending = []
        coordinator.loop._active_jobs = lambda: []
        hooks = {}

        def register(event, handler, **kwargs):
            hooks[event] = handler
            return lambda: hooks.pop(event, None)

        coordinator.hooks = SimpleNamespace(register=register)
        worker = Worker()
        worker.session = SimpleNamespace(session_id=sid, coordinator=coordinator)
        worker.controls = controller
        worker.runtime = controller.runtime
        worker.runtime.inbox = asyncio.Queue()
        worker.parked = True
        self.native[sid] = worker
        self.controllers[sid] = controller
        self.hooks[sid] = hooks
        self.workers[sid] = {'runtimeOwnerId': 'fixture-owner:' + sid}
        return controller

    async def control(self, sid, operation, args, *, guard=None):
        if guard:
            guard()
        return await self.controllers[sid].perform(operation, args)

    async def handoff_boundary(self, sid, boundary_id, cancel=False, *, update=False, receipt=None):
        self.boundary_calls.append((sid, boundary_id, cancel, update))
        result = await inspect_boundary(self.native[sid], boundary_id, cancel, update=update,
                                        expected=(receipt or {}).get('updateWait'))
        if self.lose_boundary_ack:
            raise TimeoutError('Synthetic acknowledgement lost after native enrollment')
        return result

    async def send(self, session, text, input_id, emit):
        # Ordinary input on an unrelated chat proves staging does not gate it.
        # Do not simulate a native generation on that unrelated chat.
        self.sent.append((session['id'], text, input_id))

    async def collaboration_input(self, session, args, guard, emit):
        self.admission_calls.append(copy.deepcopy(args))
        if self.before_admission:
            await self.before_admission()
        reason = guard()  # Mandatory recheck at the fixture transport boundary.
        self.guard_reasons.append(reason)
        if reason:
            return {'accepted': False, 'delivery': 'not_sent', 'reason': reason}
        admitted = self.app.collaboration.admission(session['id'], args)
        assert admitted['admitted'], admitted
        if session['id'] not in self.controllers:
            # A separately commissioned root has no inherited owner task.
            self.peer_admitted.append((session['id'], args['inputId']))
            return {'accepted': True, 'mode': 'queue'}
        controller = self.controllers[session['id']]
        if not await controller.tasks.continuation_allowed():
            return {'accepted': False, 'delivery': 'not_sent', 'reason': 'Task guard refused'}
        goal = controller.coordinator.session_state['goal']
        self.admitted.append({
            'sessionId': session['id'], 'inputId': args['inputId'],
            'task': copy.deepcopy(controller.tasks.record()), 'goal': copy.deepcopy(goal),
        })
        await emit('assistant.message', {
            'sessionId': session['id'], 'inputId': args['inputId'],
            'text': 'Fixture continuation received the saved objective: ' + goal['condition'],
        })
        return {'accepted': True, 'mode': 'queue'}

    async def close(self):
        for controller in self.controllers.values():
            await controller.close()


class UpdateWait:
    def __init__(self, tmp_path):
        self.workspace = tmp_path
        self.home = tmp_path / 'app'
        self.app = None
        self.sid = None
        self.wait_args = None
        self.release = {'version': __version__, 'revision': REVISION,
                        'attemptId': ATTEMPT, 'generation': GENERATION}

    def open(self):
        self.runtime = OwnerRuntime()
        self.app = AppService(self.home, self.runtime, workspace=self.workspace)
        self.runtime.app = self.app
        self.app.management = SimpleNamespace(
            ensure_runtime=AsyncMock(), unresolved_configuration=lambda session: False,
            command=AsyncMock(side_effect=AssertionError('Unexpected management command in update-wait fixture')),
            pending_config_tasks={}, lock=asyncio.Lock(),
            notifications=SimpleNamespace(send=AsyncMock()))
        self.manager = UpdateManager(self.app)
        self.app.update_manager = self.manager
        self.manager.running_identity = {
            'version': __version__, 'revision': 'd' * 40, 'instanceId': 'old-fixture-process'}

    async def close(self):
        if self.app and not self.app.closed:
            self.app.management = None  # The fixture has no real setup managers.
            await self.app.close()

    async def new_task(self, title, *, paused=False, full_access=False):
        await self.app.dispatch('session.create', {'title': title})
        sid = self.app._session()['id']
        if full_access:
            await self.app.dispatch('access.set', {
                'sessionId': sid, 'scope': 'session', 'mode': 'full',
                'expectedRevision': self.app.access_mode.snapshot(self.app._session(sid))['revision'],
            }, command_id='fixture-full-access', include_state=False)
        controller = self.runtime.mount(sid)
        await self.app.dispatch('task.create', {
            'sessionId': sid, 'expectedRevision': 0, 'objective': OBJECTIVE,
            'maxTurns': 8, 'constraints': ['Preserve original files and used budget'],
            'artifactRefs': ['completed-upload-receipt'],
        }, command_id='create:' + sid, include_state=False)
        controller.coordinator.session_state['goal'].update(turns_used=3, continuations=2)
        controller.persist(task_only=True)
        if paused:
            await self.app.dispatch('task.pause', {'sessionId': sid, 'expectedRevision': 1},
                                    command_id='pause:' + sid, include_state=False)
        else:
            self.runtime.native[sid].runtime.generation = {'id': 'original-generation:' + sid}
            self.runtime.native[sid].parked = False
            self.app._session(sid).update(
                status='working', collaborationGeneration={
                    'id': 'original-generation:' + sid, 'terminal': False})
            self.app._message(self.app._session(sid), 'user', 'Original accepted work',
                              inputId='original:' + sid, inputOrigin='ui',
                              delivery={'status': 'accepted'})
        self.app._publish_changes(sessions={sid})
        return sid

    def stage(self):
        self.manager.diagnostics.begin('application', REVISION, ATTEMPT)
        state = self.app.state['updates']
        state.update(phase='app-staged', pendingApp=copy.deepcopy(self.release),
                     pendingRestart=None, pendingReplacement=None)
        state['application']['canInstall'] = True
        self.wait_args = {'sessionId': self.sid, 'expectedRevision': 1,
                          'target': target(self.manager)}
        assert self.wait_args['target'] is not None
        self.app._publish_changes(globals={'updates'})

    async def wait(self, *, args=None, command='owner-wait', caller=None):
        from amplifier_web.collaboration import BINDING, PRINCIPAL
        session = self.app._session(self.sid)
        principal = PRINCIPAL.set(self.sid)
        binding = BINDING.set({
            '_generationId': session.get('collaborationGeneration', {}).get('id'),
            '_runtimeOwnerId': (self.runtime.workers.get(self.sid) or {}).get('runtimeOwnerId')})
        try:
            return await self.app.dispatch('task.waitForUpdate', copy.deepcopy(args or self.wait_args),
                origin='agent', caller_session_id=caller or self.sid,
                command_id=command, include_state=False)
        finally:
            BINDING.reset(binding)
            PRINCIPAL.reset(principal)

    @property
    def row(self):
        return self.app.state['updates']['drain']['workers'][self.sid]

    @property
    def controller(self):
        return self.runtime.controllers[self.sid]

    async def yield_turn(self, *, park=True):
        worker = self.runtime.native[self.sid]
        before = worker.session.coordinator.session_state['goal']
        await self.runtime.hooks[self.sid]['orchestrator:goal_progress'](
            'orchestrator:goal_progress', {
                'state': 'continuing', 'session_id': self.sid,
                'condition': before['condition'], 'turn': before['turns_used'],
            })
        after = worker.session.coordinator.session_state['goal']
        assert after == before and after is not before
        worker.runtime.generation = None
        worker.parked = park
        session = self.app._session(self.sid)
        session['collaborationGeneration']['terminal'] = True
        await self.app.on_runtime_event('runtime.status', {'sessionId': self.sid, 'status': 'idle'})

    async def ready(self):
        await self.wait()
        await self.yield_turn()
        assert await drain.prepare(self.manager, self.release)
        assert self.row['ready'] and self.row['yieldedGoal']['turns_used'] == 3

    def replace(self):
        drain.begin_replacement(self.manager, self.release)
        self.restart_target = {**self.release,
            'sourceInstanceId': self.manager.running_identity['instanceId'], 'requestedAt': 1.0}
        self.app.state['updates'].update(phase='activating',
            pendingRestart=copy.deepcopy(self.restart_target))
        self.old_instance = self.app.instance_id
        self.app._publish_changes(globals={'updates'})

    async def reconnect(self, *, successor=False):
        ids = list(self.runtime.controllers)
        await self.settle()
        self.app._save_full(reason='Synthetic owner update reconnect fixture')
        await self.close()
        self.open()
        for sid in ids:
            controller = self.runtime.mount(sid)
            await controller.restore()
        # A new transport has no custody of the retired process's workers.
        self.runtime.workers.clear()
        if successor:
            self.manager.running_identity = {
                'version': self.release['version'], 'revision': self.release['revision'],
                'instanceId': 'new-fixture-process'}
            assert self.app.instance_id != self.old_instance

    def health(self, **changes):
        return {'ok': True, 'app': 'amplifier-unified',
                'dataIdentity': data_identity(self.home), **self.manager.running_identity, **changes}

    async def confirm(self):
        assert await self.manager.confirm_readiness(self.health(), expected=self.restart_target)
        state = self.app.state['updates']
        assert state['applicationResult']['phase'] == 'restart-ack'
        assert state['applicationResult']['attemptId'] == self.restart_target['attemptId']
        assert state['applicationResult']['revision'] == self.restart_target['revision']
        assert not any(state.get(key) for key in ('pendingApp', 'pendingRestart', 'pendingReplacement'))

    async def settle(self):
        # Await host-owned tasks rather than a timing-based observation loop.
        async with asyncio.timeout(5):
            while self.app.tasks:
                results = await asyncio.gather(*list(self.app.tasks), return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        raise result

    async def finish(self):
        await drain.finish(self.manager, 'installed')
        await self.app.collaboration.drain(self.sid)
        await self.settle()

    async def commission_child(self):
        source = self.app._session(self.sid)
        # Bind the already accepted owner input through real lifecycle events.
        await generation(self.app, source, source['collaborationGeneration']['id'],
                         ['original:' + self.sid])
        plan(self.app, source)
        created = await commission(self.app, source, 'admitted-child')
        await self.settle()
        request = self.app.collaboration.receipt(created['initialInputId'])
        assert request['delivery'] == 'accepted'
        assert self.runtime.peer_admitted == [(created['sessionId'], created['initialInputId'])]
        child = self.app._session(created['sessionId'])
        await generation(self.app, child, 'recipient-' + created['initialInputId'],
                         [created['initialInputId']])
        return created

    async def report_child(self, created):
        child = self.app._session(created['sessionId'])
        args = {'requestId': created['initialInputId'], 'kind': 'result', 'outcome': 'success',
                'text': 'Original admitted child retained its checked candidate',
                'references': ['urn:fixture:checked-child-candidate']}
        reply = await agent_action(self.app, child, 'coordination.reply', args, 'child-original-reply')
        assert reply['result']['status'] == 'staged'
        assert (await agent_action(self.app, child, 'coordination.reply', args,
                                   'child-original-reply'))['duplicate']
        await finish_peer(self.app, child, created['initialInputId'])
        await finish_peer(self.app, child, created['initialInputId'])
        await self.app.on_runtime_event('runtime.status', {'sessionId': child['id'], 'status': 'idle'})
        await self.settle()
        request = self.app.collaboration.receipt(created['initialInputId'])
        assert request['response']['qualified'] is True
        assert request['responseRevision'] == 1
        return request

    def receipts(self, action):
        return [json.loads(row[0]) for row in self.app.db.execute(
            "SELECT receipt FROM commands WHERE json_extract(receipt,'$.commandAction')=?", (action,))]

    async def change_task(self, change):
        task = self.controller.tasks.record()
        args = {'sessionId': self.sid, 'expectedRevision': task['revision']}
        if change == 'stop':
            await self.app.dispatch('conversation.stop', {'sessionId': self.sid},
                                    command_id='human-stop', include_state=False)
            await self.settle()
            return
        operation = {'paused': 'task.pause', 'completed': 'task.complete',
                     'replaced': 'task.complete', 'revised': 'task.update'}[change]
        if change in {'completed', 'replaced'}:
            args.update(condition=task['objective'], evidence=['Synthetic completed check'])
        if change == 'revised':
            args.update(correction='Retain the new user constraint before any continuation')
        await self.app.dispatch(operation, args, command_id='human-' + change, include_state=False)
        if change == 'replaced':
            await self.app.dispatch('task.create', {
                'sessionId': self.sid, 'expectedRevision': self.controller.tasks.record()['revision'],
                'objective': 'A different explicitly accepted task', 'maxTurns': 2,
            }, command_id='replacement-task', include_state=False)


@pytest.fixture
async def waiting(tmp_path, monkeypatch, request):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'app'))
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'native'))
    harness = UpdateWait(tmp_path)
    harness.open()
    try:
        harness.sid = await harness.new_task('Owner waiting for this update',
                                            full_access=getattr(request, 'param', None) == 'full')
        harness.stage()
        yield harness
    finally:
        await harness.close()


async def test_owner_wait_yields_restores_and_continues_exactly_once_without_resuming_paused_predecessor(waiting):
    h = waiting
    paused = await h.new_task('Previously paused objective', paused=True)
    paused_before = copy.deepcopy(h.runtime.controllers[paused].tasks.record())
    original_task = copy.deepcopy(h.controller.tasks.record())
    original_capacity = copy.deepcopy(h.controller.capacity.policy)
    result = await h.wait()
    assert result['accepted'] and result['result']['status'] == 'waiting'
    assert h.app.state['updates']['drain']['phase'] == 'waiting'
    assert set(h.app.state['updates']['drain']['workers']) == {h.sid}
    assert not drain.held(h.app)
    assert h.controller.tasks.record()['status'] == 'active'
    assert h.runtime.boundary_calls == [(h.sid, h.row['boundaryId'], False, True)]
    assert 'generation' in h.row['waitingFor']
    assert not await drain.prepare(h.manager, h.release)

    # Waiting is owner-local. Unrelated ordinary input still reaches transport.
    await h.app.dispatch('session.create', {'title': 'Independent conversation'})
    independent = h.app._session()['id']
    await h.app.dispatch('conversation.send', {'sessionId': independent, 'text': 'Unrelated work'},
                         command_id='independent-input', include_state=False)
    await h.settle()
    assert h.runtime.sent == [(independent, 'Unrelated work', 'independent-input')]
    h.app._session(independent)['status'] = 'idle'  # Synthetic transport settlement.

    # An idle host status without a positive native checkpoint is insufficient.
    await h.yield_turn(park=False)
    assert not await drain.prepare(h.manager, h.release)
    assert 'checkpoint' in h.row['waitingFor']
    h.runtime.native[h.sid].parked = True
    assert await drain.prepare(h.manager, h.release)
    assert h.row['ready']
    yielded = copy.deepcopy(h.row)
    h.replace()
    assert drain.held(h.app)
    assert h.row == yielded
    await h.reconnect(successor=True)
    assert h.row == yielded  # Retained enrollment, not a new boundary or task.
    assert not h.runtime.admitted
    await h.confirm()
    await h.finish()

    assert len(h.runtime.admitted) == 1
    continuation = h.runtime.admitted[0]
    assert continuation['sessionId'] == h.sid
    assert continuation['inputId'] != 'original:' + h.sid
    assert continuation['task']['id'] == original_task['id']
    for key in ('objective', 'maxTurns', 'constraints', 'artifactRefs', 'revision', 'continuationEpoch'):
        assert continuation['task'][key] == original_task[key]
    assert continuation['goal']['condition'] == OBJECTIVE
    assert (continuation['goal']['cap'], continuation['goal']['turns_used'],
            continuation['goal']['continuations']) == (8, 3, 2)
    assert h.controller.capacity.policy == original_capacity
    assert h.app.state['updates']['drain']['phase'] == 'released'
    assert h.runtime.controllers[paused].tasks.record()['status'] == 'paused'
    assert h.runtime.controllers[paused].tasks.record()['goalState'] == paused_before['goalState']
    assert h.runtime.controllers[paused].coordinator.session_state['goal'] is None
    messages = h.app._session(h.sid)['messages']
    assert sum(m.get('inputId') == 'original:' + h.sid for m in messages) == 1
    assert sum(m['role'] == 'assistant' and m.get('inputId') == continuation['inputId'] for m in messages) == 1
    assert not h.runtime.sent
    assert len(h.receipts('updates.app')) == len(h.receipts('coordination.send')) == 1
    delivered = h.receipts('coordination.send')[0]
    assert delivered['delivery'] == 'accepted'
    assert delivered['inputId'] == continuation['inputId']
    assert h.runtime.guard_reasons == [None]

    # Duplicate command reconciliation and a second reconnect do not replay input.
    assert (await h.wait())['duplicate']
    await h.finish()
    assert len(h.runtime.admitted) == 1
    await h.reconnect(successor=True)
    assert (await h.wait())['duplicate']
    await h.finish()
    assert not h.runtime.admitted and not h.runtime.sent
    assert len(h.receipts('coordination.send')) == 1
    saved_task = (await h.app.dispatch('task.get', {'sessionId': h.sid}))['result']['task']
    assert saved_task['id'] == original_task['id'] and saved_task['status'] == 'active'
    assert saved_task['objective'] == OBJECTIVE


async def test_original_admitted_child_return_waits_for_adoption_and_is_the_only_owner_input(waiting):
    h = waiting
    created = await h.commission_child()
    source_task = copy.deepcopy(h.controller.tasks.record())
    original = created['initialInputId']
    returned = created['subscription']['continuationId']
    creation = h.app.collaboration.receipt(created['requestId'])
    assert h.app.collaboration.creation_guard(creation, for_reply=True) is None

    await h.wait()
    for key in ('id', 'revision', 'continuationEpoch'):
        assert h.controller.tasks.record()[key] == source_task[key]
    assert h.app.collaboration.creation_guard(creation, for_reply=True) is None
    assert h.app.collaboration.guard(h.app.collaboration.receipt(original), for_reply=True) is None
    request = await h.report_child(created)
    assert request['subscription']['lastClaimedRevision'] == 1
    assert h.app.collaboration.receipt(returned)['delivery'] == 'queued'
    assert drain.owner_waiting(h.app, h.sid) and not drain.held(h.app)
    assert h.app.collaboration.pending_continuation(h.sid) is None
    await h.app.collaboration.drain(h.sid)
    await h.settle()
    assert not h.runtime.admitted
    assert h.runtime.peer_admitted == [(created['sessionId'], original)]
    assert drain.latest_input(h.app._session(h.sid), service=h.app) == 'original:' + h.sid

    await h.yield_turn()
    assert await drain.prepare(h.manager, h.release)
    h.replace()
    await h.reconnect(successor=True)
    assert h.app.collaboration.receipt(original)['delivery'] == 'accepted'
    assert h.app.collaboration.receipt(returned)['delivery'] == 'queued'
    assert not h.runtime.peer_admitted and not h.runtime.admitted
    await h.confirm()
    assert h.app.collaboration.creation_guard(
        h.app.collaboration.receipt(created['requestId']), for_reply=True) is None
    await h.finish()
    assert [row['inputId'] for row in h.runtime.admitted] == [returned]
    resumed = h.runtime.admitted[0]
    assert resumed['task']['id'] == source_task['id']
    assert resumed['task']['continuationEpoch'] == source_task['continuationEpoch']
    assert resumed['goal']['condition'] == OBJECTIVE
    assert (resumed['goal']['cap'], resumed['goal']['turns_used']) == (8, 3)
    assert h.app.collaboration.receipt(returned)['delivery'] == 'accepted'
    assert not h.receipts('updates.app')  # The queued original return replaces an extra wakeup.
    public = (await h.app.dispatch('coordination.result', {'requestId': created['requestId']},
        origin='agent', caller_session_id=h.sid, include_state=False))['result']
    assert public['qualified'] and public['initialRequest']['requestId'] == original
    assert public['initialRequest']['response']['references'] == ['urn:fixture:checked-child-candidate']
    assert len(h.receipts('coordination.create')) == len(h.receipts('coordination.reply')) == 1
    assert {row['requestId'] for row in h.receipts('coordination.send')} == {original, returned}
    assert not h.runtime.peer_admitted and not h.runtime.sent

    await h.finish()
    assert [row['inputId'] for row in h.runtime.admitted] == [returned]
    await h.reconnect(successor=True)
    await h.app.collaboration.drain(created['sessionId'])
    await h.finish()
    assert not h.runtime.admitted and not h.runtime.peer_admitted and not h.runtime.sent
    for sid, identity in ((h.sid, returned), (created['sessionId'], original)):
        assert sum(m.get('inputId') == identity and m['role'] == 'user'
                   for m in h.app._session(sid)['messages']) == 1


@pytest.mark.parametrize('changed', ['stop', 'replaced'])
async def test_update_wait_does_not_relax_original_child_return_authority(waiting, changed):
    h = waiting
    created = await h.commission_child()
    await h.wait()
    await h.yield_turn()
    await h.change_task(changed)
    creation = h.app.collaboration.receipt(created['requestId'])
    assert h.app.collaboration.creation_guard(creation, for_reply=True)
    assert h.app.collaboration.guard(
        h.app.collaboration.receipt(created['initialInputId']), for_reply=True)
    # Its original admitted generation can retain evidence, not revive its owner.
    await h.report_child(created)
    assert not h.runtime.admitted
    h.replace()
    await h.reconnect(successor=True)
    await h.confirm()
    await h.finish()
    assert not h.runtime.admitted and not h.runtime.peer_admitted
    assert not h.receipts('updates.app')
    assert len(h.receipts('coordination.create')) == len(h.receipts('coordination.reply')) == 1
    assert h.app.collaboration.receipt(created['initialInputId'])['response']['qualified']
    returns = [row for row in h.receipts('coordination.send')
               if row.get('dependencyRequestId') == created['initialInputId']]
    assert len(returns) <= 1
    assert all(row['delivery'] == 'suppressed' for row in returns)
    assert h.app.collaboration.creation_guard(
        h.app.collaboration.receipt(created['requestId']), for_reply=True)


@pytest.mark.parametrize('waiting', ['full'], indirect=True)
async def test_revoked_full_access_never_reuses_the_saved_update_wait(waiting):
    h = waiting
    await h.ready()
    saved_access = h.row['accessRevision']
    assert h.app.access_mode.full(h.app._session(h.sid))
    result = await h.app.dispatch('access.set', {
        'sessionId': h.sid, 'scope': 'session', 'mode': 'ask',
        'expectedRevision': saved_access,
    }, command_id='human-revokes-full-access', include_state=False)
    assert result['result']['mode'] == 'ask'
    assert result['result']['revision'] != saved_access
    h.replace()
    await h.reconnect(successor=True)
    await h.confirm()
    await h.finish()
    assert h.app.access_mode.snapshot(h.app._session(h.sid))['mode'] == 'ask'
    assert not h.runtime.admitted and not h.receipts('updates.app')
    assert h.controller.tasks.record()['status'] == 'active'
    assert h.controller.tasks.record()['goalState']['turns_used'] == 3


@pytest.mark.parametrize('changed', [
    'hostInstanceId', 'attemptId', 'revision', 'generation', 'task-revision',
    'not-working', 'missing-owner', 'paused', 'completed', 'other-caller',
])
async def test_wait_rejects_stale_target_or_nonowning_inactive_task_before_native_effect(waiting, changed):
    h = waiting
    args = copy.deepcopy(h.wait_args)
    if changed in {'hostInstanceId', 'attemptId', 'revision', 'generation'}:
        args['target'][changed] = 'e' * len(args['target'][changed])
    elif changed == 'task-revision':
        args['expectedRevision'] += 1
    elif changed == 'not-working':
        h.app._session(h.sid)['status'] = 'idle'
    elif changed == 'missing-owner':
        h.runtime.workers[h.sid].pop('runtimeOwnerId')
    elif changed in {'paused', 'completed'}:
        await h.change_task(changed)
        args['expectedRevision'] = h.controller.tasks.record()['revision']
    before = copy.deepcopy(h.controller.tasks.record())
    with pytest.raises(AppError):
        await h.wait(args=args, caller='different-caller' if changed == 'other-caller' else None)
    assert not h.runtime.boundary_calls
    assert not h.app.state['updates'].get('drain')
    assert not h.receipts('task.waitForUpdate')
    assert h.controller.tasks.record() == before


async def test_duplicate_wait_is_one_native_enrollment_and_different_payload_cannot_reuse_id(waiting):
    h = waiting
    await h.wait()
    saved = copy.deepcopy(h.row)
    assert (await h.wait())['duplicate']
    assert len(h.runtime.boundary_calls) == 1
    with pytest.raises(AppError, match='different contents'):
        await h.wait(args={**h.wait_args, 'expectedRevision': 2})
    with pytest.raises(AppError, match='already has a saved update wait'):
        await h.wait(command='different-command-for-same-owner')
    assert h.row == saved
    assert len(h.runtime.boundary_calls) == 1


async def test_unknown_native_enrollment_is_retained_and_never_replayed(waiting):
    h = waiting
    h.runtime.lose_boundary_ack = True
    with pytest.raises(TimeoutError, match='acknowledgement lost'):
        await h.wait()
    assert h.runtime.native[h.sid]._handoff_boundary is not None
    assert h.row['unknown'] and h.row['waitingFor'] == ['boundary-outcome-unknown']
    h.runtime.lose_boundary_ack = False
    assert (await h.wait())['duplicate']
    assert not await drain.prepare(h.manager, h.release)
    assert len(h.runtime.boundary_calls) == 1
    assert not h.runtime.admitted and not h.receipts('updates.app')


async def supersede_wait_with_human_input(h):
    worker = h.runtime.native[h.sid]
    worker.runtime.generation = None
    worker.parked = True
    h.app._session(h.sid).update(status='idle')
    h.app._session(h.sid)['collaborationGeneration']['terminal'] = True
    await h.app.dispatch('conversation.send', {'sessionId': h.sid, 'text': 'New direction',
        'allowNextTurn': True}, command_id='fresh-human', include_state=False)
    await h.settle()
    worker.runtime.generation = {'id': 'replacement-generation:' + h.sid}
    worker.parked = False
    await generation(h.app, h.app._session(h.sid), worker.runtime.generation['id'], ['fresh-human'])


async def enroll_other_owner(h):
    original = h.sid
    h.sid = await h.new_task('Independent owner waiting for the same update')
    try:
        await h.wait(args={**h.wait_args, 'sessionId': h.sid}, command='other-owner-wait')
        return h.sid
    finally:
        h.sid = original


@pytest.mark.parametrize('already_suppressed', [False, True])
async def test_confirmed_superseded_wait_allows_fresh_wait_while_other_owner_waits(waiting, already_suppressed):
    h = waiting
    await h.wait()
    original = copy.deepcopy(h.row)
    task = copy.deepcopy(h.controller.tasks.record())
    other = await enroll_other_owner(h)
    other_boundary = h.app.state['updates']['drain']['workers'][other]['boundaryId']
    await supersede_wait_with_human_input(h)
    if already_suppressed:
        # A previous host kept superseded receipts in the active workers map.
        h.row['suppressed'] = 'New input already continued this conversation.'
        drain.save_wait(h.app, h.row, 'suppressed')
    await drain.observe_waiters(h.manager)
    result = await h.wait(command='fresh-owner-wait')
    assert result['accepted'] and not result.get('duplicate')
    assert h.row['boundaryId'] != original['boundaryId']
    assert h.app.state['updates']['drain']['phase'] == 'waiting'
    assert h.app.state['updates']['drain']['workers'][other]['boundaryId'] == other_boundary
    assert h.controller.tasks.record() == task
    old_receipt = (await h.wait())['result']
    assert old_receipt['status'] == 'suppressed' and old_receipt['boundary']['suppressed']
    assert old_receipt['boundary']['boundaryId'] == original['boundaryId']
    assert old_receipt['admissionStatus'] == 'acknowledged'
    assert [call for call in h.runtime.boundary_calls if call[2]] == [
        (h.sid, original['boundaryId'], True, False)]
    assert (await h.wait(command='fresh-owner-wait'))['duplicate']
    await h.yield_turn()
    first = h.sid
    h.sid = other
    await h.yield_turn()
    h.sid = first
    assert await drain.prepare(h.manager, h.release)
    h.replace()
    await h.reconnect(successor=True)
    await h.confirm()
    await h.finish()
    await h.finish()
    assert [row['sessionId'] for row in h.runtime.admitted].count(first) == 1
    assert [row['sessionId'] for row in h.runtime.admitted].count(other) == 1
    assert all(row['goal']['turns_used'] == 3 for row in h.runtime.admitted)
    assert h.controller.tasks.record()['id'] == task['id']
    assert (await h.wait())['result'] == old_receipt


@pytest.mark.parametrize('acknowledgement', ['lost', 'missing'])
async def test_superseded_wait_requires_positive_cancellation_before_reenrollment(waiting, acknowledgement):
    h = waiting
    await h.wait()
    original = copy.deepcopy(h.row)
    await enroll_other_owner(h)
    await supersede_wait_with_human_input(h)
    if acknowledgement == 'lost':
        h.runtime.lose_boundary_ack = True
    else:
        h.runtime.handoff_boundary = AsyncMock(return_value={'ready': True})
    with pytest.raises((TimeoutError, RuntimeError)):
        await drain.observe_waiters(h.manager)
    assert h.row == original
    with pytest.raises(AppError, match='already has a saved update wait'):
        await h.wait(command='fresh-owner-wait')
    assert len(h.receipts('task.waitForUpdate')) == 2
    assert not h.runtime.admitted


@pytest.mark.parametrize('acknowledgement', ['cancelled', 'lost', 'missing'])
async def test_late_cancel_observer_cannot_remove_new_wait_or_rewrite_old_receipt(waiting, acknowledgement):
    h = waiting
    await h.wait()
    old_boundary = h.row['boundaryId']
    other = await enroll_other_owner(h)
    other_boundary = h.app.state['updates']['drain']['workers'][other]['boundaryId']
    await supersede_wait_with_human_input(h)
    original = h.runtime.handoff_boundary
    reached, release = asyncio.Event(), asyncio.Event()

    async def delayed(*args, **kwargs):
        result = await original(*args, **kwargs)
        if kwargs.get('cancel') and not reached.is_set():
            reached.set()
            await release.wait()
            if acknowledgement == 'lost':
                raise TimeoutError('Old cancellation acknowledgement arrived too late')
            if acknowledgement == 'missing':
                return {'ready': True}
        return result

    h.runtime.handoff_boundary = delayed
    late = asyncio.create_task(drain.observe_waiters(h.manager))
    try:
        await asyncio.wait_for(reached.wait(), 5)
        await drain.observe_waiters(h.manager)
        receipt = copy.deepcopy((await h.wait())['result'])
        await h.wait(command='fresh-owner-wait')
        new_boundary = h.row['boundaryId']
        assert new_boundary != old_boundary
        release.set()
        await asyncio.wait_for(late, 5)
        assert h.row['boundaryId'] == new_boundary and not h.row.get('suppressed')
        assert h.app.state['updates']['drain']['phase'] == 'waiting'
        assert h.app.state['updates']['drain']['workers'][other]['boundaryId'] == other_boundary
        assert (await h.wait())['result'] == receipt
    finally:
        release.set()
        await late


async def test_goal_exhausted_at_yield_keeps_its_used_allowance_without_continuation(waiting):
    h = waiting
    await h.wait()
    h.controller.coordinator.session_state['goal']['turns_used'] = 8
    await h.yield_turn()
    assert await drain.prepare(h.manager, h.release)
    assert h.row['yieldedGoal']['turns_used'] == h.row['yieldedGoal']['cap'] == 8
    h.replace()
    await h.reconnect(successor=True)
    await h.confirm()
    await h.finish()
    assert not h.runtime.admitted and not h.receipts('updates.app')
    assert h.controller.tasks.record()['maxTurns'] == 8
    assert h.controller.tasks.record()['goalState']['turns_used'] == 8


@pytest.mark.parametrize('changed', ['stop', 'paused', 'completed', 'replaced', 'revised'])
async def test_changed_task_or_stop_after_yield_never_auto_resumes_on_healthy_update(waiting, changed):
    h = waiting
    await h.ready()
    await h.change_task(changed)
    expected = copy.deepcopy(h.controller.tasks.record())
    interruption = h.app._session(h.sid).get('interruptionRevision', 0)
    h.replace()
    await h.reconnect(successor=True)
    await h.confirm()
    await h.finish()
    assert not h.runtime.admitted and not h.runtime.sent
    assert not h.receipts('updates.app')
    current = h.controller.tasks.record()
    for key in ('id', 'objective', 'status', 'revision', 'maxTurns', 'continuationEpoch'):
        assert current[key] == expected[key]
    assert h.app._session(h.sid).get('interruptionRevision', 0) == interruption
    if changed == 'paused':
        assert h.controller.coordinator.session_state['goal'] is None


@pytest.mark.parametrize('changed', ['version', 'revision', 'attempt', 'same-process'])
async def test_unconfirmed_successor_retains_drain_and_no_continuation(waiting, changed):
    h = waiting
    await h.ready()
    h.replace()
    await h.reconnect(successor=True)
    health = h.health()
    if changed == 'version':
        health['version'] = '0.0.0'
    elif changed == 'revision':
        health['revision'] = 'e' * 40
    elif changed == 'attempt':
        h.app.state['updates']['pendingRestart']['attemptId'] = 'e' * 32
    else:
        h.manager.running_identity['instanceId'] = h.restart_target['sourceInstanceId']
        health = h.health()
    assert not await h.manager.confirm_readiness(health, expected=h.restart_target)
    await h.finish()
    assert not h.runtime.admitted and not h.receipts('updates.app')
    assert h.app.state['updates']['drain']['phase'] == 'replacing'
    assert h.app.state['updates']['pendingRestart']


@pytest.mark.parametrize('changed', ['attemptId', 'revision', 'generation'])
async def test_different_replacement_retains_waiter_evidence_without_continuing_it(waiting, changed):
    h = waiting
    await h.ready()
    retained = copy.deepcopy(h.row)
    release = copy.deepcopy(h.release)
    release[changed] = 'e' * len(release[changed])
    if changed == 'attemptId':
        h.manager.diagnostics.begin('application', REVISION, release['attemptId'])
    h.release = release
    h.replace()
    assert h.row == retained
    assert h.row['updateTarget'] != h.app.state['updates']['drain']['target']
    await h.reconnect(successor=True)
    await h.confirm()
    await h.finish()
    assert h.row['suppressed']
    assert {k: v for k, v in h.row.items() if k != 'suppressed'} == retained
    assert not drain.owner_waiting(h.app, h.sid)
    assert h.app.state['updates']['drain']['phase'] == 'released'
    assert not h.runtime.admitted and not h.receipts('updates.app')


@pytest.mark.parametrize('changed', [
    'stop', 'paused', 'completed', 'replaced', 'revised', 'drain',
    'pendingApp', 'pendingRestart', 'pendingReplacement', 'unknown-boundary',
    'result-phase', 'result-attempt', 'result-revision', 'running-version',
    'running-revision', 'same-host', 'access-revision',
])
async def test_queued_continuation_rechecks_exact_update_and_task_at_transport_admission(waiting, changed):
    h = waiting
    await h.ready()
    h.replace()
    await h.reconnect(successor=True)
    await h.confirm()

    async def invalidate():
        state = h.app.state['updates']
        if changed in {'stop', 'paused', 'completed', 'replaced', 'revised'}:
            # Stop's ordinary background settlement must not await this very
            # callback; its admission fence is the dispatch's synchronous part.
            if changed == 'stop':
                await h.app.dispatch('conversation.stop', {'sessionId': h.sid},
                                     command_id='late-human-stop', include_state=False)
            else:
                await h.change_task(changed)
        elif changed == 'drain':
            state['drain']['phase'] = 'replacing'
        elif changed.startswith('pending'):
            state[changed] = {'phase': 'unknown'}
        elif changed == 'unknown-boundary':
            # The guard follows the retained proof, not a newly manufactured wait.
            request = h.receipts('updates.app')[0]
            request['updateYield']['unknown'] = True
            h.app.collaboration.save(request['requestId'], request)
            h.app.db.commit()
        elif changed == 'result-phase':
            state['applicationResult']['phase'] = 'restart-reconcile'
        elif changed == 'result-attempt':
            state['applicationResult']['attemptId'] = 'e' * 32
        elif changed == 'result-revision':
            state['applicationResult']['revision'] = 'e' * 40
        elif changed == 'running-version':
            h.manager.running_identity['version'] = '0.0.0'
        elif changed == 'running-revision':
            h.manager.running_identity['revision'] = 'e' * 40
        elif changed == 'access-revision':
            # Same effective mode, new exact authority revision: this isolates
            # the access guard from pending configuration and Stop fences.
            await h.app.dispatch('access.set', {
                'sessionId': h.sid, 'scope': 'session', 'mode': 'ask',
                'expectedRevision': h.app.access_mode.snapshot(h.app._session(h.sid))['revision'],
            }, command_id='late-human-access-choice', include_state=False)
        else:
            h.app.instance_id = state['drain']['hostInstanceId']

    h.runtime.before_admission = invalidate
    await h.finish()
    assert len(h.runtime.admission_calls) == 1
    assert len(h.runtime.guard_reasons) == 1 and h.runtime.guard_reasons[0]
    assert not h.runtime.admitted and not h.runtime.sent
    requests = h.receipts('coordination.send')
    assert len(requests) == 1 and requests[0]['delivery'] in {'not_sent', 'suppressed'}
    await h.app.collaboration.drain(h.sid)
    await h.settle()
    assert len(h.runtime.admission_calls) == 1  # A denied input is not replayed.


@pytest.mark.parametrize('forgery', ['child', 'generation', 'owner'])
async def test_enrollment_requires_transport_bound_current_root(waiting, forgery):
    h = waiting
    session = h.app._session(h.sid)
    transport = {'_runtimeSessionId': h.sid,
        '_generationId': session['collaborationGeneration']['id'],
        '_runtimeOwnerId': h.runtime.workers[h.sid]['runtimeOwnerId']}
    transport[{'child': '_runtimeSessionId', 'generation': '_generationId',
               'owner': '_runtimeOwnerId'}[forgery]] = 'not-the-current-owner'
    with pytest.raises(AppError):
        await h.app.app_bridge('dispatch', {'action': 'task.waitForUpdate',
            'args': h.wait_args, 'id': 'forged-wait', **transport}, h.sid)
    assert not h.runtime.boundary_calls and not h.receipts('task.waitForUpdate')


async def test_unknown_boundary_does_not_hold_a_new_human_generation(waiting):
    h = waiting
    h.runtime.lose_boundary_ack = True
    with pytest.raises(TimeoutError):
        await h.wait()
    h.runtime.lose_boundary_ack = False
    # Finish the original synthetic transport before admitting fresh human input;
    # a held steering attempt is not a new accepted generation.
    h.runtime.native[h.sid].runtime.generation = None
    h.runtime.native[h.sid].parked = True
    h.app._session(h.sid).update(status='idle')
    h.app._session(h.sid)['collaborationGeneration']['terminal'] = True
    await h.app.dispatch('conversation.send', {'sessionId': h.sid, 'text': 'New direction',
        'allowNextTurn': True}, command_id='fresh-human', include_state=False)
    await h.settle()
    assert not drain.owner_waiting(h.app, h.sid)
    await drain.observe_waiters(h.manager)
    assert h.sid not in h.app.state['updates']['drain']['workers']
    assert h.app.state['updates']['drain']['phase'] == 'released'
    saved = (await h.app.dispatch('task.receipt',
        {'sessionId': h.sid, 'commandId': 'owner-wait'}, include_state=False))['result']['receipt']
    assert saved['admissionStatus'] == 'unknown' and saved['status'] == 'suppressed'
    assert saved['boundary']['unknown'] and saved['boundary']['suppressed']
    assert len(h.runtime.boundary_calls) == 2  # Original effect, then exact cancellation; no replay.
    assert not h.runtime.admitted


async def test_final_idle_gate_requires_positive_enrolled_checkpoint(waiting):
    h = waiting
    await h.wait()
    await h.yield_turn()
    # Final app_updates admission uses manager.busy(), after candidate probes.
    # A new owner may settle to idle in that interval, without observed proof.
    assert h.app._session(h.sid)['status'] == 'idle'
    assert not h.row['ready'] and h.manager.busy()
    assert any(row['kind'] == 'update-boundary' for row in h.manager.blockers())
    await drain.observe_waiters(h.manager)
    assert h.row['ready'] and not h.manager.busy()


async def test_stop_supersedes_lost_owner_wait_without_claiming_checkpoint_or_replay(waiting):
    h = waiting
    await h.wait()
    await h.yield_turn()
    h.runtime.workers.pop(h.sid)  # The original transport is positively gone.
    await drain.observe_waiters(h.manager)
    assert h.row['unknown'] and not h.row['ready']
    assert h.manager.busy()
    calls = len(h.runtime.boundary_calls)
    await h.change_task('stop')
    await drain.observe_waiters(h.manager)
    assert h.row['unknown'] and h.row['suppressed'] and not h.row['ready']
    assert h.app.state['updates']['drain']['phase'] == 'released'
    assert not h.manager.busy()
    saved = (await h.app.dispatch('task.receipt',
        {'sessionId': h.sid, 'commandId': 'owner-wait'}, include_state=False))['result']['receipt']
    assert saved['status'] == 'suppressed'
    assert saved['boundary']['unknown'] and not saved['boundary']['ready']
    assert len(h.runtime.boundary_calls) == calls  # No call to the lost owner.
    assert not h.runtime.admitted


async def test_idle_configuration_refresh_preserves_update_checkpoint_and_continuation(waiting):
    h = waiting
    session = h.app._session(h.sid)
    session.update(configurationPending=True, configurationPendingRevision=7)
    owner = h.runtime.workers[h.sid]['runtimeOwnerId']
    original_task = copy.deepcopy(h.controller.tasks.record())
    await h.wait()
    await h.yield_turn()  # The real idle event schedules the competing refresh.
    await h.settle()
    assert not h.runtime.stopped, 'Refresh retired the enrolled owner before its checkpoint'
    assert h.runtime.workers[h.sid]['runtimeOwnerId'] == owner
    assert session['configurationPending'] and not session.get('configurationBusy')

    assert await drain.prepare(h.manager, h.release)
    assert h.row['ready'] and not h.row.get('suppressed')
    await h.app.refresh_configuration(h.sid)
    assert not h.runtime.stopped, 'Refresh must not invalidate an already ready update wait'
    h.replace()
    await h.reconnect(successor=True)
    await h.app.refresh_configuration(h.sid)  # The successor has no old runtime to retire.
    assert not h.app._session(h.sid).get('configurationPending')
    await h.confirm()
    await h.finish()
    await h.finish()
    assert len(h.runtime.admitted) == 1
    admitted = h.runtime.admitted[0]
    assert admitted['task']['id'] == original_task['id']
    assert admitted['goal']['condition'] == OBJECTIVE
    assert admitted['goal']['turns_used'] == 3
    assert admitted['goal']['continuations'] == 2


@pytest.mark.parametrize('superseded', ['configuration', 'stop', 'task-pause', 'new-owner', 'no-owner'])
async def test_update_wait_does_not_block_superseding_configuration_refresh(waiting, superseded):
    h = waiting
    session = h.app._session(h.sid)
    session.update(configurationPending=True, configurationPendingRevision=7)
    await h.wait()
    # Park without an idle event so this test controls refresh admission order.
    h.runtime.native[h.sid].runtime.generation = None
    h.runtime.native[h.sid].parked = True
    session.update(status='idle')
    session['collaborationGeneration']['terminal'] = True
    if superseded == 'configuration':
        session['configurationPendingRevision'] += 1
    elif superseded == 'stop':
        await h.change_task('stop')
    elif superseded == 'task-pause':
        await h.change_task('paused')
    elif superseded == 'new-owner':
        h.runtime.workers[h.sid] = {'runtimeOwnerId': 'replacement-owner'}
    else:
        h.runtime.workers.pop(h.sid)
    await h.app.refresh_configuration(h.sid)
    await h.settle()
    assert not session.get('configurationPending')
    assert not session.get('configurationBusy')
    assert bool(h.runtime.stopped) is (superseded != 'no-owner')
    assert not h.runtime.admitted


async def test_predecessor_block_is_not_an_update_wait_and_remains_blocked(waiting):
    """The original workaround stops goals but cannot produce a completion wake."""
    h = waiting
    await h.app.dispatch('task.block', {'sessionId': h.sid, 'expectedRevision': 1,
        'reason': 'Yield for the authorized app update'}, command_id='old-block', include_state=False)
    worker = h.runtime.native[h.sid]
    worker.runtime.generation = None
    worker.parked = True
    h.app._session(h.sid).update(status='idle')
    h.app._session(h.sid)['collaborationGeneration']['terminal'] = True
    assert not await h.controller.tasks.continuation_allowed()
    assert await drain.prepare(h.manager, h.release)
    h.replace()
    await h.reconnect(successor=True)
    await h.confirm()
    await h.finish()
    assert h.controller.tasks.record()['status'] == 'blocked'
    assert h.controller.tasks.record()['goalState']['turns_used'] == 3
    assert not h.runtime.admitted and not h.receipts('updates.app')


async def test_wait_receipt_retains_target_and_adoption_without_task_mutation(waiting):
    h = waiting
    before = copy.deepcopy(h.controller.tasks.record())
    await h.ready()
    h.replace()
    await h.reconnect(successor=True)
    await h.confirm()
    await h.finish()
    result = (await h.app.dispatch('task.receipt',
        {'sessionId': h.sid, 'commandId': 'owner-wait'}, include_state=False))['result']
    assert result['status'] == 'recorded'
    assert result['receipt']['status'] == 'adopted'
    assert result['receipt']['admissionStatus'] == 'acknowledged'
    assert result['receipt']['boundary']['updateTarget'] == {
        key: h.release[key] for key in ('revision', 'attemptId', 'generation')}
    assert h.controller.tasks.record()['revision'] == before['revision']
    reference = result['receipt']['continuation']['resultRef']
    recovered = await h.app.dispatch(reference['action'], reference['args'], include_state=False)
    assert recovered['result']['receipt']['delivery'] == 'accepted'
    assert result['receipt']['continuation']['inputId'] == h.runtime.admitted[0]['inputId']


async def test_fresh_generation_can_delegate_before_another_updater_observation(waiting):
    h = waiting
    await h.wait()
    hook = h.runtime.hooks[h.sid]['tool:pre']
    assert (await hook('tool:pre', {'tool_name': 'delegate', 'tool_call_id': 'new-child'})).action == 'deny'
    # Accepted fresh input supersedes this native update freeze synchronously.
    # There is deliberately no observe_waiters/cancellation call between them.
    h.runtime.native[h.sid].runtime.generation = {'id': 'fresh-human-generation'}
    assert (await hook('tool:pre', {'tool_name': 'delegate', 'tool_call_id': 'new-child'})).action != 'deny'
    assert h.runtime.native[h.sid]._handoff_boundary is not None  # Original intent remains retained.


@pytest.mark.parametrize('kind', ['user', 'steer', 'service', 'rejected-steer'])
async def test_update_yield_distinguishes_fresh_admission_from_child_result(waiting, kind):
    from amplifier_module_loop_live.runtime import Input
    h = waiting
    runtime = h.runtime.native[h.sid].runtime

    class Accepted(dict):
        visits = 0
        def __reversed__(self):
            for key in super().__reversed__():
                self.visits += 1
                yield key

    # Old receipt bodies must never be traversed at repeated tool boundaries.
    runtime.accepted = Accepted((f'old-{i}', object()) for i in range(1000))
    runtime.accepted['original'] = Input('user', 'Original', id='original')
    await h.wait()
    before = h.controller.coordinator.session_state['goal']
    native = h.runtime.native[h.sid]
    hook = h.runtime.hooks[h.sid]['tool:pre']
    runtime.accepted.visits = 0
    assert (await hook('tool:pre', {'tool_name': 'delegate', 'tool_call_id': 'new'})).action == 'deny'
    assert runtime.accepted.visits == 1
    if kind != 'rejected-steer':
        runtime.accepted['fresh'] = Input(kind, 'New input', id='fresh',
            source='amplifier-delegate' if kind == 'service' else 'user')
    if kind == 'service':
        # Delivered child reports can grow generation.input_ids but are not
        # new human direction. Existing drain correlation still owns them.
        runtime.generation['input_ids'] = ['original', 'fresh']
    fresh = kind in {'user', 'steer'}
    assert ((await hook('tool:pre', {'tool_name': 'delegate', 'tool_call_id': 'new'})).action != 'deny') is fresh
    await h.runtime.hooks[h.sid]['orchestrator:goal_progress']('orchestrator:goal_progress', {
        'state': 'continuing', 'session_id': h.sid,
        'condition': before['condition'], 'turn': before['turns_used']})
    assert (h.controller.coordinator.session_state['goal'] is before) is fresh
    assert ('yieldedGoal' in native._handoff_boundary) is not fresh
    assert runtime.accepted.visits <= 5  # Constant old-history work plus the one new input.
