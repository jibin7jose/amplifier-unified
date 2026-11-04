"""Worker scope/bridge observations use actual runtime state, not model fields."""
import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from amplifier_web.runtime_worker import Worker
from test_collaborative_workspaces import app
from test_coordination_delivery import native_outbound_probe, programmatic_origin_probe
from test_coordination_delivery import installed_work_adapter


@pytest.mark.parametrize('fault', ['none', 'opaque_only', 'changed_plaintext', 'oversized', 'new_input', 'wrong_response',
                                 'overflow_scope', 'missing_scope', 'no_event_response', 'retention', 'live_retention',
                                 'full_provider', 'ambiguous_response', 'activation_changed'])
async def test_installed_native_callbacks_bind_early_tool_to_exact_outbound(app, tmp_path, monkeypatch, fault):
    await native_outbound_probe(app, tmp_path, monkeypatch, fault)


@pytest.mark.parametrize('fault', ['none', 'uncovered', 'same_batch_read', 'changed_parent', 'reused_parent', 'stop'])
async def test_real_programmatic_dispatch_inherits_only_generating_request(app, fault):
    await programmatic_origin_probe(app, fault)


@pytest.mark.parametrize('fault', ['none', 'reused_parent', 'app_control', 'app_control_uncovered'])
async def test_real_nested_dispatch_and_final_guard_share_one_parent_execution(app, fault):
    await programmatic_origin_probe(app, fault, guarded=True)


def worker_scope(count=12):
    caps = {}
    c = SimpleNamespace(session_id='root', get_capability=caps.get,
                        register_capability=caps.__setitem__,
                        cancellation=SimpleNamespace(is_cancelled=False))
    worker = Worker()
    worker.session = SimpleNamespace(coordinator=c)
    worker.runtime = SimpleNamespace(generation={'id': 'generation', 'input_ids': [f'input-{i}' for i in range(count)]},
                                     closed=False, stop_requested=False)
    worker.context_inputs = worker.runtime.generation['input_ids'][-8:]
    worker.context_bindings = {}
    worker.activation_gate = SimpleNamespace(current_valid=True)
    worker.bridge = AsyncMock(return_value={'accepted': True, 'result': 'fixture'})
    bridge = worker.app_access_bridge(c)
    return worker, c, caps, bridge


@pytest.mark.parametrize("count", [17, 257])
def test_origin_uses_all_runtime_inputs_without_count_or_display_truncation(count):
    worker, _, caps, _ = worker_scope()
    scope = caps['web.work_origin_scope']()
    assert len(scope['inputIds']) == 12 and scope['inputIds'][0] == 'input-0'
    assert scope['inputCoverage'] == 'complete'
    worker.runtime.generation['input_ids'] = [f'input-{i}' for i in range(count)]
    scope = caps['web.work_origin_scope']()
    assert scope['inputCoverage'] == 'complete'
    assert scope['inputIds'] == worker.runtime.generation['input_ids']
    worker.runtime.stop_requested = True
    assert caps['web.work_origin_scope']() is None


def test_origin_input_set_is_order_independent_without_dropping_any_member():
    import json
    from amplifier_web.coordination_delivery import CoordinationDelivery
    worker, _, caps, _ = worker_scope()
    delivery = CoordinationDelivery(None, None, origin_scope=caps['web.work_origin_scope'])
    before = delivery._scope()
    worker.runtime.generation['input_ids'].reverse()
    assert delivery._scope() == before
    assert set(json.loads(before)['inputIds']) == set(worker.runtime.generation['input_ids'])


async def test_bridge_strips_forged_origin_and_stamps_exact_execution():
    worker, c, caps, bridge = worker_scope()
    seen = []
    async def origin(data):
        seen.append(copy.deepcopy(data))
        return {'complete': False, 'effectAuthorized': False, 'reason': 'controlled'}
    caps['web.work_effect_origin'] = origin
    original = {'action': 'workspace.work.read', 'args': {'id': 'owned'}, 'id': 'command'}
    await bridge('dispatch', {**original, '_workOrigin': {'complete': True}})
    assert '_workOrigin' not in worker.bridge.call_args.args[1] and not seen
    assert len(worker.bridge.call_args.args[1]['_inputBindings']) == 12
    assert worker.bridge.call_args.args[1]['_inputBindings'][0]['inputId'] == 'input-0'
    c._tool_dispatch_contexts = {asyncio.current_task(): {'tool_call_id': 'actual-call'}}
    await bridge('dispatch', {**original, '_workOrigin': {'complete': True}})
    assert seen == [{'tool_call_id': 'actual-call', 'tool_name': 'app_control',
                     'tool_input': {'operation': 'dispatch', 'args': original}}]
    assert worker.bridge.call_args.args[1]['_workOrigin']['complete'] is False
    worker.runtime.generation['input_ids'] = [f'input-{i}' for i in range(17)]
    await bridge('dispatch', original)
    assert [v['inputId'] for v in worker.bridge.call_args.args[1]['_inputBindings']] == worker.runtime.generation['input_ids']


def test_child_assignment_retains_full_inputs_and_expires_with_parent_generation():
    worker, _, _, _ = worker_scope()
    caps = {}
    child = SimpleNamespace(session_id='child', get_capability=caps.get,
                            register_capability=caps.__setitem__,
                            cancellation=SimpleNamespace(is_cancelled=False))
    worker.app_access_bridge(child)
    first = caps['web.work_origin_scope']()
    assert len(first['inputIds']) == 12 and first['runtimeSessionId'] == 'child'
    worker.runtime.generation['input_ids'].append('later-root-input')
    assert caps['web.work_origin_scope']() == first
    worker.runtime.generation = {'id': 'later-generation', 'input_ids': ['later']}
    assert caps['web.work_origin_scope']() is None


@pytest.mark.parametrize('fault', ['none', 'work_deny', 'stop_after_await', 'new_input_after_await',
                                 'changed_arguments', 'late_mount', 'timeout', 'check_replaced', 'body_failure'])
async def test_final_tool_wrapper_runs_after_real_engine_approval_and_rechecks(app, fault, monkeypatch):
    monkeypatch.setattr('amplifier_web.performance_diagnostics.TIMING_ENABLED', True)
    from amplifier_core import ToolResult, HookResult
    from amplifier_core.message_models import ToolCall
    adapter, child, required = await installed_work_adapter(app)
    await adapter.fill(required)
    c = adapter.coordinator
    c.cancellation.is_cancelled = False
    worker = Worker()
    worker.session = SimpleNamespace(coordinator=c)
    worker.runtime = SimpleNamespace(generation={'id': adapter.scope['generationId'],
        'input_ids': list(adapter.scope['inputIds'])}, closed=False, stop_requested=False)
    worker.activation_gate = SimpleNamespace(current_valid=True)
    worker.app_access_bridge(c)
    timings = []
    c.register_capability('web.performance.emit', timings.append)
    adapter.delivery.begin_work.__self__.origin_scope = c.get_capability('web.work_origin_scope')
    stages, witnesses, effects, actual_arguments = [], [], [], []
    actual_check = c.get_capability('web.work_effect_check')
    async def checked(name, value):
        actual_arguments.append(value)
        return await actual_check(name, value)
    c.register_capability('web.work_effect_check', checked)
    arguments = {'path': 'fixture'}
    async def service(operation, args):
        assert operation == 'work.effect.check'
        stages.append('service')
        witnesses.append(copy.deepcopy(args))
        if fault == 'timeout':
            raise TimeoutError('canonical transport deadline')
        await asyncio.sleep(0)
        if fault == 'stop_after_await':
            worker.stop_revision += 1
        if fault == 'new_input_after_await':
            worker.runtime.generation['input_ids'].append('late-input')
        if fault == 'changed_arguments':
            actual_arguments[0]['path'] = 'changed'
        return {'applicable': True, 'allowed': fault != 'work_deny',
                'reason': None, 'requiredVersion': required['version'], 'effectAuthorized': False}
    worker.bridge = service
    class Probe:
        name = 'probe'
        async def execute(self, value):
            stages.append('execute')
            effects.append(value)
            return ToolResult(success=fault != 'body_failure', output='fixture')
    if fault != 'late_mount':
        adapter.tools['probe'] = Probe()
    async def policy(event, data):
        stages.append('policy')
        await asyncio.sleep(0)
        return HookResult()
    adapter.hooks.register('tool:pre', policy, name='test-awaiting-policy')
    class Provider:
        async def complete(self, value, **kwargs):
            return SimpleNamespace(model_dump=lambda: {'content': [{
                'type': 'tool_call', 'id': 'final-call', 'name': 'probe', 'input': arguments}]})
    wrapped = adapter.capabilities['web.provider_transform'](Provider())
    await wrapped.complete(adapter.request())
    if fault == 'check_replaced':
        original = adapter.tools['probe']
        async def replacement(name, value):
            return await checked(name, value)
        c.register_capability('web.work_effect_check', replacement)
        c.get_capability('web.work_guard_tools')()
        assert adapter.tools['probe'] is original
    if fault == 'late_mount':
        await c.mount('tools', Probe(), name='probe')
    await adapter.loop._execute_tool_only(ToolCall(id='final-call', name='probe', arguments=arguments),
                                         adapter.tools, adapter.hooks, None, c)
    assert stages[:2] == ['policy', 'service']
    assert bool(effects) is (fault in {'none', 'late_mount', 'check_replaced', 'body_failure'})
    witness = witnesses[0]['_workOrigin']
    assert witness['complete'] is True and witness['effectAuthorized'] is False
    assert witness['toolCallId'] == witnesses[0]['toolCallId'] == 'final-call'
    assert witness['origin']['admittedCallId'] in adapter.admissions
    assert c._tool_dispatch_contexts == {}
    # The same diagnostic sink also observes the preceding provider preparation.
    # This assertion owns the one dispatched tool's timing, not every phase.
    tool_timings = [row for row in timings if row.get('kind') == 'tool']
    assert len(tool_timings) == 1
    timing = tool_timings[0]
    assert timing['toolCallId'] == 'final-call'
    assert timing['generationId'] == adapter.scope['generationId']
    marks = timing['marks']
    assert marks['start'] <= marks['origin.start'] <= marks['origin.end'] <= marks['check.start'] <= marks['check.end'] <= marks['end']
    assert ('body.start' in marks) == bool(effects)
    assert timing['outcome'] == ('failed' if fault == 'body_failure' else 'completed' if effects else 'denied')
    assert 'path' not in str(timing) and 'fixture' not in str(timing)


async def test_real_rust_coordinator_mount_wraps_late_tools():
    from amplifier_core.coordinator import ModuleCoordinator
    from amplifier_core import ToolResult
    from amplifier_web.app_guidance import install_app_access
    session = SimpleNamespace(session_id='rust-mount', parent_id=None, config={}, session_state={})
    c = ModuleCoordinator(session)
    checks = []
    async def check(name, arguments):
        checks.append((name, arguments))
        return {'allowed': True, 'applicable': False, 'effectAuthorized': False}
    c.register_capability('web.work_effect_check', check)
    async def bridge(operation, args):
        return {}
    await install_app_access(c, bridge)
    class Probe:
        name = 'late'
        async def execute(self, args):
            return ToolResult(success=True, output='ok')
    original = Probe()
    await c.mount('tools', original, name='late')
    wrapped = c.get('tools')['late']
    assert wrapped is not original
    result = await wrapped.execute({'ordinary': True})
    assert result.success and checks == [('late', {'ordinary': True})]


@pytest.mark.parametrize("count", [2, 17, 257])
async def test_effect_scope_matches_required_input_membership_not_order(count):
    from amplifier_web.coordination_delivery import CoordinationDelivery
    from amplifier_web.execution_events import ExecutionEvents
    from amplifier_web.surface_delivery import SurfaceProvider
    from test_coordination_delivery import Previous, request, available_requirements, empty_discovery
    required = available_requirements()
    ids = [f'input-{i:04}-' + 'x' * 100 for i in reversed(range(count))]
    required.update(inputIds=ids, associationRequestIds=[f'ref-{i}' for i in range(count)],
                    inputCount=count, workCount=0, requirementCount=0,
                    coverage={'complete': True, 'supportedInputIds': ids, 'unsupportedInputs': []})
    scope = {'rootSessionId': required['rootSessionId'], 'runtimeSessionId': required['rootSessionId'],
             'generationId': required['generationId'], 'inputIds': ids, 'inputCoverage': 'complete'}
    async def bridge(operation, args):
        if operation == 'coordination.current':
            return {'work': {'available': True, 'discovery': empty_discovery(), 'required': required}}
        return {'current': True, 'required': required, 'effectAuthorized': False}
    delivery = CoordinationDelivery(Previous(), bridge, origin_scope=lambda: scope)
    # Exact empty manifest is still mandatory even when there are no bodies.
    # This unit fixture passes it through the real capture/binding predicate.
    from amplifier_core.message_models import Message
    tool_input = {'operation': 'dispatch', 'args': required['requirementsRef']}
    receipt = {'accepted': True, 'result': {**required, 'items': [], 'truncated': False, 'nextAfter': None}}
    delivery.remember_work(receipt, tool_input, execution_id='manifest')
    delivery.bind_work({'tool_name': 'app_control', 'tool_call_id': 'manifest',
                        'tool_input': tool_input, 'result': receipt})
    import json
    messages = [Message(role='assistant', content='', tool_calls=[{
        'id': 'manifest', 'tool': 'app_control', 'arguments': tool_input}]),
        Message(role='tool', name='app_control', tool_call_id='manifest', content=json.dumps(receipt))]
    telemetry = ExecutionEvents(scope['rootSessionId'], lambda event: None)
    telemetry.admission_guard = AsyncMock()
    class Provider:
        def get_info(self):
            return SimpleNamespace(id='fixture', defaults={})
        async def complete(self, value, **kwargs):
            return {'content': [{'type': 'tool_call', 'id': 'ordered', 'name': 'probe', 'input': {}}]}
    wrapped = SurfaceProvider(telemetry.instrument_provider(scope['rootSessionId'], Provider()), delivery)
    await wrapped.complete(request(*messages))
    proof = await delivery.recheck_effect_coverage({'tool_call_id': 'ordered', 'tool_name': 'probe', 'tool_input': {}})
    assert proof['complete'] is True, proof
    assert set(proof['origin']['inputIds']) == set(ids)
    assert delivery.reads[0]['bytes'] == len(json.dumps(receipt, ensure_ascii=False).encode())
