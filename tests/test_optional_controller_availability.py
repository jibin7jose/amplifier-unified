"""Real AppService/SQLite with unsupported optional controllers, no listeners."""
import copy
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import socket
import sys
import uuid
from types import SimpleNamespace

import pytest

from amplifier_web import portability, publishing
from amplifier_web.service import AppError, AppService
from test_service import Runtime


@pytest.fixture(autouse=True)
def no_listener_or_network(monkeypatch):
    pair_depth = 0
    original_pair = socket.socketpair

    def pair(*args, **kwargs):
        nonlocal pair_depth
        pair_depth += 1
        try:
            # Windows implements asyncio's self-pipe with a loopback socketpair.
            # Only that synchronous standard-library constructor is exempt.
            return original_pair(*args, **kwargs)
        finally:
            pair_depth -= 1

    def deny(*args, **kwargs):
        raise AssertionError('Optional-controller tests cannot open listeners or contact hosts')

    def guarded(original):
        def call(*args, **kwargs):
            if pair_depth:
                return original(*args, **kwargs)
            return deny()
        return call

    monkeypatch.setattr(socket, 'socketpair', pair)
    for method in ('bind', 'listen', 'connect', 'connect_ex'):
        monkeypatch.setattr(socket.socket, method, guarded(getattr(socket.socket, method)))
    monkeypatch.setattr(socket, 'getaddrinfo', deny)


def unavailable(monkeypatch):
    monkeypatch.setattr(publishing, 'controller_available', lambda: False)
    monkeypatch.setattr(portability, 'controller_available', lambda: False)


def forbidden(*args, **kwargs):
    raise AssertionError('Unsupported controller must not construct a writing store')


async def roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(publishing, 'Publisher', forbidden)
    monkeypatch.setattr(portability, 'TransferNode', forbidden)
    runtime = Runtime()
    root, workspace = tmp_path / 'app', tmp_path / 'workspace'
    workspace.mkdir()
    app = AppService(root, runtime, workspace=workspace)
    try:
        await app.dispatch('session.create', {'title': 'Synthetic Windows trial'})
        sid = app._session()['id']
        assert not app.publishing.available and not app.portability.available
        assert app.state['portability']['availability']['available'] is False
        assert not (root / 'publishing').exists()
        assert not (root / 'portability').exists()
        assert not app.db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'publishing_%'").fetchall()
        assert app.portability.write_context(sid) is None
        result = await app.dispatch('conversation.send', {'sessionId': sid, 'text': 'Synthetic café'}, command_id='trial-send')
        assert result['accepted'] and runtime.sent
        texts = [row['text'] for row in app._session(sid)['messages']]
        assert 'Synthetic café' in texts and 'Test transport result' in texts
        state = app.browser_state()
        assert state['portability']['availability']['available'] is False
        assert not app.portability.jobs
    finally:
        await app.close()
    restored = AppService(root, Runtime(), workspace=workspace)
    try:
        session = restored._session(sid)
        assert [row['text'] for row in session['messages']] == texts
        await restored.dispatch('session.rename', {'id': sid, 'title': 'Saved synthetic trial'})
        assert restored._session(sid)['title'] == 'Saved synthetic trial'
        assert not (root / 'publishing').exists() and not (root / 'portability').exists()
    finally:
        await restored.close()


async def test_full_app_chat_save_reload_with_unavailable_controllers(tmp_path, monkeypatch):
    unavailable(monkeypatch)
    await roundtrip(tmp_path, monkeypatch)


@pytest.mark.skipif(sys.platform != 'win32', reason='native Windows full AppService boundary')
async def test_native_default_selection_full_app_chat_save_reload(tmp_path, monkeypatch):
    # No platform/controller selection double: exercise the real Windows gate.
    assert not publishing.controller_available() and not portability.controller_available()
    await roundtrip(tmp_path, monkeypatch)


@pytest.fixture
async def app(tmp_path, monkeypatch):
    unavailable(monkeypatch)
    service = AppService(tmp_path / 'app', Runtime(), workspace=tmp_path)
    await service.dispatch('session.create', {})
    yield service
    await service.close()


async def test_unavailable_actions_have_no_admission_or_recovery(app):
    sid = app._session()['id']
    for action in ('publishing.build', 'publishing.preview', 'publishing.deploy', 'publishing.stop',
                   'publishing.target.save', 'publishing.target.inspect', 'publishing.target.select',
                   'publishing.target.remove'):
        result = await app.publishing.dispatch(action, {'sessionId': sid}, 'ui', 'synthetic-command')
        assert result['accepted'] is False and result['effect'] == 'none'
        assert result['code'] == 'controller_unavailable'
    for action in ('portability.export', 'portability.stage', 'portability.release',
                   'portability.activate', 'portability.cancel', 'portability.discard', 'portability.evidence'):
        result = await app.portability.dispatch(action, {}, 'ui', 'synthetic-command')
        assert result['accepted'] is False and result['effect'] == 'none'
    listed = (await app.dispatch('publishing.list', {'sessionId': sid}))['result']
    assert listed['sites'] == [] and listed['releases'] == [] and listed['receipts'] == []
    assert listed['availability']['available'] is False and listed['liveness'] == 'unconfirmed'
    inspected = (await app.dispatch('portability.inspect', {'sessionId': sid}))['result']
    assert inspected['host'] is None and inspected['peers'] is None and inspected['receipts'] == []
    with pytest.raises(publishing.PublishingError, match='unavailable'):
        app.publishing.capture_store('synthetic-service')
    assert not (app.data_dir / 'publishing').exists() and not (app.data_dir / 'portability').exists()


def transfer(app, phase='prepared', generation=1):
    directory = app.data_dir / 'portability' / 'receipts'
    row = {'id': str(uuid.uuid4()), 'sessionId': app._session()['id'], 'phase': phase,
           'createdAt': generation, 'generation': generation, 'revision': 1, 'direction': 'outgoing'}
    path = directory / (row['id'] + '.json')
    if sys.platform == 'win32':
        from amplifier_web.windows_files import directory as private_directory
        with private_directory(directory, owned_root=directory.parent, create=True) as pinned:
            pinned.publish_private(path.name, json.dumps(row).encode('utf-8'))
    else:
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(row), encoding='utf-8')
    return row, path


@pytest.mark.parametrize('phase', ['preparing', 'prepared', 'released', 'ready', 'activating', 'unknown', 'discarded'])
async def test_retained_transfer_fences_normal_mutation_without_controller(app, phase):
    row, path = transfer(app, phase)
    original = path.read_bytes()
    with pytest.raises(AppError, match='fenced'):
        await app.dispatch('session.rename', {'id': row['sessionId'], 'title': 'Must not change'})
    with pytest.raises(AppError, match='fenced'):
        await app.dispatch('conversation.send', {'sessionId': row['sessionId'], 'text': 'Must not start'})
    assert not app.runtime.sent and path.read_bytes() == original
    inspected = (await app.dispatch('portability.inspect', {}))['result']
    assert inspected['receipts'] == [row]  # No recover() rewrite of preparing.


async def test_latest_transfer_and_native_marker_still_deny(app):
    row, _ = transfer(app, 'released')
    transfer(app, 'active', generation=2)
    assert app.portability.write_context(row['sessionId']) is not None
    session = app._session()
    session.update(nativeProject='synthetic-project', nativeIdentity='synthetic-native')
    from amplifier_web.automatic_history import directory
    marker = directory(session) / 'transfer-fence.json'
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_bytes(b'not valid JSON; presence alone denies')
    with pytest.raises(ValueError, match='fenced'):
        app.portability.write_context(row['sessionId'])
    assert marker.read_bytes() == b'not valid JSON; presence alone denies'


@pytest.mark.parametrize('change', [
    {'generation': 0}, {'generation': True}, {'createdAt': float('nan')},
    {'phase': 'invented'}, {'sessionId': ''}, {'id': 'not-the-filename'},
])
async def test_malformed_retained_ownership_cannot_grant_write(app, change):
    row, path = transfer(app)
    original = path.read_bytes()
    row.update(change)
    path.write_text(json.dumps(row), encoding='utf-8')
    try:
        with pytest.raises(ValueError):
            app.portability.write_context(app._session()['id'])
    finally:
        # Restore only this fault-injection fixture so normal teardown can save.
        path.write_bytes(original)


def publishing_store(app):
    root = app.data_dir / 'publishing'
    root.mkdir()
    path = root / 'state.sqlite3'
    db = sqlite3.connect(path)
    db.execute('CREATE TABLE records (kind TEXT, id TEXT, body TEXT)')
    db.execute('CREATE TABLE receipts (session TEXT, request TEXT, fingerprint TEXT, body TEXT)')
    return path, db


async def test_retained_sqlite_is_read_only_owned_and_not_live(app):
    sid = app._session()['id']
    path, db = publishing_store(app)
    site = {'id': 'example', 'sessionId': sid, 'status': 'running', 'url': 'http://127.0.0.1:9999'}
    receipt = {'id': 'receipt', 'sessionId': sid, 'requestId': 'saved-request', 'siteId': 'example', 'state': 'running'}
    db.execute('INSERT INTO records VALUES (?,?,?)', ('site', site['id'], json.dumps(site)))
    db.execute('INSERT INTO receipts VALUES (?,?,?,?)', (sid, receipt['requestId'], 'saved-digest', json.dumps(receipt)))
    db.commit()
    db.close()
    original = path.read_bytes()
    value = (await app.dispatch('publishing.list', {'sessionId': sid}))['result']
    assert value['sites'][0]['status'] == 'unconfirmed' and value['sites'][0]['url'] is None
    assert value['sites'][0]['retainedStatus'] == 'running'
    assert value['receipts'] == [receipt]
    assert app.publishing.owns_records(sid)
    assert app.publishing.store.resources(sid)['items'][0]['url'] is None
    assert not app.publishing.owns_records('other-task')
    assert path.read_bytes() == original
    assert not path.with_name(path.name + '-shm').exists()
    with pytest.raises(publishing.PublishingError, match='unavailable'):
        app.publishing.store.snapshot(Path('synthetic-backup'))


async def test_retained_sqlite_receipt_cannot_reassign_ownership(app):
    sid = app._session()['id']
    _, db = publishing_store(app)
    db.execute('INSERT INTO receipts VALUES (?,?,?,?)', (sid, 'saved', 'digest',
               json.dumps({'sessionId': 'other-task', 'requestId': 'saved', 'state': 'running'})))
    db.commit()
    db.close()
    with pytest.raises(publishing.PublishingError, match='ownership'):
        app.publishing.store.owns_records(sid)


@pytest.mark.parametrize('suffix', ['-wal', '-journal'])
async def test_uncheckpointed_retained_database_is_not_silently_omitted(app, suffix):
    path, db = publishing_store(app)
    db.close()
    path.with_name(path.name + suffix).write_bytes(b'synthetic retained journal')
    with pytest.raises(publishing.PublishingError, match='journal'):
        app.publishing.store.inspect(app._session()['id'])


async def test_remote_retained_admission_is_not_local_inventory_or_current_progress(app):
    from amplifier_web.publishing_targets import PublishingTargets
    sid = app._session()['id']
    # Prepare actual app SQL tables with the POSIX registry, then observe them
    # through the unavailable controller. No network client is constructed.
    writer = PublishingTargets(app, app.publishing.target)
    target = {'id': 'remote', 'kind': 'ssh', 'label': 'Synthetic remote', 'revision': 1,
              'removed': False, 'inspection': None}
    app.db.execute('INSERT INTO publishing_targets VALUES (?,?,?)', (sid, 'remote', json.dumps(target)))
    request = {'id': 'saved', 'sessionId': sid, 'requestId': 'saved', 'targetId': 'remote',
               'siteId': 'example', 'state': 'running'}
    app.db.execute('INSERT INTO publishing_target_requests VALUES (?,?,?,?)', (sid, 'saved', 'digest', json.dumps(request)))
    app.db.commit()
    original = copy.deepcopy(request)
    value = (await app.dispatch('publishing.list', {'sessionId': sid, 'targetId': 'remote'}))['result']
    assert value['sites'] is None and value['releases'] is None
    assert value['receipts'] == [original] and value['historicalOnly']
    assert value['coverage'] == 'retained-admissions-only' and value['liveness'] == 'unconfirmed'
    raw = app.db.execute('SELECT body FROM publishing_target_requests').fetchone()[0]
    assert json.loads(raw) == original


async def test_retained_app_sql_request_body_cannot_change_owner(app):
    from amplifier_web.publishing_targets import PublishingTargets
    sid = app._session()['id']
    PublishingTargets(app, app.publishing.target)
    app.db.execute('INSERT INTO publishing_target_requests VALUES (?,?,?,?)', (sid, 'saved', 'digest',
        json.dumps({'sessionId': 'other-task', 'requestId': 'saved', 'targetId': 'remote'})))
    app.db.commit()
    with pytest.raises(publishing.PublishingError, match='ownership'):
        app.publishing.targets.list(sid)


async def test_windows_retained_transfer_authority_requires_private_handle_read(app, monkeypatch):
    row, _ = transfer(app, 'cancelled')
    from amplifier_web import windows_files
    calls = []

    @contextmanager
    def private(directory, *, owned_root):
        calls.append((directory, owned_root))
        def read(name, *, limit):
            assert name == row['id'] + '.json' and limit == 1024 * 1024
            return json.dumps(row).encode()
        yield SimpleNamespace(read_private=read)

    with monkeypatch.context() as patch:
        # Select only this module's Windows read branch, not the host platform.
        patch.setattr(portability, 'sys', SimpleNamespace(platform='win32'))
        patch.setattr(windows_files, 'directory', private)
        assert app.portability.write_context(row['sessionId']) == row['id']
        assert calls and calls[0] == (app.data_dir / 'portability' / 'receipts', app.data_dir / 'portability')

        @contextmanager
        def denied(*args, **kwargs):
            raise PermissionError('Synthetic unsafe ACL')
            yield

        patch.setattr(windows_files, 'directory', denied)
        with pytest.raises(PermissionError, match='unsafe ACL'):
            app.portability.write_context(row['sessionId'])


async def test_retained_sql_record_identity_and_source_changes_are_refused(app):
    sid = app._session()['id']
    path, db = publishing_store(app)
    db.execute('INSERT INTO records VALUES (?,?,?)', ('site', 'database-id',
        json.dumps({'id': 'different-id', 'sessionId': sid, 'status': 'running'})))
    db.commit()
    db.close()
    with pytest.raises(publishing.PublishingError, match='ownership'):
        app.publishing.store.owns_records(sid)
    import os
    with pytest.raises(publishing.PublishingError, match='changed'):
        with app.publishing.store.connection() as connection:
            assert connection.execute('SELECT count(*) FROM records').fetchone()[0] == 1
            before = path.stat()
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))


async def test_retained_resource_page_does_not_load_manifests_or_receipts(app, monkeypatch):
    sid = app._session()['id']
    _, db = publishing_store(app)
    for identity in ('alpha', 'beta'):
        db.execute('INSERT INTO records VALUES (?,?,?)', ('site', identity,
            json.dumps({'id': identity, 'sessionId': sid, 'status': 'running'})))
    db.execute('INSERT INTO records VALUES (?,?,?)', ('release', 'large-manifest',
        json.dumps({'id': 'large-manifest', 'sessionId': sid, 'files': ['synthetic-file'] * 2000})))
    db.commit()
    db.close()
    monkeypatch.setattr(app.publishing.store, 'records', forbidden)
    first = app.publishing.store.resources(sid, limit=1)
    assert [row['id'] for row in first['items']] == ['alpha']
    assert first['nextCursor'] == ['site', 'alpha']
    second = app.publishing.store.resources(sid, limit=1, before=first['nextCursor'])
    assert [row['id'] for row in second['items']] == ['beta']
    assert second['nextCursor'] is None
    assert all(row['status'] == 'unconfirmed' and row['url'] is None for row in first['items'] + second['items'])


def test_posix_target_registry_still_saves_and_selects_without_connecting(tmp_path):
    # Real SQLite and registry, no private service/listener fixture.
    from amplifier_web.publishing_targets import PublishingTargets
    db = sqlite3.connect(tmp_path / 'app.sqlite3')
    app = SimpleNamespace(db=db, _session=lambda sid: {'id': sid})
    try:
        registry = PublishingTargets(app, lambda: {'id': 'loopback', 'accessPolicy': 'loopback-only'})
        args = {'targetId': 'private', 'expectedRevision': 0, 'label': 'Synthetic private target',
                'hostname': 'example.invalid', 'python': '/usr/bin/python3',
                'socketPath': '/synthetic/control.sock'}
        value = registry.save('task', args)
        assert len(value['targets']) == 2 and value['selectedTargetId'] == 'loopback'
        assert 'historicalOnly' not in value
        assert registry.list('other')['targets'] == [registry._local()]
        assert registry.owns_records('task') and not registry.owns_records('other')
        assert registry.select('task', {'targetId': 'loopback', 'expectedRevision': 0})['selectedTargetId'] == 'loopback'
    finally:
        db.close()