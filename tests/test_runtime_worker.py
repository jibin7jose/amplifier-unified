"""Worker-stamped app arguments cross the service boundary, not just query_history."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from jsonschema import ValidationError

from amplifier_web.collaboration import BINDING, PRINCIPAL
from amplifier_web.runtime_worker import Worker
from amplifier_web.service import AppError, AppService


TRANSPORT_FIELDS = {'_generationId', '_runtimeSessionId', '_inputBindings', '_inputClients'}


def coordinator(session_id):
    capabilities = {}
    return SimpleNamespace(session_id=session_id, get_capability=capabilities.get,
                           register_capability=capabilities.__setitem__,
                           cancellation=SimpleNamespace(is_cancelled=False))


@pytest.fixture
async def worker_bridge(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'amplifier'))
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    root = app._new_session({'title': 'Earlier decision', 'workspace': str(tmp_path)})
    root['runtimeSessionId'] = 'native-root'
    root['collaborationGeneration'] = {'id': 'generation-root', 'inputIds': ['input-1']}
    root['messages'] = [{'id': 'message-1', 'role': 'user', 'text': 'A retained needle decision'}]
    app.state['sessions'].append(root)
    app.state['selectedSessionId'] = root['id']
    for client in ('old-browser', 'input-browser'):
        app.clients.attach(client)
    monkeypatch.setattr(app.history, 'refresh_catalog', AsyncMock())
    worker = Worker()
    worker.session = SimpleNamespace(coordinator=coordinator('native-root'))
    worker.runtime = SimpleNamespace(generation={'id': 'generation-root', 'input_ids': ['input-1']},
                                     closed=False, stop_requested=False)
    worker.activation_gate = SimpleNamespace(current_valid=True)
    worker.context_inputs = ['input-1']
    worker.context_bindings = {
        'input-1': {'clientId': 'input-browser', 'targets': [{'surfaceId': 'surface-1'}]}}
    envelopes = []

    async def transport(operation, args):
        envelopes.append(deepcopy(args))
        # A callback may have inherited an older browser request's context.
        with app.clients.bind('old-browser'):
            return await app.app_bridge(operation, args, root['id'])

    worker.bridge = transport
    try:
        yield app, root, worker, envelopes
    finally:
        await app.close()


@pytest.mark.parametrize('action', ['list', 'search', 'read'])
async def test_actual_worker_history_accepts_transport_but_validates_public_args(worker_bridge, monkeypatch, action):
    from amplifier_web import history_query
    app, root, worker, envelopes = worker_bridge
    public = {'action': action, 'limit': 1}
    if action == 'search':
        public['query'] = 'needle'
    elif action == 'read':
        public['session_id'] = root['id']
    before = deepcopy(root)
    query = history_query.query_history

    async def inspect(service, args, caller):
        assert args == public
        assert not TRANSPORT_FIELDS.intersection(args)
        assert PRINCIPAL.get() == 'native-root'
        assert BINDING.get() == envelopes[-1]
        binding, active = service.collaboration.active_binding(root)
        assert binding['_generationId'] == active['id'] == 'generation-root'
        assert service.clients.current.get() == 'input-browser'
        return await query(service, args, caller)

    monkeypatch.setattr(history_query, 'query_history', inspect)
    forged = {'_runtimeSessionId': 'foreign-root', '_generationId': 'foreign-generation',
              '_inputBindings': [{'inputId': 'forged', 'clientId': 'old-browser'}],
              '_inputClients': ['old-browser']}
    supplied = {**public, **forged}
    result = await worker.app_access_bridge(worker.session.coordinator)('history', supplied)
    assert envelopes[-1] == {
        **public, '_runtimeSessionId': 'native-root', '_generationId': 'generation-root',
        '_inputBindings': [{'inputId': 'input-1', 'clientId': 'input-browser'}],
        '_inputClients': ['input-browser']}
    if action == 'read':
        assert result['messages'][0]['text'] == 'A retained needle decision'
    else:
        assert result['items'][0]['id'] == 'native-root'
        if action == 'search':
            assert result['items'][0]['matches'][0]['message_id'] == 'message-1'
    assert supplied == {**public, **forged}
    assert root == before
    assert PRINCIPAL.get() is None and BINDING.get() is None
    assert app.clients.current.get() is None


@pytest.mark.parametrize('extra', ['unexpected', '_unrecognizedTransport', '_contextBindings'])
async def test_actual_worker_history_still_rejects_unknown_public_and_private_fields(worker_bridge, extra):
    app, root, worker, envelopes = worker_bridge
    with pytest.raises(ValidationError, match='Additional properties'):
        await worker.app_access_bridge(worker.session.coordinator)('history', {'action': 'list', extra: []})
    assert extra in envelopes[-1]
    assert PRINCIPAL.get() is None and BINDING.get() is None
    assert app.clients.current.get() is None


async def test_worker_dispatch_keeps_observation_provenance_outside_public_args(worker_bridge, monkeypatch):
    app, root, worker, envelopes = worker_bridge
    expected = [{'inputId': 'input-1', 'clientId': 'input-browser'}]
    original = app._app_bridge

    async def inspect(operation, args, session_id, **options):
        assert args == {'action': 'observation.preview', 'args': {}}
        assert options['input_origin'] == {'clientId': 'input-browser', 'status': 'client'}
        return await original(operation, args, session_id, **options)

    async def dispatch(action, args, **options):
        assert action == 'observation.preview'
        assert options['origin'] == 'agent' and options['caller_session_id'] == root['id']
        assert app.observations.input_bindings.get() == expected
        assert BINDING.get()['_inputBindings'] == expected
        assert PRINCIPAL.get() == 'native-root'
        return {'accepted': True, 'result': {'preview': True}}

    monkeypatch.setattr(app, '_app_bridge', inspect)
    monkeypatch.setattr(app, 'dispatch', AsyncMock(side_effect=dispatch))
    previous = app.observations.input_bindings.get()
    result = await worker.app_access_bridge(worker.session.coordinator)(
        'dispatch', {'action': 'observation.preview', 'args': {}})
    assert result['result']['preview'] is True
    assert app.observations.input_bindings.get() == previous
    assert PRINCIPAL.get() is None and BINDING.get() is None


@pytest.mark.parametrize('operation', ['context.manifest', 'context.prepare'])
async def test_context_operations_retain_worker_specific_bindings(worker_bridge, monkeypatch, operation):
    app, root, worker, envelopes = worker_bridge
    seen = []

    def manifest(session_id, bindings):
        assert session_id == root['id']
        seen.append(deepcopy(bindings))
        return {'surfaces': []}

    def read(session_id, args, bindings):
        assert session_id == root['id']
        assert args['surfaceId'] == 'surface-1'
        assert args['_contextInputs'] == ['input-1']
        assert args['_contextBindings'] == bindings
        assert not TRANSPORT_FIELDS.intersection(args)
        return {'surfaceId': args['surfaceId']}

    monkeypatch.setattr(app.surface_context, 'manifest', manifest)
    monkeypatch.setattr(app.surface_context, 'read', read)
    bridge = worker.app_access_bridge(worker.session.coordinator)
    result = await bridge(operation, {'_contextInputs': ['forged'],
                                    '_contextBindings': [{'clientId': 'forged'}]})
    if operation == 'context.prepare':
        assert result['context.manifest']['available'] is True
        result = result['context.manifest']['value']
    assert result['inputIds'] == ['input-1']
    assert seen[-1] == [worker.context_bindings['input-1']]
    assert (await bridge('context.read', {'surfaceId': 'surface-1'}))['surfaceId'] == 'surface-1'
    # Delegates freeze assignment and narrow surface targets; do not remove
    # these context-specific fields along with the common transport envelope.
    child = worker.app_access_bridge(coordinator('native-child'))
    worker.context_inputs = ['later-input']
    worker.context_bindings['later-input'] = {'clientId': 'old-browser', 'targets': []}
    result = await child(operation, {})
    if operation == 'context.prepare':
        assert result['context.manifest']['available'] is True
        result = result['context.manifest']['value']
    assert result['inputIds'] == ['input-1']
    assert seen[-1] == [{'clientId': 'input-browser', 'targets': []}]


async def test_child_transport_keeps_assigned_generation_without_borrowing_root_principal(worker_bridge, monkeypatch):
    app, root, worker, envelopes = worker_bridge
    child = worker.app_access_bridge(coordinator('native-child'))
    worker.context_inputs = ['later-input']
    worker.context_bindings.clear()

    async def inspect(operation, args, session_id, **options):
        assert not TRANSPORT_FIELDS.intersection(args)
        assert PRINCIPAL.get() == 'native-child'
        assert BINDING.get()['_generationId'] == 'generation-root'
        binding, active = app.collaboration.active_binding(root, allow_descendant=True)
        assert binding['_runtimeSessionId'] == 'native-child'
        assert active['id'] == 'generation-root'
        assert BINDING.get()['_inputBindings'] == [{'inputId': 'input-1', 'clientId': 'input-browser'}]
        assert app.clients.current.get() == 'input-browser'
        app.collaboration.source({}, 'agent', session_id)

    monkeypatch.setattr(app, '_app_bridge', inspect)
    with pytest.raises(AppError, match="child cannot borrow"):
        await child('list_actions', {'_generationId': 'generation-root', '_runtimeSessionId': 'native-root'})
    assert PRINCIPAL.get() is None and BINDING.get() is None
    assert app.clients.current.get() is None

@pytest.mark.parametrize('operation,args,deadline', [
    ('dispatch', {'action': 'coordination.wait', 'args': {'waitMs': 60000, 'secret': 'private'}}, 65),
    ('work.effect.check', {'toolName': 'probe', 'toolInput': {'secret': 'private'}}, 60),
])
async def test_bridge_timeout_emits_bounded_correlated_diagnostic(monkeypatch, operation, args, deadline):
    monkeypatch.setattr('amplifier_web.performance_diagnostics.TIMING_ENABLED', True)
    import asyncio
    from amplifier_web import runtime_worker
    worker = runtime_worker.Worker()
    worker.runtime = SimpleNamespace(generation={'id': 'g'})
    emitted = []
    monkeypatch.setattr(runtime_worker, 'publish', emitted.append)
    async def expire(future, timeout):
        assert timeout == deadline
        future.cancel()
        raise TimeoutError()
    monkeypatch.setattr(asyncio, 'wait_for', expire)
    with pytest.raises(TimeoutError, match='App bridge response timed out'):
        await worker.bridge(operation, args)
    diagnostics = [row for row in emitted if row.get('type') == 'runtime.bridge']
    assert [row['phase'] for row in diagnostics] == ['requested', 'timeout']
    assert diagnostics[0]['bridgeId'] == diagnostics[1]['bridgeId']
    assert diagnostics[1]['timeoutSeconds'] == deadline
    assert diagnostics[1]['generation_id'] == 'g'
    assert 'private' not in str(diagnostics)
    assert not worker.bridges
    timings = [row['timing'] for row in emitted if row.get('type') == 'runtime.timing']
    assert len(timings) == 1 and timings[0]['outcome'] == 'failed'
    assert timings[0]['bridgeId'] == diagnostics[0]['bridgeId']
    assert 'private' not in str(timings) and not worker.bridge_timings


@pytest.mark.parametrize('outcome', ['completed', 'failed', 'cancelled'])
async def test_bridge_phase_timing_preserves_results_and_task_identity(monkeypatch, outcome):
    monkeypatch.setattr('amplifier_web.performance_diagnostics.TIMING_ENABLED', True)
    import asyncio
    from amplifier_web import runtime_worker
    from amplifier_web.performance_diagnostics import tool_scope, active_tool
    worker = Worker()
    c = coordinator('native')
    worker.runtime = SimpleNamespace(generation={'id': 'generation'})
    emitted = []
    c.register_capability('web.performance.emit', lambda row: emitted.append({'type': 'tool-timing', 'timing': row}))

    async def invoke(call):
        task = asyncio.current_task()
        c._tool_dispatch_contexts[task] = {'tool_call_id': call}
        try:
            with tool_scope(c, 'probe'):
                assert active_tool(c).facts['toolCallId'] == call
                async def inherited():
                    assert active_tool(c) is None
                await asyncio.create_task(inherited())
                if outcome == 'cancelled':
                    pending = asyncio.create_task(worker.bridge('history', {'secret': 'private'}))
                    await asyncio.sleep(0)
                    pending.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await pending
                elif outcome == 'failed':
                    with pytest.raises(RuntimeError, match='controlled'):
                        await worker.bridge('history', {'secret': 'private'})
                else:
                    assert await worker.bridge('history', {'secret': 'private'}) == {'exact': call}
        finally:
            c._tool_dispatch_contexts.pop(task)

    def publish(row):
        emitted.append(row)
        if row.get('op') == 'bridge' and outcome != 'cancelled':
            # Reply delivery is asynchronous, just as the real protocol reader.
            call = row['_timing'].get('toolCallId')
            reply = {'op': 'bridge.result', 'id': row['id'],
                     **({'error': 'controlled'} if outcome == 'failed' else {'result': {'exact': call}})}
            asyncio.create_task(worker._command_serial(reply))
    monkeypatch.setattr(runtime_worker, 'publish', publish)
    c._tool_dispatch_contexts = {}
    await asyncio.gather(invoke('call-a'), invoke('call-b'))
    timings = [row['timing'] for row in emitted if row.get('type') == 'runtime.timing']
    assert len(timings) == 2 and len({row['bridgeId'] for row in timings}) == 2
    assert all(row['outcome'] == outcome for row in timings)
    assert 'private' not in str(timings)
    if outcome != 'cancelled':
        assert {row['toolCallId'] for row in timings} == {'call-a', 'call-b'}
        assert all(row['marks']['write.start'] <= row['marks']['write.end'] <= row['marks']['received'] <= row['marks']['end'] for row in timings)
    else:
        assert all('toolCallId' not in row for row in timings), 'Inherited task context must not manufacture a join'
    assert not worker.bridges and not worker.bridge_timings and active_tool(c) is None


async def test_bridge_cleanup_precedes_disposable_timing_and_probes_off(monkeypatch):
    monkeypatch.setattr('amplifier_web.performance_diagnostics.TIMING_ENABLED', True)
    import asyncio
    from amplifier_web import runtime_worker, performance_diagnostics
    worker = Worker()
    events = []
    def publish(row):
        events.append(row)
        if row.get('type') == 'runtime.timing':
            assert not worker.bridges and not worker.bridge_timings
        if row.get('op') == 'bridge':
            asyncio.create_task(worker._command_serial({'op': 'bridge.result', 'id': row['id'], 'result': {'ok': True}}))
    monkeypatch.setattr(runtime_worker, 'publish', publish)
    assert await worker.bridge('capacity.admit', {'waitForCapacity': True}) == {'ok': True}
    assert next(i for i, r in enumerate(events) if r.get('op') == 'bridge.received') < next(
        i for i, r in enumerate(events) if r.get('type') == 'runtime.timing')
    events.clear()
    monkeypatch.setattr(performance_diagnostics, 'TIMING_ENABLED', False)
    assert await worker.bridge('history', {}) == {'ok': True}
    assert all('_timing' not in r and r.get('type') != 'runtime.timing' for r in events)


@pytest.mark.parametrize('value,enabled', [(None, False), ('0', False), ('1', True)])
def test_phase_timing_startup_is_explicit_opt_in(value, enabled):
    import os
    from pathlib import Path
    import subprocess
    import sys
    env = dict(os.environ)
    env.pop('AMPLIFIER_RUNTIME_TIMING', None)
    if value is not None:
        env['AMPLIFIER_RUNTIME_TIMING'] = value
    result = subprocess.run([sys.executable, '-B', '-c',
        'from amplifier_web.performance_diagnostics import TIMING_ENABLED; print(TIMING_ENABLED)'],
        cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(enabled)
