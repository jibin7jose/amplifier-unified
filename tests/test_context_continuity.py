"""Host adapter against the actual context-managed module and maintained dev dependencies."""
import copy
from contextlib import asynccontextmanager
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import openai
import pytest
import amplifier_module_context_managed
import amplifier_module_loop_streaming
import amplifier_module_provider_openai as provider_module
from amplifier_foundation.bundle import Bundle, BundleModuleResolver, PreparedBundle
from amplifier_module_context_managed import boundary
from amplifier_web.context_continuity import ContextContinuity, digest
from amplifier_web.runtime_controls import RuntimeControls
from test_task_continuity import Coordinator


async def mount(directory, messages, *, context_config=None):
    coordinator = Coordinator()
    coordinator.hooks = None
    async def mount_context(name, context): coordinator.context = context
    coordinator.mount = mount_context
    coordinator.register_contributor = lambda *args: None
    config = {'durable_checkpoints': True, 'max_tokens': 6000, 'summarize_trigger': .2} if context_config is None else context_config
    if context_config is None:
        await boundary.mount_boundary(coordinator, config)
    else:
        from amplifier_module_context_managed import mount as mount_context_module
        await mount_context_module(coordinator, config)
    provider = SimpleNamespace(get_info=lambda: {'id': 'fixture', 'defaults': {'model': 'model-a'}}, complete=AsyncMock(return_value=SimpleNamespace(content=[SimpleNamespace(type='text', text='Original task; revised constraints; operation uncertain-op outcome unknown; artifact report.md.')])) )
    coordinator.loop.root_provider = provider
    coordinator.loop._select_provider = lambda providers: provider
    original_get = coordinator.get
    coordinator.get = lambda name: {'fixture': provider} if name == 'providers' else original_get(name)
    await coordinator.context.set_messages(messages)
    transcript = directory / 'transcript.json'
    adapter = None
    async def checkpoint():
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text(json.dumps(await coordinator.context.get_messages()))
        if adapter: adapter.save()
    adapter = ContextContinuity(coordinator, 'session', directory, checkpoint)
    return coordinator, provider, adapter, checkpoint, transcript


async def test_actual_context_multiple_compactions_restart_and_saved_task(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'home'))
    original = [{'role': 'user', 'content': 'Build the report without replacing originals.'},
                {'role': 'assistant', 'content': 'Research ' * 2400},
                {'role': 'tool', 'tool_call_id': 'evidence', 'content': 'FULL-EVIDENCE-' * 9000},
                {'role': 'user', 'content': 'Correction: use revised constraints'},
                {'role': 'assistant', 'content': 'Working'}, {'role': 'user', 'content': 'Continue'}]
    coordinator, provider, adapter, checkpoint, transcript = await mount(tmp_path / 'session', original)
    controls = RuntimeControls(SimpleNamespace(session_id='session', coordinator=coordinator), SimpleNamespace(generation=None, queued_inputs=0))
    await controls.perform('task.create', {'commandId': 'create', 'expectedRevision': 0, 'objective': 'Build the verified report', 'constraints': ['Keep originals'], 'operationIds': ['uncertain-op'], 'artifactRefs': ['report.md']})
    for i in range(3):
        await coordinator.context.add_message({'role': 'assistant', 'content': 'Evidence ' * 2400})
        await coordinator.context.add_message({'role': 'user', 'content': f'Latest correction {i}'})
        await coordinator.context.get_messages_for_request(provider=provider)
        await checkpoint()
    await controls.perform('task.update', {'commandId': 'correct', 'expectedRevision': 1, 'correction': 'Latest correction 2'})
    await controls.perform('task.block', {'commandId': 'blocked', 'expectedRevision': 2, 'reason': 'Unknown operation outcome needs reconciliation'})
    saved = json.loads(adapter.path.read_text())
    assert saved['evidenceRefs'][0]['sha256'] == digest(original[2])
    canonical = json.loads(transcript.read_text())
    assert canonical[:len(original)] == original
    appended = canonical + [{'role': 'user', 'content': 'New correction after checkpoint'}]
    newer, provider2, adapter2, checkpoint2, transcript2 = await mount(tmp_path / 'session', appended)
    controls2 = RuntimeControls(SimpleNamespace(session_id='session', coordinator=newer), SimpleNamespace(generation=None, queued_inputs=0))
    await controls2.restore()
    view = await newer.context.get_messages_for_request()
    assert adapter2.public()['status'] == 'restored'
    assert provider2.complete.await_count == 0
    assert 'New correction after checkpoint' in str(view) and 'Latest correction 2' in str(view)
    assert 'uncertain-op outcome unknown' in str(view)
    assert await newer.context.get_messages() == appended
    assert controls2.tasks.record()['status'] == 'blocked'
    assert controls2.tasks.record()['corrections'][-1]['text'] == 'Latest correction 2'
    assert controls2.tasks.record()['artifactRefs'] == ['report.md']
    assert not await controls2.tasks.continuation_allowed()
    assert newer.session_state['goal'] is None
    await controls.close(); await controls2.close()


async def test_corrupt_host_checkpoint_remains_visibly_rejected(tmp_path):
    coordinator, provider, adapter, _, transcript = await mount(tmp_path, [{'role': 'user', 'content': 'Original'}])
    adapter.path.write_text('{broken')
    await coordinator.context.get_messages_for_request()
    assert adapter.public()['status'] == 'rejected'
    assert adapter.public()['originalsAvailable']
    assert json.loads(transcript.read_text()) == [{'role': 'user', 'content': 'Original'}]


async def test_partial_progress_is_saved_on_actual_completion_before_worker_restart(tmp_path):
    original = [{'role': 'user', 'content': 'Keep all source evidence.'},
        {'role': 'assistant', 'content': 'Evidence ' * 1200},
        {'role': 'user', 'content': 'Correction'}, {'role': 'assistant', 'content': 'Working'},
        {'role': 'user', 'content': 'Continue'}]
    first, provider, adapter, _, _ = await mount(tmp_path, original)
    first.context.config.update(summary_max_source_chars=1200, summary_max_calls=32)
    from amplifier_core.llm_errors import LLMError
    note = SimpleNamespace(content=[SimpleNamespace(type='text', text='Verified partial evidence')])
    provider.complete = AsyncMock(side_effect=[note, LLMError('offline', retryable=True)])
    # Strict preparation preserves completed work but stops the foreground turn
    # when the next summary request fails; it never silently proceeds unfitted.
    from amplifier_module_context_managed.errors import CompactionError
    with pytest.raises(CompactionError, match='summary_failed'):
        await first.context.get_messages_for_request(provider=provider)
    saved = json.loads(adapter.path.read_text())
    assert saved['summary'] is None and saved['progress']['completed'] == 1
    second, newer_provider, newer_adapter, _, _ = await mount(tmp_path, original)
    second.context.config.update(summary_max_source_chars=1200, summary_max_calls=32)
    await second.context.get_messages_for_request(provider=newer_provider)
    assert second.context.summary is not None and second.context._summary_progress is None, (second.context.checkpoint_status, second.context.summary_failure, newer_provider.complete.await_count, newer_adapter.public())
    assert 'Verified partial evidence' in newer_provider.complete.call_args_list[0].args[0].messages[-1].content
    assert await second.context.get_messages() == original


async def test_restore_observer_failure_does_not_reject_a_valid_checkpoint(tmp_path):
    original = [{'role': 'user', 'content': 'Keep originals.'},
        {'role': 'assistant', 'content': 'Evidence ' * 2400},
        {'role': 'user', 'content': 'Correction'}, {'role': 'assistant', 'content': 'Working'},
        {'role': 'user', 'content': 'Continue'}]
    first, provider, adapter, _, _ = await mount(tmp_path, original)
    await first.context.get_messages_for_request(provider=provider)
    adapter.save()
    second, model, newer_adapter, _, _ = await mount(tmp_path, original)
    second.hooks = SimpleNamespace(emit=AsyncMock(side_effect=ValueError('observer offline')))
    await second.context.get_messages_for_request(provider=model)
    assert newer_adapter.public()['status'] == 'restored'
    model.complete.assert_not_awaited()
    assert await second.context.get_messages() == original


class SyntheticNativeProvider:
    """Deterministic transport, not a claim about vendor summary quality.

    Counts the actual assembled request, including the opaque carrier, tools and
    instructions. Compaction keeps short facts and results and replaces only
    synthetic filler. No network, tool execution or model generation occurs.
    """
    name = 'fixture'
    default_model = 'model-a'

    def __init__(self):
        self.compactions = []
        self.counted = []
        self.sent = []
        self.nonreducing = False
        self.input_limit = 20000

    def get_info(self):
        from amplifier_core import ProviderInfo
        return ProviderInfo(id=self.name, display_name='Fixture',
            defaults={'model': self.default_model})

    def supports_native_compaction(self):
        return True

    def validate_compacted_context(self, message, *, model=None):
        return (message.get('metadata', {}).get('fixture-native', {}).get('model')
                == (model or self.default_model))

    @staticmethod
    def expanded(request):
        rows = []
        for message in request.messages:
            state = (message.metadata or {}).get('fixture-native')
            rows.extend(copy.deepcopy(state['rows']) if state else
                        [message.model_dump(exclude_none=True)])
        return rows

    def request_budget(self, request, *, context_estimate=0, **kwargs):
        rows = self.expanded(request)
        count = len(json.dumps({'messages': rows, 'tools': [
            tool.model_dump() for tool in request.tools or []]})) // 4
        self.counted.append((copy.deepcopy(rows), count))
        return {'estimated_input_tokens': count, 'input_limit_tokens': self.input_limit,
            'context_token_budget': context_estimate if count <= self.input_limit else 0,
            'measurement': {'kind': 'provider_count', 'input_tokens': count,
                            'source': 'synthetic-serialized-request'}}

    async def compact_context(self, request):
        rows = self.expanded(request)
        self.compactions.append(copy.deepcopy(rows))
        if not self.nonreducing:
            rows = [{**row, 'content': 'Synthetic research retained by reference.'}
                    if isinstance(row.get('content'), str)
                    and row['content'].startswith('FILLER:') else row for row in rows]
        return {'kind': 'native', 'message': {'role': 'user',
            'content': 'Native reference state; original history remains available.',
            'metadata': {'source': 'context-managed', 'ephemeral': True, 'persisted': True,
                         'fixture-native': {'model': request.model, 'rows': rows}}}}

    async def complete(self, request, **kwargs):
        self.sent.append(request.model_copy(deep=True))
        return SimpleNamespace(content=[])


async def native_fixture(directory, original, *, provider=None, context_config=None):
    from amplifier_core import ChatRequest, Message, ToolSpec
    from amplifier_web.host.session import SelectedProvider
    from amplifier_web.surface_delivery import SurfaceProvider
    coordinator, _, adapter, checkpoint, transcript = await mount(directory, original, context_config=context_config)
    raw = provider or SyntheticNativeProvider()
    selected = SelectedProvider(raw, {'model': 'selected-model'})
    # Real host wrapper, with no unrelated surface/coordination input.
    selected.execution_adapter = lambda _: wrapped
    delivery = SimpleNamespace(prepare=AsyncMock(side_effect=lambda request, *_: request),
                               commit=lambda request: None)
    wrapped = SurfaceProvider(raw, delivery)
    coordinator.loop.root_provider = selected
    original_get = coordinator.get
    coordinator.get = lambda name: {'fixture': raw} if name == 'providers' else original_get(name)
    coordinator.context.config.update(max_tokens=20000, summarize_trigger=.2, native_min_new_tokens=0)
    coordinator.context.active_operations = lambda: [{'id': 'uncertain-op', 'outcome': 'unknown',
                                                     'instruction': 'Inspect, never replay.'}]
    async def instructions():
        return 'Accepted instruction: keep originals and never replay uncertain effects.'
    await coordinator.context.set_system_prompt_factory(instructions)
    async def count_view(rows):
        request = ChatRequest(messages=[Message(**row) for row in rows],
            tools=[ToolSpec(name='fixture_read', description='Read only', parameters={})])
        return {'dispatch': request, 'budget_decision':
                await selected.request_budget(request, context_estimate=10000)}
    async def prepare():
        return await coordinator.context.get_measured_request_view(
            provider=selected, retain_contents=['Accepted reminder: retain result identities.'],
            count_view=count_view)
    return coordinator, raw, selected, adapter, checkpoint, transcript, prepare


def native_history():
    return [
        {'role': 'user', 'content': 'Build report; keep originals.'},
        {'role': 'assistant', 'content': 'FILLER:' + 'research ' * 4000},
        {'role': 'assistant', 'content': '', 'tool_calls': [
            {'id': 'done-once', 'name': 'fixture_read', 'arguments': {'path': 'report.txt'}}]},
        {'role': 'tool', 'tool_call_id': 'done-once', 'name': 'fixture_read',
         'content': 'Verified result: report-v1; effect completed once.'},
        {'role': 'user', 'content': 'Correction: use report-v2.'},
        {'role': 'assistant', 'content': 'Acknowledged.'},
        {'role': 'user', 'content': 'Accepted reminder: retain result identities.',
         'metadata': {'ephemeral': True, 'persisted': True, 'reminder_placement': 'tail'}},
        {'role': 'user', 'content': 'Continue, without replaying effects.'},
    ]


async def test_native_measured_context_reduces_twice_and_restores_without_compaction_or_completion(tmp_path):
    original = native_history()
    c, raw, selected, adapter, checkpoint, transcript, prepare = await native_fixture(tmp_path, original)
    ends = []
    for cycle in range(2):
        if cycle:
            await c.context.add_message({'role': 'assistant', 'content': 'FILLER:' + 'new evidence ' * 3000})
            await c.context.add_message({'role': 'user', 'content': 'Correction: keep report-v3 too.'})
            await c.context.add_message({'role': 'assistant', 'content': 'Accepted.'})
            await c.context.add_message({'role': 'user', 'content': 'Continue cycle two.'})
        canonical = await c.context.get_messages()
        before = len(raw.counted)
        result = await prepare()
        dispatch = result['final_attempt']['dispatch']
        decision = result['final_attempt']['budget_decision']
        assert decision['estimated_input_tokens'] < raw.counted[before][1]
        assert decision['estimated_input_tokens'] <= decision['input_limit_tokens']
        print(json.dumps({'cycle': cycle + 1, 'synthetic_input_before': raw.counted[before][1],
                          'synthetic_input_after': decision['estimated_input_tokens'],
                          'input_limit': decision['input_limit_tokens'],
                          'through_message': c.context.summary[0]}))
        expanded = raw.expanded(dispatch)
        rendered = json.dumps(expanded)
        assert 'Accepted instruction: keep originals' in rendered
        assert 'Accepted reminder: retain result identities.' in rendered
        assert 'Verified result: report-v1; effect completed once.' in rendered
        assert 'uncertain-op' in rendered and 'Inspect, never replay.' in rendered
        assert 'Correction: use report-v2.' in rendered
        if cycle:
            assert 'Correction: keep report-v3 too.' in rendered
        assert any(row.get('tool_call_id') == 'done-once' for row in expanded)
        assert dispatch.tools[0].name == 'fixture_read'
        await selected.complete(dispatch)
        assert raw.expanded(raw.sent[-1]) == raw.counted[-1][0] == expanded
        assert raw.sent[-1].tools == dispatch.tools
        assert raw.sent[-1].model == 'selected-model'
        ends.append(c.context.summary[0])
        await checkpoint()
        assert await c.context.get_messages() == canonical
        assert json.loads(transcript.read_text()) == canonical
    assert ends[1] > ends[0] > 0
    assert len(raw.compactions) == len(raw.sent) == 2
    saved = adapter.path.read_bytes()
    newer, next_raw, _, next_adapter, _, _, next_prepare = await native_fixture(tmp_path, canonical)
    restored = await next_prepare()
    restored_rows = next_raw.expanded(restored['final_attempt']['dispatch'])
    assert restored_rows == raw.counted[-1][0]
    assert next_adapter.public()['status'] == 'restored'
    assert next_raw.compactions == next_raw.sent == []
    assert next_adapter.path.read_bytes() == saved
    assert await newer.context.get_messages() == canonical


async def test_native_nonreduction_blocks_dispatch_and_keeps_checkpoint_and_history(tmp_path):
    from amplifier_module_context_managed.errors import CompactionError
    c, raw, selected, adapter, checkpoint, transcript, prepare = await native_fixture(tmp_path, native_history())
    await prepare()
    await checkpoint()
    saved = adapter.path.read_bytes()
    accepted = copy.deepcopy(c.context.summary)
    raw.nonreducing = True
    await c.context.add_message({'role': 'assistant', 'content': 'FILLER:' + 'new evidence ' * 3000})
    await c.context.add_message({'role': 'user', 'content': 'New correction.'})
    await c.context.add_message({'role': 'assistant', 'content': 'Acknowledged.'})
    await c.context.add_message({'role': 'user', 'content': 'Continue.'})
    canonical = await c.context.get_messages()
    with pytest.raises(CompactionError, match='native_no_reduction'):
        await prepare()
    assert c.context.summary == accepted
    assert adapter.path.read_bytes() == saved
    assert json.loads(transcript.read_text()) == canonical
    assert raw.sent == []
    calls = len(raw.compactions)
    with pytest.raises(CompactionError, match='previous_failure'):
        await prepare()
    assert len(raw.compactions) == calls
    assert await c.context.get_messages() == canonical


async def test_native_reduction_does_not_bypass_oversized_required_current_input(tmp_path):
    from amplifier_core.llm_errors import ContextLengthError
    original = native_history()
    original[-1]['content'] = 'Accepted current input: ' + 'retain this exactly ' * 5000
    c, raw, _, _, _, transcript, prepare = await native_fixture(tmp_path, original)
    with pytest.raises(ContextLengthError):
        await prepare()
    assert raw.sent == []
    assert await c.context.get_messages() == original
    assert json.loads(transcript.read_text()) == original


# These are synthetic workload constants, not vendor tokenization or a diagnosis
# of the historical incident. Neither budget nor endpoint behavior changes after
# failure: only an explicitly requested smaller recovery batch can make progress.
RECOVERY_MODEL = 'gpt-6.1-sol'
RECOVERY_BUDGET = 200000
RECOVERY_TRIGGER = .1
RECOVERY_BATCH_BYTES = 110000
RECOVERY_LARGE_BYTES = 160000
RECOVERY_FILLER = 'x' * 100000
RECOVERY_INSTRUCTION = 'Current instruction: preserve ORBIT and original evidence.'
RECOVERY_REMINDER = 'Current reminder: retain result identities; never replay effects.'
RECOVERY_DONE = 'COMPLETED-EFFECT-17'
RECOVERY_UNKNOWN = {'id': 'UNKNOWN-OP-31', 'outcome': 'unknown',
                    'instruction': 'Inspect its receipt; never replay.'}
RECOVERY_PROJECTION = {
    'conversation', 'input', 'instructions', 'model', 'parallel_tool_calls',
    'previous_response_id', 'reasoning', 'text', 'tool_choice', 'tools', 'truncation',
}


class RecoveryWire:
    """Real SDK HTTP boundary; deterministic, content-dependent synthetic service."""

    def __init__(self):
        self.counts, self.compactions, self.completions = [], [], []
        self.events, self.views, self.preparations = [], [], []
        self.effects = self.unknown_invocations = 0
        self.stop_revision = self.epoch = 0
        self.unknown = copy.deepcopy(RECOVERY_UNKNOWN)
        self.instruction, self.reminder = RECOVERY_INSTRUCTION, RECOVERY_REMINDER

    @staticmethod
    def count(body):
        return max(1, len(json.dumps(body, sort_keys=True).encode()) // 4)

    @classmethod
    def reduce_filler(cls, value):
        if isinstance(value, dict):
            return {key: cls.reduce_filler(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls.reduce_filler(item) for item in value]
        if isinstance(value, str):
            return value.replace(RECOVERY_FILLER, '[synthetic filler retained in originals]')
        return value

    async def transport(self, request):
        body = json.loads(request.content)
        if request.url.path.endswith('/input_tokens'):
            count = self.count(body)
            self.counts.append((copy.deepcopy(body), count))
            return httpx.Response(200, json={'input_tokens': count})
        if request.url.path.endswith('/compact'):
            # Large windows retain all bytes and add opaque state (no reduction).
            # Small windows remove ONLY known filler; named facts, prior opaque
            # windows, function calls and actual results remain in order.
            large = len(json.dumps(body['input']).encode()) > RECOVERY_LARGE_BYTES
            window = (copy.deepcopy(body['input']) if large
                      else self.reduce_filler(body['input']))
            window.append({'type': 'compaction', 'encrypted_content': 'synthetic-' + digest(body)})
            self.compactions.append((copy.deepcopy(body), copy.deepcopy(window), large))
            return httpx.Response(200, json={
                'id': 'compact-' + digest(body), 'object': 'response.compaction',
                'created_at': 1, 'output': window,
                'usage': {'input_tokens': self.count(body), 'output_tokens': self.count(window),
                          'total_tokens': self.count(body) + self.count(window)},
            })
        assert request.url.path.endswith('/responses'), request.url
        assert not body.get('stream')
        self.completions.append(copy.deepcopy(body))
        completed = any(item.get('type') == 'function_call_output'
                        and RECOVERY_DONE in str(item.get('output'))
                        for item in body['input'])
        if not completed:
            assert 'SEED-EFFECT' in json.dumps(body['input'])
            output = [{'type': 'function_call', 'id': 'fc-effect-once',
                       'call_id': 'effect-once', 'name': 'save_once',
                       'arguments': '{}', 'status': 'completed'}]
        else:
            text = ('Finished adjacent correction: ship Tuesday; report-v2; UNKNOWN-OP-31 remains unknown.'
                    if 'ship Tuesday' in json.dumps(body['input']) else
                    'Usable continuation: report-v2; COMPLETED-EFFECT-17 retained; UNKNOWN-OP-31 remains unknown.')
            output = [{'type': 'message', 'id': 'msg-' + digest(body),
                       'role': 'assistant', 'status': 'completed',
                       'content': [{'type': 'output_text', 'text': text, 'annotations': []}]}]
        count = self.count({key: value for key, value in body.items() if key in RECOVERY_PROJECTION})
        return httpx.Response(200, json={
            'id': 'response-' + digest(body), 'object': 'response', 'created_at': 1,
            'model': RECOVERY_MODEL, 'status': 'completed', 'output': output,
            'usage': {'input_tokens': count, 'output_tokens': 20, 'total_tokens': count + 20},
        })


@asynccontextmanager
async def ordinary_recovery_case(directory, wire, messages=()):
    """Maintained native_case pattern: Core + PreparedBundle + the real loop.

    The fixture supplies host ownership/Stop observations and synthetic HTTP, not
    a substitute orchestrator, request builder, context recovery or provider.
    """
    from amplifier_core import HookResult, ToolResult
    from amplifier_web.host.session import SelectedProvider
    from amplifier_foundation.session.activation import ActivationGate
    plan = {'session': {
        'orchestrator': {'module': 'loop-streaming', 'config': {
            'use_streaming': False, 'extended_thinking': True,
            'min_delay_between_calls_ms': 0, 'ephemeral_injection_mode': 'tail'}},
        'context': {'module': 'context-managed', 'config': {
            'durable_checkpoints': True, 'archive_recovery': False,
            'max_tokens': RECOVERY_BUDGET, 'summarize_trigger': RECOVERY_TRIGGER,
            'native_min_new_tokens': 0, 'compaction_notice_enabled': False}}}}
    paths = {'loop-streaming': Path(amplifier_module_loop_streaming.__file__).parent,
             'context-managed': Path(amplifier_module_context_managed.__file__).parent}
    session = await PreparedBundle(plan, BundleModuleResolver(paths),
        Bundle(name='ordinary-recovery', session=plan['session'])).create_session(
            session_id='ordinary-recovery')
    coordinator = session.coordinator
    context = coordinator.get('context')
    gate = ActivationGate()
    activation = gate.activate()
    runtime = SimpleNamespace(generation=None, queued_inputs=0, stop_requested=False, closed=False)
    sdk = openai.AsyncOpenAI(api_key='synthetic-not-live', max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(wire.transport)))
    raw = provider_module.OpenAIProvider(client=sdk, config={
        'default_model': RECOVERY_MODEL, 'use_streaming': False, 'max_retries': 0})
    raw.coordinator = coordinator
    selected = SelectedProvider(raw, {
        'instance': 'openai', 'model': RECOVERY_MODEL, 'effort': 'high', 'max_output_tokens': 1000})
    # The base streaming loop selects its mounted mapping; only loop-live has
    # a root_provider override. Mount the actual selected wrapper on this path.
    await coordinator.mount('providers', selected, name='openai')
    coordinator.get('orchestrator').root_provider = selected
    coordinator.register_capability('live.activation', gate)
    coordinator.register_capability('live.runtime', runtime)
    coordinator.register_capability('web.context_recovery.epoch', lambda: wire.epoch)
    coordinator.register_capability('context.active_operations', lambda: [copy.deepcopy(wire.unknown)])

    async def bridge(operation, args):
        assert operation == 'dispatch'
        assert args == {'action': 'coordination.list', 'args': {
            'sessionId': session.session_id, 'limit': 1, 'includeWorkers': False}}
        return {'result': {'items': [{'target': {'sessionId': session.session_id},
                                     'signal': {'interruptionRevision': wire.stop_revision}}]}}

    coordinator.register_capability('web.app_access.bridge', bridge)

    class SaveOnce:
        name = 'save_once'
        description = 'Save the synthetic report once'
        input_schema = {'type': 'object', 'properties': {}}

        async def execute(self, arguments):
            assert arguments == {}
            wire.effects += 1
            return ToolResult(success=True, output={
                'receipt': RECOVERY_DONE, 'artifact': 'report-v1', 'outcome': 'completed'})

    class UnknownOperation:
        name = 'unknown_operation'
        description = 'Sentinel that must never execute'
        input_schema = {'type': 'object', 'properties': {}}

        async def execute(self, arguments):
            wire.unknown_invocations += 1
            raise AssertionError('Recovery replayed an operation with an unknown outcome')

    await coordinator.mount('tools', SaveOnce(), name='save_once')
    await coordinator.mount('tools', UnknownOperation(), name='unknown_operation')
    await context.set_messages(copy.deepcopy(list(messages)))

    async def instructions():
        return wire.instruction

    async def reminder(event, data):
        return HookResult(action='inject_context', context_injection=wire.reminder,
                          context_injection_role='user', ephemeral=True)

    async def observe(event, data):
        wire.events.append((event, copy.deepcopy(data)))
        return HookResult()

    await context.set_system_prompt_factory(instructions)
    coordinator.hooks.register('prompt:submit', reminder, name='recovery-current-reminder')
    for event in ('provider:request_attribution', 'context:compaction_finished',
                  'orchestrator:provider_budget', 'tool:post'):
        coordinator.hooks.register(event, observe, priority=99, name='recovery-witness-' + event)
    transcript = directory / 'transcript.json'
    adapter = None

    async def checkpoint():
        gate.check_current()
        directory.mkdir(parents=True, exist_ok=True)
        transcript.write_text(json.dumps(await context.get_messages()))
        if adapter:
            adapter.save()

    # Test composition also runs against the original host, which has no writer
    # seam. That baseline must reach the missing recovery control, not fail setup.
    writer = ({'writer_guard': gate.check_current}
              if 'writer_guard' in inspect.signature(ContextContinuity).parameters else {})
    adapter = ContextContinuity(coordinator, session.session_id, directory, checkpoint, **writer)
    controls = RuntimeControls(session, runtime)
    measured = coordinator.get_capability('context.measured_request_view')

    async def observe_preparation(**kwargs):
        wire.preparations.append(kwargs)
        result = await measured(**kwargs)
        wire.views.append(result)
        return result

    coordinator.register_capability('context.measured_request_view', observe_preparation)
    try:
        yield SimpleNamespace(session=session, context=context, raw=raw, selected=selected,
            adapter=adapter, controls=controls, runtime=runtime, gate=gate,
            activation=activation, checkpoint=checkpoint, transcript=transcript, measured=measured)
    finally:
        await controls.close()
        await sdk.close()
        await session.cleanup()


async def append_recovery_evidence(context, labels):
    for label in labels:
        await context.add_message({'role': 'user', 'content': f'Accepted evidence {label}: preserve ORBIT.'})
        await context.add_message({'role': 'assistant', 'content': f'FILLER-{label}: ' + RECOVERY_FILLER})
    for row in (
        {'role': 'user', 'content': 'Correction: use report-v2; budget 25.'},
        {'role': 'assistant', 'content': 'Verified correction retained.'},
        {'role': 'user', 'content': 'Keep the completed receipt and UNKNOWN-OP-31 outcome unknown.'},
        {'role': 'assistant', 'content': 'No effects were replayed.'},
    ):
        await context.add_message(row)


def recovery_command(wire, command_id='recover-once'):
    # Represents a trusted command supplied by the host management boundary.
    # Management authorization tests belong to the parent, not this fixture.
    return {'_recoveryCommand': {'id': command_id, 'interruptionRevision': wire.stop_revision},
            'targetBytes': RECOVERY_BATCH_BYTES}


def assert_recovery_dispatch(wire, result, *, adjacent=False):
    final = result['final_attempt']
    decision = final['budget_decision']
    assert decision['estimated_input_tokens'] <= min(
        RECOVERY_BUDGET, decision['input_limit_tokens'])
    body = wire.completions[-1]
    assert wire.counts[-1][0] == {
        key: value for key, value in body.items() if key in RECOVERY_PROJECTION}
    # The maintained provider's public count contract does not include the
    # separately qualified attribution extension. Compare the actual SDK HTTP
    # count/create projections above and the returned authoritative count here.
    assert decision['measurement']['kind'] == 'provider_count'
    assert decision['measurement']['input_tokens'] == wire.counts[-1][1]
    assert wire.counts[-1][1] == wire.count(wire.counts[-1][0])
    assert body['model'] == RECOVERY_MODEL and body['max_output_tokens'] == 1000
    assert body['reasoning']['effort'] == 'high'
    assert {tool['name'] for tool in body['tools']} == {'save_once', 'unknown_operation'}
    rendered = json.dumps(body)
    for fact in (wire.instruction, wire.reminder, 'ORBIT', 'report-v2',
                 'budget 25', RECOVERY_DONE, 'UNKNOWN-OP-31', 'unknown', 'never replay'):
        assert fact in rendered
    assert '"outcome": "unknown"' in str(body)
    assert not any(item.get('type') == 'function_call_output' and item.get('call_id') == 'UNKNOWN-OP-31'
                   for item in body['input'])
    if adjacent:
        assert 'ship Tuesday' in rendered
    assert wire.effects == 1 and wire.unknown_invocations == 0
    assert wire.unknown == RECOVERY_UNKNOWN


@pytest.mark.parametrize('prior_checkpoint', [False, True], ids=['without-prior', 'with-prior'])
async def test_explicit_recovery_in_ordinary_loop_then_adjacent_compaction(
        tmp_path, monkeypatch, prior_checkpoint):
    """#1198/#1207: actual failed preparation -> explicit intent -> normal loop."""
    from amplifier_module_context_managed.errors import CompactionError
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'home'))
    wire = RecoveryWire()
    async with ordinary_recovery_case(tmp_path / 'session', wire) as case:
        await case.session.execute('SEED-EFFECT: save report-v1 once; preserve ORBIT.')
        assert wire.effects == 1
        assert all(body['max_output_tokens'] == 1000 for body in wire.completions)
        actual_receipt = copy.deepcopy(next(data for event, data in wire.events if event == 'tool:post'))
        if prior_checkpoint:
            await append_recovery_evidence(case.context, ['PRIOR'])
            await case.session.execute('Establish the prior report checkpoint.')
            assert case.context.summary is not None
        await case.checkpoint()
        prior = copy.deepcopy(case.context.summary)
        saved = case.adapter.path.read_bytes() if case.adapter.path.exists() else None
        await append_recovery_evidence(case.context, ['A', 'B', 'C'])
        prefix = copy.deepcopy(await case.context.get_messages())
        before_calls = len(wire.completions)
        with pytest.raises(RuntimeError, match=r'Execution failed: CompactionError:.*native_no_reduction'):
            await case.session.execute('Failing ordinary request: finish from the retained evidence.')
        failed_source = copy.deepcopy(await case.context.get_messages())
        assert failed_source[:len(prefix)] == prefix
        assert case.context.summary == prior
        assert (case.adapter.path.read_bytes() if case.adapter.path.exists() else None) == saved
        assert json.loads(case.transcript.read_text()) == failed_source
        assert len(wire.completions) == before_calls and wire.compactions[-1][2] is True
        failure = copy.deepcopy([data for event, data in wire.events
                                 if event == 'context:compaction_finished'][-1])
        assert failure['failure']['code'] == 'native_no_reduction'
        assert failure['native_input_tokens_after'] >= failure['input_tokens_before']
        calls = len(wire.compactions)
        # Retry the same preparation, NOT a new human input or changed source.
        with pytest.raises(CompactionError, match='previous_failure'):
            await case.measured(**wire.preparations[-1])
        assert len(wire.compactions) == calls and len(wire.completions) == before_calls
        assert await case.context.get_messages() == failed_source

        # A saved count_view from the failed turn cannot satisfy recovery: the
        # next ordinary loop must assemble these current instructions/reminders.
        wire.instruction += ' Current instruction revision: BETA.'
        wire.reminder += ' Current reminder revision: BETA.'
        before_arm = (len(wire.counts), len(wire.compactions), len(wire.completions))
        task_before = copy.deepcopy(case.controls.tasks.record())
        contract = copy.deepcopy({'plan': case.session.coordinator.config,
                                  'context': case.context.config,
                                  'selection': case.controls.selection})
        pending = await case.controls.perform('context.recovery.request', recovery_command(wire))
        assert pending['state'] == 'pending' and pending['attempts'] == 1
        assert await case.controls.perform('context.recovery.request', recovery_command(wire)) == pending
        assert await case.controls.perform('context.recovery.status') == pending
        assert (len(wire.counts), len(wire.compactions), len(wire.completions)) == before_arm
        assert await case.context.get_messages() == failed_source
        assert case.controls.tasks.record() == task_before
        assert case.runtime.generation is None and case.runtime.queued_inputs == 0
        result = await case.session.execute('Explicit fresh input: continue using report-v2 without replay.')
        assert 'Usable continuation: report-v2' in result
        recovered = wire.views[-1]
        assert recovered['outcome'] == 'recovered'
        assert_recovery_dispatch(wire, recovered)
        status = await case.controls.perform('context.recovery.status')
        assert status['state'] == 'committed' and status['attempts'] == 1
        assert status['completedParts'] > 1
        assert status['verification']['after'] < status['verification']['before']
        assert status['verification']['after'] <= status['verification']['limit']
        assert status['verification']['limit'] <= RECOVERY_BUDGET
        assert len(wire.completions) == before_calls + 1
        assert all(not row[2] for row in wire.compactions[calls:])
        assert {'plan': case.session.coordinator.config, 'context': case.context.config,
                'selection': case.controls.selection} == contract
        recovered_window = copy.deepcopy(case.context.summary[1]['metadata']['openai:compaction']['output'])
        recovered_end = case.context.summary[0]
        recovered_source = copy.deepcopy(await case.context.get_messages())
        assert recovered_source[:len(failed_source)] == failed_source

        await append_recovery_evidence(case.context, ['ADJACENT'])
        await case.context.add_message({'role': 'user', 'content': 'Latest settled correction: ship Tuesday.'})
        await case.context.add_message({'role': 'assistant', 'content': 'Tuesday accepted; preserve report-v2.'})
        adjacent_prefix = copy.deepcopy(await case.context.get_messages())
        adjacent_calls = len(wire.compactions)
        wire.instruction = RECOVERY_INSTRUCTION + ' Current instruction revision: GAMMA.'
        wire.reminder = RECOVERY_REMINDER + ' Current reminder revision: GAMMA.'
        result = await case.session.execute('Finish the adjacent cycle with the retained report.')
        assert 'Finished adjacent correction: ship Tuesday' in result
        assert len(wire.compactions) == adjacent_calls + 1
        assert wire.compactions[-1][0]['input'][:len(recovered_window)] == recovered_window
        assert case.context.summary[0] > recovered_end
        assert_recovery_dispatch(wire, wire.views[-1], adjacent=True)
        assert {'plan': case.session.coordinator.config, 'context': case.context.config,
                'selection': case.controls.selection} == contract
        assert await case.controls.perform('context.recovery.status') == status
        assert ('context:compaction_finished', failure) in wire.events
        assert ('tool:post', actual_receipt) in wire.events
        canonical = await case.context.get_messages()
        assert canonical[:len(adjacent_prefix)] == adjacent_prefix
        assert canonical[:len(recovered_source)] == recovered_source
        assert sum(row.get('role') == 'tool' and row.get('tool_call_id') == 'effect-once'
                   for row in canonical) == 1
        assert not any(row.get('role') == 'tool' and row.get('tool_call_id') == 'UNKNOWN-OP-31'
                       for row in canonical)
        await case.checkpoint()
        assert json.loads(case.transcript.read_text()) == canonical


@pytest.mark.parametrize('change', [
    'pending', 'stop', 'configuration', 'prefix', 'running', 'installing', 'unknown',
])
async def test_recovery_intent_remount_and_preparation_fences(tmp_path, monkeypatch, change):
    """A pending intent survives remount; uncertain consumed work never retries."""
    from amplifier_module_context_managed.errors import CompactionError
    from amplifier_web.context_continuity import ContextRecoveryError
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'home'))
    directory, wire = tmp_path / 'session', RecoveryWire()
    async with ordinary_recovery_case(directory, wire) as first:
        await first.session.execute('SEED-EFFECT: save report-v1 once; preserve ORBIT.')
        await append_recovery_evidence(first.context, ['PRIOR'])
        await first.session.execute('Establish a compatible prior checkpoint.')
        await first.checkpoint()
        prior = copy.deepcopy(first.context.summary)
        saved = first.adapter.path.read_bytes()
        await append_recovery_evidence(first.context, ['A', 'B', 'C'])
        with pytest.raises(RuntimeError, match=r'Execution failed: CompactionError:.*native_no_reduction'):
            await first.session.execute('Failing ordinary preparation before remount.')
        source = copy.deepcopy(await first.context.get_messages())
        command = recovery_command(wire)
        pending = await first.controls.perform('context.recovery.request', command)
        assert pending['state'] == 'pending'
        journal_path = first.adapter.recovery_path
        if change in {'running', 'installing', 'unknown'}:
            # Exact durable crash states, not fabricated successful recovery.
            # No candidate is installed and no provider response is invented.
            journal = json.loads(journal_path.read_text())
            journal['attempts'][journal['current']]['state'] = change
            journal_path.write_text(json.dumps(journal))
        first.gate.release(first.activation)

    retained_journal = journal_path.read_bytes()
    if change == 'stop':
        wire.stop_revision += 1  # Durable host Stop, independent of a new activation.
    if change == 'prefix':
        source[0]['content'] += ' Changed canonical prefix.'
    before = (len(wire.counts), len(wire.compactions), len(wire.completions))
    async with ordinary_recovery_case(directory, wire, source) as second:
        assert second.gate is not first.gate
        assert second.activation is not first.activation
        assert second.gate.current_valid and not first.gate.current_valid
        assert not second.adapter.loaded and second.context.summary is None
        if change == 'configuration':
            second.context.config['summarize_trigger'] = .15
        uncertain = change in {'running', 'installing', 'unknown'}
        status = await second.controls.perform('context.recovery.status')
        assert status['state'] == ('unknown' if uncertain else 'pending')
        assert status['attempts'] == 1
        assert await second.controls.perform('context.recovery.request', command) == status
        assert journal_path.read_bytes() == retained_journal  # Read/duplicate never rearm.
        assert (len(wire.counts), len(wire.compactions), len(wire.completions)) == before
        assert await second.context.get_messages() == source
        assert second.adapter.path.read_bytes() == saved
        if change == 'pending':
            assert 'Usable continuation' in await second.session.execute('Fresh continuation after remount.')
            recovered = wire.views[-1]
            assert recovered['outcome'] == 'recovered'
            assert second.adapter.loaded
            assert second.context.summary[0] > prior[0]
            assert_recovery_dispatch(wire, recovered)
            # Startup restore preceded module recovery: it must seed with the
            # accepted checkpoint instead of reprocessing that original prefix.
            prior_window = prior[1]['metadata']['openai:compaction']['output']
            first_batch = wire.compactions[before[1]][0]['input']
            assert first_batch[:len(prior_window)] == prior_window
            assert len(wire.completions) == before[2] + 1
            committed = await second.controls.perform('context.recovery.status')
            assert committed['state'] == 'committed' and committed['attempts'] == 1
            after = (len(wire.counts), len(wire.compactions), len(wire.completions))
            assert await second.controls.perform('context.recovery.request', command) == committed
            assert (len(wire.counts), len(wire.compactions), len(wire.completions)) == after
        else:
            code = {'stop': 'recovery_interrupted', 'configuration': 'recovery_contract_changed',
                    'prefix': 'recovery_source_changed'}.get(change, 'recovery_unreconciled')
            with pytest.raises(RuntimeError, match='Execution failed: ContextRecoveryError:.*' + code):
                await second.session.execute('Explicit new input must not bypass the recovery fence.')
            status = await second.controls.perform('context.recovery.status')
            assert status['state'] == ('unknown' if uncertain else 'failed')
            assert second.context.summary is None  # Nothing restored/installed before admission.
            assert second.adapter.path.read_bytes() == saved
            assert (len(wire.counts), len(wire.compactions), len(wire.completions)) == before
            assert await second.controls.perform('context.recovery.request', command) == status
            if uncertain:
                with pytest.raises(ContextRecoveryError, match='recovery_unreconciled'):
                    await second.controls.perform('context.recovery.request',
                                                  recovery_command(wire, 'do-not-replay'))
            with pytest.raises(RuntimeError, match=r'Execution failed: ContextRecoveryError:.*recovery_unreconciled'):
                await second.session.execute('Another input does not retry consumed recovery.')
            assert (len(wire.counts), len(wire.compactions), len(wire.completions)) == before
        canonical = await second.context.get_messages()
        assert canonical[:len(source)] == source
        assert wire.effects == 1 and wire.unknown_invocations == 0
        assert wire.unknown == RECOVERY_UNKNOWN
        assert second.context.config['max_tokens'] == RECOVERY_BUDGET


def recovery_decision_accounts():
    return [{'module': 'provider-fixture-decision', 'instance_id': name,
             'source': 'git+https://example.test/decision@exact', 'enabled': enabled,
             'config': {'api_key': secret, 'decisions': {
                 'enabled': True, 'backends': {'fixture': {'model': 'decision-model'}}}}}
            for name, enabled, secret in (
                ('decision-active', True, 'DECISION-ACTIVE-SECRET-72'),
                ('decision-disabled', False, 'DECISION-DISABLED-SECRET-81'))]


@pytest.fixture
def project_recovery_accounts(monkeypatch):
    from amplifier_web import decision_accounts as accounts
    definitions = {'provider-fixture-decision': 'hooks-fixture-decisions'}
    # Replace only package metadata discovery. UUID allocation, projection and
    # logical-plan validation are real; no decision hook or service is invoked.
    monkeypatch.setattr(accounts, 'catalogs', lambda: definitions)

    def project(case, rows):
        logical = accounts.logical_plan(case.session.coordinator.config)
        logical['providers'] = copy.deepcopy(rows)
        logical.setdefault('hooks', [])
        projection = accounts.DecisionAccounts(
            logical, None, lambda module, source: source)
        case.session.coordinator.config.clear()
        case.session.coordinator.config.update(projection.plan)
        return logical, projection

    return project


@pytest.mark.parametrize('change', [
    'unchanged', 'account', 'source', 'credential', 'settings', 'disabled-credential',
])
async def test_decision_account_remount_recovery_uses_exact_logical_choices(
        tmp_path, monkeypatch, project_recovery_accounts, change):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'home'))
    directory, wire = tmp_path / 'session', RecoveryWire()
    rows = recovery_decision_accounts()
    secrets = [row['config']['api_key'] for row in rows] + ['ROTATED-DECISION-SECRET-96']
    async with ordinary_recovery_case(directory, wire) as first:
        logical, projection = project_recovery_accounts(first, rows)
        first_plan = copy.deepcopy(first.session.coordinator.config)
        contract = first.controls.recovery_contract()
        assert contract['plan'] == logical
        assert first.session.coordinator.config == first_plan  # Inspection is not a remount.
        bindings = {row['binding_id'] for row in projection.records}
        assert len(bindings) == 2 and len(projection.expected) == 1
        await first.session.execute('SEED-EFFECT: save report-v1 once; preserve ORBIT.')
        await append_recovery_evidence(first.context, ['A', 'B', 'C'])
        before_failure = len(wire.completions)
        with pytest.raises(RuntimeError, match=r'Execution failed: CompactionError:.*native_no_reduction'):
            await first.session.execute('Failing preparation with decision accounts before remount.')
        assert len(wire.completions) == before_failure
        source = copy.deepcopy(await first.context.get_messages())
        saved = first.adapter.path.read_bytes() if first.adapter.path.exists() else None
        command = recovery_command(wire)
        before_arm = (len(wire.counts), len(wire.compactions), len(wire.completions))
        pending = await first.controls.perform('context.recovery.request', command)
        assert pending['state'] == 'pending' and pending['attempts'] == 1
        assert (len(wire.counts), len(wire.compactions), len(wire.completions)) == before_arm
        journal_path = first.adapter.recovery_path
        journal = json.loads(journal_path.read_text())
        assert journal['attempts'][journal['current']]['contract'] == {
            'version': 1, 'sha256': digest(contract)}
        for secret in secrets:
            assert secret not in journal_path.read_text() + json.dumps(pending)
        first.gate.release(first.activation)

    if change == 'account':
        rows[0]['instance_id'] = 'different-account'
    elif change == 'source':
        rows[0]['source'] += '-changed'
    elif change in {'credential', 'disabled-credential'}:
        rows[1 if change == 'disabled-credential' else 0]['config']['api_key'] = secrets[-1]
    elif change == 'settings':
        rows[0]['config']['decisions']['backends']['fixture']['model'] = 'different-decision-model'
    retained_journal = journal_path.read_bytes()
    before = (len(wire.counts), len(wire.compactions), len(wire.completions))
    async with ordinary_recovery_case(directory, wire, source) as second:
        logical, projection = project_recovery_accounts(second, rows)
        assert bindings.isdisjoint(row['binding_id'] for row in projection.records)
        assert len(projection.records) == 2 and len(projection.expected) == 1
        assert second.session.coordinator.config != first_plan
        assert second.gate is not first.gate and not first.gate.current_valid
        next_contract = second.controls.recovery_contract()
        assert next_contract['plan'] == logical
        assert (next_contract == contract) is (change == 'unchanged')
        assert await second.controls.perform('context.recovery.status') == pending
        assert await second.controls.perform('context.recovery.request', command) == pending
        assert journal_path.read_bytes() == retained_journal
        assert (len(wire.counts), len(wire.compactions), len(wire.completions)) == before
        if change == 'unchanged':
            assert 'Usable continuation' in await second.session.execute('Fresh input after decision remount.')
            assert wire.views[-1]['outcome'] == 'recovered'
            assert_recovery_dispatch(wire, wire.views[-1])
            status = await second.controls.perform('context.recovery.status')
            assert status['state'] == 'committed' and status['attempts'] == 1
            assert status['completedParts'] > 1
            assert status['verification']['after'] < status['verification']['before']
            assert len(wire.completions) == before[2] + 1
        else:
            with pytest.raises(RuntimeError, match=r'Execution failed: ContextRecoveryError:.*recovery_contract_changed'):
                await second.session.execute('Changed decision choices must refuse before provider work.')
            status = await second.controls.perform('context.recovery.status')
            assert status['state'] == 'failed' and status['code'] == 'recovery_contract_changed'
            assert not second.adapter.loaded and second.context.summary is None
            assert (second.adapter.path.read_bytes() if second.adapter.path.exists() else None) == saved
            assert (len(wire.counts), len(wire.compactions), len(wire.completions)) == before
            with pytest.raises(RuntimeError, match=r'Execution failed: ContextRecoveryError:.*recovery_unreconciled'):
                await second.session.execute('A fresh input cannot replay the refused recovery.')
            assert (len(wire.counts), len(wire.compactions), len(wire.completions)) == before
        after = (len(wire.counts), len(wire.compactions), len(wire.completions))
        assert await second.controls.perform('context.recovery.request', command) == status
        assert (len(wire.counts), len(wire.compactions), len(wire.completions)) == after
        assert (await second.context.get_messages())[:len(source)] == source
        assert wire.effects == 1 and wire.unknown_invocations == 0
        assert wire.unknown == RECOVERY_UNKNOWN
        assert second.context.config['max_tokens'] == RECOVERY_BUDGET
        for secret in secrets:
            assert secret not in journal_path.read_text() + json.dumps(status)
        assert 'binding_id' not in journal_path.read_text()


@pytest.mark.parametrize('phase', ['arming', 'preparation'])
async def test_decision_account_recovery_guard_detects_change_during_stop_check(
        tmp_path, monkeypatch, project_recovery_accounts, phase):
    from amplifier_web.context_continuity import ContextRecoveryError
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'home'))
    wire = RecoveryWire()
    source = [{'role': 'user', 'content': 'Keep the original task.'}]
    async with ordinary_recovery_case(tmp_path / 'session', wire, source) as case:
        rows = recovery_decision_accounts()
        project_recovery_accounts(case, rows)
        if phase == 'preparation':
            pending = await case.controls.perform('context.recovery.request', recovery_command(wire))
            assert pending['state'] == 'pending'
        coordinator = case.session.coordinator
        bridge = coordinator.get_capability('web.app_access.bridge')

        async def change_during_stop_check(*args):
            response = await bridge(*args)
            rows[0]['config']['api_key'] = 'ACTIVE-RUN-CHANGED-SECRET'
            project_recovery_accounts(case, rows)
            return response

        coordinator.register_capability('web.app_access.bridge', change_during_stop_check)
        if phase == 'arming':
            with pytest.raises(ContextRecoveryError, match='recovery_contract_changed'):
                await case.controls.perform('context.recovery.request', recovery_command(wire))
            assert not case.adapter.recovery_path.exists()
        else:
            with pytest.raises(RuntimeError, match=r'Execution failed: ContextRecoveryError:.*recovery_contract_changed'):
                await case.session.execute('Fresh input cannot bypass an in-flight configuration change.')
            status = await case.controls.perform('context.recovery.status')
            assert status['state'] == 'failed' and status['code'] == 'recovery_contract_changed'
            assert not case.adapter.loaded and case.context.summary is None
            assert 'ACTIVE-RUN-CHANGED-SECRET' not in case.adapter.recovery_path.read_text() + json.dumps(status)
        assert not case.adapter.path.exists()
        assert wire.counts == wire.compactions == wire.completions == []
        assert wire.effects == wire.unknown_invocations == 0
        assert (await case.context.get_messages())[:len(source)] == source


async def test_decision_account_recovery_rejects_unvalidated_projection(
        tmp_path, monkeypatch, project_recovery_accounts):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'home'))
    wire = RecoveryWire()
    source = [{'role': 'user', 'content': 'Keep the original task.'}]
    async with ordinary_recovery_case(tmp_path / 'session', wire, source) as case:
        project_recovery_accounts(case, recovery_decision_accounts())
        hook = case.session.coordinator.config['hooks'][0]
        hook['config']['api_key'] = 'FORGED-HOOK-SECRET'
        with pytest.raises(ValueError, match='does not match its logical account/source'):
            await case.controls.perform('context.recovery.request', recovery_command(wire))
        assert not case.adapter.recovery_path.exists()
        assert not case.adapter.path.exists()
        assert wire.counts == wire.compactions == wire.completions == []
        assert wire.effects == wire.unknown_invocations == 0
        assert await case.context.get_messages() == source


def bind_continuity_recovery_guards(coordinator, adapter, contract):
    """Host ownership/contract observations for the existing synthetic fixture."""
    from amplifier_foundation.session.activation import ActivationGate
    gate = ActivationGate()
    gate.activate()
    coordinator.cancellation = SimpleNamespace(is_cancelled=False)
    coordinator.register_capability('live.activation', gate)
    coordinator.register_capability('live.runtime', SimpleNamespace(stop_requested=False, closed=False))
    coordinator.register_capability('web.context_recovery.epoch', lambda: 0)
    coordinator.register_capability('web.context_recovery.contract', lambda: contract)
    coordinator.register_capability('web.app_access.bridge', AsyncMock(return_value={
        'result': {'items': [{'target': {'sessionId': 'session'},
                              'signal': {'interruptionRevision': 0}}]}}))
    adapter.writer_guard = gate.check_current
    return gate


@pytest.mark.parametrize('change', [
    None, 'selection', 'maxOutputTokens', 'context', 'loop', 'plan',
], ids=['unchanged', 'model', 'output', 'context', 'loop', 'credentials'])
async def test_recovery_contract_digest_hides_secrets_and_fences_remount(tmp_path, change):
    from amplifier_web.context_continuity import ContextRecoveryError
    sentinels = ('CREDENTIAL-SENTINEL-72', 'PROMPT-SENTINEL-81', 'TOOL-SCHEMA-SENTINEL-96')
    contract = {
        'selection': {'model': 'selected-model'}, 'maxOutputTokens': 1000,
        'context': {'summarize_trigger': .2}, 'loop': {'use_streaming': False},
        'plan': {'providers': [{'config': {'api_key': sentinels[0]}}],
                 'instructions': sentinels[1], 'tools': [{'parameters': {'description': sentinels[2]}}]},
    }
    source = native_history()
    first, raw, _, adapter, _, _, _ = await native_fixture(tmp_path, source)
    gate = bind_continuity_recovery_guards(first, adapter, contract)
    pending = await adapter.request_recovery('digest-only', 0)
    journal = json.loads(adapter.recovery_path.read_text())
    assert journal['attempts']['digest-only']['contract'] == {'version': 1, 'sha256': digest(contract)}
    assert raw.counted == raw.compactions == raw.sent == []
    assert not adapter.path.exists()  # Arming did not checkpoint or compact.
    for sentinel in sentinels:
        assert sentinel not in adapter.recovery_path.read_text()
        assert sentinel not in json.dumps(pending)
    gate.release(gate.current())

    # A remount gets fresh object identities and a different dictionary order.
    newer_contract = copy.deepcopy(dict(reversed(list(contract.items()))))
    if change == 'maxOutputTokens':
        newer_contract[change] += 1
    elif change == 'selection':
        newer_contract[change]['model'] = 'different-model'
    elif change == 'context':
        newer_contract[change]['summarize_trigger'] = .3
    elif change == 'loop':
        newer_contract[change]['use_streaming'] = True
    elif change == 'plan':
        newer_contract[change]['providers'][0]['config']['api_key'] = 'changed-credential'
    second, newer_raw, _, newer, _, _, prepare = await native_fixture(tmp_path, source)
    bind_continuity_recovery_guards(second, newer, newer_contract)
    if change:
        with pytest.raises(ContextRecoveryError, match='recovery_contract_changed'):
            await prepare()
        assert newer.recovery_status()['state'] == 'failed'
        assert not newer.loaded and second.context.summary is None
        assert not newer.path.exists()
        assert newer_raw.counted == newer_raw.compactions == newer_raw.sent == []
    else:
        assert (await prepare())['outcome'] == 'recovered'
        assert newer.recovery_status()['state'] == 'committed'
    for sentinel in sentinels:
        assert sentinel not in newer.recovery_path.read_text()
        assert sentinel not in json.dumps(newer.recovery_status())


@pytest.mark.parametrize('invalidate', ['before-export', 'during-export'])
async def test_ordinary_checkpoint_save_fences_activation_after_recovery_commit(tmp_path, invalidate):
    source = native_history()
    coordinator, raw, _, adapter, _, _, prepare = await native_fixture(tmp_path, source)
    gate = bind_continuity_recovery_guards(coordinator, adapter, {'fixture': 1})
    await adapter.request_recovery('save-fence', 0)
    assert (await prepare())['outcome'] == 'recovered'
    assert adapter.recovery_status()['state'] == 'committed'
    saved = adapter.path.read_bytes()
    calls = (len(raw.counted), len(raw.compactions), len(raw.sent))
    export = coordinator.get_capability('context.checkpoint.export')
    exports = []
    coordinator.context.evidence_refs.append({'kind': 'fixture', 'revision': 'new-current-evidence'})

    def observed_export(identity):
        exports.append(identity)
        record = export(identity)
        if invalidate == 'during-export' and len(exports) == 1:
            gate.release(gate.current())
        return record

    coordinator.register_capability('context.checkpoint.export', observed_export)
    persist = coordinator.get_capability('context.persist_checkpoint')
    if invalidate == 'before-export':
        gate.release(gate.current())
    with pytest.raises(RuntimeError, match='released or superseded'):
        persist()
    assert len(exports) == (0 if invalidate == 'before-export' else 1)
    assert adapter.path.read_bytes() == saved

    gate.activate()
    persist()  # The current owner can still save ordinary progress after commit.
    record = json.loads(adapter.path.read_text())
    assert record['evidenceRefs'][-1] == {'kind': 'fixture', 'revision': 'new-current-evidence'}
    assert adapter.path.read_bytes() != saved
    assert adapter.recovery_status()['state'] == 'committed'
    assert (len(raw.counted), len(raw.compactions), len(raw.sent)) == calls


@pytest.mark.parametrize('state', ['installing', 'unknown', 'committed'])
@pytest.mark.parametrize('path', ['preserve', 'non-measured'])
async def test_lazy_restore_requires_committed_recovery_candidate(tmp_path, state, path):
    from amplifier_web.context_continuity import ContextRecoveryError
    source = native_history()
    first, _, _, adapter, _, _, prepare = await native_fixture(tmp_path, source)
    gate = bind_continuity_recovery_guards(first, adapter, {'fixture': 1})
    await adapter.request_recovery('restore-fence', 0)
    assert (await prepare())['outcome'] == 'recovered'
    summary = copy.deepcopy(first.context.summary)
    candidate = adapter.path.read_bytes()
    journal = json.loads(adapter.recovery_path.read_text())
    attempt = journal['attempts'][journal['current']]
    assert attempt['candidateSha256'] == json.loads(candidate)['sha256']
    # Synthetic crash disposition over an actual verified candidate. The test
    # changes only receipt state; it does not invent provider output or replay it.
    attempt['state'] = state
    adapter.recovery_path.write_text(json.dumps(journal))
    retained = adapter.recovery_path.read_bytes()
    gate.release(gate.current())

    second, raw, selected, newer, _, _, _ = await native_fixture(tmp_path, source)
    bind_continuity_recovery_guards(second, newer, {'fixture': 1})
    assert not newer.loaded and second.context.summary is None
    async def read_view():
        if path == 'preserve':
            return await newer.preserve(source)
        return await second.context.get_messages_for_request_retaining(
            provider=selected, retain_contents=['Accepted reminder: retain result identities.'])
    if state != 'committed':
        with pytest.raises(ContextRecoveryError, match='recovery_unreconciled'):
            await read_view()
        assert not newer.loaded and second.context.summary is None
        newer.save()
        with pytest.raises(ContextRecoveryError, match='recovery_unreconciled'):
            await read_view()  # Reading again is not reconciliation.
    else:
        await read_view()
        assert newer.loaded and second.context.summary == summary
        assert newer.public()['status'] == 'restored'
    assert newer.path.read_bytes() == candidate
    assert newer.recovery_path.read_bytes() == retained
    assert raw.compactions == raw.sent == []
    if state != 'committed' or path == 'preserve':
        assert raw.counted == []
    else:
        assert raw.counted  # Ordinary fitting is not replay or another recovery.
    assert await second.context.get_messages() == source


@pytest.mark.parametrize('explicit_engine', [False, True])
async def test_live_plan_default_boundary_retains_native_checkpoint_across_reload(tmp_path, explicit_engine):
    from amplifier_web.host.session import live_plan
    from amplifier_core import ChatRequest, Message
    requested = {'module': 'context-managed', 'config': {
        'max_tokens': 20000, 'summarize_trigger': .2,
        # This checks checkpoint continuity, not archive selection/omission.
        'archive_recovery': False,
        **({'engine': 'boundary'} if explicit_engine else {})}}
    plan, _ = live_plan({'session': {'orchestrator': 'loop-live', 'context': requested}})
    config = plan['session']['context']['config']
    original = native_history()
    original[1]['content'] = 'FILLER:' + 'research ' * 12000

    c, raw, _, adapter, checkpoint, transcript, prepare = await native_fixture(
        tmp_path, original, context_config=config)
    raw_limit = raw.input_limit
    # Canonical history is larger than the continuation budget, not merely
    # above a trigger. The fake native compactor accepts a larger source window.
    base_budget = raw.request_budget(ChatRequest(messages=[Message(**row) for row in original]))
    assert base_budget['estimated_input_tokens'] > raw_limit
    raw.input_limit = 50000
    result = await prepare()
    assert result['final_attempt']['budget_decision']['estimated_input_tokens'] < raw_limit
    await checkpoint()
    assert adapter.path.is_file(), 'Default Boundary must persist the accepted native checkpoint'
    first = json.loads(adapter.path.read_text())
    saved_bytes = adapter.path.read_bytes()
    assert first['summary']['kind'] == 'native'
    assert json.loads(transcript.read_text()) == original
    assert raw.sent == [] and len(raw.compactions) == 1

    # A fresh context/host adapter receives the same originals but cannot
    # compact this prefix again. Only restoring the accepted checkpoint works.
    next_raw = SyntheticNativeProvider()
    next_raw.nonreducing = True
    newer, next_raw, _, restored, _, _, next_prepare = await native_fixture(
        tmp_path, original, provider=next_raw, context_config=config)
    restored.save()
    assert restored.path.read_bytes() == saved_bytes
    fitted = await next_prepare()
    assert restored.path.read_bytes() == saved_bytes
    assert restored.public()['status'] == 'restored'
    assert next_raw.compactions == next_raw.sent == []
    assert next_raw.counted, 'Restoration still counts; it is not zero provider calls'
    assert fitted['final_attempt']['budget_decision']['estimated_input_tokens'] < raw_limit
    assert await newer.context.get_messages() == original

    next_raw.nonreducing = False
    for row in [
        {'role': 'assistant', 'content': 'FILLER:' + 'new research ' * 7000},
        {'role': 'user', 'content': 'Correction: keep report-v3 too.'},
        {'role': 'assistant', 'content': 'Acknowledged.'},
        {'role': 'user', 'content': 'Continue without replay.'},
    ]:
        await newer.context.add_message(row)
    next_raw.input_limit = 50000
    second_view = await next_prepare()
    restored.save()
    second = json.loads(restored.path.read_text())
    assert second['summary']['throughMessage'] > first['summary']['throughMessage']
    assert second_view['final_attempt']['budget_decision']['estimated_input_tokens'] < raw_limit
    assert len(next_raw.compactions) == 1 and next_raw.sent == []
    text = json.dumps(next_raw.expanded(second_view['final_attempt']['dispatch']))
    for required in ('Build report; keep originals.', 'report-v1; effect completed once.',
                     'Correction: use report-v2.', 'Correction: keep report-v3 too.',
                     'Accepted instruction:', 'Accepted reminder:', 'uncertain-op'):
        assert required in text
    assert (await newer.context.get_messages())[:len(original)] == original
    print(json.dumps({'default_engine': not explicit_engine,
        'synthetic_canonical_tokens': base_budget['estimated_input_tokens'],
        'continuation_limit': raw_limit,
        'first_fitted': result['final_attempt']['budget_decision']['estimated_input_tokens'],
        'restored_fitted': fitted['final_attempt']['budget_decision']['estimated_input_tokens'],
        'second_fitted': second_view['final_attempt']['budget_decision']['estimated_input_tokens'],
        'covered_messages': [first['summary']['throughMessage'], second['summary']['throughMessage']]}))
