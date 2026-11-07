"""Physical task storage owned by the existing workspace-resource ledger.

This owner allocates local bytes only. It never changes tool permissions,
launches a browser/database server, deletes contents or replays uncertain work.
"""
from __future__ import annotations

import asyncio
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import time
import uuid

from filelock import FileLock, Timeout as LockTimeout

KINDS = ('scratch-directory', 'browser-profile', 'sqlite-database')
IGNORE_CONTENT = b'# Local task storage is retained separately from source.\n*\n'


def definitions(schema, string):
    scope = {'workspaceId': string(100), 'sessionId': string(200)}
    return {
        'workspace.resources.provision': ('Allocate one task-owned scratch directory, browser-profile directory or local SQLite file inside this chat\'s current working folder, excluded from ordinary Git snapshots. Uses existing file access without another approval; explicit restrictions still apply. Does not launch processes or change permissions. Stable retry returns the original allocation, including older allocations outside the workspace.', schema({**scope,
            'kind': {'enum': list(KINDS)}, 'label': string(120)}, [*scope, 'kind'])),
        'workspace.resources.read': ('Inspect this task’s bounded owned storage without starting workers or recreating missing files. Current reference file-tool access is an observation, not a new grant.', schema({**scope,
            'id': string(100), 'offset': {'type': 'integer', 'minimum': 0}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 32}}, [*scope])),
        'workspace.resources.retire': ('Retire a task-owned allocation at its exact revision, retaining every file. Does not delete data, close an external browser/database, or claim its effects finished.', schema({**scope,
            'id': string(100), 'expectedRevision': {'type': 'integer', 'minimum': 1}}, [*scope, 'id', 'expectedRevision'])),
    }


def connect(home):
    directory = home / 'workspace-resources'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink():
        raise ValueError('The resource owner directory was replaced.')
    target = directory / 'reservations.sqlite3'
    if target.is_symlink():
        raise ValueError('The resource owner ledger was replaced.')
    db = sqlite3.connect(target, timeout=5)
    db.executescript('''
        CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS allocations(id TEXT PRIMARY KEY, workspace TEXT NOT NULL,
            root TEXT NOT NULL, status TEXT NOT NULL, body TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS allocations_root ON allocations(root,status);
        CREATE INDEX IF NOT EXISTS allocations_page ON allocations(root);
        CREATE INDEX IF NOT EXISTS allocations_status ON allocations(status);
    ''')
    target.chmod(0o600)
    return db


def identity(info):
    return [info.st_dev, info.st_ino]


def save(db, row):
    db.execute('INSERT OR REPLACE INTO allocations VALUES(?,?,?,?,?)',
               (row['id'], row['workspaceId'], row['sessionId'], row['status'], json.dumps(row)))


def get(db, sid, rid):
    found = db.execute('SELECT body FROM allocations WHERE id=? AND root=?', (rid, sid)).fetchone()
    if not found:
        raise ValueError('Choose an allocation owned by this conversation.')
    return json.loads(found[0])


def directory(home, row):
    if row.get('storageRoot'):
        from .task_storage_placement import root
        try:
            base = root(home, row)
        except (OSError, ValueError):
            # Retain the original path for inspection when a move is unknown;
            # open_parent still refuses effects through an unverified binding.
            base = Path(row['storageRoot'])
        return base / '.amplifier' / 'task-storage' / hashlib.sha256(row['sessionId'].encode()).hexdigest() / row['id']
    return home / 'workspace-resources' / 'owned' / hashlib.sha256(row['sessionId'].encode()).hexdigest() / row['id']


def public(home, row):
    root = directory(home, row)
    return {**{key: value for key, value in row.items() if key not in {'directoryIdentity', 'fileIdentity', 'storageRootIdentity', 'storageCheckout'}},
            'directory': str(root), 'path': str(root / 'database.sqlite3' if row['kind'] == 'sqlite-database' else root),
            'placement': 'working-folder' if row.get('storageRoot') else 'app-storage',
            'retention': 'preserved-until-explicit-cleanup', 'fileAccessChanged': False}


def _open_directory(parent, name, *, create=False):
    if create:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent)
        except FileExistsError:
            pass
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)


def _open_root(path):
    """Reject alias substitution anywhere in a recorded canonical path."""
    path = Path(path)
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            child = _open_directory(fd, part)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _ensure_ignore(parent, *, create=True, expected=None):
    """Own only our ignore file; never modify the repository's ignore rules."""
    try:
        if not create:
            raise FileExistsError
        fd = os.open('.gitignore', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    except FileExistsError:
        fd = os.open('.gitignore', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or expected is not None and identity(info) != expected
                    or stream.read(1024) != IGNORE_CONTENT):
                raise ValueError('The task-storage ignore file changed. Existing files were preserved.')
    else:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(IGNORE_CONTENT)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(parent)


def open_parent(home, row, *, create=False, admission=None):
    """Pin every managed path component; a symlink cannot redirect an effect."""
    if row.get('storageRoot'):
        from .task_storage_placement import root
        current = root(home, row)
        fd = _open_root(current)
        if identity(os.fstat(fd)) != row['storageRootIdentity']:
            os.close(fd)
            raise ValueError('The task storage working folder was replaced.')
        parts = ('.amplifier', 'task-storage', hashlib.sha256(row['sessionId'].encode()).hexdigest())
    else:
        current = home
        fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        parts = ('workspace-resources', 'owned', hashlib.sha256(row['sessionId'].encode()).hexdigest())
    try:
        for part in parts:
            current = current / part
            writable = admission is None or str(current) in admission['writable']
            expected = admission['existing'].get(str(current)) if admission else None
            if admission is not None and not writable and expected is None:
                raise ValueError('Task storage placement changed after access inspection.')
            child = _open_directory(fd, part, create=create and writable)
            os.close(fd)
            fd = child
            if expected is not None and identity(os.fstat(fd)) != expected:
                raise ValueError('An existing task storage parent changed after access inspection.')
            if row.get('storageRoot') and part == 'task-storage' and create:
                lock = home / 'workspace-resources' / ('ignore-' + hashlib.sha256(row['storageRoot'].encode()).hexdigest() + '.lock')
                with FileLock(str(lock), timeout=5):
                    ignore = str(current / '.gitignore')
                    _ensure_ignore(fd, create=admission is None or ignore in admission['writable'],
                                   expected=admission['existing'].get(ignore) if admission else None)
        return fd
    except BaseException:
        os.close(fd)
        raise


def observe(home, row):
    """Read identity only: no content scan, repair, creation or process probe."""
    if os.name == 'nt' and row.get('testMachine'):
        from .test_origin_storage import observe
        return observe(home, row)
    if not row.get('directoryIdentity'):
        return {**row, 'observation': 'unverified'}
    try:
        parent = open_parent(home, row)
        try:
            fd = _open_directory(parent, row['id'])
        finally:
            os.close(parent)
        try:
            if identity(os.fstat(fd)) != row['directoryIdentity']:
                raise ValueError('Changed directory identity')
            if row['kind'] == 'sqlite-database':
                info = os.stat('database.sqlite3', dir_fd=fd, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or identity(info) != row.get('fileIdentity'):
                    raise ValueError('Changed database identity')
            return {**row, 'observation': 'present'}
        finally:
            os.close(fd)
    except FileNotFoundError:
        return {**row, 'observation': 'missing'}
    except (OSError, ValueError):
        return {**row, 'observation': 'changed-or-unavailable'}


def prepare(home, workspace, sid, actor, args, command_id, execution_root=None):
    if not command_id or len(command_id) > 200:
        raise ValueError('Supply a stable command ID for resource allocation.')
    signature = hashlib.sha256(json.dumps(['task-resource', workspace, sid, actor, args], sort_keys=True).encode()).hexdigest()
    with closing(connect(home)) as db:
        prior = db.execute('SELECT fingerprint,result FROM commands WHERE id=?', (command_id,)).fetchone()
        if prior:
            if prior[0] != signature:
                raise ValueError('This resource command already belongs to different arguments or an owner.')
            return get(db, sid, json.loads(prior[1])['allocationId']), False
    placement = {}
    if execution_root is not None:
        from .task_storage_placement import capture
        # Git/filesystem verification must not hold the shared ledger writer.
        placement = capture(home, execution_root)
    with closing(connect(home)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        prior = db.execute('SELECT fingerprint,result FROM commands WHERE id=?', (command_id,)).fetchone()
        if prior:
            if prior[0] != signature:
                raise ValueError('This resource command already belongs to different arguments or an owner.')
            return get(db, sid, json.loads(prior[1])['allocationId']), False
        total, owned = db.execute("SELECT count(*),coalesce(sum(root=?),0) FROM allocations WHERE status!='retired'", (sid,)).fetchone()
        from .test_execution_settings import concurrency
        limits = concurrency(home)
        if (limits['maxOwnedResources'] and total >= limits['maxOwnedResources']) or (limits['maxOwnedResourcesPerChat'] and owned >= limits['maxOwnedResourcesPerChat']):
            raise ValueError('Task resource capacity is full. Inspect and retire unused allocations or adjust Advanced execution limits in Settings.')
        row = dict(id=str(uuid.uuid4()), workspaceId=workspace, sessionId=sid, owner=actor,
                   kind=args['kind'], label=args.get('label', '').strip(), status='prepared', revision=1,
                   createdAt=time.time(), commandId=command_id)
        row.update(placement)
        save(db, row)
        db.execute('INSERT INTO commands VALUES(?,?,?)', (command_id, signature, json.dumps({'allocationId': row['id']})))
        return row, True


def suppress(home, row, reason):
    with closing(connect(home)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        row = get(db, row['sessionId'], row['id'])
        if row['status'] == 'prepared':
            row.update(status='suppressed', reason=reason, revision=row['revision']+1)
            save(db, row)
        return row


def allocate(home, initial, admit=None):
    """One admitted local effect; any retry only observes its durable state."""
    if os.name == 'nt' and initial.get('testMachine'):
        from .test_origin_storage import allocate
        return allocate(home, initial, admit)
    lock = home / 'workspace-resources' / ('allocation-' + initial['id'] + '.lock')
    with FileLock(str(lock), timeout=5), closing(connect(home)) as db:
        admission = None
        if admit:
            admission = admit()  # Main-loop authority check after any owner-lock wait.
        with db:
            db.execute('BEGIN IMMEDIATE')
            row = get(db, initial['sessionId'], initial['id'])
            if row['status'] != 'prepared':
                return row
            row.update(status='allocating', revision=row['revision']+1)
            save(db, row)
        parent = fd = None
        try:
            parent = open_parent(home, row, create=True, admission=admission)
            # Never reuse a directory that predates this effect.
            os.mkdir(row['id'], mode=0o700, dir_fd=parent)
            fd = _open_directory(parent, row['id'])
            row['directoryIdentity'] = identity(os.fstat(fd))
            with db:
                save(db, row)
            if row['kind'] == 'sqlite-database':
                # Create valid database bytes in memory, then write only through
                # the pinned exclusive destination. SQLite never follows a path
                # supplied by a peer or a newly substituted filesystem symlink.
                with closing(sqlite3.connect(':memory:')) as memory:
                    memory.execute('PRAGMA user_version=0')
                    content = memory.serialize()
                out = os.open('database.sqlite3', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                              0o600, dir_fd=fd)
                with os.fdopen(out, 'wb') as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                    row['fileIdentity'] = identity(os.fstat(stream.fileno()))
            os.fsync(fd)
            os.fsync(parent)
            if observe(home, row)['observation'] != 'present':
                raise ValueError('The resource path changed during allocation.')
            row.update(status='ready', revision=row['revision']+1)
        except BaseException:
            row.update(status='unknown', revision=row['revision']+1,
                       reason='Allocation did not finish with verified ownership. Preserve and inspect the resource; it was not replayed.')
            with db:
                save(db, row)
            raise
        finally:
            if fd is not None:
                os.close(fd)
            if parent is not None:
                os.close(parent)
        with db:
            save(db, row)
        return row


def read(home, sid, args):
    with closing(connect(home)) as db:
        limit, offset = args.get('limit', 25), args.get('offset', 0)
        rows = ([get(db, sid, args['id'])] if args.get('id') else [json.loads(value[0]) for value in db.execute(
            "SELECT body FROM allocations WHERE root=? AND json_extract(body,'$.kind')!='test-environment' ORDER BY rowid DESC LIMIT ? OFFSET ?", (sid, limit+1, offset))])
        if any(row['kind'] not in KINDS for row in rows):
            raise ValueError('Use the resource’s dedicated controls to inspect its owned state.')
        return {'items': [public(home, observe(home, recover(home, db, row))) for row in rows[:limit]],
                'nextOffset': offset + limit if len(rows) > limit else None}


def recover(home, db, row):
    """A free physical owner lock distinguishes interruption from live work."""
    if row['status'] != 'allocating':
        return row
    try:
        with FileLock(str(home / 'workspace-resources' / ('allocation-' + row['id'] + '.lock')), timeout=0):
            with db:
                db.execute('BEGIN IMMEDIATE')
                row = get(db, row['sessionId'], row['id'])
                if row['status'] == 'allocating':
                    row.update(status='unknown', revision=row['revision']+1,
                               reason='The allocation owner stopped before recording completion. All contents are retained; no effect was replayed.')
                    save(db, row)
                return row
    except LockTimeout:
        return row


def retire(home, sid, actor, args, command_id, admit=None):
    if not command_id:
        raise ValueError('Supply a stable command ID for retirement.')
    uuid.UUID(args['id'])
    signature = hashlib.sha256(json.dumps(['retire-task-resource', sid, actor, args], sort_keys=True).encode()).hexdigest()
    lock = home / 'workspace-resources' / ('allocation-' + args['id'] + '.lock')
    # A live physical effect owns this lock. Retirement never races its final
    # identity write or changes its state while an external effect is pending.
    with FileLock(str(lock), timeout=0), closing(connect(home)) as db, db:
        if admit:
            admit()
        db.execute('BEGIN IMMEDIATE')
        prior = db.execute('SELECT fingerprint,result FROM commands WHERE id=?', (command_id,)).fetchone()
        if prior:
            if prior[0] != signature:
                raise ValueError('This resource command already belongs to a different retirement.')
            return json.loads(prior[1])
        row = get(db, sid, args['id'])
        if row['kind'] not in KINDS:
            raise ValueError('Use the resource’s dedicated controls; storage retirement cannot retire a live environment.')
        if row['revision'] != args['expectedRevision']:
            raise ValueError('The allocation changed. Inspect its current revision.')
        # A free owner lock proves no live allocation thread here. Contents,
        # including an interrupted effect's uncertain bytes, are all retained.
        row.update(status='retired', revision=row['revision']+1, retiredAt=time.time())
        save(db, row)
        result = public(home, row)
        db.execute('INSERT INTO commands VALUES(?,?,?)', (command_id, signature, json.dumps(result)))
        return result


async def access(app, sid, rows):
    method = getattr(app.runtime, 'resource_access', None)
    paths = [row['path'] for row in rows]
    unknown = {'status': 'unknown', 'reason': 'No ready supported file-policy observation. Allocation does not grant file access.'}
    if not method or not paths:
        return [{**row, 'access': unknown} for row in rows]
    try:
        async with asyncio.timeout(12):
            report = await method(sid, paths)
        observed = report.get('paths', {}) if isinstance(report, dict) and report.get('status') == 'observed' else {}
        return [{**row, 'access': observed.get(row['path'], unknown)} for row in rows]
    except (RuntimeError, ValueError, TimeoutError):
        return [{**row, 'access': unknown} for row in rows]


async def require_allocation_access(app, sid, row):
    """An agent cannot use the allocator to bypass its mounted write policy."""
    root = directory(app.data_dir, row)
    managed = root.parent.parent
    existing, paths = {}, [str(root)]
    for path in (managed.parent, managed, managed / '.gitignore', root.parent):
        try:
            info = path.lstat()
        except FileNotFoundError:
            paths.append(str(path))
        else:
            if not (stat.S_ISREG(info.st_mode) if path.name == '.gitignore' else stat.S_ISDIR(info.st_mode)):
                raise ValueError('The task storage parent is not an ordinary folder or ignore file.')
            existing[str(path)] = identity(info)
    if row['kind'] == 'sqlite-database':
        paths.append(str(root / 'database.sqlite3'))
    reports = await access(app, sid, [{'path': path} for path in paths])
    if any(item['access']['status'] != 'allowed' for item in reports):
        raise ValueError('Task storage is inside this chat’s working folder, but its current file tools '
                         'do not confirm write access there. Inspect the current file restrictions or '
                         'finish pending configuration refresh before allocating. No workspace files were created.')
    return {'writable': set(paths), 'existing': existing}


async def dispatch(app, action, args, origin, command_id, caller):
    from .errors import AppError
    from .collaboration import PRINCIPAL
    user = origin in {'ui', 'user'}
    sid = args['sessionId']
    if not user and sid != caller:
        raise AppError('Task resources belong to the calling conversation.', 403)
    source = app._session(sid, hydrate=False)
    native = source.get('runtimeSessionId') or source.get('nativeIdentity') or sid
    if source.get('sessionKind', 'root') != 'root' or not user and PRINCIPAL.get() != native:
        raise AppError('Task storage requires its host-bound root owner.', 403)
    workspace = next((w for w in app.state['workspaces'] if w['id'] == args['workspaceId']), None)
    if workspace is None or source['workspace'] != workspace['path']:
        raise AppError('Choose this conversation’s current workspace.', 403)
    actor = {'sessionId': sid, 'principal': (source.get('runtimeSessionId') or source.get('nativeIdentity') or sid) if user else PRINCIPAL.get()}
    captured = tuple(source.get(key) for key in ('workspace', 'workingDirectory', 'executionRevision', 'interruptionRevision'))
    def guard():
        current = app._session(sid, hydrate=False)
        if captured != tuple(current.get(key) for key in ('workspace', 'workingDirectory', 'executionRevision', 'interruptionRevision')):
            raise AppError('The task moved or stopped before resource admission.', 409)
        if not user:
            app.collaboration.active_binding(current)
            if reason := app.assignments.blocked(current):
                raise AppError(reason, 409)
    loop = asyncio.get_running_loop()
    async def locked_guard():
        async with app.lock:
            guard()
        if action == 'workspace.resources.provision' and not user:
            admission = await require_allocation_access(app, sid, allocation_row)
            async with app.lock:
                guard()
            return admission
    def admit():
        pending = asyncio.run_coroutine_threadsafe(locked_guard(), loop)
        try:
            return pending.result(timeout=15)
        except TimeoutError:
            pending.cancel()
            raise ValueError('Task storage access inspection timed out. No workspace files were created.') from None
    try:
        if action == 'workspace.resources.read':
            page = await asyncio.to_thread(read, app.data_dir, sid, args)
        else:
            guard()
            if action == 'workspace.resources.provision':
                row, fresh = await asyncio.to_thread(prepare, app.data_dir, workspace['id'], sid, actor, args, command_id,
                                                    source.get('workingDirectory') or source['workspace'])
                allocation_row = row
                if fresh:
                    try:
                        guard()
                    except BaseException:
                        await asyncio.to_thread(suppress, app.data_dir, row, 'Task authority changed before resource admission.')
                        raise
                    # Cancellation cannot stop a filesystem thread. Its durable
                    # owner finishes the admitted effect before releasing control.
                    try:
                        row = await app.worktrees.finish_thread(allocate, app.data_dir, row, admit)
                    except BaseException:
                        await asyncio.to_thread(suppress, app.data_dir, row, 'Allocation admission or completion was interrupted; inspect the retained resource.')
                        raise
                page = await asyncio.to_thread(read, app.data_dir, sid, {'id': row['id']})
            else:
                result = await app.worktrees.finish_thread(retire, app.data_dir, sid, actor, args, command_id, admit)
                page = {'items': [result], 'nextOffset': None}
        page['items'] = await access(app, sid, page['items'])
        if captured[0] != app._session(sid, hydrate=False).get('workspace'):
            raise AppError('The task moved. Inspect resources from its current workspace.', 409)
        return {'accepted': True, 'result': page}
    except (ValueError, OSError, sqlite3.Error, LockTimeout) as exc:
        raise AppError(str(exc), 409) from None
