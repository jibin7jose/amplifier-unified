"""Workspace resource inventory, retained outside removable working folders."""
from __future__ import annotations
import json
import time
import uuid
import hashlib
import asyncio
import sqlite3
from contextlib import closing
from filelock import FileLock
from amplifier_worktrees.git import atomic


HOLDING = {'held', 'uncertain'}


def definitions(schema, string):
    from .task_resources import definitions as task_definitions
    scope = {'workspaceId': string(100)}
    change = {**scope, 'id': string(100), 'expectedRevision': {'type': 'integer', 'minimum': 1},
              'evidence': {**string(4000), 'minLength': 1}}
    return {
        **task_definitions(schema, string),
        'workspace.resources.managed': ('Read one task’s bounded publishing, recorded processes and owned task storage. Does not allocate, replay or stop anything. Agents inspect only their bound task. Storage is not a browser/process sandbox or dynamic preview discovery.', schema({**scope,
            'sessionId': string(200), 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100},
            'operationsBefore': string(200),
            'publishingBefore': {'type': 'array', 'items': string(200), 'minItems': 2, 'maxItems': 2}}, ['workspaceId', 'sessionId'])),
        'workspace.resources.reservations': ('List cooperative resource reservations without loading chats. Matching keys share one admission domain across this app data home; separate installations and aliases are not covered.', schema({**scope,
            'offset': {'type': 'integer', 'minimum': 0}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100},
            'includeReleased': {'type': 'boolean'}}, ['workspaceId'])),
        'workspace.resources.reserve': ('Reserve an exact resource key for this task before using it. Exclusive by default; shared requires every holder to choose shared. Does not provision or fence infrastructure. Agents cannot choose a different owner.', schema({**scope,
            'sessionId': string(200), 'resourceKey': {**string(500), 'minLength': 1},
            'mode': {'enum': ['exclusive', 'shared']}, 'note': string(1000)}, ['workspaceId', 'resourceKey'])),
        'workspace.resources.release': ('Relinquish an observed reservation with evidence; never performs teardown. Agents can release only their own principal\'s claim. Users may reconcile retained claims after checking the resource.', schema(change)),
        'workspace.resources.uncertain': ('Keep a reservation held while its physical outcome is uncertain. No expiry or automatic reassignment. Requires its exact revision and evidence.', schema(change)),
    }


def reservation_command(home, workspace_id, action, args, actor, command_id=None):
    """One admission lock across workspaces, separate from advisory inventories.

    Receipts are never evicted: replaying an old acquire after release must not
    acquire anything. This owner retains claims only, never external effects.
    """
    directory = home / 'workspace-resources'
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / 'reservations.sqlite3'
    # Index current claims independently of durable command receipts. A long
    # history of released resources must not inflate each admission/read.
    with closing(sqlite3.connect(target, timeout=5)) as db, db:
        target.chmod(0o600)
        db.executescript('''
            CREATE TABLE IF NOT EXISTS claims(id TEXT PRIMARY KEY, workspace TEXT NOT NULL,
                resource_key TEXT NOT NULL, root TEXT NOT NULL, status TEXT NOT NULL, body TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS claims_key ON claims(resource_key, status);
            CREATE INDEX IF NOT EXISTS claims_workspace ON claims(workspace, status);
            CREATE INDEX IF NOT EXISTS claims_holding ON claims(status, root);
            CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS metadata(id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL);
            INSERT OR IGNORE INTO metadata VALUES(1, 0);
        ''')
        db.execute('BEGIN' if action == 'workspace.resources.reservations' else 'BEGIN IMMEDIATE')
        if action == 'workspace.resources.reservations':
            offset, limit = args.get('offset', 0), args.get('limit', 100)
            condition = '' if args.get('includeReleased') else " AND status IN ('held','uncertain')"
            rows = [json.loads(row[0]) for row in db.execute(
                'SELECT body FROM claims WHERE workspace=?' + condition + ' ORDER BY rowid LIMIT ? OFFSET ?',
                (workspace_id, limit + 1, offset))]
            return {'revision': db.execute('SELECT revision FROM metadata').fetchone()[0], 'items': rows[:limit],
                    'nextOffset': offset + limit if len(rows) > limit else None,
                    'enforcement': 'cooperative-matching-keys', 'scope': 'app-data-home'}
        if not command_id:
            raise ValueError('A resource reservation needs a stable command ID.')
        signature = hashlib.sha256(json.dumps([workspace_id, action, args, actor], sort_keys=True).encode()).hexdigest()
        prior = db.execute('SELECT fingerprint,result FROM commands WHERE id=?', (command_id,)).fetchone()
        if prior:
            if prior[0] != signature:
                raise ValueError('This resource request already belongs to a different update or actor.')
            return json.loads(prior[1])
        if action == 'workspace.resources.reserve':
            key = args['resourceKey'].strip()
            if not key or len(key) > 500 or any(ord(c) < 32 or ord(c) == 127 for c in key):
                raise ValueError('Use a nonempty resource key without control characters.')
            mode = args.get('mode', 'exclusive')
            if mode not in {'exclusive', 'shared'} or not actor.get('sessionId') or not actor.get('principal'):
                raise ValueError('Choose a valid resource owner and sharing mode.')
            from .test_execution_settings import concurrency
            limits = concurrency(home)
            same = [json.loads(row[0]) for row in db.execute(
                "SELECT body FROM claims WHERE resource_key=? AND status IN ('held','uncertain')", (key,))]
            total, owned = db.execute("SELECT count(*),coalesce(sum(root=?),0) FROM claims WHERE status IN ('held','uncertain')", (actor['sessionId'],)).fetchone()
            if any(row['status'] == 'uncertain' or row['mode'] != 'shared' or mode != 'shared' for row in same):
                # Do not disclose another workspace's notes or conversation.
                result = {'outcome': 'conflict', 'resourceKey': key,
                          'detail': 'This resource key is already held. Coordinate with its owner or use a different resource.'}
            elif any(row['owner'] == actor for row in same):
                result = {'outcome': 'conflict', 'resourceKey': key,
                          'detail': 'This principal already holds this shared resource. Inspect its existing reservation.'}
            elif (limits['maxResourceReservations'] and total >= limits['maxResourceReservations']) or (limits['maxResourceReservationsPerChat'] and owned >= limits['maxResourceReservationsPerChat']):
                result = {'outcome': 'limit', 'detail': 'Resource reservation capacity is full. Inspect and release unused reservations or adjust Advanced execution limits in Settings.'}
            else:
                row = {'id': str(uuid.uuid4()), 'workspaceId': workspace_id, 'resourceKey': key,
                       'owner': dict(actor), 'mode': mode, 'status': 'held', 'revision': 1,
                       'note': args.get('note', '').strip(), 'createdAt': time.time()}
                db.execute('INSERT INTO claims VALUES(?,?,?,?,?,?)',
                    (row['id'], workspace_id, key, actor['sessionId'], row['status'], json.dumps(row)))
                result = {'outcome': 'reserved', 'reservation': row}
        elif action in {'workspace.resources.release', 'workspace.resources.uncertain'}:
            found = db.execute('SELECT body FROM claims WHERE id=? AND workspace=?', (args['id'], workspace_id)).fetchone()
            if not found:
                raise ValueError('This reservation was not found in this workspace.')
            row = json.loads(found[0])
            if actor.get('kind') != 'user' and actor != row['owner']:
                raise ValueError('Only this reservation\'s actual owner or a user can change it.')
            if row['revision'] != args['expectedRevision'] or row['status'] not in HOLDING:
                result = {'outcome': 'stale', 'detail': 'The reservation changed. Inspect its current revision before updating.'}
            else:
                evidence = args.get('evidence', '').strip()
                if not evidence:
                    raise ValueError('Describe the observation before changing a reservation.')
                row.update(status='released' if action.endswith('.release') else 'uncertain',
                           evidence=evidence, updatedBy=dict(actor), updatedAt=time.time(), revision=row['revision'] + 1)
                db.execute('UPDATE claims SET status=?,body=? WHERE id=?', (row['status'], json.dumps(row), row['id']))
                result = {'outcome': row['status'], 'reservation': row}
        else:
            raise ValueError('Unknown resource reservation action.')
        db.execute('UPDATE metadata SET revision=revision+1 WHERE id=1')
        db.execute('INSERT INTO commands VALUES(?,?,?)', (command_id, signature, json.dumps(result)))
        return result


async def dispatch_reservation(app, action, args, origin, command_id, caller):
    """Bind authority before entering the shared resource owner."""
    from .collaboration import PRINCIPAL
    from .errors import AppError
    workspace = next((w for w in app.state['workspaces'] if w['id'] == args['workspaceId']), None)
    if action in {'workspace.resources.provision', 'workspace.resources.read', 'workspace.resources.retire'}:
        from .task_resources import dispatch
        return await dispatch(app, action, args, origin, command_id, caller)
    if action == 'workspace.resources.managed':
        return await managed(app, workspace, args, origin, caller)
    # Retained claims can be reconciled even after a registration is removed.
    # Only acquiring new claims requires a currently registered workspace.
    user = origin in {'ui', 'user'}
    actor = {'kind': 'user'}
    read_binding = None
    if not user or action == 'workspace.resources.reserve':
        sid = args.get('sessionId') if user else caller
        if not sid or not user and args.get('sessionId', sid) != sid:
            raise AppError('Choose the calling conversation as resource owner.', 403)
        session = app._session(sid, hydrate=False)
        if session.get('sessionKind', 'root') != 'root':
            raise AppError('Resource owners require their host-bound root conversation.', 403)
        if action not in {'workspace.resources.release', 'workspace.resources.uncertain'} and (workspace is None or session.get('workspace') != workspace['path']):
            raise AppError('Resource reservations belong to the calling conversation\'s workspace.', 403)
        native = session.get('runtimeSessionId') or session.get('nativeIdentity') or sid
        actor = {'kind': 'agent', 'sessionId': sid, 'principal': native if user else PRINCIPAL.get() or native}
        if action == 'workspace.resources.reservations':
            read_binding = (sid, session.get('workspace'))
    try:
        result = await asyncio.to_thread(reservation_command, app.data_dir, args['workspaceId'], action, args, actor, command_id)
    except (ValueError, OSError, sqlite3.Error) as exc:
        raise AppError(str(exc), 409) from None
    if read_binding and app._session(read_binding[0], hydrate=False).get('workspace') != read_binding[1]:
        raise AppError('The conversation moved; inspect reservations from its current workspace.', 409)
    return {'accepted': True, 'result': result}


async def managed(app, workspace, args, origin, caller):
    """Select explicit task scope without hydrating history or warming runtime."""
    from .errors import AppError
    sid = args['sessionId']
    if origin not in {'ui', 'user'} and sid != caller:
        raise AppError('Inspect resources through the calling conversation.', 403)
    session = app._session(sid, hydrate=False)
    if (workspace is None or session.get('workspace') != workspace['path']
            or session.get('sessionKind', 'root') != 'root'):
        raise AppError('Choose a root conversation in this workspace.', 403)
    # Capture identity before yielding; these owners bind every read to sid.
    binding = (session.get('workspace'), session.get('sessionKind', 'root'))
    limit = args.get('limit', 25)
    try:
        from .task_resources import read, access
        publishing, operations, storage = await asyncio.gather(
            asyncio.to_thread(app.publishing.store.resources, sid, limit, args.get('publishingBefore')),
            asyncio.to_thread(app.operations.journal.resources, sid, limit, args.get('operationsBefore')),
            asyncio.to_thread(read, app.data_dir, sid, {'limit': min(limit, 32)}),
        )
        storage['items'] = await access(app, sid, storage['items'])
    except (ValueError, OSError, sqlite3.Error) as exc:
        raise AppError(str(exc), 409) from None
    current = app._session(sid, hydrate=False)
    if binding != (current.get('workspace'), current.get('sessionKind', 'root')):
        raise AppError('The conversation moved; inspect its current workspace.', 409)
    return {'accepted': True, 'result': {
        'workspaceId': workspace['id'], 'sessionId': sid, 'publishing': publishing, 'operations': operations,
        'storage': storage,
        'isolation': [{'kind': kind, 'status': 'inspect-storage-records',
                       'enforcement': 'separate-storage-only'}
                      for kind in ('scratch-directory', 'sqlite-database', 'browser-profile')],
        'coverage': {'publishing': 'local-static-listeners', 'operations': 'recorded-native-lifecycle',
                     'dynamicPreviewDiscovery': 'unsupported', 'remotePublishing': 'not-inspected',
                     'operationWorkingDirectory': 'unavailable', 'externalProcesses': 'not-inspected'},
    }}


def command(home, workspace_id, action, args, command_id=None):
    directory = home / 'workspace-resources'
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (uuid.UUID(workspace_id).hex + '.json')
    with FileLock(str(target) + '.lock'):
        value = json.loads(target.read_text()) if target.exists() else {'revision': 0, 'resources': [], 'commands': {}}
        if action == 'workspace.resources.list':
            return {'revision': value['revision'], 'resources': value['resources']}
        fingerprint = json.dumps([action, args], sort_keys=True)
        prior = value['commands'].get(command_id) if command_id else None
        if prior:
            if prior['fingerprint'] != fingerprint:
                raise ValueError('This resource request already belongs to a different update.')
            return prior['result']
        if action == 'workspace.resources.add':
            for key in ('kind', 'resourceId', 'owner', 'note'):
                if not isinstance(args.get(key, ''), str) or len(args.get(key, '')) > 1000:
                    raise ValueError('Invalid resource description.')
            if not all(args.get(key, '').strip() for key in ('kind', 'resourceId', 'owner')):
                raise ValueError('Identify the resource type, identifier and owner.')
            result = {key: args.get(key, '').strip() for key in ('kind', 'resourceId', 'owner', 'note')}
            result.update(id=str(uuid.uuid4()), status='active', revision=1, createdAt=time.time())
            value['resources'].append(result)
        else:
            result = next((row for row in value['resources'] if row['id'] == args['id']), None)
            if not result or result['revision'] != args['expectedRevision']:
                raise ValueError('The resource record changed. Inspect it before updating.')
            if args['status'] not in {'active', 'reaped', 'observed_absent'} or not args.get('evidence', '').strip():
                raise ValueError('An observed resource status needs evidence. Recording it does not perform teardown.')
            result.update(status=args['status'], evidence=args['evidence'].strip(), updatedAt=time.time(), revision=result['revision'] + 1)
        value['revision'] += 1
        if command_id:
            value['commands'][command_id] = {'fingerprint': fingerprint, 'result': dict(result)}
            value['commands'] = dict(list(value['commands'].items())[-200:])
        atomic(target, value)
        return result
