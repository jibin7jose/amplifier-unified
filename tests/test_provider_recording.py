import copy
import asyncio
import json
from types import SimpleNamespace

import pytest

from amplifier_web.provider_recording import (
    RAW_MODULES, apply_provider_recording, install_request_redaction,
    recording_enabled, redact_request,
)


def test_app_owned_opt_in_is_strict_and_defaults_off(tmp_path):
    assert not recording_enabled(tmp_path)
    path = tmp_path / 'diagnostics' / 'config.json'
    path.parent.mkdir()
    for value in ('invalid', '[]', '{"providerRequests":"true"}', '{"enabled":true}'):
        path.write_text(value)
        assert not recording_enabled(tmp_path)
    path.write_text('{"providerRequests":true}')
    assert recording_enabled(tmp_path)


def test_every_catalog_adapter_defaults_and_nested_opt_out_are_preserved():
    plan = {'providers': [{'module': name, 'instance_id': str(i)} for i, name in enumerate(sorted(RAW_MODULES))],
            'agents': {'child': {'providers': [
                {'module': 'provider-openai', 'id': 'private', 'config': {'raw': False}},
                {'module': 'provider-anthropic', 'config': {'raw': 'false'}},
                {'module': 'provider-litellm'}, {'module': 'provider-mock'},
                {'module': 'provider-custom', 'config': {'debug': True}},
                {'module': 'provider-openai', 'enabled': False},
            ], 'agents': {'grandchild': {'providers': [{'module': 'provider-openai-chatgpt'}]}}}}}
    initial = copy.deepcopy(plan)
    assert apply_provider_recording(plan, enabled=False) == initial
    apply_provider_recording(plan, enabled=True)
    assert all(row['config']['raw'] is True for row in plan['providers'])
    child = plan['agents']['child']
    assert child['providers'][0]['config']['raw'] is False
    assert child['providers'][1]['config']['raw'] == 'false'
    assert child['providers'][2]['config'] == {'raw_debug': True}
    assert 'config' not in child['providers'][3]  # Mock emits counts, not a real request.
    assert child['providers'][4]['config'] == {'debug': True}  # Do not invent custom contracts.
    assert 'config' not in child['providers'][5]
    assert child['agents']['grandchild']['providers'][0]['config'] == {'raw': True}


def test_litellm_explicit_opt_out_and_no_blanket_debug():
    plan = {'providers': [{'module': 'provider-litellm', 'config': {'raw_debug': False}},
                          {'module': 'provider-openai', 'config': {'debug': False}}]}
    apply_provider_recording(plan, enabled=True)
    assert plan['providers'][0]['config']['raw_debug'] is False
    assert plan['providers'][1]['config'] == {'debug': False, 'raw': True}


def test_litellm_canonical_raw_choice_is_not_overridden_by_legacy_alias():
    plan = {'providers': [
        {'module': 'provider-litellm', 'config': {'raw': False}},
        {'module': 'provider-litellm', 'config': {'raw': True}},
    ]}
    initial = copy.deepcopy(plan)
    apply_provider_recording(plan, enabled=True)
    assert plan == initial
    apply_provider_recording(plan, enabled=False)
    assert plan == initial


def test_host_defaults_can_be_disabled_after_a_foundation_snapshot_round_trip():
    from amplifier_foundation import Bundle
    plan = {'providers': [{'module': 'provider-openai'},
                          {'module': 'provider-anthropic', 'config': {'raw': True}}],
            'agents': {'nested': {'providers': [{'module': 'provider-litellm'}]}}}
    apply_provider_recording(plan, enabled=True)
    # Use Foundation's real public bundle conversion used by saved snapshots.
    retained = Bundle.from_dict({'bundle': {'name': 'fixture'}, **plan}).to_mount_plan()
    apply_provider_recording(retained, enabled=False)
    assert 'raw' not in retained['providers'][0]['config']
    assert retained['providers'][1]['config']['raw'] is True
    assert 'raw_debug' not in retained['agents']['nested']['providers'][0]['config']
    apply_provider_recording(retained, enabled=True)
    retained['providers'][0]['config']['raw'] = False  # Explicit override after capture.
    apply_provider_recording(retained, enabled=True)
    assert retained['providers'][0]['config']['raw'] is False


def test_capture_scrubs_nested_and_known_credentials_without_mutating_request(monkeypatch):
    monkeypatch.setenv('SYNTHETIC_API_KEY', 'fixture-secret-abcdefgh')
    original = {'messages': [{'content': 'prefix fixture-secret-abcdefgh'}],
                'extra_headers': {'Authorization': 'Bearer secret', 'X-Api-Key': 'key'},
                'input': 'api_key="private-value"', 'url': 'https://owner:secret@example.invalid/' }
    before = copy.deepcopy(original)
    safe, truncated = redact_request(original)
    assert not truncated and original == before
    serialized = json.dumps(safe)
    assert 'fixture-secret-abcdefgh' not in serialized
    assert 'private-value' not in serialized and 'owner:secret' not in serialized
    assert safe['extra_headers'] == {'Authorization': '[REDACTED]', 'X-Api-Key': '[REDACTED]'}
    assert safe['messages'][0]['content'] == 'prefix [REDACTED]'


@pytest.mark.parametrize('rows', [1, 1024])
def test_capture_reads_environment_once_and_refreshes_secrets_between_captures(monkeypatch, rows):
    from amplifier_web import worker_diagnostics

    monkeypatch.setenv('SYNTHETIC_API_KEY', 'first-fixture-credential')
    reads = []
    original_items = worker_diagnostics.os.environ.items
    def environment_items():
        reads.append(1)
        return original_items()
    monkeypatch.setattr(worker_diagnostics.os.environ, 'items', environment_items)

    for index, secret in enumerate(('first-fixture-credential', 'rotated-fixture-credential'), 1):
        monkeypatch.setenv('SYNTHETIC_API_KEY', secret)
        payload = [{'label ' + secret: 'value ' + secret} for _ in range(rows)]
        safe, truncated = redact_request(payload)
        assert not truncated
        assert safe == [{'label [REDACTED]': 'value [REDACTED]'}] * rows
        assert payload == [{'label ' + secret: 'value ' + secret}] * rows
        assert len(reads) == index, 'Environment work must not grow with request fields'


def test_capture_scrubs_generic_token_fields_in_structured_and_litellm_string_forms():
    original = {'extra_headers':{'Authorization':'Bearer opaque-bearer',
        'Cookie':'sid=opaque-cookie', 'token':'opaque-token', 'x-auth-token':'opaque-auth'},
        'input_tokens':123, 'max_output_tokens':456, 'token_file':'/private/oauth/tokens.json'}
    safe, _ = redact_request(original)
    assert all(value == '[REDACTED]' for value in safe['extra_headers'].values())
    assert safe['input_tokens'] == 123 and safe['max_output_tokens'] == 456
    assert safe['token_file'] == original['token_file']
    serialized, _ = redact_request(json.dumps(original))
    assert 'opaque-' not in serialized
    assert '"input_tokens": 123' in serialized and '"max_output_tokens": 456' in serialized
    assert '/private/oauth/tokens.json' in serialized


def test_child_explicit_true_survives_parent_default_and_later_disabling():
    from amplifier_web.host.children import child_plan
    parent = {'providers':[{'module':'provider-openai','id':'one'}]}
    apply_provider_recording(parent, enabled=True)
    child = child_plan(parent, {'providers':[
        {'module':'provider-openai','id':'one','config':{'raw':True}}]})
    apply_provider_recording(child, enabled=False)
    assert child['providers'][0]['config']['raw'] is True
    inherited = child_plan(parent, {})
    apply_provider_recording(inherited, enabled=False)
    assert 'raw' not in inherited['providers'][0]['config']
    assert parent['providers'][0]['config']['raw'] is True


@pytest.mark.parametrize('selection, expected', [('none', []), (['named'], ['named'])])
def test_recording_defaults_do_not_change_child_agent_inheritance_filters(selection, expected):
    from amplifier_web.host.children import child_plan
    parent = {'providers':[{'module':'provider-openai'}], 'agents':{'named':{},'other':{}}}
    apply_provider_recording(parent, enabled=True)
    result = child_plan(parent, {'agents':selection})
    assert list(result['agents']) == expected


def test_capture_bounds_wide_deep_and_large_payloads():
    for original in ({'input': 'huge' * 50000}, [{'input': 'data'}] * 10000):
        safe, truncated = redact_request(original, limit=200)
        assert truncated
        assert len(json.dumps(safe)) < 800
    deep = {}
    for _ in range(40):
        deep = {'nested': deep}
    assert redact_request(deep)[1] is True


async def test_real_kernel_pipeline_redacts_before_capture_and_keeps_request_unchanged():
    from amplifier_core import HookRegistry, HookResult
    hooks = HookRegistry()
    capabilities, records = {}, []
    coordinator = SimpleNamespace(hooks=hooks, get_capability=capabilities.get,
        register_capability=lambda key, value: capabilities.update({key: value}))
    async def capture(event, data):
        records.append(data)
        return HookResult()
    hooks.register('llm:request', capture, priority=100)
    hooks.register('llm:response', capture, priority=100)
    install_request_redaction(coordinator)
    install_request_redaction(coordinator)
    raw = {'model': 'fixture', 'messages': ['real emitted body'], 'api_key': 'must-not-store'}
    await hooks.emit('llm:request', {'provider': 'test', 'raw': raw})
    assert len(records) == 1
    assert records[0]['raw']['api_key'] == '[REDACTED]'
    assert records[0]['raw']['messages'] == ['real emitted body']
    assert records[0]['request_capture']['redacted'] is True
    assert raw['api_key'] == 'must-not-store'
    await hooks.emit('llm:request', {'provider': 'test', 'message_count': 1})
    assert 'raw' not in records[-1] and 'raw_request' not in records[-1]
    await hooks.emit('llm:response', {'raw': {'api_key': 'also-private'}})
    assert records[-1]['raw']['api_key'] == '[REDACTED]'


async def test_canonical_capture_gets_exact_wrapped_call_identity_for_stream_retry_and_child():
    from amplifier_core import HookRegistry, HookResult
    from amplifier_web.execution_events import ExecutionEvents, CURRENT_CALL
    hooks, capabilities, records = HookRegistry(), {}, []
    events = ExecutionEvents('root', lambda value: None)
    coordinator = SimpleNamespace(hooks=hooks, get_capability=capabilities.get,
        register_capability=lambda key, value: capabilities.update({key: value}))
    async def capture(event, data):
        records.append((event, data))
        return HookResult()
    for event in ('llm:request', 'llm:response'):
        hooks.register(event, capture, priority=100)
    install_request_redaction(coordinator)
    async def emit(event, sid, attempt):
        # Core can dispatch hooks in a new task; the exact call context must
        # reach the redactor before canonical capture in that task too.
        await asyncio.create_task(hooks.emit(event, {'session_id': sid,
            'request_id': attempt, 'unified_call_id': 'forged', 'raw': {'text': event}}))
    class Provider:
        def __init__(self, sid): self.sid = sid
        def get_info(self): return {'id': 'test', 'defaults': {'model': 'fixture'}}
        async def complete(self, request):
            for attempt in ('first', 'retry'):
                await emit('llm:request', self.sid, attempt)
                await emit('llm:response', self.sid, attempt)
            return {'usage': {'input_tokens': 3, 'output_tokens': 2}}
        async def stream(self, request):
            await emit('llm:request', self.sid, 'stream')
            yield {'text': 'visible'}
            await emit('llm:response', self.sid, 'stream')
    async def streamed():
        provider = events.instrument_provider('child', Provider('child'))
        async for _ in provider.stream(SimpleNamespace(model='fixture')):
            assert CURRENT_CALL.get() is None, 'Provider context must not leak across yielded chunks'
    root = events.instrument_provider('root', Provider('root'))
    await asyncio.gather(root.complete(SimpleNamespace(model='fixture')), streamed())
    by_session = {row['sessionId']: row['id'] for row in events.nodes.values()}
    assert len(records) == 6 and len(set(by_session.values())) == 2
    assert all(data['unified_call_id'] == by_session[data['session_id']] for _, data in records)
    assert events.usage()['usage']['totalTokens'] == 5
    for payload in ({'unified_call_id': 'forged'}, {'unified_call_id': 'forged', 'raw': 'text'}):
        await hooks.emit('llm:request', payload)
        assert 'unified_call_id' not in records[-1][1]
        assert payload['unified_call_id'] == 'forged', 'Never mutate the provider input'


async def test_failed_redaction_does_not_forward_unsanitized_payload(monkeypatch):
    from amplifier_core import HookRegistry, HookResult
    hooks, capabilities, records = HookRegistry(), {}, []
    coordinator = SimpleNamespace(hooks=hooks, get_capability=capabilities.get,
        register_capability=lambda key, value: capabilities.update({key: value}))
    async def capture(event, data):
        records.append(data)
        return HookResult()
    hooks.register('llm:request', capture, priority=100)
    install_request_redaction(coordinator)
    def fail(value):
        raise ValueError('redaction failed')
    monkeypatch.setattr('amplifier_web.provider_recording.redact_request', fail)
    await hooks.emit('llm:request', {'raw': {'api_key': 'never-forward'}})
    assert records[0]['raw'] == '[capture unavailable]'
    assert records[0]['request_capture']['truncated'] is True


def test_diagnostic_configuration_is_a_worker_mount_input(tmp_path, monkeypatch):
    from amplifier_web.shared_state import configuration_paths
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'shared'))
    path = tmp_path / 'app' / 'diagnostics' / 'config.json'
    assert path in configuration_paths(tmp_path, 'session', tmp_path / 'app')
