"""Reviewed, recoverable deletion of explicitly allocated managed conversations.

The journal records ownership, not chat content. A confirmed tombstone is saved
before any files move. Startup resumes incomplete cleanup before hydrating views.
Managed folders are storage organization, not a security sandbox.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import sys
import time
import uuid
from types import SimpleNamespace

from .managed_chats import is_managed, metadata
from .session_files import amplifier_home, project_slug, validate_id

ACTIVE = {'working', 'running', 'starting', 'ready', 'queued', 'pending', 'stopping', 'cancel_requested'}
BUSY = 'Finish or stop this chat’s active work and pending interactions before deleting it.'
UNOWNED = 'The saved chat ownership could not be verified. Its files were preserved.'
LOG = logging.getLogger(__name__)


def shared_identity(identity):
    """Foundation's shared execution/checkpoint address, not a native worker ID."""
    return isinstance(identity, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,127}', identity) is not None


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS managed_deletions (id TEXT PRIMARY KEY, token TEXT UNIQUE, phase TEXT NOT NULL, value TEXT NOT NULL)')


def tombstones(db):
    return [json.loads(row[0]) for row in db.execute("SELECT value FROM managed_deletions WHERE phase IN ('confirmed','done')")]


def removed(db, identity=None, workspace=None):
    return any(identity in row['ids'] or (not row.get('preserveWorkspace') and workspace == row['workspace']) for row in tombstones(db))


def _tables(db):
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def _mentions(value, identifiers):
    if isinstance(value, dict):
        return any(key in identifiers or _mentions(item, identifiers) for key, item in value.items())
    if isinstance(value, list):
        return any(_mentions(item, identifiers) for item in value)
    return isinstance(value, str) and value in identifiers


def _owned(value, ids):
    return isinstance(value, dict) and any(value.get(key) in ids for key in ('sessionId', 'session_id', 'rootSessionId', 'ownerSessionId'))


def _safe(path):
    """No symlink ancestor, including an otherwise in-root symlink."""
    path = Path(path).absolute()
    for part in (*reversed(path.parents), path):
        if part.is_symlink():
            raise ValueError('This chat contains a symbolic link. Remove the link before deleting the chat; its files were preserved.')
    return path


def _inventory(path):
    path = _safe(path)
    if not path.exists():
        return None
    rows = []
    def visit(item):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise ValueError('This chat contains a symbolic link or special file. Its files were preserved.')
        if info.st_dev != path.stat().st_dev:
            raise ValueError('This chat contains a mounted filesystem. Its files were preserved.')
        rows.append([str(item.relative_to(path)), info.st_ino, info.st_size if item.is_file() else 0, info.st_mtime_ns, item.is_file()])
        if item.is_dir():
            for child in sorted(item.iterdir()):
                visit(child)
    visit(path)
    return {'path': str(path), 'device': path.stat().st_dev, 'inode': path.stat().st_ino,
            'stamp': hashlib.sha256(_json(rows).encode()).hexdigest(),
            'fileCount': sum(1 for row in rows if row[4]), 'fileBytes': sum(row[2] for row in rows)}


def _scope(app, sid):
    row = app._session(sid)
    if not is_managed(row):
        raise ValueError('Only chats created without a workspace can be deleted. Archive this workspace chat instead.')
    folder = Path(row.get('workspace', '')).absolute()
    try:
        canonical = str(uuid.UUID(folder.parent.name)) == folder.parent.name
    except ValueError:
        canonical = False
    if not canonical or folder != app.data_dir.resolve()/'chats'/folder.parent.name/'files':
        raise ValueError(UNOWNED)
    _safe(folder)
    marker = metadata(folder)
    if marker is None or folder != app.data_dir.resolve()/'chats'/marker['id']/'files':
        raise ValueError(UNOWNED)
    root = folder.parent
    _safe(root/'managed-chat.json')
    native_id = row.get('nativeIdentity') or row.get('runtimeSessionId') or row['id']
    from .session_navigation import is_top_level
    if native_id != marker['id'] or not is_top_level(row):
        raise ValueError('Delete the managed conversation that owns this worker instead.')
    directory = getattr(app, 'directory', None)
    sessions = ([app._admit_session(item) for item in directory.select('workspace=?', (str(folder),), limit=-1)]
                if directory is not None else [item for item in app.state['sessions'] if item.get('workspace') == str(folder)])
    ids = {row['id'], marker['id']}
    for item in sessions:
        if item['id'] != row['id'] and is_top_level(item):
            raise ValueError('Another conversation uses this chat folder. Archive it instead; no files were removed.')
        ids.update(filter(None, (item['id'], item.get('nativeIdentity'), item.get('runtimeSessionId'))))
    children = (directory.select('parent_id IN (SELECT value FROM json_each(?))', (json.dumps(sorted(ids)),), limit=-1)
                if directory is not None else app.state['sessions'])
    for item in children:
        if item.get('parentId') in ids and not is_top_level(item) and item.get('workspace') != str(folder):
            raise ValueError('A worker uses a separate folder. Archive this chat until that worker is detached.')
    return row, marker, sessions, ids


def _idle(app, sessions, ids):
    publishing = getattr(app, 'publishing', None)
    if publishing and any(publishing.owns_records(sid) for sid in ids):
        raise ValueError('This chat owns retained publishing releases and receipts. Archive it to preserve their ownership.')
    if app.recall.task and not app.recall.task.done():
        raise ValueError('Wait for history indexing to finish before deleting this chat.')
    for row in sessions:
        if row.get('naming', {}).get('status') == 'working':
            raise ValueError(BUSY)
        if row.get('status') in ACTIVE or row.get('configurationBusy') or row.get('_deleting') or row.get('ownership', {}).get('status') in {'blocked','yielding','yielded','yield-failed','taking-over'}:
            raise ValueError(BUSY)
        if any(item.get('status') in ACTIVE or item.get('persistent') and item.get('status') == 'idle' for item in row.get('workers', [])):
            raise ValueError(BUSY)
        if any(item.get('status') == 'pending' for item in row.get('approvals', []) + row.get('questions', [])):
            raise ValueError(BUSY)
        if row.get('worktrees') or row.get('worktreeHandoffs') or row.get('workingDirectory') not in {None, row.get('workspace')}:
            raise ValueError('This chat has a separate worktree. Archive it instead; external worktree files are preserved.')
    # Scheduling can create new conversations. Until its ownership is explicitly
    # detached, refuse instead of deleting an independent destination or replaying.
    for record in app.schedules.store.list():
        if record.get('sessionId') in ids and record.get('status') != 'cancelled':
            raise ValueError('Cancel this chat’s schedules before deleting it, or archive the chat.')
    from amplifier_scheduling.store import ACTIVE_RUNS
    for encoded, in app.schedules.store.db.execute('SELECT value FROM schedule_runs'):
        run = json.loads(encoded)
        if run.get('sessionId') in ids and run.get('phase') in ACTIVE_RUNS:
            raise ValueError(BUSY)
    journal = app.operations.journal
    with journal.lock:
        for encoded, in journal.db.execute('SELECT value FROM operations WHERE session_id IN ('+','.join('?'*len(ids))+')', tuple(ids)):
            if json.loads(encoded).get('state', json.loads(encoded).get('status')) in ACTIVE | {'outcome_unknown'}:
                raise ValueError(BUSY)
    if 'smart_tool_operations' in _tables(app.db):
        for encoded, in app.db.execute('SELECT value FROM smart_tool_operations'):
            item = json.loads(encoded)
            if _owned(item, ids) and item.get('status', item.get('phase')) in ACTIVE:
                raise ValueError(BUSY)
    if (_owned(app.state.get('voice'), ids) and app.state['voice'].get('status') not in {'disconnected','error'}) or any(_owned(item, ids) for item in app.state.get('voice', {}).get('calls', [])):
        raise ValueError(BUSY)


def _paths(app, marker, sessions, ids):
    folder = Path(marker['workspace'])
    project = project_slug(folder)
    native = _safe(amplifier_home()/'projects'/project/'sessions')
    paths = [folder.parent]
    native_ids = {marker['id']}
    native_meta = {}
    if native.exists():
        for directory in native.iterdir():
            # Foundation keeps stable metadata locks beside session directories.
            # They contain no chat content and must keep their lock inode.
            if directory.name.startswith('.') and directory.name.endswith('.metadata.lock'):
                identity = directory.name[1:-len('.metadata.lock')]
                validate_id(identity)
                if not _safe(directory).is_file() or not (native/identity).is_dir():
                    raise ValueError(UNOWNED)
                continue
            validate_id(directory.name)
            _safe(directory)
            if not directory.is_dir():
                raise ValueError(UNOWNED)
            meta_path = directory/'metadata.json'
            if meta_path.exists():
                info = json.loads(_safe(meta_path).read_text())
                if info.get('working_dir', info.get('workspace')) != str(folder):
                    raise ValueError(UNOWNED)
                native_meta[directory.name] = info
            elif directory.name not in ids:
                raise ValueError(UNOWNED)
            native_ids.add(directory.name)
        for identity, info in native_meta.items():
            seen = {identity}
            while identity != marker['id']:
                identity = native_meta.get(identity, {}).get('parent_id')
                if not identity or identity in seen:
                    raise ValueError('Unrelated history uses this folder. Archive the chat instead; its history was preserved.')
                seen.add(identity)
        paths.extend(native/identity for identity in native_ids)
    ids.update(native_ids)
    from amplifier_foundation.session.shared_state import SharedSessionStore
    for identity in ids:
        validate_id(identity)
        paths.append(app.data_dir/'sessions'/identity)
    # Native workers run under their verified root's execution lease. Their IDs
    # need not be shared-store addresses. Keep every native path in the plan;
    # only checkpoint/lease addresses use Foundation's stricter root grammar.
    shared_ids = {identity for identity in native_ids if shared_identity(identity)}
    if marker['id'] not in shared_ids or any(identity not in native_meta for identity in native_ids - shared_ids):
        raise ValueError(UNOWNED)
    for identity in native_ids - shared_ids:
        fence = _safe(native/identity/'transfer-fence.json')
        if fence.exists():
            raise ValueError('A native worker has a transfer fence. Its history was preserved.')
    for identity in sorted(shared_ids):
        store = SharedSessionStore(folder, identity)
        _safe(store.checkpoint_path)
        if store.checkpoint_path.exists():
            store.read()  # validates workspace/session identity before deletion
            paths.append(store.checkpoint_path)
    ci_root = os.environ.get('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', '').strip()
    if ci_root:
        ci = _safe(Path(ci_root).expanduser().absolute()/project/'sessions')
        if ci != native:
            # Only this chat's native IDs; a foreign capture directory is a blocker.
            if ci.exists() and any(item.name not in native_ids for item in ci.iterdir()):
                raise ValueError('Unexpected capture history uses this folder. Its files were preserved.')
            paths.extend(ci/identity for identity in native_ids)
    return list(dict.fromkeys(paths)), project, native_ids, shared_ids


def _scrub_local(record, ids, artifacts):
    result = copy.deepcopy(record)
    for key in ('drafts', 'attachments', 'canvasTabs'):
        value = result.get(key)
        if isinstance(value, dict):
            result[key] = {k:v for k,v in value.items() if k not in ids}
    if result.get('selectedSessionId') in ids:
        result['selectedSessionId'] = None
        result['selectedWorkspaceId'] = None
        result['canvas'] = {}
        result['view'] = {**result.get('view', {}), 'draft': '', 'panel': 'settings' if result.get('view', {}).get('panel') == 'settings' else None}
    if result.get('canvas', {}).get('id') in artifacts or _owned(result.get('canvas'), ids):
        result['canvas'] = {}
    for key in ('canvasViews', 'canvasTabs'):
        if key in result:
            result[key] = _prune(result[key], ids, artifacts)
    return result


def _prune(value, ids, artifacts):
    if isinstance(value, list):
        return [_prune(row, ids, artifacts) for row in value if not _owned(row, ids) and not (isinstance(row, dict) and row.get('id') in artifacts) and not (isinstance(row, str) and row in ids | artifacts)]
    if isinstance(value, dict):
        return {key: _prune(item, ids, artifacts) for key, item in value.items() if key not in ids and key not in artifacts and key.partition(':')[-1] not in artifacts}
    return None if isinstance(value, str) and value in ids | artifacts else value


def scrub_state(state, plan):
    ids, artifacts = set(plan['ids']), set(plan['artifactIds'])
    state['sessions'] = [row for row in state.get('sessions', []) if row['id'] not in ids and (plan.get('preserveWorkspace') or row.get('workspace') != plan['workspace'])]
    state['pinnedSessionIds'] = [sid for sid in state.get('pinnedSessionIds', []) if sid not in ids]
    library = state.get('conversationOrganization', {})
    library['archived'] = {sid:v for sid,v in library.get('archived', {}).items() if sid not in ids}
    for row in library.get('collections', []):
        row['sessionIds'] = [sid for sid in row['sessionIds'] if sid not in ids]
    state['canvasArtifacts'] = [row for row in state.get('canvasArtifacts', []) if row.get('id') not in artifacts]
    for key in ('runtimeControl', 'events', 'canvasApps', 'smartTools'):
        if key in state:
            state[key] = _prune(state[key], ids, artifacts)
    state['events'] = [row for row in state.get('events', []) if not _mentions(row, ids)]
    for key, value in _scrub_local(state, ids, artifacts).items():
        if key in ('selectedSessionId', 'selectedWorkspaceId', 'view', 'canvas', 'canvasTabs', 'canvasViews'):
            state[key] = value


def _app_rows(app, ids):
    rows = []
    for table in ('conversation_shares', 'questions', 'output_records'):
        if table in _tables(app.db):
            rows += [[table, identity, json.loads(value)] for identity, value in app.db.execute('SELECT id,value FROM '+table+' WHERE session_id IN ('+','.join('?'*len(ids))+')', tuple(ids))]
    if 'smart_tool_operations' in _tables(app.db):
        rows += [['smart_tool_operations', identity, json.loads(value)] for identity, value in app.db.execute('SELECT id,value FROM smart_tool_operations') if _owned(json.loads(value), ids)]
    return rows


def _resources(app, removed_values, retained):
    from .resource_files import references, retained_references
    from .state_storage import resource
    def expand(pending):
        seen = set()
        while pending:
            identity = pending.pop()
            if identity in seen:
                continue
            if not re.fullmatch('[a-f0-9]{64}', identity):
                raise ValueError('An artifact reference is invalid. Its files were preserved.')
            seen.add(identity)
            pending.extend(references(resource(app.db, identity)))
        return seen
    candidates = expand(list(references(removed_values)))
    if not candidates:
        return []
    # Owned DB rows are omitted explicitly; other durable stores keep references.
    retained_values = [retained]
    tables = _tables(app.db)
    for table in ('client_views', 'smart_tool_operations', 'conversation_shares', 'output_records'):
        if table in tables:
            retained_values.extend(json.loads(value) for value, in app.db.execute('SELECT value FROM '+table) if value not in app._delete_removed_values)
    kept = expand(list(references(retained_values)))
    return sorted(candidates - kept)

def _cold_roots(app, ids):
    """Exact payload manifests remain deletion-owned after snapshot hydration.

    Explicit read snapshots materialize bodies without their private markers.
    Consult both live manifests and the last committed roots, partitioned by
    the same confirmed conversation scope. Unrelated chats retain shared blobs.
    """
    states = [app._state]
    from .state_records import load
    committed = load(app.db)
    if committed:
        states.append(committed)
    removed, retained = [], []
    for state in states:
        records = [(row['id'], row) for row in state.get('sessions', [])]
        records.extend(state.get('runtimeControl', {}).items())
        for identity, row in records:
            manifest = dict.get(row, '_coldFields', {})
            if row.get('$viewPayload'):
                manifest = {**manifest, '$viewPayload': row['$viewPayload']}
            if manifest:
                (removed if identity in ids else retained).append(copy.deepcopy(manifest))
    return removed, retained


def plan(app, sid, history=None):
    if history is not None:
        from .history_cleanup import scope
        row, marker, sessions, ids, paths, project, native_ids = scope(app, history)
        shared_ids = native_ids  # history cleanup refuses unsupported identities
    else:
        row, marker, sessions, ids = _scope(app, sid)
        paths, project, native_ids, shared_ids = _paths(app, marker, sessions, ids)
    artifacts = [item for item in app.state.get('canvasArtifacts', []) if _owned(item, ids)]
    artifact_ids = [item['id'] for item in artifacts]
    owned_rows = _app_rows(app, ids)
    provisional = {'ids': sorted(ids), 'workspace': marker['workspace'], 'artifactIds': artifact_ids, **({'preserveWorkspace': True, 'historySelection': history} if history is not None else {})}
    retained = copy.deepcopy(app._state)
    scrub_state(retained, provisional)
    retained_clients = [_scrub_local(value, ids, set(artifact_ids)) for value in app.clients.records.values()]
    values = [sessions, artifacts, [item[2] for item in owned_rows], [value for value in app.clients.records.values() if value.get('selectedSessionId') in ids]]
    cold_removed, cold_retained = _cold_roots(app, ids)
    values.extend(cold_removed)
    retained_clients.extend(cold_retained)
    # Resource graph retention is evaluated using scrubbed client values.
    removed_encoded = set()
    for table, identity, _ in owned_rows:
        removed_encoded.add(app.db.execute('SELECT value FROM '+table+' WHERE id=?', (identity,)).fetchone()[0])
    removed_encoded.update(value for value, in app.db.execute('SELECT value FROM client_views'))
    app._delete_removed_values = removed_encoded
    try:
        resources = _resources(app, values, [retained, retained_clients])
    finally:
        del app._delete_removed_values
    paths.extend(app.data_dir/'artifacts'/(identity+'.json') for identity in resources)
    # Attachments are shared globally. Preserve references in other chat records,
    # immutable bodies and native transcripts, including independent copies.
    encoded = _json(values)
    candidates = {identity for identity in re.findall(r'(?<![a-f0-9])[a-f0-9]{32}(?![a-f0-9])', encoded)
                  if (app.data_dir/'attachments'/identity).exists()}
    retained_tables = []
    for table, column in (('feedback_attachments','metadata'), ('feedback_requests','payload'), ('feedback_requests','receipt'), ('feedback_followups','payload'), ('feedback_followups','receipt')):
        if table in _tables(app.db):
            retained_tables.extend(value for value, in app.db.execute('SELECT '+column+' FROM '+table))
    keep_text = _json([retained, retained_clients, retained_tables])
    shared = {identity for identity in candidates if identity in keep_text}
    if candidates - shared:
        for source in (amplifier_home()/'projects').glob('*/sessions/*'):
            if source in paths:
                continue
            for name in ('transcript.jsonl', 'transcript.jsonl.backup', 'unified/view.json'):
                path = _safe(source/name)
                if path.exists():
                    content = path.read_text(errors='replace')
                    shared.update(identity for identity in candidates if identity in content)
        for identity, value in app.db.execute('SELECT id,value FROM state_resources'):
            if identity not in resources:
                from .resource_files import resolve
                content = _json(resolve(app.db, identity, json.loads(value)))
                shared.update(candidate for candidate in candidates if candidate in content)
    attachments = sorted(candidates-shared)
    for identity in attachments:
        from .attachments import metadata as attachment_metadata
        attachment_metadata(app.data_dir, identity)
        paths.append(app.data_dir/'attachments'/identity)
    entries = [entry for path in paths if (entry := _inventory(path))]
    from amplifier_foundation.session.shared_state import SharedSessionStore
    coordination = [str((SharedSessionStore.from_identity({'version':1, 'workspace':marker['workspace'], 'sessionId':identity}) if history is not None else SharedSessionStore(marker['workspace'], identity)).checkpoint_path.with_name('session.lock')) for identity in sorted(shared_ids)]
    native = amplifier_home()/'projects'/project/'sessions'
    metadata_locks = [str(_safe(native/f'.{identity}.metadata.lock')) for identity in sorted(native_ids)
                      if (native/identity).is_dir()]
    review_identity = {'scope': 'project' if history is not None and not history.get('nativeId') else 'session',
                       'workspace': marker['workspace'],
                       'sessionId': history.get('nativeId') if history is not None else row['id']}
    if row.get('reviewActivityAt') is not None:
        review_identity['lastActivityAt'] = row['reviewActivityAt']
    return {**provisional, 'reviewIdentity': review_identity, 'id': sid, 'title': row['title'], 'rootId': marker['id'], 'project': project, 'coordinationLocks': coordination,
        'nativeIds': sorted(native_ids), 'sharedIds': sorted(shared_ids), 'metadataLocks': metadata_locks, 'paths': entries,
        'contentStamp': hashlib.sha256(_json([sessions, artifacts, owned_rows]).encode()).hexdigest(), 'resourceIds': resources, 'attachmentIds': attachments,
        'rows': [[table, identity] for table, identity, _ in owned_rows],
        'summary': {'conversationCount': len(native_ids) if history is not None else 1, 'workerCount': max(0, len(native_ids)-1),
                    'fileCount': sum(item['fileCount'] for item in entries), 'fileBytes': sum(item['fileBytes'] for item in entries),
                    'attachmentCount': len(attachments), 'artifactCount': len(artifact_ids),
                    'shareCount': sum(table == 'conversation_shares' for table, _, _ in owned_rows)}}


def _cleanup_db(db, plan):
    ids, tables = set(plan['ids']), _tables(db)
    for table, identity in plan['rows']:
        if table in tables:
            if table == 'output_records' and 'output_comments' in tables:
                db.execute('DELETE FROM output_comments WHERE output_id=?', (identity,))
            db.execute('DELETE FROM '+table+' WHERE id=?', (identity,))
    for identity in plan['resourceIds']:
        db.execute('DELETE FROM state_resources WHERE id=?', (identity,))
    for identity, value in db.execute('SELECT id,value FROM client_views').fetchall() if 'client_views' in tables else []:
        db.execute('UPDATE client_views SET value=? WHERE id=?', (_json(_scrub_local(json.loads(value), ids, set(plan['artifactIds']))), identity))
    for identity, value in db.execute('SELECT id,receipt FROM commands').fetchall():
        if _mentions(json.loads(value), ids):
            db.execute('UPDATE commands SET receipt=? WHERE id=?', (_json({'accepted': False, 'deleted': True, 'error': 'This conversation was permanently deleted. Its work was not replayed.'}), identity))
    for table, column in (('output_receipts','value'), ('feedback_requests','receipt'), ('feedback_followups','receipt')):
        if table not in tables:
            continue
        for identity, value in db.execute('SELECT id,'+column+' FROM '+table).fetchall():
            if _mentions(json.loads(value), ids | set(plan['artifactIds'])):
                db.execute('UPDATE '+table+' SET '+column+'=? WHERE id=?', (_json({'deleted': True}), identity))


def _cleanup_aux(home, plan):
    ids = plan['ids']; marks = ','.join('?'*len(ids))
    def connect(path):
        _safe(path)
        return sqlite3.connect(path, timeout=10)
    operation = home/'operations.sqlite3'
    if operation.exists():
        with connect(operation) as db:
            for table in ('operation_events', 'operation_output'):
                db.execute('DELETE FROM '+table+' WHERE operation_id IN (SELECT id FROM operations WHERE session_id IN ('+marks+'))', ids)
            db.execute('DELETE FROM operations WHERE session_id IN ('+marks+')', ids)
            if 'operation_requests' in _tables(db):
                db.execute('DELETE FROM operation_requests WHERE session_id IN ('+marks+')', ids)
    recall = home/'recall.sqlite3'
    if recall.exists():
        with connect(recall) as db:
            db.execute('DELETE FROM sources WHERE id IN ('+marks+')', ids)
            db.execute('DELETE FROM messages WHERE session_id IN ('+marks+')', ids)
            memories = [row[0] for row in db.execute("SELECT id FROM memories WHERE (scope='task' AND target IN ("+marks+")) OR (scope='workspace' AND target=?)", [*ids, None if plan.get('preserveWorkspace') else plan['workspace']])]
            for identity in memories:
                db.execute('DELETE FROM memory_versions WHERE id=?', (identity,))
                db.execute('DELETE FROM memories WHERE id=?', (identity,))
            for identity, encoded in db.execute('SELECT id,result FROM memory_receipts').fetchall():
                if _mentions(json.loads(encoded), set(ids+memories)):
                    db.execute('UPDATE memory_receipts SET result=? WHERE id=?', (_json({'deleted':True}), identity))
    schedules = home/'schedules.sqlite3'
    if schedules.exists():
        with connect(schedules) as db:
            db.execute('DELETE FROM schedule_runs WHERE session_id IN ('+marks+')', ids)
            db.execute('DELETE FROM schedules WHERE session_id IN ('+marks+')', ids)
            for identity, encoded in db.execute('SELECT id,result FROM schedule_commands').fetchall():
                if _mentions(json.loads(encoded), set(ids)):
                    db.execute('UPDATE schedule_commands SET result=? WHERE id=?', (_json({'deleted': True}), identity))
    diagnostics = home/'diagnostics'/'events.sqlite3'
    if diagnostics.exists():
        with connect(diagnostics) as db:
            db.execute('DELETE FROM deliveries WHERE record_id IN (SELECT id FROM records WHERE session IN ('+marks+') OR workspace=?)', [*ids, None if plan.get('preserveWorkspace') else plan['workspace']])
            db.execute('DELETE FROM records WHERE session IN ('+marks+') OR workspace=?', [*ids, None if plan.get('preserveWorkspace') else plan['workspace']])
            for path, in db.execute('SELECT path FROM captures').fetchall():
                if any(Path(path).is_relative_to(item['path']) for item in plan['paths']):
                    db.execute('DELETE FROM captures WHERE path=?', (path,))


def _release_all(held, *, cause=None):
    """Attempt every release; report cleanup errors without replacing a cause."""
    failures = []
    for item in reversed(held):
        try:
            item.release()
        except BaseException as exc:
            failures.append(exc)
    if not failures:
        return
    failure = BaseExceptionGroup('Conversation lock release failed', failures)
    if cause is None:
        raise failure
    cause.add_note(f'Conversation lock cleanup also failed: {failure!r}')
    LOG.error('Conversation lock cleanup failed while preserving the original exception',
              exc_info=(type(failure), failure, failure.__traceback__))


def _hold(plan):
    from amplifier_foundation.session.shared_state import SharedSessionStore
    held = []
    try:
        for identity in plan.get('sharedIds', plan['nativeIds']):
            store = (SharedSessionStore.from_identity({'version':1, 'workspace':plan['workspace'], 'sessionId':identity}) if plan.get('preserveWorkspace') else SharedSessionStore(plan['workspace'], identity))
            _safe(store.checkpoint_path)
            held.append(store.acquire(app='amplifier-unified', operation='managed-chat-delete'))
        # These sibling flocks match Foundation metadata_lock's filelock protocol,
        # not execution ownership. Root leases above exclude native execution;
        # metadata exclusion also covers cooperating idle naming writers.
        for path in plan.get('metadataLocks', []):
            held.append(_RecoveryLock(path, create=True))
        return held
    except BaseException as exc:
        _release_all(held, cause=exc)
        if not isinstance(exc, Exception):
            raise
        raise ValueError('Another application owns this conversation. Close it there before deleting this chat.') from exc


class _RecoveryLock:
    """Thread-neutral metadata flock, or an existing confirmed-recovery lock."""
    def __init__(self, path, *, create=False):
        import fcntl
        self.fd = os.open(_safe(path), os.O_RDWR | os.O_NOFOLLOW | (os.O_CREAT if create else 0), 0o600)
        try:
            if not stat.S_ISREG(os.fstat(self.fd).st_mode):
                raise ValueError('The saved lock is not a regular file.')
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(self.fd)
            raise

    def release(self):
        import fcntl
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)


def _recovery_hold(plan):
    # SharedSessionStore requires an existing workspace. Recreating the deleted
    # workspace just to acquire its lock would reintroduce resume/import paths.
    held = []
    try:
        for path in [*plan['coordinationLocks'], *plan.get('metadataLocks', [])]:
            held.append(_RecoveryLock(path))
        return held
    except BaseException as exc:
        _release_all(held, cause=exc)
        raise


async def _acquire(plan):
    # Cancelling to_thread does not stop acquisition. Keep the task alive and
    # release its eventual handles even if the request ends before it returns.
    task = asyncio.create_task(asyncio.to_thread(_hold, plan))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancelled:
        # Exception variables are cleared after except; bind the cancellation
        # into the callback so late cleanup can retain its diagnostics.
        def release_result(finished, cause=cancelled):
            if not finished.cancelled() and finished.exception() is None:
                _release_all(finished.result(), cause=cause)
        task.add_done_callback(release_result)
        raise


def _done_value(plan):
    return _json({key:plan[key] for key in ('id','ids','rootId','workspace','project','preserveWorkspace','historySelection') if key in plan})


def _stage(plan, *, db=None):
    # Called under the app action lock. Content-addressed resource creation can
    # now safely create a new original path while old bytes purge off-loop.
    for item in plan['paths']:
        path = _safe(item['path'])
        target = _safe(path.with_name('.unified-delete-'+plan['token']+'-'+path.name))
        resource = path.parent.name == 'artifacts' and re.fullmatch('[a-f0-9]{64}\\.json', path.name)
        if resource and not target.exists():
            if db is None or not db.in_transaction:
                raise ValueError('Resource staging requires writer exclusion.')
            if db.execute('SELECT 1 FROM state_resources WHERE id=?', (path.stem,)).fetchone():
                continue  # another owner re-adopted the exact hash before staging
        current = target if target.exists() else path
        if not current.exists():
            continue
        info = current.stat()
        if (info.st_dev, info.st_ino) != (item['device'], item['inode']):
            raise ValueError('A chat folder changed during deletion; remaining files were preserved.')
        if current == path:
            os.rename(path, target)


def _purge(home, plan):
    with sqlite3.connect(Path(home)/'app.sqlite3') as db:
        db.execute('BEGIN IMMEDIATE')
        _stage(plan, db=db)  # recovery rechecks hashes before staging original paths
    for item in plan['paths']:
        path = Path(item['path'])
        target = _safe(path.with_name('.unified-delete-'+plan['token']+'-'+path.name))
        if not target.exists():
            continue
        info = target.stat()
        if (info.st_dev, info.st_ino) != (item['device'], item['inode']):
            raise ValueError('A chat folder changed during deletion; remaining files were preserved.')
        _inventory(target)
        if target.is_dir():
            if not shutil.rmtree.avoids_symlink_attacks:
                raise ValueError('This host cannot safely remove chat folders.')
            shutil.rmtree(target)
        else:
            target.unlink()
    _cleanup_aux(home, plan)


def recover(home, db, state):
    """Resume only already-confirmed plans, before any saved view can reappear."""
    initialize(db)
    for plan_value, in db.execute("SELECT value FROM managed_deletions WHERE phase='confirmed'").fetchall():
        value = json.loads(plan_value)
        scrub_state(state, value)
        _cleanup_db(db, value)
        from .state_records import checkpoint
        checkpoint(db, state)
        db.commit()
        held = []
        recovery_error = None
        try:
            held = _recovery_hold(value)
            # A confirmed cleanup may have stopped between directory batches.
            # Startup is offline; finish that same reconciliation before marking
            # the durable deletion receipt complete.
            from .resource_files import marked_references, sweep_unindexed_locked
            db.execute('BEGIN IMMEDIATE')
            cursor = None
            try:
                if marked_references(db, state) is None:
                    raise ValueError('Artifact reachability is uncertain; cleanup remains pending.')
                while True:
                    cursor, progress = sweep_unindexed_locked(db, cursor)
                    if progress['complete']:
                        break
            finally:
                if cursor is not None:
                    cursor.close()
                db.rollback()
            _purge(Path(home), value)
        except (OSError, ValueError, sqlite3.Error) as exc:
            recovery_error = exc
            continue  # durable tombstone still prevents resume/reimport/replay
        finally:
            _release_all(held, cause=recovery_error or sys.exception())
        db.execute("UPDATE managed_deletions SET phase='done', value=? WHERE id=?", (_done_value(value), value['id']))
        db.commit()


def _read_plan(home, state, clients, sid, history=None):
    db = sqlite3.connect(home/'app.sqlite3')
    try:
        db.execute('BEGIN')
        context = SimpleNamespace(data_dir=home, db=db, state=state, _state=state,
            clients=SimpleNamespace(records=clients))
        context._session = lambda identity: next(row for row in state['sessions'] if row['id'] == identity)
        return plan(context, sid, history)
    finally:
        db.close()


async def _reconcile_resources(app):
    """Confirmed path holds app lock; one stable graph and writer transaction."""
    from .resource_files import marked_references, sweep_unindexed_locked
    cursor = None
    if getattr(app, 'backup_in_progress', False):
        raise ValueError('Resource backup is active; file cleanup remains pending.')
    app.db.execute('BEGIN IMMEDIATE')
    try:
        roots = [app._state, *app.clients.records.values()]
        if marked_references(app.db, roots) is None:
            raise ValueError('Artifact reachability is uncertain; file cleanup remains pending.')
        while True:
            cursor, progress = sweep_unindexed_locked(app.db, cursor)
            if progress['complete']:
                break
            await asyncio.sleep(0)
    finally:
        if cursor is not None:
            cursor.close()
        app.db.rollback()  # files only, no database mutation


async def reviewed_plan(app, sid, history=None):
    async with app.lock:
        if history is None:
            _, _, sessions, ids = _scope(app, sid)
            _idle(app, sessions, ids)
        state, clients = copy.deepcopy(app._state), copy.deepcopy(app.clients.records)
    result = await asyncio.to_thread(_read_plan, app.data_dir, state, clients, sid, history)
    if history is not None:
        async with app.lock:
            ids = set(result['ids'])
            _idle(app, [row for row in app.state['sessions'] if row['id'] in ids], ids)
    return result


async def dispatch(app, action, args, origin, include_state, history=None):
    from .errors import AppError
    sid = args['id']
    held = []
    fenced_ids = set()
    try:
        async with app.lock:
            if action == 'session.delete':
                saved = app.db.execute('SELECT token,phase,value FROM managed_deletions WHERE id=?', (sid,)).fetchone()
                if not saved or saved[0] != args.get('confirmationToken'):
                    raise ValueError('Review this chat with session.deletePreview before confirming deletion.')
                value = json.loads(saved[2])
                if saved[1] in {'confirmed', 'done'}:
                    return {'accepted': True, 'result': {'id':sid, 'deleted':True, 'cleanupPending':saved[1]=='confirmed'}, **({'state':app.browser_state()} if include_state else {})}
                if value['expiresAt'] < time.time():
                    raise ValueError('The deletion preview expired. Review this chat again.')
        current = await reviewed_plan(app, sid, history)
        async with app.lock:
            # State may have changed while file enumeration yielded. The next
            # confirmed preview also rechecks the full scope and byte stamps.
            if history is None:
                _, _, sessions, ids = _scope(app, sid)
            else:
                ids = set(current['ids'])
                sessions = [row for row in app.state['sessions'] if row['id'] in ids]
            _idle(app, sessions, ids)
            ids = set(current['ids'])
            if hashlib.sha256(_json([sessions, [item for item in app.state.get('canvasArtifacts', []) if _owned(item, ids)], _app_rows(app, ids)]).encode()).hexdigest() != current['contentStamp']:
                raise ValueError('The chat changed during review. Review it again.')
            if action == 'session.deletePreview':
                value = {**current, 'token': uuid.uuid4().hex, 'expiresAt':time.time()+600}
                app.db.execute('INSERT OR REPLACE INTO managed_deletions VALUES (?,?,?,?)', (sid,value['token'],'preview',_json(value)))
                app.db.commit()
                result = {key:value[key] for key in ('id','title','expiresAt','summary','reviewIdentity')}
                result.update(confirmationToken=value['token'],
                    description='Permanently delete this chat, its managed files, saved history, and unshared attachments and artifacts. Published links from this chat stop working.',
                    preserved=['Independent copies and files saved outside this chat', 'Separately saved global memories, exports, and existing backups'])
                if history is not None:
                    result.update(description='Permanently delete the reviewed sessions and their worker history from both CLI and Unified, along with their unshared saved outputs and attachments. Project working files and CLI configuration remain.', preserved=['Project working files and Git history', 'CLI settings and credentials', 'Other sessions, shared outputs, memories, exports and backups'])
                return {'accepted':True, 'result':result}
            if current != {key:value.get(key) for key in current}:
                raise ValueError('The chat changed since this preview. Review it again before deleting.')
            for identity in current['ids']:
                app.canvas_views.guard_transition('session.delete', {'id': identity})
            # Admission fence takes effect before awaiting worker retirement.
            fenced_ids = set(current['ids'])
            for row in app.state['sessions']:
                if row['id'] in fenced_ids:
                    row['_deleting'] = True
        for identity in current['ids']:
            task = app.warmup.pending.get(identity)
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if app.runtime:
                await app.runtime.stop(identity)
        held = await _acquire(value)
        # A runtime may checkpoint during retirement. Any changed content needs
        # a fresh user preview; stable shared locks keep other hosts out now.
        for item in value['paths']:
            if await asyncio.to_thread(_inventory, item['path']) != item:
                raise ValueError('The chat changed while closing its runtime. Review it again before deleting.')
        if app.operations.pending:
            await asyncio.gather(*list(app.operations.pending), return_exceptions=True)
        async with app.diagnostics.flush_lock:
            app.diagnostics.pending = [item for item in app.diagnostics.pending if item[3] not in current['ids'] and (current.get('preserveWorkspace') or item[4] != current['workspace'])]
        async with app.lock:
            final_revision = app.state['revision']
            final_state, final_clients = copy.deepcopy(app._state), copy.deepcopy(app.clients.records)
            for row in final_state['sessions']:
                row.pop('_deleting', None)
        final_plan = await asyncio.to_thread(_read_plan, app.data_dir, final_state, final_clients, sid, history)
        async with app.lock:
            if app.state['revision'] != final_revision or final_plan != current:
                raise ValueError('The chat or shared files changed during confirmation. Review deletion again.')
            app.db.execute("UPDATE managed_deletions SET phase='confirmed' WHERE id=?", (sid,))
            app.db.commit()  # deletion authorization survives process interruption
            app._deleted_session_ids.update(value['ids'])
            scrub_state(app._state, value)
            for identity, record in list(app.clients.records.items()):
                app.clients.records[identity] = _scrub_local(record, set(value['ids']), set(value['artifactIds']))
                app.clients.dirty.add(identity)
            _cleanup_db(app.db, value)
            app.questions.store.version += 1
            for path in list(app._view_cache):
                if any(Path(path).is_relative_to(item['path']) for item in value['paths']):
                    app._view_cache.pop(path, None)
            app._save_full(reason='Confirmed deletion reconciles catalog, controller stores and references')
            # Confirmed deletion stays durable even if staging is interrupted.
            staging_error = None
            try:
                await _reconcile_resources(app)
                app.db.execute('BEGIN IMMEDIATE')
                try:
                    _stage(value, db=app.db)
                finally:
                    app.db.rollback()
            except (OSError, ValueError) as exc:
                staging_error = str(exc)
            app._publish_changes()
        error = staging_error
        try:
            if not error:
                await asyncio.to_thread(_purge, app.data_dir, value)
        except (OSError, ValueError, sqlite3.Error) as exc:
            error = str(exc)
        async with app.lock:
            if not error:
                app.db.execute("UPDATE managed_deletions SET phase='done', value=? WHERE id=?", (_done_value(value), sid))
                app.db.commit()
            result = {'id':sid, 'deleted':True, 'cleanupPending':bool(error)}
            if error:
                result['warning'] = 'The chat is deleted, but some file cleanup is pending: '+error
            return {'accepted':True, 'result':result, **({'state':app.browser_state()} if include_state else {})}
    except (ValueError, OSError, sqlite3.Error) as exc:
        raise AppError(str(exc), 409) from None
    finally:
        for row in app.state['sessions']:
            if row['id'] in fenced_ids:
                row.pop('_deleting', None)
        _release_all(held, cause=sys.exception())
