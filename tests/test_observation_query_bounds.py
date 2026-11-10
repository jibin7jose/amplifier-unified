"""Observation pages and revisions must not expand an unrelated history library."""
import json

import pytest

from amplifier_web.history_directory_observations import digest, revision
from test_history_directory_navigation import observation_fixture


@pytest.mark.parametrize('size', [0, 63, 64, 65, 128, 129])
def test_pages_find_terminal_boundary_without_directory_counts(tmp_path, monkeypatch, size):
    complete, directory, observations = observation_fixture(tmp_path, size)
    statements, readers = [], []
    original = directory.read_snapshot
    def reader(state):
        value = original(state)
        value.db.set_trace_callback(statements.append)
        readers.append(value)
        return value
    monkeypatch.setattr(directory, 'read_snapshot', reader)
    params = {'scope': 'all', 'limit': '64'}
    found = []
    while True:
        page = observations.page('sessions', params)
        found.extend(row['id'] for row in page['items'])
        if not page['nextCursor']:
            break
        params = {**params, 'cursor': page['nextCursor']}
    assert found == [row['id'] for row in complete['sessions']]
    assert not observations.leases and not observations.snapshots
    assert all(value._closed for value in readers)
    counts = observations.activity('all')
    assert counts['status'] == 'ready'
    assert counts['executionCount'] == sum(row['status'] == 'working' for row in complete['sessions'])
    assert all(value._closed for value in readers)
    assert not any('SELECT COUNT(*) FROM directory_rows' in sql for sql in statements)
    observations.close()


def test_lookahead_does_not_decode_next_page_and_backwards_seek_still_works(tmp_path):
    from amplifier_web.client_observations import DirectoryFacts
    _, directory, observations = observation_fixture(tmp_path, 129)
    reader = directory.read_snapshot(observations.service._state)
    decoded = []
    original = reader.decode
    reader.decode = lambda row: (decoded.append(row), original(row))[1]
    facts = DirectoryFacts(observations, reader, reader._frozen_state)
    assert facts[63][0]['id'] == 's63' and len(decoded) == 64
    assert facts.has(64) and len(decoded) == 64
    assert facts[64][0]['id'] == 's64' and len(decoded) == 128
    assert not facts.has(129)
    assert facts[128][0]['id'] == 's128'
    assert facts[0][0]['id'] == 's0'
    assert not facts.has(5000)
    assert facts[128][0]['id'] == 's128'
    facts.close()
    assert facts.raw_block is None and facts.block == [] and reader._closed


def test_batched_revision_matches_public_pins_and_current_visible_notice_facts(tmp_path, monkeypatch):
    complete, directory, observations = observation_fixture(tmp_path, 150)
    for row in complete['sessions']:
        row['manualUnread'] = 'notice-' + row['id']
    complete['sessions'][9]['sessionKind'] = 'internal'
    directory.sync(complete['sessions'], complete)
    directory.db.execute("INSERT INTO history_directory_notices VALUES('missing-owner',0,'unread:orphan','orphan',0,'{}')")
    directory.db.commit()
    state = observations.service._state
    state['pinnedSessionIds'] = [row['id'] for row in complete['sessions']] + ['unknown']
    hidden = directory.db.execute("SELECT hidden_id FROM history_local_rows WHERE id='s5'").fetchone()[0]
    state['hiddenNativeSessions'] = [hidden]
    state['attentionRead'] = dict(directory.db.execute('SELECT key,fingerprint FROM history_directory_notices'))
    state['attentionRead']['unread:s3'] = 'stale-fingerprint'
    state['attentionRead']['unknown'] = 'unrelated-receipt'
    sql, params = directory.effective(state)
    expected_pins = [row[0] for row in directory.db.execute(sql +
        ' SELECT id FROM directory_rows WHERE id IN (SELECT value FROM json_each(?)) ORDER BY id',
        [*params, json.dumps(state['pinnedSessionIds'])])]
    expected_read = [tuple(row) for row in directory.db.execute(sql +
        ' SELECT n.owner,n.key,n.fingerprint FROM directory_notices n JOIN json_each(?) r ON r.key=n.key AND r.value=n.fingerprint '
        "WHERE EXISTS(SELECT 1 FROM directory_rows d WHERE d.id=n.owner AND d.session_kind!='internal') ORDER BY n.owner,n.key,n.fingerprint",
        [*params, json.dumps(state['attentionRead'])])]
    monkeypatch.setattr(directory, 'decode', lambda *_: pytest.fail('revision decoded dormant metadata'))
    result = revision(directory, state)
    assert result['sessions'][4] == digest(expected_pins)
    assert result['attention'][4] == digest(expected_read)
    assert 's5' not in expected_pins and 'unknown' not in expected_pins
    assert len(expected_pins) > 128  # Exercise more than one identity batch.
    assert not {'s3', 's5', 's9', 'missing-owner'} & {row[0] for row in expected_read}
    observations.close()


def test_revision_work_is_bounded_by_requested_ids_not_native_library(tmp_path):
    from amplifier_web.native_catalog import NativeCatalog
    from test_native_directory import seed
    _, directory, observations = observation_fixture(tmp_path, 6)
    catalog = NativeCatalog(tmp_path / 'native.sqlite3', tmp_path / 'home')
    seed(catalog, tmp_path / 'workspace', 27000)
    directory.bind(catalog)
    state = observations.service._state
    state['pinnedSessionIds'] = ['s4', 'saved-26999', 'missing']
    state['attentionRead'] = dict(directory.db.execute('SELECT key,fingerprint FROM history_directory_notices'))
    operations = 0
    def progress():
        nonlocal operations
        operations += 1000
        return operations > 1_000_000
    directory.db.set_progress_handler(progress, 1000)
    try:
        result = revision(directory, state)
    finally:
        directory.db.set_progress_handler(None, 0)
    assert result['sessions'][4] == digest(['s4', 'saved-26999'])
    assert state['sessions'] == []
    print(json.dumps({'nativeRows': 27000, 'requestedPins': 3, 'revisionVm': operations}))
    observations.close()


def test_activity_counts_skip_idle_native_library_without_decoding_or_changing_metadata(tmp_path, monkeypatch):
    from amplifier_web.client_observations import DirectoryFacts
    from amplifier_web.history_directory import HistoryDirectory
    from amplifier_web.native_catalog import NativeCatalog
    from test_native_directory import seed
    complete, directory, observations = observation_fixture(tmp_path, 6)
    catalog = NativeCatalog(tmp_path / 'native.sqlite3', tmp_path / 'home')
    seed(catalog, tmp_path / 'workspace', 27000)
    directory.bind(catalog)
    state = observations.service._state
    kinds = ('root', 'root', 'worker', 'worker', 'internal', 'root')
    monkeypatch.setattr(HistoryDirectory, 'decode', lambda *_: pytest.fail('activity counts decoded metadata'))
    measurements = []
    try:
        for phase in ('idle', 'mixed', 'hidden-active', 'idle-again'):
            active = phase in {'mixed', 'hidden-active'}
            for index, row in enumerate(complete['sessions']):
                row.update(sessionKind=kinds[index], status='idle', configurationBusy=False, historyLoading=False)
                if active:
                    row.update(status=('working', 'starting', 'working', 'starting', 'working', 'idle')[index],
                               configurationBusy=index == 4, historyLoading=index in {1, 4, 5})
            directory.sync(complete['sessions'], complete)
            directory.db.commit()
            hidden = directory.db.execute("SELECT hidden_id FROM history_local_rows WHERE id='s0'").fetchone()[0]
            state['hiddenNativeSessions'] = [hidden] if phase == 'hidden-active' else []
            before = directory.db.execute('SELECT id,summary FROM history_local_rows ORDER BY id').fetchall()
            changes = directory.db.total_changes
            expected = {'roots': (0, 0, 0), 'delegates': (0, 0, 0)}
            if active:
                expected = {'roots': (0 if phase == 'hidden-active' else 1, 1, 2), 'delegates': (1, 1, 0)}
            for automatic_index in (True, False):
                reader = directory.read_snapshot(state)
                reader.db.execute('PRAGMA automatic_index=' + ('ON' if automatic_index else 'OFF'))
                facts = DirectoryFacts(observations, reader, reader._frozen_state)
                operations = 0
                def progress():
                    nonlocal operations
                    operations += 100
                    return operations > 100_000
                reader.db.set_progress_handler(progress, 100)
                try:
                    result = facts.activity_counts()
                finally:
                    reader.db.set_progress_handler(None, 0)
                    facts.close()
                for role, counts in expected.items():
                    assert tuple(result[role][key] for key in ('executionCount', 'preparingCount', 'loadingCount')) == counts
                    assert result[role]['attentionCount'] == result[role]['requiresActionCount'] == 0
                assert reader._closed and state['sessions'] == []
                measurements.append({'phase': phase, 'automaticIndex': automatic_index, 'vm': operations})
            assert directory.db.total_changes == changes
            assert directory.db.execute('SELECT id,summary FROM history_local_rows ORDER BY id').fetchall() == before
        print(json.dumps({'nativeRows': 27000, 'localRows': 6, 'activityCounts': measurements}))
    finally:
        observations.close()


def test_activity_counts_do_not_process_notice_receipts(tmp_path):
    """Counting busy owners is independent of retained attention history."""
    import time
    from amplifier_web.client_observations import DirectoryFacts

    complete, directory, observations = observation_fixture(tmp_path, 600)
    for row in complete['sessions']:
        row['manualUnread'] = 'notice-' + row['id']
        row['preparation'] = {'detail': 'retained-detail ' * 1024}
    directory.sync(complete['sessions'], complete)
    directory.db.commit()
    state = observations.service._state
    measurements, results = [], []
    try:
        for receipt_count in (0, 20_000):
            state['attentionRead'] = {f'old-notice-{i}': f'old-fingerprint-{i}'
                                      for i in range(receipt_count)}
            reader = directory.read_snapshot(state)
            facts = DirectoryFacts(observations, reader, reader._frozen_state)
            statements, steps = [], []
            reader.db.set_trace_callback(lambda sql: statements.append(len(sql.encode())))
            reader.db.set_progress_handler(lambda: steps.append(1) or 0, 1000)
            started = time.thread_time_ns()
            try:
                results.append(facts.activity_counts())
            finally:
                measurements.append({'receipts': receipt_count, 'vm': len(steps) * 1000,
                                     'statementBytes': max(statements, default=0),
                                     'cpuMs': (time.thread_time_ns() - started) / 1e6})
                facts.close()
        print(json.dumps({'activityCountCost': measurements}))
        assert results[0] == results[1]
        assert sum(row['executionCount'] for row in results[0].values()) == 27
        # Old acknowledgements cannot change current execution/preparation/loading
        # counts. Guard query work and bound bytes, not just the small response.
        assert measurements[1]['vm'] == measurements[0]['vm']
        assert measurements[1]['statementBytes'] == measurements[0]['statementBytes']
        assert state['sessions'] == []
    finally:
        observations.close()


def test_notice_membership_avoids_activity_join_work(tmp_path):
    """Read receipts only need canonical owner identity and role, not bodies."""
    from amplifier_web.history_directory_observations import visible_ids
    from amplifier_web.native_catalog import NativeCatalog
    from test_native_directory import seed

    complete, directory, observations = observation_fixture(tmp_path, 258)
    catalog = NativeCatalog(tmp_path / 'membership-native.sqlite3', tmp_path / 'home')
    seed(catalog, tmp_path / 'workspace', 27000)
    directory.bind(catalog)
    for row in complete['sessions'][:101]:
        row['manualUnread'] = 'notice-' + row['id']
    directory.sync(complete['sessions'], complete)
    directory.db.commit()
    state = observations.service._state
    state['sessions'] = complete['sessions'][:6]
    ids = [row['id'] for row in complete['sessions'][:101]]
    state['attentionRead'] = dict(directory.db.execute('SELECT key,fingerprint FROM history_directory_notices'))
    statements = 0
    def progress():
        nonlocal statements
        statements += 100
        return 0
    directory.db.set_progress_handler(progress, 100)
    try:
        actual = visible_ids(directory, state, ids, notices=True)
    finally:
        directory.db.set_progress_handler(None, 0)
    membership_work, statements = statements, 0
    directory.db.set_progress_handler(progress, 100)
    try:
        sql, params = directory.effective(state, public_ids=ids)
        expected = {row[0] for row in directory.db.execute(sql +
            " SELECT id FROM directory_rows WHERE session_kind!='internal'", params)}
    finally:
        directory.db.set_progress_handler(None, 0)
    assert actual == expected
    # Compare the canonical full-row oracle on this SQLite build; do not make
    # one platform's exact VM count a portable performance contract.
    assert membership_work < statements * .95
    print(json.dumps({'membershipVm': membership_work, 'fullRowVm': statements,
                      'localRows': 258, 'noticeOwners': 101}))
    observations.close()
