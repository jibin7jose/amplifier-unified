"""Opt-in worker phase evidence never changes requests or outcomes."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from amplifier_web import performance_diagnostics as timings


def coordinator(emit):
    capabilities = {'web.performance.emit': emit,
                    'web.performance.scope': lambda: {'generationId': 'generation-1'}}
    return SimpleNamespace(session_id='session-1', get_capability=capabilities.get)


@pytest.mark.parametrize('outcome', ['completed', 'failed', 'cancelled'])
async def test_preparation_preserves_exact_result_failure_and_cancellation(monkeypatch, outcome):
    monkeypatch.setattr(timings, 'TIMING_ENABLED', True)
    rows = []
    result = object()
    error = asyncio.CancelledError('private') if outcome == 'cancelled' else ValueError('private')
    async def operation(value, *, option):
        assert value is result and option == 'secret prompt'
        if outcome != 'completed':
            raise error
        return value
    observed = timings.time_preparation(coordinator(rows.append), 'context.view', operation)
    if outcome == 'completed':
        assert await observed(result, option='secret prompt') is result
    else:
        with pytest.raises(type(error)) as caught:
            await observed(result, option='secret prompt')
        assert caught.value is error
    assert len(rows) == 1 and rows[0]['outcome'] == outcome
    assert rows[0]['operation'] == 'context.view'
    assert rows[0]['generationId'] == 'generation-1'
    assert rows[0]['marks']['start'] <= rows[0]['marks']['end']
    assert 'private' not in json.dumps(rows) and 'secret' not in json.dumps(rows)


async def test_default_off_does_not_install_wrappers_hooks_or_inspect_coordinator(monkeypatch):
    monkeypatch.setattr(timings, 'TIMING_ENABLED', False)
    class Unavailable:
        def __getattr__(self, name):raise AssertionError(name)
    async def original(value):return value
    assert timings.time_preparation(Unavailable(), 'context.view', original) is original
    timings.install_preparation_timing(Unavailable())
    from amplifier_web.surface_delivery import SurfaceProvider
    wrapped = SurfaceProvider(object(), object(), coordinator=Unavailable())
    assert wrapped.timing_coordinator is None and '_prepare' not in vars(wrapped)


async def test_failed_observer_does_not_replace_success(monkeypatch):
    monkeypatch.setattr(timings, 'TIMING_ENABLED', True)
    def failed(value):raise ValueError('observer-only')
    async def original():return 'unchanged'
    assert await timings.time_preparation(coordinator(failed), 'provider.budget', original)() == 'unchanged'


async def test_read_only_context_keeps_supported_behavior(monkeypatch):
    monkeypatch.setattr(timings, 'TIMING_ENABLED', True)
    async def view():return ['unchanged']
    class Context:
        @property
        def get_messages_for_request(self):return view
    context = Context()
    capabilities = {}
    hooks = []
    c = SimpleNamespace(session_id='native', get_capability=capabilities.get,
        register_capability=capabilities.__setitem__, get=lambda _: context,
        hooks=SimpleNamespace(register=lambda *a, **k: hooks.append((a, k))))
    timings.install_preparation_timing(c)
    assert await context.get_messages_for_request() == ['unchanged']
    assert len(hooks) == 2


def test_host_receipt_adds_separate_clock_fence_and_keeps_ring_bounded(monkeypatch):
    monkeypatch.setattr(timings, 'clock_domain', lambda: 'a'*64)
    monkeypatch.setattr(timings.time, 'monotonic_ns', lambda: 40)
    diagnostics = timings.PerformanceDiagnostics()
    row = {'kind': 'preparation', 'operation': 'context.view', 'clockDomain': 'b'*64,
           'marks': {'start': 10, 'end': 20}, 'payload': 'private prompt'}
    for _ in range(125):
        diagnostics.record_runtime_timing('owner', row)
    retained = diagnostics.snapshot()['runtimeTimings']
    assert len(retained) == 120
    assert retained[0]['clockDomain'] == 'b'*64
    assert retained[0]['hostClockDomain'] == 'a'*64
    assert retained[0]['marks'] == {'start': 10, 'end': 20, 'host.observed': 40}
    assert row['marks'] == {'start': 10, 'end': 20} and 'private' not in json.dumps(retained)
    assert 'operation' not in timings.timing_metadata({**row, 'operation': 'private-content'})


async def test_actual_native_loop_observes_preparation_without_request_changes(monkeypatch):
    monkeypatch.setattr(timings, 'TIMING_ENABLED', True)
    from test_pretransport_refit_native import native_case
    from amplifier_web.surface_delivery import SurfaceProvider
    session, state = await native_case('stale_visual', preparation_context=True, full_access=True)
    c = session.coordinator
    rows = []
    c.register_capability('web.performance.emit', rows.append)
    c.register_capability('web.performance.scope', lambda: {'generationId': 'native-generation'})
    async def factory():return 'Synthetic stable factory guidance.'
    await c.get('context').set_system_prompt_factory(factory)
    timings.install_preparation_timing(c)
    first_view = c.get('context').get_messages_for_request
    timings.install_preparation_timing(c)
    assert c.get('context').get_messages_for_request is first_view
    original = c.get('providers')['fixture']
    await c.mount('providers', SurfaceProvider(original.original, original.delivery,
                                              coordinator=c), name='fixture')
    try:
        assert await session.execute('Read once, then finish.') == 'Done'
        assert state.tools == 1 and len(state.transports) == 2
        assert state.transports[1] == state.measurements[2]
        assert 'c3ludGhldGlj' not in state.transports[1]
        phases = {r['operation'] for r in rows}
        assert phases == {'provider.hooks', 'context.view', 'context.factory',
                          'surface.prepare', 'provider.budget', 'provider.refit'}
        hooks = [r for r in rows if r['operation'] == 'provider.hooks']
        assert len(hooks) == 4
        assert [r['iteration'] for r in hooks] == [1, 1, 2, 2]
        assert [set(r['marks']) for r in hooks] == [{'start'}, {'end'}]*2
        assert all(r['marks']['end'] >= r['marks']['start']
                   for r in rows if r['operation'] != 'provider.hooks')
        assert all(r['runtimeSessionId'] == session.session_id for r in rows)
        assert 'Read once' not in json.dumps(rows) and 'Synthetic stable' not in json.dumps(rows)
    finally:
        await session.cleanup()
