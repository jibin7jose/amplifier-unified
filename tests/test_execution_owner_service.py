"""Real service admission, persistence and Stop guard with controlled runtime I/O."""
import asyncio
import json

import pytest

from amplifier_web.execution_owner import ExecutionOwner
from amplifier_web.service import AppService
from test_coordination_generation import source_plan


class Runtime:
    def __init__(self):
        self.send_entered = asyncio.Event()
        self.send_ack = asyncio.Event()
        self.stop_entered = asyncio.Event()
        self.stop_finish = asyncio.Event()
        self.send_task = None
        self.send_cancelled = False
        self.send_error = None
        self.stop_error = None
        self.stops = []
        self.decisions = []

    async def start(self, session, emit):
        pass

    async def send(self, session, text, input_id, emit):
        self.send_task = asyncio.current_task()
        await emit('runtime.status', {'sessionId': session['id'], 'status': 'working',
                                     'event': 'input.delivered', 'inputId': input_id})
        self.send_entered.set()
        try:
            await self.send_ack.wait()
        except asyncio.CancelledError:
            self.send_cancelled = True
            raise
        if self.send_error:
            raise self.send_error
        return {'accepted': True}

    async def stop(self, sid, *, reason='stopped'):
        self.stops.append(sid)
        self.stop_entered.set()
        await self.stop_finish.wait()
        if self.stop_error:
            raise self.stop_error

    async def approval(self, *args):
        self.decisions.append(args)

    async def close(self):
        pass


async def bounded(awaitable):
    # A deadline detects a stranded observer without cancelling canonical work.
    return await asyncio.wait_for(asyncio.shield(awaitable), 2)


async def published(app, predicate):
    queue = app.subscribe()
    try:
        async with asyncio.timeout(2):
            while not predicate():
                await queue.get()
    finally:
        app.unsubscribe(queue)


@pytest.fixture
async def attached(tmp_path):
    runtime = Runtime()
    app = AppService(tmp_path / 'app', runtime, workspace=tmp_path)
    owner = ExecutionOwner(app, workspace=tmp_path, bundle='foundation', origin='ui')
    sid = await owner.create(str(tmp_path))
    source_plan(app, app._session(sid))
    prompts = []

    async def prompt():
        async def observe(event):
            pass
        task = asyncio.create_task(owner.prompt(sid, 'input', 'Retained input', observe))
        prompts.append(task)
        await bounded(runtime.send_entered.wait())
        return task

    yield app, owner, runtime, sid, prompt
    runtime.send_ack.set()
    runtime.stop_finish.set()
    for task in prompts:
        task.cancel()
    await asyncio.gather(*prompts, return_exceptions=True)
    await owner.close()


def receipt(app, identity):
    return json.loads(app.db.execute('SELECT receipt FROM commands WHERE id=?', (identity,)).fetchone()[0])


async def finish(app, sid):
    await app.on_runtime_event('runtime.generation', {
        'sessionId': sid, 'rootSessionId': sid, 'event': 'generation.finished',
        'generation_id': 'generation', 'input_ids': ['input'],
        'text': 'Canonical result', 'active_job_ids': [],
        'disposition': 'manager_turn_finished',
    })


@pytest.mark.parametrize('outcome', ['failed', 'confirmed', 'completed', 'stale-error'])
async def test_admitted_stop_observes_canonical_terminal_state(attached, outcome):
    app, owner, runtime, sid, prompt = attached
    if outcome != 'completed':
        runtime.send_ack.set()
    task = await prompt()
    await published(app, lambda: receipt(app, 'input').get('delivery') == 'accepted')
    runtime.stop_error = RuntimeError('Controlled asynchronous Stop failure') if outcome in {'failed', 'completed'} else None
    await owner.cancel(sid)
    await bounded(runtime.stop_entered.wait())
    stop_id = app._session(sid)['lastInterruption']['commandId']
    assert receipt(app, stop_id)['accepted']
    assert app._session(sid)['status'] == 'stopping'

    if outcome == 'completed':
        # Hold admission so the response waiter sees both retained observations.
        await finish(app, sid)
    elif outcome == 'stale-error':
        await app.on_runtime_event('runtime.error', {
            'sessionId': sid, 'error': 'Stale failure must be ignored',
            'terminalFailure': {'generationId': 'not-current'},
        })
        assert app._session(sid)['status'] == 'stopping'
        assert not owner.pending[sid]['stopped'].done()
    runtime.stop_finish.set()
    expected_status = 'error' if runtime.stop_error else 'stopped'
    await published(app, lambda: app._session(sid)['status'] == expected_status)
    runtime.send_ack.set()
    if outcome == 'failed':
        with pytest.raises((ValueError, RuntimeError), match='Stop|asynchronous'):
            await bounded(task)
    else:
        expected = {'outcome': 'completed', 'text': 'Canonical result'} if outcome == 'completed' else {'outcome': 'cancelled'}
        assert await bounded(task) == expected
    assert runtime.stops == [sid]  # No retry of an admitted Stop.
    assert not runtime.send_cancelled
    assert receipt(app, 'input')['delivery'] == 'accepted'


@pytest.mark.parametrize('detach', ['ask', 'release', 'release-then-error'])
async def test_observer_detaches_before_send_ack_without_cancelling_admission(attached, detach):
    app, owner, runtime, sid, prompt = attached
    task = await prompt()
    row = owner.pending[sid]
    assert not runtime.send_ack.is_set()
    assert receipt(app, 'input')['delivery'] == 'accepted'
    if detach == 'ask':
        assert not app.access_mode.full(app._session(sid))
        await app.on_runtime_event('approval.requested', {
            'sessionId': sid, 'id': 'approval', 'options': ['allow', 'deny'],
        })
    else:
        await owner.release(sid)
    await bounded(row['delivery'].failed)
    with pytest.raises(ValueError, match='approval|detached'):
        await bounded(task)

    assert not runtime.send_ack.is_set() and not runtime.send_cancelled
    assert runtime.send_task in app.tasks and not runtime.send_task.done()
    assert runtime.stops == runtime.decisions == []
    assert len([m for m in app._session(sid)['messages'] if m.get('inputId') == 'input']) == 1
    if detach == 'ask':
        assert next(a for a in app._session(sid)['approvals'] if a['id'] == 'approval')['status'] == 'pending'

    if detach == 'release-then-error':
        runtime.send_error = RuntimeError('Late send acknowledgment failed')
    runtime.send_ack.set()
    # Observe completion without retrieving its exception: the adapter must
    # consume late task failures itself while the service retains the failure.
    done, _ = await asyncio.wait({runtime.send_task}, timeout=2)
    assert done == {runtime.send_task}
    if runtime.send_error:
        assert app._session(sid)['status'] == 'error'
        # The earlier exact input.delivered observation is stronger evidence
        # than a later missing ACK; it remains accepted, never replayed.
        assert receipt(app, 'input')['delivery'] == 'accepted'
        assert not runtime.send_task._log_traceback
    else:
        assert receipt(app, 'input')['delivery'] == 'accepted'
        await finish(app, sid)
        result = await bounded(app.wait_for_response(sid, 'input', timeout=None))
        assert result['text'] == 'Canonical result'
    assert runtime.stops == runtime.decisions == []
    assert not runtime.send_cancelled
