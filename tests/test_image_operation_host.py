"""Optional provider-image calls keep host admission, identity and safe telemetry."""
import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from amplifier_web.execution_events import (
    ADMITTED_CALL, CALL_PURPOSE, CURRENT_CALL, CURRENT_PROVIDER, ExecutionEvents, public_usage,
)
from amplifier_web.host.session import SelectedProvider
from amplifier_web.runtime import normalize_event
from test_assignments import app, start


class Copyable(SimpleNamespace):
    def model_copy(self, *, update):
        return type(self)(**{**copy.deepcopy(vars(self)), **update})


@pytest.fixture(params=[False, True], ids=['mutable', 'immutable'])
def image_provider(request):
    state = SimpleNamespace(image_calls=[], chat_calls=[], contexts=[], on_image=None,
                            error=None, result={'status': 'completed'})

    class Provider:
        __slots__ = ()

        def get_info(self):
            return Copyable(id='fixture-account', defaults={'model': 'default-chat'})

        async def complete(self, request, **kwargs):
            state.chat_calls.append((request, kwargs))
            return SimpleNamespace(usage={'input_tokens': 2, 'output_tokens': 1, 'cost_usd': '0.01'})

        async def generate_image(self, request, *, plan):
            state.image_calls.append((request, plan))
            state.contexts.append((copy.deepcopy(CALL_PURPOSE.get()), CURRENT_CALL.get()))
            if state.on_image is not None:
                await state.on_image()
            if state.error is not None:
                raise state.error
            return state.result

    class MutableProvider(Provider):
        pass

    state.original = Provider() if request.param else MutableProvider()
    return state


def image_request():
    return {'prompt': 'hidden-prompt', 'images': [{'bytes': b'hidden-input-bytes'}]}


def image_plan():
    return {'model': 'image-fixture', 'plan_id': 'hidden-plan-token'}


def public_projection(events, emitted):
    normalized = [normalize_event(event, events.root_id)[1] for event in emitted]
    # This also rejects accidentally retained raw bytes, not just known strings.
    return json.dumps({'emitted': emitted, 'normalized': normalized,
                       'usage': events.usage()}, sort_keys=True)


@pytest.mark.parametrize('immutable', [False, True], ids=['mutable', 'immutable'])
@pytest.mark.parametrize('exposure', ['absent', 'none', 'noncallable'])
def test_instrumentation_does_not_invent_optional_image_support(immutable, exposure):
    class ChatProvider:
        __slots__ = ()

        def get_info(self):
            return Copyable(id='chat-only', defaults={'model': 'chat-fixture'})

        async def complete(self, request):
            return SimpleNamespace(usage={})

    attrs = {'__slots__': ()} if immutable else {}
    if exposure != 'absent':
        attrs['generate_image'] = None if exposure == 'none' else 'not-a-method'
    native = type('OptionalProvider', (ChatProvider,), attrs)()
    events = ExecutionEvents('root', lambda event: None)
    observed = events.instrument_provider('root', native)
    selected = SelectedProvider(observed, {'model': 'root-chat'})
    for value in (observed, selected):
        assert not callable(getattr(value, 'generate_image', None))
        if exposure == 'absent':
            assert not hasattr(value, 'generate_image')
        else:
            assert value.generate_image == attrs['generate_image']
    assert events.nodes == {}


@pytest.mark.asyncio
async def test_callable_image_method_is_observed_once_and_forwards_exact_objects(image_provider):
    state = image_provider
    events = ExecutionEvents('root', lambda event: None)
    provider = events.instrument_provider('root', state.original)
    assert callable(provider.generate_image)
    assert events.instrument_provider('root', state.original) is provider
    assert events.instrument_provider('root', provider) is provider
    request, plan = image_request(), image_plan()
    before = copy.deepcopy((request, plan))
    assert await provider.generate_image(request, plan=plan) is state.result
    assert len(state.image_calls) == 1 and state.chat_calls == []
    assert state.image_calls[0][0] is request and state.image_calls[0][1] is plan
    assert (request, plan) == before
    assert events.usage()['calls'] == 1
    row = next(iter(events.nodes.values()))
    assert row['phase'] == 'completed' and row['model'] == 'image-fixture'
    assert row['purpose'] == 'image_generation' and row['label'] == 'Image generation'
    assert CALL_PURPOSE.get() is None
    assert CURRENT_CALL.get() is None and CURRENT_PROVIDER.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['unknown', 'blocked', 'failed'])
@pytest.mark.parametrize('normalized', [None, {'input_tokens': 11, 'output_tokens': 7, 'cost_usd': '0.03'}])
async def test_noncompleted_image_evidence_never_emits_completed(image_provider, status, normalized):
    state = image_provider
    state.result = {'status': status, 'error': 'hidden-native-error',
                    'continuation': 'hidden-continuation', 'images': [{'bytes': b'hidden-output-bytes'}],
                    'usage': {'normalized': normalized,
                              'provider': {'input_tokens': 99999, 'cost_usd': 999,
                                           'raw': 'hidden-provider-usage'}}}
    emitted = []
    events = ExecutionEvents('root', emitted.append)
    provider = events.instrument_provider('root', state.original)
    result = await provider.generate_image(image_request(), plan=image_plan())
    assert result is state.result and result['status'] == status
    assert len(state.image_calls) == 1 and state.chat_calls == []
    assert len(events.nodes) == 1
    row = next(iter(events.nodes.values()))
    assert row['phase'] not in {'completed', 'running', 'retrying'}
    assert row['endedAt'] is not None
    assert all(event['event']['phase'] != 'completed' for event in emitted)
    expected_usage = public_usage(state.result['usage'])
    assert row['usage'] == expected_usage
    assert events.usage()['calls'] == 1
    assert events.usage()['usage']['costUsd'] == (None if normalized is None else 0.03)
    if normalized is not None:
        assert events.usage()['usage']['totalTokens'] == 18
    assert 'hidden-' not in public_projection(events, emitted)
    assert CALL_PURPOSE.get() is None
    assert CURRENT_CALL.get() is None and CURRENT_PROVIDER.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize('sid', ['root', 'child'])
async def test_image_admission_denial_precedes_any_provider_invocation(image_provider, sid):
    state = image_provider
    events = ExecutionEvents('root', lambda event: None)
    events.turn_id = 'owning-turn'
    if sid == 'child':
        events.lifecycle({'type': 'child.updated', 'sessionId': sid,
                          'parentSessionId': 'root', 'status': 'running'})
    before = copy.deepcopy(events.nodes)
    admissions = []

    async def deny(row):
        admissions.append(copy.deepcopy(row))
        raise PermissionError('fixture capacity denied')

    events.admission_guard = deny
    provider = events.instrument_provider(sid, state.original)
    with pytest.raises(PermissionError, match='fixture capacity denied'):
        await provider.generate_image(image_request(), plan=image_plan())
    assert state.image_calls == [] and state.chat_calls == []
    assert events.nodes == before
    assert len(admissions) == 1
    assert admissions[0]['sessionId'] == sid and admissions[0]['turnId'] == 'owning-turn'
    assert admissions[0]['model'] == 'image-fixture'
    assert admissions[0]['purpose'] == 'image_generation'
    assert 'hidden-' not in json.dumps(admissions)
    assert CALL_PURPOSE.get() is None
    assert CURRENT_CALL.get() is None and CURRENT_PROVIDER.get() is None


@pytest.mark.asyncio
async def test_image_usage_is_normalized_once_and_separate_from_chat_without_payloads(image_provider):
    state = image_provider
    state.result = {'status': 'completed', 'images': [{'bytes': b'hidden-output-bytes'}],
                    'continuation': 'hidden-continuation', 'usage': {
                        'normalized': {'input_tokens': 11, 'output_tokens': 7, 'cost_usd': '0.03'},
                        'provider': {'input_tokens': 99999, 'cost_usd': 999,
                                     'raw': 'hidden-provider-usage'},
                    }}
    emitted = []
    events = ExecutionEvents('root', emitted.append)
    events.turn_id = 'owning-turn'

    async def hooks():
        # Native hooks may use other tasks; they must not add duplicate calls or
        # publish their unnormalized usage and private response fields.
        async def send(name, data):
            events.hook('root', name, data)
        await asyncio.create_task(send('llm:request', {'raw': 'hidden-native-request'}))
        await asyncio.create_task(send('llm:response', {
            'usage': {'input_tokens': 99999, 'cost_usd': 999}, 'raw': 'hidden-native-response'}))

    state.on_image = hooks
    provider = events.instrument_provider('root', state.original)
    await provider.generate_image(image_request(), plan=image_plan())
    await provider.complete(Copyable(model='controller-chat', metadata={}))
    rows = list(events.nodes.values())
    assert len(rows) == 2 and len(state.image_calls) == len(state.chat_calls) == 1
    image, chat = rows
    assert image['purpose'] == 'image_generation' and image['label'] == 'Image generation'
    assert chat.get('purpose') is None and chat['label'] == 'Model call'
    assert image['model'] == 'image-fixture' and chat['model'] == 'controller-chat'
    assert image['turnId'] == chat['turnId'] == 'owning-turn'
    assert image['usage'] == {'inputTokens': 11, 'outputTokens': 7, 'totalTokens': 18,
                              'grossInputTokens': 11, 'grossTotalTokens': 18,
                              'costUsd': 0.03, 'costType': 'reported'}
    public_image = normalize_event({'type': 'execution.event', 'event': image}, 'root')[1]
    public_chat = normalize_event({'type': 'execution.event', 'event': chat}, 'root')[1]
    assert public_image['label'] == 'Image generation' and public_chat['label'] == 'Model call'
    assert public_image['usage'] == image['usage']
    assert events.usage()['calls'] == 2
    assert events.usage()['usage']['totalTokens'] == 21
    assert events.usage()['usage']['costUsd'] == pytest.approx(0.04)
    assert 'hidden-' not in public_projection(events, emitted)
    assert state.contexts[0][0]['purpose'] == 'image_generation'
    assert state.contexts[0][1] == image['id']
    assert CALL_PURPOSE.get() is None


@pytest.mark.asyncio
async def test_missing_normalized_usage_is_unavailable_not_native_cost_or_zero(image_provider):
    state = image_provider
    usage = {'normalized': None, 'provider': {'cost_usd': 9, 'input_tokens': 99}}
    assert public_usage(usage) == {'costType': 'unavailable'}
    state.result = {'status': 'completed', 'usage': usage}
    events = ExecutionEvents('root', lambda event: None)
    provider = events.instrument_provider('root', state.original)
    await provider.generate_image(image_request(), plan=image_plan())
    assert next(iter(events.nodes.values()))['usage'] == {'costType': 'unavailable'}
    assert events.usage()['usage']['costUsd'] is None
    assert events.usage()['usage']['costType'] == 'unavailable'


@pytest.mark.asyncio
@pytest.mark.parametrize('cancelled', [False, True], ids=['error', 'cancelled'])
async def test_image_failure_restores_enclosing_purpose_without_retry_or_payload_leak(image_provider, cancelled):
    state = image_provider
    state.error = asyncio.CancelledError('hidden-cancellation') if cancelled else RuntimeError('hidden-provider-error')
    emitted = []
    events = ExecutionEvents('root', emitted.append)
    provider = events.instrument_provider('root', state.original)
    enclosing = {'label': 'Background fixture', 'purpose': 'fixture',
                 'turnId': 'earlier-turn', 'lifecycle': 'background'}
    token = CALL_PURPOSE.set(enclosing)
    try:
        with pytest.raises(type(state.error)):
            await provider.generate_image(image_request(), plan=image_plan())
        assert CALL_PURPOSE.get() is enclosing
    finally:
        CALL_PURPOSE.reset(token)
    assert len(state.image_calls) == 1 and state.chat_calls == []
    row = next(iter(events.nodes.values()))
    assert row['phase'] == ('cancelled' if cancelled else 'error')
    assert row['purpose'] == 'image_generation' and row['turnId'] == 'earlier-turn'
    assert row['lifecycle'] == 'background'
    assert all(event['event']['phase'] != 'completed' for event in emitted)
    assert 'hidden-' not in public_projection(events, emitted)
    assert CURRENT_CALL.get() is None and CURRENT_PROVIDER.get() is None


@pytest.mark.asyncio
async def test_root_chat_selection_does_not_replace_image_model_or_image_plan(image_provider):
    state = image_provider
    events = ExecutionEvents('root', lambda event: None)
    observed = events.instrument_provider('root', state.original)
    selection = {'instance': 'fixture-account', 'model': 'selected-chat', 'effort': 'high'}
    selected = SelectedProvider(observed, copy.deepcopy(selection))
    request, plan = image_request(), image_plan()
    before = copy.deepcopy((request, plan))
    assert selected.default_model == 'selected-chat'
    assert await selected.generate_image(request, plan=plan) is state.result
    assert (request, plan) == before and selected.selection == selection
    assert state.image_calls[0][0] is request and state.image_calls[0][1] is plan
    assert state.chat_calls == []
    image = next(iter(events.nodes.values()))
    assert image['model'] == 'image-fixture' and image['purpose'] == 'image_generation'

    chat = Copyable(model='original-chat', metadata={}, reasoning_effort=None, max_output_tokens=64)
    await selected.complete(chat)
    assert chat.model == 'original-chat' and chat.reasoning_effort is None
    received, kwargs = state.chat_calls[0]
    assert received.model == 'selected-chat' and received.reasoning_effort == 'high'
    assert kwargs['model'] == 'selected-chat' and kwargs['reasoning_effort'] == 'high'
    assert list(events.nodes.values())[-1]['model'] == 'selected-chat'
    assert image['model'] == 'image-fixture'
    assert len(state.image_calls) == 1 and events.usage()['calls'] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("denied", [False, True])
async def test_legacy_backend_uses_same_image_admission_and_usage_boundary(denied):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    events = ExecutionEvents('root', lambda event: None)
    async def admission(row):
        assert row['model'] == 'legacy-image'
        if denied:
            raise PermissionError('budget denied')
    events.admission_guard = admission
    backend = SimpleNamespace(
        describe=lambda: {'provider': 'fixture', 'model': 'legacy-image'},
        generate=AsyncMock(return_value={'data': b'not logged', 'usage': {'input_tokens': 3}}))
    arguments = {'prompt': 'private prompt', 'images': []}
    if denied:
        with pytest.raises(PermissionError):
            await events.image_backend_call('root', backend, arguments)
        backend.generate.assert_not_called()
    else:
        await events.image_backend_call('root', backend, arguments)
        backend.generate.assert_awaited_once_with(**arguments)
        row = next(iter(events.nodes.values()))
        assert row['purpose'] == 'image_generation' and row['usage']['inputTokens'] == 3
        assert 'private prompt' not in repr(events.nodes) and 'not logged' not in repr(events.nodes)


@pytest.mark.asyncio
@pytest.mark.parametrize('dispatch', ['routed', 'legacy'])
async def test_image_dispatch_retains_child_run_and_rejects_replaced_coordinator(
        app, image_provider, monkeypatch, dispatch):
    """Exercise installed wrappers and both worker/service admission boundaries."""
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.runtime_worker import Worker
    from test_task_continuity import controls

    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(app.data_dir))
    await start(app)
    source = app.state['sessions'][0]
    sid = source['id']
    generation = source['collaborationGeneration']
    registry = SimpleNamespace(rows={}, sessions={})
    root_capabilities = {'live.children': registry}
    worker = Worker()
    worker.session = SimpleNamespace(session_id=sid, coordinator=SimpleNamespace(
        session_id=sid, get_capability=root_capabilities.get))
    worker.runtime = SimpleNamespace(session_id=sid, closed=False, stop_requested=False,
        generation={'id': generation['id'], 'input_ids': list(generation['inputIds'])})
    worker.activation_gate = SimpleNamespace(current_valid=True)
    manager = RuntimeManager()
    manager.app_bridge = app.app_bridge
    owner = {'process': SimpleNamespace(returncode=None), 'runtimeOwnerId': 'image-fixture-owner'}
    manager.workers[sid] = owner
    app.runtime.workers = manager.workers
    bridges, admissions, effects = [], [], []

    async def bridge(operation, args):
        replies = []
        async def write(row, reply):
            replies.append(reply)
        manager._write = write
        await manager._bridge(sid, owner, {
            'id': 'image-admission-' + str(len(bridges)), 'operation': operation, 'args': args})
        reply, = replies
        assert not reply.get('error'), reply
        bridges.append(copy.deepcopy({'args': args, 'result': reply['result']}))
        return reply['result']

    worker.bridge = bridge
    pending = None

    async def project(previous, event):
        if previous:
            await previous
        normalized = normalize_event(event, sid)
        if normalized:
            await app.on_runtime_event(*normalized)

    def publish(event):
        nonlocal pending
        pending = asyncio.create_task(project(pending, event))

    telemetry = worker.telemetry = ExecutionEvents(sid, publish)
    telemetry.lifecycle({'type': 'input.delivered', 'input_id': generation['inputIds'][0]})
    control = controls(sid)

    async def admitted(row):
        admissions.append(copy.deepcopy(row))
        if pending:
            await pending
        return await worker.admit_model(row)

    control.capacity.admit = admitted
    telemetry.admission_guard = control.capacity.guard

    async def effect():
        admission = ADMITTED_CALL.get()
        assert admission is not None and admission.guarded
        assert admission.root_id == sid and admission.session_id == 'image-child'
        effects.append(admission.id)

    image_provider.on_image = effect

    async def generate(**arguments):
        await effect()
        return {'usage': {'input_tokens': 3}}

    backend = SimpleNamespace(
        describe=lambda: {'provider': 'fixture-account', 'model': 'image-fixture'},
        generate=generate)

    def install_child(run_id):
        capabilities = {'web.worker_run': run_id, 'live.children': registry}
        providers = {'fixture-account': type(image_provider.original)()}
        callbacks = {}
        coordinator = SimpleNamespace(session_id='image-child',
            cancellation=SimpleNamespace(is_cancelled=False),
            get={'providers': providers}.get, get_capability=capabilities.get,
            register_capability=lambda key, value: capabilities.update({key: value}),
            hooks=SimpleNamespace(register=lambda event, callback, **kwargs:
                                  callbacks.update({event: callback})))
        registry.rows['image-child'] = {
            'sessionId': 'image-child', 'parentSessionId': sid, 'runId': run_id,
            'callId': run_id + '-spawn', 'status': 'running'}
        registry.sessions['image-child'] = SimpleNamespace(coordinator=coordinator)
        worker.app_access_bridge(coordinator)  # Installs the actual current-run lookup.
        worker.install_activity(coordinator)

        async def invoke():
            if dispatch == 'routed':
                return await providers['fixture-account'].generate_image(image_request(), plan=image_plan())
            return await capabilities['image.backend_invoke'](backend, {'prompt': 'hidden-prompt'})
        return invoke

    try:
        original = install_child('original-run')
        await original()
        await pending
        assert len(effects) == len(bridges) == 1
        assert admissions[-1]['runId'] == 'original-run'
        assert bridges[-1]['args']['_childOrigin']['runId'] == 'original-run'
        assert bridges[-1]['result']['allowed'] is True

        replacement = install_child('replacement-run')
        await pending
        before = copy.deepcopy(telemetry.nodes)
        with pytest.raises(asyncio.CancelledError, match='originating native child run'):
            await original()
        assert admissions[-1]['runId'] == 'original-run'
        assert len(effects) == len(bridges) == 1  # No transport reservation or provider effect.
        assert telemetry.nodes == before
        assert control.capacity.last_denial is None  # Stale execution is not a budget pause.

        await replacement()
        await pending
        assert len(effects) == len(bridges) == 2
        assert [row['runId'] for row in admissions] == [
            'original-run', 'original-run', 'replacement-run']
        assert bridges[-1]['args']['_childOrigin']['runId'] == 'replacement-run'
        assert bridges[-1]['result']['allowed'] is True
        calls = [row for row in telemetry.nodes.values() if row['kind'] == 'llm']
        assert [row['runId'] for row in calls] == ['original-run', 'replacement-run']
        assert [row['id'] for row in calls] == effects
        assert all(row['phase'] == 'completed' and row['model'] == 'image-fixture'
                   and row['purpose'] == 'image_generation' for row in calls)
        assert len(image_provider.image_calls) == (2 if dispatch == 'routed' else 0)
        assert image_provider.chat_calls == []
        assert ADMITTED_CALL.get() is None and CALL_PURPOSE.get() is None
        assert CURRENT_CALL.get() is None and CURRENT_PROVIDER.get() is None
    finally:
        if pending:
            await pending
        await control.close()
