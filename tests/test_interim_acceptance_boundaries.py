"""Original resident-call completeness, not a capture-only tail substitute.

Synthetic canonical records pass through the real observer and host ingestion.
The HTTP reads use the normal saved-window worker; no private proof/cache is
seeded and no historical tool ownership is inferred.
"""
import asyncio
import hashlib
import json
import time

import pytest

from amplifier_web.automatic_history import AutomaticHistory
from amplifier_web.execution import ensure_turn, finish
from amplifier_web.execution_events import ExecutionEvents
from amplifier_web.server import create_app
from amplifier_web.session_files import append_event
from test_service import Runtime


async def _start(home, workspace, monkeypatch):
    """Observe the first scheduled refresh without adding/cancelling one."""
    completed = asyncio.Event()
    errors = []
    original = AutomaticHistory.refresh

    async def refresh(history, *args, **kwargs):
        try:
            return await original(history, *args, **kwargs)
        except BaseException as error:
            if history.service.data_dir == home:
                errors.append(type(error).__name__)
            raise
        finally:
            if history.service.data_dir == home:
                completed.set()

    # Restore the class before returning; each new service gets its own observer.
    with monkeypatch.context() as patch:
        patch.setattr(AutomaticHistory, 'refresh', refresh)
        runtime = Runtime()
        application = await create_app(home, workspace=workspace, runtime=runtime,
                                       voice=False, preload_providers=False,
                                       background_updates=False)
        try:
            await asyncio.wait_for(completed.wait(), 10)
            assert errors == []
            assert not application['service'].state['sharedHistory'].get('error')
        except BaseException:
            await application['service'].close()
            raise
    return application, runtime


async def _emit_resident_calls(service, split):
    await service.dispatch('session.create', {})
    session = service._session()
    sid = session['id']
    native = session.get('runtimeSessionId') or sid
    # This synthetic producer supplies the exact canonical records below.
    # Declare that ownership, as the real mounted capture hook does, rather
    # than also requesting Diagnostics' metadata-only fallback copies.
    session['runtimeReport'] = {'contextIntelligence': {'enabled': True}}
    input_id = 'original-144-input'
    service._message(session, 'user', 'Inspect four files and then 140 more.',
                     'chat', inputId=input_id)
    ensure_turn(session, input_id)
    emitted = []
    observer = ExecutionEvents(native, emitted.append)
    observer.lifecycle({'type': 'input.delivered', 'input_id': input_id, 'source': 'user'})
    anchor = service._message(session, 'assistant', 'First four files.', 'chat',
                              inputId=input_id, timestampKnown=True)
    expected = {}
    for index in range(144):
        if split and index == 4:
            anchor = service._message(session, 'assistant', 'The remaining files.',
                                      'chat', inputId=input_id, timestampKnown=True)
        arguments = {'file_path': f'file-{index}.txt',
                     'note': ('界𝄞' * 10000 if index < 4 else 'small') + f':INPUT-{index}'}
        output = ('雪𝄞' * 10000 if index < 4 else 'small') + f':OUTPUT-{index}'
        call = f'original-call-{index}'
        tool = ('app_control', 'app_control', 'read_file', 'bash')[index] if index < 4 else 'read_file'
        for event in ('tool:pre', 'tool:post'):
            data = {'session_id': native, 'tool_call_id': call, 'tool_name': tool,
                    'tool_input': arguments, 'timestamp': time.time()}
            if event == 'tool:post':
                data['result'] = output
            canonical = observer.hook(native, event, data)
            assert canonical is not None
            update = emitted[-1]['event']
            assert update['turnId'] == input_id
            await service.on_runtime_event('execution.event', update)
            append_event(session['workspace'], native, event, canonical)
        expected[update['id']] = {
            'call': call, 'tool': tool, 'anchor': anchor['id'], 'turn': input_id,
            'input': arguments, 'output': output,
        }
    final = service._message(session, 'assistant', 'All 144 files inspected.', 'chat',
                              inputId=input_id)
    finish(session, 'completed')
    session['status'] = 'idle'
    nodes = session['execution']['nodes']
    assert len(nodes) == len(expected) == 144
    assert {row['id'] for row in nodes} == set(expected)
    assert all(row.get('liveObservation') is True for row in nodes)
    assert all(row.get('toolObservationId') and row.get('rootSessionId') == sid for row in nodes)
    # These are all real resident host observations, not authored inline bodies.
    assert all('input' not in row and 'output' not in row for row in nodes)
    await service._flush_pending_progress()
    return session, expected, final['id']


async def _get(client, params):
    response = await client.get('/api/conversation/detail', params=params)
    value = await response.json()
    assert response.status == 200, {'request': params, 'status': response.status, 'response': value}
    return value


async def _field(client, reference):
    chunks, offset = [], 0
    for _ in range(64):
        page = await _get(client, {**reference, 'offset': offset, 'limit': 4096})
        assert len(page['value']) <= 4096
        chunks.append(page['value'])
        if page.get('nextOffset') is None:
            return ''.join(chunks), len(chunks)
        assert page['nextOffset'] > offset
        offset = page['nextOffset']
    pytest.fail('The exact field did not terminate within the synthetic bound')


async def _verify(client, service, sid, expected, final_id, retired):
    # Same public boundary the registered browser uses. Do not call private
    # resident_tree/proof builders to discover or pre-admit the missing group.
    await _get(client, {'sessionId': sid, 'part': 'groups'})
    response = await client.get('/api/state')
    assert response.status == 200
    state = await response.json()
    visible = next(row for row in state['sessions'] if row['id'] == sid)
    # Use the same explicit group-page overlay as Load earlier conversation.
    # The initial projection remains a tail, not all 144 resident positions.
    groups = visible['execution']['segments']
    cursor = visible['executionWindow'].get('before')
    group_cursors = set()
    for _ in range(8):
        if not cursor or not cursor.startswith('resident-groups:'):
            break
        assert cursor not in group_cursors
        group_cursors.add(cursor)
        older = await _get(client, {'sessionId': sid, 'part': 'groups', 'before': cursor})
        assert len(older['items']) <= 100
        assert older['coverage']['sourceEnd'] - older['coverage']['sourceStart'] <= 100
        # Latest summaries win when a group spans ranges; earlier groups remain.
        groups = list({row['id']: row for row in [*older['items'], *groups]}.values())
        cursor = older.get('before')
    else:
        pytest.fail('Resident group discovery did not progress within the fixture bound')
    segments = [row for row in groups
                if row.get('turnId') == 'original-144-input' and not row.get('observationWindow')]
    anchors = list(dict.fromkeys(row['anchor'] for row in expected.values()))
    assert [row['anchorMessageId'] for row in segments] == anchors, {
        'requiredAnchors': anchors, 'actualGroups': segments,
        'workProjection': visible.get('workProjection'),
    }
    assert all(row['anchorMessageId'] != final_id for row in segments)
    all_ids, pages_read = [], []
    for segment in segments:
        query = {'sessionId': sid, 'part': 'nodes', 'group': segment['id']}
        page = await _get(client, query)
        pages, cursors = [page], set()
        for _ in range(8):
            cursor = page.get('before')
            if cursor is None:
                break
            assert cursor not in cursors
            cursors.add(cursor)
            page = await _get(client, {**query, 'before': cursor, 'revision': pages[0]['revision']})
            pages.append(page)
        else:
            pytest.fail('Resident cursor did not terminate in its existing range budget')
        assert all(len(part['items']) <= 100 for part in pages)
        nodes = [node for part in reversed(pages) for node in part['items']]
        required = [identity for identity, row in expected.items()
                    if row['anchor'] == segment['anchorMessageId']]
        assert [node['id'] for node in nodes] == required
        assert len(set(required)) == len(nodes)
        for node in nodes:
            row = expected[node['id']]
            assert node['turnId'] == row['turn']
            assert node['anchorMessageId'] == row['anchor']
            assert node['toolCallId'] == row['call']
            assert node['label'] == row['tool']
            assert node['toolDetail']['sessionId'] == sid
            assert node['toolDetail']['id'] == node['id']
        all_ids.extend(required)
        pages_read.append([len(part['items']) for part in pages])

        references = []
        # Full long fields for the original four and the latest small call.
        for node in nodes:
            if node['id'] not in list(expected)[:4] + list(expected)[-1:]:
                continue
            lookup = node['toolDetail']
            for _ in range(8):
                detail = await _get(client, lookup)
                if detail['item'].get('inputDetail') and detail['item'].get('outputDetail'):
                    break
                assert detail.get('before'), detail
                lookup = {**node['toolDetail'], 'lookupKey': detail['lookupKey'],
                          'before': detail['before']}
            else:
                pytest.fail('Exact canonical tool lookup did not terminate')
            for field in ('input', 'output'):
                reference = detail['item'][field + 'Detail']
                assert reference['sessionId'] == sid
                assert reference['digest'] and reference['windowKey']
                value, chunks = await _field(client, reference)
                assert (json.loads(value) if field == 'input' else value) == expected[node['id']][field]
                if node['id'] in list(expected)[:4]:
                    assert chunks > 4
                references.append((reference, value))
        # Resident admitted groups support up to800 known IDs; saved observation
        # windows are a distinct100-ID contract and are not used as a substitute.
        sync = await client.post('/api/conversation/work-sync', json={
            'sessionId': sid, 'group': segment['id'],
            'known': {node['id']: node['detailVersion'] for node in nodes}})
        assert sync.status == 200, await sync.text()
        synced = await sync.json()
        assert synced['order'] == required
        for reference, value in references:
            assert (await _field(client, reference))[0] == value
    assert all_ids == list(expected) and len(set(all_ids)) == 144
    if retired:
        assert not dict.__contains__(service._session(sid, hydrate=False), 'execution')
    print('INTERIM-A-144', json.dumps({
        'count': len(all_ids), 'unique': len(set(all_ids)), 'pageSizes': pages_read,
        'groupPages': len(group_cursors),
        'identitiesSha256': hashlib.sha256(json.dumps(all_ids).encode()).hexdigest(),
        'retired': retired,
    }))


@pytest.mark.parametrize('phase', ['live', 'reopen', 'retired-reopen'])
@pytest.mark.parametrize('split', [False, True], ids=['one-group', 'early-and-tail-groups'])
async def test_144_resident_calls_complete_through_registered_web(
        tmp_path, monkeypatch, authenticated_client, phase, split):
    """Same workload independently qualifies live and genuine cold reopen."""
    home = tmp_path / 'app'
    application, runtime = await _start(home, tmp_path, monkeypatch)
    service, client, socket = application['service'], None, None
    try:
        session, expected, final_id = await _emit_resident_calls(service, split)
        sid = session['id']
        retired = phase == 'retired-reopen'
        if phase != 'live':
            if retired:
                service._state['selectedSessionId'] = None
                monkeypatch.setattr(service.cold_display, 'RECENT_LIMIT', 0)
                monkeypatch.setattr(service.cold_display, 'MIN_BYTES', 1)
                service.cold_display.retire(force=True)
                assert not dict.__contains__(session, 'execution')
            service._save()
            await service.close()
            assert runtime.started == runtime.sent == []
            application, runtime = await _start(home, tmp_path, monkeypatch)
            service = application['service']
        service.clients.attach('original-acceptance-browser', kind='web')
        with service.clients.bind('original-acceptance-browser'):
            # Selection is client-local presentation, not source/Work readiness.
            service.state['selectedSessionId'] = sid
        client = await authenticated_client(application)
        client.session.headers['X-Amplifier-Client'] = 'original-acceptance-browser'
        socket = await client.ws_connect('/api/events/ws?clientId=original-acceptance-browser')
        initial = await socket.receive_json(timeout=5)
        assert initial['event'] == 'state'
        assert initial['data']['client']['id'] == 'original-acceptance-browser'
        # A tests completeness after ordinary transcript readiness; C separately
        # retains the first read during startup. Observe real stream updates,
        # never hydrate or fabricate a saved message/group to pass A.
        async with asyncio.timeout(10):
            snapshot = initial['data']
            while True:
                visible = next(row for row in snapshot['sessions'] if row['id'] == sid)
                assert not visible.get('historyError')
                if not visible.get('historyLoading'):
                    assert final_id in {row['id'] for row in visible['messages']}
                    break
                event = await socket.receive_json()
                if event['event'] == 'state':
                    snapshot = event['data']
        await _verify(client, service, sid, expected, final_id, retired)
        assert runtime.started == runtime.sent == []
    finally:
        if socket is not None:
            await socket.close()
        if client is not None:
            await client.close()
        if not service.closed:
            await service.close()


def _discovery_anchor(owner, session):
    """Only the unit controls use a small accepted-page stand-in, never A-C."""
    from amplifier_web.work_windows import source_identity
    saved = {'group': 'event-window:original', 'sessionId': session['id'],
             'source': source_identity(session), 'before': None}
    owner.pages[saved['group']] = saved
    owner.project(session)
    return saved


def test_resident_group_discovery_is_demand_paged_and_revalidates_admission(tmp_path, monkeypatch):
    from test_resident_work_pages import live_fixture
    owner, session, _ = live_fixture(tmp_path, monkeypatch, count=144)
    for row in session['execution']['nodes'][:4]:
        row['anchorMessageId'] = 'early'
    visited = []

    class BoundedRows(list):
        def __iter__(self):
            raise AssertionError('Discovery must not scan the full execution tree')

        def __getitem__(self, key):
            if isinstance(key, slice):
                start, end, _ = key.indices(len(self))
                assert end - start <= 100
                visited.append((start, end))
            elif isinstance(key, int):
                position = key if key >= 0 else len(self) + key
                visited.append((position, position + 1))
            return super().__getitem__(key)

    session['execution']['nodes'] = BoundedRows(session['execution']['nodes'])
    projection = owner.project(session)
    assert visited == [(44, 144)]  # Passive state still visits only its tail.
    assert [row['id'] for row in projection['execution']['segments']] == ['t@user']
    assert projection['executionWindow']['before'] is None  # Canonical loading is not exhaustion.
    saved = _discovery_anchor(owner, session)
    cursor = owner.live_pages.group_navigation(session, saved, {})['before']
    visited.clear()
    groups = owner.live_pages.read_groups(session, cursor)
    assert visited == [(0, 44)]  # One explicit range, not an eight-page scan.
    assert [row['id'] for row in groups['items']] == ['t@early', 't@user']
    assert groups['before'] is None
    visited.clear()
    earlier = owner.live_pages.read(session, 't@early')
    positions = {i for start, end in visited for i in range(start, end)}
    assert len(positions) <= 100, {'distinctPositions': len(positions), 'ranges': visited}
    assert [row['id'] for row in earlier['items']] == ['0', '1', '2', '3']
    assert earlier['before'] is None
    visited.clear()
    latest = owner.live_pages.read(session, 't@user')
    positions = {i for start, end in visited for i in range(start, end)}
    assert len(positions) <= 100, {'distinctPositions': len(positions), 'ranges': visited}
    assert [row['id'] for row in latest['items']] == [str(i) for i in range(44, 144)]
    assert owner.live_pages.read_text(session, earlier['items'][0]['outputDetail'])['value'] == 'body ' * 1000
    # A discovery descriptor never overrules current position/group evidence.
    for index in range(4):
        session['execution']['nodes'][index]['anchorMessageId'] = 'moved'
    with pytest.raises(ValueError, match='no longer available'):
        owner.live_pages.read(session, 't@early')
    owner.invalidate({'s'})
    with pytest.raises(ValueError, match='changed'):
        owner.live_pages.read_groups(session, cursor)


@pytest.mark.parametrize('change', ['append', 'invalidate', 'tail-before', 'tail-total', 'other-client'])
def test_initial_older_range_requires_current_complete_tail_witness(tmp_path, monkeypatch, change):
    from test_resident_work_pages import live_fixture
    owner, session, _ = live_fixture(tmp_path, monkeypatch, count=144)
    for row in session['execution']['nodes'][:4]:
        row['anchorMessageId'] = 'early'
    saved = _discovery_anchor(owner, session)
    cursor = owner.live_pages.group_navigation(session, saved, {})['before']
    owner.live_pages.read_groups(session, cursor)
    if change == 'append':
        session['execution']['nodes'].append({**session['execution']['nodes'][-1], 'id': 'new'})
        with pytest.raises(ValueError, match='changed'):
            owner.live_pages.read_groups(session, cursor)
        # A fresh tail projection cannot revive old-length discovery.
        owner.project(session)
    elif change == 'invalidate':
        owner.invalidate({'s'})
        owner.project(session)
    elif change == 'tail-before':
        owner.live_pages.observe_tail(session, {'items': [], 'total': 0, 'before': 'more'})
    elif change == 'tail-total':
        owner.live_pages.observe_tail(session, {'items': [], 'total': 101, 'before': None})
    else:
        monkeypatch.setattr(owner.live_pages, '_client', lambda: 'other-client')
    with pytest.raises(ValueError, match='no longer available'):
        owner.live_pages.read(session, 't@early')
    # Current tail remains available, not silently replaced by older evidence.
    assert len(owner.live_pages.read(session, 't@user')['items']) == 100
    owner.retain_interest({'s'})
    owner.retain_interest(set())
    assert not owner.live_pages.tails


@pytest.mark.parametrize('mutation', ['source', 'client', 'owner', 'forged'])
def test_resident_group_discovery_refuses_other_scope(tmp_path, monkeypatch, mutation):
    from test_resident_work_pages import live_fixture
    owner, session, _ = live_fixture(tmp_path, monkeypatch, count=144)
    cursor = owner.live_pages.group_navigation(session, _discovery_anchor(owner, session), {})['before']
    if mutation == 'source':
        session['nativeIdentity'] = 'other'
    elif mutation == 'client':
        monkeypatch.setattr(owner.live_pages, '_client', lambda: 'other-browser')
    elif mutation == 'owner':
        monkeypatch.setattr(owner, '_owner', lambda _: dict(session))
    else:
        cursor = cursor[:-4] + 'aaaa'
    with pytest.raises(ValueError):
        owner.live_pages.read_groups(session, cursor)


def test_resident_group_discovery_progresses_empty_ranges_and_bounds_receipts(tmp_path, monkeypatch):
    from test_resident_work_pages import live_fixture
    owner, session, _ = live_fixture(tmp_path, monkeypatch, count=1001)
    for row in session['execution']['nodes']:
        row['canonicalHistory'] = True
    cursor = owner.live_pages.group_navigation(session, _discovery_anchor(owner, session), {})['before']
    ends = []
    for _ in range(11):
        page = owner.live_pages.read_groups(session, cursor)
        assert page['items'] == []
        ends.append(page['coverage']['sourceEnd'])
        assert len(owner.live_pages.discovery) <= 8
        cursor = page['before']
        if cursor is None:
            break
    assert ends == [901, 801, 701, 601, 501, 401, 301, 201, 101, 1]
    owner.retain_interest({'s'})
    owner.retain_interest(set())
    assert not owner.live_pages.discovery


def test_discovery_budget_expires_instead_of_downgrading_repeated_group(tmp_path, monkeypatch):
    from test_resident_work_pages import live_fixture
    owner, session, _ = live_fixture(tmp_path, monkeypatch, count=1001)
    for index in range(701, 901):
        session['execution']['nodes'][index]['anchorMessageId'] = 'repeated'
    saved = _discovery_anchor(owner, session)
    original = owner.live_pages.group_navigation(session, saved, {})['before']
    cursor = original
    for _ in range(8):
        cursor = owner.live_pages.read_groups(session, cursor)['before']
    # Admit newest 100 of the repeated group, then hold its exact open lease.
    opened = owner.live_pages.read(session, 't@repeated')
    assert [row['id'] for row in opened['items']] == [str(i) for i in range(801, 901)]
    field = opened['items'][0]['outputDetail']
    with pytest.raises(ValueError, match='discovery reached its page limit'):
        owner.live_pages.read_groups(session, cursor)
    assert owner.live_pages.discovery == {}
    assert owner.live_pages.read_text(session, field)['value'] == 'body ' * 1000
    with pytest.raises(ValueError, match='no longer available'):
        owner.live_pages.read(session, 't@repeated')
    with pytest.raises(ValueError, match='changed'):
        owner.live_pages.read_groups(session, original)
    fresh = owner.live_pages.group_navigation(session, saved, {})['before']
    owner.live_pages.read_groups(session, fresh)
    assert [row['id'] for row in owner.live_pages.read(session, 't@repeated')['items']] == [
        str(i) for i in range(801, 901)]


@pytest.mark.parametrize('declared_capture', [False, True])
async def test_synthetic_capture_ownership_distinguishes_diagnostics_fallback(tmp_path, declared_capture):
    from amplifier_web.service import AppService
    service = AppService(tmp_path / 'app', Runtime(), workspace=tmp_path)
    try:
        await service.dispatch('session.create', {})
        session = service._session()
        if declared_capture:
            session['runtimeReport'] = {'contextIntelligence': {'enabled': True}}
        ensure_turn(session, 'capture-control')
        emitted = []
        observer = ExecutionEvents(session['id'], emitted.append)
        observer.lifecycle({'type': 'input.delivered', 'input_id': 'capture-control', 'source': 'user'})
        before = len(service.diagnostics.pending)
        for event in ('tool:pre', 'tool:post'):
            record = observer.hook(session['id'], event, {
                'session_id': session['id'], 'tool_call_id': 'capture-control-call',
                'tool_name': 'read_file', 'tool_input': {'path': 'synthetic'},
                'result': 'synthetic', 'timestamp': time.time()})
            await service.on_runtime_event('execution.event', emitted[-1]['event'])
            append_event(session['workspace'], session['id'], event, record)
        queued = [item[5] for item in service.diagnostics.pending[before:] if item[2] == 'tools']
        assert queued == ([] if declared_capture else ['tool:pre', 'tool:post'])
        assert len(session['execution']['nodes']) == 1
    finally:
        await service.close()


async def test_resident_discovery_handoff_keeps_original_canonical_cursor_without_worker(tmp_path, monkeypatch):
    from test_resident_work_pages import live_fixture
    from amplifier_web.work_windows import source_identity
    owner, session, _ = live_fixture(tmp_path, monkeypatch, count=144)
    saved = {'group': 'event-window:original', 'sessionId': 's',
             'source': source_identity(session), 'before': 'original-canonical-cursor'}
    owner.pages[saved['group']] = saved
    cursor = owner.live_pages.group_navigation(session, saved, {})['before']
    # Another current canonical window must not retarget an older chain.
    owner.current['s'] = 'event-window:replacement'
    def forbidden():
        raise AssertionError('Resident discovery must not read canonical history')
    monkeypatch.setattr(owner, '_worker', forbidden)
    page = await owner.read('s', cursor)
    assert page['before'] == 'original-canonical-cursor'
    assert owner.current['s'] == 'event-window:replacement'
    assert owner.status == {}
    assert len(page['items']) <= 100
    del owner.pages[saved['group']]
    with pytest.raises(ValueError, match='expired'):
        await owner.read('s', cursor)
    await owner.close()
    assert not owner.live_pages.discovery


async def test_tail_witness_budget_never_spends_open_group_or_discovery_leases(tmp_path, monkeypatch):
    from test_resident_work_pages import live_fixture
    owner, session, _ = live_fixture(tmp_path, monkeypatch, count=144)
    for row in session['execution']['nodes'][:4]:
        row['anchorMessageId'] = 'early'
    saved = _discovery_anchor(owner, session)
    cursor = owner.live_pages.group_navigation(session, saved, {})['before']
    owner.live_pages.read_groups(session, cursor)
    receipts = dict(owner.live_pages.discovery)
    for index in range(8):
        monkeypatch.setattr(owner.live_pages, '_client', lambda index=index: f'other-{index}')
        owner.project(session)
    assert len(owner.live_pages.tails) == 8
    assert owner.live_pages.discovery == receipts
    monkeypatch.setattr(owner.live_pages, '_client', lambda: None)
    with pytest.raises(ValueError, match='no longer available'):
        owner.live_pages.read(session, 't@early')
    owner.project(session)
    assert len(owner.live_pages.read(session, 't@early')['items']) == 4
    await owner.close()
    assert not owner.live_pages.tails