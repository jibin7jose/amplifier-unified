"""Controlled native/JS wire fixtures through real worker and host effect owners.

No external provider, private history or filesystem effect. Native SDK payloads,
read receipts, fitting, admission, tool dispatch, Work checks and pin mutations
are production paths; the socket and ordinary provider response are synthetic.
"""
import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from amplifier_core import ToolResult
from amplifier_core.message_models import ToolCall, ToolSpec
from amplifier_web.runtime_worker import Worker
from test_collaborative_workspaces import app
from test_coordination_delivery import installed_work_adapter
from test_work_invocation import retained_bytes
from test_access_mode import save


async def joined(app):
    adapter, child, required = await installed_work_adapter(app)
    c = adapter.coordinator
    c.cancellation.is_cancelled = False
    worker = Worker()
    worker.home, worker.workspace = app.data_dir, child['workspace']
    worker.session = SimpleNamespace(coordinator=c)
    worker.runtime = SimpleNamespace(generation={'id': required['generationId'],
        'input_ids': list(required['inputIds'])}, closed=False, stop_requested=False)
    worker.activation_gate = SimpleNamespace(current_valid=True)
    async def bridge(operation, arguments):
        return await app.app_bridge(operation, arguments, child['id'])
    worker.bridge = bridge
    adapter.dispatch_bridge = worker.app_access_bridge(c)
    adapter.delivery.begin_work.__self__.origin_scope = c.get_capability('web.work_origin_scope')
    adapter.delivery.begin_work.__self__.full_access = c.get_capability('web.work_full_access')
    return adapter, child, required, worker


@pytest.mark.parametrize('fault', ['none', 'uncovered', 'opaque', 'host_stop'])
@pytest.mark.parametrize('mode', ['ask', 'full'])
async def test_native_early_tool_reaches_real_host_precondition(app, tmp_path, monkeypatch, fault, mode):
    from amplifier_module_provider_openai import OpenAIProvider
    from amplifier_web.native_provider import install_native
    from amplifier_module_loop_live.scope import LIVE_OWNER
    adapter, child, required, worker = await joined(app)
    if mode == 'full':
        await save(app, child)
    if fault != 'uncovered':
        await adapter.fill(required)
    c = adapter.coordinator
    providers = {'configured': OpenAIProvider(api_key='fixture', config={'default_model': 'gpt-6-astra'})}
    old_get, old_mount = c.get, c.mount
    c.get = lambda kind: providers if kind == 'providers' else SimpleNamespace(get_messages=AsyncMock(return_value=[])) if kind == 'context' else old_get(kind)
    async def mount(kind, module, name=None):
        if kind == 'providers': providers[name] = module
        else: return await old_mount(kind, module, name=name)
    c.mount = mount
    c.loop = adapter.loop
    c.loop.config['native_provider'] = True
    c.loop._select_provider = lambda values: values['configured']
    adapter.capabilities['live.runtime'] = worker.runtime
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    await install_native(c.loop, c, providers)
    host = adapter.capabilities['web.native_provider']; native = host.provider
    effects, results, sent, measured = [], [], [], []
    class Effect:
        name = 'delegate'
        async def execute(self, value):
            effects.append(value)
            return ToolResult(success=True, output='Synthetic effect, no delegated work.')
    await c.mount('tools', Effect(), name='delegate')
    async def start(call):
        if fault == 'host_stop':
            await app.dispatch('conversation.stop', {'sessionId': child['id']}, command_id='native-host-stop')
        results.append(await adapter.loop._execute_tool_only(call, adapter.tools, adapter.hooks, None, c))
    item = {'type': 'function_call', 'id': 'item-one', 'call_id': 'native-tool',
            'name': 'delegate', 'arguments': '{"instruction":"fixture"}', 'async': True, 'status': 'completed'}
    events = [
        {'type': 'response.created', 'response': {'id': 'response-one'}},
        {'type': 'response.output_item.done', 'item': item, 'output_index': 0, 'sequence_number': 2},
        {'type': 'response.completed', 'response': {'id': 'response-one', 'object': 'response',
            'status': 'completed', 'model': 'gpt-6-astra', 'output': [item],
            'usage': {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}}}]
    async def send(wire): sent.append(wire)
    native.socket = SimpleNamespace(send=send, recv=AsyncMock(side_effect=[json.dumps(e) for e in events]), close=AsyncMock())
    native._connect = AsyncMock()
    native._guard_assembled_params_with_provider_count = AsyncMock(return_value=None)
    native._provider_count_available = lambda: False
    client = SimpleNamespace(base_url='https://api.openai.com/v1', responses=SimpleNamespace())
    client.with_options = lambda **kw: client
    native._client = client
    native_owner = SimpleNamespace(coordinator=c, config={'background_delegate': True},
        runtime=SimpleNamespace(emit=AsyncMock()), start_native_job=start, native_job=lambda identity: None,
        context=SimpleNamespace(get_messages=AsyncMock(return_value=[m.model_dump() for m in adapter.messages])))
    if fault == 'opaque':
        def checkpoint(params, full):
            result = copy.deepcopy(params); result['input'] = [{'type': 'compaction', 'encrypted_content': 'opaque'}]
            return result
        native._apply_checkpoint = checkpoint
    class Driver:
        get_info = native.get_info
        async def request_budget(self, value, **kw): measured.append(value.model_dump_json()); return {'tokens': 100}
        async def complete(self, value, **kw):
            assert value.model_dump_json() == measured[0]
            token = LIVE_OWNER.set(native_owner)
            try: return await native.complete(value, model='gpt-6-astra')
            finally: LIVE_OWNER.reset(token)
    wrapped = adapter.capabilities['web.provider_transform'](Driver())
    request = adapter.request(); request = request.model_copy(update={'tools': [*request.tools,
        ToolSpec(name='delegate', parameters={'type': 'object', 'properties': {'instruction': {'type': 'string'}}})]})
    await wrapped.request_budget(request)
    await wrapped.complete(request)
    assert len(sent) == len(results) == 1
    allowed = fault == 'none' or mode == 'full' and fault in {'uncovered', 'opaque'}
    assert effects == ([{'instruction': 'fixture'}] if allowed else [])
    assert host.state['sends'][-1]['outcome'] != 'not_sent'


@pytest.mark.parametrize('fault', ['none', 'uncovered', 'same_batch_read', 'host_stop', 'reused_parent'])
@pytest.mark.parametrize('mode', ['ask', 'full'])
async def test_programmatic_inner_pin_uses_real_service_not_synthetic_policy(app, fault, mode):
    from amplifier_module_tool_exec import ProgrammaticTool
    adapter, child, required, worker = await joined(app)
    if mode == 'full':
        await save(app, child)
    pin_dispatches, pin_results = [], []
    actual_host = worker.bridge
    async def observed_host(operation, arguments):
        if operation == 'dispatch' and arguments.get('action') == 'session.pin':
            pin_dispatches.append(copy.deepcopy(arguments))
            try:
                result = await actual_host(operation, arguments)
            except Exception as exc:
                pin_results.append({'denied': True, 'code': getattr(exc, 'code', None), 'reason': str(exc)})
                raise
            pin_results.append({'denied': False, 'result': result})
            return result
        return await actual_host(operation, arguments)
    worker.bridge = observed_host
    if fault not in {'uncovered', 'same_batch_read'}:
        await adapter.fill(required)
    c = adapter.coordinator
    from amplifier_web.host.session import install_checkpoint_hooks
    checkpoints = []
    async def checkpoint(*, timing=None, transcript_required=True):
        checkpoints.append(transcript_required)
    install_checkpoint_hooks(c, SimpleNamespace(session_id=c.session_id, generation=None), checkpoint)
    adapter.loop.config['programmatic_dispatch'] = True
    c.register_capability('tools.dispatch', adapter.loop.approved_dispatch)
    adapter.delivery.install_dispatch(c)
    await c.mount('tools', ProgrammaticTool(c, {'timeout_seconds': 10}), name='tool_exec')
    prefix = ''
    if fault == 'same_batch_read':
        prefix = 'await tools.app_control(' + json.dumps({'operation':'dispatch','args':required['requirementsRef']}) + '); '
    body = {'operation':'dispatch','args':{'action':'session.pin','args':{'id':child['id'],'pinned':True},'id':'nested-real-pin'}}
    code = prefix + 'text(await tools.app_control(' + json.dumps(body) + '));'
    class Provider:
        async def complete(self, value, **kw):
            return SimpleNamespace(model_dump=lambda:{'content':[{'type':'tool_call','id':'nested-parent','name':'tool_exec','input':{'code':code}}]})
    await adapter.capabilities['web.provider_transform'](Provider()).complete(adapter.request())
    if fault == 'host_stop':
        await app.dispatch('conversation.stop', {'sessionId':child['id']}, command_id='nested-host-stop')
    async def execute():
        with adapter.loop.approved_dispatch.run_scope(c):
            return await adapter.loop._execute_tool_only(ToolCall(id='nested-parent', name='tool_exec', arguments={'code':code}), adapter.tools, adapter.hooks, None, c)
    before = retained_bytes(app)
    execution_result = await execute()
    if mode == 'full' and fault != 'host_stop':
        assert checkpoints.count(False) == (2 if fault == 'same_batch_read' else 1), execution_result
        assert checkpoints.count(True) == 1
    if fault in {'none','reused_parent'} or mode == 'full' and fault in {'uncovered', 'same_batch_read'}:
        assert 'nested-real-pin' in retained_bytes(app) and child['id'] in app._state['pinnedSessionIds'], execution_result
        assert len(pin_dispatches) == 1
        if fault == 'reused_parent':
            after=retained_bytes(app);await execute();assert retained_bytes(app)==after
            # Known native consumption refuses the repeated container before
            # inner dispatch, independently of Ask or Full coverage policy.
            assert len(pin_dispatches) == 1
    else:
        assert 'nested-real-pin' not in retained_bytes(app) and retained_bytes(app)==before


async def test_full_native_dispatch_is_consumed_but_new_call_can_read_exact_command_receipt(app):
    adapter, child, _, worker = await joined(app)
    await save(app, child)
    arguments = {'operation': 'dispatch', 'args': {'action': 'session.rename',
        'args': {'id': child['id'], 'title': 'Exact retained effect'}, 'id': 'native-exact-command'}}
    dispatched = []
    host = worker.bridge
    async def observed(operation, args):
        result = await host(operation, args)
        if operation == 'dispatch' and args.get('id') == 'native-exact-command':
            dispatched.append(result)
        return result
    worker.bridge = observed
    identity = 'first-native-call'
    class Provider:
        async def complete(self, value, **kw):
            return SimpleNamespace(model_dump=lambda: {'content': [{'type': 'tool_call',
                'id': identity, 'name': 'app_control', 'input': arguments}]})
    provider = adapter.capabilities['web.provider_transform'](Provider())
    async def execute():
        return await adapter.loop._execute_tool_only(ToolCall(id=identity, name='app_control', arguments=arguments),
            adapter.tools, adapter.hooks, None, adapter.coordinator)
    await provider.complete(adapter.request())
    await execute()
    assert len(dispatched) == 1 and dispatched[0]['accepted']
    before = retained_bytes(app)
    await execute()
    assert len(dispatched) == 1 and retained_bytes(app) == before
    identity = 'fresh-native-call-for-exact-retry'
    await provider.complete(adapter.request())
    await execute()
    assert len(dispatched) == 2 and dispatched[1]['duplicate']
    assert retained_bytes(app) == before and child['title'] == 'Exact retained effect'


async def test_full_native_unknown_tool_outcome_does_not_repeat_consumed_call(app):
    adapter, child, _, _ = await joined(app)
    await save(app, child)
    effects = []
    class UnknownEffect:
        name = 'write_file'
        async def execute(self, value):
            effects.append(value)
            raise RuntimeError('Effect entered; acknowledgement lost.')
    await adapter.coordinator.mount('tools', UnknownEffect(), name='write_file')
    arguments = {'path': 'synthetic-owned-effect'}
    class Provider:
        async def complete(self, value, **kw):
            return SimpleNamespace(model_dump=lambda: {'content': [{'type': 'tool_call',
                'id': 'unknown-native-call', 'name': 'write_file', 'input': arguments}]})
    await adapter.capabilities['web.provider_transform'](Provider()).complete(adapter.request())
    for _ in range(2):
        await adapter.loop._execute_tool_only(ToolCall(id='unknown-native-call', name='write_file', arguments=arguments),
            adapter.tools, adapter.hooks, None, adapter.coordinator)
    assert effects == [arguments]
