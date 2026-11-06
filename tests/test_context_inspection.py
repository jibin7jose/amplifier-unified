"""Discriminating request, privacy, retention and passive-read boundaries."""
import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from amplifier_core.message_models import ChatRequest, ChatResponse, Message, TextBlock, Usage
from amplifier_web.context_inspection import projection, METHOD
from amplifier_web.context_inspection_adapter import ContextInspection
from amplifier_web.context_inspection_store import InspectionStore, MAX_REQUESTS, key
from amplifier_web.execution_events import ExecutionEvents, CALL_PURPOSE


def evidence(name, value):
    root = os.environ.get('AMPLIFIER_TEST_ARTIFACTS')
    if root:
        path = Path(root) / 'context-inspector-native'
        path.mkdir(parents=True, exist_ok=True)
        (path / (name + '.json')).write_text(json.dumps(value, indent=2))


def config(home, *, recording=True, enabled=True):
    path = home / 'diagnostics/config.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'enabled': enabled, 'providerRequests': recording, 'retentionDays': 30}))


def request(text='Captured user text'):
    return ChatRequest(model='fixture', messages=[
        Message(role='system', content='Outer instruction\n<context_file paths="AGENTS.md → /synthetic/AGENTS.md">Keep originals.</context_file>'),
        Message(role='user', content=text),
        Message(role='user', content='Continuation note', metadata={'source': 'context-managed', 'ephemeral': True}),
        Message(role='user', content='Current reminder', metadata={'ephemeral': True}),
    ], max_output_tokens=200)


class Provider:
    config = {'raw': True}
    def __init__(self): self.calls = []; self.fail = False
    def get_info(self): return {'id': 'fixture', 'defaults': {'model': 'fixture'}}
    async def complete(self, value, **kwargs):
        self.calls.append(value.model_dump())
        if self.fail: raise ValueError('scripted failure')
        return ChatResponse(content=[TextBlock(text='Done')], usage=Usage(input_tokens=1000, output_tokens=20, total_tokens=1020))
    async def request_budget(self, value, *, request_options=None, **kwargs):
        return {'estimated_input_tokens': 980, 'input_limit_tokens': 5000, 'context_window_tokens': 8000,
                'context_token_budget': kwargs.get('context_estimate', 0),
                'max_output_tokens': 200, 'measurement': {'kind': 'provider_count', 'input_tokens': 980, 'source':'scripted-fixture'}}
    async def stream(self, value, **kwargs):
        self.calls.append(value.model_dump())
        yield {'text': 'Done', 'usage': {'input_tokens': 1001, 'output_tokens': 21}}
        if self.fail: raise ValueError('scripted stream failure')


def observed(tmp_path, *, recording=True):
    home = tmp_path / 'home'; config(home, recording=recording)
    c = SimpleNamespace(get=lambda name: SimpleNamespace(config={'max_tokens': 6000, 'summarize_trigger': .8}))
    observer = ContextInspection(c, tmp_path / 'capture', home)
    events = ExecutionEvents('root', lambda row: None)
    events.context_inspectors['root'] = observer
    provider = events.instrument_provider('root', Provider())
    return observer, events, provider


@pytest.mark.parametrize('streaming', [False, True])
async def test_exact_identity_two_cycles_and_failure_do_not_mix(tmp_path, streaming):
    observer, events, provider = observed(tmp_path)
    first = request()
    await provider.request_budget(first, request_options={'model': 'fixture'})
    if streaming:
        async for _ in provider.stream(first, model='fixture'): pass
    else: await provider.complete(first, model='fixture')
    await observer.drain()
    result = observer.store.read(bodies=True)
    first_id = result['selected']['callId']
    assert result['selected']['phase'] == 'completed'
    assert result['selected']['budget']['measurement']['input_tokens'] == 980
    assert result['manifest']['method'] == METHOD
    item = next(i for i in result['items'] if i.get('sourceLabel'))
    assert observer.store.read(call_id=first_id, item_id=item['id'], bodies=True)['text'] == 'Keep originals.'
    first.messages[0].content = 'changed mutable source after request'
    second = request('Adjacent request'); await provider.complete(second)
    await observer.drain()
    latest = observer.store.read(bodies=True)
    assert latest['selected']['callId'] != first_id
    assert observer.store.read(call_id=first_id, item_id=item['id'], bodies=True)['text'] == 'Keep originals.'
    provider.fail = True
    with pytest.raises(ValueError): await provider.complete(request('failed request'))
    await observer.drain()
    failed = observer.store.read(bodies=True)
    assert failed['selected']['callId'] == latest['selected']['callId']
    assert failed['latest']['phase'] == 'error'
    assert failed['latest']['callId'] != failed['selected']['callId']
    assert len(provider.calls) == 3
    assert len({r['id'] for r in events.nodes.values()}) == 3


async def test_no_record_metadata_contains_no_text_paths_or_hashes(tmp_path):
    observer, _, provider = observed(tmp_path, recording=False)
    await provider.complete(request('private source text'))
    await observer.drain()
    result = observer.store.read(bodies=False)
    assert result['manifest']['estimatedTextTokens'] > 0
    assert not any(i['bodyAvailable'] for i in result['items'])
    raw = ''.join(p.read_text() for p in observer.store.directory.glob('*.json'))
    assert all(s not in raw for s in ('private source text', '/synthetic', 'Keep originals', 'AGENTS.md'))
    assert not list(observer.store.directory.glob('*.txt'))
    provider.config = {'raw': False}
    await provider.complete(request('explicit optout'))
    await observer.drain()
    assert observer.store.read()['manifest']['coverage'] == 'unavailable'


async def test_secret_redaction_opaque_state_and_body_revocation(tmp_path):
    observer, _, provider = observed(tmp_path)
    value = request('api_key=sk-abcdefghijklmnopqrstuvwx')
    value.messages.append(Message(role='user', content='secret native placeholder',
        metadata={'openai:compaction': {'output': ['encrypted hidden payload']}}))
    await provider.complete(value)
    await observer.drain()
    result = observer.store.read(bodies=True)
    secret = next(i for i in result['items'] if i.get('redacted'))
    assert secret['tokens'] is None
    body = observer.store.read(call_id=result['selected']['callId'], item_id=secret['id'], bodies=True)
    assert 'sk-abcdefghijklmnopqrstuvwx' not in body['text']
    assert 'encrypted hidden payload' not in ''.join(p.read_text() for p in observer.store.directory.glob('*') if p.is_file())
    assert any('opaque' in (i.get('bodyUnavailable') or '') for i in result['items'])
    denied = observer.store.read(call_id=result['selected']['callId'], item_id=secret['id'], bodies=False)
    assert denied['text'] is None


async def test_background_counts_and_late_completion_do_not_retarget(tmp_path):
    observer, _, provider = observed(tmp_path)
    await provider.complete(request())
    await observer.drain()
    before = observer.store.read()['selected']['callId']
    token = CALL_PURPOSE.set({'purpose': 'naming', 'lifecycle': 'background', 'label': 'Naming'})
    try: await provider.complete(request('auxiliary'))
    finally: CALL_PURPOSE.reset(token)
    assert observer.store.read()['selected']['callId'] == before
    # Finish an older call after a newer one: selection uses request start,
    # never the arrival order of completion receipts.
    manifest, texts = projection(request(), bodies=True)
    now = time.time()
    for identity, start in [('llm:older', now), ('llm:newer', now + 1)]:
        observer.store.begin({'callId': identity, 'startedAt': start, 'phase': 'running'}, manifest, texts)
    observer.store.finish('llm:newer', 'completed', {})
    observer.store.finish('llm:older', 'completed', {})
    assert observer.store.read()['selected']['callId'] == 'llm:newer'


async def test_ring_bounds_and_metadata_never_reads_body(tmp_path, monkeypatch):
    observer, _, provider = observed(tmp_path)
    for i in range(9): await provider.complete(request('x' * 12000 + str(i)))
    await observer.drain()
    assert len(observer.store.index()) == MAX_REQUESTS
    assert len(list(observer.store.directory.glob('*.txt'))) == MAX_REQUESTS
    original = observer.store._read
    reads = []
    def tracked(name, maximum):
        reads.append((name, maximum)); return original(name, maximum)
    monkeypatch.setattr(observer.store, '_read', tracked)
    result = observer.store.read(bodies=True)
    assert len(reads) == 2 and not any(n.endswith('.txt') for n, _ in reads)
    item = next(i for i in result['items'] if i['label'].startswith('User 2'))
    first = observer.store.read(call_id=result['selected']['callId'], item_id=item['id'], bodies=True)
    assert len(first['text']) == 6000 and first['nextOffset'] == 6000
    with pytest.raises(ValueError): observer.store.read(call_id='llm:expired')
    with pytest.raises(ValueError): observer.store.read(call_id=result['selected']['callId'], item_id='../../file', bodies=True)


def test_unicode_denominator_and_capture_boundaries():
    manifest, _ = projection(ChatRequest(messages=[Message(role='user', content='😀' * 5)]))
    assert manifest['estimatedTextTokens'] == 2
    assert manifest['items'][0]['tokens'] == 2
    bounded, _ = projection(request('x' * 120001), bodies=True)
    assert bounded['bounded'] and any(i['tokens'] is None for i in bounded['items'])
    assert bounded['estimatedTextTokens'] == sum(i['tokens'] for i in bounded['items'] if i['tokens'] is not None)


async def test_changed_budget_binding_loses_measurement_and_observer_failure_is_not_call_failure(tmp_path, monkeypatch):
    observer, _, provider = observed(tmp_path)
    value = request(); await provider.request_budget(value)
    value.messages.append(Message(role='user', content='changed after counting'))
    await provider.complete(value)
    await observer.drain()
    assert observer.store.read()['selected']['budget'] == {}
    monkeypatch.setattr(observer.store, 'begin', lambda *args: (_ for _ in ()).throw(OSError('storage unavailable')))
    assert (await provider.complete(request())).content[0].text == 'Done'
    await observer.drain()


async def test_read_uses_no_history_and_rechecks_recording_after_await(tmp_path, monkeypatch):
    from amplifier_web.context_inspection_read import read
    from amplifier_web.service import AppError
    observer, _, provider = observed(tmp_path)
    await provider.complete(request())
    await observer.drain()
    session = {'id': 'root', 'workspace': str(tmp_path)}
    service = SimpleNamespace(data_dir=observer.home, _session=lambda sid, hydrate: session if not hydrate else pytest.fail('history hydration'))
    monkeypatch.setattr('amplifier_web.context_inspection_read.InspectionStore', lambda path: observer.store)
    result = await read(service, {'sessionId': 'root'})
    item = next(i for i in result['items'] if i['bodyAvailable'])
    original = observer.store.read
    def revoke(**kwargs):
        result = original(**kwargs); config(observer.home, recording=False); return result
    monkeypatch.setattr(observer.store, 'read', revoke)
    with pytest.raises(AppError, match='access changed'):
        await read(service, {'sessionId': 'root', 'callId': result['selected']['callId'], 'itemId': item['id']})


async def test_real_core_foundation_final_refit_matches_independent_transport(tmp_path):
    from test_pretransport_refit_native import native_case
    session, state = await native_case('stale_visual', selected=True, preparation_context=True)
    config(tmp_path / 'home')
    observer = ContextInspection(session.coordinator, tmp_path / 'capture', tmp_path / 'home')
    events = ExecutionEvents(session.session_id, lambda event: None)
    events.context_inspectors[session.session_id] = observer
    selected = session.coordinator.get('providers')['fixture']
    wrapped = selected._execution_provider()
    raw = wrapped.original
    raw.config = {'raw': True}
    wrapped.original = events.instrument_provider(session.session_id, raw)
    # The real native factory reloads a file each cycle. This is synthetic
    # permitted test input, not a production source-file lookup by the inspector.
    source = tmp_path / 'AGENTS.md'
    source.write_text('Retain exact captured evidence.')
    async def instructions():
        return f'<context_file paths="AGENTS.md → {source}">{source.read_text()}</context_file>'
    await session.coordinator.get('context').set_system_prompt_factory(instructions)
    try:
        assert await session.execute('Read once, then finish.') == 'Done'
        await observer.drain()
        assert state.tools == 1 and len(state.transports) == 2
        assert len(state.measurements) == 3  # refused preflight is not a call
        result = observer.store.read(bodies=True)
        assert len(result['requests']) == 2
        actual = json.loads(state.transports[-1])
        captured = []
        for item in result['items']:
            if item['bodyAvailable']:
                text = observer.store.read(call_id=result['selected']['callId'], item_id=item['id'], bodies=True)['text']
                captured.append(text)
                if item['category'] != 'tools':
                    assert text in json.dumps(actual, ensure_ascii=False) or any(
                        isinstance(m['content'], str) and text in m['content'] for m in actual['messages'])
        assert 'Retain exact captured evidence.' in captured
        assert 'c3ludGhldGlj' not in ''.join(captured), 'revoked prefit pixels must not leak'
        source.write_text('Current source is different.')
        item = next(i for i in result['items'] if i.get('sourceLabel'))
        assert observer.store.read(call_id=result['selected']['callId'], item_id=item['id'], bodies=True)['text'] == 'Retain exact captured evidence.'
        assert item['sha256'] == hashlib.sha256(b'Retain exact captured evidence.').hexdigest()
        evidence('final-refit', {'boundary':'actual Core/Foundation/SelectedProvider/SurfaceProvider/ExecutionEvents',
            'providerCalls':len(state.transports), 'budgetCalls':len(state.measurements),
            'independentTransportSha256':[hashlib.sha256(s.encode()).hexdigest() for s in state.transports],
            'capturedRequests':result['requests'], 'capturedItems':result['items'],
            'sourceChangeAfterRequest':'verified unchanged captured text', 'revokedPixelsAbsent':True})
    finally:
        await session.cleanup()


async def test_real_foundation_file_expansion_and_core_streaming(tmp_path):
    import importlib.util
    from amplifier_core import ProviderInfo
    from amplifier_foundation.bundle import Bundle, PreparedBundle, BundleModuleResolver
    from amplifier_web.surface_delivery import SurfaceProvider, SurfaceDelivery
    source = tmp_path / 'context.md'; source.write_text('Foundation file version one.')
    plan = {'session': {'orchestrator': {'module': 'loop-streaming', 'config': {
        'use_streaming': True, 'min_delay_between_calls_ms': 0}}, 'context': {'module': 'context-simple'}}}
    paths = {name: Path(importlib.util.find_spec('amplifier_module_' + name.replace('-', '_')).origin).parent
             for name in ('loop-streaming', 'context-simple')}
    native = await PreparedBundle(plan, BundleModuleResolver(paths), Bundle(
        name='inspector-stream', session=plan['session'], context={'fixture:context.md': source})).create_session(session_id='inspector-stream')
    config(tmp_path / 'home')
    observer = ContextInspection(native.coordinator, tmp_path / 'capture', tmp_path / 'home')
    events = ExecutionEvents(native.session_id, lambda event: None)
    events.context_inspectors[native.session_id] = observer
    class StreamProvider(Provider):
        def get_info(self): return ProviderInfo(id='fixture', display_name='Fixture', capabilities=['streaming'], defaults={'model':'fixture'})
        def parse_tool_calls(self, response): return []
        async def stream(self, value, **kwargs):
            self.calls.append(value.model_dump())
            yield {'content':'Done', 'usage':{'input_tokens':333, 'output_tokens':1}}
    raw = StreamProvider()
    async def bridge(operation, args): return {'surfaces':[]}
    wrapped = SurfaceProvider(events.instrument_provider(native.session_id, raw), SurfaceDelivery(bridge))
    await native.coordinator.mount('providers', wrapped, name='fixture')
    try:
        assert await native.execute('Inspect the actual streamed request.') == 'Done'
        await observer.drain()
        result = observer.store.read(bodies=True)
        assert result['selected']['phase'] == 'completed'
        assert result['selected']['usage']['inputTokens'] == 333
        assert len(raw.calls) == 1
        file = next(i for i in result['items'] if 'context.md' in i.get('sourceLabel',''))
        source.write_text('Foundation file version two.')
        assert 'version one' in observer.store.read(call_id=result['selected']['callId'], item_id=file['id'], bodies=True)['text']
        assert await native.execute('An adjacent request.') == 'Done'
        await observer.drain()
        newer = observer.store.read(bodies=True)
        file2 = next(i for i in newer['items'] if 'context.md' in i.get('sourceLabel',''))
        assert 'version two' in observer.store.read(call_id=newer['selected']['callId'], item_id=file2['id'], bodies=True)['text']
        assert result['selected']['callId'] != newer['selected']['callId']
        evidence('native-stream', {'providerCalls':len(raw.calls), 'completedRequestIds':[result['selected']['callId'],newer['selected']['callId']],
            'foundationFileVersions':['one','two'], 'oldCapturePreserved':True,
            'firstCapturedFile':file, 'secondCapturedFile':file2})
    finally:
        await native.cleanup()


async def test_actual_managed_compaction_projects_summary_not_full_saved_history(tmp_path):
    from test_context_continuity import mount
    original = [{'role':'user','content':'Keep this objective.'}]
    for index in range(5):
        original += [{'role':'assistant','content':f'Research detail {index} ' * 150},
                     {'role':'user','content':f'Continue with retained result {index}.'}]
    c, summary_provider, adapter, checkpoint, transcript = await mount(tmp_path / 'native', original)
    rows = await c.context.get_messages_for_request(provider=summary_provider)
    assert c.context.summary is not None
    assert await c.context.get_messages() == original
    manifest, texts = projection(ChatRequest(messages=[Message(**m) for m in rows]), bodies=True)
    assert next(v for v in manifest['categories'] if v['id']=='summary')['items'] > 0
    assert 'Research detail 0 ' * 150 not in ''.join(texts.values())
    assert any('Original task' in text for text in texts.values())
    evidence('managed-compaction', {'originalMessages':len(original), 'requestMessages':len(rows),
        'categories':manifest['categories'], 'originalHistoryUnchanged':True, 'oldResearchAbsent':True})


async def test_storage_wait_cannot_yield_between_final_guard_and_provider_entry(tmp_path, monkeypatch):
    import threading
    observer, _, provider = observed(tmp_path)
    gate, started = threading.Event(), threading.Event()
    original = observer.store.begin
    def delayed(*args, **kwargs):
        started.set(); gate.wait(5); return original(*args, **kwargs)
    monkeypatch.setattr(observer.store, 'begin', delayed)
    task = asyncio.create_task(provider.complete(request()))
    try:
        await asyncio.to_thread(started.wait, 2)
        assert len(provider.calls) == 1, 'provider entry must not await diagnostic disk I/O'
    finally:
        gate.set()
        await task
        await observer.drain()


async def test_authenticated_action_and_peer_scope_without_runtime_wake(tmp_path, authenticated_client):
    from amplifier_web.server import create_app
    from amplifier_web.service import AppError
    from test_service import Runtime
    runtime = Runtime()
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path,
                           runtime=runtime, voice=False, background_work=False, background_updates=False)
    client = await authenticated_client(app)
    created = await app['service'].dispatch('session.create', {})
    sid = created['sessionId']
    before = list(runtime.started)
    result = await client.post('/api/actions', json={'action':'context.inspect','args':{'sessionId':sid},'id':'inspect'})
    assert result.status == 200
    assert (await result.json())['result']['status'] == 'no_completed_request'
    assert runtime.started == before
    rejected = await client.post('/api/actions', headers={'Origin':'https://untrusted.example'},
        json={'action':'context.inspect','args':{'sessionId':sid}})
    assert rejected.status == 403
    with pytest.raises(AppError):
        await app['service'].dispatch('context.inspect', {'sessionId':sid}, origin='agent', caller_session_id='peer')


def test_empty_and_adversarial_projection_work_is_bounded():
    from amplifier_web.context_inspection import MAX_VISITS, MAX_TEXT_CHARS
    timings = []
    for size in (100, 10000, 100000):
        work = {}
        # Use a non-copying repeated sequence; no fixture hydration cost is
        # confused with projection work. Empty content must still spend visits.
        value = SimpleNamespace(messages=[{'role':'user','content':[]}] * size, tools=[])
        started = time.perf_counter()
        manifest, _ = projection(value, work=work)
        timings.append({'messages':size,'seconds':time.perf_counter()-started,'work':work})
        assert work['messages'] == min(size, MAX_VISITS)
        assert manifest['bounded'] == (size > MAX_VISITS)
    work = {}
    adversarial = '<context_file paths="x">' * 20000  # no closing tag
    started = time.perf_counter()
    manifest, _ = projection(SimpleNamespace(messages=[
        {'role':'system','content':adversarial}] * 10000, tools=[]), work=work)
    timings.append({'shape':'unclosed repeated wrappers','seconds':time.perf_counter()-started,'work':work})
    assert work['wrapperCharacters'] <= MAX_TEXT_CHARS
    assert work['messages'] + work['blocks'] <= MAX_VISITS
    assert manifest['bounded'] and manifest['estimatedTextTokens'] == 0
    # With the global content allowance exhausted, schemas must not encode.
    work = {}
    blocks = [{'type':'text','text':None}] * 10000
    projection(SimpleNamespace(messages=[{'role':'user','content':blocks}],tools=[]), work=work)
    assert work['messages'] + work['blocks'] == MAX_VISITS
    evidence('bounded-work', {'method':'deterministic visited work; timings descriptive, not a universal gate','cases':timings})


def test_expiry_read_policy_and_physical_next_capture_retirement(tmp_path):
    store = InspectionStore(tmp_path / 'capture')
    manifest, texts = projection(request('old recorded text'), bodies=True)
    old = {'callId':'llm:aged','startedAt':time.time()-3*86400,'phase':'running'}
    store.begin(old, manifest, texts)
    store.finish(old['callId'], 'completed', {})
    assert store.index(days=1) == []
    assert store._path(key(old['callId'])+'.txt').exists(), 'read expiry is not a physical deletion claim'
    assert len(store.index(days=None)) == 1, 'finish must not orphan an aged indexed body'
    store.begin({'callId':'llm:new','startedAt':time.time(),'phase':'running'}, *projection(request(),bodies=True), days=1)
    assert not store._path(key(old['callId'])+'.txt').exists()
    assert not store._path(key(old['callId'])+'.json').exists()
    with pytest.raises(ValueError): store.read(call_id=old['callId'], days=1)


async def test_astral_pagination_offsets_are_unicode_codepoints(tmp_path):
    observer, _, provider = observed(tmp_path)
    await provider.complete(request('😀' * 10000))
    await observer.drain()
    result = observer.store.read(bodies=True)
    item = next(i for i in result['items'] if i['characters'] == 10000)
    page = observer.store.read(call_id=result['selected']['callId'], item_id=item['id'], bodies=True)
    assert page['endOffset'] == 6000 and page['nextOffset'] == 6000 and len(page['text']) == 6000
    page2 = observer.store.read(call_id=result['selected']['callId'], item_id=item['id'], offset=6000, bodies=True)
    assert page2['endOffset'] == 10000 and page2['nextOffset'] is None and len(page2['text']) == 4000


@pytest.mark.parametrize('failed_stage', ['body', 'manifest', 'index-before', 'index-after'])
def test_failed_snapshot_write_retains_bounded_transaction_and_no_orphans(tmp_path, monkeypatch, failed_stage):
    store = InspectionStore(tmp_path / 'capture')
    original = store._write
    def fail(name, value):
        match = (name.endswith('.txt') if failed_stage == 'body' else
                 name not in {'index.json','transaction.json'} and name.endswith('.json') if failed_stage == 'manifest'
                 else name == 'index.json')
        if match:
            if failed_stage == 'index-after': original(name, value)
            raise OSError('scripted write acknowledgement failure')
        return original(name, value)
    monkeypatch.setattr(store, '_write', fail)
    for index in range(8):
        with pytest.raises(OSError):
            store.begin({'callId':f'llm:failed-{index}','startedAt':time.time(),'phase':'running'},
                        *projection(request(), bodies=True))
        files = list(store.directory.glob('*.txt'))
        assert len(files) == len(store.index(days=None)) <= MAX_REQUESTS
        assert not store._path('transaction.json').exists()
    monkeypatch.setattr(store, '_write', original)
    store.begin({'callId':'llm:healthy','startedAt':time.time(),'phase':'running'}, *projection(request(),bodies=True))
    assert len(list(store.directory.glob('*.txt'))) <= MAX_REQUESTS


def test_cleanup_failure_blocks_new_snapshot_growth_until_reconciled(tmp_path, monkeypatch):
    store = InspectionStore(tmp_path / 'capture')
    for index in range(4):
        store.begin({'callId':f'llm:before-{index}','startedAt':time.time(),'phase':'running'}, *projection(request(),bodies=True))
    unlink = Path.unlink
    def refuse(path, *args, **kwargs):
        if path.name == key('llm:before-0')+'.txt':
            raise OSError('scripted cleanup failure')
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', refuse)
    for index in range(4, 9):
        with pytest.raises(OSError):
            store.begin({'callId':f'llm:after-{index}','startedAt':time.time(),'phase':'running'}, *projection(request(),bodies=True))
        assert len(list(store.directory.glob('*.txt'))) == MAX_REQUESTS + 1
    monkeypatch.setattr(Path, 'unlink', unlink)
    store.begin({'callId':'llm:recovered','startedAt':time.time(),'phase':'running'}, *projection(request(),bodies=True))
    assert len(list(store.directory.glob('*.txt'))) == MAX_REQUESTS
