"""Temporary custody is explicit; neither age nor a cache budget grants deletion."""
import asyncio
from contextlib import closing
import copy
import hashlib
import json
import sqlite3
import threading
import time
from types import SimpleNamespace

from filelock import FileLock
import pytest

from amplifier_web import task_resources as resources, task_environments
from amplifier_web import storage_cleanup, workspace_archive_retention as owner
from test_storage_cleanup import app


SETTINGS = {**storage_cleanup.DEFAULTS, 'workspaceArchiveKeepDays': 7}


def saved(home, identity='check-1', days=8):
    row = {'id': identity, 'sessionId': 'root', 'workspaceId': 'workspace', 'source': '/source',
        'kind': 'test-environment', 'status': 'retired', 'environmentState': 'removed',
        'profileId': 'python-project', 'revision': 7, 'universeId': 'synthetic-env',
        'workspaceArchivePolicy': owner.capture(SETTINGS),
        'workspaceArchiveRetiredAt': time.time() - days * 86400}
    root = resources.directory(home, row)
    folder = root / ('retained-' + hashlib.sha256(b'remove-1').hexdigest())
    folder.mkdir(parents=True)
    paths = [root/'source.tar', folder/'workspace.tar', folder/'artifacts.tar']
    for path, content in zip(paths, (b'initial input', b'installed temporary dependencies', b'final patch and reports')):
        path.write_bytes(content)
    checksum = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    source = paths[0].stat().st_size
    archives = [{'id': row['universeId'], 'source': role, 'destination': str(path),
        'verified': True, 'bytes': path.stat().st_size, 'sha256': checksum(path)}
        for role, path in zip(('/workspace', '/artifacts'), paths[1:])]
    retained = {'verified': True, 'policy': 'files', 'sourceSha256': checksum(paths[0]),
        'sourceBytes': source, 'archiveBytes': sum(a['bytes'] for a in archives),
        'totalBytes': sum(p.stat().st_size for p in paths)}
    row['snapshot'] = {'archive_sha256': checksum(paths[0])}
    row['retainedFiles'] = copy.deepcopy(retained)
    row['lastOperation'] = {'requestId': 'remove-1', 'sessionId': 'root', 'resourceId': identity,
        'state': 'completed', 'action': 'workspace.environments.remove',
        'result': {'ok': True, 'status': 'removed', 'universeId': row['universeId'],
            'retention': retained, 'archives': archives}}
    put(home, row)
    return row, paths


def put(home, row):
    with closing(resources.connect(home)) as db, db:
        resources.save(db, row)


def choice(home):
    return {r['id']: r for r in owner.inspect(home, SETTINGS)['candidates'] if r['selected']}


def test_expiry_preserves_source_results_original_receipt_and_reports_absence(tmp_path):
    row, paths = saved(tmp_path)
    original = copy.deepcopy(row['lastOperation'])
    events = []
    owner.retire(tmp_path, SETTINGS, choice(tmp_path), lambda *e: events.append(e), lambda r: None)
    assert [p.exists() for p in paths] == [True, False, True]
    with closing(resources.connect(tmp_path)) as db:
        after = resources.get(db, 'root', row['id'])
    assert after['lastOperation'] == original
    assert after['workspaceArchiveExpiry']['sha256'] == original['result']['archives'][0]['sha256']
    assert [event[0] for event in events] == ['attempting', 'completed']
    assert owner.inspect(tmp_path, SETTINGS)['candidates'] == []
    from amplifier_web.saved_test_storage import read
    item, = read(SimpleNamespace(data_dir=tmp_path))['items']
    assert item['workspaceAvailability'] == 'expired'
    assert item['totalBytes'] == original['result']['retention']['totalBytes']  # Historical, not remaining bytes.
    assert task_environments.public(tmp_path, after)['retention']['preserved'] is False


@pytest.mark.parametrize('change', ['legacy', 'keep', 'active', 'unknown', 'pending', 'preparation',
    'remote', 'full', 'bad-source', 'wrong-root', 'wrong-universe', 'wrong-destination', 'already-expired', 'no-retired-time'])
def test_no_implicit_disposal_from_idle_age_or_old_receipts(tmp_path, change):
    row, paths = saved(tmp_path, days=10000)
    if change in {'legacy', 'keep'}: row.pop('workspaceArchivePolicy')
    if change == 'active': row['status'] = 'ready'
    if change == 'unknown': row['lastOperation']['state'] = 'outcome_unknown'
    if change == 'pending': row['pendingRequest'] = 'unconfirmed-operation'
    if change == 'preparation': row['preparationPending'] = True
    if change == 'remote': row['testMachine'] = {'id': 'remote'}
    if change == 'full': row['lastOperation']['result']['retention']['policy'] = 'full'
    if change == 'bad-source': row['snapshot']['archive_sha256'] = 'f'*64
    if change == 'wrong-root': row['lastOperation']['sessionId'] = 'other'
    if change == 'wrong-universe': row['lastOperation']['result']['universeId'] = 'other'
    if change == 'wrong-destination': row['lastOperation']['result']['archives'][0]['destination'] = str(paths[0])
    if change == 'already-expired': row['workspaceArchiveExpiry'] = {'status': 'expired'}
    if change == 'no-retired-time': row.pop('workspaceArchiveRetiredAt')
    put(tmp_path, row)
    assert choice(tmp_path) == {}
    assert all(p.exists() for p in paths)


def test_duration_captured_at_admission_cannot_be_shortened_and_disable_pauses(tmp_path):
    saved(tmp_path, days=5)
    shorter = {**SETTINGS, 'workspaceArchiveKeepDays': 1}
    assert not owner.inspect(tmp_path, shorter)['candidates'][0]['selected']
    assert not owner.inspect(tmp_path, {**SETTINGS, 'workspaceArchiveKeepDays': 30})['candidates'][0]['selected']
    assert owner.inspect(tmp_path, storage_cleanup.DEFAULTS)['candidates'] == []
    assert owner.capture(SETTINGS, keep=True) is None
    assert owner.capture(storage_cleanup.DEFAULTS) is None


def test_metadata_pages_never_read_archive_bodies_or_create_missing_store(tmp_path, monkeypatch):
    assert owner.inspect(tmp_path, SETTINGS)['candidates'] == []
    assert list(tmp_path.iterdir()) == []
    for index in range(owner.PAGE + 3): saved(tmp_path, f'check-{index}')
    original = owner.checked_file
    def stat_only(*args, **kwargs):
        assert not kwargs.get('verify')
        return original(*args, **kwargs)
    monkeypatch.setattr(owner, 'checked_file', stat_only)
    first = owner.inspect(tmp_path, SETTINGS)
    second = owner.inspect(tmp_path, SETTINGS, first['workspaceArchiveCursor'])
    assert len(first['candidates']) == owner.PAGE and not first['complete']
    assert len(second['candidates']) == 3 and second['complete']
    assert not {r['id'] for r in first['candidates']} & {r['id'] for r in second['candidates']}


@pytest.mark.parametrize('changed', ['workspace', 'source', 'results', 'symlink', 'locked', 'revision', 'unresolved'])
def test_change_after_selection_or_unresolved_operation_keeps_workspace(tmp_path, changed):
    row, paths = saved(tmp_path)
    selected = choice(tmp_path)
    if changed in {'workspace', 'source', 'results'}:
        path = paths[{'source': 0, 'workspace': 1, 'results': 2}[changed]]
        path.write_bytes(b'X' * path.stat().st_size)
    if changed == 'symlink':
        target = tmp_path / 'other'; target.write_bytes(paths[1].read_bytes());paths[1].unlink();paths[1].symlink_to(target)
    if changed == 'revision': row['revision'] += 1;put(tmp_path, row)
    events = []
    def guard(row):
        if changed == 'unresolved': raise ValueError('Unresolved original removal')
    lock = FileLock(str(tmp_path/'workspace-resources'/('environment-'+row['id']+'.lock')))
    if changed == 'locked': lock.acquire()
    try: owner.retire(tmp_path, SETTINGS, selected, lambda *e: events.append(e), guard)
    finally:
        if changed == 'locked': lock.release()
    assert paths[1].exists() and not events


def test_callback_boundary_rechecks_files_and_crash_keeps_uncertainty(tmp_path):
    row, paths = saved(tmp_path)
    events = []
    def race(status, item):
        events.append(status)
        if status == 'attempting': paths[2].write_bytes(b'changed results')
    owner.retire(tmp_path, SETTINGS, choice(tmp_path), race, lambda r: None)
    assert events == ['attempting'] and paths[1].exists()


async def test_storage_owner_runs_off_loop_and_unknown_removal_blocks(app, monkeypatch):
    from amplifier_web import task_environments
    storage_cleanup.store(app, storage_cleanup.POLICY, {**SETTINGS, 'keepDays': 3650, 'maxUnusedBytes': 0})
    row, paths = saved(app.data_dir)
    journal = sqlite3.connect(':memory:')
    journal.execute('CREATE TABLE operation_requests(id TEXT,session_id TEXT,value TEXT)')
    journal.execute('INSERT INTO operation_requests VALUES(?,?,?)', ('old', 'root', json.dumps({
        'resourceId': row['id'], 'sessionId': 'root', 'action': 'workspace.environments.remove', 'state': 'outcome_unknown'})))
    app.operations = SimpleNamespace(requests=SimpleNamespace(journal=SimpleNamespace(lock=threading.RLock(), db=journal)))
    original_check = owner.checked_file
    def metadata_only(*args, **kwargs):
        assert not kwargs.get('verify'), 'Unresolved removal must not read archive contents'
        return original_check(*args, **kwargs)
    with monkeypatch.context() as held:
        held.setattr(owner, 'checked_file', metadata_only)
        result = await storage_cleanup.run(app, 'held', automatic=True)
    assert not result['effects'] and paths[1].exists()
    journal.execute('DELETE FROM operation_requests')
    started, resume = threading.Event(), threading.Event()
    original = owner.checked_file
    def verifying(*args, **kwargs):
        if kwargs.get('verify'):
            assert threading.current_thread() is not threading.main_thread()
            started.set(); assert resume.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(owner, 'checked_file', verifying)
    task = asyncio.create_task(storage_cleanup.run(app, 'expire', automatic=True))
    for _ in range(500):
        if started.is_set(): break
        await asyncio.sleep(.01)
    assert started.is_set() and not task.done()  # The app loop remains responsive during hashing.
    resume.set()
    result = await task
    assert result['status'] == 'completed' and not paths[1].exists()
    assert len(result['effects']) == 1
    assert await storage_cleanup.run(app, 'expire', automatic=True) == result


async def test_interrupted_unlink_never_replays_after_restart(app, monkeypatch):
    storage_cleanup.store(app, storage_cleanup.POLICY, {**SETTINGS, 'keepDays': 3650, 'maxUnusedBytes': 0})
    row, paths = saved(app.data_dir)
    monkeypatch.setattr(task_environments, 'require_resolved_removal', lambda *args: None)
    original = resources.save
    def fail_record(db, value):
        if value.get('workspaceArchiveExpiry'): raise OSError('simulated disk failure after unlink')
        original(db, value)
    monkeypatch.setattr(resources, 'save', fail_record)
    result = await storage_cleanup.run(app, 'interrupted', automatic=True)
    assert result['status'] == 'needs-inspection' and not paths[1].exists()
    assert result['effects'][0]['status'] == 'attempting'
    # A replacement at the old path is never treated as proof the old effect failed.
    paths[1].write_bytes(b'installed temporary dependencies')
    monkeypatch.setattr(resources, 'save', original)
    del app._storage_cleanup_lock
    next_result = await storage_cleanup.run(app, 'new-request', automatic=True)
    assert not next_result['effects'] and paths[1].exists()


def test_expiry_batch_is_bounded_without_discarding_remainder(tmp_path):
    for index in range(4): saved(tmp_path, f'check-{index}')
    events = []
    owner.retire(tmp_path, SETTINGS, choice(tmp_path), lambda *e: events.append(e), lambda r: None)
    assert sum(event[0] == 'completed' for event in events) == 2
    assert len(choice(tmp_path)) == 2


async def test_cancellation_during_verification_never_admits_unlink_later(app, monkeypatch):
    storage_cleanup.store(app, storage_cleanup.POLICY, {**SETTINGS, 'keepDays': 3650, 'maxUnusedBytes': 0})
    row, paths = saved(app.data_dir)
    monkeypatch.setattr(task_environments, 'require_resolved_removal', lambda *args: None)
    started, resume, finished = threading.Event(), threading.Event(), threading.Event()
    original = owner.checked_file
    def verifying(*args, **kwargs):
        if kwargs.get('verify'):
            started.set(); assert resume.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(owner, 'checked_file', verifying)
    original_retire = owner.retire
    def retiring(*args):
        try: return original_retire(*args)
        finally: finished.set()
    monkeypatch.setattr(owner, 'retire', retiring)
    task = asyncio.create_task(storage_cleanup.run(app, 'cancel-expiry', automatic=True))
    for _ in range(500):
        if started.is_set(): break
        await asyncio.sleep(.01)
    assert started.is_set()
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    resume.set()
    for _ in range(500):
        if finished.is_set(): break
        await asyncio.sleep(.01)
    assert finished.is_set() and paths[1].exists()
    receipt = storage_cleanup.record(app, 'storage-cleanup-run:cancel-expiry')
    assert receipt['status'] == 'interrupted' and not receipt['effects']


@pytest.mark.parametrize('field,value', [
    ('workspaceArchivePolicy', ['temporary']), ('snapshot', ['hash']),
    ('lastOperation', ['completed']), ('lastOperation.result', ['removed']),
    ('lastOperation.result.archives', [None, None]),
])
def test_malformed_retention_record_is_skipped_without_holding_later_work(tmp_path, field, value):
    row, paths = saved(tmp_path, 'malformed')
    target = row
    parts = field.split('.')
    for part in parts[:-1]: target = target[part]
    target[parts[-1]] = value
    put(tmp_path, row)
    saved(tmp_path, 'valid')
    assert set(choice(tmp_path)) == {'valid'}
    assert all(path.exists() for path in paths)
    with pytest.raises(ValueError):
        owner.candidate(tmp_path, row, SETTINGS, time.time())
