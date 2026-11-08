"""Selected quiet-watch work must not decode unrelated retained evidence."""
from contextlib import contextmanager
import json
import sqlite3
from types import SimpleNamespace

import pytest

from amplifier_web import observation_store
from amplifier_web.observation_store import ObservationStore
from test_observations import fixture, create


def retained(store, count, sid):
    """Both foreign sessions and different watches in the selected session."""
    for index in range(count):
        identity = f'history-{index:05d}'
        owner = sid if index % 2 else 'unrelated-session'
        common = {'id': identity, 'sessionId': owner, 'createdAt': index,
                  'updatedAt': index, 'retainedBody': 'saved evidence ' * 256}
        store.put('watch', {**common, 'status': 'ended', 'requestId': identity,
                            'revision': 1, 'terminal': 'actionable'})
        store.put('run', {**common, 'watchId': identity, 'watchRevision': 1,
                          'phase': 'pending'})
        store.put('outbox', {**common, 'watchId': identity, 'phase': 'unknown'})


def original_rows(store, kind, sid=None, watch_id=None):
    # Oracle is the original primary-key scan and Python filtering, independent
    # of the candidate expression indexes and new selector implementation.
    rows = [json.loads(row[0]) for row in store.db.execute(
        'SELECT body FROM observation_objects INDEXED BY sqlite_autoindex_observation_objects_1 WHERE kind=?', (kind,))]
    return [row for row in rows if (sid is None or row.get('sessionId') == sid)
            and (watch_id is None or row.get('watchId') == watch_id)]


def test_existing_store_gains_indexes_without_rewriting_records_or_order(tmp_path):
    path = tmp_path / 'observations.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE observation_objects (kind TEXT, id TEXT, body TEXT, PRIMARY KEY(kind,id))')
        for identity, phase in [('z', 'waiting_worker'), ('a', 'unknown'), ('m', 'pending')]:
            db.execute('INSERT INTO observation_objects VALUES (?,?,?)', ('outbox', identity,
                       json.dumps({'id': identity, 'sessionId': 'selected', 'watchId': 'watch', 'phase': phase}, indent=2)))
        before = db.execute('SELECT kind,id,body FROM observation_objects ORDER BY kind,id').fetchall()
    store = ObservationStore(path)
    try:
        assert store.db.execute('SELECT kind,id,body FROM observation_objects ORDER BY kind,id').fetchall() == before
        assert store.rows('outbox', 'selected') == original_rows(store, 'outbox', 'selected')
    finally:
        store.close()


@contextmanager
def work(store, monkeypatch):
    counts = {'decodedRows': 0, 'decodedBytes': 0, 'sqlVm': 0, 'statements': 0}
    def decode(value):
        counts['decodedRows'] += 1
        counts['decodedBytes'] += len(value.encode())
        return json.loads(value)
    def progress():
        counts['sqlVm'] += 1
        return 0
    def statement(_):
        counts['statements'] += 1
    with monkeypatch.context() as patch:
        # Replace only this module's binding, not the shared json module used by
        # MCP or other owners. This counts actual store decoding, including retry.
        patch.setattr(observation_store, 'json', SimpleNamespace(loads=decode, dumps=json.dumps))
        store.db.set_progress_handler(progress, 1)
        store.db.set_trace_callback(statement)
        try:
            yield counts
        finally:
            store.db.set_progress_handler(None, 0)
            store.db.set_trace_callback(None)


@pytest.mark.parametrize('history', [0, 100, 1000])
async def test_exact_public_reads_and_retry_ignore_other_watch_history(tmp_path, monkeypatch, history):
    app, _, _, sid, args, *_ = await fixture(tmp_path, monkeypatch)
    try:
        preview = (await app.dispatch('observation.preview', args))['result']
        payload = {**args, 'previewHash': preview['previewHash'], 'requestId': 'selected'}
        watch = (await app.dispatch('observation.create', payload))['result']['watch']
        store = app.observations.store
        for identity in ('z', 'a'):
            store.put('run', {'id': identity, 'sessionId': sid, 'watchId': watch['id'], 'phase': 'pending'})
            store.put('outbox', {'id': identity, 'sessionId': sid, 'watchId': watch['id'], 'phase': 'unknown'})
        retained(store, history, sid)
        expected = {'watch': watch, 'runs': original_rows(store, 'run', sid, watch['id']),
                    'handoffs': original_rows(store, 'outbox', sid, watch['id'])}
        with work(store, monkeypatch) as counts:
            for action in ('observation.read', 'observation.report'):
                assert (await app.dispatch(action, {'sessionId': sid, 'id': watch['id']}))['result'] == expected
            assert (await app.dispatch('observation.request', {'sessionId': sid, 'requestId': 'selected'}))['result'] == {'watch': watch}
            duplicate = (await app.dispatch('observation.create', {**payload, 'previewHash': 'old-preview'}))['result']
            assert duplicate == {'watch': watch, 'duplicate': True}
            assert app.observations.operation_record(sid, 'observation:' + watch['id'])['evidence']['handoffs'] == expected['handoffs']
        print(json.dumps({'operation': 'exact-reads-retry', 'retainedWatches': history, **counts}))
        assert counts['decodedRows'] == 16
        assert counts['decodedBytes'] < 20000
        assert counts['sqlVm'] < 1500
        # Public lists remain complete and in their original order. Their work
        # legitimately grows with this requested session's returned records.
        assert (await app.dispatch('observation.list', {'sessionId': sid}))['result']['watches'] == original_rows(store, 'watch', sid)
    finally:
        await app.close()


@pytest.mark.parametrize('history', [0, 100, 1000])
async def test_tick_ignores_ended_history_but_terminalizes_expired_active_watch(tmp_path, monkeypatch, history):
    app, runtime, now, sid, args, *_ = await fixture(tmp_path, monkeypatch)
    try:
        active = await create(app, args, request='active')
        expired = await create(app, args, request='expired')
        store = app.observations.store
        expired['expiresAt'] = now[0] - 1
        store.put('watch', expired)
        retained(store, history, sid)
        before = store.db.execute("SELECT kind,id,body FROM observation_objects WHERE id LIKE 'history-%' ORDER BY kind,id").fetchall()
        with work(store, monkeypatch) as counts:
            await app.observations.tick()
        assert store.get('watch', active['id'])['status'] == 'active'
        assert store.get('watch', expired['id'])['terminal'] == 'expired'
        runs = original_rows(store, 'run', sid, active['id'])
        assert len(runs) == 1 and runs[0]['phase'] == 'pending'
        assert not original_rows(store, 'run', sid, expired['id'])
        handoff = original_rows(store, 'outbox', sid, expired['id'])
        assert len(handoff) == 1 and handoff[0]['phase'] == 'accepted'
        assert runtime.observation_input.await_count == 1
        assert store.db.execute("SELECT kind,id,body FROM observation_objects WHERE id LIKE 'history-%' ORDER BY kind,id").fetchall() == before
        print(json.dumps({'operation': 'active-tick', 'retainedWatches': history, **counts}))
        assert counts['decodedRows'] < 35
        assert counts['decodedBytes'] < 40000
        assert counts['sqlVm'] < 6000
    finally:
        await app.close()


@pytest.mark.parametrize('history', [0, 100, 1000])
def test_lease_recovery_and_suppression_visit_only_pending_work(tmp_path, monkeypatch, history):
    path = tmp_path / 'observations.sqlite3'
    first, second = ObservationStore(path), ObservationStore(path)
    try:
        retained(first, history, 'session')
        watch = {'id': 'selected', 'sessionId': 'session', 'revision': 1, 'status': 'active',
                 'nextDue': 0, 'sequence': 0, 'intervalSeconds': 15, 'expiresAt': 10000}
        first.put('watch', watch)
        first.acquire(0)
        run = first.claim(watch['id'], 0)
        first.put('outbox', {'id': 'lost', 'watchId': 'selected', 'sessionId': 'session',
                            'phase': 'submitting', 'presentationPhase': 'claimed'})
        first.put('outbox', {'id': 'pending', 'watchId': 'selected', 'sessionId': 'session', 'phase': 'pending'})
        first.put('outbox', {'id': 'waiting', 'watchId': 'selected', 'sessionId': 'session', 'phase': 'waiting_worker'})
        with work(second, monkeypatch) as counts:
            assert second.acquire(61)
            resumed = second.claim(watch['id'], 61)
            second.suppress(watch['id'], 62)
        assert resumed['id'] == run['id'] and resumed['owner'] == second.owner
        assert second.get('outbox', 'lost')['phase'] == 'unknown'
        assert second.get('outbox', 'lost')['presentationPhase'] == 'unknown'
        assert second.get('outbox', 'pending')['phase'] == second.get('outbox', 'waiting')['phase'] == 'suppressed'
        assert all(row['phase'] == 'unknown' for row in original_rows(second, 'outbox') if row['id'].startswith('history-'))
        print(json.dumps({'operation': 'lease-recovery', 'retainedWatches': history, **counts}))
        assert counts['decodedRows'] == 6
        assert counts['decodedBytes'] < 3000
        assert counts['sqlVm'] < 2500
    finally:
        first.close()
        second.close()
