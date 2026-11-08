"""Current task pages bound observation, without changing work or retry authority."""
import asyncio
import base64
import copy
import json

import pytest

from amplifier_web.assignment_tasks import PAGE_BYTES
from amplifier_web.errors import AppError
from test_assignments import app, child
from test_assignment_task_records import legacy, rows
from test_assignment_tasks import change


async def seed(app, count=20, *, normalized=True):
    owner, value = await legacy(app, count)
    if normalized and count:
        await change(app, owner, 'update', next(iter(value['tasks'].values())), 'normalize', title='First task')
    return owner, app.assignments.current(owner), list(value['tasks'].values())


async def listing(app, owner, **args):
    return (await app.dispatch('assignment.tasks.list', {'sessionId': owner['id'], **args}))['result']


@pytest.mark.parametrize('normalized', [False, True])
async def test_pages_cover_legacy_and_normalized_items_without_mutation(app, normalized):
    owner, value, tasks = await seed(app, normalized=normalized)
    before = rows(app)
    seen, after = [], None
    while True:
        page = await listing(app, owner, **({'after': after} if after else {}))
        assert len(page['tasks']) <= 8
        assert len(json.dumps(page['tasks'], ensure_ascii=False).encode()) <= PAGE_BYTES
        assert page['coverage']['kind'] == 'current-page' and not page['coverage']['changed']
        for task in page['tasks']:
            assert not {'description', 'completion', 'dependsOn', 'blockedBy'} & task.keys()
            assert task['detailRef']['args'] == {'sessionId': owner['id'], 'taskId': task['id']}
        seen.extend(task['id'] for task in page['tasks'])
        after = page['nextAfter']
        if after is None:
            break
    assert seen == [task['id'] for task in tasks]
    assert rows(app) == before
    assert app.assignments.current(owner).get('taskStorage') == value.get('taskStorage')


async def test_exact_detail_reads_only_item_and_direct_off_page_dependencies(app, monkeypatch):
    owner, value, tasks = await seed(app)
    first = app.assignments.tasks.get(value, tasks[0]['id'])
    first.update(status='completed', completion={'report': 'Full result', 'references': ['opaque:' + 'x' * 2000]})
    app.assignments.tasks.save(value, first)
    last = app.assignments.tasks.get(value, tasks[-1]['id'])
    last['dependsOn'] = [first['id']]
    app.assignments.tasks.save(value, last)
    app.db.commit()
    reads, original = [], app.assignments.tasks._record
    def record(value, key, encoded):
        reads.append(key)
        return original(value, key, encoded)
    monkeypatch.setattr(app.assignments.tasks, '_record', record)
    detail = (await app.dispatch('assignment.tasks.read', {'sessionId': owner['id'], 'taskId': last['id']}))['result']
    assert reads == [app.assignments.tasks.key(last['id']), app.assignments.tasks.key(first['id'])]
    assert 'tasks' not in detail and detail['task']['dependsOn'] == [first['id']]
    assert detail['task']['readiness'] == 'ready'
    assert detail['dependencies'] == [{'id': first['id'], 'available': True, 'title': first['title'], 'status': 'completed', 'revision': first['revision']}]
    full = (await app.dispatch('assignment.tasks.read', {'sessionId': owner['id'], 'taskId': first['id']}))['result']['task']
    assert full['completion'] == first['completion']
    history = await listing(app, owner, view='history')
    assert [item['id'] for item in history['tasks']] == [first['id']]
    assert 'completion' not in history['tasks'][0]


async def test_continuation_reports_progress_without_restarting_and_rejects_foreign_scope(app):
    owner, value, tasks = await seed(app)
    first = await listing(app, owner, limit=3)
    after = first['nextAfter']
    await change(app, owner, 'cancel', tasks[5], 'cancel-between-pages')
    second = await listing(app, owner, limit=3, after=after)
    assert second['coverage']['changed']
    assert [task['id'] for task in second['tasks']] == [tasks[i]['id'] for i in [3, 4, 6]]
    with pytest.raises(AppError, match='cursor'):
        await listing(app, owner, view='history', after=after)
    peer, _ = await child(app, owner)
    with pytest.raises(AppError, match='cursor'):
        await listing(app, peer, after=after)
    for token in ['invalid!', base64.urlsafe_b64encode(json.dumps([1, 'foreign', 1, 0, 'task']).encode()).decode()]:
        with pytest.raises(AppError, match='cursor'):
            await listing(app, owner, after=token)


async def test_byte_fit_cursor_covers_exact_retained_prefix_and_equal_timestamps(app):
    owner, value, tasks = await seed(app)
    for task in tasks:
        task.update(createdAt=1, title='\U0001f330' * 200, blockedReason='\U0001f330' * 1000)
        app.assignments.tasks.save(value, task)
    app.db.commit()
    expected = sorted(task['id'] for task in tasks)
    page = await listing(app, owner, limit=16)
    assert 0 < len(page['tasks']) < 16 and page['nextAfter']
    assert all(task['blockedReasonTruncated'] for task in page['tasks'])
    actual = [task['id'] for task in page['tasks']]
    while page['nextAfter']:
        page = await listing(app, owner, limit=16, after=page['nextAfter'])
        actual.extend(task['id'] for task in page['tasks'])
    assert actual == expected


async def test_same_page_wait_rechecks_current_scope_and_cancellation_is_passive(app):
    owner, value, tasks = await seed(app)
    first = await listing(app, owner, limit=3)
    page = await listing(app, owner, limit=3, after=first['nextAfter'])
    args = {'sessionId': owner['id'], 'limit': 3, 'after': first['nextAfter'], 'afterVersion': page['version'], 'waitMs': 1000}
    waiting = asyncio.create_task(app.dispatch('assignment.tasks.wait', args))
    await asyncio.sleep(0)
    await change(app, owner, 'cancel', tasks[3], 'cancel-visible')
    changed = await waiting
    assert changed['changed'] and changed['result']['tasks'][0]['id'] == tasks[4]['id']
    before = rows(app)
    args['afterVersion'] = changed['result']['version']
    waiting = asyncio.create_task(app.dispatch('assignment.tasks.wait', args))
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert rows(app) == before


async def test_current_control_reference_is_caller_scoped_and_historical_result_stays_exact(app):
    owner = app.state['sessions'][0]
    peer, _ = await child(app, owner)
    args = {'sessionId': owner['id'], 'expectedRevision': app.assignments.current(owner)['revision'], 'maxOpenTasks': 7}
    receipt = await app.dispatch('assignment.set', args, command_id='saved-control')
    historical = copy.deepcopy(app.collaboration.receipt('saved-control'))
    # Model a prior-version immutable receipt without rewriting current authority.
    historical['result']['assignment'].pop('taskListRef')
    historical['result']['assignment']['tasks'] = {'old-task': {'id': 'old-task', 'completion': {'report': 'Original'}}}
    app.collaboration.save('saved-control', historical)
    app.db.commit()
    encoded = app.db.execute("SELECT receipt FROM commands WHERE id='saved-control'").fetchone()[0]
    for actor in (owner, peer):
        current = (await app.dispatch('assignment.read', {'sessionId': actor['id']}))['result']['assignment']
        assert 'tasks' not in current and 'taskStorage' not in current
        assert current['taskListRef']['args'] == {'sessionId': actor['id']}
    repeated = await app.dispatch('assignment.set', args, command_id='saved-control')
    assert repeated['duplicate'] and repeated['result'] == historical['result']
    assert app.db.execute("SELECT receipt FROM commands WHERE id='saved-control'").fetchone()[0] == encoded
    assert 'tasks' not in receipt['result']['assignment']


async def test_active_page_and_exact_read_do_not_scan_retained_terminal_bodies(app, monkeypatch, record_property):
    """Synthetic canonical records isolate page work from retained task bodies."""
    import time
    import uuid
    owner, value, tasks = await seed(app, count=8)
    stats = {}
    original = app.assignments.tasks._record
    def record(value, key, encoded):
        stats['decodedRows'] += 1
        stats['decodedTaskBytes'] += len(encoded.encode())
        return original(value, key, encoded)
    monkeypatch.setattr(app.assignments.tasks, '_record', record)
    async def observe(action, args):
        stats.update(decodedRows=0, decodedTaskBytes=0, vmSteps=0)
        def progress():
            stats['vmSteps'] += 1
            return 0
        app.db.set_progress_handler(progress, 1)
        started = time.perf_counter()
        try:
            result = (await app.dispatch(action, {'sessionId': owner['id'], **args}))['result']
        finally:
            app.db.set_progress_handler(None, 0)
        return result, {**stats, 'instrumentedMs': (time.perf_counter() - started) * 1000,
            'responseBytes': len(json.dumps(result, ensure_ascii=False).encode()),
            'taskPayloadBytes': len(json.dumps(result.get('tasks', result.get('task')), ensure_ascii=False).encode())}
    before, small = await observe('assignment.tasks.list', {})
    detail_before, small_detail = await observe('assignment.tasks.read', {'taskId': tasks[0]['id']})
    for index in range(120):
        identity = 'task:' + str(uuid.uuid5(uuid.NAMESPACE_URL, value['id'] + ':historical:' + str(index)))
        task = {**tasks[0], 'id': identity, 'createdAt': 100 + index, 'status': 'completed',
            'description': 'd' * 4000, 'completion': {'report': 'r' * 4000, 'references': ['r' * 2000] * 16,
            'reportedBy': owner['id'], 'reportedAt': 100 + index, 'verification': 'participant_report'}}
        app.assignments.tasks.save(value, task, new=True)
    # Old command bodies share the table but cannot become current task candidates.
    for index in range(512):
        app.db.execute('INSERT INTO commands(id,fingerprint,receipt) VALUES(?,?,?)',
            ('historical-receipt-' + str(index), 'synthetic', json.dumps({'commandAction': 'assignment.tasks.complete',
                'assignmentId': value['id'], 'result': {'task': {'report': 'x' * 4000}}})))
    app.db.commit()
    after, large = await observe('assignment.tasks.list', {})
    detail_after, large_detail = await observe('assignment.tasks.read', {'taskId': tasks[0]['id']})
    assert after == before and detail_after == detail_before
    assert large['decodedRows'] == small['decodedRows'] == 8
    assert large_detail['decodedRows'] == small_detail['decodedRows'] == 1
    assert large['decodedTaskBytes'] == small['decodedTaskBytes']
    assert large_detail['decodedTaskBytes'] == small_detail['decodedTaskBytes']
    assert large['vmSteps'] <= small['vmSteps'] + 100
    assert large_detail['vmSteps'] <= small_detail['vmSteps'] + 100
    sql = app.db.execute("SELECT sql FROM sqlite_master WHERE name='assignment_task_pages'").fetchone()[0]
    app.db.execute('DROP INDEX assignment_task_pages')
    index_steps = 0
    def progress():
        nonlocal index_steps
        index_steps += 1
        return 0
    app.db.set_progress_handler(progress, 1)
    started = time.perf_counter()
    app.db.execute(sql)
    first_index = {'vmSteps': index_steps, 'instrumentedMs': (time.perf_counter() - started) * 1000}
    index_steps = 0
    started = time.perf_counter()
    app.db.execute(sql.replace('CREATE INDEX ', 'CREATE INDEX IF NOT EXISTS ', 1))
    warm_index = {'vmSteps': index_steps, 'instrumentedMs': (time.perf_counter() - started) * 1000}
    app.db.set_progress_handler(None, 0)
    record_property('page_cost', json.dumps({'smallPage': small, 'retainedPage': large,
        'smallDetail': small_detail, 'retainedDetail': large_detail, 'firstIndexBuild': first_index,
        'existingIndexOpen': warm_index, 'active': 8, 'terminal': 120, 'historicalReceipts': 512,
        'scope': 'Real AppService/SQLite with synthetic records, resident fixed roster; instrumented component timings, no provider/runtime or universal latency claim.'}))
