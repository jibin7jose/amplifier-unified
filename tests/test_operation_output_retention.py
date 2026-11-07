"""Opted-in output expiry preserves observations, retry identity and authority."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_operations import OperationJournal
from amplifier_web import storage_cleanup
from amplifier_web.service import AppError, AppService
from test_operations import event, final, output

DAY = 86400


def observed(journal, identity, *, days=30, state='completed'):
    journal.ingest('s', 'r', event(identity), output_keep_days=days)
    journal.ingest('s', 'r', output(identity))
    journal.ingest('s', 'r', final(identity, state=state, returncode=0 if state == 'completed' else 1))
    return journal.status('s', identity)


@pytest.mark.parametrize('state', ['completed', 'failed', 'cancelled'])
def test_expiry_is_atomic_truthful_and_deduplicates_original_events(tmp_path, monkeypatch, state):
    clock = [1000]
    monkeypatch.setattr('amplifier_operations.journal.time.time', lambda: clock[0])
    path = tmp_path / 'operations.db'
    journal = OperationJournal(path)
    before = observed(journal, 'p1', state=state)
    assert before['outputSettledAt'] == 1000 and before['endedAt'] == 2
    assert before['outputExpiresAt'] == 1000 + 30 * DAY
    clock[0] += 30 * DAY
    with monkeypatch.context() as patch:
        patch.setattr(journal, '_save', lambda value: (_ for _ in ()).throw(OSError('Fixture disk failure')))
        with pytest.raises(OSError):
            journal.expire_output('s', 'p1', before['revision'], clock[0])
    assert journal.status('s', 'p1') == before
    assert journal.read('s', 'p1')['chunks'][0]['text'] == 'hello\n'
    result = journal.expire_output('s', 'p1', before['revision'], clock[0])
    assert result == {'status': 'expired', 'removedOutputBytes': 6}
    page = journal.read('s', 'p1')
    assert page['state'] == state and page['returncode'] == before['returncode']
    assert page['outputAvailability'] == 'expired' and page['outputExpiredAt'] == clock[0]
    assert page['streamComplete'] and page['captureComplete'] and not page['outputComplete']
    assert page['chunks'] == [] and page['cursorGap'] and not page['hasMore']
    assert page['nextCursor'] == page['earliestCursor'] == page['latestCursor'] == 1
    assert page['retainedOutputBytes'] == page['retainedChunks'] == 0
    assert page['droppedOutputBytes'] == page['totalOutputBytes'] == 6
    assert journal.read('s', 'p1', 1)['outputAvailability'] == 'expired'
    assert not journal.read('s', 'p1', 1)['cursorGap']
    assert journal.ingest('s', 'r', output('p1')) == (journal.status('s', 'p1'), False)
    with pytest.raises(ValueError, match='Conflicting'):
        journal.ingest('s', 'r', output('p1', text='changed'))
    assert journal.expire_output('s', 'p1', before['revision'], clock[0])['status'] == 'already-expired'
    journal.close()
    restored = OperationJournal(path)
    try:
        restored.recover()
        assert restored.read('s', 'p1') == page
        assert restored.output_expiry_candidates(clock[0])['items'] == []
    finally:
        restored.close()


def test_legacy_unknown_interrupted_active_and_empty_output_are_not_due(tmp_path, monkeypatch):
    monkeypatch.setattr('amplifier_operations.journal.time.time', lambda: 1000)
    journal = OperationJournal(tmp_path / 'operations.db')
    try:
        observed(journal, 'legacy', days=None)
        for state in ('outcome_unknown', 'interrupted'):
            observed(journal, state, state=state)
        journal.ingest('s', 'r', event('active'), output_keep_days=30)
        journal.ingest('s', 'r', output('active'))
        journal.ingest('s', 'r', event('empty'), output_keep_days=30)
        journal.ingest('s', 'r', final('empty', sequence=2, total_output_bytes=0))
        records = [journal.read('s', identity) for identity in ('legacy', 'outcome_unknown', 'interrupted', 'active', 'empty')]
        assert journal.output_expiry_candidates(1000 + 31 * DAY)['items'] == []
        assert records == [journal.read('s', row['id']) for row in records]
        # Enabling later cannot classify an existing operation retroactively.
        journal.ingest('s', 'r', event('started-off'))
        journal.ingest('s', 'r', output('started-off'), output_keep_days=30)
        journal.ingest('s', 'r', final('started-off'), output_keep_days=30)
        assert 'outputExpiresAt' not in journal.status('s', 'started-off')
        # Actual late final evidence can settle an unknown operation, then its
        # captured prospective lifetime starts at host-observed settlement.
        journal.ingest('s', 'r', final('outcome_unknown', sequence=4))
        assert [row['id'] for row in journal.output_expiry_candidates(1000 + 31 * DAY)['items']] == ['outcome_unknown']
    finally:
        journal.close()


def test_current_longer_policy_extends_but_shorter_cannot_reduce_captured_lifetime(tmp_path, monkeypatch):
    monkeypatch.setattr('amplifier_operations.journal.time.time', lambda: 1000)
    journal = OperationJournal(tmp_path / 'operations.db')
    try:
        row = observed(journal, 'p1')
        assert journal.expire_output('s', 'p1', row['revision'], 1000 + 2 * DAY,
                                     keep_days=1)['status'] == 'protected'
        assert journal.expire_output('s', 'p1', row['revision'], 1000 + 31 * DAY,
                                     keep_days=60)['status'] == 'protected'
        assert journal.status('s', 'p1') == row
        assert journal.expire_output('s', 'p1', row['revision'], 1000 + 61 * DAY,
                                     keep_days=60)['status'] == 'expired'
    finally:
        journal.close()


def test_due_page_is_indexed_and_advances_past_protected_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr('amplifier_operations.journal.time.time', lambda: 1000)
    journal = OperationJournal(tmp_path / 'operations.db')
    try:
        for index in range(18):
            observed(journal, f'due-{index:02}')
        # Unrelated legacy records do not enter the deadline index or page.
        with journal.db:
            journal.db.executemany('INSERT INTO operations VALUES (?,?,?)',
                ((f'old-{i}', 'other', json.dumps({'id': f'old-{i}', 'state': 'completed', 'createdAt': i})) for i in range(2000)))
        statements = []
        journal.db.set_trace_callback(statements.append)
        first = journal.output_expiry_candidates(1000 + 31 * DAY)
        journal.db.set_trace_callback(None)
        query = next(text for text in statements if text.startswith('SELECT id,session_id'))
        plan = journal.db.execute('EXPLAIN QUERY PLAN ' + query).fetchall()
        assert any('operations_output_expiry' in str(row) for row in plan)
        assert not any('TEMP B-TREE' in str(row) or 'SCAN operations' in str(row) for row in plan)
        second = journal.output_expiry_candidates(1000 + 31 * DAY, after=first['nextCursor'])
        last = journal.output_expiry_candidates(1000 + 31 * DAY, after=second['nextCursor'])
        assert [len(page['items']) for page in (first, second, last)] == [8, 8, 2]
        assert last['nextCursor'] is None
        assert len({row['id'] for page in (first, second, last) for row in page['items']}) == 18
    finally:
        journal.close()


@pytest.fixture
async def app(tmp_path):
    value = AppService(tmp_path / 'app', workspace=tmp_path)
    await value.dispatch('session.create', {})
    yield value
    await value.close()


def control(app, task=None, **fields):
    sid = app._session()['id']
    path = app.data_dir / 'sessions' / sid / 'control-state.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'task': task, **fields}))
    return path


async def admit_output(app, identity='p1'):
    sid = app._session()['id']
    for observation in (event(identity), output(identity), final(identity)):
        await app.operations.observe(sid, sid, observation)
    return app.operations.status(sid, identity)


async def enable(app, identity='enable'):
    return (await app.dispatch('storage.cleanup.applyPreset', {
        'expectedRevision': storage_cleanup.policy(app)['revision'],
        'preset': 'temporary-operation-output'}, command_id=identity))['result']['policy']


async def test_policy_is_opt_in_prospective_and_preserves_other_choices(app, monkeypatch):
    clock = [1000]
    monkeypatch.setattr('amplifier_operations.journal.time.time', lambda: clock[0])
    assert storage_cleanup.policy(app)['operationOutputKeepDays'] is None
    await admit_output(app, 'old')
    storage_cleanup.store(app, storage_cleanup.POLICY,
        {**storage_cleanup.DEFAULTS, 'keepDays': 11, 'maxUnusedBytes': 123, 'workspaceArchiveKeepDays': 19})
    settings = await enable(app)
    assert settings['operationOutputKeepDays'] == 30 and settings['workspaceArchiveKeepDays'] == 19
    assert settings['keepDays'] == 11 and settings['maxUnusedBytes'] == 123
    await admit_output(app)
    control(app, taskHistory='Not part of current task authority')
    clock[0] += 31 * DAY
    result = await app.operations.expire_outputs(settings)
    assert result['expired'] == 1
    sid = app._session()['id']
    assert app.operations.read({'sessionId': sid, 'id': 'old'})['chunks']
    assert app.operations.read({'sessionId': sid, 'id': 'p1'})['outputAvailability'] == 'expired'
    again = await app.dispatch('storage.cleanup.applyPreset', {'expectedRevision': 0,
        'preset': 'temporary-operation-output'}, command_id='enable')
    assert again['duplicate'] and again['result']['policy'] == settings
    with pytest.raises(AppError, match='different contents'):
        await app.dispatch('storage.cleanup.applyPreset', {'expectedRevision': 0,
            'preset': 'temporary-test-workspaces'}, command_id='enable')


async def test_only_started_observations_capture_policy_and_kernel_result_survives(app, monkeypatch):
    await enable(app)
    sid = app._session()['id']
    await app.operations.observe(sid, sid, {**event(), 'source': 'computation', 'kind': 'kernel-cell'})
    # Chunk persistence must not re-read app policy or classify a missing start.
    monkeypatch.setattr(storage_cleanup, 'policy', lambda app: pytest.fail('Policy was reread after admission'))
    for observation in (output(), final(result='42', error=None)):
        await app.operations.observe(sid, sid, {**observation, 'source': 'computation', 'kind': 'kernel-cell'})
    row = app.operations.journal.status(sid, 'p1')
    app.operations.journal.expire_output(sid, 'p1', row['revision'], row['outputExpiresAt'])
    after = app.operations.read({'sessionId': sid, 'id': 'p1'})
    assert after['chunks'] == [] and after['evidence'] == {'result': '42', 'error': None}
    assert after['state'] == 'completed' and not after['outputComplete']
    await app.operations.observe(sid, sid, output('missing-start'))
    await app.operations.observe(sid, sid, final('missing-start'))
    assert 'outputExpiresAt' not in app.operations.status(sid, 'missing-start')


async def test_policy_is_rechecked_after_waiting_for_current_authority(app, monkeypatch):
    settings = await enable(app)
    row = await admit_output(app)
    control(app)
    monkeypatch.setattr('amplifier_operations.journal.time.time', lambda: row['outputExpiresAt'])
    async with app.lock:
        cleanup = asyncio.create_task(app.operations.expire_outputs(settings))
        await asyncio.sleep(0)
        assert not cleanup.done()
        storage_cleanup.store(app, storage_cleanup.POLICY,
                              {**settings, 'operationOutputKeepDays': None, 'revision': 2})
    assert (await cleanup)['status'] == 'deferred'
    assert app.operations.status(app._session()['id'], 'p1') == row


@pytest.mark.parametrize('change', ['policy', 'task', 'preparing'])
async def test_other_work_runs_between_candidates_and_protects_remaining_output(app, monkeypatch, change):
    clock = [1000]
    monkeypatch.setattr('amplifier_operations.journal.time.time', lambda: clock[0])
    settings = await enable(app)
    first = await admit_output(app)
    await admit_output(app, 'p2')
    control(app)
    sid = app._session()['id']
    changed = []
    def protect_second():
        assert not app.lock.locked()
        changed.append(change)
        if change == 'policy':
            storage_cleanup.store(app, storage_cleanup.POLICY,
                                  {**settings, 'operationOutputKeepDays': None, 'revision': 2})
        elif change == 'preparing':
            app.runtime = SimpleNamespace(workers={}, _preparations={sid: {}}, close=AsyncMock())
        else:
            control(app, {'id': 'task', 'revision': 1, 'status': 'active', 'objective': 'Follow up',
                'constraints': [], 'questionIds': [], 'operationIds': ['p2'], 'artifactRefs': [],
                'completionEvidence': [], 'corrections': []})
    original = app.operations.journal.expire_output
    def expire(session_id, identity, *args, **kwargs):
        result = original(session_id, identity, *args, **kwargs)
        if identity == 'p1':
            asyncio.get_running_loop().call_soon(protect_second)
        return result
    monkeypatch.setattr(app.operations.journal, 'expire_output', expire)
    clock[0] += 31 * DAY
    result = await app.operations.expire_outputs(settings)
    assert changed == [change] and result['expired'] == 1
    assert app.operations.read({'sessionId': sid, 'id': 'p2'})['chunks']
    if change == 'policy':
        assert result['status'] == 'deferred'
        assert result['nextCursor'] == [first['outputExpiresAt'], 'p1']
        storage_cleanup.store(app, storage_cleanup.POLICY, settings)
        assert (await app.operations.expire_outputs(settings, after=result['nextCursor']))['expired'] == 1
    else:
        assert result['protected'] == 1 and result['nextCursor'] is None


async def test_later_candidate_failure_still_notifies_readers_of_committed_expiry(app, monkeypatch):
    settings = await enable(app)
    first = await admit_output(app)
    second = await admit_output(app, 'p2')
    control(app)
    monkeypatch.setattr('amplifier_operations.journal.time.time', lambda: second['outputExpiresAt'])
    changed = app.operations.changed
    original = app.operations.journal.expire_output
    def expire(session_id, identity, *args, **kwargs):
        if identity == 'p2':
            raise OSError('Fixture journal write failure')
        return original(session_id, identity, *args, **kwargs)
    monkeypatch.setattr(app.operations.journal, 'expire_output', expire)
    with pytest.raises(OSError):
        await app.operations.expire_outputs(settings)
    assert changed.is_set()
    sid = app._session()['id']
    assert app.operations.status(sid, 'p1')['outputAvailability'] == 'expired'
    assert app.operations.status(sid, 'p2') == second


async def test_completed_work_can_release_output_and_new_validation_remains_truthful(app, monkeypatch):
    from amplifier_web.integration_validation import outcome
    settings = await enable(app)
    row = await admit_output(app)
    task = {'id': 'task', 'revision': 2, 'status': 'completed', 'objective': 'Finished work',
            'constraints': [], 'questionIds': [], 'operationIds': ['p1'], 'artifactRefs': [],
            'completionEvidence': ['p1'], 'corrections': []}
    control(app, task)
    assert outcome(row, 'matched', 'matched') == 'passed'
    monkeypatch.setattr('amplifier_operations.journal.time.time', lambda: row['outputExpiresAt'])
    assert (await app.operations.expire_outputs(settings))['expired'] == 1
    expired = app.operations.status(app._session()['id'], 'p1')
    assert outcome(expired, 'matched', 'matched') == 'unknown'
    assert expired['state'] == 'completed' and expired['returncode'] == 0


@pytest.mark.parametrize('protection', ['active-task', 'paused-task', 'blocked-task', 'missing', 'malformed', 'oversized', 'live', 'preparing', 'disabled'])
async def test_current_authority_and_policy_protect_output(app, monkeypatch, protection):
    clock = [1000]
    monkeypatch.setattr('amplifier_operations.journal.time.time', lambda: clock[0])
    settings = await enable(app)
    await admit_output(app)
    sid = app._session()['id']
    path = control(app)
    if protection.endswith('-task'):
        task = {'id': 'task', 'revision': 1, 'status': protection.split('-')[0], 'objective': 'Keep working',
                'constraints': [], 'questionIds': [], 'operationIds': ['p1'], 'artifactRefs': [],
                'completionEvidence': [], 'corrections': []}
        control(app, task)
    elif protection == 'missing':
        path.unlink()
    elif protection == 'malformed':
        path.write_text('{broken')
    elif protection == 'oversized':
        control(app, unrelated='x' * (256 * 1024))
    elif protection in {'live', 'preparing'}:
        app.runtime = SimpleNamespace(workers={sid: {}} if protection == 'live' else {},
            _preparations={sid: {}} if protection == 'preparing' else {}, close=AsyncMock())
    elif protection == 'disabled':
        storage_cleanup.store(app, storage_cleanup.POLICY, {**settings, 'enabled': False, 'revision': 2})
    clock[0] += 31 * DAY
    before = app.operations.read({'sessionId': sid, 'id': 'p1'})
    result = await app.operations.expire_outputs(settings)
    assert result['expired'] == 0
    assert result['protected'] == (0 if protection == 'disabled' else 1)
    assert app.operations.read({'sessionId': sid, 'id': 'p1'}) == before


async def test_expiry_wakes_readers_but_exact_request_retry_does_not_execute(app, monkeypatch):
    clock = [1000]
    monkeypatch.setattr('amplifier_operations.journal.time.time', lambda: clock[0])
    settings = await enable(app)
    sid = app._session()['id']
    app.runtime = SimpleNamespace(control=AsyncMock(return_value={'result': {'success': True,
        'output': {'process_id': 'p1'}}}), close=AsyncMock())
    app.management = SimpleNamespace(ensure_runtime=AsyncMock(), setup_manager=None, global_setup_manager=None,
        workspace_setup_managers={}, provider_catalog=SimpleNamespace(close=AsyncMock()))
    args = {'sessionId': sid, 'requestId': 'original', 'command': 'printf fixture'}
    receipt = await app.dispatch('operations.submit', args)
    await admit_output(app)
    control(app)
    before = app.operations.status(sid, 'p1')
    waiter = asyncio.create_task(app.dispatch('operations.wait', {'sessionId': sid, 'id': 'p1',
        'afterRevision': before['revision'], 'waitMs': 5000}))
    await asyncio.sleep(0)
    assert not waiter.done()
    clock[0] += 31 * DAY
    assert (await app.operations.expire_outputs(settings))['expired'] == 1
    assert (await asyncio.wait_for(waiter, 1))['result']['outputAvailability'] == 'expired'
    assert await app.dispatch('operations.submit', args) == receipt
    with pytest.raises(AppError, match='different operation'):
        await app.dispatch('operations.submit', {**args, 'command': 'printf changed'})
    app.runtime.control.assert_awaited_once()


async def test_existing_storage_maintenance_enforces_and_records_one_bounded_page(app, monkeypatch):
    clock = [1000]
    monkeypatch.setattr('amplifier_operations.journal.time.time', lambda: clock[0])
    await enable(app)
    await admit_output(app)
    control(app)
    clock[0] += 31 * DAY
    monkeypatch.setattr(storage_cleanup, 'preview', AsyncMock(return_value={
        'rows': [], 'selectedFileBytes': 0, 'complete': True}))
    from amplifier_web import update_retention, runtime_environment_retention, managed_runner_updates
    from amplifier_web import test_dependency_layers, workspace_archive_retention
    monkeypatch.setattr(update_retention, 'maintenance_blocked', lambda manager: False)
    for owner, name in ((update_retention, 'reclaim'), (runtime_environment_retention, 'reclaim'), (managed_runner_updates, 'retire')):
        monkeypatch.setattr(owner, name, AsyncMock())
    monkeypatch.setattr(test_dependency_layers, 'retire', lambda *args: None)
    monkeypatch.setattr(workspace_archive_retention, 'retire', lambda *args: None)
    report = await storage_cleanup.run(app, 'maintenance', automatic=True)
    assert report['operationOutput']['expired'] == 1
    assert report['operationOutput']['removedOutputBytes'] == 6
    assert report['removedFileBytes'] == 0
    assert storage_cleanup.read(app)['lastCleanup'] == report
    assert await storage_cleanup.run(app, 'maintenance', automatic=True) == report
