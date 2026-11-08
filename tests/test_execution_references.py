"""Committed execution reuse still witnesses content and validates storage."""
import copy
import json
import sqlite3

import pytest

from amplifier_web import execution_preparation, resource_files
from amplifier_web.execution_preparation import ExecutionPreparation


@pytest.fixture
def db(tmp_path):
    connection = sqlite3.connect(tmp_path / 'state.sqlite3')
    connection.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY, value TEXT NOT NULL)')
    connection.commit()
    yield connection
    connection.close()


def owner(identity='one'):
    return {'id': identity, 'runtimeSessionId': identity, 'workspace': '/synthetic',
            'execution': {'nodes': [{'id': str(i), 'kind': 'tool', 'phase': 'completed',
                'nested': [None, True, 1, 1.0, -0.0], 'text': 'é' * 500} for i in range(260)],
                'retiredUsageNodes': [], 'turns': []}}


def save(cache, db, row, owners=None):
    pending = {}
    result = cache.reference(db, row, row['execution'], pending)
    db.commit()
    cache.committed(pending, owners or {row['id']: row})
    return result


def test_two_owners_reuse_only_after_every_fresh_witness(db, monkeypatch):
    cache = ExecutionPreparation()
    rows = {name: owner(name) for name in ('one', 'two')}
    refs = {name: save(cache, db, row, rows) for name, row in rows.items()}
    count = []
    witness = execution_preparation._witness
    monkeypatch.setattr(execution_preparation, '_witness', lambda value: (count.append(1), witness(value))[1])
    with monkeypatch.context() as patch:
        patch.setattr(resource_files.hashlib, 'sha256', lambda *a, **k: pytest.fail('Unchanged whole resource rehashed'))
        for name, row in rows.items():
            result = save(cache, db, row, rows)
            assert result == refs[name]
            result['bytes'] = 0  # Returned dict is not retained evidence.
    assert len(count) == 10  # Three node chunks plus two other complete fields per owner.
    assert cache.bytes + cache._reserved({}) <= cache.MAX_BYTES


@pytest.mark.parametrize('change', ['alias', 'typed', 'field', 'remove-field', 'remove-chunk', 'order', 'custom'])
def test_mutations_and_unsupported_fragments_do_not_reuse(db, change):
    cache, row = ExecutionPreparation(), owner()
    first = save(cache, db, row)
    previous = cache.references[row['id']]
    tree = row['execution']
    if change == 'alias': tree['nodes'][-1]['nested'][0] = 'last chunk'
    elif change == 'typed': tree['nodes'][128]['nested'][1] = 1
    elif change == 'field': tree['extra'] = {'x': 1}
    elif change == 'remove-field': del tree['turns']
    elif change == 'remove-chunk': del tree['nodes'][128:]
    elif change == 'order': tree['nodes'].reverse()
    else:
        class Custom(list):
            pass
        tree['turns'] = Custom()
    result = save(cache, db, row)
    assert result['$resource'] == resource_files.prepare(tree).identity
    if change == 'custom':
        assert result == first and row['id'] not in cache.references
    else:
        assert result != first and cache.references[row['id']] is not previous


@pytest.mark.parametrize('change', ['missing-file', 'missing-index', 'replaced-valid-file'])
def test_lost_or_replaced_storage_requires_revalidation(db, change, monkeypatch):
    cache, row = ExecutionPreparation(), owner()
    first = save(cache, db, row)
    previous = cache.references[row['id']]
    path = resource_files.root(db) / (first['$resource'] + '.json')
    if change == 'missing-file': path.unlink()
    elif change == 'missing-index':
        db.execute('DELETE FROM state_resources WHERE id=?', (first['$resource'],))
        db.commit()
    else:
        text = path.read_bytes()
        path.unlink()
        path.write_bytes(text)
    validations = []
    validate = resource_files.project_detached
    monkeypatch.setattr(resource_files, 'project_detached', lambda *a, **k: (validations.append(1), validate(*a, **k))[1])
    assert save(cache, db, row) == first
    assert len(validations) == 1 and cache.references[row['id']] is not previous
    assert resource_files.read_detached(resource_files.detach(db, first['$resource'])) == row['execution']


@pytest.mark.parametrize('change', ['same-size-corruption', 'wrong-size', 'symlink', 'changed-index'])
def test_storage_conflicts_never_become_successful_cached_saves(db, change, tmp_path):
    cache, row = ExecutionPreparation(), owner()
    first = save(cache, db, row)
    previous = cache.references[row['id']]
    path = resource_files.root(db) / (first['$resource'] + '.json')
    if change == 'same-size-corruption':
        text = path.read_bytes(); path.write_bytes(text.replace(b'completed', b'corrupted', 1))
    elif change == 'wrong-size': path.write_bytes(b'{}')
    elif change == 'symlink':
        target = tmp_path / 'outside'; target.write_bytes(path.read_bytes())
        path.unlink(); path.symlink_to(target)
    else:
        db.execute('UPDATE state_resources SET value=? WHERE id=?', ('{"$blob":"' + 'f' * 64 + '"}', first['$resource']))
        db.commit()
    with pytest.raises((ValueError, OSError)):
        cache.reference(db, row, row['execution'], {})
    db.rollback()
    assert cache.references[row['id']] is previous
    assert path.exists()  # Preserve conflicting evidence.


def test_preexisting_same_size_corruption_cannot_seed_reference(db):
    cache, row = ExecutionPreparation(), owner()
    prepared = resource_files.prepare(row['execution'])
    path = resource_files.root(db) / (prepared.identity + '.json')
    path.parent.mkdir()
    path.write_bytes(b'x' * prepared.size)
    pending = {}
    with pytest.raises(ValueError, match='content conflicts'):
        cache.reference(db, row, row['execution'], pending)
    db.rollback()
    assert not cache.references and pending[row['id']] is None
    assert path.read_bytes() == b'x' * prepared.size


def test_failed_save_retry_owner_remap_and_cold_invalidation(db):
    cache, row = ExecutionPreparation(), owner()
    first = save(cache, db, row)
    original = cache.references[row['id']]
    row['execution']['nodes'][-1]['nested'][0] = 'staged'
    pending = {}
    staged = cache.reference(db, row, row['execution'], pending)
    assert pending[row['id']] is not original
    db.rollback()  # Resource staging succeeded; the owner pointer did not commit.
    assert cache.references[row['id']] is original
    row['execution']['nodes'][-1]['nested'][0] = 'retry'
    result = save(cache, db, row)
    assert result != first and result != staged
    assert resource_files.read_detached(resource_files.detach(db, result['$resource'])) == row['execution']
    prior = cache.references[row['id']]
    row = copy.deepcopy(row)
    assert save(cache, db, row) == result
    assert cache.references[row['id']] is not prior
    prior = cache.references[row['id']]
    row['runtimeSessionId'] = 'remapped'
    assert save(cache, db, row) == result and cache.references[row['id']] is not prior
    prior = cache.references[row['id']]
    row['workspace'] = '/another-workspace'
    assert save(cache, db, row) == result and cache.references[row['id']] is not prior
    cache.committed({row['id']: None}, {row['id']: row})
    assert not cache.references
    save(cache, db, row)
    cache.retain(())
    assert not cache.references and not cache.entries and not cache.bytes


@pytest.mark.parametrize('deferred', [False, True])
def test_reused_reference_keeps_writer_exclusion_until_pointer_settlement(db, deferred):
    cache, row = ExecutionPreparation(), owner()
    save(cache, db, row)
    path = next(r[2] for r in db.execute('PRAGMA database_list') if r[1] == 'main')
    other = sqlite3.connect(path, timeout=0)
    try:
        if deferred:
            db.execute('BEGIN')
            db.execute('SELECT id FROM state_resources').fetchall()
        pending = {}
        cache.reference(db, row, row['execution'], pending)
        assert db.in_transaction
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            other.execute('BEGIN IMMEDIATE')
        db.rollback()  # The canonical owner decides whether its pointer commits.
        other.execute('BEGIN IMMEDIATE')
        other.rollback()
    finally:
        other.close()


def test_inline_and_saturated_cache_preserve_original_storage(db):
    cache, row = ExecutionPreparation(), owner()
    cache.MAX_BYTES = 16000
    rows, pending = {}, {}
    for index in range(8):
        current = owner(str(index)); rows[current['id']] = current
        result = cache.reference(db, current, current['execution'], pending)
        assert result['$resource'] == resource_files.prepare(current['execution']).identity
        assert cache.bytes + cache._reserved(pending) <= cache.MAX_BYTES
    db.commit(); cache.committed(pending, rows)
    assert cache.bytes + cache._reserved({}) <= cache.MAX_BYTES
    memory = sqlite3.connect(':memory:')
    try:
        memory.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY, value TEXT NOT NULL)')
        memory.commit()
        cache = ExecutionPreparation()
        result = save(cache, memory, row)
        assert not cache.references
        assert resource_files.read_detached(resource_files.detach(memory, result['$resource'])) == row['execution']
    finally:
        memory.close()


def test_committed_and_provisional_reference_metadata_share_fragment_budget(db):
    cache = ExecutionPreparation()
    cache.MAX_BYTES = 700_000
    rows = {name: owner(name) for name in ('one', 'two')}
    for row in rows.values(): save(cache, db, row, rows)
    assert len(cache.references) == 2 and cache.entries
    committed = dict(cache.references)
    original_keys = set(cache.entries)
    pending = {}
    for name in ('one', 'three', 'four', 'five'):
        row = rows.setdefault(name, owner(name))
        row['execution']['nodes'][-1]['nested'][0] = 'provisional replacement'
        cache.reference(db, row, row['execution'], pending)
        assert any(entry is not None for entry in pending.values())
        assert cache.references == committed
        assert cache.bytes + cache._reserved(pending) <= cache.MAX_BYTES
    assert not original_keys <= set(cache.entries)
    db.commit(); cache.committed(pending, rows)
    assert cache.bytes + cache._reserved({}) <= cache.MAX_BYTES


@pytest.mark.parametrize('change', ['replacement', 'runtime', 'workspace', 'cold', 'native', 'deleted'])
def test_staged_entry_cannot_be_promoted_for_an_invalidated_owner(db, change):
    cache, row = ExecutionPreparation(), owner()
    pending = {}
    resource = cache.reference(db, row, row['execution'], pending)
    assert pending[row['id']] is not None
    deleted = ()
    if change == 'replacement': row = copy.deepcopy(row)
    elif change == 'runtime': row['runtimeSessionId'] = 'replacement'
    elif change == 'workspace': row['workspace'] = '/replacement'
    elif change == 'cold': row['_coldFields'] = {'execution': resource}
    elif change == 'native': row['historyManaged'] = True
    else: deleted = {row['id']}
    db.commit(); cache.committed(pending, {row['id']: row}, deleted)
    assert not cache.references


@pytest.mark.parametrize('failure', ['pointer', 'preexisting-corrupt-file'])
async def test_failed_owner_pointer_commit_preserves_previous_reference_and_retry(tmp_path, monkeypatch, failure):
    from amplifier_web.service import AppService
    from amplifier_web.storage_migration import close_maintenance
    from amplifier_web import state_records
    from amplifier_web.cold_display import load, MARKER
    from test_automatic_history import ObservedRuntime
    app = AppService(tmp_path / 'app', ObservedRuntime(), workspace=tmp_path)
    try:
        for task in (app.history, app.event_log_view, app.diagnostics, app.schedules, app.observations):
            await task.close()
        row = app._new_session({'workspace': str(tmp_path)})
        row['execution'] = owner()['execution']
        row['execution']['retiredUsageNodes'] = [
            {'id': str(i), 'sessionId': row['id'], 'producerId': 'prior', 'kind': 'llm',
             'phase': 'completed', 'revision': 1, 'usage': {'inputTokens': i}} for i in range(130)]
        app._state['sessions'].append(row)
        if failure == 'preexisting-corrupt-file':
            from amplifier_web.session_projection import stored_execution
            prepared = resource_files.prepare(stored_execution(row['execution']))
            path = resource_files.root(app.db) / (prepared.identity + '.json')
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b'x' * prepared.size)
            with pytest.raises(ValueError, match='content conflicts'):
                app._publish_changes(sessions={row['id']})
            assert row['id'] not in app._execution_preparation.references
            assert row['id'] not in {s['id'] for s in state_records.load(app.db)['sessions']}
            assert path.read_bytes() == b'x' * prepared.size
            assert not app.db.in_transaction
            # The rejected fixture was never saved. Remove only this synthetic
            # resident row so normal shutdown does not retry the intentional
            # corruption; leave its conflicting file intact for the assertions.
            app._state['sessions'].remove(row)
            return
        app._publish_changes(sessions={row['id']})
        await close_maintenance(app)
        previous = app._execution_preparation.references[row['id']]
        assert previous.input_digest is not None
        alias = row['execution']['nodes'][-1]['nested']
        alias[0] = 'staged before pointer failure'
        original = state_records.save_working
        def fail(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('Synthetic owner pointer rejection')
        with monkeypatch.context() as patch:
            patch.setattr(state_records, 'save_working', fail)
            with pytest.raises(RuntimeError, match='pointer rejection'):
                app._publish_changes(sessions={row['id']})
        assert app._execution_preparation.references[row['id']] is previous
        alias[0] = 'new retry value'
        app._publish_changes(sessions={row['id']})
        saved = next(s for s in state_records.load(app.db)['sessions'] if s['id'] == row['id'])
        view = load(app.db, saved['$viewPayload'])
        assert load(app.db, view[MARKER]['execution'])['nodes'][-1]['nested'][0] == 'new retry value'
        assert app._execution_preparation.references[row['id']] is not previous
    finally:
        await close_maintenance(app)
        await app.close()


def observed_owner(identity='one'):
    row = owner(identity)
    row['execution'] = {'nodes': [], 'turns': [{'id': 'turn', 'phase': 'running'}],
        'currentTurnId': 'turn', 'retiredUsageNodes': [
            {'id': str(i), 'sessionId': identity, 'rootSessionId': identity,
             'producerId': 'native', 'kind': 'llm', 'turnId': 'turn',
             'phase': 'completed', 'revision': 1,
             'usage': {'inputTokens': i}, 'assignmentReservation': {'tokens': [i]}}
            for i in range(300)]}
    row['execution']['nodes'] = [dict(copy.deepcopy(receipt), liveObservation=True)
                                 for receipt in row['execution']['retiredUsageNodes']]
    return row


def save_projected(cache, db, row, owners=None):
    pending = {}
    inline, reference = cache.stored(db, row, row['execution'], pending)
    assert inline is None
    db.commit()
    cache.committed(pending, owners or {row['id']: row})
    return reference


def test_three_owners_skip_conversion_only_after_fresh_content_and_committed_proof(db, monkeypatch):
    from amplifier_web import session_projection
    cache = ExecutionPreparation()
    rows = {name: observed_owner(name) for name in ('one', 'two', 'three')}
    previous = {name: save_projected(cache, db, row, rows) for name, row in rows.items()}
    assert all(entry.input_digest for entry in cache.references.values())
    # Even after every derived encoding fragment is evicted, an exact committed
    # resource plus freshly witnessed original input needs no reconversion.
    cache.entries.clear(); cache.bytes = 0
    calls, original = [], session_projection.stored_execution
    def observe(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(session_projection, 'stored_execution', observe)
    for name, row in rows.items():
        row['streaming'] = 'new token'
        actual = save_projected(cache, db, row, rows)
        assert actual == previous[name]
        actual['pendingWork'] = False  # Returned metadata cannot poison reuse.
        assert save_projected(cache, db, row, rows)['pendingWork'] is True
    assert not calls
    assert cache.bytes + cache._reserved({}) <= cache.MAX_BYTES
    # A cold service must derive everything again from source/storage evidence.
    assert save_projected(ExecutionPreparation(), db, rows['one']) == previous['one']
    assert calls == [1]


@pytest.mark.parametrize('change', ['nested-node', 'nested-retired', 'typed', 'turn',
    'remove-turn', 'remove-field', 'add-field', 'reorder', 'missing-receipt', 'assignment'])
def test_original_execution_mutations_cannot_reuse_projection(db, change, monkeypatch):
    from amplifier_web import session_projection
    cache, row = ExecutionPreparation(), observed_owner()
    save_projected(cache, db, row)
    tree = row['execution']
    if change == 'nested-node': tree['nodes'][-1]['usage']['inputTokens'] = 901
    elif change == 'nested-retired': tree['retiredUsageNodes'][-1]['usage']['inputTokens'] = 902
    elif change == 'typed': tree['nodes'][-1]['revision'] = True
    elif change == 'turn': tree['turns'][0]['phase'] = 'completed'
    elif change == 'remove-turn': tree['turns'].clear()
    elif change == 'remove-field': del tree['currentTurnId']
    elif change == 'add-field': tree['new'] = {'value': 1}
    elif change == 'reorder': tree['nodes'].reverse()
    elif change == 'missing-receipt':
        tree['nodes'][-1]['producerId'] = None
        tree['retiredUsageNodes'][-1]['producerId'] = None
    else: tree['retiredUsageNodes'][-1]['assignmentReservation']['tokens'].append(903)
    expected = session_projection.stored_execution(tree)
    calls, original = [], session_projection.stored_execution
    def observe(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(session_projection, 'stored_execution', observe)
    result = save_projected(cache, db, row)
    assert calls == [1]
    assert resource_files.read_detached(resource_files.detach(db, result['$resource'])) == expected
    assert save_projected(cache, db, row) == result and calls == [1]


@pytest.mark.parametrize('change', ['file', 'runtime', 'workspace', 'replacement'])
def test_projection_shortcut_never_skips_owner_or_resource_validation(db, change, monkeypatch):
    from amplifier_web import session_projection
    cache, row = ExecutionPreparation(), observed_owner()
    previous = save_projected(cache, db, row)
    if change == 'file':
        (resource_files.root(db) / (previous['$resource'] + '.json')).unlink()
    elif change == 'runtime': row['runtimeSessionId'] = 'different'
    elif change == 'workspace': row['workspace'] = '/different'
    else: row = copy.deepcopy(row)
    calls, original = [], session_projection.stored_execution
    def observe(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(session_projection, 'stored_execution', observe)
    assert save_projected(cache, db, row) == previous
    assert calls == [1]


def test_original_content_custom_types_keep_projection_hooks_without_reducers(db):
    class Custom(dict):
        copies = 0
        def __reduce_ex__(self, protocol):
            raise AssertionError('Never invoke reduction while checking reuse')
        def __deepcopy__(self, memo):
            type(self).copies += 1
            return {'tokens': [44]}
    cache, row = ExecutionPreparation(), observed_owner()
    save_projected(cache, db, row)
    row['execution']['retiredUsageNodes'][0]['assignmentReservation'] = Custom(tokens=[44])
    result = save_projected(cache, db, row)
    assert Custom.copies > 0
    assert cache.references[row['id']].input_digest is None
    assert resource_files.read_detached(resource_files.detach(db, result['$resource']))[
        'retiredUsageNodes'][0]['assignmentReservation'] == {'tokens': [44]}


async def test_scalar_shortcut_preserves_changed_worker_and_task_view_and_restart(tmp_path, monkeypatch):
    from amplifier_web.service import AppService
    from amplifier_web.storage_migration import close_maintenance
    from amplifier_web import state_records, session_projection
    from amplifier_web.cold_display import load, MARKER
    from test_automatic_history import ObservedRuntime
    app = AppService(tmp_path / 'app', ObservedRuntime(), workspace=tmp_path)
    try:
        for task in (app.history, app.event_log_view, app.diagnostics, app.schedules, app.observations):
            await task.close()
        row = app._new_session({'workspace': str(tmp_path)})
        row['execution'] = observed_owner(row['id'])['execution']
        row['workers'] = [{'id': 'child', 'status': 'working', 'reportReceipts': []}]
        row['task'] = {'objective': 'Initial objective', 'status': 'active'}
        app._state['sessions'].append(row)
        app._publish_changes(sessions={row['id']})
        await close_maintenance(app)
        previous = app._execution_preparation.references[row['id']]
        row['streaming'] = 'Current response'
        row['workers'][0]['reportReceipts'].append({'id': 'report', 'text': 'Done with proof'})
        row['workers'][0]['status'] = 'completed'
        row['task']['objective'] = 'Updated objective'
        row['interruptionRevision'] = 7
        row['messages'].append({'id': 'next-input', 'role': 'user', 'text': 'Changed input',
                                'inputId': 'next-input', 'createdAt': 2})
        with monkeypatch.context() as patch:
            patch.setattr(session_projection, 'stored_execution', lambda *a, **k: pytest.fail(
                'Unchanged execution must not be converted for a task/report save'))
            app._publish_changes(sessions={row['id']})
        assert app._execution_preparation.references[row['id']] is previous
        saved = next(s for s in state_records.load(app.db)['sessions'] if s['id'] == row['id'])
        view = load(app.db, saved['$viewPayload'])
        assert view['workers'] == row['workers'] and view['task'] == row['task']
        assert view['interruptionRevision'] == 7 and view['messages'] == row['messages']
        assert load(app.db, view[MARKER]['execution']) == session_projection.stored_execution(row['execution'])
        # Simulate the new service's empty disposable preparation owner and save
        # once more; persisted execution and changed non-execution facts agree.
        app._execution_preparation = ExecutionPreparation()
        app._publish_changes(sessions={row['id']})
        saved = next(s for s in state_records.load(app.db)['sessions'] if s['id'] == row['id'])
        restarted = load(app.db, saved['$viewPayload'])
        assert restarted['workers'] == view['workers'] and restarted['task'] == view['task']
        assert restarted['interruptionRevision'] == 7 and restarted['messages'] == view['messages']
        assert restarted[MARKER]['execution'] == view[MARKER]['execution']
    finally:
        await close_maintenance(app)
        await app.close()


def test_changed_dropped_fields_do_not_accumulate_reference_metadata_charge(db):
    cache, row = ExecutionPreparation(), observed_owner()
    reference = save_projected(cache, db, row)
    original_charge = cache.references[row['id']].charge
    previous_digest = cache.references[row['id']].input_digest
    for number in range(12):
        row['execution']['nodes'][-1]['displayOnly'] = {'iteration': number}
        assert save_projected(cache, db, row) == reference
        current = cache.references[row['id']]
        assert current.input_digest != previous_digest
        assert current.charge == original_charge
        previous_digest = current.input_digest


def test_small_execution_keeps_inline_path_without_content_digest(db, monkeypatch):
    from amplifier_web.session_projection import stored_execution
    cache, row = ExecutionPreparation(), owner()
    row['execution']['nodes'] = row['execution']['nodes'][:3]
    monkeypatch.setattr(execution_preparation, '_input_digest', lambda *args: pytest.fail(
        'Short inline execution does not need a projection-reuse witness'))
    inline, reference = cache.stored(db, row, row['execution'], {})
    assert inline == stored_execution(row['execution']) and reference is None
    assert not cache.references


def test_digest_refresh_reserves_distinct_committed_and_pending_entries(db, monkeypatch):
    cache, row = ExecutionPreparation(), observed_owner()
    reference = save_projected(cache, db, row)
    committed = cache.references[row['id']]
    cache.entries.clear(); cache.bytes = 0
    cache.MAX_BYTES = committed.charge + committed.charge // 2
    row['execution']['nodes'][-1]['displayOnly'] = 'unchanged canonical projection'
    # Exercise the committed-reference return at the constrained reservation
    # boundary without manufacturing encoding fragments just to fill a cache.
    def reuse(db, session, tree, pending, **kwargs):
        assert resource_files.prepare(tree).identity == reference['$resource']
        pending[session['id']] = committed
        return resource_files.reuse_reference(db, committed.resource)
    monkeypatch.setattr(cache, 'reference', reuse)
    pending = {}
    inline, actual = cache.stored(db, row, row['execution'], pending)
    assert inline is None and actual == reference
    assert pending[row['id']] is committed
    assert cache.bytes + cache._reserved(pending) <= cache.MAX_BYTES
    db.commit(); cache.committed(pending, {row['id']: row})
    assert cache.references[row['id']] is committed


def test_projection_summary_keeps_all_pending_work_and_voice_facts(db):
    cache, row = ExecutionPreparation(), observed_owner()
    for group in ('nodes', 'retiredUsageNodes'):
        row['execution'][group][-1].update(lifecycle='voice', phase='running', revision=3)
    active = save_projected(cache, db, row)
    assert active['pendingObservation'] and active['pendingWork'] and active['pendingVoice']
    assert save_projected(cache, db, row) == active
    for group in ('nodes', 'retiredUsageNodes'):
        row['execution'][group][-1].update(phase='completed', endedAt=20, revision=4)
    row['execution']['turns'][0]['phase'] = 'completed'
    complete = save_projected(cache, db, row)
    assert complete['executionProjection'] == 1
    assert not complete['pendingObservation'] and not complete['pendingWork'] and not complete['pendingVoice']
    assert save_projected(cache, db, row) == complete


def test_projection_hook_cannot_seed_digest_of_a_different_source(db):
    cache, row = ExecutionPreparation(), observed_owner()
    class ChangesSource(dict):
        def __deepcopy__(self, memo):
            row['execution']['retiredUsageNodes'][0]['assignmentReservation'] = {'tokens': [99]}
            return {'tokens': [44]}
    row['execution']['retiredUsageNodes'][0]['assignmentReservation'] = ChangesSource()
    first = save_projected(cache, db, row)
    assert cache.references[row['id']].input_digest is None
    assert resource_files.read_detached(resource_files.detach(db, first['$resource']))[
        'retiredUsageNodes'][0]['assignmentReservation'] == {'tokens': [44]}
    second = save_projected(cache, db, row)
    assert first != second
    assert resource_files.read_detached(resource_files.detach(db, second['$resource']))[
        'retiredUsageNodes'][0]['assignmentReservation'] == {'tokens': [99]}


def test_failed_fragment_encoding_keeps_committed_reference_and_repairs_exactly(db):
    from amplifier_web.session_projection import stored_execution
    cache, row = ExecutionPreparation(), observed_owner()
    first = save_projected(cache, db, row)
    committed = cache.references[row['id']]
    alias = row['execution']['retiredUsageNodes'][128]['assignmentReservation']['tokens']
    alias.append(alias)
    with pytest.raises(ValueError, match='Circular'):
        cache.stored(db, row, row['execution'], {})
    db.rollback()
    assert cache.references[row['id']] is committed
    alias.pop()
    alias.append(99)
    result = save_projected(cache, db, row)
    assert result != first
    assert resource_files.read_detached(resource_files.detach(db, result['$resource'])) == stored_execution(row['execution'])


@pytest.mark.parametrize('change', ['node', 'receipt', 'turn', 'shared-receipt', 'all-invalid'])
def test_placement_fragments_revalidate_changed_dependencies_and_preserve_exact_output(db, monkeypatch, change):
    from amplifier_web import session_projection
    cache, row = ExecutionPreparation(), observed_owner()
    tree = row['execution']
    # One changed receipt must invalidate every fragment that depends on it.
    if change == 'shared-receipt':
        tree['nodes'][128] = copy.deepcopy(tree['nodes'][0])
    save_projected(cache, db, row)
    if change == 'node':
        tree['nodes'][-1]['phase'] = 'running'
    elif change == 'receipt':
        tree['retiredUsageNodes'][-1]['usage']['inputTokens'] = 900
        tree['retiredUsageNodes'][-1]['revision'] = 2
    elif change == 'shared-receipt':
        tree['retiredUsageNodes'][0]['usage']['inputTokens'] = 901
        tree['retiredUsageNodes'][0]['revision'] = 2
    elif change == 'turn':
        tree['turns'].clear()
    else:
        for node in tree['nodes']:
            node['observationWindow'] = 'invalid'
    expected = session_projection.stored_execution(tree)
    if change in ('turn', 'all-invalid'):
        assert expected['nodes'] == []
    calls, original = [], session_projection._observed_work
    def observe(node, *args):
        calls.append(node['id'])
        return original(node, *args)
    monkeypatch.setattr(session_projection, '_observed_work', observe)
    result = save_projected(cache, db, row)
    assert resource_files.read_detached(resource_files.detach(db, result['$resource'])) == expected
    if change in ('node', 'receipt'):
        assert len(calls) <= cache.CHUNK
    elif change == 'shared-receipt':
        assert len(calls) == 2 * cache.CHUNK
    else:
        assert len(calls) == len(tree['nodes'])
    assert cache.bytes + cache._reserved({}) <= cache.MAX_BYTES
    assert cache.accounting_bytes <= cache.MAX_BYTES


def test_placement_preparation_falls_back_with_small_budget_and_inline_values(db):
    from amplifier_web.session_projection import stored_execution
    cache, row = ExecutionPreparation(), observed_owner()
    cache.MAX_BYTES = 1000
    result = save_projected(cache, db, row)
    assert resource_files.read_detached(resource_files.detach(db, result['$resource'])) == stored_execution(row['execution'])
    assert cache.bytes + cache._reserved({}) <= cache.MAX_BYTES
    # A large node list alone does not turn inline tuple values into JSON lists.
    tree = {'nodes': [{'id': str(i), 'value': ('inline', i)} for i in range(300)]}
    inline, reference = cache.stored(db, row, tree, {})
    assert reference is None and inline == stored_execution(tree)
    assert type(inline['nodes'][0]['value']) is tuple
