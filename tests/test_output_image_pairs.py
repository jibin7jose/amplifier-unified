"""Explicit comparisons keep exact pixels together without implicit image history."""
import base64
import copy
import hashlib
import json
import struct
import zlib
from types import SimpleNamespace

import pytest
from amplifier_core import HookRegistry, ProviderInfo
from amplifier_core.message_models import ChatRequest, Message, ToolCall, ToolSpec
from amplifier_web.app_guidance import install_app_access
from amplifier_web.host.session import SelectedProvider
from amplifier_web.service import AppError, AppService
from test_output_images import png


def identities(rows):
    return [{'id': row['id'], 'sha256': row['sha256']} for row in rows]


def pixels(request):
    return [base64.b64decode(block.source['data']) for message in request.messages
            if isinstance(message.content, list) for block in message.content if block.type == 'image']


def status(request):
    return json.loads(request.messages[-1].content[0].text.split('\n', 1)[1])


class Provider:
    def get_info(self):
        return ProviderInfo(id='offline', display_name='Offline', credential_env_vars=[],
                            capabilities=['vision'], defaults={'model': 'base'})

    async def list_models(self):
        return [SimpleNamespace(id='selected', capabilities=['vision']),
                SimpleNamespace(id='text', capabilities=[])]

    async def complete(self, request, **kwargs):return request
    async def request_budget(self, request, **kwargs):return request
    async def stream(self, request, **kwargs):yield request


@pytest.fixture
async def pair(tmp_path):
    app = AppService(tmp_path/'app', workspace=tmp_path)
    await app.dispatch('session.create', {})
    sid = app._session()['id']
    rows, data = [], [png(3, 2), png(4, 2)]
    for index, raw in enumerate(data):
        path = tmp_path/f'{index}.png';path.write_bytes(raw)
        rows.append((await app.dispatch('outputs.attach', {
            'sessionId': sid, 'kind': 'file', 'title': f'Image {index+1}', 'path': path.name}))['result'])
    yield SimpleNamespace(app=app, sid=sid, rows=rows, data=data, path=tmp_path)
    await app.close()


async def test_shared_pair_is_atomic_ordered_immutable_and_owned(pair):
    p = pair;app = p.app
    before = app.state['selectedSessionId'];app.state['view']['draft'] = 'Unsent'
    (p.path/'0.png').write_bytes(b'changed live file')
    selected = identities(p.rows[::-1])
    result = await app.dispatch('outputs.images', {'sessionId': p.sid, 'images': selected})
    assert identities(result['result']['images']) == selected
    assert '_image' not in json.dumps(result)
    private = await app.app_bridge('outputs.images.read', {'images': selected}, p.sid)
    assert [base64.b64decode(row['_image']) for row in private['images']] == p.data[::-1]
    assert private['bytes'] == sum(map(len, p.data))
    assert app.state['selectedSessionId'] == before and app.state['view']['draft'] == 'Unsent'
    action = next(row for row in app.get_actions() if row['name'] == 'outputs.images')
    assert action['inputSchema']['properties']['images']['minItems'] == 2
    for bad in [[], selected[:1], selected+[selected[0]], [selected[0], selected[0]],
                [selected[0], {**selected[1], 'sha256': selected[0]['sha256']}],
                [selected[0], {**selected[1], 'sha256': '0'*64}]]:
        with pytest.raises(AppError):
            await app.dispatch('outputs.images', {'sessionId': p.sid, 'images': bad})
    await app.dispatch('session.create', {});other = app._session()['id']
    with pytest.raises(ValueError, match='another conversation'):
        await app.app_bridge('outputs.images.read', {'images': selected}, other)
    with pytest.raises(AppError, match='calling conversation'):
        await app.app_bridge('dispatch', {'action': 'outputs.images',
            'args': {'sessionId': p.sid, 'images': selected}}, other)


async def test_pair_aggregate_limit_is_not_two_independent_file_limits(pair):
    # Two valid PNG files below8MiB each must still fail the combined limit.
    p = pair;rows = []
    for index, original in enumerate(p.data):
        ancillary = b'tEXt'+b'note\0'+b'x'*(4*1024*1024)
        chunk = struct.pack('>I', len(ancillary)-4)+ancillary+struct.pack('>I', zlib.crc32(ancillary))
        raw = original[:-12]+chunk+original[-12:]
        path = p.path/f'large-{index}.png';path.write_bytes(raw)
        rows.append((await p.app.dispatch('outputs.attach', {'sessionId': p.sid, 'kind': 'file',
            'title': f'Large {index}', 'path': path.name}))['result'])
        assert p.app.outputs.image(p.sid, rows[-1]['id'], rows[-1]['sha256'])['bytes'] < 8*1024*1024
    with pytest.raises(AppError, match='combined8MB'):
        await p.app.dispatch('outputs.images', {'sessionId': p.sid, 'images': identities(rows)})


class Runtime:
    async def setup(self, pair):
        self.pair = pair;self.epoch = ['input-one'];self.tools = {};self.capabilities = {}
        self.hooks = HookRegistry();self.messages = [Message(role='user', content='Compare the saved originals.')]
        async def mount(kind, tool, name):self.tools[name] = tool
        coordinator = SimpleNamespace(get_capability=self.capabilities.get,
            get=lambda kind: self.tools if kind == 'tools' else None,
            register_capability=self.capabilities.__setitem__, mount=mount, hooks=self.hooks)
        async def bridge(operation, args):
            # Mirror the worker envelope for both preparation and revalidation;
            # let the real host own the manifest instead of replacing one read.
            if operation.startswith('context.'):
                args = {**args, '_contextInputs': list(self.epoch), '_contextBindings': []}
            return await pair.app.app_bridge(operation, args, pair.sid)
        await install_app_access(coordinator, bridge)
        self.delivery = self.capabilities['web.surface_delivery']
        self.provider = SelectedProvider(Provider(), {'model': 'selected', 'effort': 'high'},
                                        self.capabilities['web.provider_transform'])
        self.counter = 0
        return self

    def request(self):
        return ChatRequest(messages=list(self.messages), tools=[ToolSpec(name='app_control', parameters={})])

    async def read(self, rows=None, *, single=False, live_loop=False, hook=True, scope=None):
        self.counter += 1
        rows = self.pair.rows if rows is None else rows
        arguments = {'operation': 'dispatch', 'args': {
            'action': 'outputs.image' if single else 'outputs.images',
            'args': identities(rows)[0] if single else {'images': identities(rows)}}}
        if scope is not None:arguments['args']['args']['scope'] = scope
        call = ToolCall(id=f'image-call-{self.counter}', name='app_control', arguments=arguments)
        # This is the actual loop's persisted assistant tool-call shape.
        self.messages.append(Message(role='assistant', content='', tool_calls=[{
            'id': call.id, 'tool': call.name, 'arguments': call.arguments}]))
        if live_loop:
            loop = pytest.importorskip('amplifier_module_loop_live').BundleLiveOrchestrator({})
            loop._tool_calls_this_turn = 0
            identity, name, content = await loop._execute_tool_only(call, self.tools, self.hooks, None)
        else:
            result = await self.tools['app_control'].execute(arguments)
            value = result.model_dump()
            if hook:
                emitted = await self.hooks.emit('tool:post', {'tool_name': call.name, 'tool_call_id': call.id,
                    'tool_input': arguments, 'result': value})
                value = emitted.data['result']
            identity, name, content = call.id, call.name, json.dumps(value)
        self.messages.append(Message(role='tool', name=name, tool_call_id=identity, content=content))
        return json.loads(content)


@pytest.fixture
async def runtime(pair):return await Runtime().setup(pair)


async def test_pair_persists_only_in_current_input_and_reports_explicit_replacement(runtime):
    r = runtime
    await r.read(single=True)
    assert pixels(await r.provider.complete(r.request())) == r.pair.data[:1]
    receipt = await r.read()
    assert receipt['output']['result']['inspection']['previousImages'] == identities(r.pair.rows[:1])
    original = r.request();before = original.model_dump()
    sent = await r.provider.complete(original)
    assert pixels(sent) == r.pair.data and original.model_dump() == before
    assert status(sent)['comparisonReady'] and status(sent)['replaced'] == identities(r.pair.rows[:1])
    assert status(sent)['delivered'] == identities(r.pair.rows)
    assert pixels(await r.provider.complete(r.request())) == r.pair.data
    await r.read(r.pair.rows[::-1])
    reversed_pair = await r.provider.complete(r.request())
    assert pixels(reversed_pair) == r.pair.data[::-1]
    assert status(reversed_pair)['replaced'] == identities(r.pair.rows)
    r.epoch[:] = ['input-two']
    expired = await r.provider.complete(r.request())
    assert pixels(expired) == [] and not status(expired)['comparisonReady']
    assert r.delivery.output_selection() == []
    # A fresh coordinator cannot reauthorize pixels from copied history.
    fresh = await Runtime().setup(r.pair);fresh.messages = copy.deepcopy(r.messages)
    assert pixels(await fresh.provider.complete(fresh.request())) == []


async def test_single_read_deliberately_replaces_pair_and_failed_pair_keeps_selection(runtime):
    r = runtime;await r.read();await r.provider.complete(r.request())
    failure = await r.read([r.pair.rows[0], r.pair.rows[0]])
    assert failure['error']['imageSelectionUnchanged'] is True
    assert failure['error']['activeImages'] == identities(r.pair.rows)
    assert pixels(await r.provider.complete(r.request())) == r.pair.data
    single = await r.read(r.pair.rows[1:], single=True)
    assert single['output']['result']['replacedImages'] == identities(r.pair.rows)
    assert pixels(await r.provider.complete(r.request())) == r.pair.data[1:]


async def test_multiple_pair_requests_report_superseded_selection(runtime):
    r = runtime
    await r.read();await r.read(r.pair.rows[::-1])
    result = await r.provider.complete(r.request())
    assert pixels(result) == r.pair.data[::-1]
    assert status(result)['replaced'] == identities(r.pair.rows)
    assert status(result)['delivered'] == identities(r.pair.rows[::-1])


@pytest.mark.parametrize('keep_extra', [False, True])
async def test_structured_core_call_blocks_bind_with_or_without_legacy_call_list(runtime, keep_extra):
    from amplifier_core.message_models import ToolCallBlock
    r = runtime;await r.read()
    message = r.messages[-2];call = message.tool_calls[0]
    message.content = [ToolCallBlock(id=call['id'], name=call['tool'], input=call['arguments'])]
    if not keep_extra:message.model_extra.pop('tool_calls')
    assert pixels(await r.provider.complete(r.request())) == r.pair.data


@pytest.mark.parametrize('tamper', ['unbound', 'tool_id', 'assistant_id', 'arguments', 'nested',
                                  'error', 'receipt', 'truncated', 'duplicate_result', 'duplicate_call'])
async def test_pair_requires_real_direct_call_and_retained_success(runtime, tamper):
    r = runtime;await r.read(hook=tamper != 'unbound')
    if tamper == 'tool_id':r.messages[-1].tool_call_id = 'other-call'
    elif tamper == 'assistant_id':r.messages[-2].tool_calls[0]['id'] = 'other-call'
    elif tamper == 'arguments':r.messages[-2].tool_calls[0]['arguments']['args']['images'] = []
    elif tamper == 'nested':r.messages[-1].name = 'tool_exec'
    elif tamper in ('error', 'receipt'):
        value = json.loads(r.messages[-1].content)
        if tamper == 'error':value.update(success=False, error={'message': 'denied'})
        else:value['output']['result']['inspection']['receipt'] = 'forged'
        r.messages[-1].content = json.dumps(value)
    elif tamper == 'truncated':r.messages[-1].content = '{"output":"compacted"}'
    elif tamper == 'duplicate_result':r.messages.append(copy.deepcopy(r.messages[-1]))
    elif tamper == 'duplicate_call':r.messages.insert(-1, copy.deepcopy(r.messages[-2]))
    assert pixels(await r.provider.complete(r.request())) == []


async def test_active_compacted_pair_reports_omission_and_modified_failed_selection_preserves_previous(runtime):
    r = runtime;await r.read(single=True);await r.provider.complete(r.request())
    await r.read()
    value = json.loads(r.messages[-1].content)
    value.update(success=False, error={'message': 'Post-hook rejected'})
    r.messages[-1].content = json.dumps(value)
    assert pixels(await r.provider.complete(r.request())) == r.pair.data[:1]
    await r.read();assert pixels(await r.provider.complete(r.request())) == r.pair.data
    r.messages[-1].content = '{"output":"compacted"}'
    result = await r.provider.complete(r.request())
    assert pixels(result) == [] and not status(result)['comparisonReady']
    assert status(result)['requested'] == identities(r.pair.rows)
    assert len(status(result)['omitted']) == 2


@pytest.mark.parametrize('change', ['unavailable', 'epoch', 'replacement'])
async def test_cached_pair_is_atomic_and_revalidated_before_transport(runtime, monkeypatch, change):
    r = runtime;await r.read();request = r.request()
    assert pixels(await r.provider.request_budget(request)) == r.pair.data
    if change == 'unavailable':
        from amplifier_web import resource_files
        original = resource_files.read_detached
        body = r.pair.rows[1]['body']['$resource']
        def content(source, **kwargs):
            if source.identity == body:raise ValueError('Unavailable')
            return original(source, **kwargs)
        monkeypatch.setattr(resource_files, 'read_detached', content)
    elif change == 'epoch':r.epoch[:] = ['new-input']
    else:await r.read(r.pair.rows[::-1])
    sent = await r.provider.complete(request)
    assert pixels(sent) == [] and status(sent)['comparisonReady'] is False
    assert status(sent)['delivered'] == [] and len(status(sent)['omitted']) == 2


async def test_no_vision_reports_complete_omitted_pair(runtime):
    r = runtime;await r.read();r.provider.selection['model'] = 'text'
    result = await r.provider.complete(r.request())
    assert pixels(result) == [] and not status(result)['comparisonReady']
    assert len(status(result)['omitted']) == 2


async def test_real_loop_core_selected_provider_serializes_both_exact_images_together(runtime, monkeypatch):
    openai = pytest.importorskip('amplifier_module_provider_openai')
    context_module = pytest.importorskip('amplifier_module_context_simple')
    from openai.resources.responses.responses import AsyncResponses
    r = runtime;await r.read(live_loop=True)
    async def forbidden(*args, **kwargs):raise AssertionError('No SDK sends permitted')
    monkeypatch.setattr(AsyncResponses, 'create', forbidden)
    calls = []
    class WireProvider(openai.OpenAIProvider):
        async def list_models(self):return [SimpleNamespace(id='gpt-5.6-terra', capabilities=['vision'])]
        async def request_budget(self, request, **kwargs):return record('budget', request)
        async def complete(self, request, **kwargs):return record('complete', request)
        async def stream(self, request, **kwargs):yield record('stream', request)
    provider = WireProvider(api_key='offline-dummy-no-credential')
    def record(phase, request):
        params, _, _ = provider._assemble_initial_responses_params(request)
        def images(value):
            if isinstance(value, str) and value.startswith('data:image/png;base64,'):
                return [base64.b64decode(value.split(',', 1)[1], validate=True)]
            if isinstance(value, dict):return [raw for child in value.values() for raw in images(child)]
            if isinstance(value, list):return [raw for child in value for raw in images(child)]
            return []
        actual = images(params['input'])
        assert actual == r.pair.data
        assert [hashlib.sha256(raw).hexdigest() for raw in actual] == [row['sha256'] for row in r.pair.rows]
        assert params['model'] == 'gpt-5.6-terra' and request.reasoning_effort == 'high'
        assert params['store'] is False and 'previous_response_id' not in params
        calls.append(phase)
        return request
    selected = SelectedProvider(provider, {'model': 'gpt-5.6-terra', 'effort': 'high'}, r.capabilities['web.provider_transform'])
    context = context_module.SimpleContextManager()
    for message in r.messages:await context.add_message(message.model_dump())
    history = await context.get_messages();before = copy.deepcopy(history)
    request = ChatRequest(messages=[Message.model_validate(row) for row in history],
                          tools=[ToolSpec(name='app_control', parameters={})])
    await selected.request_budget(request);await selected.complete(request)
    stream = selected.stream(request);await anext(stream);await stream.aclose()
    assert calls == ['budget', 'complete', 'stream']
    assert await context.get_messages() == before and pixels(request) == []
