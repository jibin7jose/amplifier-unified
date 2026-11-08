"""Retained task history does not exhaust an assignment's useful lifetime."""
import copy
import json
import tarfile
import time
import uuid

import pytest

from amplifier_web.recovery import backup
from amplifier_web.service import AppError, AppService
from test_assignments import app, limits, start
from test_assignment_tasks import change, create


@pytest.mark.parametrize('terminal', ['completed', 'cancelled'])
async def test_continuation_past_128_preserves_history_retry_restart_and_backup(app, tmp_path, terminal):
    owner = app.state['sessions'][0]
    await start(app)
    # This selected limit governs commissioned roots, not task declarations.
    await limits(app, owner, maxOpenTasks=1)
    originals, identities, previous = [], [], None
    for index in range(132):
        args = {'sessionId': owner['id'], 'expectedAssignmentRevision': app.assignments.current(owner)['revision'],
                'title': 'Work ' + str(index), 'dependsOn': [previous['id']] if previous and terminal == 'completed' else []}
        command = 'lifetime-create-' + str(index)
        result = await app.dispatch('assignment.tasks.create', args, command_id=command)
        task = result['result']['task']
        if index == 0:
            originals.append(('assignment.tasks.create', args, command, copy.deepcopy(result)))
        if terminal == 'completed':
            claim_args = {'sessionId': owner['id'], 'taskId': task['id'], 'expectedRevision': task['revision']}
            claimed = await app.dispatch('assignment.tasks.claim', claim_args, command_id='lifetime-claim-' + str(index))
            task = claimed['result']['task']
            if index == 0:
                originals.append(('assignment.tasks.claim', claim_args, 'lifetime-claim-0', copy.deepcopy(claimed)))
        verb = 'complete' if terminal == 'completed' else 'cancel'
        settled_args = {'sessionId': owner['id'], 'taskId': task['id'], 'expectedRevision': task['revision']}
        if terminal == 'completed':
            settled_args.update(claimId=task['claim']['id'], report='Retained result ' + str(index), references=['source:' + str(index)])
        settled = await app.dispatch('assignment.tasks.' + verb, settled_args, command_id='lifetime-settle-' + str(index))
        previous = settled['result']['task']
        identities.append(previous['id'])
        if index == 0:
            originals.append(('assignment.tasks.' + verb, settled_args, 'lifetime-settle-0', copy.deepcopy(settled)))
    encoded = {command: app.db.execute('SELECT receipt FROM commands WHERE id=?', (command,)).fetchone()[0]
               for _, _, command, _ in originals}
    saved = await backup(app)
    restore_home = tmp_path / 'restore'; restore_home.mkdir()
    with tarfile.open(saved['backup']) as archive:
        archive.extractall(restore_home, filter='data')
    await app.close()
    for home in (app.data_dir, restore_home):
        restored = AppService(home, workspace=owner['workspace'])
        try:
            actor = restored._session(owner['id'])
            observed, after = [], None
            while True:
                page = (await restored.dispatch('assignment.tasks.list', {'sessionId': actor['id'], 'view': 'history',
                    'limit': 16, **({'after': after} if after else {})}))['result']
                observed.extend(task['id'] for task in page['tasks'])
                after = page['nextAfter']
                if after is None:
                    break
            assert observed == identities
            for action, args, command, original in originals:
                repeated = await restored.dispatch(action, args, command_id=command)
                assert repeated['duplicate'] and repeated['historical'] and repeated['result'] == original['result']
                assert restored.db.execute('SELECT receipt FROM commands WHERE id=?', (command,)).fetchone()[0] == encoded[command]
            detail = (await restored.dispatch('assignment.tasks.read', {'sessionId': actor['id'], 'taskId': identities[0]}))['result']['task']
            assert detail['status'] == terminal
            if terminal == 'completed':
                assert detail['completion'] == originals[-1][3]['result']['task']['completion']
                assert detail['claim']['id'] == originals[1][3]['result']['task']['claim']['id']
            with pytest.raises(AppError, match='cannot be changed'):
                await change(restored, actor, 'update', detail, 'do-not-reopen', title='Not a fresh task')
            following = await create(restored, actor, 'Continue after recovery', [previous['id']], 'after-recovery')
            assert following['readiness'] == ('ready' if terminal == 'completed' else 'blocked')
            if terminal == 'cancelled':
                with pytest.raises(AppError, match='blocked'):
                    await change(restored, actor, 'claim', following, 'cancelled-is-not-completed')
            else:
                claim = (await change(restored, actor, 'claim', following, 'after-recovery-claim'))['result']['task']
                await change(restored, actor, 'complete', claim, 'after-recovery-complete', report='Continued successfully')
            value = restored.assignments.current(actor)
            await restored.dispatch('assignment.stop', {'sessionId': actor['id'], 'expectedRevision': value['revision']}, command_id='explicit-stop')
            with pytest.raises(AppError, match='stopped'):
                await create(restored, actor, identity='not-after-stop')
            # Historical retries retain their meaning even after explicit Stop.
            action, args, command, original = originals[0]
            repeated = await restored.dispatch(action, args, command_id=command)
            assert repeated['duplicate'] and repeated['result'] == original['result']
        finally:
            await restored.close()


async def test_new_task_cost_tracks_direct_dependencies_not_retained_history(app, monkeypatch, record_property):
    """Synthetic records isolate fixed-active CREATE/read cost; no provider work."""
    owner = app.state['sessions'][0]
    await start(app)
    prerequisite = await create(app, owner, identity='prerequisite')
    claimed = (await change(app, owner, 'claim', prerequisite, 'prerequisite-claim'))['result']['task']
    prerequisite = (await change(app, owner, 'complete', claimed, 'prerequisite-done', report='Ready'))['result']['task']
    active = await create(app, owner, identity='fixed-active')
    value = app.assignments.current(owner)
    stats, measurements = {}, []
    original = app.assignments.tasks._record
    def record(value, key, encoded):
        stats['decodedRows'] += 1
        stats['decodedTaskBytes'] += len(encoded.encode())
        return original(value, key, encoded)
    monkeypatch.setattr(app.assignments.tasks, '_record', record)
    async def observe(action, args, identity):
        stats.update(decodedRows=0, decodedTaskBytes=0, vmSteps=0)
        statements = []
        def progress():
            stats['vmSteps'] += 1
            return 0
        app.db.set_progress_handler(progress, 1)
        app.db.set_trace_callback(statements.append)
        started = time.perf_counter()
        try:
            result = (await app.dispatch(action, {'sessionId': owner['id'], **args}, command_id=identity))['result']
        finally:
            app.db.set_progress_handler(None, 0)
            app.db.set_trace_callback(None)
        measured = {**stats, 'instrumentedMs': (time.perf_counter() - started) * 1000,
                    'responseBytes': len(json.dumps(result, ensure_ascii=False).encode()),
                    'sqlStatements': len(statements), 'sqlBytes': sum(len(statement.encode()) for statement in statements)}
        return result, measured
    for retained in (0, 256, 1024):
        # Canonical synthetic current records and distinct historical command
        # bodies deliberately share the same assignment and SQLite table.
        for index in range(0 if not measurements else measurements[-1]['retained'], retained):
            task = {**prerequisite, 'id': 'task:' + str(uuid.uuid5(uuid.NAMESPACE_URL, value['id'] + ':retained:' + str(index))),
                    'description': 'd' * 4000, 'createdAt': 100 + index,
                    'completion': {**prerequisite['completion'], 'report': 'r' * 4000, 'references': ['r' * 2000] * 16}}
            app.assignments.tasks.save(value, task, new=True)
            app.db.execute('INSERT INTO commands(id,fingerprint,receipt) VALUES(?,?,?)',
                ('historical-' + str(index), 'synthetic', json.dumps({'commandAction': 'assignment.tasks.complete',
                    'assignmentId': value['id'], 'result': {'task': task}})))
        app.db.commit()
        task, created = await observe('assignment.tasks.create', {'title': 'New independent work', 'dependsOn': [prerequisite['id']],
            'expectedAssignmentRevision': app.assignments.current(owner)['revision']}, 'measured-' + str(retained))
        # Restore fixed unfinished work through the actual transition owner.
        await change(app, owner, 'cancel', task['task'], 'settle-measured-' + str(retained))
        page, paged = await observe('assignment.tasks.list', {}, 'page-' + str(retained))
        detail, exact = await observe('assignment.tasks.read', {'taskId': active['id']}, 'detail-' + str(retained))
        assert [row['id'] for row in page['tasks']] == [active['id']]
        assert detail['task']['id'] == active['id']
        measurements.append({'retained': retained, 'create': created, 'page': paged, 'detail': exact})
    for operation in ('create', 'page', 'detail'):
        small, large = measurements[0][operation], measurements[-1][operation]
        assert large['decodedRows'] == small['decodedRows']
        assert large['decodedTaskBytes'] == small['decodedTaskBytes']
        assert large['vmSteps'] <= small['vmSteps'] + 100
        assert large['sqlStatements'] == small['sqlStatements']
    record_property('lifetime_cost', json.dumps({'measurements': measurements,
        'scope': 'Real AppService/SQLite, synthetic retained bodies, one resident member and one fixed active task. Instrumented component timings; no provider or universal latency bound. Index first-build and member roster cost are separate.'}))
