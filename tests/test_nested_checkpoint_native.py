"""Real Core/Live/QuickJS dispatch checkpoints, synthetic provider, no network."""
import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
from amplifier_core import AmplifierSession, HookResult, ProviderInfo, ToolResult
from amplifier_core.message_models import ChatResponse, TextBlock, ToolCall, Usage
from amplifier_module_loop_live.runtime import Input, Runtime
from amplifier_module_tool_exec import ProgrammaticTool

from amplifier_web.coordination_delivery import CoordinationDelivery, WORK_NESTED
from amplifier_web.host.session import install_checkpoint_hooks


async def environment(tmp_path, *, count=4, fault=None, parallel=False, direct=False, work_mode=None):
    session = AmplifierSession({'session': {
        'orchestrator': {'module': 'loop-live', 'config': {
            'programmatic_dispatch': True, 'use_streaming': False,
            'min_delay_between_calls_ms': 0}},
        'context': {'module': 'context-simple'}}, 'providers': []})
    await session.initialize()
    c = session.coordinator
    context = c.get('context')
    state = SimpleNamespace(calls=[], checkpoints=[], transports=[], started=asyncio.Event(),
        provider_started=asyncio.Event(), stopped=False)
    runtime = Runtime(session_id=session.session_id)
    def observe(event):
        if event['type'] == 'generation.finished':
            asyncio.create_task(runtime.submit(Input('stop')))
    runtime.observer = observe
    c.register_capability('live.runtime', runtime)
    path = tmp_path / 'checkpoint.json'

    async def checkpoint(status='in_progress', *, timing=None, transcript_required=True):
        state.checkpoints.append(transcript_required)
        if transcript_required:
            rows = await context.get_messages()
            path.write_text(json.dumps(rows))
        return True
    c.register_capability('live.checkpoint', checkpoint)
    install_checkpoint_hooks(c, runtime, checkpoint)
    # Real dispatcher lease plus Unified's own observation, not copied JSON proof.
    if work_mode is None:
        delivery = CoordinationDelivery(None, None)
        delivery.install_dispatch(c)
        async def nested_pre(event, data):
            delivery.observe_nested_pre(data)
            return HookResult()
        c.hooks.register('tool:pre', nested_pre, priority=-200)
    else:
        from amplifier_web.app_guidance import install_app_access
        from amplifier_web.coordination_delivery import WORK_EXECUTION
        # Real installed WorkCheckedTool + fitted request + native dispatcher.
        # The controlled policy models Full or a refused child; canonical host
        # Full/Ask/Stop decisions are separately exercised by work_native_effects.
        state.work_checks = []
        async def check(name, arguments):
            proof = WORK_EXECUTION.get()['proof']
            state.work_checks.append((name, proof['complete'], proof['effectAuthorized']))
            return {'allowed': work_mode == 'full' or name == 'tool_exec'}
        c.register_capability('web.work_effect_check', check)
        c.register_capability('web.work_full_access', lambda: True)
        c.register_capability('web.work_origin_scope', lambda: {
            'rootSessionId': session.session_id, 'runtimeSessionId': session.session_id,
            'generationId': 'fixture-generation', 'inputIds': ['fixture-input'],
            'inputCoverage': 'complete', 'stopRevision': 0})
        async def bridge(operation, arguments):
            return {'context.manifest': {'surfaces': [], 'inputIds': ['fixture-input']},
                    'memory.context': {'items': []}, 'coordination.current': {}}[operation]
        await install_app_access(c, bridge)
        delivery = c.get_capability('web.surface_delivery').begin_work.__self__
        state.delivery = delivery

    class Probe:
        name = 'probe'
        description = 'Synthetic local receipt'
        input_schema = {'type': 'object', 'properties': {'n': {'type': 'integer'}}}
        async def execute(self, args):
            state.calls.append(args['n'])
            # A nested tool may deliberately change context. It must survive
            # normal completion, a later error, and cancellation of its parent.
            await context.add_message({'role': 'system', 'content': f"nested-{args['n']}"})
            if fault == 'compaction':
                await c.hooks.emit('context:compaction_finished', {'session_id': c.session_id})
                assert f"nested-{args['n']}" in path.read_text()
            if fault == 'error' and args['n'] == 1:
                raise ValueError('synthetic failure after saved external effect')
            if fault == 'cancel' and args['n'] == 1:
                state.started.set()
                await asyncio.Event().wait()
            return ToolResult(success=True, output={'n': args['n']})
    await c.mount('tools', Probe(), name='probe')
    await c.mount('tools', ProgrammaticTool(c), name='tool_exec')

    class Provider:
        name = 'fixture'
        def get_info(self):
            return ProviderInfo(id='fixture', display_name='Fixture', capabilities=['tools'],
                                defaults={'model': 'fixture'})
        def parse_tool_calls(self, response):
            return response.tool_calls or []
        async def complete(self, request, **kwargs):
            state.transports.append(copy.deepcopy(request))
            if len(state.transports) == 1:
                if direct:
                    return ChatResponse(content=[], tool_calls=[ToolCall(id=f'direct-{n}', name='probe',
                        arguments={'n': n}) for n in range(count)],
                        usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2))
                code = (f"await Promise.all(Array.from({{length:{count}}}, async (_,n)=>text(await tools.probe({{n}}))));"
                        if parallel else f"for(let n=0;n<{count};n++) text(await tools.probe({{n}}));")
                if fault == 'parent_error':
                    code = 'text(await tools.probe({n:0})); throw new Error("synthetic parent failure");'
                return ChatResponse(content=[], tool_calls=[ToolCall(id='parent', name='tool_exec',
                    arguments={'code': code})], usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2))
            # Existing parent tool:post persists nested context edits, then the
            # loop appends ordered results. Terminal saves persist those results.
            # Do not add a new pre-provider durable guarantee or duplicate save.
            rows = await context.get_messages()
            expected = {f'direct-{n}' for n in range(count)} if direct else {'parent'}
            results = [m for m in rows if m.get('tool_call_id') in expected]
            assert len(results) == len(expected)
            assert all(any(m.get('content') == f'nested-{n}' for m in json.loads(path.read_text()))
                       for n in state.calls)
            if not direct:
                state.parent_result = results[0]
            state.provider_started.set()
            return ChatResponse(content=[TextBlock(text='Done')], tool_calls=[],
                usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2))
    provider = Provider()
    if work_mode is not None:
        provider = c.get_capability('web.provider_transform')(provider)
    await c.mount('providers', provider, name='fixture')
    return session, c, context, runtime, state, path


@pytest.mark.parametrize('fault', [None, 'error', 'compaction'])
async def test_real_nested_results_keep_enclosing_and_terminal_checkpoints(tmp_path, fault):
    session, c, context, runtime, state, path = await environment(tmp_path, fault=fault)
    try:
        await asyncio.wait_for(session.execute('Run the local probes once, then finish.'), 10)
        assert state.calls == [0, 1, 2, 3]
        assert len(state.transports) == 2
        assert state.checkpoints.count(False) == 4
        assert state.checkpoints.count(True) == 4 + (4 if fault == 'compaction' else 0)
        saved = json.loads(path.read_text())
        assert all(any(m.get('content') == f'nested-{n}' for m in saved) for n in state.calls)
        result = json.loads(state.parent_result['content'])
        assert [call['status'] for call in result['output']['calls']] == (
            ['completed', 'failed', 'completed', 'completed'] if fault == 'error' else ['completed'] * 4)
        assert sum(m.get('tool_call_id') == 'parent' for m in saved) == 1
        assert WORK_NESTED.get() is None
    finally:
        await session.cleanup()


@pytest.mark.parametrize('work_mode', [None, 'full'])
async def test_real_live_cancellation_flushes_context_without_replaying_tools(tmp_path, work_mode):
    session, c, context, runtime, state, path = await environment(
        tmp_path, fault='cancel', work_mode=work_mode)
    execution = asyncio.create_task(session.execute('Run once.'))
    try:
        await asyncio.wait_for(state.started.wait(), 10)
        await runtime.submit(Input('stop', target='cancel'))
        await asyncio.wait_for(execution, 10)
        saved = json.loads(path.read_text())
        assert state.calls == [0, 1]
        assert len(state.transports) == 1
        assert any(m.get('content') == 'nested-0' for m in saved)
        assert any(m.get('content') == 'nested-1' for m in saved)
        assert state.checkpoints.count(False) == 1
        assert state.checkpoints[-1] is True
        assert runtime.closed is True
        assert WORK_NESTED.get() is None
    finally:
        execution.cancel()
        await asyncio.gather(execution, return_exceptions=True)
        await session.cleanup()


async def test_parallel_real_dispatch_children_keep_enclosing_checkpoint(tmp_path):
    session, c, context, runtime, state, path = await environment(tmp_path, count=7, parallel=True)
    try:
        await asyncio.wait_for(session.execute('Run in parallel once.'), 10)
        assert sorted(state.calls) == list(range(7))
        assert state.checkpoints.count(False) == 7
        assert state.checkpoints.count(True) == 4
        assert len(state.transports) == 2
        result = json.loads(state.parent_result['content'])
        assert len(result['output']['calls']) == 7
        assert all(call['status'] == 'completed' for call in result['output']['calls'])
        saved = json.loads(path.read_text())
        assert all(any(m.get('content') == f'nested-{n}' for m in saved) for n in state.calls)
    finally:
        await session.cleanup()


@pytest.mark.parametrize('count', [1, 4])
async def test_ordinary_direct_tools_do_not_add_checkpoint_callbacks(tmp_path, count):
    session, c, context, runtime, state, path = await environment(tmp_path, count=count, direct=True)
    try:
        assert c.get_capability('session.durable_checkpoint') is None
        await asyncio.wait_for(session.execute('Run the direct probes once.'), 10)
        assert sorted(state.calls) == list(range(count))
        assert len(state.transports) == 2
        assert state.checkpoints.count(False) == 0
        # Baseline: each tool:post plus orchestrator completion, live turn
        # completion and final session save. No additional ordered-append save.
        assert state.checkpoints.count(True) == count + 3
        saved = json.loads(path.read_text())
        assert [m['tool_call_id'] for m in saved if m.get('role') == 'tool'] == [
            f'direct-{n}' for n in range(count)]
    finally:
        await session.cleanup()


async def test_parent_program_error_keeps_completed_child_context_without_replay(tmp_path):
    session, c, context, runtime, state, path = await environment(tmp_path, fault='parent_error')
    try:
        await asyncio.wait_for(session.execute('Run once.'), 10)
        assert state.calls == [0]
        assert len(state.transports) == 2
        assert state.checkpoints.count(False) == 1
        assert state.checkpoints.count(True) == 4
        saved = json.loads(path.read_text())
        assert any(m.get('content') == 'nested-0' for m in saved)
        result = json.loads(state.parent_result['content'])
        assert result['success'] is False
        assert len(result['output']['calls']) == 1
        assert result['output']['calls'][0]['status'] == 'completed'
        assert sum(m.get('tool_call_id') == 'parent' for m in saved) == 1
    finally:
        await session.cleanup()


@pytest.mark.parametrize('work_mode', ['full', 'deny_child'])
async def test_production_work_wrapper_keeps_native_nested_saves_without_granting_assurance(tmp_path, work_mode):
    session, c, context, runtime, state, path = await environment(tmp_path, work_mode=work_mode)
    try:
        await asyncio.wait_for(session.execute('Run the local probes once.'), 10)
        assert state.calls == ([0, 1, 2, 3] if work_mode == 'full' else [])
        assert [name for name, _, _ in state.work_checks] == ['tool_exec', *(['probe'] * 4)]
        assert all(not complete and not authorized for _, complete, authorized in state.work_checks)
        assert any(row.get('assuranceUnavailable') for row in state.delivery.requests.values())
        assert state.checkpoints.count(False) == 4
        assert state.checkpoints.count(True) == 4
        assert WORK_NESTED.get() is None
        saved = json.loads(path.read_text())
        assert all(any(m.get('content') == f'nested-{n}' for m in saved) for n in state.calls)
        assert sum(m.get('tool_call_id') == 'parent' for m in saved) == 1
    finally:
        await session.cleanup()
