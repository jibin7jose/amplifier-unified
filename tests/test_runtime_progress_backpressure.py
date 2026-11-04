"""Buffered worker telemetry must not delay control messages or HTTP work."""
from amplifier_web.state_records import load as load_saved_state
import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from amplifier_web.runtime import RuntimeManager
from test_automatic_history import app_factory


@pytest.mark.parametrize('terminal', ['ready', 'stopped', 'error'])
async def test_module_preparation_burst_keeps_latest_progress_and_durable_terminal(
    app_factory, monkeypatch, terminal,
):
    app = app_factory()
    await app.dispatch('session.create', {})
    sid = app.state['selectedSessionId']
    await app.on_runtime_event('runtime.status', {'sessionId': sid, 'status': 'starting'})
    revision = app.state['revision']
    saves = []
    original = app._save

    def save():
        saves.append(app._session(sid)['status'])
        original()

    monkeypatch.setattr(app, '_save', save)
    manager = RuntimeManager()
    row = {'emit': app.on_runtime_event, 'started_at': time.monotonic(),
           'phase': 'bundle-preparation', 'detail': ''}
    for number in range(55):
        row['detail'] = f'Preparing configured module {number}'
        await manager._emit_progress(sid, row)
    assert saves == [], 'Module callbacks are progress, not 55 durable transitions'
    assert app.state['revision'] == revision
    assert app._session(sid)['progress']['detail'] == 'Preparing configured module 54'
    # Revision-sensitive readers can explicitly flush the latest progress.
    await app._flush_pending_progress()
    assert saves == ['starting']
    assert app.browser_state()['sessions'][0]['progress']['detail'].endswith('54')
    row['detail'] = 'Final setup observation'
    await manager._emit_progress(sid, row)
    if terminal == 'error':
        await app.on_runtime_event('runtime.error', {'sessionId': sid, 'error': 'Fixture setup failed'})
    else:
        await app.on_runtime_event('runtime.status', {'sessionId': sid, 'status': terminal})
    assert saves == ['starting', terminal]
    assert not app._progress_dirty
    saved_revision = app.state['revision']
    persisted = load_saved_state(app.db)
    assert persisted['revision'] == saved_revision
    await asyncio.sleep(.3)
    assert app._session(sid)['status'] == terminal
    assert app.state['revision'] == saved_revision, 'A stale timer must not republish terminal work'


async def test_buffered_worker_events_yield_to_bridge_without_reordering_events():
    order, replies = [], []

    async def bridge(operation, args, sid):
        assert args['_runtimeOwnerId'] == 'fixture-owner'
        order.append('bridge')
        return {'accepted': True}

    async def emit(kind, payload):
        if kind == 'assistant.delta':
            order.append(payload['text'])

    async def drain():
        pass

    async def wait():
        return 0

    stream = asyncio.StreamReader()
    messages = [{'op': 'bridge', 'id': 'capacity', 'operation': 'capacity.admit', 'args': {}}]
    messages += [{'type': 'assistant.delta', 'text': str(number)} for number in range(55)]
    stream.feed_data(b''.join(json.dumps(row).encode() + b'\n' for row in messages))
    stream.feed_eof()
    process = SimpleNamespace(stdout=stream, returncode=None, wait=wait,
                              stdin=SimpleNamespace(write=replies.append, drain=drain))
    ready = asyncio.get_running_loop().create_future()
    ready.set_result({})
    row = {'process': process, 'emit': emit, 'closing': False, 'pending': {}, 'ready': ready,
           'runtime_id': 'session', 'runtimeOwnerId': 'fixture-owner',
           'inputId': 'input', 'bridge_tasks': set(), 'backgroundCalls': set()}
    manager = RuntimeManager(bridge)
    manager.workers['session'] = row
    await manager._read('session', row)
    await asyncio.gather(*row['bridge_tasks'])
    assert [item for item in order if item != 'bridge'] == [str(number) for number in range(55)]
    assert order.index('bridge') < order.index('54'), 'Buffered telemetry must not monopolize the reader'
    assert len(replies) == 1
    assert json.loads(replies[0]) == {'op': 'bridge.result', 'id': 'capacity', 'result': {'accepted': True}}


async def test_tool_and_worker_progress_burst_is_coalesced_but_approval_flushes(app_factory):
    app = app_factory()
    await app.dispatch('session.create', {})
    sid = app.state['selectedSessionId']
    app._session(sid)['workers'] = [{'id': 'child', 'runId': 'current', 'status': 'running'}]
    revision = app.state['revision']
    queue = app.subscribe()
    for number in range(30):
        await app.on_runtime_event('runtime.tool', {'sessionId': sid, 'tool': 'read_file', 'callId': str(number), 'phase': 'pre'})
        await app.on_runtime_event('runtime.tool', {'sessionId': sid, 'tool': 'read_file', 'callId': str(number), 'phase': 'post'})
        await app.on_runtime_event('worker.updated', {'sessionId': sid, 'id': 'child', 'runId': 'current',
            'activityOnly': True, 'updatedAt': number, 'detail': str(number)})
    assert queue.empty() and app.state['revision'] == revision
    assert app._session(sid)['workers'][0]['updatedAt'] == 29
    await app.on_runtime_event('approval.requested', {'sessionId': sid, 'id': 'review', 'prompt': 'Review this'})
    latest = queue.get_nowait()['sessions'][0]
    assert latest['approvals'][-1]['id'] == 'review'
    assert latest['workers'][0]['updatedAt'] == 29
    assert not latest['activity']['activeTools']
    assert app.state['revision'] == revision + 1 and not app._progress_dirty
    await asyncio.sleep(.3)
    assert queue.empty()


@pytest.mark.parametrize('phase', ['completed', 'failed', 'outcome_unknown'])
async def test_terminal_execution_event_commits_without_rewriting_unrelated_state(
    app_factory, monkeypatch, phase,
):
    from amplifier_web.state_records import load
    from amplifier_web.session_projection import hydrate
    from test_scoped_state_records import chats

    app, rows = chats(app_factory)
    selected, other = rows
    queue = app.subscribe()
    checkpoint = app.db.execute('SELECT value FROM state WHERE id=1').fetchone()[0]
    unrelated = app._state['runtimeControl'][other['id']]
    encodes = []
    original = json.dumps

    def encode(value, *args, **kwargs):
        if value is unrelated or isinstance(value, dict) and 'runtimeControl' in value:
            encodes.append(1)
        return original(value, *args, **kwargs)

    monkeypatch.setattr(json, 'dumps', encode)
    event = {'sessionId': selected['id'], 'rootSessionId': selected['id'],
             'id': 'finished-call', 'kind': 'llm', 'phase': phase,
             'startedAt': 10, 'endedAt': 11,
             'usage': {'inputTokens': 12, 'outputTokens': 3}}
    if phase == 'failed':
        event['failure'] = {'category': 'provider', 'errorType': 'FixtureError'}
    await app.on_runtime_event('execution.event', event)

    # Terminal evidence is committed immediately, not left to the progress timer.
    assert not app._progress_dirty
    assert queue.get_nowait()['revision'] == app.state['revision']
    assert encodes == [], 'One completed call must not serialize unrelated configuration'
    assert app.db.execute('SELECT value FROM state WHERE id=1').fetchone()[0] == checkpoint
    # Read through a separate connection before close() can fold the records.
    import sqlite3
    with sqlite3.connect(f'file:{app.data_dir / "app.sqlite3"}?mode=ro', uri=True) as db:
        restored = load(db)
        hydrate(app.data_dir, restored, db)
    saved = next(row for row in restored['sessions'] if row['id'] == selected['id'])
    node = next(row for row in saved['execution']['nodes'] if row['id'] == 'finished-call')
    assert node['phase'] == phase and node['usage'] == event['usage']
    if phase == 'failed':
        assert saved['failure']['errorType'] == 'FixtureError'
    assert restored['runtimeControl'][other['id']] == unrelated
    assert restored['revision'] == app.state['revision']


async def test_terminal_execution_event_flushes_pending_global_changes(app_factory):
    from amplifier_web.state_records import load

    app = app_factory()
    await app.dispatch('session.create', {})
    sid = app.state['selectedSessionId']
    app._state['management'] = {'phase': 'fixture-pending'}
    app._publish_progress()  # Unknown/global writer already awaiting a save.
    await app.on_runtime_event('execution.event', {
        'sessionId': sid, 'id': 'done', 'kind': 'llm', 'phase': 'completed',
        'startedAt': 10, 'endedAt': 11})
    assert not app._progress_dirty
    assert load(app.db)['management'] == {'phase': 'fixture-pending'}
    checkpoint = load_saved_state(app.db)
    assert checkpoint['revision'] == app.state['revision']


@pytest.mark.parametrize('retained', [1, 475])
async def test_bridge_diagnostics_do_not_dirty_or_publish_history(app_factory, monkeypatch, retained):
    app = app_factory()
    from amplifier_web.diagnostics import Diagnostics
    monkeypatch.setattr(app.diagnostics, 'record', Diagnostics.record.__get__(app.diagnostics))
    await app.dispatch('session.create', {})
    sid = app.state['selectedSessionId']
    session = app._session(sid)
    session['workers'] = [{'id': f'old-{i}', 'status': 'completed', 'report': 'x' * 4096}
                          for i in range(retained)]
    session['task'] = {'corrections': 'x' * 1_000_000}
    app._publish_changes(sessions={sid})
    revision = app.state['revision']
    queue = app.subscribe()
    lookup = app._session
    def no_hydrate(identity, *, hydrate=True):
        assert hydrate is False
        return lookup(identity, hydrate=False)
    monkeypatch.setattr(app, '_session', no_hydrate)
    saves = []
    original = app._save
    def save():
        saves.append(True)
        return original()
    monkeypatch.setattr(app, '_save', save)
    for number in range(12):
        await app.on_runtime_event('runtime.bridge', {'sessionId': sid,
            'bridgeId': str(number), 'operation': 'context.manifest', 'phase': 'completed'})
    assert saves == []
    await app._flush_pending_progress()
    assert saves == [] and app.state['revision'] == revision and queue.empty()
    assert not any(row.get('type') == 'runtime.bridge' for row in session.get('runtimeEvents', []))
    assert len(app.diagnostics.bridge_events(session)) == 12
    await app.diagnostics.flush()
    assert len(app.diagnostics.bridge_events(session)) == 12
    assert len(session['workers']) == retained and len(session['task']['corrections']) == 1_000_000
    app.unsubscribe(queue)


async def test_phase_timings_never_hydrate_dirty_or_persist_state(app_factory, monkeypatch):
    from amplifier_web.performance_diagnostics import PhaseTiming
    app = app_factory()
    revision = app.state['revision']
    def forbidden(*args, **kwargs):
        raise AssertionError('Timing observation must not read or write conversation state')
    with monkeypatch.context() as patch:
        patch.setattr(app, '_session', forbidden)
        patch.setattr(app, '_save', forbidden)
        for i in range(140):
            timing = PhaseTiming(kind='host.bridge', bridgeId=str(i))
            timing.mark('start'); timing.mark('end')
            await app.on_runtime_event('runtime.timing', {'sessionId': 'unadmitted', 'timing': {
                **timing.snapshot(), 'secret': 'private',
                'marks': {**timing.marks, 'secret': 1, 'body.start': float('inf'), 'body.end': True}}})
    rows = app.performance_diagnostics.snapshot()['runtimeTimings']
    assert len(rows) == 120 and rows[0]['bridgeId'] == '20'
    assert all(set(row['marks']) == {'start', 'end'} for row in rows)
    assert 'private' not in str(rows)
    rows[-1]['marks']['start'] = 0
    assert app.performance_diagnostics.snapshot()['runtimeTimings'][-1]['marks']['start'] > 0
    assert app.state['revision'] == revision and not getattr(app, '_progress_dirty', False)


@pytest.mark.parametrize('fault', ['none', 'malformed', 'serialize', 'cancel'])
async def test_host_bridge_timing_never_awaits_sink_or_inherits_task(fault, monkeypatch):
    monkeypatch.setattr('amplifier_web.performance_diagnostics.TIMING_ENABLED', True)
    from amplifier_web.performance_diagnostics import PerformanceDiagnostics, active_bridge
    from amplifier_web.runtime import normalize_event
    diagnostics = PerformanceDiagnostics()
    replies = []
    escaped = []
    async def forbidden_sink(*args):
        raise AssertionError('Private host timing must not call the event sink')
    async def bridge(operation, args, sid):
        assert args['_runtimeOwnerId'] == 'fixture-owner'
        timing = active_bridge()
        assert bool(timing) == (fault != 'malformed')
        async def child():
            assert active_bridge() is None
            escaped.append(timing)
        await asyncio.create_task(child())
        if fault == 'cancel':
            raise asyncio.CancelledError()
        return {'exact': True}
    manager = RuntimeManager(bridge, performance_diagnostics=diagnostics)
    row = {'process': SimpleNamespace(returncode=None), 'emit': forbidden_sink,
           'runtimeOwnerId': 'fixture-owner'}
    manager.workers['s'] = row
    async def write(row, value):
        if fault == 'serialize' and 'result' in value:
            raise ValueError('controlled serialization error')
        replies.append(value)
    manager._write = write
    metadata = {'kind': 'bridge', 'bridgeId': 'b', 'marks': {'start': 1}}
    if fault == 'malformed':
        metadata.update(kind=[], outcome={})
        assert normalize_event({'type': 'runtime.timing', 'timing': metadata}, 's')[1]['timing'] == {
            'bridgeId': 'b', 'marks': {'start': 1}}
    call = manager._bridge('s', row, {'id': 'b', 'operation': 'history', 'args': {}, '_timing': metadata})
    if fault == 'cancel':
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(call, 1)
    else:
        await asyncio.wait_for(call, 1)
        assert replies == [{'op': 'bridge.result', 'id': 'b', **(
            {'error': 'controlled serialization error'} if fault == 'serialize' else {'result': {'exact': True}})}]
    rows = diagnostics.snapshot()['runtimeTimings']
    assert len(rows) == (0 if fault == 'malformed' else 1)
    if rows:
        assert rows[0]['outcome'] == {'none': 'completed', 'serialize': 'failed', 'cancel': 'cancelled'}[fault]
        assert not escaped[0].active
    assert active_bridge() is None
