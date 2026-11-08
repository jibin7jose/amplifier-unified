"""Lazy task records retain authority, exact results and SQLite recovery."""
import copy
import asyncio
import json
import sqlite3
import tarfile
import time
import uuid

import pytest

from amplifier_operations.coordination import fingerprint
from amplifier_web.assignment_tasks import ASSIGNMENT_RECORD, RECORD, STORAGE
from amplifier_web.service import AppError, AppService
from test_assignments import app, start
from test_assignment_tasks import create, change


def task_count(app, value):
    return app.db.execute("""SELECT count(*) FROM commands WHERE json_extract(receipt,'$.assignmentId')=?
        AND json_extract(receipt,'$.commandAction')='assignment.task.record'""", (value['id'],)).fetchone()[0]


def rows(app):
    return list(app.db.execute('SELECT id,fingerprint,receipt FROM commands ORDER BY id'))


async def legacy(app, count=1):
    owner = app.state['sessions'][0]
    await start(app)
    value = app.assignments.current(owner)
    value['tasks'] = {}
    for index in range(count):
        identity = 'task:' + str(uuid.uuid5(uuid.NAMESPACE_URL, value['id'] + ':' + str(index)))
        value['tasks'][identity] = {'id': identity, 'revision': 1, 'status': 'open',
            'title': 'Retained item ' + str(index), 'description': 'Original detail',
            'dependsOn': [], 'blockedReason': '', 'createdBy': owner['id'],
            'createdAt': index + 1, 'updatedAt': index + 1}
    app.assignments.save(value)
    app._publish_changes(sessions={owner['id']})
    return owner, copy.deepcopy(value)


@pytest.mark.parametrize('count', [0, 1, 128])
async def test_lazy_conversion_keeps_paged_order_and_legacy_receipts(app, count):
    owner, before = await legacy(app, count)
    commands = rows(app)
    observed, after = [], None
    while True:
        page = (await app.dispatch('assignment.tasks.list', {'sessionId': owner['id'], **({'after': after} if after else {})}))['result']
        observed.extend(task['id'] for task in page['tasks'])
        after = page['nextAfter']
        if after is None:
            break
    assert observed == list(before['tasks'])
    assert rows(app) == commands  # Passive read does not convert.
    if count:
        first = next(iter(before['tasks'].values()))
        result = await change(app, owner, 'update', first, 'convert', title='Updated title')
    else:
        result = {'result': {'task': await create(app, owner, identity='convert')}}
    current = app.assignments.current(owner)
    assert current['taskStorage'] == STORAGE and 'tasks' not in current
    assert app.collaboration.receipt(current['id'])['commandAction'] == ASSIGNMENT_RECORD
    projected = (await app.dispatch('assignment.read', {'sessionId': owner['id']}))['result']['assignment']
    assert 'taskStorage' not in projected
    assert 'tasks' not in projected
    assert projected['taskListRef'] == {'action': 'assignment.tasks.list', 'args': {'sessionId': owner['id']}}
    for identity, task in before['tasks'].items():
        if identity != result['result']['task']['id']:
            assert app.assignments.tasks.get(current, identity) == task
    # Historical start receipt and every other previous command remain exact.
    after = dict((identity, (digest, receipt)) for identity, digest, receipt in rows(app))
    for identity, digest, receipt in commands:
        if identity != current['id']:
            assert after[identity] == (digest, receipt)
    if count == 128:
        await create(app, owner, identity='continue-past-old-cap')
        assert task_count(app, current) == 129


@pytest.mark.parametrize('fault', ['item-insert', 'header-save', 'before-commit', 'after-commit'])
async def test_conversion_mutation_and_receipt_are_one_recoverable_transaction(app, monkeypatch, fault):
    owner, before = await legacy(app, 2)
    task = next(iter(before['tasks'].values()))
    original_rows = rows(app)
    with monkeypatch.context() as patch:
        if fault == 'item-insert':
            original = app.assignments.tasks.save
            def fail(*args, **kwargs):
                original(*args, **kwargs)
                raise OSError('Interrupted after item insert')
            patch.setattr(app.assignments.tasks, 'save', fail)
        elif fault == 'header-save':
            original = app.assignments.save
            def fail(*args, **kwargs):
                original(*args, **kwargs)
                raise OSError('Interrupted after header switch')
            patch.setattr(app.assignments, 'save', fail)
        else:
            original = app._publish_changes
            def fail(**kwargs):
                if fault == 'after-commit': original(**kwargs)
                raise OSError('Interrupted at publication')
            patch.setattr(app, '_publish_changes', fail)
        with pytest.raises(OSError):
            await change(app, owner, 'claim', task, 'claim-on-conversion')
    if fault != 'after-commit':
        assert rows(app) == original_rows
        assert app.assignments.current(owner) == before
    result = await change(app, owner, 'claim', task, 'claim-on-conversion')
    assert bool(result.get('duplicate')) is (fault == 'after-commit')
    current = app.assignments.current(owner)
    assert task_count(app, current) == 2
    assert app.assignments.tasks.get(current, task['id'])['revision'] == 2
    again = await change(app, owner, 'claim', task, 'claim-on-conversion')
    assert again['duplicate'] and again['result'] == result['result']


async def test_historical_retry_does_not_convert_or_refresh_original_result(app):
    owner, value = await legacy(app)
    task = next(iter(value['tasks'].values()))
    args = {'sessionId': owner['id'], 'taskId': task['id'], 'expectedRevision': 1}
    receipt = {'accepted': True, 'commandAction': 'assignment.tasks.claim',
               'assignmentId': value['id'], 'result': {'task': {'id': task['id'], 'claim': {'id': 'historical'}}}}
    app.collaboration.insert('historical', fingerprint(['assignment.tasks.claim', args, 'ui', owner['id']]), receipt)
    app.db.commit()
    before = rows(app)
    result = await app.dispatch('assignment.tasks.claim', args, command_id='historical')
    assert result['historical'] and result['result'] == receipt['result']
    assert rows(app) == before and 'taskStorage' not in app.assignments.current(owner)


@pytest.mark.parametrize('collision', ['foreign-command', 'other-assignment', 'same-item'])
async def test_conversion_never_overwrites_a_conflicting_record_key(app, collision):
    owner, value = await legacy(app)
    task = next(iter(value['tasks'].values()))
    record = {'commandAction': RECORD, 'assignmentId': value['id'], 'result': task}
    if collision == 'foreign-command': record['commandAction'] = 'unrelated.effect'
    if collision == 'other-assignment': record['assignmentId'] = 'other'
    app.collaboration.insert(app.assignments.tasks.key(task['id']), 'existing', record)
    app.db.commit()
    before = rows(app)
    with pytest.raises(AppError, match='key is already in use'):
        await change(app, owner, 'claim', task, 'conflict')
    assert rows(app) == before


@pytest.mark.parametrize('layout', ['mixed', 'unknown', 'marker-with-v1'])
async def test_unknown_or_mixed_layout_is_never_an_empty_board(app, layout):
    owner, value = await legacy(app)
    value['taskStorage'] = STORAGE if layout != 'unknown' else 'future-format'
    if layout != 'mixed': value.pop('tasks')
    receipt = {'commandAction': 'assignment.record' if layout == 'marker-with-v1' else ASSIGNMENT_RECORD,
               'result': value}
    app.collaboration.save(value['id'], receipt)
    app.db.commit()
    before = rows(app)
    with pytest.raises(AppError, match='assignment record is unavailable'):
        app.assignments.current(owner)
    assert rows(app) == before


async def test_exact_lookup_rejects_foreign_record_and_old_reader_refuses_converted_header(app):
    owner, value = await legacy(app)
    task = next(iter(value['tasks'].values()))
    await change(app, owner, 'claim', task, 'convert')
    current = app.assignments.current(owner)
    before = rows(app)
    # Exact pre-conversion Assignments.load boundary (b77578e4d). A host may
    # start; its old reader refuses this assignment rather than an empty map.
    def old_load(identity):
        receipt = app.collaboration.receipt(identity)
        if receipt.get('commandAction') != 'assignment.record':
            app.collaboration.error('The assignment record is unavailable.', 409)
        return receipt['result']
    with pytest.raises(AppError, match='assignment record is unavailable'):
        old_load(value['id'])
    assert rows(app) == before
    assert app.assignments.tasks.get({**current, 'id': 'foreign-assignment'}, task['id']) is None


@pytest.mark.parametrize('normalized', [False, True])
async def test_foreign_task_stays_absent_at_public_task_and_work_boundaries(app, normalized):
    from test_workspace_work import create as create_work, change_work
    owner, value = await legacy(app)
    task = next(iter(value['tasks'].values()))
    if normalized:
        await change(app, owner, 'update', task, 'convert', title='Kept item')
    other = app.state['sessions'][1]
    app.assignments.ensure(other, 'independent')
    from test_collaborative_workspaces import generation
    await generation(app, other, 'other-generation', ['other-input'])
    if normalized:
        await create(app, other, identity='other-task')
    item = (await create_work(app, other, command='other-work'))['result']['item']
    before = rows(app)
    with pytest.raises(AppError, match='Task not found') as error:
        await change(app, other, 'update', task, 'foreign-update', title='Not mine')
    assert error.value.status == 404
    with pytest.raises(AppError, match='must belong to both') as error:
        await change_work(app, other, 'link', item, 'foreign-link', linkedSessionId=other['id'],
            role='executor', candidate='b' * 40, taskId=task['id'], claimId='foreign-claim')
    assert error.value.status == 403
    assert rows(app) == before


async def test_matching_reader_restart_and_actual_backup_preserve_claim_and_exact_retry(app, tmp_path):
    from amplifier_web.recovery import backup
    owner, value = await legacy(app, 2)
    task = next(iter(value['tasks'].values()))
    result = await change(app, owner, 'claim', task, 'backup-claim')
    expected = app.assignments.project(app.assignments.current(owner), session_id=owner['id'])
    command_rows = rows(app)
    saved = await backup(app)
    restore_home = tmp_path / 'restore'; restore_home.mkdir()
    with tarfile.open(saved['backup']) as archive:
        # Restore the actual private backup, including canonical resources
        # referenced by the shared SQLite snapshot.
        archive.extractall(restore_home, filter='data')
    await app.close()
    for home in (app.data_dir, restore_home):
        restored = AppService(home, workspace=owner['workspace'])
        try:
            actor = restored._session(owner['id'])
            assert restored.assignments.project(restored.assignments.current(actor), session_id=actor['id']) == expected
            repeated = await change(restored, actor, 'claim', task, 'backup-claim')
            assert repeated['duplicate'] and repeated['result'] == result['result']
            actual = dict((identity, (digest, receipt)) for identity, digest, receipt in rows(restored))
            assert all(actual[identity] == (digest, receipt) for identity, digest, receipt in command_rows)
        finally:
            await restored.close()


async def test_dependency_edit_reads_only_reachable_items_and_header_never_reembeds(app, monkeypatch):
    owner, value = await legacy(app, 8)
    first, second, third, *unrelated = value['tasks'].values()
    await change(app, owner, 'update', first, 'convert', dependsOn=[second['id']])
    current = app.assignments.current(owner)
    seen = []
    original = app.assignments.tasks.get
    def tracked(assignment, identity):
        seen.append(identity)
        return original(assignment, identity)
    monkeypatch.setattr(app.assignments.tasks, 'get', tracked)
    with pytest.raises(AppError, match='cycle'):
        await change(app, owner, 'update', second, 'cycle', dependsOn=[first['id']])
    assert set(seen) <= {first['id'], second['id']}
    seen.clear()
    await change(app, owner, 'update', third, 'title-only', title='Edited title')
    assert set(seen) == {third['id']}
    current['integration'] = {'responsibleSessionId': owner['id']}
    app.assignments.save(current)  # Existing non-task writers preserve v2.
    assert app.collaboration.receipt(value['id'])['commandAction'] == ASSIGNMENT_RECORD
    assert 'tasks' not in app.assignments.current(owner)


async def test_stale_claim_review_keeps_legacy_order_and_exact_total(app):
    from amplifier_web.workspace_review import _claims
    owner, value = await legacy(app, 12)
    peer = app.state['sessions'][1]
    peer['assignmentId'] = value['id']
    value['members'][peer['id']] = {'status': 'bound', 'role': 'helper'}
    for index, task in enumerate(value['tasks'].values()):
        holder = owner if index % 2 else peer
        task.update(status='claimed', claim={'id': 'claim-' + str(index), 'sessionId': holder['id'],
                                             'interruptionRevision': 0})
    app.assignments.save(value)
    owner['interruptionRevision'] = peer['interruptionRevision'] = 1
    lookup = {row['id']: row for row in (owner, peer)}.get
    before = _claims(app, owner, lookup)
    # The first supported task mutation performs conversion. Release one claim
    # so both legacy and normalized oracles observe the same remaining set.
    task = next(iter(value['tasks'].values()))
    await change(app, owner, 'release', task, 'convert-claims')
    expected = [task for task in before['items'] if task['taskId'] != next(iter(value['tasks']))]
    after = _claims(app, owner, lookup)
    assert after['total'] == 11 and after['truncated'] and len(after['items']) == 8
    assert after['items'][:7] == expected
    assert [row['taskId'] for row in after['items']] == list(value['tasks'])[1:9]


async def test_scoped_queries_ignore_terminal_bodies_and_index_build_cost_is_explicit(app, monkeypatch, record_property):
    from amplifier_web.assignment_tasks import AssignmentTasks
    owner, value = await legacy(app)
    task = next(iter(value['tasks'].values()))
    await change(app, owner, 'claim', task, 'convert')
    current = app.assignments.current(owner)
    def measure(operation):
        steps = 0
        def progress():
            nonlocal steps
            steps += 1
            return 0
        app.db.set_progress_handler(progress, 1)
        begin = time.perf_counter()
        try: result = operation()
        finally: app.db.set_progress_handler(None, 0)
        return result, {'vmSteps': steps, 'seconds': time.perf_counter() - begin}
    def read_exact():
        header = app.assignments.current(owner)
        return (app.assignments.tasks.get(header, task['id']),
                app.assignments.tasks.unfinished_claims(header, owner['id']))
    before, small = measure(read_exact)
    # Same assignment and one current claim, with 127 larger terminal bodies.
    # Synthetic records isolate the indexed claim/read boundary.
    for index in range(127):
        terminal = {**task, 'id': 'terminal-' + str(index), 'status': 'completed',
                    'completion': {'report': 'x' * 4000, 'references': ['y' * 2000] * 16}}
        app.assignments.tasks.save(current, terminal, new=True)
    for index in range(2048):
        app.collaboration.insert('unrelated-history-' + str(index), 'fixture',
            {'commandAction': 'historical.fixture', 'assignmentId': 'unrelated', 'result': 'z' * 1000})
    app.db.commit()
    decoded = []
    original = json.loads
    def track(encoded, *args, **kwargs):
        decoded.append(len(encoded))
        return original(encoded, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(json, 'loads', track)
        after, large = measure(read_exact)
    assert after == before and large['vmSteps'] <= small['vmSteps'] + 20
    assert len(decoded) == 3 and max(decoded) < 4000
    app.db.execute('DROP INDEX assignment_task_claims')
    _, first_build = measure(lambda: AssignmentTasks(app.assignments))
    _, warm = measure(lambda: AssignmentTasks(app.assignments))
    assert first_build['vmSteps'] > warm['vmSteps'] * 10
    assert read_exact() == before
    record_property('task_record_work', json.dumps({'small': small, 'large': large,
        'decodedBytes': sum(decoded), 'terminalItems': 127, 'unrelatedCommands': 2048,
        'firstIndexBuild': first_build, 'existingIndexStartup': warm}))


async def test_sqlite_backup_observes_whole_old_or_new_conversion(app, tmp_path):
    from amplifier_web.recovery import _backup_files
    owner, value = await legacy(app, 2)
    before = rows(app)
    # Keep an actual SQLite conversion transaction uncommitted while the
    # existing backup owner snapshots from its separate read connection.
    current = app.assignments.current(owner)
    app.assignments.tasks.convert(current)
    app.assignments.save(current)
    assert app.db.in_transaction
    old = await asyncio.to_thread(_backup_files, app.data_dir, [])
    app.db.commit()
    after = rows(app)
    new = await asyncio.to_thread(_backup_files, app.data_dir, [])
    for index, (snapshot, expected) in enumerate(((old, before), (new, after))):
        destination = tmp_path / ('snapshot-' + str(index) + '.sqlite3')
        with tarfile.open(snapshot['backup']) as archive, archive.extractfile('app.sqlite3') as source:
            destination.write_bytes(source.read())
        with sqlite3.connect(destination) as db:
            assert list(db.execute('SELECT id,fingerprint,receipt FROM commands ORDER BY id')) == expected


async def test_explicit_conversation_reset_clears_normalized_items_with_commands(app):
    from amplifier_web.management import Management
    from amplifier_web.recovery import reset
    owner, value = await legacy(app)
    await change(app, owner, 'claim', next(iter(value['tasks'].values())), 'convert')
    assert task_count(app, app.assignments.current(owner)) == 1
    await reset(Management(app), {'parts': ['conversations'], 'apply': True, 'confirmation': 'RESET'})
    assert not app.db.execute('SELECT 1 FROM commands').fetchone()


async def test_oversized_legacy_map_remains_unavailable_for_conversion(app):
    owner, before = await legacy(app, 129)
    commands = rows(app)
    with pytest.raises(AppError, match='legacy assignment task map'):
        await create(app, owner, identity='not-a-supported-legacy-map')
    assert rows(app) == commands
    assert app.assignments.current(owner) == before
