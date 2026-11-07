"""Settings wait through real host admission, controls and native boundary logic.

Process creation/mounting are adapters here; the separate native-loop test
qualifies the goal checkpoint. No provider call or live state is used.
"""
import asyncio
import copy
import json
import os
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from amplifier_web import configuration_continuation as continuation
from amplifier_web.handoff_boundary import checkpoint_configuration, inspect
from amplifier_web.runtime_worker import Worker
from amplifier_web.runtime_controls import override_path
from test_queued_configuration_boundary import boundary, queue, plan
from test_configuration_authority import revoke
from test_task_continuity import create


@pytest.fixture
async def waiting(boundary):
    b = boundary
    runtime, c = b.runtime, b.runtime.coordinator
    handlers = {}

    def register(event, handler, **kwargs):
        handlers.setdefault(event, []).append(handler)
        return lambda: handlers[event].remove(handler)

    c.session_id = b.owner['id']
    c.hooks = SimpleNamespace(register=register)
    c.register_capability('live.continuation_guard_supported', True)
    runtime.loop.pending = []
    runtime.loop._active_jobs = lambda: []
    runtime.loop.goal_stall_threshold = 3
    runtime.native.inbox = asyncio.Queue()
    runtime.native.accepted = {}
    await create(runtime.controls, maxTurns=8)
    goal = c.session_state['goal']
    goal.update(turns_used=3, continuations=2)
    runtime.controls.persist(task_only=True)
    b.owner['task'] = copy.deepcopy(runtime.controls.tasks.record())
    b.owner['messages'] = [{'id': 'human', 'role': 'user', 'text': 'Finish the report',
                            'inputId': 'human-input'}]
    runtime.native.generation = {'id': b.owner['collaborationGeneration']['id']}
    runtime.block_start = False
    # A process reload in this transport reads its last saved configuration.
    override_path(b.owner['id']).write_text(json.dumps(plan(1)))
    runtime.workers = {b.owner['id']: {'runtimeOwnerId': 'original-owner',
                                     'native_worker_pid': os.getpid(),
                                     'process': SimpleNamespace(returncode=None)}}
    worker = b.worker = Worker()
    worker.session = runtime.controls.session
    worker.controls, worker.runtime = runtime.controls, runtime.native
    worker.parked = False
    b.handlers = handlers

    async def boundary_read(sid, operation, *, receipt, configuration, cancel=False):
        assert sid == b.owner['id'] and configuration
        expected = receipt['configurationWait']
        if runtime.workers[sid]['runtimeOwnerId'] != expected['owner']:
            raise ValueError('The requesting runtime owner changed')
        return await inspect(worker, operation, cancel, configuration=True, expected=expected)

    @contextmanager
    def stopped(session):
        assert session['id'] not in runtime.workers
        yield  # Native writer lease is covered separately with RuntimeManager.

    runtime.handoff_boundary = boundary_read
    runtime.stopped_boundary = stopped
    yield b


async def enrolled(b):
    await queue(b)
    async with asyncio.timeout(3):
        while not getattr(b.worker, '_handoff_boundary', None):
            await asyncio.sleep(.01)
    b.operation = b.owner['pendingConfiguration']['operationId']
    return b.app.management.configuration_record(b.owner['id'], b.operation)


async def yield_and_park(b):
    c = b.runtime.coordinator
    goal = c.session_state['goal']
    for hook in tuple(b.handlers['orchestrator:goal_progress']):
        await hook('orchestrator:goal_progress', {'state': 'continuing',
            'session_id': c.session_id, 'condition': goal['condition'], 'turn': goal['turns_used']})
    assert c.session_state['goal'] == goal and c.session_state['goal'] is not goal
    b.runtime.native.generation = None
    b.owner['collaborationGeneration']['terminal'] = True
    checkpoint_configuration(b.worker)
    b.worker.parked = True
    b.owner['status'] = 'idle'


async def finish(b):
    await asyncio.wait_for(b.queued_task, 4)
    await b.app.collaboration.drain(b.owner['id'])
    return b.app.management.configuration_record(b.owner['id'], b.operation)


async def test_settings_waits_for_tools_then_resumes_same_goal_once(waiting):
    b = waiting
    record = await enrolled(b)
    assert record['request']['source']['continuation']['lastInputId'] == 'human-input'
    assert not b.runtime.calls and not b.runtime.inputs
    before = copy.deepcopy(b.runtime.coordinator.session_state['goal'])
    b.worker.bridges['tool'] = asyncio.get_running_loop().create_future()
    await yield_and_park(b)
    await continuation.settle_worker(b.app.management, b.owner['id'], b.operation)
    assert not b.runtime.calls  # A settled root alone cannot retire a live tool.
    b.worker.bridges.pop('tool').set_result(None)
    record = await finish(b)
    assert record['state']['phase'] == 'ready', json.dumps(record['state'])
    assert b.app.management.configuration_readback_matches(plan(2), b.owner['configuration']['plan'])
    assert b.owner['configuration']['plan']['tools'][0]['config']['limit'] == 2
    assert b.runtime.inputs and len(b.runtime.inputs) == 1
    for _ in range(3):
        continuation.publish_outcome(b.app.management, b.owner, b.operation)
        await b.app.collaboration.drain(b.owner['id'])
    assert len(b.runtime.inputs) == 1
    assert b.runtime.coordinator.session_state['goal'] == before
    assert b.runtime.controls.tasks.record()['id'] == b.owner['task']['id']
    assert b.runtime.calls.count('saved') == 1


async def test_progress_revision_does_not_invalidate_settings_wait(waiting):
    b = waiting
    await enrolled(b)
    task = b.runtime.controls.tasks.record()
    await b.runtime.controls.perform('task.update', {'commandId': 'progress',
        'expectedRevision': task['revision'], 'artifactRefs': ['report.md', 'upload-receipt.json']})
    b.owner['task'] = copy.deepcopy(b.runtime.controls.tasks.record())
    await yield_and_park(b)
    assert (await finish(b))['state']['phase'] == 'ready'
    assert len(b.runtime.inputs) == 1


async def test_queued_status_explains_continuation_without_starting_work(waiting):
    b = waiting
    await enrolled(b)
    result = (await b.app.dispatch('configuration.operation.read', {
        'sessionId': b.owner['id'], 'operationId': b.operation, 'includeRecovery': True}))['result']
    assert 'continue automatically after verification' in result['recovery']['reason']
    assert not result['recovery']['canResume'] and not result['recovery']['canReconcile']
    assert not b.runtime.calls and not b.runtime.inputs
    await b.app.management.perform('configuration.cancel', {'id': b.owner['id']})
    await asyncio.gather(b.queued_task, return_exceptions=True)


@pytest.mark.parametrize('change', ['stop', 'task', 'input', 'authority', 'owner'])
async def test_changed_intent_never_applies_or_continues(waiting, change):
    b = waiting
    await enrolled(b)
    if change == 'stop':
        b.owner['interruptionRevision'] = b.owner.get('interruptionRevision', 0) + 1
    elif change == 'task':
        b.owner['task']['continuationEpoch'] += 1
    elif change == 'input':
        b.owner['messages'].append({'id': 'new', 'role': 'user', 'text': 'Different work', 'inputId': 'new'})
    elif change == 'authority':
        await revoke(b.app, b.authorized)
    else:
        b.runtime.workers[b.owner['id']]['runtimeOwnerId'] = 'replacement-owner'
    record = await finish(b)
    assert record['state']['phase'] == 'error'
    assert record['state']['effect'] == 'none'
    assert not b.runtime.calls and not b.runtime.inputs


async def test_cancel_releases_native_boundary_without_new_work(waiting):
    b = waiting
    await enrolled(b)
    await b.app.dispatch('configuration.cancel', {'id': b.owner['id']})
    await asyncio.gather(b.queued_task, return_exceptions=True)
    assert b.worker._handoff_boundary is None
    assert not b.runtime.calls and not b.runtime.inputs
    assert not b.owner.get('configurationBusy')


async def test_cancel_after_checkpoint_keeps_settings_and_does_not_resume(waiting):
    b = waiting
    await enrolled(b)
    # Keep a tool reply outstanding so cancellation reaches the queued operation
    # before application claims it. Cancelling an already-applied change is not
    # rollback, and dispatch acceptance alone does not order these two tasks.
    b.worker.bridges['tool'] = asyncio.get_running_loop().create_future()
    await yield_and_park(b)
    await b.app.management.perform('configuration.cancel', {'id': b.owner['id']})
    await asyncio.gather(b.queued_task, return_exceptions=True)
    record = b.app.management.configuration_record(b.owner['id'], b.operation)
    assert record['state']['phase'] == 'cancelled'
    assert record['state']['effect'] == 'none'
    assert b.worker._handoff_boundary is None
    assert not b.runtime.calls and not b.runtime.inputs
    assert not b.owner.get('configurationBusy')
    assert b.runtime.coordinator.session_state['goal']['turns_used'] == 3
    b.worker.bridges.pop('tool').set_result(None)


async def test_confirmed_no_effect_failure_returns_once_under_original_settings(waiting):
    b = waiting
    await enrolled(b)
    await yield_and_park(b)
    # Real management refuses before native dispatch when PID proof is absent.
    b.runtime.workers[b.owner['id']]['native_worker_pid'] = None
    record = await finish(b)
    assert record['state']['phase'] == 'error' and record['state']['effect'] == 'none'
    assert 'identity was not confirmed' in record['state']['error']
    assert 'configuration.apply' not in b.runtime.calls
    assert b.runtime.coordinator.config == plan(1)
    assert b.worker._handoff_boundary is None
    assert not b.owner.get('configurationBusy')
    assert len(b.runtime.inputs) == 1
    request = json.loads(b.app.db.execute('SELECT receipt FROM commands WHERE id=?',
        ('configuration-wait:' + b.operation,)).fetchone()[0])
    assert request['terminalOutcome']['outcome'] == 'configuration-failed'
    assert 'identity was not confirmed' in request['terminalOutcome']['text']
    for _ in range(3):
        continuation.publish_outcome(b.app.management, b.owner, b.operation)
        await b.app.collaboration.drain(b.owner['id'])
    assert len(b.runtime.inputs) == 1


async def test_new_input_after_checkpoint_prevents_settings_effect_and_continuation(waiting):
    b = waiting
    await enrolled(b)
    await yield_and_park(b)
    b.owner['messages'].append({'id': 'new', 'role': 'user', 'text': 'Different work', 'inputId': 'new'})
    record = await finish(b)
    assert record['state']['phase'] == 'error' and record['state']['effect'] == 'none'
    assert not b.runtime.calls and not b.runtime.inputs
    assert b.owner['messages'][-1]['inputId'] == 'new'
    assert not b.owner.get('configurationBusy')


async def test_settings_outcome_does_not_extend_exhausted_goal(waiting):
    b = waiting
    await enrolled(b)
    b.runtime.coordinator.session_state['goal']['cap'] = 3
    await yield_and_park(b)
    record = await finish(b)
    assert record['state']['phase'] == 'ready'
    assert b.runtime.calls.count('saved') == 1
    assert not b.runtime.inputs
    assert b.runtime.coordinator.session_state['goal']['turns_used'] == 3


@pytest.mark.parametrize('fault', ['lost-before-dispatch', 'lost-after-save'])
async def test_uncertain_settings_effect_never_auto_continues(waiting, fault):
    b = waiting
    b.runtime.fault = fault
    await enrolled(b)
    await yield_and_park(b)
    record = await finish(b)
    assert record['state']['phase'] == 'error'
    assert record['state']['effect'] != 'none'
    assert not b.runtime.inputs


@pytest.mark.parametrize('proof', ['valid', 'absent', 'different-operation', 'changed-goal'])
async def test_lost_worker_reply_requires_saved_same_checkpoint(waiting, proof):
    b = waiting
    record = await enrolled(b)
    await yield_and_park(b)
    path = b.runtime.controls.state_path()
    saved = json.loads(path.read_text())
    if proof == 'absent':
        saved.pop('configurationCheckpoint')
    elif proof == 'different-operation':
        saved['configurationCheckpoint']['operationId'] = 'another-operation'
    elif proof == 'changed-goal':
        saved['goal']['turns_used'] += 1
    path.write_text(json.dumps(saved))
    b.runtime.workers.clear()
    b.owner['status'] = 'idle'
    record = await finish(b)
    if proof == 'valid':
        assert record['state']['phase'] == 'ready', record['state']
        assert len(b.runtime.inputs) == 1
    else:
        assert record['state']['phase'] == 'error'
        assert not b.runtime.calls and not b.runtime.inputs
