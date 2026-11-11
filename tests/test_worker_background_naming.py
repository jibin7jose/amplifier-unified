"""Background naming must retain its writer without blocking the live inbox."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web.runtime_worker import Worker
from amplifier_foundation.session.activation import ActivationGate


@pytest.fixture
async def worker(tmp_path, monkeypatch):
    from amplifier_foundation.session import shared_state
    owner = Worker()
    owner.workspace, owner.home = tmp_path/'workspace', tmp_path/'home'
    owner.workspace.mkdir(); owner.home.mkdir()
    owner.runtime = SimpleNamespace(session_id='naming', queued_inputs=0,
        inbox=asyncio.Queue(), generation=None)
    owner.shared_store = shared_state.SharedSessionStore(owner.workspace, 'naming', root=tmp_path/'shared')
    owner.shared_store_stamp = shared_state.file_stamp
    owner.shared_handle = owner.shared_store.acquire(app='amplifier-unified', fixture=True)
    owner.activation_gate = ActivationGate()
    owner.activation = owner.activation_gate.activate()
    owner.checkpoint = AsyncMock()
    owner.session = SimpleNamespace(coordinator=SimpleNamespace(get=lambda _: None,
        get_capability=lambda name: owner.checkpoint if name == 'live.checkpoint' else None))
    owner.finish_name = asyncio.Event()
    async def name():
        await owner.finish_name.wait()
        owner.activation_gate.check_current()
        assert owner.shared_handle.active
    owner.naming = SimpleNamespace(pending=asyncio.create_task(name()))
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', lambda _: None)
    try:
        yield owner
    finally:
        owner.shutdown.set()
        for task in [owner.naming.pending, *owner.tasks]: task.cancel()
        await asyncio.gather(owner.naming.pending, *owner.tasks, return_exceptions=True)
        if owner.shared_handle: owner.shared_handle.release()


async def settle(worker):
    worker.finish_name.set()
    await worker.naming.pending
    for _ in range(100):
        if not worker.tasks: return
        await asyncio.sleep(.01)
    pytest.fail('Deferred parking did not settle')


async def test_next_inbox_input_does_not_wait_for_background_name(worker):
    # Same ordering as loop-live: await live.park, then read the next input.
    async def inbox():
        await worker.park(activation=worker.activation)
        return await worker.runtime.inbox.get()
    consumer = asyncio.create_task(inbox())
    try:
        await asyncio.sleep(0)
        worker.runtime.queued_inputs = 1
        worker.runtime.inbox.put_nowait('next user input')
        assert await asyncio.wait_for(asyncio.shield(consumer), .5) == 'next user input'
        assert not worker.naming.pending.done()
        assert worker.shared_handle.active
        worker.checkpoint.assert_not_awaited()
    finally:
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)


async def test_idle_name_completion_checkpoints_and_releases_once(worker):
    await asyncio.wait_for(worker.park(), .5)
    await asyncio.wait_for(worker.park(), .5)
    assert worker.shared_handle.active
    assert len(worker.tasks) == 1
    await settle(worker)
    assert worker.parked and worker.shared_handle is None
    worker.checkpoint.assert_awaited_once_with('completed')


async def test_name_completion_cannot_park_active_foreground_work(worker):
    await asyncio.wait_for(worker.park(), .5)
    worker.runtime.generation = {'id': 'foreground'}
    await settle(worker)
    assert worker.shared_handle.active and not worker.parked
    worker.checkpoint.assert_not_awaited()
    worker.runtime.generation = None
    await worker.park()
    assert worker.parked


async def test_cancelled_deferred_parking_does_not_cancel_naming(worker):
    await asyncio.wait_for(worker.park(), .5)
    tasks = list(worker.tasks)
    for task in tasks: task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    assert not worker.naming.pending.done()
    await worker.park()
    await settle(worker)
    assert worker.parked


@pytest.mark.parametrize('change', ['session', 'activation', 'shutdown'])
async def test_old_naming_waiter_cannot_park_replacement_or_shutdown(worker, change):
    await asyncio.wait_for(worker.park(), .5)
    if change == 'session':
        worker.session = SimpleNamespace(coordinator=worker.session.coordinator)
    elif change == 'activation':
        # The old task keeps its original context token until it finishes.
        worker.activation = object()
    else:
        worker.shutdown.set()
    await settle(worker)
    worker.checkpoint.assert_not_awaited()
    assert worker.shared_handle.active and not worker.parked


async def test_cancelled_naming_still_releases_idle_writer(worker):
    await asyncio.wait_for(worker.park(), .5)
    worker.naming.pending.cancel()
    await asyncio.gather(worker.naming.pending, return_exceptions=True)
    for _ in range(100):
        if worker.parked: break
        await asyncio.sleep(.01)
    assert worker.parked and worker.shared_handle is None
    worker.checkpoint.assert_awaited_once_with('completed')


async def test_failed_deferred_checkpoint_reports_error_without_releasing_writer(worker, monkeypatch):
    events = []
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', events.append)
    worker.checkpoint.side_effect = OSError('checkpoint failed')
    await asyncio.wait_for(worker.park(), .5)
    await settle(worker)
    assert worker.shutdown.is_set()
    assert worker.shared_handle.active and not worker.parked
    assert any(e['type'] == 'runtime.error' and 'checkpoint failed' in e['error'] for e in events)


async def test_real_live_loop_admits_second_turn_while_name_is_pending(worker):
    pytest.importorskip('amplifier_module_loop_live')
    pytest.importorskip('amplifier_module_context_simple')
    from amplifier_core import AmplifierSession
    from amplifier_core.message_models import ChatResponse, TextBlock
    from amplifier_module_loop_live.runtime import Runtime, Input

    class Provider:
        name, config = 'fixture', {}
        def __init__(self):
            self.requests, self.responses = asyncio.Queue(), asyncio.Queue()
        def get_info(self): return SimpleNamespace(default_model='fixture')
        def parse_tool_calls(self, response): return []
        async def complete(self, request, **kwargs):
            await self.requests.put(request)
            return await self.responses.get()

    provider = Provider()
    worker.runtime = Runtime(session_id='naming')
    worker.runtime.capture_activation = worker.activation_gate.current
    session = AmplifierSession({'session': {
        'orchestrator': {'module': 'loop-live', 'config': {
            'configured_bundle': True, 'background_delegate': True,
            'min_delay_between_calls_ms': 0}},
        'context': {'module': 'context-simple'}}, 'providers': []}, session_id='naming')
    await session.initialize()
    worker.session = session
    await session.coordinator.mount('providers', provider, name='fixture')
    for name, capability in {'live.runtime': worker.runtime,
            'live.activation': worker.activation_gate, 'live.park': worker.park,
            'live.checkpoint': worker.checkpoint}.items():
        session.coordinator.register_capability(name, capability)
    token = worker.activation_gate.bind(worker.activation)
    await worker.runtime.submit(Input('user', 'First turn', id='first', activation=worker.activation))
    execution = asyncio.create_task(session.execute(''))
    try:
        await worker.runtime.wait_for(lambda e: e['type'] == 'session.ready', 3)
        try:
            await asyncio.wait_for(provider.requests.get(), 3)
        except TimeoutError:
            pytest.fail(str(worker.runtime.events))
        await provider.responses.put(ChatResponse(content=[TextBlock(text='First answer')]))
        await worker.runtime.wait_for(lambda e: e['type'] == 'generation.finished', 3)
        await worker.runtime.wait_for(lambda e: e['type'] == 'session.idle', 3)
        await worker.runtime.submit(Input('user', 'Second turn', id='second', activation=worker.activation))
        request = await asyncio.wait_for(provider.requests.get(), 1)
        assert 'Second turn' in str(request.messages)
        assert not worker.naming.pending.done()
        assert worker.shared_handle.active and not worker.parked
    finally:
        execution.cancel()
        await asyncio.gather(execution, return_exceptions=True)
        await session.cleanup()
        worker.activation_gate.reset(token)
