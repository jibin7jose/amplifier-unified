"""Host identity, private durability, and shared action acceptance."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from amplifier_web.native_provider import NativeProviderHost, install_native
from amplifier_web.runtime_controls import RuntimeControls
from amplifier_web.runtime import normalize_event


class Coordinator:
    session_id='native-test'
    session_state={}
    config={'providers':[]}
    def __init__(self, provider, *, enabled=True):
        self.providers={'configured':provider}
        self.loop=SimpleNamespace(config={'native_provider':enabled},root_provider=None,jobs={})
        self.loop._select_provider=lambda providers:self.loop.root_provider or providers['configured']
        self.messages=[{'role':'user','content':'original'}]
        self.context=SimpleNamespace(get_messages=self.get_messages)
        self.caps={'live.runtime':SimpleNamespace(generation={'id':'generation-1'})}
        from amplifier_web.execution_events import ExecutionEvents
        self.telemetry = ExecutionEvents(self.session_id, lambda event: None)
        self.caps['web.provider_call'] = lambda provider, request, invoke, **kwargs: self.telemetry.provider_call(self.session_id, provider, request, invoke, **kwargs)
    async def get_messages(self):return copy.deepcopy(self.messages)
    def get(self,key):return {'providers':self.providers,'orchestrator':self.loop,'context':self.context}.get(key)
    def get_capability(self,key):return self.caps.get(key)
    def register_capability(self,key,value):self.caps[key]=value
    async def mount(self,section,value,name):self.providers[name]=value


def fake():
    return SimpleNamespace(get_info=lambda:SimpleNamespace(defaults={'model':'fixture'}))


@pytest.mark.asyncio
async def test_disabled_default_does_not_load_or_replace_provider(tmp_path,monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME',str(tmp_path))
    c=Coordinator(fake(),enabled=False)
    original=c.providers['configured']
    await install_native(c.loop,c,c.providers)
    status=c.get_capability('web.native_provider').status()
    assert not status['supported'] and status['steering']=='request_boundary'
    assert c.providers['configured'] is original and not list(tmp_path.rglob('*.json'))


@pytest.mark.asyncio
async def test_supported_instance_wrap_is_opt_in_and_preserves_root_selection(tmp_path,monkeypatch):
    pytest.importorskip("amplifier_module_provider_openai.native")
    OpenAIProvider = pytest.importorskip("amplifier_module_provider_openai").OpenAIProvider
    from amplifier_web.host.session import SelectedProvider
    monkeypatch.setenv('AMPLIFIER_WEB_HOME',str(tmp_path))
    provider=OpenAIProvider(api_key='fixture',config={'default_model':'gpt-6-astra'})
    c=Coordinator(provider)
    selection={'instance':'configured','model':'gpt-6-astra','effort':'high'}
    c.loop.root_provider=SelectedProvider(provider,copy.deepcopy(selection))
    await install_native(c.loop,c,c.providers)
    host=c.get_capability('web.native_provider')
    assert host.status()['supported']
    from amplifier_module_loop_live.scope import LIVE_OWNER
    token=LIVE_OWNER.set(SimpleNamespace(coordinator=object()))
    try:assert host.provider.owner_getter() is None
    finally:LIVE_OWNER.reset(token)
    owned=SimpleNamespace(coordinator=c)
    token=LIVE_OWNER.set(owned)
    try:assert host.provider.owner_getter() is owned
    finally:LIVE_OWNER.reset(token)
    assert c.loop.root_provider.original is host.provider
    assert c.loop.root_provider.selection==selection
    assert host.provider._api_key=='fixture' and provider._client is None
    # Transparent surface/observation wrappers preserve the selected instance.
    from amplifier_web.surface_delivery import SurfaceProvider
    c.loop.root_provider=SurfaceProvider(c.loop.root_provider,SimpleNamespace())
    assert host.status()['supported'] and host.selected_mount()[1] is host.provider
    # An explicit model/effort change must not use the previous identity's state.
    c.loop.root_provider.selection['effort']='low'
    assert host.status()['steering']=='request_boundary'
    with pytest.raises(ValueError):await host.compact()


@pytest.mark.asyncio
@pytest.mark.parametrize('model,endpoint',[('gpt-5.6-terra',None),('gpt-6-astra','https://proxy.example/v1')])
async def test_unsupported_selected_model_or_endpoint_falls_back_without_switch(tmp_path,monkeypatch,model,endpoint):
    OpenAIProvider = pytest.importorskip("amplifier_module_provider_openai").OpenAIProvider
    monkeypatch.setenv('AMPLIFIER_WEB_HOME',str(tmp_path))
    p=OpenAIProvider(api_key='fixture',config={'default_model':model,**({'base_url':endpoint} if endpoint else {})})
    c=Coordinator(p)
    await install_native(c.loop,c,c.providers)
    assert c.providers['configured'] is p and p._client is None
    assert c.get_capability('web.native_provider').status()['steering']=='request_boundary'


@pytest.mark.parametrize("model", [
    "gpt-6-astra", "gpt-6-sol", "gpt-6-luna", "gpt-6.1-sol", "gpt-6.1-terra",
    "gpt-5.6", "gpt-5.6-terra", "gpt-6", "gpt-6.1", "gpt-6.1-terra-preview",
    "gpt-6-astra-2026-11-06", "GPT-6-astra", "", None, 6, [],
])
async def test_native_admission_uses_provider_exact_policy(tmp_path, monkeypatch, model):
    from amplifier_module_provider_openai import OpenAIProvider
    from amplifier_module_provider_openai._capabilities import get_request_policy
    from amplifier_web.host.session import SelectedProvider
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path))
    original = OpenAIProvider(api_key="fixture", config={"default_model": "gpt-6-astra"})
    c = Coordinator(original)
    c.loop.root_provider = SelectedProvider(original, {"instance": "configured", "model": model})
    await install_native(c.loop, c, c.providers)
    host = c.get_capability("web.native_provider")
    assert host.status()["supported"] is get_request_policy(model).supports_steering
    assert original._client is None and not host.path.exists()


@pytest.mark.parametrize("endpoint", [
    "https://api.openai.com/v1", "https://api.openai.com/v1/",
    "http://api.openai.com/v1", "https://proxy.example/v1",
    "https://deployment.openai.azure.com/openai/v1", "https://api.openai.com:443/v1",
    "https://user:password@api.openai.com/v1", "https://api.openai.com/v1?api-version=1",
    "https://api.openai.com/v1#fragment", "https://api.openai.com/v1;custom", "https://[broken",
])
async def test_only_official_endpoint_is_admitted(tmp_path, monkeypatch, endpoint):
    from amplifier_module_provider_openai import OpenAIProvider
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path))
    original = OpenAIProvider(api_key="fixture", config={"default_model": "gpt-6.1-terra"})
    original.base_url = endpoint
    c = Coordinator(original)
    await install_native(c.loop, c, c.providers)
    host = c.get_capability("web.native_provider")
    assert host.status()["supported"] is (endpoint in {
        "https://api.openai.com/v1", "https://api.openai.com/v1/"})
    assert original._client is None


@pytest.mark.parametrize("source", [
    "environment", "configured", "configured_url", "configured_sdk_url", "injected", "injected_url",
])
@pytest.mark.parametrize("endpoint", ["https://api.openai.com/v1", "https://proxy.invalid/v1"])
async def test_effective_sdk_endpoint_admission_and_ordinary_fallback(tmp_path, monkeypatch, source, endpoint):
    import socket
    import httpx
    from openai import AsyncOpenAI
    from openai._base_client import URL as SDKURL
    from amplifier_core.message_models import ChatRequest, Message
    from amplifier_module_provider_openai import OpenAIProvider
    from amplifier_module_loop_live.scope import LIVE_OWNER
    from amplifier_web.host.session import SelectedProvider

    async def deny_network(*args, **kwargs):
        raise AssertionError("Endpoint qualification must not use the network")
    def deny_socket(*args, **kwargs):
        raise AssertionError("Endpoint qualification must not use the network")
    monkeypatch.setattr(socket.socket, "connect", deny_socket)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", deny_network)
    monkeypatch.setattr("websockets.connect", deny_network)
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path))
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    requests = []
    def transport(request):
        body = json.loads(request.content)
        requests.append((request.url, body))
        assert str(request.url).startswith(endpoint + "/")
        if request.url.path.endswith("/input_tokens"):
            return httpx.Response(200, json={"input_tokens": 5})
        assert request.url.path == "/v1/responses"
        return httpx.Response(200, json={
            "id": "ordinary", "object": "response", "created_at": 1,
            "model": "gpt-6.1-terra", "status": "completed",
            "output": [{"type": "message", "id": "message-ordinary", "role": "assistant",
                        "content": [{"type": "output_text", "text": "Ordinary works.", "annotations": []}]}],
            "usage": {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7}})
    config = {"default_model": "gpt-6.1-terra", "use_streaming": False, "max_retries": 0}
    client = None
    if source == "environment":
        monkeypatch.setenv("OPENAI_BASE_URL", endpoint)
    elif source.startswith("configured"):
        # Explicit configuration wins over a conflicting environment.
        monkeypatch.setenv("OPENAI_BASE_URL", "https://environment.invalid/v1")
        config["base_url"] = (SDKURL(endpoint) if source == "configured_sdk_url"
                              else httpx.URL(endpoint) if source.endswith("_url") else endpoint)
    else:
        # An existing SDK client wins over both config and environment.
        conflicting = ("https://configured.invalid/v1" if endpoint == "https://api.openai.com/v1"
                       else "https://api.openai.com/v1")
        monkeypatch.setenv("OPENAI_BASE_URL", conflicting)
        config["base_url"] = conflicting
        client = AsyncOpenAI(api_key="fixture", max_retries=0,
            base_url=httpx.URL(endpoint) if source.endswith("_url") else endpoint,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)))
    original = OpenAIProvider(api_key="fixture", client=client, config=config)
    c = Coordinator(original)
    selected = SelectedProvider(original, {"instance": "configured", "model": "gpt-6.1-terra"})
    c.loop.root_provider = selected
    # Lazy SDK construction is observed only if a real ordinary completion runs,
    # never during install/status. Retain the real constructor and serialization.
    builds = []
    def sdk(**kwargs):
        builds.append(kwargs)
        return AsyncOpenAI(**{**kwargs, "http_client": httpx.AsyncClient(
            transport=httpx.MockTransport(transport))})
    monkeypatch.setattr("amplifier_module_provider_openai.AsyncOpenAI", sdk)
    try:
        await install_native(c.loop, c, c.providers)
        host = c.get_capability("web.native_provider")
        official = endpoint == "https://api.openai.com/v1"
        for _ in range(2):
            assert host.status()["supported"] is official
            assert host.status()["steering"] == ("native" if official else "request_boundary")
        assert original._client is client and not builds and not requests
        if official:
            assert selected.original is host.provider
        else:
            assert c.providers["configured"] is selected.original is original
            token = LIVE_OWNER.set(SimpleNamespace(coordinator=c, context=c.context, config={}))
            try:
                reply = await selected.complete(ChatRequest(
                    messages=[Message(role="user", content="Synthetic ordinary input")], max_output_tokens=16))
            finally:
                LIVE_OWNER.reset(token)
            assert reply.content[0].text == "Ordinary works."
            assert len([body for url, body in requests if url.path == "/v1/responses"]) == 1
            assert len(builds) == (client is None)
            assert host.provider is None and not host.path.exists()
    finally:
        actual_client = (c.get_capability("web.native_provider").provider or original)._client
        if actual_client is not None:
            await actual_client.close()


@pytest.mark.parametrize("value", [
    "https://api.openai.com/v1", "https://api.openai.com/v1/",
    "http://api.openai.com/v1", "https://proxy.invalid/v1",
    "https://api.openai.com:444/v1", "https://user:pass@api.openai.com/v1",
    "https://api.openai.com/v1?key=value", "https://api.openai.com/v1#fragment",
    "https://api.openai.com/v1;parameters",
])
@pytest.mark.parametrize("source", ["environment", "configured_url", "client"])
async def test_effective_endpoint_sources_keep_strict_url_checks(monkeypatch, value, source):
    import httpx
    from openai import AsyncOpenAI
    from amplifier_web.native_provider import _official_endpoint
    provider = SimpleNamespace(base_url=None, _client=None)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    if source == "environment":
        monkeypatch.setenv("OPENAI_BASE_URL", value)
    elif source == "configured_url":
        provider.base_url = httpx.URL(value)
    else:
        # SDK base_url is an httpx.URL. Constructing a client makes no requests.
        provider._client = AsyncOpenAI(api_key="fixture", base_url=value)
    try:
        assert _official_endpoint(provider) is (value in {
            "https://api.openai.com/v1", "https://api.openai.com/v1/"})
    finally:
        if provider._client is not None:
            await provider._client.close()


def test_endpoint_status_never_stringifies_unknown_objects_or_uses_missing_client_url(monkeypatch):
    from amplifier_web.native_provider import _official_endpoint
    class PretendURL:
        def __str__(self):
            raise AssertionError("Unknown endpoint types must not be stringified")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    for provider in [
        SimpleNamespace(base_url=PretendURL(), _client=None),
        SimpleNamespace(base_url="https://api.openai.com/v1", _client=SimpleNamespace()),
        SimpleNamespace(base_url=None, _client=SimpleNamespace(base_url=PretendURL())),
        SimpleNamespace(base_url="", _client=None),
    ]:
        assert not _official_endpoint(provider)
    monkeypatch.delenv("OPENAI_BASE_URL")
    assert _official_endpoint(SimpleNamespace(base_url=None, _client=None))


@pytest.mark.parametrize("extra,final", [
    ({}, "gpt-6-sol"),
    ({"model": "gpt-6.1-terra"}, "gpt-6.1-terra"),
    ({"model": "gpt-5.6", "extra_body": {"model": "gpt-6-luna"}}, "gpt-6-luna"),
    ({"model": "gpt-6-astra", "extra_body": {"model": "gpt-5.6"}}, "gpt-5.6"),
    ({"extra_body": {"model": ["gpt-6-astra"]}}, None),
    ({"extra_body": "not an object"}, None),
])
async def test_status_uses_pure_provider_final_precedence(tmp_path, monkeypatch, extra, final):
    from amplifier_module_provider_openai import OpenAIProvider
    from amplifier_module_provider_openai._capabilities import get_request_policy
    from amplifier_web.host.session import SelectedProvider
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path))
    original = OpenAIProvider(api_key="fixture", config={
        "default_model": "gpt-5.6", "extra_request_params": extra})
    c = Coordinator(original)
    selection = {"instance": "configured", "model": "gpt-6-sol"}
    c.loop.root_provider = SelectedProvider(original, copy.deepcopy(selection))
    before = copy.deepcopy(original._extra_params_warned_keys)
    await install_native(c.loop, c, c.providers)
    host = c.get_capability("web.native_provider")
    status = host.status()
    assert status["model"] == final
    assert status["supported"] is get_request_policy(final).supports_steering
    assert c.loop.root_provider.selection == selection
    assert original.default_model == "gpt-5.6"
    assert original._extra_params_warned_keys == before and original._client is None
    if host.provider:
        c.providers["configured"] = OpenAIProvider(api_key="fixture", config={
            "default_model": "gpt-5.6", "extra_request_params": extra})
        assert host.status()["steering"] == "request_boundary"  # Same name/model is not the same mount.


async def test_provider_subclass_and_prewrapped_instance_are_not_native(tmp_path, monkeypatch):
    from amplifier_module_provider_openai import OpenAIProvider
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path))
    class AzureLikeProvider(OpenAIProvider):
        pass
    for cls, wrapped in [(AzureLikeProvider, False), (OpenAIProvider, True)]:
        original = cls(api_key="fixture", config={"default_model": "gpt-6.1-terra"})
        if wrapped:
            original.complete = AsyncMock()
        c = Coordinator(original)
        await install_native(c.loop, c, c.providers)
        assert c.get_capability("web.native_provider").provider is None
        assert c.providers["configured"] is original and original._client is None


@pytest.mark.asyncio
async def test_durable_receipts_reconcile_restart_unknown_without_replay(tmp_path):
    c=Coordinator(fake())
    host=NativeProviderHost(c,c.loop,tmp_path)
    host.identity=host.selected_identity()
    await host.lifecycle('native.submitting',{'input':'private text excluded'})
    await host.lifecycle('steering.accepted',{'input_id':'input-2','steer_id':'s'})
    saved=host.path.read_text()
    assert 'private text' not in saved
    restored=NativeProviderHost(c,c.loop,tmp_path)
    assert restored.status()['receipt']['outcome']=='unknown'
    assert restored.state['generationId']=='generation-1'
    assert restored.state['receipts'][-1]['input_id']=='input-2'
    assert c.messages==[{'role':'user','content':'original'}]
    # A recorded final receipt remains final on restart.
    restored.identity=restored.selected_identity()
    await restored.lifecycle('native.completed',{'response_id':'r','pending_steering':False})
    assert NativeProviderHost(c,c.loop,tmp_path).state['outcome']=='completed'


def send_record(operation='response.create', **changes):
    return {'send_id': 'send-one', 'operation': operation, 'epoch': 'connection-one',
            'response_id': 'parent' if operation == 'response.steer' else None,
            'input_id': 'input-one' if operation == 'response.steer' else None,
            'attempt_id': None, 'execution_replayed': False, **changes}


@pytest.mark.parametrize('operation', ['response.create', 'response.steer'])
@pytest.mark.parametrize('reason', ['cancelled', 'rejected'])
async def test_bound_unsent_receipt_preserves_original_owner_and_parent(tmp_path, operation, reason):
    c = Coordinator(fake())
    host = NativeProviderHost(c, c.loop, tmp_path)
    host.identity = host.selected_identity()
    host.state.update(requestId='parent-attempt', outcome='running', responseId='parent')
    callback = host.bind_send_lifecycle(
        attempt_id='original-attempt', generation_id='generation-1',
        admitted_call_id='original-budget-call')
    record = send_record(operation)
    await callback('native.submitting', record)
    parent = copy.deepcopy({k: v for k, v in host.state.items() if k not in {'sends', 'receipts', 'updatedAt'}})
    # A late terminal belongs to the captured owner, not a new generation.
    c.caps['live.runtime'].generation = {'id': 'new-generation'}
    await callback('native.not_sent', {**record, 'attempt_id': 'original-attempt',
                                     'reason': reason, 'input': 'PRIVATE INPUT'})
    row = host.state['sends'][0]
    assert row['outcome'] == 'not_sent' and row['reason'] == reason
    assert row['attemptId'] == 'original-attempt'
    assert row['generationId'] == 'generation-1'
    assert row['admittedCallId'] == 'original-budget-call'
    if operation == 'response.steer':
        assert {k: v for k, v in host.state.items() if k not in {'sends', 'receipts', 'updatedAt'}} == parent
    else:
        assert host.state['outcome'] == 'not_sent'
    restored = NativeProviderHost(c, c.loop, tmp_path)
    assert restored.state['sends'][0] == row
    assert 'PRIVATE INPUT' not in host.path.read_text()
    assert host.path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('fault', [
    {'epoch': 'other'}, {'operation': 'response.steer'}, {'response_id': 'other'},
    {'input_id': 'other'}, {'attempt_id': 'other'}, {'send_id': 'other'},
])
async def test_unsent_receipt_requires_exact_send_and_captured_binding(tmp_path, fault):
    c = Coordinator(fake())
    host = NativeProviderHost(c, c.loop, tmp_path)
    host.identity = host.selected_identity()
    callback = host.bind_send_lifecycle(
        attempt_id='original-attempt', generation_id='generation-1', admitted_call_id='budget-call')
    record = send_record()
    await callback('native.submitting', record)
    before = host.path.read_bytes()
    with pytest.raises(ValueError):
        await callback('native.not_sent', {**record, 'reason': 'rejected', **fault})
    assert host.path.read_bytes() == before
    assert NativeProviderHost(c, c.loop, tmp_path).state['sends'][0]['outcome'] == 'unknown'


@pytest.mark.parametrize('lost', ['ignored', 'failed'])
async def test_unsent_missing_durable_terminal_remains_unknown(tmp_path, monkeypatch, lost):
    c = Coordinator(fake())
    host = NativeProviderHost(c, c.loop, tmp_path)
    host.identity = host.selected_identity()
    callback = host.bind_send_lifecycle(
        attempt_id='attempt', generation_id='generation-1', admitted_call_id='budget-call')
    record = send_record()
    await callback('native.submitting', record)
    if lost == 'failed':
        def fail():
            raise OSError('Synthetic failed private persistence')
        monkeypatch.setattr(host, 'save', fail)
        with pytest.raises(OSError):
            await callback('native.not_sent', {**record, 'reason': 'rejected'})
        assert host.state['sends'][0]['outcome'] == 'submitting'
    # Deliberately ignored terminal is the original bug's negative control.
    restored = NativeProviderHost(c, c.loop, tmp_path)
    assert restored.state['sends'][0]['outcome'] == 'unknown'
    assert restored.state['outcome'] == 'unknown'


@pytest.mark.parametrize('parent_outcome', ['running', 'completed', 'unknown'])
async def test_unsent_steer_never_downgrades_parent_or_other_unknown_send(tmp_path, parent_outcome):
    c = Coordinator(fake())
    host = NativeProviderHost(c, c.loop, tmp_path)
    host.identity = host.selected_identity()
    first = host.bind_send_lifecycle(
        attempt_id='first', generation_id='generation-1', admitted_call_id='first-budget')
    await first('native.submitting', send_record(send_id='first-send'))
    host.state.update(outcome=parent_outcome, responseId='parent')
    second = host.bind_send_lifecycle(
        attempt_id='second', generation_id='generation-1', admitted_call_id='second-budget')
    steer = send_record('response.steer', send_id='second-send')
    await second('native.submitting', steer)
    await second('native.not_sent', {**steer, 'reason': 'cancelled'})
    assert host.state['outcome'] == parent_outcome
    assert host.state['requestId'] == 'first'
    restored = NativeProviderHost(c, c.loop, tmp_path)
    assert [row['outcome'] for row in restored.state['sends']] == ['unknown', 'not_sent']


async def test_bound_send_rejects_new_submission_after_generation_change(tmp_path):
    c = Coordinator(fake())
    host = NativeProviderHost(c, c.loop, tmp_path)
    host.identity = host.selected_identity()
    callback = host.bind_send_lifecycle(
        attempt_id='attempt', generation_id='generation-1', admitted_call_id='budget-call')
    c.caps['live.runtime'].generation = {'id': 'new-generation'}
    with pytest.raises(ValueError, match='binding'):
        await callback('native.submitting', send_record())
    assert not host.path.exists()


async def test_restart_after_send_before_response_is_unknown_and_blocks_transport(tmp_path, monkeypatch):
    from amplifier_module_provider_openai import OpenAIProvider

    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    c = Coordinator(OpenAIProvider(api_key='fixture', config={'default_model': 'gpt-6-astra'}))
    host = NativeProviderHost(c, c.loop, tmp_path / 'sessions' / c.session_id)
    host.identity = host.selected_identity()
    callback = host.bind_send_lifecycle(
        attempt_id='attempt', generation_id='generation-1', admitted_call_id='budget')
    await callback('native.submitting', send_record())
    await callback('native.request', {})
    assert host.state['outcome'] == 'sent'
    # Reinstall simulates a fresh host over exactly the same durable record.
    c.caps.pop('web.native_provider')
    await install_native(c.loop, c, c.providers)
    restored = c.get_capability('web.native_provider')
    assert restored.status()['receipt']['outcome'] == 'unknown'
    assert restored.state['sends'][0]['outcome'] == 'unknown'
    assert restored.provider.request_uncertain is True


@pytest.mark.parametrize('operation', ['response.create', 'response.steer'])
@pytest.mark.parametrize('ending', ['prepare_reject', 'final_reject', 'cancel', 'send_error', 'ignored_terminal'])
async def test_real_native_unsent_callback_reaches_bound_host_disk(tmp_path, monkeypatch, operation, ending):
    import asyncio
    from amplifier_core.llm_errors import LLMError
    from amplifier_module_provider_openai import OpenAIProvider
    from amplifier_module_provider_openai.native import NativeSendAdmission
    from websockets.protocol import State

    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    c = Coordinator(OpenAIProvider(api_key='fixture', config={'default_model': 'gpt-6-astra'}))
    await install_native(c.loop, c, c.providers)
    host = c.get_capability('web.native_provider')
    native = host.provider
    callback = host.bind_send_lifecycle(
        attempt_id='attempt', generation_id='generation-1', admitted_call_id='budget-call')
    observed = []

    async def lifecycle(kind, data):
        observed.append(kind)
        if not (kind == 'native.not_sent' and ending == 'ignored_terminal'):
            await callback(kind, data)

    def admit(envelope):
        assert envelope.attempt_id == 'attempt' and envelope.kind == operation
        assert 'PRIVATE' in envelope.payload
        return ending == 'send_error'

    async def prepare(kind, *, input_id):
        assert kind == operation
        if ending == 'prepare_reject':
            raise ValueError('PRIVATE callback failure')
        if ending == 'cancel':
            raise asyncio.CancelledError()
        return NativeSendAdmission(attempt_id='attempt', admit=admit)

    native.lifecycle = lifecycle
    native.prepare_send = prepare
    native.owner = SimpleNamespace(
        config={}, runtime=SimpleNamespace(emit=AsyncMock()), _text=lambda command: 'PRIVATE steer')
    native._connect = AsyncMock()
    socket = SimpleNamespace(state=State.OPEN, send=AsyncMock(side_effect=OSError('Synthetic send failure')),
                             close=AsyncMock(), recv=AsyncMock())
    native.socket = socket
    if operation == 'response.steer':
        native.response_id = 'parent'
        host.state.update(requestId='parent-attempt', outcome='running', responseId='parent')
    failure = asyncio.CancelledError if ending == 'cancel' else (
        OSError if ending == 'send_error' else LLMError)
    with pytest.raises(failure):
        if operation == 'response.create':
            await native._native_response({'input': [{'role': 'user', 'content': 'PRIVATE create'}]})
        else:
            # This earlier slice tests upstream receipt callbacks, not the new
            # host admission wrapper (covered separately through installation).
            from amplifier_module_provider_openai.native import NativeResponsesProvider
            await NativeResponsesProvider.steer_live(native, SimpleNamespace(id='input-one'))
    if ending == 'send_error':
        socket.send.assert_awaited_once()
        assert 'native.not_sent' not in observed
        if operation == 'response.steer':
            assert 'native.outcome_unknown' in observed
            assert native._observer_failure is None
            assert host.state['sends'][0]['outcome'] == 'unknown'
            assert host.state['sends'][0]['reason'] == 'steer_send'
    else:
        socket.send.assert_not_awaited()
        assert observed.count('native.not_sent') == 1
    restored = NativeProviderHost(c, c.loop, host.directory)
    expected = 'unknown' if ending in {'send_error', 'ignored_terminal'} else 'not_sent'
    assert restored.state['sends'][0]['outcome'] == expected
    assert restored.state['sends'][0]['admittedCallId'] == 'budget-call'
    assert restored.state['sends'][0]['attemptId'] == 'attempt'
    assert 'PRIVATE' not in host.path.read_text()
    if operation == 'response.steer' and ending not in {'send_error', 'ignored_terminal'}:
        assert host.state['outcome'] == 'running' and host.state['requestId'] == 'parent-attempt'


@pytest.mark.parametrize('phase', ['native.request', 'native.outcome_unknown', 'native.completed'])
async def test_known_entered_or_terminal_send_cannot_be_relabelled_unsent(tmp_path, phase):
    c = Coordinator(fake())
    host = NativeProviderHost(c, c.loop, tmp_path)
    host.identity = host.selected_identity()
    callback = host.bind_send_lifecycle(
        attempt_id='attempt', generation_id='generation-1', admitted_call_id='budget-call')
    record = send_record()
    await callback('native.submitting', record)
    if phase == 'native.completed':
        await callback('native.response.created', {'response_id': 'reply'})
    await callback(phase, {'response_id': 'reply'})
    before = host.path.read_bytes()
    with pytest.raises(ValueError):
        await callback('native.not_sent', {**record, 'reason': 'rejected'})
    assert host.path.read_bytes() == before


async def test_native_phase_cannot_cross_distinct_budget_bindings(tmp_path):
    c = Coordinator(fake())
    host = NativeProviderHost(c, c.loop, tmp_path)
    host.identity = host.selected_identity()
    first = host.bind_send_lifecycle(attempt_id='same-attempt', generation_id='generation-1',
                                     admitted_call_id='first-budget')
    second = host.bind_send_lifecycle(attempt_id='same-attempt', generation_id='generation-1',
                                      admitted_call_id='second-budget')
    await first('native.submitting', send_record(send_id='first-send'))
    await second('native.submitting', send_record(send_id='second-send'))
    await first('native.response.created', {'response_id': 'first-response'})
    await first('native.completed', {'response_id': 'first-response'})
    assert [row['outcome'] for row in host.state['sends']] == ['completed', 'submitting']
    assert host.state['outcome'] == 'submitting' and host.state['admittedCallId'] == 'second-budget'
    before = host.path.read_bytes()
    with pytest.raises(ValueError, match='already exists'):
        await second('native.submitting', send_record(send_id='third-send'))
    assert host.path.read_bytes() == before


@pytest.mark.parametrize('data', [{}, {'response_id': 'wrong'},
                                  {'response_id': 'right', 'pending_steering': 'false'}])
async def test_native_completion_requires_observed_response_and_typed_terminal(tmp_path, data):
    c = Coordinator(fake())
    host = NativeProviderHost(c, c.loop, tmp_path)
    host.identity = host.selected_identity()
    callback = host.bind_send_lifecycle(
        attempt_id='attempt', generation_id='generation-1', admitted_call_id='budget')
    await callback('native.submitting', send_record())
    await callback('native.response.created', {'response_id': 'right'})
    before = host.path.read_bytes()
    with pytest.raises(ValueError):
        await callback('native.completed', data)
    assert host.path.read_bytes() == before


@pytest.mark.parametrize('resolution', ['applied', 'failed', 'accepted_failed'])
async def test_sent_steering_resolution_survives_restart_without_changing_parent(tmp_path, resolution):
    c = Coordinator(fake())
    host = NativeProviderHost(c, c.loop, tmp_path)
    host.identity = host.selected_identity()
    host.state.update(requestId='parent-attempt', outcome='running', responseId='parent')
    callback = host.bind_send_lifecycle(
        attempt_id='steer-attempt', generation_id='generation-1', admitted_call_id='budget')
    record = send_record('response.steer')
    await callback('native.submitting', record)
    await callback('steering.sent', {'input_id': 'input-one', 'response_id': 'parent'})
    if resolution in {'applied', 'accepted_failed'}:
        await callback('steering.accepted', {
            'input_id': 'input-one', 'response_id': 'parent', 'steer_id': 'steer-one'})
    if resolution == 'applied':
        await callback('steering.applied', {
            'input_id': 'input-one', 'response_id': 'successor', 'steer_id': 'steer-one'})
    else:
        await callback('steering.failed', {'input_id': 'input-one'})
    assert host.state['outcome'] == 'running' and host.state['requestId'] == 'parent-attempt'
    expected = 'failed' if resolution == 'accepted_failed' else resolution
    assert NativeProviderHost(c, c.loop, tmp_path).state['sends'][0]['outcome'] == expected


async def test_completed_create_is_terminal_even_with_pending_steering(tmp_path):
    c = Coordinator(fake())
    host = NativeProviderHost(c, c.loop, tmp_path)
    host.identity = host.selected_identity()
    callback = host.bind_send_lifecycle(
        attempt_id='attempt', generation_id='generation-1', admitted_call_id='budget')
    await callback('native.submitting', send_record())
    await callback('native.response.created', {'response_id': 'parent'})
    await callback('native.completed', {'response_id': 'parent', 'pending_steering': True})
    assert host.state['outcome'] == 'pending'  # Logical continuation remains unresolved.
    assert host.state['sends'][0]['outcome'] == 'completed'  # This create did finish.
    restored = NativeProviderHost(c, c.loop, tmp_path)
    assert restored.state['outcome'] == 'unknown'
    assert restored.state['sends'][0]['outcome'] == 'completed'


@pytest.mark.parametrize('input_id', [None, 'input-one'])
async def test_parent_unknown_only_relabels_steer_when_its_input_was_sent(tmp_path, input_id):
    c = Coordinator(fake())
    host = NativeProviderHost(c, c.loop, tmp_path)
    host.identity = host.selected_identity()
    parent = host.bind_send_lifecycle(
        attempt_id='parent-attempt', generation_id='generation-1', admitted_call_id='parent-budget')
    steer = host.bind_send_lifecycle(
        attempt_id='steer-attempt', generation_id='generation-1', admitted_call_id='steer-budget')
    await parent('native.submitting', send_record(send_id='parent-send'))
    record = send_record('response.steer', send_id='steer-send')
    await steer('native.submitting', record)
    unknown = {'reason': 'steer_send' if input_id else 'connection_closed', 'input_id': input_id}
    await steer('native.outcome_unknown', unknown)
    await parent('native.outcome_unknown', unknown)
    assert host.state['sends'][0]['outcome'] == 'unknown'
    assert host.state['reason'] == unknown['reason']
    if input_id is None:
        assert host.state['sends'][1]['outcome'] == 'submitting'
        await steer('native.not_sent', {**record, 'reason': 'rejected'})
        assert host.state['sends'][1]['outcome'] == 'not_sent'
    else:
        assert host.state['sends'][1]['outcome'] == 'unknown'
        before = host.path.read_bytes()
        with pytest.raises(ValueError):
            await steer('native.not_sent', {**record, 'reason': 'rejected'})
        assert host.path.read_bytes() == before


@pytest.mark.parametrize('event', ['native.error', 'native.connection.renewed'])
async def test_unhandled_observation_is_not_receipted_as_a_bound_send(tmp_path, event):
    c = Coordinator(fake())
    host = NativeProviderHost(c, c.loop, tmp_path)
    host.identity = host.selected_identity()
    parent = host.bind_send_lifecycle(
        attempt_id='parent-attempt', generation_id='generation-1', admitted_call_id='parent-budget')
    steer = host.bind_send_lifecycle(
        attempt_id='steer-attempt', generation_id='generation-1', admitted_call_id='steer-budget')
    await parent('native.submitting', send_record(send_id='parent-send'))
    await steer('native.submitting', send_record('response.steer', send_id='steer-send'))
    before = host.path.read_bytes()
    for callback in (parent, steer):
        await callback(event, {'input_id': 'input-one', 'response_id': 'parent', 'code': 'PRIVATE'})
    assert host.path.read_bytes() == before


@pytest.mark.parametrize('free_terminal', [False, True])
async def test_receipt_capacity_never_evicts_unknown_sends(tmp_path, monkeypatch, free_terminal):
    c = Coordinator(fake())
    host = NativeProviderHost(c, c.loop, tmp_path)
    host.identity = host.selected_identity()
    monkeypatch.setattr(host, 'save', lambda: None)  # Exercise the in-memory bound without disk churn.
    for index in range(128):
        callback = host.bind_send_lifecycle(
            attempt_id=f'attempt-{index}', generation_id='generation-1', admitted_call_id=f'budget-{index}')
        await callback('native.submitting', send_record(send_id=f'send-{index}'))
        if index == 0 and free_terminal:
            await callback('native.response.created', {'response_id': 'parent'})
            await callback('native.completed', {'response_id': 'parent', 'pending_steering': True})
        else:
            await callback('native.outcome_unknown', {'reason': 'connection_closed'})
    before = copy.deepcopy(host.state)
    callback = host.bind_send_lifecycle(
        attempt_id='new-attempt', generation_id='generation-1', admitted_call_id='new-budget')
    if not free_terminal:
        with pytest.raises(ValueError, match='capacity'):
            await callback('native.submitting', send_record(send_id='new-send'))
        assert host.state == before
    else:
        await callback('native.submitting', send_record(send_id='new-send'))
        assert len(host.state['sends']) == 128
        assert host.state['sends'][:-1] == before['sends'][1:]
        assert host.state['sends'][-1]['send_id'] == 'new-send'


async def test_real_parent_completion_survives_overlapping_steer_preparation(tmp_path, monkeypatch):
    await native_parent_overlap(tmp_path, monkeypatch, fail_parent=False)


async def test_real_parent_failure_preserves_unsent_overlapping_steer(tmp_path, monkeypatch):
    await native_parent_overlap(tmp_path, monkeypatch, fail_parent=True)


async def native_parent_overlap(tmp_path, monkeypatch, *, fail_parent):
    import asyncio
    from amplifier_module_provider_openai import OpenAIProvider
    from amplifier_module_provider_openai.native import NativeSendAdmission, NATIVE_REQUEST
    from amplifier_core.llm_errors import LLMError
    from websockets.protocol import State

    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    c = Coordinator(OpenAIProvider(api_key='fixture', config={'default_model': 'gpt-6-astra'}))
    await install_native(c.loop, c, c.providers)
    host = c.get_capability('web.native_provider')
    native = host.provider
    parent = host.bind_send_lifecycle(
        attempt_id='parent-attempt', generation_id='generation-1', admitted_call_id='parent-budget')
    steer = host.bind_send_lifecycle(
        attempt_id='steer-attempt', generation_id='generation-1', admitted_call_id='steer-budget')
    parent_created, preparing, resume = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def lifecycle(kind, data):
        if kind in {'native.submitting', 'native.not_sent'}:
            await (parent if data['operation'] == 'response.create' else steer)(kind, data)
        else:
            # Keep both original bindings: the steer must not replace the parent's sink.
            await steer(kind, data)
            await parent(kind, data)
        if kind == 'native.response.created':
            parent_created.set()

    async def prepare(kind, *, input_id):
        if kind == 'response.steer':
            preparing.set()
            await resume.wait()
        return NativeSendAdmission(
            attempt_id='parent-attempt' if kind == 'response.create' else 'steer-attempt',
            admit=lambda envelope: True)

    events = asyncio.Queue()
    native.lifecycle, native.prepare_send = lifecycle, prepare
    native.owner = SimpleNamespace(
        config={}, runtime=SimpleNamespace(emit=AsyncMock()), _text=lambda command: 'Synthetic steer')
    native._connect = AsyncMock()
    native._guard_assembled_params_with_provider_count = AsyncMock()
    socket = SimpleNamespace(state=State.OPEN, send=AsyncMock(), close=AsyncMock(), recv=events.get)
    native.socket = socket
    await events.put(json.dumps({'type': 'response.created', 'response': {'id': 'parent'}}))
    async def run_parent():
        token = NATIVE_REQUEST.set(native)
        try:
            return await native._create_response({'input': []})
        finally:
            NATIVE_REQUEST.reset(token)
    parent_task = asyncio.create_task(run_parent())
    steer_task = None
    try:
        await asyncio.wait_for(parent_created.wait(), 1)
        from amplifier_module_provider_openai.native import NativeResponsesProvider
        steer_task = asyncio.create_task(NativeResponsesProvider.steer_live(native, SimpleNamespace(id='input-one')))
        await asyncio.wait_for(preparing.wait(), 1)
        if fail_parent:
            await events.put(json.dumps({'type': 'error', 'error': {'code': 'synthetic'}}))
            with pytest.raises(LLMError):
                await asyncio.wait_for(parent_task, 1)
        else:
            await events.put(json.dumps({'type': 'response.completed', 'response': {
                'id': 'parent', 'status': 'completed', 'output': [], 'usage': {}}}))
            await asyncio.wait_for(parent_task, 1)
        expected = 'unknown' if fail_parent else 'completed'
        assert host.state['outcome'] == expected
        assert host.state['sends'][1]['outcome'] == 'submitting'
        resume.set()
        with pytest.raises(LLMError):
            await asyncio.wait_for(steer_task, 1)  # Parent finished: the stale steer is not sent.
        assert socket.send.await_count == 1
        assert [row['outcome'] for row in host.state['sends']] == [expected, 'not_sent']
        assert host.state['outcome'] == expected and native._observer_failure is None
    finally:
        for task in (parent_task, steer_task):
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_shared_control_compacts_only_idle_and_never_returns_private_opaque(tmp_path,monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME',str(tmp_path))
    c=Coordinator(fake())
    host=NativeProviderHost(c,c.loop,tmp_path)
    host.identity=host.selected_identity()
    host.provider=SimpleNamespace(get_info=c.providers['configured'].get_info, native_status=lambda:{'supported':True,'compactAvailable':True,'checkpoint':'created'},
        native_compact=AsyncMock(return_value={'checkpoint':'created'}),native_export_checkpoint=lambda:{'output':'opaque-secret'})
    c.providers['configured'] = host.provider
    c.caps['live.checkpoint']=AsyncMock()
    runtime=SimpleNamespace(generation=None,queued_inputs=0)
    controls=RuntimeControls(SimpleNamespace(session_id=c.session_id,coordinator=c),runtime)
    controls.checkpoint=AsyncMock()
    result=await controls.perform('native.compact')
    assert 'opaque-secret' not in json.dumps(result)
    assert json.loads(host.checkpoint_path.read_text())['record']['output']=='opaque-secret'
    assert host.checkpoint_path.stat().st_mode & 0o777 == 0o600
    c.caps['live.checkpoint'].assert_awaited_once()
    runtime.generation={'id':'active'}
    with pytest.raises(ValueError,match='Wait'):await controls.perform('native.compact')
    host.provider.native_compact.assert_awaited_once()
    assert (await controls.perform('native.status'))['supported']
    await controls.close()


def test_public_steering_event_reports_identity_not_content():
    event=normalize_event({'type':'steering.applied','input_id':'i','response_id':'r','steer_id':'s',
                           'encrypted_content':'secret','input':'private'},'owned')
    assert event==('runtime.steering',{'sessionId':'owned','event':'steering.applied','input_id':'i','response_id':'r','steer_id':'s'})


@pytest.mark.asyncio
@pytest.mark.parametrize('operation',['native.status','native.compact'])
async def test_agent_runtime_alias_cannot_target_other_session_or_forge_actor(tmp_path,operation):
    from amplifier_web.service import AppService, AppError
    from amplifier_web.management import Management
    class Runtime:
        async def close(self):pass
        control=AsyncMock()
    runtime=Runtime()
    app=AppService(tmp_path,runtime,workspace=tmp_path);app.management=Management(app)
    try:
        await app.dispatch('session.create',{})
        own=app._session()['id']
        await app.dispatch('session.create',{})
        other=app._session()['id']
        with pytest.raises(AppError,match='calling conversation'):
            await app.app_bridge('dispatch',{'action':'runtime.control','args':{
                'sessionId':other,'operation':operation,'args':{'actor':'user','source':'user'}}},own)
        runtime.control.assert_not_awaited()
        assert app.state['selectedSessionId']==other
    finally:await app.close()


@pytest.mark.asyncio
async def test_changed_selection_uses_ordinary_transport_without_poisoning_native(tmp_path, monkeypatch):
    pytest.importorskip("amplifier_module_provider_openai.native")
    from amplifier_module_provider_openai import OpenAIProvider
    from amplifier_module_provider_openai.native import NATIVE_REQUEST
    from amplifier_module_loop_live.scope import LIVE_OWNER
    from amplifier_core.message_models import ChatRequest, Message
    from amplifier_web.host.session import SelectedProvider
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    provider=OpenAIProvider(api_key='fixture', config={'default_model':'gpt-6-astra'})
    c=Coordinator(provider)
    selected=SelectedProvider(provider, {'instance':'configured','model':'gpt-6-astra','effort':'high'})
    c.loop.root_provider=selected
    await install_native(c.loop,c,c.providers)
    host=c.get_capability('web.native_provider')
    selected.selection['effort']='low'
    calls=[]
    async def ordinary(self,request,**kwargs):
        calls.append((request,kwargs,NATIVE_REQUEST.get()))
        return 'ordinary'
    monkeypatch.setattr(OpenAIProvider,'complete',ordinary)
    token=LIVE_OWNER.set(SimpleNamespace(coordinator=c))
    try:
        assert host.provider.owner_getter() is None
        assert await selected.complete(ChatRequest(messages=[Message(role='user',content='next')]))=='ordinary'
    finally:LIVE_OWNER.reset(token)
    assert calls[0][0].reasoning_effort=='low' and calls[0][1]['model']=='gpt-6-astra'
    assert calls[0][2] is None
    assert not host.provider.request_uncertain and not host.path.exists()
    assert host.status()['steering']=='request_boundary'


@pytest.mark.asyncio
async def test_compaction_capacity_denial_prevents_sdk_and_success_records_usage(tmp_path):
    c=Coordinator(fake())
    host=NativeProviderHost(c,c.loop,tmp_path)
    host.identity=host.selected_identity()
    host.provider=SimpleNamespace(get_info=c.providers['configured'].get_info,
        native_status=lambda:{'supported':True,'checkpoint':'created'},
        native_compact=AsyncMock(return_value={'checkpoint':'created','usage':{'input_tokens':7,'output_tokens':3}}),
        native_export_checkpoint=lambda:{'output':'private'})
    c.providers['configured'] = host.provider
    async def deny(row):raise ValueError('capacity denied')
    c.telemetry.admission_guard=deny
    with pytest.raises(ValueError,match='capacity denied'):await host.compact()
    host.provider.native_compact.assert_not_awaited()
    assert not host.checkpoint_path.exists() and not c.telemetry.nodes
    admitted=[]
    async def admit(row):admitted.append(row['id'])
    c.telemetry.admission_guard=admit
    await host.compact()
    assert len(admitted)==1 and len(c.telemetry.nodes)==1
    receipt=next(iter(c.telemetry.nodes.values()))
    assert receipt['id']==admitted[0] and receipt['phase']=='completed'
    assert receipt['label']=='Compact provider context' and receipt['usage']['totalTokens']==10
    assert 'private' not in json.dumps(receipt)
    del c.caps['web.provider_call']
    with pytest.raises(ValueError,match='accounting'):await host.compact()
    host.provider.native_compact.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('work_boundary', [False, True])
async def test_selected_observed_surface_native_budget_checkpoint_and_reconnect(tmp_path, monkeypatch, work_boundary):
    pytest.importorskip('amplifier_module_provider_openai.native')
    from amplifier_module_provider_openai import OpenAIProvider
    from amplifier_module_loop_live.scope import LIVE_OWNER
    from amplifier_core.message_models import ChatRequest, Message
    from amplifier_web.host.session import SelectedProvider
    from amplifier_web.surface_delivery import SurfaceProvider
    from websockets.protocol import State
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    c=Coordinator(OpenAIProvider(api_key='fixture',config={'default_model':'gpt-6-astra'}))
    selected=SelectedProvider(c.providers['configured'],{'instance':'configured','model':'gpt-6-astra','effort':'high'})
    c.loop.root_provider=selected
    await install_native(c.loop,c,c.providers)
    host=c.get_capability('web.native_provider');native=host.provider
    c.messages=[Message(role='user',content='original').model_dump()]
    request=ChatRequest(messages=[Message(role='system',content='factory instructions'),Message(**c.messages[0])],max_output_tokens=32)
    opaque=[{'type':'compaction','encrypted_content':'PRIVATE OPAQUE'}]
    compact=AsyncMock(return_value=SimpleNamespace(content=json.dumps({'output':opaque,'usage':{'input_tokens':2,'output_tokens':1}})))
    client=SimpleNamespace(base_url='https://api.openai.com/v1',
                           responses=SimpleNamespace(with_raw_response=SimpleNamespace(compact=compact)))
    client.with_options=lambda **kwargs:client
    native._client=client
    native._provider_count_available=lambda:False
    native._guard_assembled_params_with_provider_count=AsyncMock(return_value=None)
    admissions=[]
    async def admit(row):admissions.append(row['id'])
    c.telemetry.admission_guard=admit
    pixel='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aJxkAAAAASUVORK5CYII='
    class Delivery:
        async def prepare(self,request,provider,**kwargs):
            return request.model_copy(update={'messages':[*request.messages,Message(role='user',metadata={'ephemeral':True},content=[{'type':'image','source':{'type':'base64','media_type':'image/png','data':pixel}}])]})
        def commit(self,request):pass
    observed=c.telemetry.instrument_provider(c.session_id,native)
    from amplifier_web.coordination_delivery import CoordinationDelivery
    # Production app installation adds this boundary even when the request has
    # no Work tools/association. Retain the ordinary untasked native path while
    # exercising the real admitted-call join, not a visual-only legacy fixture.
    scope = {'rootSessionId': c.session_id, 'runtimeSessionId': c.session_id,
             'generationId': 'generation-1', 'inputIds': ['ordinary-input'], 'inputCoverage': 'complete'}
    delivery = CoordinationDelivery(Delivery(), AsyncMock(), origin_scope=lambda: copy.deepcopy(scope)) if work_boundary else Delivery()
    if work_boundary:
        c.caps['web.surface_delivery'] = delivery
    surface=SurfaceProvider(observed,delivery)
    selected.execution_adapter=lambda provider:surface
    def socket(response_id):
        return SimpleNamespace(state=State.OPEN,send=AsyncMock(),close=AsyncMock(),recv=AsyncMock(side_effect=[
            json.dumps({'type':'response.created','response':{'id':response_id}}),
            json.dumps({'type':'response.completed','response':{'id':response_id,'status':'completed','model':'gpt-6-astra','output':[],'usage':{'input_tokens':1,'output_tokens':0}}})]))
    first=socket('first');native.socket=first
    owner=SimpleNamespace(coordinator=c,context=c.context,config={},runtime=SimpleNamespace(emit=AsyncMock()),native_job=lambda identity:None)
    token=LIVE_OWNER.set(owner)
    try:
        await selected.complete(request)
        await host.compact()
        assert compact.call_args.kwargs['instructions']=='factory instructions'
        before=len(admissions)
        await selected.request_budget(request,context_estimate=20)
        assert len(admissions)==before  # Pure budget observation is not a completion.
        assert native._budget_params(request,model='gpt-6-astra')['input'] != opaque
        second=socket('second');native.socket=second
        await selected.complete(request)
        sent=json.loads(second.send.call_args.args[0])
        assert sent['model']=='gpt-6-astra' and sent['reasoning']['effort']=='high'
        assert sent['instructions']=='factory instructions' and sent['input'][0]==opaque[0]
        assert sent['input'][-1]['content'][0]['image_url']=='data:image/png;base64,'+pixel
        assert 'previous_response_id' not in sent
        second.state=State.CLOSED
        third=socket('third')
        async def connect():native.socket=third
        native._connect=connect
        await selected.complete(request)
        renewed=json.loads(third.send.call_args.args[0])
        assert renewed['input']==sent['input'] and 'previous_response_id' not in renewed
        async def deny(row):raise ValueError('paused before native send')
        c.telemetry.admission_guard=deny
        with pytest.raises(ValueError,match='paused'):await selected.complete(request)
        third.send.assert_awaited_once()
    finally:LIVE_OWNER.reset(token)
    assert len(admissions)==4 and len(c.telemetry.nodes)==4
    assert all(row['phase']=='completed' and row['model']=='gpt-6-astra' for row in c.telemetry.nodes.values())
    assert selected.selection['effort']=='high' and not native.request_uncertain
    assert 'PRIVATE OPAQUE' not in json.dumps(list(c.telemetry.nodes.values()))


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [{}, {"timeout": 7.25}, {"timeout": None}])
@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
async def test_native_boundary_forwards_options_and_restores_scope(tmp_path, monkeypatch, options, outcome):
    import asyncio

    native = pytest.importorskip("amplifier_module_provider_openai.native")
    OpenAIProvider = pytest.importorskip("amplifier_module_provider_openai").OpenAIProvider
    from amplifier_module_loop_live.scope import NATIVE_REQUEST as LOOP_REQUEST

    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path))
    c = Coordinator(OpenAIProvider(api_key="fixture", config={"default_model": "gpt-6-astra"}))
    await install_native(c.loop, c, c.providers)
    provider = c.get_capability("web.native_provider").provider
    params, result, previous = {}, object(), object()
    seen = []

    async def native_response(self, received, **kwargs):
        seen.append((self, received, kwargs, LOOP_REQUEST.get()))
        if outcome == "error":
            raise ValueError("fixture failure")
        if outcome == "cancel":
            raise asyncio.CancelledError
        return result

    monkeypatch.setattr(native.NativeResponsesProvider, "_native_response", native_response)
    loop_token = LOOP_REQUEST.set(previous)
    provider_token = native.NATIVE_REQUEST.set(provider)
    try:
        if outcome == "success":
            assert await provider._native_response(params, **options) is result
        else:
            with pytest.raises(ValueError if outcome == "error" else asyncio.CancelledError):
                await provider._native_response(params, **options)
        assert len(seen) == 1
        assert seen[0][0] is provider and seen[0][1] is params
        assert seen[0][2] == options and seen[0][3] is provider
        assert LOOP_REQUEST.get() is previous
        assert native.NATIVE_REQUEST.get() is provider
    finally:
        LOOP_REQUEST.reset(loop_token)
        native.NATIVE_REQUEST.reset(provider_token)
