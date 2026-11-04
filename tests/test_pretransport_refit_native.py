"""Actual Core/Streaming preparation; synthetic model and retained visual data."""
import asyncio
import copy
import importlib.util
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from amplifier_core import HookResult, ProviderInfo, ToolResult
from amplifier_core.message_models import ChatRequest, ChatResponse, Message, TextBlock, ToolCall, Usage
from amplifier_foundation.bundle import Bundle, PreparedBundle, BundleModuleResolver

from amplifier_web.coordination_delivery import CoordinationDelivery
from amplifier_web.surface_delivery import SurfaceProvider
from amplifier_web.voice_visual_delivery import VoiceVisualDelivery
from amplifier_web.output_image_delivery import OutputImageDelivery, pair_retained
from amplifier_web.host.session import SelectedProvider
from test_coordination_delivery import Previous, available_requirements, empty_discovery
from test_output_image_pairs import pair, identities, pixels, status


async def native_case(mode='stale_visual', max_iterations=-1, legacy_budget=False, output_pair=None,
                      selected=False, preparation_context=False, full_access=False):
    plan = {'session': {'orchestrator': {'module': 'loop-streaming', 'config': {
        'use_streaming': False, 'extended_thinking': True,
        'min_delay_between_calls_ms': 0, 'max_iterations': max_iterations,
        'ephemeral_injection_mode': 'tail'}}, 'context': {'module': 'context-simple'}}}
    paths = {name: Path(importlib.util.find_spec('amplifier_module_' + name.replace('-', '_')).origin).parent
             for name in ('loop-streaming', 'context-simple')}
    session = await PreparedBundle(plan, BundleModuleResolver(paths),
        Bundle(name='refit-fixture', session=plan['session'])).create_session(
            session_id=output_pair.sid if output_pair else 'refit-fixture')
    scope = {'rootSessionId': session.session_id, 'runtimeSessionId': session.session_id,
             'generationId': 'generation-1', 'inputIds': ['original-input'],
             'inputCoverage': 'complete', 'stopRevision': 0}
    required = available_requirements()
    required.update(rootSessionId=session.session_id, generationId=scope['generationId'],
                    inputIds=scope['inputIds'])
    required['coverage']['supportedInputIds'] = scope['inputIds']
    required['requirementsRef']['args']['sessionId'] = session.session_id
    state = SimpleNamespace(stale=False, measurements=[], transports=[], tools=0, preparations=0,
                            budget_options=[], dispatch_options=[], image_reads=0,
                            timeout_cancelled=False, timeout_elapsed=None, bound=[], context_snapshots=0,
                            context_timeout_cancelled=False, authority_waiting=asyncio.Event(),
                            authority_cancelled=False, authority_checks=0, full_access=full_access)
    pair_input = {'operation': 'dispatch', 'args': {'action': 'outputs.images',
        'args': {'images': identities(output_pair.rows)}}} if output_pair else None

    class Surfaces(Previous):
        epoch = ['original-input']
        # This fixture supplies a successfully observed input identity.
        epoch_available = True
        image_capabilities = SimpleNamespace(supports=AsyncMock(return_value=True))

    def change_work(version):
        assert version != required['version']
        required['version'] = version
        required['requirementsRef']['args']['expectedVersion'] = version

    async def bridge(operation, args):
        if operation == 'context.prepare':
            state.context_snapshots += 1
            if mode == 'pair_refit_context_timeout' and state.context_snapshots == 3:
                try:
                    await asyncio.Event().wait()
                finally:
                    state.context_timeout_cancelled = True
            return {'coordination.current': {'available': True,
                'value': await bridge('coordination.current', {})}}
        if operation == 'coordination.current':
            state.preparations += 1
            if mode.startswith('pair_refit_observation_unavailable') and state.preparations == 3:
                return {'status': 'unavailable'}
            if mode == 'scope_during_refit' and state.preparations == 3:
                scope['generationId'] = 'changed-generation'
            # Real bridge responses cross JSON IPC; saved observations cannot
            # share mutable dictionaries with the current host owner.
            return {'work': {'available': True, 'discovery': empty_discovery(),
                             'required': copy.deepcopy(required)}}
        if operation == 'work.invocation.revalidate':
            state.authority_checks += 1
            if len(state.measurements) >= 2:
                if mode == 'pair_refit_authority_delay':
                    await asyncio.sleep(1.65)
                elif mode in {'pair_refit_authority_unavailable', 'pair_refit_permission_revoked'}:
                    raise TimeoutError('Synthetic bridge response deadline expired')
                elif mode == 'pair_refit_authority_changed':
                    return {'current': False, 'effectAuthorized': False, 'required': required}
                elif mode == 'pair_refit_authority_stop':
                    scope['stopRevision'] += 1
                elif mode == 'pair_refit_authority_cancel':
                    state.authority_task = asyncio.current_task()
                    state.authority_waiting.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        state.authority_cancelled = True
            if mode == 'authority_unavailable' and len(state.measurements) > 1:
                raise TimeoutError('Synthetic unavailable authority')
            if mode == 'authority_typed_error' and len(state.measurements) > 1:
                from amplifier_web.request_refit import RequestFitChanged
                raise RequestFitChanged('content_changed')
            if mode == 'currentness_changed' and len(state.measurements) > 1:
                return {'current': False, 'effectAuthorized': False, 'required': required}
            return {'current': True, 'effectAuthorized': False, 'required': required}
        if operation == 'voice.visual.read':
            if state.stale:
                raise ValueError('Synthetic observation revoked')
            return {'_image': 'c3ludGhldGlj', 'source': 'Synthetic capture'}
        if operation == 'context.manifest':
            return {'inputIds': list(scope['inputIds'])}
        if operation == 'outputs.images.read':
            assert output_pair is not None
            assert args == {'images': identities(output_pair.rows)}
            state.image_reads += 1
            if state.image_reads == 2:
                started = time.monotonic()
                try:
                    # Use the production two-second wait_for in revalidate;
                    # do not substitute TimeoutError or shorten its deadline.
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    state.timeout_cancelled = True
                    state.timeout_elapsed = time.monotonic() - started
                    if mode == 'pair_stop':
                        scope['stopRevision'] += 1
                    elif mode == 'pair_input_changed':
                        scope['inputIds'] = ['original-input', 'later-input']
                    elif mode == 'pair_work_changed':
                        change_work('b' * 64)
                    raise
            if mode == 'pair_timeout_without_pixels' and state.image_reads > 2:
                raise ValueError('Synthetic saved-image read remains unavailable')
            return await output_pair.app.app_bridge(operation, args, output_pair.sid)
        raise AssertionError(operation)

    from amplifier_web.preparation_context import PreparationContext
    context = PreparationContext(bridge) if preparation_context else None
    read = context.read if context else bridge
    work = CoordinationDelivery(Surfaces(), read, origin_scope=lambda: copy.deepcopy(scope),
                                full_access=lambda: state.full_access)
    visual = OutputImageDelivery(work, read) if output_pair else VoiceVisualDelivery(work, read)
    state.delivery = visual

    class Tool:
        name = 'app_control'
        description = 'Read a synthetic observation once'
        input_schema = {'type': 'object', 'properties': {}}

        async def execute(self, arguments):
            state.tools += 1
            if output_pair:
                assert arguments == pair_input
                receipt = await output_pair.app.dispatch('outputs.images', {
                    'sessionId': output_pair.sid, 'images': identities(output_pair.rows)})
                return ToolResult(success=True, output=visual.remember_pair(receipt, arguments))
            return ToolResult(success=True, output=visual.remember({
                'accepted': True, 'result': {'id': 'capture-1', 'callId': 'visual-1'}}))

    class Provider:
        name = 'fixture'

        def get_info(self):
            return ProviderInfo(id=self.name, display_name='Fixture',
                                capabilities=['tools'], defaults={'model': 'fixture'})

        def parse_tool_calls(self, response):
            return response.tool_calls or []

        async def request_budget(self, request, *, context_estimate=0, request_options=None, **kwargs):
            state.measurements.append(request.model_dump_json())
            state.budget_options.append(copy.deepcopy(request_options))
            # Permission changes after the first measurement containing pixels,
            # before the provider's transport entrypoint is reached.
            if any(isinstance(m.content, list) and any(b.type == 'image' for b in m.content)
                   for m in request.messages):
                if mode not in {'authority_unavailable', 'authority_typed_error', 'currentness_changed',
                                'provider_error', 'provider_typed_error', 'binding_missing'}:
                    state.stale = True
                if mode == 'authority_changed':
                    change_work('b' * 64)
                elif mode == 'stop':
                    scope['stopRevision'] += 1
                elif mode == 'new_generation':
                    scope['generationId'] = 'changed-generation'
                elif mode == 'new_input':
                    scope['inputIds'] = ['original-input', 'later-input']
                elif mode == 'binding_missing' and len(state.measurements) == 2:
                    work.requests.clear()
            if len(state.measurements) == 3 and mode == 'pair_refit_budget_work_changed':
                change_work('d' * 64)
            if len(state.measurements) == 3 and mode in {
                    'pair_refit_permission_revoked', 'pair_refit_observation_unavailable_revoked'}:
                state.full_access = False
            if len(state.measurements) == 3 and mode == 'pair_refit_scope_stop':
                scope['stopRevision'] += 1
            if len(state.measurements) == 3 and mode == 'repeated_change':
                request.messages.append(Message(role='user', content='changed after refit'))
            return {'estimated_input_tokens': 10001 if mode == 'oversized_refit' and len(state.measurements) == 3 else 100,
                    'input_limit_tokens': 10000,
                    'context_token_budget': context_estimate,
                    'measurement': {'kind': 'provider_count', 'input_tokens': 100,
                                    'source': 'fixture'}}

        async def complete(self, request, **kwargs):
            state.transports.append(request.model_dump_json())
            state.dispatch_options.append(copy.deepcopy(kwargs))
            if len(state.transports) == 2:
                if mode == 'provider_error':
                    raise ValueError('Work request bytes changed after fitting; refit before transport.')
                if mode == 'provider_typed_error':
                    from amplifier_web.request_refit import RequestFitChanged
                    raise RequestFitChanged('content_changed')
            return ChatResponse(content=[] if len(state.transports) == 1 else [TextBlock(text='Done')],
                tool_calls=[ToolCall(id='read-once', name='app_control', arguments=pair_input or {})]
                    if len(state.transports) == 1 else [],
                usage=Usage(input_tokens=100, output_tokens=1, total_tokens=101))

    if legacy_budget:
        named_budget = Provider.request_budget
        async def legacy(self, request, **kwargs):
            assert 'request_options' not in kwargs
            return await named_budget(self, request, **kwargs)
        Provider.request_budget = legacy
    await session.coordinator.mount('tools', Tool(), name='app_control')
    if output_pair:
        async def bind_image_pair(event, data):
            visual.bind_pair(data)
            state.bound.append(copy.deepcopy(data))
            return HookResult()
        session.coordinator.hooks.register('tool:post', bind_image_pair, name='fixture-image-pair-receipt')
    original = Provider()
    wrapped = SurfaceProvider(original, context.wrap(visual) if context else visual)
    provider = SelectedProvider(original, {'model': 'selected-model', 'effort': 'high',
        'max_output_tokens': 321}, lambda _: wrapped) if selected else wrapped
    await session.coordinator.mount('providers', provider, name='fixture')
    return session, state


@pytest.mark.parametrize('mode', ['stale_visual', 'binding_missing'])
@pytest.mark.parametrize('max_iterations', [-1, 1])
async def test_actual_loop_refits_without_repeating_completed_tool(mode, max_iterations):
    session, state = await native_case(mode, max_iterations)
    try:
        assert await session.execute('Read once, then finish using the remaining valid evidence.') == 'Done'
        assert state.tools == 1
        assert len(state.measurements) == 3
        assert len(state.transports) == 2
        assert state.budget_options == [{'extended_thinking': True}] * 3
        assert state.dispatch_options == [{'extended_thinking': True}] * 2
        assert state.transports[1] == state.measurements[2]
        assert 'c3ludGhldGlj' in state.measurements[1]
        if mode == 'stale_visual':
            assert 'c3ludGhldGlj' not in state.transports[1]
        history = await session.coordinator.get('context').get_messages()
        assert sum(m.get('role') == 'tool' for m in history) == 1
        assert sum(m.get('role') == 'user' and m.get('content') ==
                   'Read once, then finish using the remaining valid evidence.' for m in history) == 1
        # The capped loop adds its existing finalization instruction once.
        assert sum(m.get('role') == 'user' for m in history) == (2 if max_iterations == 1 else 1)
    finally:
        await session.cleanup()


async def test_context_refit_cancels_pending_authority_check_without_sending(pair):
    session, state = await native_case('pair_refit_authority_cancel', output_pair=pair,
        selected=True, preparation_context=True)
    execution = asyncio.create_task(session.execute('Compare the saved images once, then finish.'))
    try:
        await asyncio.wait_for(state.authority_waiting.wait(), 10)
        # Cancel the executing worker task, not Core's outer Python/Rust
        # awaitable proxy, which has its own cancellation lifecycle.
        state.authority_task.cancel()
        # Installed Core reports the inner cancellation through its public
        # RuntimeError boundary; it must not become a Work refusal or send.
        with pytest.raises(RuntimeError, match='Execution failed: CancelledError'):
            await execution
        assert state.authority_cancelled
        assert len(state.transports) == 1 and len(state.measurements) == 3
        assert state.tools == 1 and state.context_snapshots == 2
    finally:
        if getattr(state, 'authority_task', None) is not None and not state.authority_task.done():
            state.authority_task.cancel()
            await asyncio.gather(state.authority_task, return_exceptions=True)
        execution.cancel()
        await asyncio.gather(execution, return_exceptions=True)
        await session.cleanup()


@pytest.mark.parametrize('mode, measurements, transports, reason', [
    ('authority_changed', 2, 1, 'authority_changed'),
    ('authority_unavailable', 2, 1, 'authority_unavailable'),
    ('authority_typed_error', 2, 1, 'authority_unavailable'),
    ('currentness_changed', 2, 1, 'authority_changed'),
    ('stop', 2, 1, 'native_scope_changed'),
    ('new_generation', 2, 1, 'native_scope_changed'),
    ('new_input', 2, 1, 'native_scope_changed'),
    ('scope_during_refit', 2, 1, 'native_scope_changed'),
    ('repeated_change', 3, 1, 'content_changed'),
    ('oversized_refit', 3, 1, 'content_changed'),
    ('provider_error', 2, 2, 'bytes changed'),
    ('provider_typed_error', 2, 2, 'content_changed'),
])
async def test_actual_loop_does_not_replay_changed_authority_or_sent_request(mode, measurements, transports, reason):
    session, state = await native_case(mode)
    try:
        with pytest.raises(RuntimeError, match=reason):
            await session.execute('Read once, then finish using the remaining valid evidence.')
        assert state.tools == 1
        assert len(state.measurements) == measurements
        assert len(state.transports) == transports
        history = await session.coordinator.get('context').get_messages()
        assert sum(m.get('role') == 'tool' for m in history) == 1
        assert sum(m.get('role') == 'user' for m in history) == 1
    finally:
        await session.cleanup()


def test_refusal_diagnostic_names_components_without_request_contents():
    from amplifier_web.request_refit import RequestFitChanged, changed_components
    before = json.dumps({'messages': ['private prompt and pixels'], 'tools': ['private schema']})
    after = json.dumps({'messages': ['different private pixels'], 'tools': ['private schema']})
    error = RequestFitChanged('content_changed', components=changed_components(before, after))
    assert error.components == ('messages',)
    assert str(error) == 'Work request refused before transport: content_changed (messages); refit before transport.'


async def test_actual_loop_legacy_budget_keeps_its_kwargs_shape_during_refit():
    session, state = await native_case(legacy_budget=True)
    try:
        assert await session.execute('Read once, then finish.') == 'Done'
        assert state.tools == 1
        assert state.budget_options == [None] * 3
        assert state.dispatch_options == [{'extended_thinking': True}] * 2
        assert state.transports[-1] == state.measurements[-1]
    finally:
        await session.cleanup()


@pytest.mark.parametrize('mode', ['pair_timeout', 'pair_timeout_without_pixels',
                                  'pair_work_changed', 'pair_input_changed', 'pair_stop'])
@pytest.mark.parametrize('selected', [False, True])
async def test_output_pair_deadline_refits_real_retained_selection_once(pair, mode, selected):
    session, state = await native_case(mode, output_pair=pair, selected=selected)
    try:
        execution = session.execute('Compare the saved images once, then finish.')
        if mode in {'pair_work_changed', 'pair_input_changed', 'pair_stop'}:
            reason = 'authority_changed' if mode == 'pair_work_changed' else 'native_scope_changed'
            with pytest.raises(RuntimeError, match=reason):
                await execution
            assert len(state.measurements) == 2 and len(state.transports) == 1
        else:
            assert await execution == 'Done'
            assert len(state.measurements) == 3 and len(state.transports) == 2
            assert state.transports[-1] == state.measurements[-1]
            sent = ChatRequest.model_validate_json(state.transports[-1])
            if selected:
                assert sent.model == 'selected-model'
                assert sent.reasoning_effort == 'high'
                assert sent.max_output_tokens == 321
                assert state.budget_options == [dict(extended_thinking=True,
                    model='selected-model', reasoning_effort='high')] * 3
                assert state.dispatch_options == [dict(extended_thinking=True,
                    model='selected-model', reasoning_effort='high')] * 2
            manifest = status(sent)
            assert manifest['toolCallId'] == 'read-once'
            assert manifest['requested'] == identities(pair.rows)
            if mode == 'pair_timeout':
                assert pixels(sent) == pair.data
                assert manifest['comparisonReady'] is True
                assert manifest['delivered'] == identities(pair.rows)
            else:
                assert pixels(sent) == []
                assert manifest['comparisonReady'] is False and manifest['delivered'] == []
                assert {row['reasonCode'] for row in manifest['omitted']} == {'image_read_unavailable'}
        assert state.timeout_cancelled is True and state.timeout_elapsed >= 1.9
        assert state.tools == 1 and len(state.bound) == 1
        assert state.bound[0]['tool_name'] == 'app_control'
        assert state.bound[0]['tool_call_id'] == 'read-once'
        assert state.bound[0]['tool_input']['args']['action'] == 'outputs.images'
        observation = state.delivery.observation
        assert observation['tool_call_id'] == 'read-once'
        measured_pair = ChatRequest.model_validate_json(state.measurements[1])
        assert pair_retained(measured_pair, observation) is True
        assert pixels(measured_pair) == pair.data
        assert status(measured_pair)['inspectionReceipt'] == observation['receipt']['result']['inspection']['receipt']
        assert state.delivery.output_selection() == identities(pair.rows)
        history = await session.coordinator.get('context').get_messages()
        assert sum(m.get('role') == 'tool' and m.get('tool_call_id') == 'read-once' for m in history) == 1
        assert sum(m.get('role') == 'user' and m.get('content') ==
                   'Compare the saved images once, then finish.' for m in history) == 1
    finally:
        await session.cleanup()


async def test_pair_refit_does_not_repeat_the_original_context_snapshot(pair):
    session, state = await native_case('pair_refit_context_timeout', output_pair=pair,
                                       selected=True, preparation_context=True)
    try:
        assert await session.execute('Compare the saved images once, then finish.') == 'Done'
        assert state.tools == 1 and len(state.transports) == 2
        assert len(state.measurements) == 3
        assert state.transports[-1] == state.measurements[-1]
        assert state.context_snapshots == 2  # One for each actual model request.
        assert state.timeout_cancelled and not state.context_timeout_cancelled
    finally:
        await session.cleanup()


@pytest.mark.parametrize('mode,reason,measurements', [
    ('pair_work_changed', 'authority_changed', 3),
    ('pair_input_changed', 'native_scope_changed', 2),
    ('pair_stop', 'native_scope_changed', 2),
    ('pair_refit_authority_changed', 'authority_changed', 3),
    ('pair_refit_authority_unavailable', 'authority_unavailable', 3),
    ('pair_refit_authority_stop', 'native_scope_changed', 3),
    ('pair_refit_budget_work_changed', 'authority_changed', 3),
    ('pair_refit_authority_delay', None, 3),
])
async def test_context_refit_keeps_fresh_authority_and_stop_checks(pair, mode, reason, measurements):
    session, state = await native_case(mode, output_pair=pair, selected=True,
        preparation_context=True)
    try:
        execution = session.execute('Compare the saved images once, then finish.')
        if reason:
            with pytest.raises(RuntimeError, match=reason):
                await execution
            assert len(state.transports) == 1 and len(state.measurements) == measurements
        else:
            assert await execution == 'Done'
            assert len(state.transports) == 2 and len(state.measurements) == 3
            assert state.transports[-1] == state.measurements[-1]
        assert state.context_snapshots == 2
        assert state.tools == 1
    finally:
        await session.cleanup()
