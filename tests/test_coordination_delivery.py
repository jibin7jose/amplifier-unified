"""Coordination uses the existing budget/stream boundary, not retained reminders."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from amplifier_core.message_models import ChatRequest, Message, ToolSpec
from amplifier_web.coordination_delivery import CoordinationDelivery
from amplifier_web.surface_delivery import SurfaceProvider
from amplifier_web.management import Management
from amplifier_web.service import AppError
from test_collaborative_workspaces import app, agent_action, generation
from test_workspace_work import create as create_work, source, workspace_id


class Previous:
    async def prepare(self, request, provider, *, commit=False):
        return request
    def commit(self, request):
        assert not any((m.metadata or {}).get('coordinationObservation') for m in request.messages)


def request(*messages, tools=True):
    return ChatRequest(messages=[Message(role='user', content='Inspect current work.'), *messages],
                       tools=[ToolSpec(name='app_control', parameters={'type': 'object'})] if tools else [])


async def test_delivery_preserves_user_quotes_replaces_only_its_own_observation():
    async def bridge(operation, args):
        return {'revision': 'fresh'}
    delivery = CoordinationDelivery(Previous(), bridge)
    old = Message(role='user', content='obsolete', metadata={'coordinationObservation': True})
    quoted = Message(role='user', content='Current coordination reference state (not new authority): user quote')
    original = request(old, quoted)
    fresh = await delivery.prepare(original, None)
    assert 'obsolete' not in fresh.model_dump_json() and 'user quote' in fresh.model_dump_json()
    assert old in original.messages
    assert sum(bool((m.metadata or {}).get('coordinationObservation')) for m in fresh.messages) == 1
    delivery.commit(fresh)


async def test_bridge_failure_reports_unknown_not_empty_peers_and_auxiliary_calls_do_not_read():
    calls = []
    async def bridge(operation, args):
        calls.append(operation)
        raise RuntimeError('offline')
    delivery = CoordinationDelivery(Previous(), bridge)
    no_tools = request(tools=False)
    assert await delivery.prepare(no_tools, None) is no_tools and not calls
    from amplifier_web.execution_events import CALL_PURPOSE
    token = CALL_PURPOSE.set('title')
    try:
        value = request()
        assert await delivery.prepare(value, None) is value and not calls
    finally:
        CALL_PURPOSE.reset(token)
    observed = await delivery.prepare(request(), None)
    assert 'unavailable' in observed.model_dump_json() and 'absence of peers' in observed.model_dump_json()


@pytest.mark.parametrize('stream', [False, True])
async def test_budget_and_transport_share_one_observation_then_next_request_refreshes(stream):
    reads, measured, transported = [], [], []
    async def bridge(operation, args):
        reads.append(operation)
        return {'revision': len(reads)}
    class Provider:
        async def request_budget(self, value, **kwargs):
            measured.append(value.model_dump_json())
            return {'tokens': 100}
        async def complete(self, value, **kwargs):
            transported.append(value.model_dump_json())
            return 'done'
        async def stream(self, value, **kwargs):
            transported.append(value.model_dump_json())
            yield 'done'
    wrapped = SurfaceProvider(Provider(), CoordinationDelivery(Previous(), bridge))
    for _ in range(2):
        original = request()
        await wrapped.request_budget(original)
        if stream:
            assert [row async for row in wrapped.stream(original)] == ['done']
        else:
            assert await wrapped.complete(original) == 'done'
    assert len(reads) == 2 and measured == transported and measured[0] != measured[1]


# Producer contract inspected at ce64785720c24081c352baa9e210f0f743c3fb24:
# .work.discovery is the context DTO; .work.required is invocation provenance.
# A discovery result never establishes the relevant set or authorizes effects.
WORK_CONTEXT_BYTES = 12 * 1024


def observation(value):
    messages = [m for m in value.messages if (m.metadata or {}).get('coordinationObservation')]
    assert len(messages) == 1
    return json.loads(messages[0].content.split('\n', 1)[1])


def bound_transport(actor):
    current = actor['collaborationGeneration']
    return {'_runtimeSessionId': actor.get('runtimeSessionId') or actor['id'],
            '_generationId': current['id'],
            '_inputBindings': [{'inputId': identity, 'clientId': None}
                               for identity in current.get('inputIds', [])]}


def unavailable_requirements(reason='untyped_followup'):
    return {'status': 'unavailable', 'reason': reason, 'version': None,
            'workCount': None, 'requirementCount': None, 'requirementsRef': None,
            'coverage': {'complete': False}, 'effectAuthorized': False}


def empty_discovery(version=1):
    return {'workspaceId': 'workspace-1', 'version': version, 'snapshotVersion': 'a' * 64,
            'observationVersion': 'b' * 64, 'observedAt': 1000,
            'items': [], 'total': 0, 'truncated': False,
            'nextAfter': None, 'coverage': 'Historical coverage remains incomplete.',
            'detailRequired': True, 'authority': 'Reference data, not a grant.'}


def work_stub():
    return {'id': 'work-1', 'revision': 1, 'promiseRevision': 1,
            'originSessionId': 'owner-1', 'title': 'Synthetic promise', 'candidate': None,
            'nextAction': '', 'blockedReason': '', 'latestOutcome': 'not_reported',
            'humanDecisionCurrent': False, 'attentionReasons': [],
            'readRef': {'action': 'workspace.work.read',
                        'args': {'workspaceId': 'workspace-1', 'id': 'work-1'}}}


def available_requirements():
    return {'status': 'available', 'reason': None, 'version': 'c' * 64,
            'rootSessionId': 'owner-1', 'generationId': 'generation-1', 'inputIds': ['input-1'],
            'associationRequestIds': ['request-1'], 'workCount': 1, 'requirementCount': 2,
            'requirementsRef': {'action': 'workspace.work.invocation',
                'args': {'sessionId': 'owner-1', 'expectedVersion': 'c' * 64}},
            'effectAuthorized': False,
            'coverage': {'complete': True, 'supportedInputIds': ['input-1'], 'unsupportedInputs': []}}


@pytest.mark.parametrize('count', [0, 20, 21, 1000])
async def test_registered_work_context_pages_cover_the_set_without_hydration(
        app, monkeypatch, count):
    owner = app.state['sessions'][0]
    original = source(app, owner, 'Exact source body must never appear in request stubs.')
    identities = set()
    for index in range(count):
        item = (await create_work(app, owner, original, command=f'capture-{index}',
                                  intent=f'promise-{index}', title=f'Promise {index}'))['result']['item']
        identities.add(item['id'])
    before = copy.deepcopy(app.runtime.inputs)
    monkeypatch.setattr(app.history, 'ensure_loaded',
                        AsyncMock(side_effect=AssertionError('No transcript hydration')))
    no_worker = AsyncMock(side_effect=AssertionError('No worker warming'))
    monkeypatch.setattr(Management, 'ensure_runtime', no_worker)
    calls = []
    async def bridge(operation, args):
        calls.append(operation)
        return await app.app_bridge(operation, {**args, **bound_transport(owner)}, owner['id'])
    try:
        delivery = CoordinationDelivery(Previous(), bridge)
        prepared = await delivery.prepare(request(), None)
        work = observation(prepared)['work']
        assert len(json.dumps(work, ensure_ascii=False).encode()) <= WORK_CONTEXT_BYTES
        assert work['available'] is True
        # These ordinary fixture inputs have no typed commissioning association.
        # Even zero discovered work cannot assert complete invocation coverage.
        assert work['required']['status'] == 'unavailable'
        assert work['required']['effectAuthorized'] is False
        assert work['required']['coverage']['complete'] is False
        page = work['discovery']
        assert calls == ['coordination.current']  # One request-boundary observation.
        seen, cursors = set(), set()
        for _ in range(1001):
            assert len(json.dumps(page, ensure_ascii=False).encode()) <= WORK_CONTEXT_BYTES
            assert len(page['items']) <= 20
            assert page['total'] == count
            assert page['coverage']  # Recorded empty is not historical completeness.
            assert 'Exact source body' not in json.dumps(page)
            batch = {row['id'] for row in page['items']}
            assert not seen.intersection(batch)
            seen.update(batch)
            assert page['truncated'] == bool(page['nextAfter'])
            if page['nextAfter'] is None:
                break
            assert batch and page['nextAfter'] not in cursors
            cursors.add(page['nextAfter'])
            page = (await app.app_bridge('dispatch', {
                'action': 'workspace.work.context',
                'args': {'sessionId': owner['id'], 'after': page['nextAfter']},
                **bound_transport(owner)}, owner['id']))['result']
        else:
            pytest.fail('Work context paging did not terminate')
        assert seen == identities
        assert app.runtime.inputs == before
        no_worker.assert_not_called()
        delivery.commit(prepared)
    finally:
        assert app.runtime.inputs == before


async def test_work_context_includes_owned_and_contributing_items_only(app):
    owner, contributor = app.state['sessions']
    own = (await create_work(app, owner))['result']['item']
    await generation(app, contributor, 'contributor-generation', ['synthetic-input'])
    linked = (await create_work(app, contributor, command='contributed',
                               intent='contributed'))['result']['item']
    unrelated = (await create_work(app, contributor, command='unrelated',
                                  intent='unrelated'))['result']['item']
    await agent_action(app, contributor, 'workspace.work.link', {
        'sessionId': contributor['id'], 'workspaceId': workspace_id(app, contributor),
        'id': linked['id'], 'expectedRevision': linked['revision'],
        'linkedSessionId': owner['id'], 'role': 'reviewer', 'candidate': 'a' * 40,
    }, 'link-reviewer')
    value = await app.app_bridge('coordination.current', bound_transport(owner), owner['id'])
    ids = {row['id'] for row in value['work']['discovery']['items']}
    assert ids == {own['id'], linked['id']}
    assert unrelated['id'] not in ids
    assert value['work']['required']['status'] == 'unavailable'
    assert value['work']['required']['effectAuthorized'] is False


async def test_work_cursor_rejects_a_changed_set_instead_of_mixing_snapshots(app):
    owner = app.state['sessions'][0]
    original = source(app, owner)
    for index in range(21):
        await create_work(app, owner, original, command=f'initial-{index}', intent=str(index))
    first = (await app.app_bridge('dispatch', {
        'action': 'workspace.work.context', 'args': {'sessionId': owner['id']}}, owner['id']))['result']
    assert first['nextAfter']
    await create_work(app, owner, original, command='new-record', intent='new-record')
    with pytest.raises(AppError, match='changed|refresh|snapshot'):
        await app.app_bridge('dispatch', {'action': 'workspace.work.context',
            'args': {'sessionId': owner['id'], 'after': first['nextAfter']}}, owner['id'])


@pytest.mark.parametrize('work', [
    None,
    {'available': False, 'discovery': {'status': 'unavailable', 'reason': 'Projection failed'},
     'required': unavailable_requirements('Projection failed')},
])
async def test_missing_work_projection_is_not_an_empty_complete_set(work):
    async def bridge(operation, args):
        return {} if work is None else {'work': work}
    prepared = await CoordinationDelivery(Previous(), bridge).prepare(request(), None)
    projected = observation(prepared)['work']
    assert projected['available'] is False
    assert projected['required']['status'] == 'unavailable'
    assert projected['required']['coverage']['complete'] is False
    assert projected['required']['effectAuthorized'] is False
    assert projected['discovery'].get('total') is None


async def test_unbounded_or_body_bearing_work_projection_fails_closed():
    async def bridge(operation, args):
        return {'work': {'available': True, 'discovery': {'items': [{
            'id': 'work-one', 'revision': 1, 'promiseRevision': 1,
            'constraints': ['PRIVATE_CONSTRAINT_BODY' * 20000],
            'originalSource': {'text': 'PRIVATE_SOURCE_BODY' * 20000},
        }], 'total': 1, 'coverage': 'unknown'}, 'required': unavailable_requirements()}}
    prepared = await CoordinationDelivery(Previous(), bridge).prepare(request(), None)
    text = prepared.model_dump_json()
    assert 'PRIVATE_CONSTRAINT_BODY' not in text
    assert 'PRIVATE_SOURCE_BODY' not in text
    projected = observation(prepared)['work']
    assert projected['available'] is False
    assert projected['required']['status'] == 'unavailable'
    assert projected['required']['effectAuthorized'] is False
    assert projected['discovery'].get('total') is None  # Failed projection is not trusted.


@pytest.mark.parametrize('change', ['small_body', 'claimed_grant', 'false_complete'])
async def test_work_projection_rejects_invalid_semantics_even_below_byte_limit(change):
    work = {'available': True, 'discovery': empty_discovery(),
            'required': unavailable_requirements()}
    if change == 'small_body':
        work['discovery']['total'] = 1
        work['discovery']['items'] = [{
            'id': 'work-1', 'revision': 1, 'promiseRevision': 1,
            'originalSource': {'text': 'SOURCE_BODY_SHOULD_NOT_BE_IN_PREFIX'},
            'readRef': {'action': 'workspace.work.read',
                        'args': {'workspaceId': 'workspace-1', 'id': 'work-1'}}}]
    elif change == 'claimed_grant':
        work['required']['effectAuthorized'] = True
    else:
        work['required']['coverage']['complete'] = True
    assert len(json.dumps(work).encode()) < WORK_CONTEXT_BYTES
    async def bridge(operation, args):
        return {'work': work}
    value = await CoordinationDelivery(Previous(), bridge).prepare(request(), None)
    result = observation(value)['work']
    assert result['available'] is False
    assert result['required']['coverage']['complete'] is False
    assert result['required']['effectAuthorized'] is False
    assert 'SOURCE_BODY_SHOULD_NOT_BE_IN_PREFIX' not in value.model_dump_json()


@pytest.mark.parametrize('change', [
    'missing_header', 'missing_id', 'wrong_ref', 'nested_title', 'nested_candidate',
    'bad_revision', 'wrong_coverage', 'wrong_requirements_ref',
])
async def test_work_dto_shape_and_read_handles_are_required_below_byte_bound(change):
    from amplifier_web.coordination_delivery import _work_reference
    work = {'available': True, 'discovery': {**empty_discovery(), 'items': [work_stub()], 'total': 1},
            'required': available_requirements()}
    # The control is valid, so a rejection cannot be due to a broken fixture.
    assert _work_reference(work) == work
    row, header = work['discovery']['items'][0], work['required']
    if change == 'missing_header':
        work['required'] = {'status': 'available', 'version': 'v',
                            'effectAuthorized': False, 'coverage': {'complete': True}}
    elif change == 'missing_id':
        row.pop('id')
    elif change == 'wrong_ref':
        row['readRef']['args']['id'] = 'other-work'
    elif change in {'nested_title', 'nested_candidate'}:
        row[change.removeprefix('nested_')] = {'body': 'HIDDEN_SOURCE_BODY'}
    elif change == 'bad_revision':
        row['promiseRevision'] = True
    elif change == 'wrong_coverage':
        header['coverage']['supportedInputIds'] = ['other-input']
    else:
        header['requirementsRef']['args']['expectedVersion'] = 'd' * 64
    assert len(json.dumps(work).encode()) < WORK_CONTEXT_BYTES
    async def bridge(operation, args):
        return {'work': work}
    prepared = await CoordinationDelivery(Previous(), bridge).prepare(request(), None)
    result = observation(prepared)['work']
    assert result['available'] is False
    assert result['required']['coverage']['complete'] is False
    assert result['required']['effectAuthorized'] is False
    assert 'HIDDEN_SOURCE_BODY' not in prepared.model_dump_json()


@pytest.mark.parametrize('reason', ['input_projection_incomplete', 'input_coverage_overflow'])
@pytest.mark.parametrize('fault', ['none', 'nested_body', 'wrong_count', 'claimed_complete'])
def test_unavailable_input_projection_preserves_discovery_with_strict_counts(reason, fault):
    from amplifier_web.coordination_delivery import _work_reference
    header = unavailable_requirements('Invocation projection unavailable.')
    coverage = {'complete': False, 'supportedInputIds': [], 'unsupportedInputs': [],
                'reason': reason, 'deliveredInputCount': 17 if reason.endswith('overflow') else 3,
                'projectedInputCount': 1}
    if reason.endswith('overflow'):
        header.update(inputIds=[], inputCount=17)
    else:
        header['inputIds'] = ['a', 'b', 'c']
        coverage['missingInputIds'] = ['b', 'c']
    header['coverage'] = coverage
    work = {'available': True, 'discovery': empty_discovery(), 'required': header}
    assert _work_reference(work) == work
    if fault == 'nested_body':
        coverage['projectedInputCount'] = {'body': 'PRIVATE'}
    elif fault == 'wrong_count':
        coverage['deliveredInputCount'] = 1
    elif fault == 'claimed_complete':
        coverage['complete'] = True
    result = _work_reference(work)
    assert result['available'] is (fault == 'none')
    assert result['required']['effectAuthorized'] is False
    if fault == 'none':
        assert result['discovery'] == work['discovery']
        assert result['required']['coverage'] == coverage


@pytest.mark.parametrize('stream', [False, True])
async def test_work_observation_is_measured_and_transported_once_per_request(stream):
    calls, measured, transported = [], [], []
    async def bridge(operation, args):
        calls.append(operation)
        return {'work': {'available': True, 'discovery': empty_discovery(len(calls)),
                         'required': unavailable_requirements()}}
    class Provider:
        async def request_budget(self, value, **kwargs):
            measured.append(value.model_dump_json())
            return {'tokens': 100}
        async def complete(self, value, **kwargs):
            transported.append(value.model_dump_json())
            return 'done'
        async def stream(self, value, **kwargs):
            transported.append(value.model_dump_json())
            yield 'done'
    wrapped = SurfaceProvider(Provider(), CoordinationDelivery(Previous(), bridge))
    for _ in range(2):
        original = request()
        await wrapped.request_budget(original)
        if stream:
            assert [row async for row in wrapped.stream(original)] == ['done']
        else:
            assert await wrapped.complete(original) == 'done'
    assert calls == ['coordination.current', 'coordination.current']
    assert measured == transported
    assert observation_json_version(measured[0]) != observation_json_version(measured[1])


def observation_json_version(serialized):
    return observation(ChatRequest.model_validate_json(serialized))['work']['discovery']['version']


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('stale', [False, True])
async def test_outer_delivery_preserves_inner_final_boundary(stream, stale):
    """Exercise the real wrapper chain, not a production authority predicate."""
    from amplifier_web.surface_delivery import SurfaceDelivery
    from amplifier_web.voice_visual_delivery import VoiceVisualDelivery
    from amplifier_web.output_image_delivery import OutputImageDelivery
    from amplifier_web.memory_delivery import MemoryDelivery

    reads, events, measured, transported = [], [], [], []
    replies = {
        'context.manifest': {'inputIds': ['synthetic-input'], 'surfaces': []},
        'coordination.current': {
            'work': {'available': True, 'discovery': empty_discovery(),
                     'required': unavailable_requirements()}},
        'memory.context': {'items': []},
    }

    async def bridge(operation, args):
        reads.append((operation, copy.deepcopy(args)))
        return copy.deepcopy(replies[operation])

    class ChangedAtBoundary(RuntimeError):
        pass

    class CheckedCoordination(CoordinationDelivery):
        async def revalidate(self, value):
            events.append('revalidate')
            assert value.model_dump_json().encode('utf-8') == measured[0]
            if self.stale:
                raise ChangedAtBoundary('changed after budget')
            return value

        def commit(self, value):
            events.append('commit')
            super().commit(value)

    class Provider:
        async def request_budget(self, value, **kwargs):
            events.append('budget')
            measured.append(value.model_dump_json().encode('utf-8'))
            return {'tokens': 100}

        async def complete(self, value, **kwargs):
            events.append('transport')
            transported.append(value.model_dump_json().encode('utf-8'))
            return 'done'

        async def stream(self, value, **kwargs):
            events.append('transport')
            transported.append(value.model_dump_json().encode('utf-8'))
            yield 'done'

    inner = CheckedCoordination(SurfaceDelivery(bridge), bridge)
    inner.stale = False
    delivery = MemoryDelivery(
        OutputImageDelivery(VoiceVisualDelivery(inner, bridge), bridge), bridge)
    wrapped = SurfaceProvider(Provider(), delivery)
    original = request(Message(role='user', content='Preserve \u03bb and caf\u00e9.'))
    original_bytes = original.model_dump_json().encode('utf-8')
    await wrapped.request_budget(original)
    assert events == ['budget']
    inner.stale = stale  # Mutate only after fitting, before transport.

    async def send():
        if stream:
            return [row async for row in wrapped.stream(original)]
        return [await wrapped.complete(original)]

    if stale:
        with pytest.raises(ChangedAtBoundary, match='changed after budget'):
            await send()
        assert transported == []
        assert events == ['budget', 'revalidate']
    else:
        assert await send() == ['done']
        assert transported == measured
        assert events == ['budget', 'revalidate', 'commit', 'transport']
    assert len(measured) == 1
    assert reads == [('context.manifest', {}), ('coordination.current', {}),
                     ('memory.context', {})]
    assert original.model_dump_json().encode('utf-8') == original_bytes


class InstalledWorkAdapter:
    """Real mounted app tool + real loop tool dispatch, no model service.

    This is host-request coverage, not native transport or effect admission.
    """
    async def setup(self, app, child):
        from types import SimpleNamespace
        from amplifier_core import HookRegistry
        from amplifier_web.app_guidance import install_app_access
        from amplifier_module_loop_live import BundleLiveOrchestrator
        from test_work_invocation import transport
        self.tools, self.capabilities, self.messages = {}, {}, []
        self.hooks = HookRegistry()
        self.loop = BundleLiveOrchestrator({})
        self.loop._tool_calls_this_turn = 0
        self.counter = 0
        async def mount(kind, tool, name):
            self.tools[name] = tool
        async def process_hook_result(result, event, name):
            return result
        coordinator = SimpleNamespace(get_capability=self.capabilities.get,
            get=lambda kind: self.tools if kind == 'tools' else None,
            register_capability=self.capabilities.__setitem__, mount=mount, hooks=self.hooks,
            session_id=child['id'], process_hook_result=process_hook_result,
            cancellation=SimpleNamespace(is_immediate=False, register_tool_start=lambda *a: None,
                                         register_tool_complete=lambda *a: None))
        self.coordinator = coordinator
        self.dispatch_bridge = None
        from amplifier_web.execution_events import ExecutionEvents
        self.telemetry = ExecutionEvents(child['id'], lambda event: None)
        self.admissions = []
        async def admit(row):
            self.admissions.append(row['id'])
        self.telemetry.admission_guard = admit
        self.scope = {
            'rootSessionId': child['id'], 'runtimeSessionId': child['id'],
            'generationId': child['collaborationGeneration']['id'],
            'inputIds': child['collaborationGeneration']['inputIds'],
            'inputCoverage': 'complete',
        }
        self.capabilities['web.work_origin_scope'] = lambda: copy.deepcopy(self.scope)
        def observe(provider):
            if not callable(getattr(provider, 'get_info', None)):
                provider.get_info = lambda: SimpleNamespace(id='fixture', defaults={})
            return self.telemetry.instrument_provider(child['id'], provider)
        self.capabilities['web.provider_observe'] = observe
        async def bridge(operation, args):
            if operation == 'dispatch' and self.dispatch_bridge is not None:
                return await self.dispatch_bridge(operation, args)
            if operation == 'context.manifest':
                return {'surfaces': [], 'inputIds': child['collaborationGeneration']['inputIds']}
            if operation == 'memory.context':
                return {'items': []}
            return await app.app_bridge(operation, {**args, **transport(child)}, child['id'])
        await install_app_access(coordinator, bridge)
        self.delivery = self.capabilities['web.surface_delivery']
        return self

    def request(self):
        return request(*self.messages)

    async def read(self, ref, **patch):
        from amplifier_core.message_models import ToolCall
        self.counter += 1
        args = {'operation': 'dispatch', 'args': {
            'action': ref['action'], 'args': {**ref['args'], **patch}}}
        call = ToolCall(id=f'work-read-{self.counter}', name='app_control', arguments=args)
        self.messages.append(Message(role='assistant', content='', tool_calls=[{
            'id': call.id, 'tool': call.name, 'arguments': call.arguments}]))
        identity, name, content = await self.loop._execute_tool_only(
            call, self.tools, self.hooks, None, self.coordinator)
        self.messages.append(Message(role='tool', name=name, tool_call_id=identity, content=content))
        from amplifier_web.output_image_delivery import successful_output
        from amplifier_web.output_image_delivery import pair_retained
        observed = [row for row in self.delivery.reads if row['input'] == args]
        assert observed, f'No captured actual bridge result for {args["args"]["action"]}'
        bound = [row for row in observed if row['tool_call_id'] == identity]
        assert bound, json.dumps({
            'stage': 'tool_post_binding', 'action': args['args']['action'],
            'saved': observed[-1]['receipt'], 'retained': successful_output(content)}, ensure_ascii=False)
        assert pair_retained(self.request(), bound[-1]), json.dumps({
            'stage': 'fitted_pair', 'saved': bound[-1]['receipt'],
            'retained': successful_output(content)}, ensure_ascii=False)
        return successful_output(content)['result']

    async def fill(self, required):
        rows, cursor = [], None
        while True:
            page = await self.read(required['requirementsRef'], limit=1,
                                   **({'after': cursor} if cursor else {}))
            rows.extend(page['items'])
            cursor = page['nextAfter']
            if cursor is None:
                break
        for row in rows:
            offset = 0
            while True:
                value = await self.read(row['readRef'], offset=offset, limit=700)
                page = value['source'] if row['kind'] == 'source' else value
                offset = page['nextOffset']
                if offset is None:
                    break


async def installed_work_adapter(app):
    from test_work_invocation import prepared_work, commissioned, current_work
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'delivered-pages', constraints=['Preserve \u03bb caf\u00e9. ' * 50] * 5)
    _, child = await commissioned(app, owner, item)
    required = (await current_work(app, child))['required']
    assert required['status'] == 'available'
    return await InstalledWorkAdapter().setup(app, child), child, required


@pytest.mark.parametrize('fault', ['none', 'missing_guard', 'new_input', 'overflow', 'new_generation'])
async def test_effect_origin_joins_actual_admission_and_full_input_scope(app, fault):
    adapter, _, required = await installed_work_adapter(app)
    await adapter.fill(required)
    class Provider:
        async def complete(self, value, **kwargs):
            return SimpleNamespace(model_dump=lambda: {'content': [{
                'type': 'tool_call', 'id': 'origin-call', 'name': 'write_file', 'input': {'path': 'owned'}}]})
    if fault == 'missing_guard':
        adapter.telemetry.admission_guard = None
    wrapped = adapter.capabilities['web.provider_transform'](Provider())
    await wrapped.complete(adapter.request())
    if fault == 'new_input':
        adapter.scope['inputIds'] = [*adapter.scope['inputIds'], 'later-input']
    if fault == 'overflow':
        adapter.scope['inputCoverage'] = 'overflow'
    if fault == 'new_generation':
        adapter.scope['generationId'] = 'successor'
    coverage = await adapter.delivery.recheck_effect_coverage({
        'tool_call_id': 'origin-call', 'tool_name': 'write_file', 'tool_input': {'path': 'owned'}})
    assert coverage['complete'] is (fault == 'none'), coverage
    assert coverage['effectAuthorized'] is False
    if fault == 'none':
        assert coverage['origin']['admittedCallId'] in adapter.admissions
        assert coverage['origin']['inputIds'] == required['inputIds']
        assert coverage['origin']['fittedSha256'] == coverage['requestSha256']


async def test_capacity_await_cannot_bind_a_successor_input_to_old_fitted_bytes(app):
    adapter, _, required = await installed_work_adapter(app)
    await adapter.fill(required)
    transported = []
    async def admit(row):
        adapter.scope['inputIds'] = [*adapter.scope['inputIds'], 'during-capacity-await']
    adapter.telemetry.admission_guard = admit
    class Provider:
        async def complete(self, value, **kwargs):
            transported.append(value)
    wrapped = adapter.capabilities['web.provider_transform'](Provider())
    with pytest.raises(ValueError, match='origin changed'):
        await wrapped.complete(adapter.request())
    assert transported == []


async def test_untasked_input_change_during_admission_is_unproven_not_refused():
    from amplifier_web.execution_events import ExecutionEvents
    scope = {'rootSessionId': 'root', 'runtimeSessionId': 'root', 'generationId': 'g',
             'inputIds': ['first'], 'inputCoverage': 'complete'}
    delivery = CoordinationDelivery(Previous(), AsyncMock(return_value={}), origin_scope=lambda: scope)
    telemetry = ExecutionEvents('root', lambda event: None)
    async def admit(row):
        scope['inputIds'].append('next')
    telemetry.admission_guard = admit
    sent = []
    class Provider:
        def get_info(self):
            return SimpleNamespace(id='fixture', defaults={})
        async def complete(self, value, **kwargs):
            sent.append(value)
            return {}
    wrapped = SurfaceProvider(telemetry.instrument_provider('root', Provider()), delivery)
    await wrapped.complete(request(tools=False))
    assert len(sent) == 1 and next(iter(delivery.attempts.values()))['origin'] is None


async def native_outbound_probe(app, tmp_path, monkeypatch, fault):
    import asyncio
    from amplifier_module_provider_openai import OpenAIProvider
    from amplifier_module_provider_openai.native import NativeResponsesProvider, NATIVE_REQUEST
    from amplifier_web.native_provider import install_native
    adapter, _, required = await installed_work_adapter(app)
    await adapter.fill(required)
    if fault == 'overflow_scope':
        adapter.scope['inputCoverage'] = 'overflow'
    if fault == 'missing_scope':
        adapter.delivery.begin_work.__self__.origin_scope = lambda: None
    c = adapter.coordinator
    providers = {'configured': OpenAIProvider(api_key='fixture', config={'default_model': 'gpt-6-astra'})}
    c.get = lambda key: {'tools': adapter.tools, 'providers': providers,
                        'context': SimpleNamespace(get_messages=AsyncMock(return_value=[]))}.get(key)
    c.loop = adapter.loop
    c.loop.config['native_provider'] = True
    c.loop._select_provider = lambda values: values['configured']
    async def mount(kind, value, name):
        (providers if kind == 'providers' else adapter.tools)[name] = value
    c.mount = mount
    adapter.capabilities['live.runtime'] = SimpleNamespace(generation={'id': adapter.scope['generationId']})
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    await install_native(c.loop, c, providers)
    host = adapter.capabilities['web.native_provider']
    native = host.provider
    assert native.prepare_send == host.prepare_send and native.lifecycle == host.send_lifecycle
    proofs, sent, measured = [], [], []
    class Effect:
        name = 'delegate'
        async def execute(self, value):
            from amplifier_core import ToolResult
            dispatch = c._tool_dispatch_contexts[asyncio.current_task()]
            proofs.append(await adapter.delivery.recheck_effect_coverage({
                **dispatch, 'tool_name': self.name, 'tool_input': value}))
            return ToolResult(success=True, output='Controlled fixture; no delegated work.')
    adapter.tools['delegate'] = Effect()
    async def start(call):
        await adapter.loop._execute_tool_only(call, adapter.tools, adapter.hooks, None, c)
    item = {'type': 'function_call', 'id': 'item-one', 'call_id': 'native-tool',
            'name': 'delegate', 'arguments': '{"instruction":"fixture"}', 'async': True, 'status': 'completed'}
    response_id = 'wrong' if fault == 'wrong_response' else 'response-one'
    events = [
        {'type': 'response.created', 'response': {'id': 'response-one'}},
        {'type': 'response.output_item.done', 'response_id': response_id, 'item': item},
        {'type': 'response.completed', 'response': {'id': 'response-one', 'object': 'response',
            'status': 'completed', 'model': 'gpt-6-astra', 'output': [item],
            'usage': {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}}},
    ]
    if fault == 'no_event_response':
        events[1].pop('response_id')
        events[1].update(output_index=0, sequence_number=2)
    if fault == 'ambiguous_response':
        events.insert(1, {'type': 'response.created', 'response': {'id': 'ambiguous'}})
        events[2].pop('response_id')
    if fault == 'retention':
        # Expired owners must not permanently poison native transport.
        owner = adapter.delivery.begin_work.__self__
        host.response_origins = {f'old-{i}': (owner, {'id': f'expired-{i}'}) for i in range(128)}
    async def send(wire):
        sent.append(wire)
    native.socket = SimpleNamespace(send=send, recv=AsyncMock(side_effect=[json.dumps(e) for e in events]),
                                    close=AsyncMock())
    native._connect = AsyncMock()
    native._guard_assembled_params_with_provider_count = AsyncMock(return_value=None)
    native_owner = SimpleNamespace(coordinator=c, config={'background_delegate': True},
        runtime=SimpleNamespace(emit=AsyncMock()), start_native_job=start, native_job=lambda identity: None,
        context=SimpleNamespace(get_messages=AsyncMock(return_value=[m.model_dump() for m in adapter.messages])))
    native.owner = native_owner
    if fault in {'opaque_only', 'changed_plaintext'}:
        def checkpoint(params, full):
            params = copy.deepcopy(params)
            if fault == 'opaque_only':
                params['input'] = [{'type': 'compaction', 'encrypted_content': 'opaque'}]
            else:
                for entry in params['input']:
                    for block in entry.get('content', []):
                        if isinstance(block, dict) and block.get('text', '').startswith('Required Work detail plaintext'):
                            block['text'] += ' altered'
            return params
        native._apply_checkpoint = checkpoint
    if fault == 'oversized':
        monkeypatch.setattr('amplifier_web.coordination_delivery.PLAINTEXT_BYTES', 1)
    class Driver:
        get_info = native.get_info
        async def request_budget(self, value, **kwargs):
            measured.append(value.model_dump_json())
            return {'tokens': 100}
        async def complete(self, value, **kwargs):
            assert value.model_dump_json() == measured[0]
            if fault == 'full_provider':
                from amplifier_module_loop_live.scope import LIVE_OWNER
                native.owner = None
                native._provider_count_available = lambda: False
                client = SimpleNamespace(base_url='https://api.openai.com/v1', responses=SimpleNamespace())
                client.with_options = lambda **options: client
                native._client = client
                token = LIVE_OWNER.set(native_owner)
                try:
                    return await native.complete(value, model='gpt-6-astra')
                finally:
                    LIVE_OWNER.reset(token)
            if fault == 'live_retention':
                from amplifier_web.coordination_delivery import WORK_ATTEMPT
                owner, attempt = WORK_ATTEMPT.get()
                host.response_origins = {f'live-{i}': (owner, attempt) for i in range(128)}
            native._request_messages = [m.model_dump() for m in value.messages]
            params = {'input': super(NativeResponsesProvider, native)._convert_messages(native._request_messages),
                      'tools': [{'type': 'function', 'name': 'delegate', 'parameters': {'type': 'object'}}]}
            if fault == 'new_input':
                adapter.scope['inputIds'].append('new-input')
            if fault == 'activation_changed':
                adapter.capabilities['live.activation'] = SimpleNamespace(current_valid=False)
            token = NATIVE_REQUEST.set(native)
            try:
                await native._create_response(params)
            finally:
                NATIVE_REQUEST.reset(token)
            return {}
    wrapped = adapter.capabilities['web.provider_transform'](Driver())
    original = adapter.request()
    if fault == 'full_provider':
        original = original.model_copy(update={'tools': [*original.tools,
            ToolSpec(name='delegate', parameters={'type': 'object', 'properties': {'instruction': {'type': 'string'}}})]})
    await wrapped.request_budget(original)
    if fault in {'new_input', 'activation_changed'}:
        with pytest.raises(Exception):
            await wrapped.complete(original)
        assert sent == [] and proofs == []
        if fault == 'activation_changed':
            assert host.state['sends'][-1]['outcome'] == 'not_sent'
    elif fault == 'ambiguous_response':
        from amplifier_core.llm_errors import LLMError
        with pytest.raises(LLMError, match='outcome unknown'):
            await wrapped.complete(original)
        assert len(sent) == len(proofs) == 1 and proofs[0]['complete'] is False
        assert host.state['outcome'] == 'unknown'
    else:
        await wrapped.complete(original)
        assert len(sent) == len(proofs) == 1
        proven = fault in {'none', 'no_event_response', 'retention', 'full_provider'}
        assert proofs[0]['complete'] is proven, proofs
        assert proofs[0]['effectAuthorized'] is False
        if proven:
            import hashlib
            assert proofs[0]['outboundSha256'] == hashlib.sha256(sent[0].encode()).hexdigest()
            assert proofs[0]['responseId'] == 'response-one'
            assert proofs[0]['origin']['admittedCallId'] in adapter.admissions
    assert 'fixture"}' not in host.path.read_text() if host.path.exists() else True


async def programmatic_origin_probe(app, fault, *, guarded=False):
    import asyncio
    from amplifier_core import ToolResult
    from amplifier_core.message_models import ToolCall
    from amplifier_module_tool_exec import ProgrammaticTool
    adapter, _, required = await installed_work_adapter(app)
    if fault not in {'uncovered', 'same_batch_read', 'app_control_uncovered'}:
        await adapter.fill(required)
    c = adapter.coordinator
    if guarded:
        from amplifier_web.runtime_worker import Worker
        worker = Worker()
        worker.session = SimpleNamespace(coordinator=c)
        worker.runtime = SimpleNamespace(generation={'id': adapter.scope['generationId'],
            'input_ids': list(adapter.scope['inputIds'])}, closed=False, stop_requested=False)
        worker.activation_gate = SimpleNamespace(current_valid=True)
        assigned_bridge = worker.app_access_bridge(c)
        adapter.delivery.begin_work.__self__.origin_scope = c.get_capability('web.work_origin_scope')
        async def service(operation, args):
            if operation == 'dispatch':
                assert args['action'] == 'synthetic.effect'
                proofs.append(copy.deepcopy(args['_workOrigin']))
                return {'accepted': True, 'result': 'fixture'}
            assert operation == 'work.effect.check'
            # Controlled service policy only; real classifier belongs to the
            # backend integration. Container execution is not its nested effect.
            return {'applicable': True, 'allowed': args['toolName'] == 'tool_exec'
                    or args['_workOrigin'].get('complete') is True, 'effectAuthorized': False}
        worker.bridge = service
        adapter.dispatch_bridge = assigned_bridge
    adapter.loop.config['programmatic_dispatch'] = True
    c.register_capability('tools.dispatch', adapter.loop.approved_dispatch)
    adapter.delivery.install_dispatch(c)
    adapter.tools['tool_exec'] = ProgrammaticTool(c, {'timeout_seconds': 10})
    c.cancellation.is_cancelled = False
    proofs = []
    class Probe:
        name = 'probe'
        async def execute(self, value):
            dispatch = c._tool_dispatch_contexts[asyncio.current_task()]
            if fault == 'stop':
                adapter.scope['stopRevision'] = 1
            check = adapter.delivery.execution_origin if guarded else adapter.delivery.claim_effect_origin
            proof = await check({
                **dispatch, 'tool_name': 'probe', 'tool_input': value})
            proofs.append(proof)
            return ToolResult(success=True, output={'covered': proof.get('complete')})
    adapter.tools['probe'] = Probe()
    prefix = ''
    if fault == 'same_batch_read':
        ref = required['requirementsRef']
        prefix = 'await tools.app_control(' + json.dumps({
            'operation': 'dispatch', 'args': ref}) + '); '
    code = prefix + 'text(await Promise.all([tools.probe({n:1}), tools.probe({n:2})]));'
    if fault in {'app_control', 'app_control_uncovered'}:
        code = 'text(await tools.app_control({operation:"dispatch",args:{action:"synthetic.effect",args:{value:1}}}));'
    class Provider:
        async def complete(self, value, **kwargs):
            return SimpleNamespace(model_dump=lambda: {'content': [{
                'type': 'tool_call', 'id': 'parent-program', 'name': 'tool_exec', 'input': {'code': code}}]})
    wrapped = adapter.capabilities['web.provider_transform'](Provider())
    await wrapped.complete(adapter.request())
    args = {'code': code + (' ' if fault == 'changed_parent' else '')}
    async def execute():
        with adapter.loop.approved_dispatch.run_scope(c):
            return await adapter.loop._execute_tool_only(
                ToolCall(id='parent-program', name='tool_exec', arguments=args),
                adapter.tools, adapter.hooks, None, c)
    first = await execute()
    assert len(proofs) == (1 if fault.startswith('app_control') else 2), first
    assert all(p['complete'] is (fault in {'none', 'reused_parent', 'app_control'}) for p in proofs), proofs
    if fault == 'app_control':
        assert proofs[0]['parentToolCallId'] == 'parent-program'
    if fault == 'none':
        assert len({p['toolCallId'] for p in proofs}) == 2
        assert {p['parentToolCallId'] for p in proofs} == {'parent-program'}
        assert len({p['origin']['admittedCallId'] for p in proofs}) == 1
    if fault == 'reused_parent':
        proofs.clear()
        await execute()
        if guarded:
            assert proofs == []  # The final check refuses before either effect.
        else:
            assert len(proofs) == 2 and all(p['complete'] is False for p in proofs)
    from amplifier_web.coordination_delivery import WORK_NESTED
    assert WORK_NESTED.get() is None and not c._tool_dispatch_contexts


@pytest.mark.parametrize('fault', ['none', 'partial', 'forged_id', 'changed_body', 'compacted', 'same_call', 'reopened'])
async def test_installed_adapter_requires_genuine_complete_retained_pages(app, fault):
    adapter, child, required = await installed_work_adapter(app)
    before = await adapter.delivery.prepare(adapter.request(), None)
    assert adapter.delivery.request_coverage(before)['complete'] is False
    await adapter.fill(required)
    assert adapter.counter >= 4  # Manifest and Unicode content paging both occurred.
    # A read occurring after a request was fitted cannot acknowledge that request.
    if fault == 'same_call':
        prepared = before
    else:
        if fault == 'partial':
            adapter.messages.pop()
        elif fault == 'forged_id':
            tool = next(m for m in adapter.messages if m.role == 'tool')
            tool.tool_call_id = 'forged-result-id'
        elif fault == 'changed_body':
            tool = next(m for m in reversed(adapter.messages) if m.role == 'tool')
            tool.content = tool.content[:100] + ' [truncated]'
        elif fault == 'compacted':
            adapter.messages = [Message(role='user', content='Summary claims all details were read.')]
        elif fault == 'reopened':
            old_messages = adapter.messages
            adapter = await InstalledWorkAdapter().setup(app, child)
            adapter.messages = old_messages  # History without genuine local observations.
        prepared = await adapter.delivery.prepare(adapter.request(), None)
    coverage = adapter.delivery.request_coverage(prepared)
    assert coverage['complete'] is (fault == 'none'), json.dumps(coverage, ensure_ascii=False)
    assert coverage['effectAuthorized'] is False  # Not native/effect acceptance.
    if fault == 'none':
        assert coverage['coveredCount'] == required['requirementCount']
        assert coverage['scope'] == 'fitted_host_request_only'


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('change', [
    'none', 'stop', 'outer_await', 'changed_bytes', 'remove_prefix', 'change_prefix', 'mutated_measured',
])
async def test_installed_work_final_boundary_checks_after_outer_awaits(app, stream, change):
    adapter, child, required = await installed_work_adapter(app)
    await adapter.fill(required)
    measured, transported = [], []
    class Provider:
        async def request_budget(self, value, **kwargs):
            measured.append(value.model_dump_json())
            return {'tokens': 100}
        async def complete(self, value, **kwargs):
            transported.append(value.model_dump_json())
            return 'done'
        async def stream(self, value, **kwargs):
            transported.append(value.model_dump_json())
            yield 'done'
    wrapped = adapter.capabilities['web.provider_transform'](Provider())
    original = adapter.request()
    await wrapped.request_budget(original)
    async def stop():
        # Explicit fault injection at the producer's real root authority fields,
        # not a fabricated successful guard/observation receipt.
        child['status'] = 'stopped'
        child['interruptionRevision'] = child.get('interruptionRevision', 0) + 1
    if change == 'stop':
        await stop()
    elif change == 'mutated_measured':
        original.messages[0].content = 'Changed after measurement'
    elif change in {'outer_await', 'changed_bytes', 'remove_prefix', 'change_prefix'}:
        previous = adapter.delivery.revalidate
        async def outer(value):
            value = await previous(value)
            if change == 'outer_await':
                await stop()
            elif change == 'changed_bytes':
                value = value.model_copy(update={'messages': [*value.messages,
                    Message(role='user', content='Unmeasured substitution')]})
            elif change == 'remove_prefix':
                value = value.model_copy(update={'messages': [
                    m for m in value.messages if not (m.metadata or {}).get('coordinationObservation')]})
            else:
                value = value.model_copy(deep=True)
                message = next(m for m in value.messages if (m.metadata or {}).get('coordinationObservation'))
                message.content = 'Changed bound prefix'
            return value
        adapter.delivery.revalidate = outer
    async def send():
        if stream:
            return [row async for row in wrapped.stream(original)]
        return [await wrapped.complete(original)]
    if change == 'none':
        assert await send() == ['done']
        assert transported == measured
    else:
        with pytest.raises(ValueError, match='refit'):
            await send()
        assert transported == []


@pytest.mark.parametrize('stream', [False, True])
async def test_bound_work_provider_failure_does_not_retry_without_new_currentness(app, stream, monkeypatch):
    from amplifier_core.llm_errors import LLMTimeoutError
    adapter, _, _ = await installed_work_adapter(app)
    calls = []
    class Provider:
        async def complete(self, value, **kwargs):
            calls.append('complete')
            raise LLMTimeoutError('Synthetic unreturned timeout', retryable=True)
        async def stream(self, value, **kwargs):
            calls.append('stream')
            raise LLMTimeoutError('Synthetic unreturned timeout', retryable=True)
            yield  # Async generator shape.
    monkeypatch.setattr('amplifier_web.request_recovery.DELAYS', (0, 0))
    wrapped = adapter.capabilities['web.provider_transform'](Provider())
    with pytest.raises(LLMTimeoutError):
        if stream:
            async for _ in wrapped.stream(adapter.request()):
                pass
        else:
            await wrapped.complete(adapter.request())
    assert calls == ['stream' if stream else 'complete']


async def test_interleaved_complete_calls_bind_own_content_not_last_request(app):
    import asyncio
    from types import SimpleNamespace
    adapter, _, required = await installed_work_adapter(app)
    await adapter.fill(required)
    entered, release = asyncio.Event(), asyncio.Event()
    class Provider:
        async def complete(self, value, **kwargs):
            label = kwargs['label']
            if label == 'covered':
                entered.set()
                await release.wait()
            return SimpleNamespace(model_dump=lambda: {'content': [{
                'type': 'tool_call', 'id': label, 'name': 'write_file', 'input': {'path': label}}]})
    wrapped = adapter.capabilities['web.provider_transform'](Provider())
    task = asyncio.create_task(wrapped.complete(adapter.request(), label='covered'))
    try:
        await entered.wait()
        await wrapped.complete(request(), label='uncovered')
        release.set()
        await task
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    def event(label):
        return {'tool_call_id': label, 'tool_name': 'write_file', 'tool_input': {'path': label}}
    check = adapter.capabilities['web.work_effect_coverage']
    covered = await check(event('covered'))
    assert covered['complete'] is True, json.dumps(covered)
    assert (await check(event('uncovered')))['complete'] is False
    assert (await check({**event('covered'), 'tool_input': {'path': 'changed'}}))['complete'] is False
    assert covered['effectAuthorized'] is False
    assert covered['requirementCount'] == required['requirementCount']


@pytest.mark.parametrize('unbound', [False, True])
async def test_call_id_reuse_and_uncertain_completion_do_not_reuse_origin(app, unbound):
    from types import SimpleNamespace
    adapter, _, required = await installed_work_adapter(app)
    await adapter.fill(required)
    class Provider:
        async def complete(self, value, **kwargs):
            if kwargs.get('fail'):
                raise ConnectionError('Synthetic uncertain send')
            return SimpleNamespace(model_dump=lambda: {'content': [{
                'type': 'tool_call', 'id': 'reused', 'name': 'write_file', 'input': {'path': 'same'}}]})
    wrapped = adapter.capabilities['web.provider_transform'](Provider())
    event = {'tool_call_id': 'reused', 'tool_name': 'write_file', 'tool_input': {'path': 'same'}}
    await wrapped.complete(adapter.request())
    assert adapter.delivery.effect_coverage(event)['complete'] is True
    # A failed current projection cannot reuse an earlier complete certificate.
    owner = adapter.delivery.begin_work.__self__
    original_bridge = owner.bridge
    if unbound:
        async def failed_projection(operation, args):
            if operation == 'coordination.current':
                raise ConnectionError('Synthetic unavailable current projection')
            return await original_bridge(operation, args)
        owner.bridge = failed_projection
    await wrapped.complete(adapter.request())  # Same ID/args, different actual request.
    owner.bridge = original_bridge
    assert adapter.delivery.effect_coverage(event)['complete'] is False
    assert (await adapter.capabilities['web.work_effect_coverage'](event))['complete'] is False
    with pytest.raises(ConnectionError):
        await wrapped.complete(adapter.request(), fail=True)
    assert adapter.delivery.effect_coverage(event)['complete'] is False


async def test_native_callback_observations_never_promote_pre_send_trace_to_grant(app):
    from amplifier_web.coordination_delivery import WORK_ATTEMPT
    adapter, _, required = await installed_work_adapter(app)
    await adapter.fill(required)
    checked = []
    class Provider:
        async def complete(self, value, **kwargs):
            delivery, attempt = WORK_ATTEMPT.get()
            delivery.native_work_event(attempt, 'native.send', {
                'type': 'response.create', 'input': [{'type': 'compaction', 'encrypted_content': 'opaque'}]})
            for response in ('response-one', 'response-two'):
                delivery.native_work_event(attempt, 'native.receive', {
                    'type': 'response.created', 'response': {'id': response}})
                delivery.native_work_event(attempt, 'native.receive', {
                    'type': 'response.output_item.done', 'response_id': response, 'item': {
                        'type': 'function_call', 'call_id': response + '-call',
                        'name': 'delegate', 'arguments': '{"instruction":"synthetic"}'}})
                checked.append(delivery.effect_coverage({
                    'tool_call_id': response + '-call', 'tool_name': 'delegate',
                    'tool_input': {'instruction': 'synthetic'}}))
            return {}  # Final aggregate cannot relabel earlier call origins.
    wrapped = adapter.capabilities['web.provider_transform'](Provider())
    await wrapped.complete(adapter.request())
    assert len(checked) == 2 and all(row['complete'] is False for row in checked)
    assert all(row['effectAuthorized'] is False for row in checked)
    # Controlled callback fixture only, not a provider-wire/native acceptance claim.
    assert WORK_ATTEMPT.get() is None


async def test_budget_only_and_cancelled_request_never_certify_a_tool(app):
    import asyncio
    from amplifier_web.coordination_delivery import WORK_ATTEMPT
    adapter, _, required = await installed_work_adapter(app)
    await adapter.fill(required)
    started = asyncio.Event()
    class Provider:
        async def request_budget(self, value, **kwargs):
            return {'tokens': 100}
        async def complete(self, value, **kwargs):
            started.set()
            await asyncio.Event().wait()
    wrapped = adapter.capabilities['web.provider_transform'](Provider())
    original = adapter.request()
    await wrapped.request_budget(original)
    assert not adapter.delivery.attempts
    assert not adapter.delivery.calls
    sending = asyncio.create_task(wrapped.complete(original))
    try:
        await started.wait()
        sending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await sending
    finally:
        sending.cancel()
        await asyncio.gather(sending, return_exceptions=True)
    assert not adapter.delivery.calls
    assert all(a['status'] == 'unknown' for a in adapter.delivery.attempts.values())
    assert WORK_ATTEMPT.get() is None


async def test_effect_observation_rechecks_stop_without_grant_or_replay(app):
    adapter, child, required = await installed_work_adapter(app)
    await adapter.fill(required)
    class Provider:
        async def complete(self, value, **kwargs):
            return SimpleNamespace(model_dump=lambda: {'content': [{
                'type': 'tool_call', 'id': 'bound-write', 'name': 'write_file', 'input': {'path': 'owned'}}]})
    wrapped = adapter.capabilities['web.provider_transform'](Provider())
    await wrapped.complete(adapter.request())
    event = {'tool_call_id': 'bound-write', 'tool_name': 'write_file', 'tool_input': {'path': 'owned'}}
    owner = adapter.delivery.begin_work.__self__
    original_bridge = owner.bridge
    async def changed_during_check(operation, args):
        if operation == 'work.invocation.revalidate':
            child['status'] = 'stopped'
            child['interruptionRevision'] = child.get('interruptionRevision', 0) + 1
        return await original_bridge(operation, args)
    owner.bridge = changed_during_check
    value = await adapter.capabilities['web.work_effect_coverage'](event)
    assert value['complete'] is False and value['effectAuthorized'] is False
    assert len(adapter.delivery.attempts) == 1  # No reread, regrant, or provider retry.


@pytest.mark.parametrize('work_bound', [False, True])
async def test_stream_attempt_context_does_not_cross_yield_or_aclose_task(work_bound):
    import asyncio
    from amplifier_web.coordination_delivery import WORK_ATTEMPT
    required = available_requirements() if work_bound else unavailable_requirements()
    seen, closed = [], []
    async def bridge(operation, args):
        if operation == 'work.invocation.revalidate':
            return {'current': True, 'required': required, 'effectAuthorized': False}
        return {'work': {'available': True, 'discovery': empty_discovery(), 'required': required}}
    class Provider:
        async def stream(self, value, **kwargs):
            try:
                seen.append(WORK_ATTEMPT.get())
                yield 'first'
                seen.append(WORK_ATTEMPT.get())
                yield 'second'
            finally:
                closed.append(WORK_ATTEMPT.get())
    delivery = CoordinationDelivery(Previous(), bridge)
    iterator = SurfaceProvider(Provider(), delivery).stream(request())
    async def next_in_child():
        assert WORK_ATTEMPT.get() is None
        value = await anext(iterator)
        assert WORK_ATTEMPT.get() is None
        return value
    assert await asyncio.create_task(next_in_child()) == 'first'
    assert await asyncio.create_task(next_in_child()) == 'second'
    assert WORK_ATTEMPT.get() is None
    await asyncio.create_task(iterator.aclose())
    assert WORK_ATTEMPT.get() is None
    assert len(seen) == 2 and len(closed) == 1
    assert seen[0][1] is seen[1][1] is closed[0][1]
    assert seen[0][1]['status'] == 'unknown'  # Closed before a terminal response.
    assert not delivery.calls


@pytest.mark.parametrize('work_bound', [False, True])
@pytest.mark.parametrize('ending', ['completed', 'error', 'cancelled'])
async def test_stream_attempt_restores_caller_and_closes_on_terminal_paths(work_bound, ending):
    import asyncio
    from amplifier_web.coordination_delivery import WORK_ATTEMPT
    required = available_requirements() if work_bound else unavailable_requirements()
    entered = asyncio.Event()
    observed, closed = [], []
    async def bridge(operation, args):
        if operation == 'work.invocation.revalidate':
            return {'current': True, 'required': required, 'effectAuthorized': False}
        return {'work': {'available': True, 'discovery': empty_discovery(), 'required': required}}
    class Provider:
        async def stream(self, value, **kwargs):
            try:
                observed.append(WORK_ATTEMPT.get())
                yield 'first'
                observed.append(WORK_ATTEMPT.get())
                entered.set()
                if ending == 'error':
                    raise RuntimeError('Synthetic stream failure')
                if ending == 'cancelled':
                    await asyncio.Event().wait()
            finally:
                closed.append(WORK_ATTEMPT.get())
    delivery = CoordinationDelivery(Previous(), bridge)
    iterator = SurfaceProvider(Provider(), delivery).stream(request())
    sentinel = ('unrelated-caller', {'status': 'caller-state'})
    token = WORK_ATTEMPT.set(sentinel)
    pending = None
    try:
        assert await anext(iterator) == 'first'
        assert WORK_ATTEMPT.get() is sentinel
        async def consume():
            try:
                return await anext(iterator)
            finally:
                assert WORK_ATTEMPT.get() is sentinel
        pending = asyncio.create_task(consume())
        if ending == 'cancelled':
            await entered.wait()
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        elif ending == 'error':
            with pytest.raises(RuntimeError, match='Synthetic stream failure'):
                await pending
        else:
            with pytest.raises(StopAsyncIteration):
                await pending
        assert WORK_ATTEMPT.get() is sentinel
        await asyncio.create_task(iterator.aclose())
        assert WORK_ATTEMPT.get() is sentinel
        assert len(closed) == 1 and len(observed) == 2
        attempt = observed[0][1]
        assert observed[1][1] is closed[0][1] is attempt
        assert attempt['status'] == ('returned' if ending == 'completed' else 'unknown')
        assert not delivery.calls
    finally:
        if pending and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await iterator.aclose()
        WORK_ATTEMPT.reset(token)


@pytest.mark.parametrize('unavailable', [False, True])
@pytest.mark.parametrize('ending', ['completed', 'error', 'cancelled', 'closed'])
async def test_stream_cannot_reuse_completed_call_coverage_at_outward_yield(app, unavailable, ending):
    import asyncio
    from amplifier_web.coordination_delivery import WORK_ATTEMPT
    adapter, _, required = await installed_work_adapter(app)
    await adapter.fill(required)
    event = {'tool_call_id': 'same-call', 'tool_name': 'synthetic_write', 'tool_input': {'value': 'same'}}
    started = asyncio.Event()
    class Provider:
        async def complete(self, value, **kwargs):
            return {'content': [{'type': 'tool_call', 'id': event['tool_call_id'],
                                 'name': event['tool_name'], 'input': event['tool_input']}]}
        async def stream(self, value, **kwargs):
            yield {'type': 'tool_call', 'id': event['tool_call_id'],
                   'name': event['tool_name'], 'input': event['tool_input']}
            started.set()
            if ending == 'error':
                raise RuntimeError('Synthetic stream error')
            if ending == 'cancelled':
                await asyncio.Event().wait()
    wrapped = adapter.capabilities['web.provider_transform'](Provider())
    await wrapped.complete(adapter.request())
    assert (await adapter.delivery.recheck_effect_coverage(event))['complete'] is True
    owner = adapter.delivery.begin_work.__self__
    original_bridge = owner.bridge
    if unavailable:
        async def offline(operation, args):
            if operation == 'coordination.current':
                raise ConnectionError('Synthetic unavailable projection')
            return await original_bridge(operation, args)
        owner.bridge = offline
    iterator = wrapped.stream(adapter.request())
    pending = None
    try:
        assert (await anext(iterator))['id'] == 'same-call'
        assert WORK_ATTEMPT.get() is None  # The token must not cross yield.
        # Actual consumer-side lookup, outside the provider's context.
        assert (await adapter.delivery.recheck_effect_coverage(event))['complete'] is False
        if ending == 'closed':
            await asyncio.create_task(iterator.aclose())
        else:
            pending = asyncio.create_task(anext(iterator))
            if ending == 'cancelled':
                await started.wait()
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
            else:
                with pytest.raises(RuntimeError if ending == 'error' else StopAsyncIteration):
                    await pending
        owner.bridge = original_bridge
        result = await adapter.delivery.recheck_effect_coverage(event)
        assert result['complete'] is False and result['effectAuthorized'] is False
    finally:
        owner.bridge = original_bridge
        if pending and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await iterator.aclose()


async def test_complete_overlapping_an_unqualified_stream_cannot_restore_old_coverage(app):
    import asyncio
    adapter, _, required = await installed_work_adapter(app)
    await adapter.fill(required)
    entered, release = asyncio.Event(), asyncio.Event()
    class Provider:
        async def complete(self, value, **kwargs):
            if kwargs.get('wait'):
                entered.set()
                await release.wait()
            return {'content': [{'type': 'tool_call', 'id': kwargs['identity'],
                                 'name': 'synthetic_write', 'input': {}}]}
        async def stream(self, value, **kwargs):
            yield 'unqualified stream'
    wrapped = adapter.capabilities['web.provider_transform'](Provider())
    pending = asyncio.create_task(wrapped.complete(adapter.request(), identity='overlap', wait=True))
    iterator = None
    try:
        await entered.wait()
        iterator = wrapped.stream(adapter.request())
        await anext(iterator)
        release.set()
        await pending
        event = {'tool_call_id': 'overlap', 'tool_name': 'synthetic_write', 'tool_input': {}}
        assert adapter.delivery.effect_coverage(event)['complete'] is False
        await iterator.aclose()
        assert adapter.delivery.effect_coverage(event)['complete'] is False
        await wrapped.complete(adapter.request(), identity='fresh')
        assert adapter.delivery.effect_coverage({**event, 'tool_call_id': 'fresh'})['complete'] is True
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
        if iterator:
            await iterator.aclose()


@pytest.mark.parametrize('prior', ['unbound', 'nested'])
async def test_identical_unbound_or_nested_page_does_not_poison_direct_read(app, prior):
    from amplifier_core import ToolResult
    from amplifier_core.message_models import ToolCall
    from amplifier_web.output_image_delivery import successful_output
    adapter, _, required = await installed_work_adapter(app)
    manifest = await adapter.read(required['requirementsRef'], limit=1)
    ref = next(row['readRef'] for row in manifest['items'] if row['kind'] == 'promise')
    args = {'operation': 'dispatch', 'args': {'action': ref['action'], 'args': {**ref['args'], 'limit': 700, 'offset': 0}}}
    if prior == 'unbound':
        earlier = (await adapter.tools['app_control'].execute(args)).output
    else:
        # Real ApprovedDispatch lease creates nested call IDs/hook attribution.
        # This synthetic tool executes a read only; no program interpreter or writes.
        captured = []
        async def nested(input):
            lease = adapter.loop.approved_dispatch.bind()
            try:
                result = await lease.call('app_control', args, request_id='read-page')
                assert result['success'] is True, result
                captured.append(result['output'])
                return ToolResult(success=True, output=result['output'])
            finally:
                lease.close()
        adapter.tools['tool_exec'] = SimpleNamespace(name='tool_exec', execute=nested)
        adapter.loop.config['programmatic_dispatch'] = True
        with adapter.loop.approved_dispatch.run_scope(adapter.coordinator):
            await adapter.loop._execute_tool_only(
                ToolCall(id='outer-read', name='tool_exec', arguments={}),
                adapter.tools, adapter.hooks, None, adapter.coordinator)
        assert len(captured) == 1
        earlier = successful_output(captured[0])
    current = await adapter.read(ref, limit=700, offset=0)
    assert earlier['result'] == current  # Deterministic page, identical content.
    await adapter.fill(required)
    prepared = await adapter.delivery.prepare(adapter.request(), None)
    coverage = adapter.delivery.request_coverage(prepared)
    assert coverage['complete'] is True, json.dumps(coverage, ensure_ascii=False)


async def test_differing_first_manifest_pages_remain_explicitly_ambiguous(app):
    adapter, _, required = await installed_work_adapter(app)
    initial = await adapter.read(required['requirementsRef'])  # Preserve old default20 setup.
    assert len(initial['items']) > 1 and initial['truncated'] is False
    await adapter.fill(required)  # limit1 starts a conflicting retained first page.
    prepared = await adapter.delivery.prepare(adapter.request(), None)
    coverage = adapter.delivery.request_coverage(prepared)
    assert coverage['complete'] is False, json.dumps(coverage, ensure_ascii=False)
    assert coverage['reason'] == 'Required manifest page is absent or ambiguous in fitted content.'
    assert coverage['effectAuthorized'] is False


async def test_concurrent_identical_pages_bind_exact_execution_ids_and_reject_forgery(app):
    import asyncio
    from amplifier_web.coordination_delivery import _work_pair_retained as pair_retained
    adapter, _, required = await installed_work_adapter(app)
    manifest = await adapter.read(required['requirementsRef'])
    ref = next(row['readRef'] for row in manifest['items'] if row['kind'] == 'promise')
    pages = await asyncio.gather(adapter.read(ref, offset=0, limit=700),
                                 adapter.read(ref, offset=0, limit=700))
    assert pages[0] == pages[1]
    rows = [r for r in adapter.delivery.reads if r['input']['args']['action'] == ref['action']]
    assert len(rows) == 2 and len({r['tool_call_id'] for r in rows}) == 2
    assert all(pair_retained(adapter.request(), r) for r in rows)
    for change in ('id', 'input', 'body', 'input_type', 'body_type'):
        forged = adapter.request().model_copy(deep=True)
        tool = next(m for m in forged.messages if m.role == 'tool' and m.tool_call_id == rows[0]['tool_call_id'])
        if change == 'id':
            tool.tool_call_id = 'forged'
        elif change == 'body':
            tool.content += ' modified'
        elif change == 'body_type':
            value = json.loads(tool.content)
            from amplifier_web.output_image_delivery import successful_output
            successful_output(value)['result']['offset'] = False
            tool.content = json.dumps(value)
        else:
            assistant = next(m for m in forged.messages if m.role == 'assistant'
                             and any(c.get('id') == rows[0]['tool_call_id'] for c in m.tool_calls or []))
            assistant.tool_calls[0]['arguments']['args']['args']['offset'] = False if change == 'input_type' else 1
        assert not pair_retained(forged, rows[0])


async def test_stale_visual_refuses_send_then_explicit_budget_refits_same_request():
    from amplifier_web.voice_visual_delivery import VoiceVisualDelivery
    from amplifier_web.output_image_delivery import OutputImageDelivery
    from amplifier_web.memory_delivery import MemoryDelivery
    required = available_requirements()
    stale, measured, sent = False, [], []
    class Surfaces(Previous):
        epoch = ['input-1']
        image_capabilities = SimpleNamespace(supports=AsyncMock(return_value=True))
    async def bridge(operation, args):
        if operation == 'coordination.current':
            return {'work': {'available': True, 'discovery': empty_discovery(), 'required': required}}
        if operation == 'work.invocation.revalidate':
            return {'current': True, 'required': required, 'effectAuthorized': False}
        if operation == 'memory.context':
            return {'items': []}
        if operation == 'voice.visual.read':
            if stale:
                raise ValueError('Consent ended')
            return {'_image': 'c3ludGhldGlj', 'source': 'Synthetic capture'}
        raise AssertionError(operation)
    work = CoordinationDelivery(Surfaces(), bridge)
    voice = VoiceVisualDelivery(work, bridge)
    receipt = voice.remember({'accepted': True, 'result': {'id': 'capture-1', 'callId': 'voice-1'}})
    delivery = MemoryDelivery(OutputImageDelivery(voice, bridge), bridge)
    class Provider:
        async def request_budget(self, value, **kwargs):
            measured.append(value.model_dump_json())
            return {'tokens': 100}
        async def complete(self, value, **kwargs):
            sent.append(value.model_dump_json())
            return 'done'
    wrapped = SurfaceProvider(Provider(), delivery)
    original = request(Message(role='tool', name='app_control', tool_call_id='visual-read', content=json.dumps(receipt)))
    await wrapped.request_budget(original)
    stale = True
    with pytest.raises(ValueError, match='refit'):
        await wrapped.complete(original)
    assert sent == []
    # No silent reprepare/send after the rejected measured request.
    with pytest.raises(ValueError, match='budget|refit'):
        await wrapped.complete(original)
    await wrapped.request_budget(original)
    assert len(measured) == 2 and measured[0] != measured[1]
    assert await wrapped.complete(original) == 'done'
    assert sent == measured[1:]


async def test_real_tool_pre_receives_exact_completed_call_origin(app):
    from amplifier_core import HookResult, ToolResult
    from amplifier_core.message_models import ToolCall
    adapter, _, required = await installed_work_adapter(app)
    await adapter.fill(required)
    seen, writes = [], []
    class Provider:
        async def complete(self, value, **kwargs):
            return {'content': [{'type': 'tool_call', 'id': 'fixture-effect',
                                 'name': 'synthetic_write', 'input': {'text': 'bounded'}}]}
    async def observe(event, data):
        if data.get('tool_name') == 'synthetic_write':
            seen.append((copy.deepcopy(data), await adapter.capabilities['web.work_effect_coverage'](data)))
        return HookResult()
    async def write(input):
        writes.append(copy.deepcopy(input))  # In-memory fixture, not a host write.
        return ToolResult(success=True, output={'recorded': True})
    adapter.tools['synthetic_write'] = SimpleNamespace(name='synthetic_write', execute=write)
    adapter.hooks.register('tool:pre', observe, name='fixture-origin-observer')
    wrapped = adapter.capabilities['web.provider_transform'](Provider())
    response = await wrapped.complete(adapter.request())
    call = response['content'][0]
    await adapter.loop._execute_tool_only(
        ToolCall(id=call['id'], name=call['name'], arguments=call['input']),
        adapter.tools, adapter.hooks, None, adapter.coordinator)
    assert writes == [{'text': 'bounded'}] and len(seen) == 1
    assert seen[0][0]['tool_call_id'] == 'fixture-effect'
    assert seen[0][1]['complete'] is True and seen[0][1]['effectAuthorized'] is False
