"""Current observation receipts join their owners once, without nested scans."""
import json
import time

from amplifier_web.history_directory_observations import revision, visible_ids, digest
from test_history_directory_exact_ids import native_fixture


def reference_receipts(directory, state):
    sql, params = directory.effective(state, fields={'id'}, notice_values=True)
    rows = [tuple(row) for row in directory.db.execute(sql +
        ' SELECT n.owner,n.key,n.fingerprint FROM directory_notices n JOIN json_each(?) r '
        'ON r.key=n.key AND r.value=n.fingerprint ORDER BY n.owner,n.key,n.fingerprint',
        [*params, json.dumps(state['attentionRead'])])]
    visible = visible_ids(directory, state, (row[0] for row in rows), notices=True)
    rows = [row for row in rows if row[0] in visible]
    roots = visible_ids(directory, state, (row[0] for row in rows), roots=True)
    return rows, [row for row in rows if row[0] in roots]


def test_changed_observations_do_not_repeat_owner_resolution(tmp_path):
    measurements = []
    for count in (50, 600):
        path = tmp_path / str(count)
        path.mkdir()
        state, directory, _, project, workspace = native_fixture(path, 27000, 0)
        rows = [{'id': f'owner-{i}', 'title': f'Owner {i}', 'status': 'idle',
                 'workspace': str(workspace), 'nativeProject': project,
                 'nativeIdentity': f'saved-{i:05d}', 'historyManaged': True,
                 'completion': {'id': f'finished-{i}'}} for i in range(count)]
        directory.sync(rows, state)
        directory.db.commit()
        state['sessions'] = rows[:6]
        state['attentionRead'] = dict(directory.db.execute(
            'SELECT key,fingerprint FROM history_directory_notices'))
        state['attentionRead']['absent'] = 'not a current notice'
        expected, roots = reference_receipts(directory, state)
        steps = 0
        def progress():
            nonlocal steps
            steps += 1000
            return 0
        directory.db.set_progress_handler(progress, 1000)
        started = time.thread_time_ns()
        try:
            # Actual public changes deliberately invalidate warm revision reuse.
            for turn in range(3):
                rows[0]['title'] = f'Changed {turn}'
                result = revision(directory, state)
                assert result['attention'][-2] == digest(expected)
                assert result['workspace'][-2] == digest(roots)
        finally:
            directory.db.set_progress_handler(None, 0)
        measurements.append({'owners': count, 'vm': steps,
                             'cpuMs': (time.thread_time_ns() - started) / 1e6})
        assert len(state['sessions']) == 6
        assert directory.db.execute('SELECT count(*) FROM history_local_rows').fetchone()[0] == count
    print(json.dumps({'observationReceiptCost': measurements}))
    # Twelve times as many current receipts permits linear aggregation plus
    # fixed setup, not a receipt scan for each notice or each bounded ID batch.
    assert measurements[1]['vm'] < measurements[0]['vm'] * 20
    # 600 receipts, six admitted owners, 27k unrelated native rows and three
    # changed observations fit below 600k VM instructions. The prior batched
    # owner resolutions used 1.36M on this exact fixture.
    assert measurements[1]['vm'] < 600_000


def test_receipt_scope_and_fingerprints_match_canonical_visibility(tmp_path):
    state, directory, catalog, project, workspace = native_fixture(tmp_path, 8, 0)
    rows = [{'id': f'owner-{i}', 'title': f'Owner {i}', 'status': 'idle',
             'nativeIdentity': f'saved-{i:05d}', 'nativeProject': project,
             'historyManaged': True, 'completion': {'id': f'finished-{i}'}}
            for i in range(5)]
    directory.sync(rows, state)
    directory.db.commit()
    state['sessions'] = rows[:1]
    state['attentionRead'] = dict(directory.db.execute(
        'SELECT key,fingerprint FROM history_directory_notices'))
    from test_history_directory_exact_ids import change_native
    for kind in ('root', 'worker', 'internal'):
        changed = catalog.get(project, 'saved-00002')
        changed['sessionKind'] = kind
        change_native(catalog, project, workspace, [changed])
        for hidden in ([], [directory.db.execute(
                'SELECT hidden_id FROM history_local_rows WHERE id=?', ('owner-3',)).fetchone()[0]]):
            state['hiddenNativeSessions'] = hidden
            expected, roots = reference_receipts(directory, state)
            actual = revision(directory, state)
            assert actual['attention'][-2] == digest(expected)
            assert actual['root_navigation'][-2] == digest(roots)
    # A stale receipt cannot acknowledge a changed completion.
    rows[0]['completion']['id'] = 'new-completion'
    expected, roots = reference_receipts(directory, state)
    assert all(row[0] != 'owner-0' for row in expected)
    actual = revision(directory, state)
    assert actual['attention'][-2] == digest(expected)
    assert actual['workspace'][-2] == digest(roots)
