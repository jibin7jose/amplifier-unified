"""Durable test outcomes through the real guarded continuation transport.

The physical backend is controlled. These tests qualify host admission and
journals, not a model/provider turn or physical Docker execution.
"""
import asyncio
import json

import pytest

from amplifier_web import task_environments as environments
from amplifier_web.service import AppService
from amplifier_web.test_queue import TestQueue
from test_configuration_authority import app
from test_coordination_generation import Transport, until
from test_task_environments import backend, scope
from test_test_queue import drive, queue, record, submit


class BoundaryTransport(Transport):
    """Opt-in no-effect busy boundary; busy alone permits automatic returns."""
    def __init__(self):
        super().__init__()
        self.unavailable_boundaries = set()

    async def collaboration_input(self, session, arguments, guard, emit):
        if (session['id'] in self.unavailable_boundaries
                and session.get('status') in {'working', 'ready', 'starting'}):
            return {'accepted': False, 'effect': 'none', 'waitingForBoundary': True}
        return await super().collaboration_input(session, arguments, guard, emit)


@pytest.fixture(autouse=True)
def transport(app):
    runtime = BoundaryTransport()
    runtime.app = app
    app.runtime = runtime
    return runtime


def notifications(app, request):
    return [json.loads(row[0]) for row in app.db.execute(
        "SELECT receipt FROM commands WHERE json_extract(receipt,'$.dependencyRequestId')=?",
        (request['requestId'],))]


async def delivered(app, request):
    await until(lambda: len(notifications(app, request)) == 1
                and notifications(app, request)[0]['delivery'] == 'accepted')
    return notifications(app, request)[0]


def assert_single_notification(app, request, transport):
    rows = notifications(app, request)
    assert len(rows) == 1
    notification = rows[0]
    assert notification['dependencySatisfied'] is False
    assert notification['dependencyOutcomeRevision'] == 1
    assert len(transport.inputs) == 1
    assert transport.inputs[0][1] == notification['inputId']
    source = app._session(request['senderSessionId'])
    messages = [m for m in source['messages'] if m.get('inputId') == notification['inputId']]
    assert len(messages) == 1 and messages[0]['inputOrigin'] == 'peer'
    assert messages[0]['peerEnvelope']['replyToRequestId'] == request['requestId']
    return notification


@pytest.mark.parametrize('outcome', ['passed', 'failed', 'unknown'])
async def test_observed_outcome_resumes_once_without_claiming_acceptance(
        app, queue, backend, transport, monkeypatch, outcome):
    invoke = environments.invoke

    def checked(config, home, row, action, args):
        if action == 'execute' and outcome == 'unknown':
            backend.append((action, dict(args)))
            raise TimeoutError('Lost command acknowledgement')
        result = invoke(config, home, row, action, args)
        if action == 'job_read' and outcome == 'unknown':
            result['job'].update(state='unknown', terminal=False)
        if action == 'execute' and outcome == 'failed':
            result['result']['exit_code'] = 1
            result['job']['state'] = 'failed'
        return result

    monkeypatch.setattr(environments, 'invoke', checked)
    app.state['view']['draft'] = 'Continue independent work here'
    selection = app.state['selectedSessionId']
    await submit(app, notify=True)
    await drive(queue)
    request = record(queue, app)
    assert request['job']['status'] == outcome
    notification = await delivered(app, request)
    assert notification['continuationReason'] == outcome
    assert notification['outcomeSnapshot']['qualified'] is False
    assert_single_notification(app, request, transport)
    for _ in range(2):
        await drive(queue)
        app.collaboration.enqueue_continuation(record(queue, app))
        await app.collaboration.drain(request['senderSessionId'])
    assert_single_notification(app, request, transport)
    assert [action for action, _ in backend].count('execute') == 1
    assert app.state['selectedSessionId'] == selection
    assert app.state['view']['draft'] == 'Continue independent work here'


async def test_busy_root_keeps_result_queued_then_admits_after_idle(
        app, queue, backend, transport):
    source = app.state['sessions'][0]
    source['status'] = 'working'
    transport.unavailable_boundaries.add(source['id'])
    await submit(app, notify=True)
    await drive(queue)
    request = record(queue, app)
    await app.collaboration.drain(source['id'])
    assert request['job']['status'] == 'passed'
    assert notifications(app, request)[0]['delivery'] == 'queued'
    assert not transport.inputs
    await app.on_runtime_event('runtime.status', {'sessionId': source['id'], 'status': 'idle'})
    await delivered(app, request)
    assert_single_notification(app, request, transport)
    assert [action for action, _ in backend].count('execute') == 1


async def test_busy_root_accepts_automatic_result_at_available_boundary(
        app, queue, backend, transport):
    source = app.state['sessions'][0]
    source['status'] = 'working'
    await submit(app, notify=True)
    await drive(queue)
    request = record(queue, app)
    notification = await delivered(app, request)
    assert notification['mode'] == 'auto'
    assert source['status'] == 'working'
    assert_single_notification(app, request, transport)
    assert [action for action, _ in backend].count('execute') == 1


@pytest.mark.parametrize('failure', ['before', 'after'])
async def test_notification_commit_failure_retries_durably_without_reexecuting(
        app, queue, backend, transport, monkeypatch, failure):
    source = app.state['sessions'][0]
    source['status'] = 'working'
    transport.unavailable_boundaries.add(source['id'])
    await submit(app, notify=True)
    request = record(queue, app)
    identity = request['subscription']['continuationId']
    publish = app._publish_changes

    def fail(**kwargs):
        if app.db.execute('SELECT 1 FROM commands WHERE id=?', (identity,)).fetchone():
            if failure == 'after':
                publish(**kwargs)
            raise OSError('Injected continuation commit failure')
        return publish(**kwargs)

    monkeypatch.setattr(app, '_publish_changes', fail)
    await drive(queue)
    saved = record(queue, app)
    assert saved['job']['status'] == 'passed'
    assert saved['terminalOutcome']['outcome'] == 'passed'
    assert len(notifications(app, saved)) == (0 if failure == 'before' else 1)
    assert not transport.inputs
    monkeypatch.setattr(app, '_publish_changes', publish)
    # Reopen the actual host database, not merely an in-memory scheduler object.
    await app.close()
    recovered_runtime = Transport()
    recovered = recovered_runtime.app = AppService(
        app.data_dir, recovered_runtime, workspace=app.default_workspace)
    monkeypatch.setattr(recovered.test_queue, 'start', lambda: None)
    try:
        recovered._session(source['id'])['status'] = 'idle'
        recovered.collaboration.start()
        await drive(recovered.test_queue)
        await delivered(recovered, saved)
        assert_single_notification(recovered, saved, recovered_runtime)
        await drive(recovered.test_queue)
        recovered.collaboration.start()
        await recovered.collaboration.drain(source['id'])
        assert_single_notification(recovered, saved, recovered_runtime)
        assert [action for action, _ in backend].count('execute') == 1
    finally:
        await recovered.close()


@pytest.mark.parametrize('phase', ['execute', 'remove'])
async def test_restart_joins_exact_operation_before_notifying(
        app, queue, backend, transport, monkeypatch, phase):
    effect = queue.effect

    async def lost(receipt, config, action, **kwargs):
        result = await effect(receipt, config, action, **kwargs)
        if action == phase:
            raise asyncio.CancelledError
        return result

    monkeypatch.setattr(queue, 'effect', lost)
    await submit(app, notify=True)
    await queue.tick()
    await asyncio.gather(*queue.tasks.values(), return_exceptions=True)
    request = record(queue, app)
    assert request['job']['status'] == ('checking' if phase == 'execute' else 'removing')
    assert not notifications(app, request) and not transport.inputs
    operation = app.operations.requests.read(request['senderSessionId'], request['requestId'] + ':' + phase)
    assert operation['state'] == 'completed'
    recovered = app.test_queue = TestQueue(app)
    await drive(recovered)
    request = record(recovered, app)
    assert request['job']['status'] == 'passed'
    await delivered(app, request)
    assert_single_notification(app, request, transport)
    assert [action for action, _ in backend].count('execute') == 1
    assert [action for action, _ in backend].count('remove') == 1


@pytest.mark.parametrize('control', ['cancel', 'stop', 'assignment-stop'])
async def test_control_during_check_keeps_evidence_but_never_resumes(
        app, queue, backend, transport, monkeypatch, control):
    source = app.state['sessions'][0]
    if control == 'assignment-stop':
        await app.dispatch('assignment.start', {'sessionId': source['id']}, command_id='assignment')
    entered, release = asyncio.Event(), asyncio.Event()
    effect = queue.effect

    async def paused(receipt, config, action, **kwargs):
        result = await effect(receipt, config, action, **kwargs)
        if action == 'execute':
            entered.set()
            await release.wait()
        return result

    monkeypatch.setattr(queue, 'effect', paused)
    await submit(app, notify=True)
    await queue.tick()
    await asyncio.wait_for(entered.wait(), 5)
    try:
        if control == 'cancel':
            await app.dispatch('workspace.tests.cancel', {**scope(app), 'requestId': 'check'})
        elif control == 'stop':
            await app.dispatch('conversation.stop', {'sessionId': source['id']}, command_id='stop')
        else:
            value = app.assignments.current(source)
            await app.dispatch('assignment.stop', {'sessionId': source['id'],
                'expectedRevision': value['revision']}, command_id='stop-assignment')
    finally:
        release.set()
    await asyncio.gather(*queue.tasks.values())
    request = record(queue, app)
    assert request['job']['check']['state'] == 'completed'
    assert request['job']['removal']['state'] == 'completed'
    assert request['terminalOutcome']['qualified'] is False
    await drive(queue)
    await app.collaboration.drain(source['id'])
    assert not transport.inputs
    assert all(row['delivery'] == 'suppressed' for row in notifications(app, request))
    if control == 'cancel':
        assert request['job']['status'] == 'cancelled'
        assert request['subscription']['status'] == 'suppressed'
    assert [action for action, _ in backend].count('execute') == 1
    assert [action for action, _ in backend].count('remove') == 1


@pytest.mark.parametrize('control', ['stop', 'assignment-stop'])
async def test_control_after_result_before_admission_suppresses_queued_notification(
        app, queue, backend, transport, control):
    source = app.state['sessions'][0]
    source['status'] = 'working'
    transport.unavailable_boundaries.add(source['id'])
    if control == 'assignment-stop':
        await app.dispatch('assignment.start', {'sessionId': source['id']}, command_id='assignment')
    await submit(app, notify=True)
    await drive(queue)
    request = record(queue, app)
    assert notifications(app, request)[0]['delivery'] == 'queued'
    if control == 'stop':
        await app.dispatch('conversation.stop', {'sessionId': source['id']}, command_id='stop')
    else:
        value = app.assignments.current(source)
        await app.dispatch('assignment.stop', {'sessionId': source['id'],
            'expectedRevision': value['revision']}, command_id='stop-assignment')
    await app.collaboration.drain(source['id'])
    assert notifications(app, request)[0]['delivery'] == 'suppressed'
    assert record(queue, app)['job']['status'] == 'passed'
    assert not transport.inputs
    assert [action for action, _ in backend].count('execute') == 1
