"""Bounded input timings preserve admission, delivery and cancellation behavior."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web import performance_diagnostics
from amplifier_web.performance_diagnostics import PerformanceDiagnostics
from amplifier_web.runtime import RuntimeManager, RuntimeStartupError
from amplifier_web.service import AppError, AppService


@pytest.fixture
def clock(monkeypatch):
    value = [0.0]
    real = performance_diagnostics.time
    # Do not change asyncio's own clock or introduce real multi-second sleeps.
    monkeypatch.setattr(performance_diagnostics, 'time', SimpleNamespace(
        monotonic=lambda: value[0], time=real.time, thread_time=real.thread_time))
    return value


def phases(diagnostics):
    return {name: row for name, row in diagnostics.snapshot()['operations'].items()
            if name.startswith('input.')}


@pytest.mark.parametrize('operation', ['send', 'retry'])
@pytest.mark.parametrize('outcome', ['accepted', 'error', 'cancelled'])
async def test_native_phases_keep_exact_write_and_reply_semantics(clock, operation, outcome):
    diagnostics = PerformanceDiagnostics()
    manager = RuntimeManager(performance_diagnostics=diagnostics)
    session = {'id': 'chat', 'messages': []}
    row = {'process': SimpleNamespace(returncode=None), 'pending': {}, 'inflight': set()}
    writes, guards = [], []

    async def start(source, emit, **kwargs):
        clock[0] += 3
        manager.workers[source['id']] = row

    async def write(worker, payload):
        clock[0] += 5
        writes.append(payload)
        future = worker['pending'][payload['id']]

        def reply():
            clock[0] += 7
            if outcome == 'cancelled':
                future.cancel()
            else:
                worker['inflight'].discard(payload['id'])
                if outcome == 'error':
                    future.set_exception(RuntimeError('private native error'))
                else:
                    future.set_result({'accepted': True})
        asyncio.get_running_loop().call_soon(reply)

    manager.start = start
    manager._write = write
    send = getattr(manager, operation)
    request = send(session, 'private message body', 'private-input', AsyncMock(),
                   admission_check=lambda: guards.append('checked'))
    if outcome == 'accepted':
        assert await request == {'accepted': True}
    else:
        with pytest.raises(asyncio.CancelledError if outcome == 'cancelled' else RuntimeError):
            await request
    assert len(writes) == 1 and writes[0]['op'] == operation
    assert writes[0]['text'] == 'private message body' and writes[0]['input_id'] == 'private-input'
    assert guards and not manager._locks['chat'].locked() and not row['pending']
    # Cancellation after write remains uncertain; instrumentation never replays it.
    assert bool(row['inflight']) is (outcome == 'cancelled')
    assert {name: row['totalMs'] for name, row in phases(diagnostics).items()} == {
        'input.runtime.prepare': 3000, 'input.runtime.admit': 5000,
        'input.runtime.ack': 7000}
    samples = diagnostics.snapshot()['slowOperations']
    assert all(row['sessionId'] == 'chat' and 'threadCpuMs' not in row for row in samples)
    assert 'private' not in json.dumps(diagnostics.snapshot())


async def test_guard_refusal_records_admission_without_write_or_ack(clock):
    diagnostics = PerformanceDiagnostics()
    manager = RuntimeManager(performance_diagnostics=diagnostics)
    manager._write = AsyncMock()

    def guard():
        clock[0] += 2
        raise ValueError('private authority changed')

    with pytest.raises(ValueError, match='authority changed'):
        await manager._request('chat', 'send', guard=guard, text='body', input_id='one')
    manager._write.assert_not_called()
    assert not manager._locks['chat'].locked()
    assert phases(diagnostics) == {
        'input.runtime.admit': {'count': 1, 'totalMs': 2000, 'maxMs': 2000}}


@pytest.mark.parametrize('cancelled', [False, True])
async def test_failed_preparation_preserves_exception_and_does_not_admit(clock, cancelled):
    diagnostics = PerformanceDiagnostics()
    manager = RuntimeManager(performance_diagnostics=diagnostics)
    manager._request = AsyncMock()

    async def start(*args, **kwargs):
        clock[0] += 4
        raise asyncio.CancelledError() if cancelled else ValueError('private preparation error')

    manager.start = start
    with pytest.raises(asyncio.CancelledError if cancelled else RuntimeStartupError):
        await manager.send({'id': 'chat'}, 'body', 'one', AsyncMock())
    manager._request.assert_not_called()
    assert phases(diagnostics) == {
        'input.runtime.prepare': {'count': 1, 'totalMs': 4000, 'maxMs': 4000}}


async def test_warm_preparation_and_uninstrumented_owner_remain_supported(clock):
    diagnostics = PerformanceDiagnostics()
    for installed in (diagnostics, None):
        manager = RuntimeManager(performance_diagnostics=installed)
        manager.start = AsyncMock()
        manager._request = AsyncMock(return_value={'accepted': True})
        assert await manager.send({'id': 'chat'}, 'body', 'one', AsyncMock()) == {'accepted': True}
        assert await manager.retry({'id': 'chat'}, 'body', 'one', AsyncMock()) == {'accepted': True}
    assert phases(diagnostics) == {
        'input.runtime.prepare': {'count': 2, 'totalMs': 0, 'maxMs': 0}}
    assert diagnostics.snapshot()['slowOperations'] == []


async def test_host_send_separates_history_configuration_and_delivery(clock, tmp_path, monkeypatch):
    runtime = SimpleNamespace(send=AsyncMock(return_value={'accepted': True}), close=AsyncMock())
    service = AppService(tmp_path / 'app', runtime, workspace=tmp_path)
    try:
        await service.dispatch('session.create', {'title': 'Synthetic chat'})
        session = service._session()
        history, refresh = service.history.ensure_loaded, service.refresh_configuration
        publish = service._publish_changes

        async def load(sid):
            clock[0] += 2
            return await history(sid)

        async def configuration(sid):
            clock[0] += 3
            return await refresh(sid)

        def publication(**kwargs):
            # Only delivery settlement is detail-only for this unchanged fixture.
            if kwargs.get('detail_only'):
                clock[0] += 5
            return publish(**kwargs)

        monkeypatch.setattr(service.history, 'ensure_loaded', load)
        monkeypatch.setattr(service, 'refresh_configuration', configuration)
        monkeypatch.setattr(service, '_publish_changes', publication)
        result = await service.dispatch('conversation.send',
            {'sessionId': session['id'], 'text': 'private body'}, command_id='private-input')
        assert result['accepted'] and result['delivery'] == 'accepted'
        assert runtime.send.await_count == 1
        saved = next(row for row in session['messages'] if row.get('inputId') == 'private-input')
        assert saved['text'] == 'private body' and saved['delivery']['status'] == 'accepted'
        assert {name: row['totalMs'] for name, row in phases(service.performance_diagnostics).items()} == {
            'input.history': 2000, 'input.configuration': 3000, 'input.delivery.publish': 5000}
        assert 'private' not in json.dumps(service.performance_diagnostics.snapshot())
        # An unrelated read must not acquire input preparation/ACK phases.
        before = phases(service.performance_diagnostics)
        await service.dispatch('conversation.delivery',
            {'sessionId': session['id'], 'inputId': 'private-input'})
        assert phases(service.performance_diagnostics) == before
    finally:
        await service.close()


async def test_history_refusal_is_measured_without_runtime_work(clock, tmp_path, monkeypatch):
    runtime = SimpleNamespace(send=AsyncMock(), close=AsyncMock())
    service = AppService(tmp_path / 'app', runtime, workspace=tmp_path)
    try:
        await service.dispatch('session.create', {'title': 'Synthetic chat'})

        async def load(sid):
            clock[0] += 2
            raise ValueError('private history refusal')

        monkeypatch.setattr(service.history, 'ensure_loaded', load)
        with pytest.raises(AppError, match='history refusal'):
            await service.dispatch('conversation.send', {'text': 'private body'})
        runtime.send.assert_not_called()
        assert phases(service.performance_diagnostics) == {
            'input.history': {'count': 1, 'totalMs': 2000, 'maxMs': 2000}}
    finally:
        await service.close()
