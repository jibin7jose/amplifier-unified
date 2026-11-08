import asyncio
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace
import uuid

import pytest

from amplifier_web.service import AppError, AppService
from amplifier_web.session_files import amplifier_home, project_slug
from amplifier_web import managed_deletion
from test_service import Runtime
from test_live_clients import command
from test_automatic_history import native_session


@pytest.fixture
async def app(tmp_path):
    service = AppService(tmp_path/'app', Runtime(), workspace=tmp_path)
    for client in ('web','agent'):
        service.clients.attach(client, kind='web' if client=='web' else 'api')
    yield service
    if not service.closed:
        await service.close()


async def create(app, command_id=None):
    result = await command(app, 'web', 'session.create', {'location':{'kind':'managed'}}, command_id=command_id)
    return app._session(result['sessionId'])


async def preview(app, sid, client='web'):
    return (await command(app, client, 'session.deletePreview', {'id':sid}, origin='agent' if client=='agent' else 'ui'))['result']


async def delete(app, value, client='web'):
    return await command(app, client, 'session.delete', {'id':value['id'],'confirmationToken':value['confirmationToken']},
                         origin='agent' if client=='agent' else 'ui',
                         **({'caller_session_id': value['id']} if client=='agent' else {}))


@pytest.mark.parametrize('client',['web','agent'])
async def test_delete_owned_files_history_and_creation_receipt_without_replay(app, tmp_path, client):
    row = await create(app, 'first-send'); sid=row['id']; files=Path(row['workspace'])
    (files/'result.txt').write_text('Delete only this result')
    native = native_session(files, sid)
    await app.history.refresh()
    # Runtime settings are separate from managed user files and native history.
    runtime = app.data_dir/'sessions'/sid; runtime.mkdir(parents=True, exist_ok=True)
    (runtime/'configuration.json').write_text('{}')
    value = await preview(app, sid, client)
    assert value['summary']['conversationCount']==1 and value['summary']['fileBytes']>0
    assert files.is_dir() and app._session(sid)
    result = await delete(app, value, client)
    assert result['result']=={'id':sid,'deleted':True,'cleanupPending':False}
    assert not files.parent.exists() and not runtime.exists()
    assert not (amplifier_home()/'projects'/project_slug(files)/'sessions'/sid).exists()
    assert not app.state['sessions']
    assert not app.runtime.sent and not app.runtime.started
    again=await delete(app,value,client);assert again['result']['deleted']
    retry=await command(app,'web','session.create',{'location':{'kind':'managed'}},command_id='first-send')
    assert retry['deleted'] and retry['accepted'] is False
    with pytest.raises(AppError,match='permanently deleted'):
        await command(app,'web','session.create',{'id':sid,'location':{'kind':'managed'}})
    await app.history.refresh();assert not app.state['sessions']
    await app.close()
    restored=AppService(app.data_dir,Runtime(),workspace=tmp_path)
    try:
        await restored.history.refresh();assert not restored.state['sessions']
        assert not files.parent.exists()
    finally:await restored.close()


async def test_workspace_and_legacy_blank_location_cannot_delete(app):
    value=await command(app,'web','session.create',{})
    sid=value['sessionId'];before=copy.deepcopy(app._session(sid))
    with pytest.raises(AppError,match='Only chats created without a workspace'):
        await preview(app,sid)
    with pytest.raises(AppError):
        await command(app,'web','session.delete',{'id':sid})
    assert app._session(sid)==before


async def test_confirmation_expiry_changes_and_active_work_refuse(app):
    row=await create(app);files=Path(row['workspace']);sid=row['id']
    value=await preview(app,sid)
    (files/'new.txt').write_text('Changed after review')
    with pytest.raises(AppError,match='changed since'):
        await delete(app,value)
    value=await preview(app,sid)
    encoded=app.db.execute('SELECT value FROM managed_deletions WHERE id=?',(sid,)).fetchone()[0]
    saved=json.loads(encoded);saved['expiresAt']=0
    app.db.execute('UPDATE managed_deletions SET value=? WHERE id=?',(json.dumps(saved),sid));app.db.commit()
    with pytest.raises(AppError,match='expired'):await delete(app,value)
    row['status']='working'
    with pytest.raises(AppError,match='active work'):await preview(app,sid)
    row['status']='idle';row['approvals']=[{'id':'a','status':'pending'}]
    with pytest.raises(AppError,match='active work'):await preview(app,sid)
    assert (files/'new.txt').read_text()=='Changed after review'


def test_startup_preview_expiry_preserves_unknown_and_current_records(tmp_path, monkeypatch):
    monkeypatch.setattr(managed_deletion.time, 'time', lambda: 1000)
    values = {
        'expired': '{"expiresAt":999}',
        'expired-fractional': '{"expiresAt":999.9}',
        'expired-negative': '{"expiresAt":-1}',
        'expired-minimum-integer': '{"expiresAt":-9223372036854775808}',
        'expired-minimum-finite': '{"expiresAt":-1.7976931348623157e308}',
        'positive-overflow': '{"expiresAt":1e999}',
        'negative-overflow': '{"expiresAt":-1e999}',
        'boundary': '{"expiresAt":1000}',
        'future': '{"expiresAt":1001}',
        'missing': '{}',
        'text': '{"expiresAt":"0"}',
        'null': '{"expiresAt":null}',
        'boolean': '{"expiresAt":false}',
        'malformed': 'incomplete saved preview',
    }
    with sqlite3.connect(tmp_path/'app.sqlite3') as db:
        managed_deletion.initialize(db)
        db.executemany('INSERT INTO managed_deletions VALUES (?,?,?,?)',
                       [(key, key, 'preview', value) for key, value in values.items()])
        db.commit()
        managed_deletion.recover(tmp_path, db, {})
        remaining = dict(db.execute('SELECT id,value FROM managed_deletions'))
        assert remaining == {key: value for key, value in values.items() if not key.startswith('expired')}
        assert not db.in_transaction


@pytest.mark.parametrize('history', [False, True])
async def test_restart_reaps_only_expired_unconfirmed_preview(app, tmp_path, history):
    expired, fresh, completed = [await create(app) for _ in range(3)]
    files = Path(expired['workspace'])
    (files/'keep.txt').write_text('An expired review is not a deletion.')
    native_session(files, expired['id'])
    if history:
        review = (await app.dispatch('storage.historyDeletePreview', {
            'project': project_slug(files), 'nativeId': expired['id']}))['result']
    else:
        review = await preview(app, expired['id'])
    expired_key = review['id']
    encoded = app.db.execute('SELECT value FROM managed_deletions WHERE id=?', (expired_key,)).fetchone()[0]
    plan = json.loads(encoded); plan['expiresAt'] = 0
    app.db.execute('UPDATE managed_deletions SET value=? WHERE id=?', (json.dumps(plan), expired_key))
    app.db.commit()
    await preview(app, fresh['id'])
    completed_review = await preview(app, completed['id'])
    await delete(app, completed_review)
    retained = app.db.execute('SELECT * FROM managed_deletions WHERE id != ? ORDER BY id', (expired_key,)).fetchall()
    await app.close()
    restored = AppService(app.data_dir, Runtime(), workspace=tmp_path)
    try:
        assert restored.db.execute('SELECT * FROM managed_deletions ORDER BY id').fetchall() == retained
        assert (files/'keep.txt').read_text() == 'An expired review is not a deletion.'
        assert (amplifier_home()/'projects'/project_slug(files)/'sessions'/expired['id']).exists()
        assert managed_deletion.removed(restored.db, completed['id'])
        assert not managed_deletion.removed(restored.db, expired['id'])
        with pytest.raises(AppError, match='Review this chat'):
            await restored.dispatch('session.delete', {'id':expired_key, 'confirmationToken':review['confirmationToken']})
        assert not restored.runtime.sent and not restored.runtime.started
    finally:
        await restored.close()


async def test_symlink_and_unowned_marker_fail_without_touching_outside(app,tmp_path):
    row=await create(app);files=Path(row['workspace']);outside=tmp_path/'outside';outside.mkdir();(outside/'keep').write_text('keep')
    (files/'link').symlink_to(outside, target_is_directory=True)
    with pytest.raises(AppError,match='symbolic link'):await preview(app,row['id'])
    assert (outside/'keep').read_text()=='keep'
    (files/'link').unlink()
    marker=files.parent/'managed-chat.json';saved=marker.read_text();marker.write_text('{}')
    with pytest.raises(AppError,match='ownership'):await preview(app,row['id'])
    marker.write_text(saved)
    (files/'safe.txt').write_text('safe')
    value=await preview(app,row['id']);files.rename(files.with_name('old-files'));files.symlink_to(outside, target_is_directory=True)
    with pytest.raises(AppError,match='symbolic link'):await delete(app,value)
    assert (outside/'keep').read_text()=='keep'


async def test_other_root_sharing_managed_folder_is_blocked_even_without_location(app):
    row=await create(app)
    second=copy.deepcopy(row);second['id']=str(uuid.uuid4());second.pop('location');app.state['sessions'].append(second)
    with pytest.raises(AppError,match='Another conversation uses'):
        await preview(app,row['id'])
    assert Path(row['workspace']).is_dir()


async def test_children_are_owned_and_unrelated_history_refuses(app):
    row=await create(app);files=Path(row['workspace'])
    native_session(files,row['id'])
    child=str(uuid.uuid4());native_session(files,child,metadata={'parent_id':row['id']})
    value=await preview(app,row['id']);assert value['summary']['workerCount']==1
    foreign=str(uuid.uuid4());native_session(files,foreign)
    with pytest.raises(AppError,match='Unrelated history'):await preview(app,row['id'])
    import shutil
    shutil.rmtree(amplifier_home()/'projects'/project_slug(files)/'sessions'/foreign)
    value=await preview(app,row['id']);await delete(app,value)
    assert not (amplifier_home()/'projects'/project_slug(files)/'sessions'/child).exists()


def worker_history(row):
    files = Path(row['workspace'])
    root = native_session(files, row['id'])
    child = '0000000000000000-123456789abcdef0_self'
    nested = '123456789abcdef0-abcdef0123456789_self'
    return {
        row['id']: root,
        child: native_session(files, child, metadata={'parent_id': row['id']}),
        nested: native_session(files, nested, metadata={'parent_id': child}),
    }


@contextmanager
def shared_lease(store, **diagnostics):
    held = store.acquire(**diagnostics)
    try:
        yield held
    finally:
        held.release()


@pytest.mark.parametrize('client', ['web', 'agent'])
async def test_native_workers_preview_and_confirm_keep_complete_scope(app, tmp_path, monkeypatch, client):
    from amplifier_foundation.session.shared_state import SharedSessionStore
    row = await create(app); sid = row['id']; files = Path(row['workspace'])
    owned = worker_history(row)
    legacy = str(uuid.uuid4())
    owned[legacy] = native_session(files, legacy, metadata={'parent_id': sid})
    store = SharedSessionStore(files, legacy)
    with shared_lease(store, app='synthetic-legacy-worker') as lease:
        lease.write([{'role': 'user', 'content': 'Legacy worker checkpoint'}], bundle='work')
    # Relocated captures and runtime records still belong to native-only workers.
    captures = tmp_path/'captures'/project_slug(files)/'sessions'
    monkeypatch.setenv('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', str(tmp_path/'captures'))
    for identity in owned:
        capture = captures/identity
        capture.mkdir(parents=True)
        (capture/'events.jsonl').write_text('synthetic capture')
        runtime = app.data_dir/'sessions'/identity
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime/'configuration.json').write_text('{}')
    other = await create(app)
    (Path(other['workspace'])/'keep.txt').write_text('Unrelated chat')
    before = {path: path.read_bytes() for directory in owned.values() for path in directory.rglob('*') if path.is_file()}
    value = await preview(app, sid, client)
    assert value['summary']['workerCount'] == 3 and value['summary']['conversationCount'] == 1
    assert all(path.read_bytes() == content for path, content in before.items())
    plan = json.loads(app.db.execute('SELECT value FROM managed_deletions WHERE id=?', (sid,)).fetchone()[0])
    assert set(plan['nativeIds']) == set(owned) <= set(plan['ids'])
    assert set(plan['sharedIds']) == {sid, legacy}
    assert len(plan['coordinationLocks']) == 2 and len(plan['metadataLocks']) == 4
    expected_paths = {*owned.values(), *(captures/identity for identity in owned), *(app.data_dir/'sessions'/identity for identity in owned), store.checkpoint_path}
    assert expected_paths <= {Path(entry['path']) for entry in plan['paths']}
    assert (await delete(app, value, client))['result'] == {'id': sid, 'deleted': True, 'cleanupPending': False}
    assert not files.parent.exists() and not store.checkpoint_path.exists()
    assert all(not path.exists() for path in expected_paths)
    assert (Path(other['workspace'])/'keep.txt').read_text() == 'Unrelated chat'
    assert all(managed_deletion.removed(app.db, identity) for identity in owned)
    assert not app.runtime.started and not app.runtime.sent
    # Native metadata coordination remains stable after history is gone.
    assert all(Path(path).is_file() for path in plan['metadataLocks'])
    assert (await delete(app, value, client))['result']['deleted']
    await app.close()
    restored = AppService(app.data_dir, Runtime(), workspace=tmp_path)
    try:
        await restored.history.refresh()
        assert all(managed_deletion.removed(restored.db, identity) for identity in owned)
        assert not files.parent.exists()
        assert not restored.runtime.started and not restored.runtime.sent
        assert (Path(other['workspace'])/'keep.txt').exists()
    finally:
        await restored.close()


@pytest.mark.parametrize('change', ['ancestry', 'transcript', 'new-worker', 'checkpoint'])
async def test_native_worker_changes_after_preview_preserve_files(app, change):
    from amplifier_foundation.session.shared_state import SharedSessionStore
    row = await create(app); files = Path(row['workspace']); owned = worker_history(row)
    child = next(identity for identity in owned if identity != row['id'])
    value = await preview(app, row['id'])
    if change == 'ancestry':
        metadata = owned[child]/'metadata.json'
        metadata.write_text(json.dumps({**json.loads(metadata.read_text()), 'parent_id': child}))
    elif change == 'transcript':
        (owned[child]/'transcript.jsonl').write_text('Unreviewed transcript')
    elif change == 'new-worker':
        native_session(files, '0000000000000000-abcdef0123456789_self', metadata={'parent_id': row['id']})
    else:
        store = SharedSessionStore(files, row['id'])
        with shared_lease(store, app='synthetic-checkpoint-change') as lease:
            lease.write([], bundle='work')
    with pytest.raises(AppError, match='changed|Unrelated history'):
        await delete(app, value)
    assert files.exists() and all(path.exists() for path in owned.values())
    assert app.db.execute('SELECT phase FROM managed_deletions WHERE id=?', (row['id'],)).fetchone()[0] == 'preview'


@pytest.mark.parametrize('fault', ['foreign', 'cycle', 'missing-metadata', 'link', 'capture', 'fence'])
async def test_native_worker_ownership_faults_refuse_preview(app, tmp_path, monkeypatch, fault):
    row = await create(app); files = Path(row['workspace']); owned = worker_history(row)
    child = next(identity for identity in owned if identity != row['id'])
    if fault in {'foreign', 'cycle'}:
        metadata = owned[child]/'metadata.json'
        metadata.write_text(json.dumps({**json.loads(metadata.read_text()), 'parent_id': child if fault == 'cycle' else 'foreign-root'}))
    elif fault == 'missing-metadata':
        (owned[child]/'metadata.json').unlink()
    elif fault == 'link':
        (owned[child]/'outside').symlink_to(tmp_path/'not-owned')
    elif fault == 'fence':
        (owned[child]/'transfer-fence.json').write_text('Synthetic uncertain fence')
    else:
        capture = tmp_path/'captures'/project_slug(files)/'sessions'/'foreign-worker'
        capture.mkdir(parents=True)
        monkeypatch.setenv('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', str(tmp_path/'captures'))
    with pytest.raises(AppError, match='Unrelated history|ownership|symbolic|Unexpected capture|transfer fence'):
        await preview(app, row['id'])
    assert files.exists() and all(path.exists() for path in owned.values())


@pytest.mark.parametrize(('owner', 'refusal'), [
    pytest.param('root', 'Another application owns this conversation. Close it there before deleting this chat.', id='root'),
    pytest.param('metadata', 'Another application owns this conversation. Close it there before deleting this chat.', id='metadata'),
    pytest.param('transfer', 'This task is fenced for transfer; it cannot change or start work here.', id='transfer'),
])
async def test_native_workers_cannot_bypass_execution_or_metadata_ownership(app, owner, refusal):
    from amplifier_foundation.session.shared_state import SharedSessionStore
    from filelock import FileLock
    row = await create(app); files = Path(row['workspace']); owned = worker_history(row)
    store = SharedSessionStore(files, row['id'])
    held = None
    if owner in {'root', 'transfer'}:
        held = store.acquire(app='synthetic-other-host')
        if owner == 'transfer':
            held.fence_transfer('synthetic-transfer', 'synthetic-destination')
            held.release(); held = None
    else:
        child = next(path for identity, path in owned.items() if identity != row['id'])
        held = FileLock(child.parent/f'.{child.name}.metadata.lock')
        held.acquire(timeout=0)
    before = {path: path.read_bytes() for directory in owned.values() for path in directory.rglob('*') if path.is_file()}
    value = await preview(app, row['id'])
    try:
        # A root transfer fence is rejected by the service write boundary before
        # deletion acquires locks; ownership conflicts reach deletion's lock check.
        with pytest.raises(AppError) as caught:
            await delete(app, value)
        assert str(caught.value) == refusal
        assert all(path.exists() for path in owned.values()) and files.exists()
        assert not store.checkpoint_path.exists()  # execution lock is required even without a checkpoint
        assert all(path.read_bytes() == content for path, content in before.items())
        assert app.db.execute('SELECT phase FROM managed_deletions WHERE id=?', (row['id'],)).fetchone()[0] == 'preview'
        assert not managed_deletion.removed(app.db, row['id']) and not row.get('_deleting')
        if owner == 'transfer':
            assert store.transfer_fence_path in before
            assert store.transfer_fence_path.read_bytes() == before[store.transfer_fence_path]
    finally:
        if held is not None:
            held.release()
    if owner == 'metadata':
        # Failed metadata acquisition must release the earlier shared root lease.
        with shared_lease(store, app='synthetic-lock-recheck'):
            pass


@pytest.mark.parametrize('owner', ['lease', 'transfer'])
async def test_uuid_worker_without_checkpoint_still_requires_its_lease(app, owner):
    from amplifier_foundation.session.shared_state import SharedSessionStore
    row = await create(app); files = Path(row['workspace']); owned = worker_history(row)
    legacy = str(uuid.uuid4())
    directory = native_session(files, legacy, metadata={'parent_id': row['id']})
    store = SharedSessionStore(files, legacy)
    held = store.acquire(app='synthetic-legacy-owner')
    try:
        if owner == 'transfer':
            held.fence_transfer('synthetic-transfer', 'synthetic-destination')
            held.release(); held = None
        value = await preview(app, row['id'])
        with pytest.raises(AppError, match='Another application owns'):
            await delete(app, value)
        assert not store.checkpoint_path.exists()
        assert directory.exists() and all(path.exists() for path in owned.values())
        with shared_lease(SharedSessionStore(files, row['id']), app='synthetic-root-release-recheck'):
            pass
    finally:
        if held is not None:
            held.release()


async def test_metadata_protocol_survives_cross_thread_release(tmp_path):
    from amplifier_foundation.session.metadata import metadata_lock
    from filelock import FileLock, Timeout
    directory = tmp_path/'root_worker'; directory.mkdir()
    path = directory.parent/f'.{directory.name}.metadata.lock'
    with metadata_lock(directory):
        inode = path.stat().st_ino
        with pytest.raises(ValueError, match='Another application owns'):
            await asyncio.to_thread(managed_deletion._hold, {'nativeIds': [], 'metadataLocks': [str(path)]})
    held = await asyncio.to_thread(managed_deletion._hold, {'nativeIds': [], 'metadataLocks': [str(path)]})
    try:
        with pytest.raises(Timeout):
            with FileLock(path, timeout=0):
                pass
    finally:
        for handle in reversed(held):
            handle.release()
    with metadata_lock(directory):
        assert path.stat().st_ino == inode


def test_partial_shared_acquisition_releases_earlier_lease(tmp_path):
    from amplifier_foundation.session.shared_state import SharedSessionStore
    first = SharedSessionStore(tmp_path, 'a-root')
    last = SharedSessionStore(tmp_path, 'z-worker')
    blocker = last.acquire(app='synthetic-blocker')
    try:
        with pytest.raises(ValueError, match='Another application owns'):
            managed_deletion._hold({'workspace': str(tmp_path), 'nativeIds': ['a-root', 'z-worker'], 'sharedIds': ['a-root', 'z-worker']})
        with shared_lease(first, app='synthetic-release-recheck'):
            pass
    finally:
        blocker.release()


def fail_release_diagnostics(monkeypatch, store):
    """Fail the real HeldSession diagnostic write, not its finally/unlock."""
    from amplifier_foundation.session import shared_state
    original = shared_state._atomic_json
    attempts = []
    def write(path, value):
        if Path(path) == store.checkpoint_path.with_name('owner.json') and value.get('active') is False:
            attempts.append(path)
            raise OSError('Synthetic owner diagnostics failure')
        return original(path, value)
    monkeypatch.setattr(shared_state, '_atomic_json', write)
    return attempts


def two_shared_stores(workspace):
    from amplifier_foundation.session.shared_state import SharedSessionStore
    return [SharedSessionStore(workspace, str(uuid.UUID(int=value))) for value in (1, 2)]


def test_release_error_is_reported_after_all_shared_handles_are_attempted(tmp_path, monkeypatch):
    first, second = two_shared_stores(tmp_path)
    handles = [first.acquire(app='synthetic-root'), second.acquire(app='synthetic-UUID-worker')]
    with monkeypatch.context() as patcher:
        attempts = fail_release_diagnostics(patcher, second)
        with pytest.raises(ExceptionGroup, match='Conversation lock release failed') as caught:
            managed_deletion._release_all(handles)
        assert attempts and len(caught.value.exceptions) == 1
        assert str(caught.value.exceptions[0]) == 'Synthetic owner diagnostics failure'
    for store in (first, second):
        with shared_lease(store, app='synthetic-all-release-recheck'):
            pass


def test_partial_rollback_keeps_acquisition_cause_and_releases_all(tmp_path, monkeypatch, caplog):
    from amplifier_foundation.session.shared_state import SharedSessionStore
    first, second = two_shared_stores(tmp_path)
    blocked = SharedSessionStore(tmp_path, 'z-blocked')
    blocker = blocked.acquire(app='synthetic-blocker')
    try:
        with monkeypatch.context() as patcher:
            attempts = fail_release_diagnostics(patcher, second)
            with pytest.raises(ValueError, match='Another application owns') as caught:
                managed_deletion._hold({
                    'workspace': str(tmp_path),
                    'nativeIds': [store.session_id for store in (first, second, blocked)],
                })
            assert attempts and caught.value.__cause__ is not None
            assert any('cleanup also failed' in note for note in caught.value.__cause__.__notes__)
            assert 'Synthetic owner diagnostics failure' in caplog.text
        for store in (first, second):
            with shared_lease(store, app='synthetic-rollback-recheck'):
                pass
    finally:
        blocker.release()


async def test_cancel_late_result_releases_two_real_leases_despite_diagnostics_error(tmp_path, monkeypatch, caplog):
    from amplifier_foundation.session.metadata import metadata_lock
    first, second = two_shared_stores(tmp_path)
    native = tmp_path/'synthetic_worker'; native.mkdir()
    lock = native.parent/f'.{native.name}.metadata.lock'
    with metadata_lock(native):
        inode = lock.stat().st_ino
    loop = asyncio.get_running_loop()
    acquired, cleaned = asyncio.Event(), asyncio.Event()
    finish = threading.Event()
    original_hold, original_release = managed_deletion._hold, managed_deletion._release_all
    causes = []
    def delayed(plan):
        handles = original_hold(plan)
        loop.call_soon_threadsafe(acquired.set)
        if not finish.wait(5):
            original_release(handles)
            raise TimeoutError('Synthetic late-result gate timed out')
        return handles  # the callback receives two actual HeldSessions and a metadata flock
    def observe_release(handles, *, cause=None):
        causes.append(cause)
        try:
            return original_release(handles, cause=cause)
        finally:
            cleaned.set()
    with monkeypatch.context() as patcher:
        attempts = fail_release_diagnostics(patcher, second)
        patcher.setattr(managed_deletion, '_hold', delayed)
        patcher.setattr(managed_deletion, '_release_all', observe_release)
        task = asyncio.create_task(managed_deletion._acquire({
            'workspace': str(tmp_path), 'nativeIds': [first.session_id, second.session_id],
            'metadataLocks': [str(lock)],
        }))
        try:
            await asyncio.wait_for(acquired.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError) as caught:
                await task
        finally:
            finish.set()
        await asyncio.wait_for(cleaned.wait(), 5)
        assert attempts and causes == [caught.value]
        assert any('cleanup also failed' in note for note in caught.value.__notes__)
        assert 'Synthetic owner diagnostics failure' in caplog.text
    for store in (first, second):
        with shared_lease(store, app='synthetic-cancel-two-lease-recheck'):
            pass
    with metadata_lock(native):
        assert lock.stat().st_ino == inode


async def test_dispatch_final_cleanup_does_not_mask_primary_failure_or_leak_root(app, monkeypatch, caplog):
    from amplifier_foundation.session.shared_state import SharedSessionStore
    row = await create(app); files = Path(row['workspace'])
    worker_history(row)
    worker_id = str(uuid.uuid4())
    native_session(files, worker_id, metadata={'parent_id': row['id']})
    root, worker = (SharedSessionStore(files, identity) for identity in (row['id'], worker_id))
    value = await preview(app, row['id'])
    original_hold, original_inventory = managed_deletion._hold, managed_deletion._inventory
    def hold(plan):
        # Put the worker last to reproduce root starvation on reverse release.
        return original_hold({**plan, 'sharedIds': [row['id'], worker_id]})
    def changed(path):
        raise ValueError('Synthetic primary inventory failure')
    with monkeypatch.context() as patcher:
        attempts = fail_release_diagnostics(patcher, worker)
        patcher.setattr(managed_deletion, '_hold', hold)
        # Plan enumeration must finish before failing the post-acquisition check.
        async def acquire(plan):
            handles = await asyncio.to_thread(hold, plan)
            patcher.setattr(managed_deletion, '_inventory', changed)
            return handles
        patcher.setattr(managed_deletion, '_acquire', acquire)
        with pytest.raises(AppError, match='Synthetic primary inventory failure') as caught:
            await delete(app, value)
        assert attempts and any('cleanup also failed' in note for note in caught.value.__notes__)
        assert 'Synthetic owner diagnostics failure' in caplog.text
    assert managed_deletion._inventory is original_inventory and files.exists()
    for store in (root, worker):
        with shared_lease(store, app='synthetic-final-cleanup-recheck'):
            pass


async def test_cancelled_acquisition_releases_handles_that_finish_late(tmp_path, monkeypatch):
    from amplifier_foundation.session.shared_state import SharedSessionStore
    from amplifier_foundation.session.metadata import metadata_lock
    loop = asyncio.get_running_loop()
    acquired, released = asyncio.Event(), asyncio.Event()
    finish = threading.Event()
    original = managed_deletion._hold
    def delayed(plan):
        handles = original(plan)
        loop.call_soon_threadsafe(acquired.set)
        try:
            if not finish.wait(5):
                raise TimeoutError('Synthetic acquisition gate timed out')
        except BaseException:
            for handle in reversed(handles):
                handle.release()
            raise
        def release():
            for handle in reversed(handles):
                handle.release()
            released.set()
        return [SimpleNamespace(release=release)]
    monkeypatch.setattr(managed_deletion, '_hold', delayed)
    native = tmp_path/'synthetic-root_worker'; native.mkdir()
    lock = native.parent/f'.{native.name}.metadata.lock'
    with metadata_lock(native):
        inode = lock.stat().st_ino
    task = asyncio.create_task(managed_deletion._acquire({
        'workspace': str(tmp_path), 'nativeIds': ['synthetic-root'], 'metadataLocks': [str(lock)]}))
    try:
        await asyncio.wait_for(acquired.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        finish.set()
    await asyncio.wait_for(released.wait(), 5)
    with shared_lease(SharedSessionStore(tmp_path, 'synthetic-root'), app='synthetic-cancel-recheck'):
        pass
    with metadata_lock(native):
        assert lock.stat().st_ino == inode


@pytest.mark.parametrize('identity', ['root_worker', 'root.worker', 'a'*129])
def test_native_identity_support_does_not_relax_shared_root_contract(tmp_path, identity):
    from amplifier_foundation.session.shared_state import SharedSessionStore
    assert not managed_deletion.shared_identity(identity)
    with pytest.raises(ValueError, match='session_id must'):
        SharedSessionStore(tmp_path, identity)


async def test_native_worker_cleanup_recovers_with_stable_lock_inodes(app, tmp_path, monkeypatch):
    row = await create(app); files = Path(row['workspace']); owned = worker_history(row)
    value = await preview(app, row['id'])
    original = managed_deletion._purge
    monkeypatch.setattr(managed_deletion, '_purge', lambda *args: (_ for _ in ()).throw(OSError('Synthetic cleanup interruption')))
    assert (await delete(app, value))['result']['cleanupPending']
    saved = json.loads(app.db.execute('SELECT value FROM managed_deletions WHERE id=?', (row['id'],)).fetchone()[0])
    locks = {Path(path): Path(path).stat().st_ino for path in [*saved['coordinationLocks'], *saved['metadataLocks']]}
    assert not files.exists()
    await app.close()
    monkeypatch.setattr(managed_deletion, '_purge', original)
    restored = AppService(app.data_dir, Runtime(), workspace=tmp_path)
    try:
        assert restored.db.execute('SELECT phase FROM managed_deletions WHERE id=?', (row['id'],)).fetchone()[0] == 'done'
        assert not files.parent.exists() and all(not path.exists() for path in owned.values())
        assert all(path.stat().st_ino == inode for path, inode in locks.items())
        assert all(managed_deletion.removed(restored.db, identity) for identity in owned)
    finally:
        await restored.close()


async def test_old_preview_requires_review_instead_of_reusing_old_lock_scope(app):
    row = await create(app); value = await preview(app, row['id'])
    saved = json.loads(app.db.execute('SELECT value FROM managed_deletions WHERE id=?', (row['id'],)).fetchone()[0])
    saved.pop('sharedIds'); saved.pop('metadataLocks')
    app.db.execute('UPDATE managed_deletions SET value=? WHERE id=?', (json.dumps(saved), row['id'])); app.db.commit()
    with pytest.raises(AppError, match='changed since'):
        await delete(app, value)
    assert Path(row['workspace']).exists()


async def test_preserves_shared_attachments_and_artifact_bodies_revokes_share(app):
    from amplifier_web.attachments import save
    from amplifier_web.resource_files import put
    row=await create(app);other=await create(app)
    unique=save(app.data_dir,'unique.txt','dW5pcXVl');shared=save(app.data_dir,'shared.txt','c2hhcmVk')
    row['messages']=[{'id':'m','role':'user','text':'uploaded','attachments':[unique,shared]}]
    other['messages']=[{'id':'m2','role':'user','text':'independent copy','attachments':[shared]}]
    body=put(app.db,{'html':'shared artifact'});private=put(app.db,{'text':'private artifact'})
    app.state['canvasArtifacts']=[{'id':'a','sessionId':row['id'],'body':body},{'id':'b','sessionId':other['id'],'body':body},{'id':'c','sessionId':row['id'],'body':private}]
    app.db.execute('INSERT INTO conversation_shares VALUES (?,?,?,?,?)',('share','token',row['id'],0,json.dumps({'sessionId':row['id'],'content':private})))
    app._save()
    value=await preview(app,row['id']);assert value['summary']['attachmentCount']==1 and value['summary']['shareCount']==1
    await delete(app,value)
    assert not (app.data_dir/'attachments'/unique['id']).exists()
    assert (app.data_dir/'attachments'/shared['id']/'content').exists()
    assert (app.data_dir/'artifacts'/(body['$resource']+'.json')).exists()
    assert not (app.data_dir/'artifacts'/(private['$resource']+'.json')).exists()
    assert not app.db.execute('SELECT id FROM conversation_shares').fetchall()
    assert app._session(other['id'])['messages']


async def test_confirmed_cleanup_recovers_before_view_hydration(app,tmp_path,monkeypatch):
    row=await create(app,'recover');sid=row['id'];files=Path(row['workspace'])
    value=await preview(app,sid)
    original=managed_deletion._purge
    monkeypatch.setattr(managed_deletion,'_purge',lambda *a: (_ for _ in ()).throw(OSError('simulated disk interruption')))
    result=await delete(app,value);assert result['result']['cleanupPending']
    assert not app.state['sessions'] and not files.exists()
    assert list((app.data_dir/'chats').glob('.unified-delete-*'))
    await app.close();monkeypatch.setattr(managed_deletion,'_purge',original)
    restored=AppService(app.data_dir,Runtime(),workspace=tmp_path)
    try:
        assert not files.parent.exists() and not restored.state['sessions']
        assert restored.db.execute('SELECT phase FROM managed_deletions WHERE id=?',(sid,)).fetchone()[0]=='done'
    finally:await restored.close()


@pytest.mark.parametrize('deleted_role', ['owner', 'member', 'task-reference'])
@pytest.mark.parametrize('restart', [False, True])
async def test_deleted_assignment_header_closes_current_task_children_only(app, tmp_path, deleted_role, restart):
    from test_assignment_tasks import create as create_task, change as change_task
    owner, member, other = [await create(app, identity) for identity in ('task-owner', 'task-member', 'other-owner')]
    value = app.assignments.ensure(owner, 'owned-assignment')
    value['members'][member['id']] = {'status': 'bound', 'role': 'helper'}
    member['assignmentId'] = value['id']
    app.assignments.save(value)
    task = await create_task(app, owner, identity='retained-item')
    dependent = await create_task(app, owner, dependencies=[task['id']], identity='dependent-item')
    victim = owner if deleted_role == 'owner' else member
    if deleted_role == 'task-reference':
        victim = await create(app, 'independent-reference')
        claimed = (await change_task(app, owner, 'claim', task))['result']['task']
        await change_task(app, owner, 'complete', claimed, report='Related evidence', references=[victim['id']])
        assert not managed_deletion._mentions(app.assignments.current(owner), {victim['id']})
    app.assignments.ensure(other, 'other-assignment')
    unrelated = await create_task(app, other, identity='unrelated-item')
    unrelated_key = app.assignments.tasks.key(unrelated['id'])
    unrelated_before = app.db.execute('SELECT receipt FROM commands WHERE id=?', (unrelated_key,)).fetchone()[0]
    # An old command containing only assignment identity did not mention the
    # deleted chat before normalization. Do not broaden historical deletion.
    historical = {'commandAction': 'historical.observation', 'assignmentId': value['id'], 'result': 'Kept'}
    app.collaboration.insert('unrelated-history', 'historical', historical)
    app._save()
    observed = await preview(app, victim['id'])
    if restart:
        # Crash point after confirmation is durable, before any DB scrub. The
        # real startup recovery runs before Assignments is assembled.
        app.db.execute("UPDATE managed_deletions SET phase='confirmed' WHERE id=?", (victim['id'],))
        app.db.commit()
        await app.close()
        active = AppService(app.data_dir, Runtime(), workspace=tmp_path)
    else:
        await delete(app, observed)
        active = app
    try:
        assert active.collaboration.receipt(value['id'])['deleted']
        assert active.collaboration.receipt(active.assignments.tasks.key(task['id']))['deleted']
        assert active.collaboration.receipt(active.assignments.tasks.key(dependent['id']))['deleted']
        assert active.db.execute('SELECT receipt FROM commands WHERE id=?', (unrelated_key,)).fetchone()[0] == unrelated_before
        assert active.collaboration.receipt('unrelated-history') == historical
    finally:
        if active is not app:
            await active.close()


async def test_independent_recovery_copy_can_delete_without_deleting_original(app):
    original = await create(app)
    result = await command(app, 'web', 'session.recover', {'id': original['id']})
    copied = app._session(result['result']['sessionId'])
    assert copied.get('parentId') == original['id']
    await delete(app, await preview(app, copied['id']))
    assert Path(original['workspace']).is_dir() and app._session(original['id'])
    await delete(app, await preview(app, original['id']))


async def test_shared_checkpoint_is_deleted_but_other_host_lock_refuses(app):
    from amplifier_foundation.session.shared_state import SharedSessionStore
    row = await create(app)
    store = SharedSessionStore(row['workspace'], row['id'])
    held = store.acquire(app='test-other-host')
    held.write([{'role':'user','content':'private saved history'}], bundle='work')
    value = await preview(app, row['id'])
    try:
        with pytest.raises(AppError, match='Another application owns'):
            await delete(app, value)
        assert store.checkpoint_path.is_file()
    finally:
        held.release()
    await delete(app, await preview(app, row['id']))
    assert not store.checkpoint_path.exists()


async def test_new_shared_reference_during_retirement_requires_new_preview(app, monkeypatch):
    from amplifier_web.resource_files import put
    row = await create(app); other = await create(app)
    body = put(app.db, {'html':'must survive the new reference'})
    app.state['canvasArtifacts'] = [{'id':'owned','sessionId':row['id'],'body':body}]
    app._save()
    value = await preview(app,row['id'])
    original_stop = app.runtime.stop
    async def stop(sid):
        await original_stop(sid)
        if sid == row['id']:
            app.state['canvasArtifacts'].append({'id':'copy','sessionId':other['id'],'body':body})
            app._publish()
    monkeypatch.setattr(app.runtime,'stop',stop)
    with pytest.raises(AppError,match='changed'):
        await delete(app,value)
    assert (app.data_dir/'artifacts'/(body['$resource']+'.json')).is_file()
    assert app._session(row['id']) and app._session(other['id'])


async def test_new_identical_resource_after_staging_is_preserved(app, monkeypatch):
    from amplifier_web.resource_files import put
    row = await create(app);other = await create(app)
    content = {'text':'identical new resource'}
    body = put(app.db,content)
    app.state['canvasArtifacts'] = [{'id':'owned','sessionId':row['id'],'body':body}]
    app._save();value = await preview(app,row['id'])
    original_stage = managed_deletion._stage
    def stage(plan, **kwargs):
        original_stage(plan, **kwargs)
        if kwargs.get('db') is not app.db:
            return
        app.db.rollback()  # Release the staging-only lock before a new publication.
        # Simulate another publication after the atomic staging boundary.
        if not any(r.get('id')=='new' for r in app.state['canvasArtifacts']):
            fresh = put(app.db,content)
            app.state['canvasArtifacts'].append({'id':'new','sessionId':other['id'],'body':fresh})
            app._save()
    monkeypatch.setattr(managed_deletion,'_stage',stage)
    result = await delete(app,value)
    assert not result['result']['cleanupPending']
    assert (app.data_dir/'artifacts'/(body['$resource']+'.json')).is_file()
    assert app.db.execute('SELECT id FROM state_resources WHERE id=?',(body['$resource'],)).fetchone()
