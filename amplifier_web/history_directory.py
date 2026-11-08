"""Shared indexed history directory, separate from the admitted working set.

NativeCatalog owns rebuildable native facts; state_records owns local records.
This module joins their scalar projections before selecting any JSON rows. Reads
never start a runtime, acquire a writer, or make eviction a deletion operation.
"""
from __future__ import annotations

import copy
from contextvars import ContextVar
import heapq
import json
import sqlite3
from threading import get_ident
import uuid
from pathlib import Path
from typing import Any

from .performance_diagnostics import count
from .chat_navigation import ChatPageResult, timestamp


COLUMNS = ('id', 'native_id', 'native_project', 'parent_id', 'native_parent_id',
           'session_kind', 'purpose', 'workspace', 'workspace_id', 'managed',
           'available', 'title', 'description', 'created', 'recent', 'ordinal',
           'status', 'agent_created', 'activity_kind', 'activity_label', 'unread',
           'execution', 'preparing', 'loading', 'summary', 'source', 'alias_id', 'hidden_id',
           'notice_count', 'base_activity_kind', 'base_activity_label',
           'history_managed', 'title_source', 'name_source', 'auto_name', 'name_revision', 'name_policy_revision')
FIELDS = {
    'id', 'title', 'titleSource', 'nativeNameSource', 'nativeNameRevision', 'nativeNamePolicyRevision', 'autoName', 'naming',
    'description', 'bundle', 'workspace', 'workspaceId', 'workspaceAvailable',
    'workingDirectory', 'executionRevision', 'executionHost', 'executionHandoffId', 'assignmentId', 'interruptionRevision',
    'createdAt', 'updatedAt', 'recentActivityAt', 'navigationActivityAt',
    'runtimeSessionId', 'nativeIdentity', 'nativeProject', 'parentId', 'nativeParentId',
    'nativeRevision', 'turnCount', 'shared', 'historyManaged', 'historyLoaded',
    'historyLoading', 'sharedHistoryTotal', 'displayMessageCount', 'sessionKind', 'sessionPurpose', 'location', 'nativeAvailable',
    'historyReadOnlyReason', 'status', 'configurationBusy', 'configurationPending', 'fileAccessPending',
    'preparation', 'pendingConfiguration', 'ownership', 'completion', 'manualUnread', 'unreadCompletion',
    'error', 'errorAt', 'approvals', 'questions', 'collaboration', 'coordinationReference',
    '_catalogId', '_catalogRecentAt', '_deleting', '_navigationInput', '_navigationWaiting', '_navigationEndedGeneration',
}
PUBLIC_ID_BATCH = 128
MEMBERSHIP_COLUMNS = ('id', 'native_id', 'native_project', 'parent_id', 'native_parent_id',
                      'session_kind', 'workspace', 'workspace_id', 'history_managed',
                      'alias_id', 'hidden_id')


def _membership(values):
    return tuple(values[COLUMNS.index(key)] for key in MEMBERSHIP_COLUMNS)


def initialize(db):
    from .state_records import initialize as initialize_records
    initialize_records(db)
    columns = ','.join(f'{name} ' + ('REAL' if name in {'created', 'recent'} else
        'INTEGER' if name in {'managed', 'available', 'agent_created', 'unread',
                              'execution', 'preparing', 'loading', 'notice_count', 'history_managed', 'auto_name', 'name_revision', 'name_policy_revision'} else 'TEXT')
        for name in COLUMNS)
    db.execute(f'CREATE TABLE IF NOT EXISTS history_local_rows ({columns}, '
               'startup INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(id))')
    db.execute('CREATE INDEX IF NOT EXISTS history_local_native ON history_local_rows(native_project,native_id)')
    db.execute('CREATE INDEX IF NOT EXISTS history_local_identity ON history_local_rows(native_id)')
    db.execute('CREATE INDEX IF NOT EXISTS history_local_alias ON history_local_rows(alias_id)')
    db.execute('CREATE INDEX IF NOT EXISTS history_local_parent ON history_local_rows(parent_id)')
    db.execute('CREATE INDEX IF NOT EXISTS history_local_native_parent ON history_local_rows(native_parent_id)')
    db.execute('CREATE INDEX IF NOT EXISTS history_local_workspace ON history_local_rows(workspace_id,session_kind,recent)')
    db.execute('CREATE INDEX IF NOT EXISTS history_local_startup ON history_local_rows(startup)')
    db.execute('CREATE INDEX IF NOT EXISTS history_local_notices ON history_local_rows(notice_count)')
    db.execute('CREATE TABLE IF NOT EXISTS history_directory_notices(owner TEXT, slot INTEGER, key TEXT, fingerprint TEXT, '
               'is_error INTEGER, value TEXT, PRIMARY KEY(owner,slot))')
    db.execute('CREATE INDEX IF NOT EXISTS history_notice_owner ON history_directory_notices(owner)')
    db.execute('CREATE TABLE IF NOT EXISTS history_directory_layout(version INTEGER NOT NULL, revision INTEGER NOT NULL)')
    if 'membership_revision' not in {row[1] for row in db.execute('PRAGMA table_info(history_directory_layout)')}:
        # Add a generation, not a second catalog. Existing scalar rows already
        # contain the comparison facts; no historical payload migration is needed.
        db.execute('ALTER TABLE history_directory_layout ADD COLUMN membership_revision INTEGER NOT NULL DEFAULT 0')
    db.execute('CREATE TABLE IF NOT EXISTS history_directory_locations(path TEXT PRIMARY KEY,available INTEGER NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS history_directory_order(id INTEGER PRIMARY KEY CHECK(id=1), next INTEGER NOT NULL)')
    db.execute('INSERT OR IGNORE INTO history_directory_order VALUES(1,0)')
    from .history_directory_observations import initialize as observation_initialize
    observation_initialize(db)
    from .navigation_waiting import initialize as waiting_initialize
    waiting_initialize(db)
    db.execute('CREATE TABLE IF NOT EXISTS history_navigation_tasks(id TEXT PRIMARY KEY,value TEXT NOT NULL)')


def ready(db):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='history_directory_layout'").fetchone()
                and db.execute('SELECT 1 FROM history_directory_layout WHERE version=1').fetchone())


def summary(row):
    """Metadata only; do not touch ColdRecord messages/execution or view bodies."""
    value = copy.deepcopy({key: item for key, item in dict.items(row) if key in FIELDS})
    value['workingDirectory'] = dict.get(row, 'workingDirectory') or dict.get(row, 'workspace')
    value['executionRevision'] = dict.get(row, 'executionRevision', 0)
    value['interruptionRevision'] = dict.get(row, 'interruptionRevision', 0)
    moves = dict.get(row, 'worktreeHandoffs', ())
    handoffs = [item for item in reversed(moves[-16:])
                if item.get('phase') in {'pending', 'unknown'}][:5]
    value['reviewHandoffs'] = {'items': [{key: item.get(key) for key in ('id', 'phase', 'updatedAt')}
                                        for item in handoffs[:4]], 'truncated': len(handoffs) > 4,
                              'historyLimited': len(moves) > 16}
    if dict.get(row, 'historyLoaded', True) is False:
        value['displayMessageCount'] = None
    elif dict.__contains__(row, '_coldMessageCount'):
        value['displayMessageCount'] = dict.__getitem__(row, '_coldMessageCount')
    elif isinstance(dict.get(row, 'messages'), list):
        value['displayMessageCount'] = len(dict.__getitem__(row, 'messages'))
    return value


def notices(row, state):
    from .attention import session_item_count, session_item_at
    read = state.get('attentionRead', {})
    return [(row['id'], slot, item['id'], item['fingerprint'], int(item['id'] == 'session:' + row['id']),
             json.dumps(item, separators=(',', ':'), ensure_ascii=False))
            for slot in range(session_item_count(row)) if (item := session_item_at(row, read, slot)) is not None]


def _scalars(row, state, ordinal, *, dormant_task_active=None, db=None):
    from .chat_navigation import navigation_activity, timestamp
    from .session_identity import native_id, project
    from .session_navigation import is_top_level, is_agent_created
    from .session_activity import activity_flags
    from .managed_chats import is_managed
    from .navigation_summary import activity, task_blocked
    from .attention import reviewed_session_errors
    notice_rows = notices(row, state)
    receipts = state.get('attentionRead', {})
    unread = any(receipts.get(item[2]) != item[3] for item in notice_rows)
    error_reviewed = any(item[4] and receipts.get(item[2]) == item[3] for item in notice_rows)
    from .navigation_waiting import task_for
    task = task_for(state, row)
    base = activity(row, False, error_reviewed=False, blocked=task_blocked(state, row['id']), task=task, db=db)
    facts = (base if not unread and not error_reviewed else
        activity(row, unread, error_reviewed=error_reviewed, blocked=task_blocked(state, row['id']), task=task, db=db))
    flags = activity_flags(row)
    value = summary(row)
    from .navigation_waiting import input_boundary
    value['_navigationInput'] = input_boundary(row)
    value['_navigationWaiting'] = base.get('waiting')
    task = row.get('task') or state.get('runtimeControl', {}).get(row['id'], {}).get('task.get', {}).get('task') or {}
    # First directory migration visits every durable owner; subsequent keyed
    # saves refresh this bounded fact from its exact task authority. No task
    # body is duplicated and this fact never authorizes execution.
    value['_coordinationTaskActive'] = (bool(dormant_task_active) if dormant_task_active is not None else
        bool(task.get('id') and task.get('status') in {'active', 'running', 'working'}))
    kind = 'internal' if row.get('sessionKind') == 'internal' else 'root' if is_top_level(row) else 'worker'
    values = (row['id'], native_id(row), project(row), row.get('parentId'), row.get('nativeParentId'),
        kind, row.get('sessionPurpose'), row.get('workspace'), row.get('workspaceId'), int(is_managed(row)),
        int(row.get('workspaceAvailable', True) is not False), row.get('title') or 'Untitled conversation',
        row.get('description') or '', timestamp(row.get('createdAt')), navigation_activity(row), str(ordinal),
        row.get('status', 'idle'), int(is_agent_created(row)), facts['kind'], facts['label'], int(unread),
        int(flags['execution']), int(flags['preparing']), int(flags['loading']),
        json.dumps(value, separators=(',', ':'), ensure_ascii=False), 'local',
        uuid.uuid5(uuid.NAMESPACE_URL, f'amplifier-session:{project(row)}/{native_id(row)}').hex,
        uuid.uuid5(uuid.NAMESPACE_URL, f'amplifier-native:{project(row)}/{native_id(row)}').hex,
        len(notice_rows), base['kind'], base['label'], int(bool(row.get('historyManaged'))),
        row.get('titleSource'), row.get('nativeNameSource'), row.get('autoName'),
        row.get('nativeNameRevision', 0), row.get('nativeNamePolicyRevision', 0))
    generation = row.get('collaborationGeneration') or {}
    # Prior-host authority must be retired before passive attention queries can
    # expose it. Admit only pending owners, not the dormant conversation library.
    collaboration_pending = bool(generation and not generation.get('terminal')) or any(
        item.get('id', '').startswith('collaboration:') and item.get('status') == 'pending'
        for item in row.get('approvals', []))
    pending = bool(flags['execution'] or flags['preparing'] or collaboration_pending or row.get('configurationPending')
                   or row.get('status') in {'ready', 'queued', 'pending'}
                   or row.get('pendingConfiguration', {}).get('phase') in {'queued', 'applying'}
                   or row.get('naming', {}).get('status') == 'working'
                   or row.get('bundleChange', {}).get('phase') == 'working')
    return values, int(pending)


def _child_candidates(parent, namespace):
    """Indexed source candidates; canonical child membership is checked later."""
    aliases = sorted({parent.get(key) for key in ('id', 'nativeIdentity', 'runtimeSessionId')} - {None})
    prefix = '''child_input AS MATERIALIZED (SELECT json_extract(value,'$.id') id,
        json_extract(value,'$.project') project,json_extract(value,'$.workspace') workspace,
        json_extract(value,'$.aliases') aliases FROM (SELECT ? value)),'''
    params = [json.dumps({'id': parent.get('id'), 'project': parent.get('nativeProject'),
                         'workspace': parent.get('workspace'), 'aliases': aliases}, separators=(',', ':'))]
    direct = 'parent_id=(SELECT id FROM child_input)'
    alias = 'native_parent_id IN (SELECT value FROM json_each((SELECT aliases FROM child_input)))'
    live_lookup = f'{direct} OR {alias}'
    owners = ''
    if namespace is not None:
        # Parent app aliases can be outside the requested child page, and the
        # passed metadata can lag the current native identity. Probe those
        # exact parent owners, rather than scanning all parent mappings.
        prefix += '''child_parent_keys AS (
            SELECT native_project project,native_id identity FROM live_rows WHERE id=(SELECT id FROM child_input) UNION
            SELECT native_project,native_id FROM history_local_rows WHERE id=(SELECT id FROM child_input)
                UNION
            SELECT project,identity FROM history_native.native_rows WHERE namespace=? AND identity=(SELECT id FROM child_input) UNION
            SELECT project,identity FROM history_native.native_rows INDEXED BY native_rows_public
                WHERE namespace=? AND public_id=(SELECT id FROM child_input)),'''
        params += [namespace, namespace]
        scope = ('project=(SELECT project FROM child_input)' if parent.get('nativeProject') is not None
                 else 'workspace_path=(SELECT workspace FROM child_input)')
        prefix += f'''native_candidates AS MATERIALIZED (
            SELECT rowid,project,identity FROM history_native.native_rows
                WHERE namespace=? AND parent_id=(SELECT id FROM child_input) UNION
            SELECT n.rowid,n.project,n.identity FROM child_parent_keys p CROSS JOIN history_native.native_rows n
                WHERE n.namespace=? AND n.project=p.project AND n.parent_id=p.identity UNION
            SELECT rowid,project,identity FROM history_native.native_rows
                WHERE namespace=? AND {scope} AND parent_id IN (SELECT value FROM json_each((SELECT aliases FROM child_input)))),'''
        params += [namespace, namespace, namespace]
        live_lookup += ' OR EXISTS(SELECT 1 FROM native_candidates n WHERE n.project=native_project AND n.identity=native_id)'
        owners = ''' UNION SELECT l.rowid FROM native_candidates n
            CROSS JOIN history_local_rows l INDEXED BY history_local_native
            WHERE l.native_project=n.project AND l.native_id=n.identity'''
    prefix += f'''local_candidates AS (SELECT rowid FROM history_local_rows WHERE {direct} UNION
        SELECT rowid FROM history_local_rows WHERE {alias}{owners}),'''
    return prefix, params, live_lookup, 'l.rowid IN (SELECT rowid FROM local_candidates)'


class HistoryDirectory:
    def __init__(self, service, *, db=None, snapshot=False):
        self.service = service
        self.db = db or service.db
        self.snapshot = snapshot
        self._native_namespace = None
        self._native_path = None
        self._closed = False
        self._frozen_state = None
        if not snapshot:
            initialize(self.db)
        self._live_values = {}
        self._live_notices = []
        self._live_revision = 0
        self._encoded_overlay = None
        self._native_revision = None
        self._read_frame = ContextVar('directory_read_frame', default=None)

    def read_frame(self, project, *args, **kwargs):
        """Share the live scalar overlay during one synchronous projection.

        This is a call boundary, not a revision cache or an async context. Even
        an awaitable returned by project leaves the frame before it can yield.
        The next call observes in-place changes afresh. Native/durable query
        fences still run normally; only duplicate live-row preparation is saved.
        Projection callbacks must not mutate existing rows. Admission/list and
        durable-write boundaries are detected; explicit hydrating reads do not
        use this scope.
        """
        current = self._read_frame.get()
        if current is not None and current['active'] and current['thread'] == get_ident():
            return project(*args, **kwargs)
        frame = {'thread': get_ident(), 'owner': None, 'active': True}
        token = self._read_frame.set(frame)
        try:
            return project(*args, **kwargs)
        finally:
            # Tasks created by projection inherit ContextVars. Retire the
            # shared frame itself before they can run, not only our token.
            frame['active'] = False
            frame['owner'] = None
            self._read_frame.reset(token)

    def bind(self, catalog):
        if catalog is None:
            return
        path = str(catalog.path.resolve())
        if self._native_path == path and self._native_namespace == catalog.namespace:
            return
        if self._native_path:
            self.db.execute('DETACH DATABASE history_native')
        self.db.execute('ATTACH DATABASE ? AS history_native', (path,))
        self._native_path, self._native_namespace = path, catalog.namespace

    @property
    def revision(self):
        row = self.db.execute('SELECT revision FROM history_directory_layout WHERE version=1').fetchone()
        native = tuple(sorted(self._native_revision.items())) if isinstance(self._native_revision, dict) else self._native_revision
        return (native, row[0] if row else 0, self._live_revision)

    @property
    def observation_revision(self):
        from .history_directory_observations import revision
        return revision(self, self._frozen_state if self.snapshot else self.service._state)

    @property
    def navigation_revision(self):
        return self.observation_revision['navigation']

    @property
    def root_navigation_revision(self):
        return self.observation_revision['root_navigation']

    @property
    def workspace_revision(self):
        return self.observation_revision['workspace']

    def changed(self, revision=None):
        self._native_revision = revision

    def native_backing(self, row):
        """A cached native owner can replace a pristine local metadata reference.

        Missing/unavailable cache is uncertainty, never authority to discard the
        last queryable saved reference. Canonical resume still checks its files.
        """
        if not self._native_path:
            return False
        from .session_identity import native_id, project
        try:
            return self.db.execute('SELECT 1 FROM history_native.native_rows WHERE namespace=? AND project=? AND identity=?',
                (self._native_namespace, project(row), native_id(row))).fetchone() is not None
        except sqlite3.Error:
            return False

    def native_counts(self, identities):
        if not self._native_path:
            return {}
        return dict(self.db.execute('SELECT identity,count(*) FROM history_native.native_rows WHERE namespace=? '
            'AND identity IN (SELECT value FROM json_each(?)) GROUP BY identity',
            (self._native_namespace, json.dumps(sorted(set(identities))))))

    def sync_locations(self, managed, examined):
        """Persist bounded workspace-marker observations, never probe on query."""
        changed = False
        for path in set(examined) - {None}:
            previous = self.db.execute('SELECT available FROM history_directory_locations WHERE path=?', (path,)).fetchone()
            available = int(managed[path]) if path in managed else None
            if previous == ((available,) if available is not None else None):
                continue
            if available is None:
                self.db.execute('DELETE FROM history_directory_locations WHERE path=?', (path,))
            else:
                self.db.execute('INSERT OR REPLACE INTO history_directory_locations VALUES(?,?)', (path, available))
            changed = True
        if changed:
            self.db.execute('UPDATE history_directory_layout SET revision=revision+1 WHERE version=1')
        return changed

    def naming_conflicts(self, rows, state):
        """Changed legacy name writes may reset revisions; re-read only conflicts."""
        residents = {(row.get('nativeProject'), row.get('nativeIdentity') or row.get('runtimeSessionId') or row['id']): row
                     for row in state.get('sessions', ())}
        pending = []
        for native in rows:
            key = (native['nativeProject'], native['nativeIdentity'])
            current = residents.get(key)
            observations = [(current.get('nativeNameRevision', 0), current.get('nativeNamePolicyRevision', 0), current.get('title'))] if current is not None else []
            observations.extend(self.db.execute('SELECT name_revision,name_policy_revision,title FROM history_local_rows WHERE native_project=? AND native_id=?', key))
            expected = {tuple(value) for value in observations if native.get('nameRevision', 0) < value[0]
                        or native.get('namePolicyRevision', 0) < value[1]}
            if expected:
                pending.append((native, expected))
        return pending

    @staticmethod
    def verify_naming_conflicts(pending):
        """Detached, point-only canonical reads; never trust an old cache alone."""
        from amplifier_foundation.session.metadata import SessionMetadataStore
        from .automatic_history import directory
        verified = {}
        for native, expected in pending:
            try:
                metadata = SessionMetadataStore(directory(native)).read()
                name = metadata.get('name') or metadata.get('title') or metadata.get('agent_name')
                if (name == native.get('name') and metadata.get('name_revision', 0) == native.get('nameRevision', 0)
                        and metadata.get('name_policy_revision', 0) == native.get('namePolicyRevision', 0)):
                    verified[(native['nativeProject'], native['nativeIdentity'])] = expected
            except (OSError, ValueError):
                pass  # Unknown provenance cannot replace a newer observed name.
        return verified

    def refresh_native(self, rows, removed, state, *, naming_proofs=None):
        """Refresh dormant overlays; admitted owners merge through AutomaticHistory."""
        changed = []
        admitted = {row['id'] for row in state.get('sessions', ())}
        for native in rows:
            for identity, raw in self.db.execute('SELECT id,summary FROM history_local_rows WHERE native_project=? AND native_id=?',
                                                (native['nativeProject'], native['nativeIdentity'])):
                if identity in admitted:
                    # The live merge owns availability, parent aliases and frozen
                    # busy-chat navigation activity. Writing a detached variant
                    # first makes its later save look like a second real change.
                    continue
                row = json.loads(raw)
                confirmed = (row.get('nativeNameRevision', 0), row.get('nativeNamePolicyRevision', 0), row.get('title')) in (naming_proofs or {}).get((native['nativeProject'], native['nativeIdentity']), ())
                name_current = confirmed or native.get('nameRevision', 0) >= row.get('nativeNameRevision', 0)
                policy_current = confirmed or name_current and native.get('namePolicyRevision', 0) >= row.get('nativeNamePolicyRevision', 0)
                row.update(nativeProject=native['nativeProject'], nativeIdentity=native['nativeIdentity'],
                           nativeAvailable=True, workspaceId=native.get('workspaceId'))
                if name_current:
                    row.update(nativeNameSource=native.get('nameSource'), nativeNameRevision=native.get('nameRevision', 0))
                if policy_current:
                    row.update(autoName=native.get('autoName', native.get('nameSource') != 'manual'),
                               nativeNamePolicyRevision=native.get('namePolicyRevision', 0))
                if name_current and (native.get('name') or row.get('historyManaged') and row.get('titleSource') != 'manual'):
                    row['title'] = native.get('name') or native.get('title') or row.get('title', '')
                if row.get('historyManaged'):
                    row.update(workspace=native.get('workspace'),
                        **({'description': native.get('description', '')} if name_current else {}),
                        createdAt=native.get('createdAt'), updatedAt=native.get('updatedAt'),
                        sessionKind=native.get('sessionKind', 'root'), sessionPurpose=native.get('sessionPurpose'),
                        nativeParentId=native.get('parentId'), parentId=native.get('parentId'),
                        historyReadOnlyReason=native.get('readOnlyReason'))
                    row['navigationActivityAt'] = row['recentActivityAt'] = max(
                        row.get('recentActivityAt', 0), native.get('recentActivityAt', 0))
                    if not row.get('historyLoaded') or 'nativeRevision' not in row:
                        row['nativeRevision'] = native.get('transcriptRevision')
                changed.append(row)
        for project, identity in removed:
            for raw, in self.db.execute('SELECT summary FROM history_local_rows WHERE native_project=? AND native_id=?', (project, identity)):
                row = json.loads(raw)
                if row.get('historyManaged'):
                    row.update(nativeAvailable=False, historyReadOnlyReason='The canonical saved chat is no longer available. Restore its files before continuing.')
                    changed.append(row)
        self.sync(changed, state)

    def sync(self, rows, state, *, deleted=()):
        """Transactionally project only committed/admitted owners; never prune absences."""
        changed = False
        membership_changed = False
        resident_ids = {item['id'] for item in state.get('sessions', ())}
        for position, row in enumerate(rows):
            prior = self.db.execute("SELECT ordinal, json_extract(summary, '$._coordinationTaskActive') FROM history_local_rows WHERE id=?", (row['id'],)).fetchone()
            if prior:
                ordinal = prior[0]
            else:
                sequence = self.db.execute('SELECT next FROM history_directory_order WHERE id=1').fetchone()[0]
                ordinal = f'{sequence:020d}'
                self.db.execute('UPDATE history_directory_order SET next=next+1 WHERE id=1')
            scalar_state = state
            dormant_task_active = None
            if row['id'] in state.get('runtimeControl', {}) or 'task' in row:
                from .navigation_waiting import save_task, task_for
                save_task(self.db, row['id'], task_for(state, row))
            if row['id'] not in state.get('runtimeControl', {}):
                # Metadata-only canonical updates must preserve a dormant task's
                # saved admission/attention facts without loading its runtime/config body.
                from .navigation_waiting import saved_task
                task = saved_task(self.db, row['id'])
                if task.get('status') is not None:
                    scalar_state = {**state, 'runtimeControl': {row['id']: {'task.get': {'task': {
                        **task}}}}}
                elif prior and row['id'] not in resident_ids and 'task' not in row:
                    # Metadata does not carry legacy session.task. Preserve its
                    # bounded saved fact until an admitted owner saves authority.
                    dormant_task_active = prior[1]
            values, startup = _scalars(row, scalar_state, ordinal, dormant_task_active=dormant_task_active, db=self.db)
            current = self.db.execute('SELECT ' + ','.join(COLUMNS) + ',startup FROM history_local_rows WHERE id=?', (row['id'],)).fetchone()
            if current == (*values, startup):
                continue
            membership_changed |= current is None or _membership(current) != _membership(values)
            marks = ','.join('?' for _ in range(len(COLUMNS) + 1))
            from .history_directory_observations import sync as observation_sync
            observation_sync(self, row, state=state)
            self.db.execute(f'INSERT OR REPLACE INTO history_local_rows VALUES({marks})', (*values, startup))
            self.db.execute('DELETE FROM history_directory_notices WHERE owner=?', (row['id'],))
            self.db.executemany('INSERT INTO history_directory_notices VALUES(?,?,?,?,?,?)', notices(row, state))
            changed = True
        for identity in deleted:
            self.db.execute('DELETE FROM history_navigation_tasks WHERE id=?', (identity,))
            from .history_directory_observations import sync as observation_sync
            observation_sync(self, identity, state=state, deleted=True)
            removed = bool(self.db.execute('DELETE FROM history_local_rows WHERE id=?', (identity,)).rowcount)
            changed |= removed
            membership_changed |= removed
            self.db.execute('DELETE FROM history_directory_notices WHERE owner=?', (identity,))
        if changed:
            self.db.execute('UPDATE history_directory_layout SET revision=revision+1, '
                            'membership_revision=membership_revision+? WHERE version=1',
                            (int(membership_changed),))

    def finish_migration(self):
        if not ready(self.db):
            self.db.execute('INSERT INTO history_directory_layout(version,revision) VALUES(1,1)')

    def _sync_live(self, state):
        if self.snapshot:
            return
        frame = self._read_frame.get()
        owner = (state.get('sessions'), state.get('attentionRead'), state.get('runtimeControl'))
        boundary = (len(owner[0] or ()), self.db.total_changes)
        if frame is not None and frame['active'] and frame['thread'] == get_ident():
            prior = frame['owner']
            if (prior is not None and prior[1] == boundary
                    and all(a is b for a, b in zip(prior[0], owner))):
                return
            frame['owner'] = None
        else:
            frame = None
        values = {}
        sequence = self.db.execute('SELECT next FROM history_directory_order WHERE id=1').fetchone()[0]
        for row in state.get('sessions', ()):
            prior = self.db.execute('SELECT ordinal FROM history_local_rows WHERE id=?', (row['id'],)).fetchone()
            ordinal = prior[0] if prior else f'{sequence:020d}'
            if prior is None:
                sequence += 1
            record, startup = _scalars(row, state, ordinal, db=self.db)
            values[row['id']] = (*record, startup)
        if values != self._live_values:
            self._live_values = values
            self._live_notices = [notice for row in state.get('sessions', ()) for notice in notices(row, state)]
            self._live_revision += 1
        if frame is not None:
            frame['owner'] = (owner, boundary)

    def _overlay_parameters(self, *, include_summary=True):
        """Encode only a changed admitted overlay, never history or query fences.

        Live preparation replaces both containers on change; snapshot readers
        also install their own frozen containers. Their scalar rows and notice
        tuples are never edited after installation. Retain at most one full and
        one body-free row encoding plus notices, not per-page results or mutable
        application state. Read receipts and native/durable generations remain
        current on every query.
        """
        prior = self._encoded_overlay
        if prior is None or prior[0] is not self._live_values or prior[1] is not self._live_notices:
            prior = (self._live_values, self._live_notices, {},
                     json.dumps(self._live_notices, separators=(',', ':')))
            self._encoded_overlay = prior
        encoded = prior[2]
        if include_summary not in encoded:
            rows = list(self._live_values.values())
            if not include_summary:
                # Scalar predicates never inspect summary. Keep column offsets
                # stable without binding/parsing unrelated retained bodies for
                # every json_extract in the materialized live-row projection.
                position = COLUMNS.index('summary')
                rows = [(*row[:position], None, *row[position + 1:]) for row in rows]
            encoded[include_summary] = json.dumps(rows, separators=(',', ':'))
        return encoded[include_summary], prior[3]

    def effective(self, state=None, *, identity=None, public_ids=None, children_of=None, native_root_workspace=None, exact_parent=None, fields=None, notice_values=False, local_only=False):
        """Return effective rows with optional alias, exact-ID or child scope.

        exact_parent uses the child candidate indexes but preserves the agent
        overview's exact effective parent equality, including self-parent rows.
        fields limits the final UNION projection, after retaining every scalar
        needed by its canonical membership/suppression predicates.
        notice_values exposes existing item payloads only to the attention
        reader; ordinary catalog/count queries retain scalar notice columns.
        local_only retains canonical native metadata and suppression for local
        owners, but excludes native-only rows. Notices belong to local owners;
        joining them must not materialize unrelated native catalog rows first.
        """
        if sum(value is not None for value in (identity, public_ids, children_of, exact_parent)) > 1:
            raise ValueError('Use one directory source scope per query.')
        requested_fields = set(COLUMNS if fields is None else fields)
        if not requested_fields or not requested_fields.issubset(COLUMNS):
            raise ValueError('Use known directory fields for a scalar projection.')
        required_fields = set(requested_fields)
        if public_ids is not None and len(public_ids) > PUBLIC_ID_BATCH:
            raise ValueError('Public-ID candidates require one bounded batch.')
        state = self._frozen_state if self.snapshot else state if state is not None else self.service._state
        self._sync_live(state)
        columns = ','.join(COLUMNS)
        # A VALUES projection avoids taking a writer transaction during a read.
        # Only the bounded admitted working set is encoded in this parameter.
        live_columns = ','.join(f"json_extract(value,'$[{index}]') AS {name}" for index, name in enumerate(COLUMNS))
        lookup = "(id=(SELECT requested FROM lookup_input) OR native_id=(SELECT requested FROM lookup_input) OR alias_id=(SELECT requested FROM lookup_input))"
        prefix = "lookup_input AS (SELECT ? AS requested)," if identity is not None else ''
        scope_params = [identity] if identity is not None else []
        live_lookup = stored_lookup = lookup if identity is not None else None
        if public_ids is not None:
            # Candidate pruning is distinct from alias resolution. Exact
            # public IDs are matched only after canonical overlays below.
            prefix = 'requested_ids AS (SELECT value id FROM json_each(?)),'
            scope_params = [json.dumps(public_ids, separators=(',', ':'))]
            live_lookup = 'id IN (SELECT id FROM requested_ids)'
            owners = ''
            if self._native_path:
                prefix += '''native_candidates AS MATERIALIZED (
                    SELECT rowid,project,identity FROM history_native.native_rows
                    WHERE namespace=? AND identity IN (SELECT id FROM requested_ids) UNION
                    SELECT rowid,project,identity FROM history_native.native_rows
                    WHERE namespace=? AND public_id IN (SELECT id FROM requested_ids)),'''
                scope_params += [self._native_namespace, self._native_namespace]
                # Include owners even when their app ID differs from a requested
                # native alias. Otherwise filtering locals first resurrects the
                # suppressed native row. All live rows still shadow saved IDs.
                live_lookup += ' OR EXISTS(SELECT 1 FROM native_candidates n WHERE n.project=native_project AND n.identity=native_id)'
                owners = ''' UNION SELECT l.rowid FROM native_candidates n
                    CROSS JOIN history_local_rows l INDEXED BY history_local_native
                    WHERE l.native_project=n.project AND l.native_id=n.identity'''
            prefix += f'''local_candidates AS (SELECT rowid FROM history_local_rows
                WHERE id IN (SELECT id FROM requested_ids){owners}),'''
            stored_lookup = 'l.rowid IN (SELECT rowid FROM local_candidates)'
        elif children_of is not None or exact_parent is not None:
            parent = children_of if children_of is not None else {'id': exact_parent}
            prefix, scope_params, live_lookup, stored_lookup = _child_candidates(parent, self._native_namespace)
        scoped = any(value is not None for value in (identity, public_ids, children_of, exact_parent))
        if scoped and native_root_workspace is not None:
            raise ValueError('Workspace candidates cannot replace an exact source scope.')
        # Extract the admitted scalar overlay once per statement. Inlining it
        # makes native ownership/parent lookups parse each live row's summary
        # again for every catalog row, even when the query only needs counts.
        # Materialize this query's receipts so SQLite can index their keys;
        # joining json_each directly rescans every receipt for every notice.
        local = prefix + f'''live_rows AS MATERIALIZED (SELECT {live_columns} FROM json_each(?)), stored_local_rows AS (
            SELECT {columns} FROM live_rows {'WHERE ' + live_lookup if scoped else ''} UNION ALL
            SELECT {columns} FROM history_local_rows l
            WHERE {'(' + stored_lookup + ') AND ' if scoped else ''}l.id NOT IN (SELECT id FROM live_rows)),
            directory_notices AS (
                SELECT json_extract(value,'$[0]') owner,json_extract(value,'$[2]') key,
                       json_extract(value,'$[3]') fingerprint,json_extract(value,'$[4]') is_error
                       {",json_extract(value,'$[1]') slot,json_extract(value,'$[5]') value" if notice_values else ''}
                FROM json_each(?) UNION ALL
                SELECT owner,key,fingerprint,is_error{',slot,value' if notice_values else ''} FROM history_directory_notices n
                WHERE {'n.owner IN (SELECT id FROM stored_local_rows) AND ' if scoped else ''}n.owner NOT IN (SELECT id FROM live_rows)),
            read_receipts AS MATERIALIZED (SELECT key,value FROM json_each(?)),
            notice_status AS (SELECT owner,MAX(COALESCE(r.value != n.fingerprint,1)) AS unread,
                MAX(n.is_error AND r.value=n.fingerprint) AS error_reviewed
                FROM directory_notices n LEFT JOIN read_receipts r ON r.key=n.key GROUP BY owner)'''
        if self._native_path:
            parent = '''COALESCE((SELECT p.id FROM live_rows p WHERE p.native_project=n.project AND p.native_id=n.parent_id LIMIT 1),
                (SELECT p.id FROM history_local_rows p WHERE p.native_project=n.project AND p.native_id=n.parent_id LIMIT 1),
                (SELECT CASE WHEN EXISTS(SELECT 1 FROM history_native.native_rows d WHERE d.namespace=p.namespace
                    AND d.identity=p.identity AND d.project!=p.project) THEN p.public_id ELSE p.identity END
                 FROM history_native.native_rows p WHERE p.namespace=n.namespace AND p.project=n.project AND p.identity=n.parent_id),n.parent_id)'''
            canonical = []
            historic = 'n.identity IS NOT NULL AND l.history_managed'
            name_current = 'n.identity IS NOT NULL AND n.name_revision>=l.name_revision'
            policy_current = name_current + ' AND n.name_policy_revision>=l.name_policy_revision'
            named = name_current + " AND (n.has_name OR (l.history_managed AND COALESCE(l.title_source,'')!='manual'))"
            canonical_fields = dict(workspace='workspace_path', workspace_id='workspace_id', description='description',
                created='created', parent_id='parent_id', native_parent_id='parent_id', session_kind='kind', purpose='purpose')
            for name in COLUMNS:
                expression = (
                    f'CASE WHEN {named} THEN n.title ELSE l.title END' if name == 'title' else
                    f'CASE WHEN {historic} AND {name_current} THEN n.description ELSE l.description END' if name == 'description' else
                    f"CASE WHEN n.identity IS NOT NULL THEN COALESCE((SELECT available FROM history_directory_locations p WHERE p.path=n.workspace_path),n.available) ELSE l.available END" if name == 'available' else
                    f'CASE WHEN {historic} THEN {parent} ELSE l.parent_id END' if name == 'parent_id' else
                    f"CASE WHEN {historic} AND l.status NOT IN ('working','starting','stopping','ready') THEN MAX(l.recent,n.recent) ELSE l.recent END" if name == 'recent' else
                    f'CASE WHEN {historic} THEN n.{canonical_fields[name]} ELSE l.{name} END' if name in canonical_fields else
                    f'CASE WHEN {name_current} THEN n.name_source ELSE l.name_source END' if name == 'name_source' else
                    f'CASE WHEN {policy_current} THEN n.auto_name ELSE l.auto_name END' if name == 'auto_name' else
                    f'CASE WHEN {name_current} THEN n.name_revision ELSE l.name_revision END' if name == 'name_revision' else
                    f'CASE WHEN {policy_current} THEN n.name_policy_revision ELSE l.name_policy_revision END' if name == 'name_policy_revision' else
                    f'l.{name}')
                canonical.append(expression + ' AS ' + name)
            local += ', raw_local_rows AS (SELECT ' + ','.join(canonical) + ''' FROM stored_local_rows l
                LEFT JOIN history_native.native_rows n ON n.namespace=? AND n.project=l.native_project AND n.identity=l.native_id)'''
        else:
            local += ', raw_local_rows AS (SELECT * FROM stored_local_rows)'
        expressions = []
        quiet = "(l.base_activity_kind IN ('idle','unread') OR (l.base_activity_label='Needs attention' AND ns.error_reviewed))"
        for name in COLUMNS:
            expression = ("COALESCE(ns.unread,0)" if name == 'unread' else
                f"CASE WHEN {quiet} THEN CASE WHEN ns.unread THEN 'unread' ELSE 'idle' END ELSE l.base_activity_kind END" if name == 'activity_kind' else
                f"CASE WHEN {quiet} THEN CASE WHEN ns.unread THEN 'New response' ELSE 'Idle' END ELSE l.base_activity_label END" if name == 'activity_label' else f'l.{name}')
            expressions.append(expression + ' AS ' + name)
        local += ', local_rows AS (SELECT ' + ','.join(expressions) + ' FROM raw_local_rows l LEFT JOIN notice_status ns ON ns.owner=l.id)'
        parameters = scope_params + [*self._overlay_parameters(include_summary='summary' in requested_fields),
                      json.dumps(state.get('attentionRead', {}), separators=(',', ':'))]
        if self._native_path:
            parameters.append(self._native_namespace)
        if self._native_path and not local_only:
            native = '''native_rows AS (
                SELECT CASE WHEN EXISTS(SELECT 1 FROM history_native.native_rows d
                    WHERE d.namespace=n.namespace AND d.identity=n.identity AND d.project!=n.project)
                    THEN n.public_id ELSE n.identity END AS id,
                    n.identity AS native_id,n.project AS native_project,n.parent_id,n.parent_id AS native_parent_id,
                    n.kind AS session_kind,n.purpose,n.workspace_path AS workspace,n.workspace_id,
                    EXISTS(SELECT 1 FROM history_directory_locations p WHERE p.path=n.workspace_path) AS managed,
                    COALESCE((SELECT available FROM history_directory_locations p WHERE p.path=n.workspace_path),n.available) AS available,n.title,n.description,n.created,n.recent,
                    n.ordinal,'idle' AS status,0 AS agent_created,'idle' AS activity_kind,
                    'Idle' AS activity_label,0 AS unread,0 AS execution,0 AS preparing,0 AS loading,
                    n.value AS summary,'native' AS source,n.public_id AS alias_id,n.hidden_id,
                    0 AS notice_count,'idle' AS base_activity_kind,'Idle' AS base_activity_label,
                    1 AS history_managed,'native' AS title_source,n.name_source,n.auto_name,n.name_revision,n.name_policy_revision
                FROM history_native.native_rows n WHERE n.namespace=?
                AND (n.project IS NULL OR n.identity IS NULL OR json_array(n.project,n.identity) NOT IN (
                    SELECT json_array(CAST(native_project AS TEXT),CAST(native_id AS TEXT)) FROM stored_local_rows
                    WHERE native_project IS NOT NULL AND native_id IS NOT NULL))
            )'''
            # Ownership keys are unchanged by canonical metadata/notice joins.
            # Build their set once: older SQLite planners otherwise scan the
            # expanded local overlay separately for every native row. Use an
            # unambiguous scalar key: tuple NOT IN still scans for NULL matches
            # on every miss. Only the two identity scalars are encoded here.
            # Incomplete local keys cannot suppress unrelated native history.
            native = native.replace('n.project AS native_project,n.parent_id,', 'n.project AS native_project,' + parent + ' AS parent_id,')
            parameters.append(self._native_namespace)
            if identity is not None:
                native = native.replace('FROM history_native.native_rows n WHERE n.namespace=?',
                    'FROM history_native.native_rows n NOT INDEXED WHERE n.namespace=? AND n.rowid IN ('
                    'SELECT rowid FROM history_native.native_rows WHERE namespace=? AND identity=? UNION '
                    'SELECT rowid FROM history_native.native_rows WHERE namespace=? AND public_id=?)')
                parameters.extend((self._native_namespace, identity, self._native_namespace, identity))
            elif native_root_workspace is not None:
                # Prune only native candidates. All local owners still shadow
                # native identities before final canonical workspace filtering.
                # Registered IDs win over paths; only unknown IDs use a path.
                workspace_id, fallback_paths = native_root_workspace
                native = native.replace('FROM history_native.native_rows n WHERE n.namespace=?',
                    "FROM history_native.native_rows n NOT INDEXED WHERE n.namespace=? AND n.rowid IN ("
                    "SELECT rowid FROM history_native.native_rows WHERE namespace=? AND workspace_id=? AND kind='root' UNION "
                    "SELECT rowid FROM history_native.native_rows WHERE namespace=? AND workspace_path IN "
                    "(SELECT value FROM json_each(?)) AND kind='root' AND NOT directory_known_workspace(workspace_id))")
                parameters.extend((self._native_namespace, workspace_id, self._native_namespace,
                                   json.dumps(fallback_paths, separators=(',', ':'))))
            elif public_ids is not None or children_of is not None or exact_parent is not None:
                native = native.replace('FROM history_native.native_rows n WHERE n.namespace=?',
                    'FROM history_native.native_rows n NOT INDEXED WHERE n.namespace=? '
                    'AND n.rowid IN (SELECT rowid FROM native_candidates)')
        else:
            native = f'native_rows AS (SELECT {columns} FROM local_rows WHERE 0)'
        # Suppression is keyed, independent of residency. Receipts remain the
        # deletion authority; hiding a directory row never deletes a record.
        hidden = state.get('hiddenNativeSessions', [])
        hidden_workspaces = state.get('hiddenNativeWorkspaces', [])
        from .managed_deletion import CatalogSuppression
        deleted = CatalogSuppression(self.db)
        filters = ['r.id IN (SELECT id FROM requested_ids)'] if public_ids is not None else []
        if public_ids is not None:
            required_fields.add('id')
        if children_of is not None:
            required_fields.update(('session_kind', 'id', 'parent_id', 'native_parent_id', 'native_project', 'workspace'))
            # This is the same effective-parent predicate used by child pages:
            # explicit app parents win, otherwise native aliases stay scoped.
            filters.append("""r.session_kind!='root' AND r.id!=(SELECT id FROM child_input) AND (
                r.parent_id=(SELECT id FROM child_input) OR
                (r.native_parent_id IN (SELECT value FROM json_each((SELECT aliases FROM child_input))) AND
                 CASE WHEN r.native_project IS NOT NULL AND (SELECT project FROM child_input) IS NOT NULL
                    THEN r.native_project=(SELECT project FROM child_input)
                    ELSE r.workspace=(SELECT workspace FROM child_input) END))""")
        if exact_parent is not None:
            required_fields.update(('session_kind', 'parent_id'))
            filters.append("r.session_kind!='root' AND r.parent_id IS (SELECT id FROM child_input)")
        for column, values in (('hidden_id', hidden), ('workspace_id', hidden_workspaces),
                               ('native_project', sorted(deleted.projects)),
                               ('id', sorted(deleted.ids))):
            if values:
                required_fields.add(column)
                filters.append(f"COALESCE(r.{column},'') NOT IN (SELECT value FROM json_each(?))")
                parameters.append(json.dumps(values))
        if deleted.native:
            required_fields.update(('native_project', 'native_id', 'id'))
            native_keys = json.dumps(deleted.keys(deleted.native))
            for identity in ('native_id', 'id'):
                filters.append(f"COALESCE(r.native_project,'')||char(0)||COALESCE(r.{identity},'') NOT IN (SELECT value FROM json_each(?))")
                parameters.append(native_keys)
        selected_columns = ','.join(name for name in COLUMNS if name in required_fields)
        output_columns = ','.join(name for name in COLUMNS if name in requested_fields)
        return (f'''WITH {local},{native}, all_directory_rows AS (
            SELECT {selected_columns} FROM local_rows UNION ALL SELECT {selected_columns} FROM native_rows),
            directory_rows AS (SELECT {output_columns} FROM all_directory_rows r WHERE {' AND '.join(filters) or '1'})''', parameters)

    def decode(self, sql_row):
        from .native_catalog import normalize_availability
        values = dict(sql_row) if isinstance(sql_row, (dict, sqlite3.Row)) else dict(zip(COLUMNS, sql_row))
        raw = json.loads(values['summary'])
        count(self.service, 'directory.rows.decoded')
        if values['source'] == 'native':
            raw = normalize_availability(raw, values['available'])
            row = {'id': values['id'], 'titleSource': 'native', 'nativeNameSource': raw.get('nameSource'),
                'autoName': raw.get('autoName', raw.get('nameSource') != 'manual'),
                'bundle': raw.get('bundle') or self.service._state.get('settings', {}).get('bundle', 'work'),
                'updatedAt': raw.get('updatedAt', 0), 'nativeRevision': raw.get('transcriptRevision'),
                'turnCount': raw.get('turnCount'), 'shared': True, 'historyManaged': True,
                'historyLoaded': False, 'historyReadOnlyReason': raw.get('readOnlyReason'),
                '_catalogId': values['id'], '_catalogRecentAt': values['recent']}
        else:
            row = raw
            if self._native_path and values['history_managed']:
                current = self.db.execute('SELECT value FROM history_native.native_rows WHERE namespace=? AND project=? AND identity=?',
                    (self._native_namespace, values['native_project'], values['native_id'])).fetchone()
                if current is not None:
                    canonical = normalize_availability(json.loads(current[0]), values['available'])
                    count(self.service, 'directory.rows.decoded')
                    row.update(updatedAt=canonical.get('updatedAt', 0), turnCount=canonical.get('turnCount'),
                               historyReadOnlyReason=canonical.get('readOnlyReason'), nativeAvailable=True)
                    if canonical.get('bundle'):
                        row['bundle'] = canonical['bundle']
                    # A loaded view owns its older stamp until the exact reader
                    # observes new bytes; replacing it would hide a real append.
                    if not row.get('historyLoaded') or 'nativeRevision' not in row:
                        row['nativeRevision'] = canonical.get('transcriptRevision')
        if values['source'] == 'native':
            row.update(nativeIdentity=values['native_id'], runtimeSessionId=values['native_id'], nativeProject=values['native_project'])
        row.update(id=values['id'], parentId=values['parent_id'], nativeParentId=values['native_parent_id'],
            sessionKind=values['session_kind'], workspace=values['workspace'], workspaceId=values['workspace_id'],
            workspaceAvailable=bool(values['available']), title=values['title'], description=values['description'],
            createdAt=values['created'], recentActivityAt=max(timestamp(raw.get('recentActivityAt')) or 0, values['recent'])
            if values['source'] == 'local' else values['recent'], navigationActivityAt=values['recent'],
            status=values['status'])
        for field, name in (('titleSource','title_source'), ('nativeNameSource','name_source'), ('autoName','auto_name'),
                            ('nativeNameRevision','name_revision'), ('nativeNamePolicyRevision','name_policy_revision')):
            if values[name] is not None:
                row[field] = bool(values[name]) if name == 'auto_name' else values[name]
        if values['purpose']:
            row['sessionPurpose'] = values['purpose']
        if values['managed']:
            row['location'] = {'kind': 'managed'}
        return row

    def _select_rows(self, where='1', params=(), *, limit=100, offset=0, order='ordinal', state=None, identity=None, public_ids=None):
        sql, base = self.effective(state, identity=identity, public_ids=public_ids)
        return self.db.execute(sql + f' SELECT {",".join(COLUMNS)} FROM directory_rows WHERE {where} '
            + f'ORDER BY {order} LIMIT ? OFFSET ?', (*base, *params, limit, offset))

    def select(self, where='1', params=(), *, limit=100, offset=0, order='ordinal', state=None, identity=None, public_ids=None):
        return [self.decode(row) for row in self._select_rows(where, params, limit=limit, offset=offset,
            order=order, state=state, identity=identity, public_ids=public_ids)]

    def resolve(self, identity, native_project=None, *, include_hidden_workspaces=False):
        if not isinstance(identity, str):
            return None
        where = '(id=? OR native_id=? OR alias_id=?)'
        params = [identity] * 3
        if native_project:
            where += ' AND native_project=?'
            params.append(native_project)
        # Explicit chat selection can restore a workspace removed from the UI.
        # This does not override per-chat suppression or deletion receipts.
        state = ({**self.service._state, 'hiddenNativeWorkspaces': []}
                 if include_hidden_workspaces else None)
        rows = self.select(where, params, limit=2, identity=identity, state=state)
        if len(rows) > 1:
            raise ValueError('This native session ID exists in more than one project. Supply nativeProject.')
        return rows[0] if rows else None

    def contains(self, identity):
        return self.resolve(identity) is not None

    def summary_ids(self, identities, *, state=None):
        ids = sorted({identity for identity in identities if isinstance(identity, str)})
        if not ids:
            return []
        def candidates():
            for start in range(0, len(ids), PUBLIC_ID_BATCH):
                batch = ids[start:start + PUBLIC_ID_BATCH]
                yield from self._select_rows(limit=len(ids), state=state, public_ids=batch)
        # Preserve the complete query's ordinal ordering and result bound even
        # for callers requesting more than one source batch. Decode final rows
        # only; the temporary heap is bounded by the caller's requested IDs.
        ordinal = COLUMNS.index('ordinal')
        rows = heapq.nsmallest(len(ids), candidates(), key=lambda row: row[ordinal])
        return [self.decode(row) for row in rows]

    def read_snapshot(self, state):
        self._sync_live(state)
        db = sqlite3.connect(self.service.data_dir / 'app.sqlite3')
        try:
            reader = HistoryDirectory(self.service, db=db, snapshot=True)
            if self._native_path:
                from types import SimpleNamespace
                reader.bind(SimpleNamespace(path=Path(self._native_path), namespace=self._native_namespace))
            reader._live_values = dict(self._live_values)
            reader._live_notices = list(self._live_notices)
            reader._frozen_state = copy.deepcopy({key: state[key] for key in (
                'pinnedSessionIds', 'hiddenNativeSessions', 'hiddenNativeWorkspaces',
                'attentionRead') if key in state})
            reader._live_revision, reader._native_revision = self._live_revision, self._native_revision
            db.execute('BEGIN')
            db.execute('SELECT revision FROM history_directory_layout').fetchone()
            if self._native_path:
                db.execute('SELECT 1 FROM history_native.native_rows LIMIT 1').fetchone()
            return reader
        except BaseException:
            db.close()
            raise

    def close(self):
        if self.snapshot and not self._closed:
            self._closed = True
            self.db.close()

    def chats(self, state: dict[str, Any], *, section: str | None = None) -> ChatPageResult:
        from .history_directory_query import chats
        return chats(self, state, section=section)

    def child_count(self, state, parent):
        from .history_directory_query import child_count
        return child_count(self, state, parent)

    def children(self, state, parent, query='', index=0):
        from .history_directory_query import children
        return children(self, state, parent, query=query, index=index)

    def workspace_counts(self, state, *, archive='active'):
        from .history_directory_query import workspace_counts
        return workspace_counts(self, state, archive=archive)

    def root_count(self, state=None):
        state = self._frozen_state if self.snapshot else state if state is not None else self.service._state
        self._sync_live(state)
        local = self.db.execute('SELECT membership_revision FROM history_directory_layout WHERE version=1').fetchone()
        native = (self.db.execute('SELECT observation_membership FROM history_native.native_revisions WHERE namespace=?',
                                 (self._native_namespace,)).fetchone() if self._native_path else None)
        # Only unsaved membership differences matter. Ordinary admission of an
        # existing saved owner, receipt changes, and streaming/status/title
        # updates must not turn a count read into another catalog query.
        overlays = []
        for identity, values in self._live_values.items():
            current = _membership(values)
            saved = self.db.execute('SELECT ' + ','.join(MEMBERSHIP_COLUMNS) +
                                    ' FROM history_local_rows WHERE id=?', (identity,)).fetchone()
            if saved is None or tuple(saved) != current:
                overlays.append(current)
        from .managed_deletion import CatalogSuppression
        suppression = (tuple(sorted(set(state.get('hiddenNativeSessions', ())))),
                       tuple(sorted(set(state.get('hiddenNativeWorkspaces', ())))),
                       CatalogSuppression(self.db).signature())
        stamp = (self._native_path, self._native_namespace,
                 local[0] if local else 0, native[0] if native else 0,
                 tuple(sorted(overlays, key=lambda row: row[0])), suppression,
                 self.db.execute('PRAGMA main.data_version').fetchone()[0],
                 self.db.execute('PRAGMA history_native.data_version').fetchone()[0]
                 if self._native_path else None)
        reusable = self.snapshot or not self.db.in_transaction
        memo = getattr(self, '_root_count_memo', None)
        if reusable and memo is not None and memo[0] == stamp:
            return memo[1]
        sql, params = self.effective(state)
        result = self.db.execute(sql + " SELECT count(*) FROM directory_rows WHERE session_kind='root'", params).fetchone()[0]
        if reusable:
            self._root_count_memo = (stamp, result)
        return result

    def attention_rows(self, state):
        # Only owners of actual notices participate; an idle 27k native catalog
        # neither decodes summaries nor enters the existing attention projector.
        return self.select('notice_count>0', limit=-1, state=state)
