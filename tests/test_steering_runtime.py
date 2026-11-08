"""Steering contracts run with worker dependencies, without the host service."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web.conversation_steering import submit


async def test_worker_rechecks_target_after_input_preparation(monkeypatch):
    pytest.importorskip('amplifier_module_loop_live.runtime')
    runtime = SimpleNamespace(closed=False, generation={'id': 'old'}, max_input_chars=10000)
    capability = {'version': 1, 'mode': 'request_boundary', 'submit': AsyncMock()}
    controls = SimpleNamespace(coordinator=SimpleNamespace(
        get_capability=lambda name: capability if name == 'live.steering' else None))
    async def prepare(*args, **kwargs):
        runtime.generation = {'id': 'new'}
        return 'pause'
    monkeypatch.setattr('amplifier_web.message_interactions.prepare_input', prepare)
    result = await submit(controls, runtime, {'text': 'pause', 'inputId': 'correction',
        'targetGenerationId': 'old'}, None, lambda: 0)
    assert result['accepted'] is False
    capability['submit'].assert_not_awaited()


@pytest.mark.parametrize('late', [False, True])
@pytest.mark.parametrize('peer', [False, True])
async def test_real_loop_drains_bursts_including_final_checkpoint_without_new_generation(monkeypatch, tmp_path, late, peer):
    """Real core/context/loop, scripted provider/tool; no model account required."""
    live = pytest.importorskip('amplifier_module_loop_live.runtime')
    pytest.importorskip('amplifier_module_context_simple')
    from amplifier_core import AmplifierSession, ToolResult
    from amplifier_core.message_models import ChatResponse, TextBlock, ToolCall
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path))
    requests, responses = asyncio.Queue(), asyncio.Queue()
    class Provider:
        async def complete(self, request, **kwargs):
            await requests.put(request)
            return await responses.get()
        def parse_tool_calls(self, response):
            return response.tool_calls or []
        def get_info(self):
            return SimpleNamespace(default_model='fixture')
    class Step:
        name, description, input_schema = 'step', 'One step', {'type': 'object', 'properties': {}}
        async def execute(self, args):
            return ToolResult(success=True, output={'done': True})
    runtime = live.Runtime()
    session = AmplifierSession({'session': {'orchestrator': {'module': 'loop-live',
        'config': {'configured_bundle': True, 'min_delay_between_calls_ms': 0}},
        'context': {'module': 'context-simple'}}, 'providers': []}, session_id=runtime.session_id)
    task = None
    try:
        await session.initialize()
        await session.coordinator.mount('providers', Provider(), name='fixture')
        await session.coordinator.mount('tools', Step(), name='step')
        session.coordinator.register_capability('live.runtime', runtime)
        task = asyncio.create_task(session.execute(''))
        await runtime.wait_for(lambda e: e['type'] == 'session.ready', 5)
        await runtime.submit(live.Input('user', 'Do several steps', id='original'))
        await asyncio.wait_for(requests.get(), 5)
        generation = runtime.generation['id']
        entered, release = asyncio.Event(), asyncio.Event()
        if late:
            async def checkpoint(*args):
                entered.set()
                await release.wait()
            session.coordinator.register_capability('live.checkpoint', checkpoint)
            await responses.put(ChatResponse(content=[TextBlock(text='Already finished')]))
            await asyncio.wait_for(entered.wait(), 5)
        bodies = []
        for i in range(3):
            args = {'text': f'Correction {i}', 'inputId': f'correction-{i}',
                    'targetGenerationId': generation}
            controls = SimpleNamespace(coordinator=session.coordinator)
            body = args['text']
            if peer:
                from amplifier_web.collaboration_input import steer
                from amplifier_operations.coordination import peer_input
                controls.tasks = SimpleNamespace(record=lambda: {}, continuation_allowed=AsyncMock(return_value=True))
                message = {'peerEnvelope': {'inputId': args['inputId'], 'requestId': args['inputId'],
                    'recipientSessionId': runtime.session_id, 'senderSessionId': 'source',
                    'mode': 'steer'}, 'text': body}
                authorize = AsyncMock(return_value={'admitted': True, 'message': message})
                result = await steer(controls, runtime, args, None, authorize)
                assert authorize.await_count == 2
                body = peer_input(message['peerEnvelope'], body)
            else:
                result = await submit(controls, runtime, args, None, lambda: 0)
            bodies.append(body)
            assert result['accepted'] and result['disposition'] == 'queued'
        if late:
            release.set()
        else:
            await responses.put(ChatResponse(content=[], tool_calls=[ToolCall(id='step-1', name='step', arguments={})]))
        request = await asyncio.wait_for(requests.get(), 5)
        text = str(request.messages)
        positions = [text.index(f'Correction {i}') for i in range(3)]
        assert positions == sorted(positions)
        assert all(text.count(f'Correction {i}') == 1 for i in range(3))
        assert text.count('Do several steps') == 1
        await responses.put(ChatResponse(content=[TextBlock(text='All corrections considered')]))
        finished = await runtime.wait_for(lambda e: e['type'] == 'generation.finished'
                                           and e['generation_id'] == generation, 5)
        await runtime.wait_for(lambda e: e['type'] == 'session.idle' and e['sequence'] > finished['sequence'], 5)
        assert len([e for e in runtime.events if e['type'] == 'generation.started']) == 1
        assert requests.empty()
        assert finished['generation_id'] == generation
        assert all(f'correction-{i}' in finished['input_ids'] for i in range(3))
        # The producer already persists steering identity. The display reader
        # must not discard it merely because delivery occurred mid-generation.
        from amplifier_web.automatic_history import display_message
        transcript = await session.coordinator.get('context').get_messages()
        inputs = [row for row in transcript if row.get('role') == 'user'
                  and row.get('content') in ['Do several steps', *bodies]]
        markers = [(row.get('metadata') or {}).get('amplifier_input') for row in inputs]
        assert [(marker['id'], marker['kind']) for marker in markers] == [
            ('original', 'user'), *((f'correction-{i}', 'steer') for i in range(3))]
        assert [display_message(row, i, {'id': runtime.session_id}).get('nativeInputId')
                for i, row in enumerate(inputs)] == ['original', *[f'correction-{i}' for i in range(3)]]
    finally:
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await session.cleanup()


@pytest.mark.parametrize('flag', ['steering_closed', 'stop_requested'])
async def test_worker_rejects_closing_or_stopped_run_before_admission(monkeypatch, flag):
    pytest.importorskip('amplifier_module_loop_live.runtime')
    runtime = SimpleNamespace(closed=False, generation={'id': 'run'}, max_input_chars=10000)
    setattr(runtime, flag, True)
    capability = {'version': 1, 'mode': 'request_boundary', 'submit': AsyncMock()}
    controls = SimpleNamespace(coordinator=SimpleNamespace(
        get_capability=lambda name: capability if name == 'live.steering' else None))
    monkeypatch.setattr('amplifier_web.message_interactions.prepare_input', AsyncMock(return_value='pause'))
    result = await submit(controls, runtime, {'text': 'pause', 'inputId': 'late',
        'targetGenerationId': 'run'}, None, lambda: 0)
    assert result['accepted'] is False
    capability['submit'].assert_not_awaited()
