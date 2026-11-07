"""User-owned standing access policy, outside repository and model settings.

The host and native workers resolve the same exact policy and source records.
Mode changes never start a turn or replay an old operation.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def execution_id(session):
    # Match RuntimeManager.start; history aliases can differ from the live root.
    return session.get('runtimeSessionId') or session.get('nativeIdentity') or session['id']


def keys(workspace, session_id):
    workspace = str(Path(workspace).expanduser().resolve())
    return {scope: 'access-policy:' + digest(value) for scope, value in (
        ('session', ['session', workspace, session_id]), ('workspace', ['workspace', workspace]), ('app', ['app']))}


def _records(db, workspace, session_id, *, global_only=False):
    records = {}
    for scope, identity in keys(workspace, session_id).items():
        if global_only and scope != 'app':
            continue
        row = db.execute('SELECT receipt FROM commands WHERE id=?', (identity,)).fetchone()
        records[scope] = json.loads(row[0])['result'] if row else {'mode': 'inherit', 'revision': 0}
    return records


def _owner(db, identity):
    """Project one committed owner, without following cold history references."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='state_records'").fetchone():
        return None
    row = db.execute("""SELECT id, json_extract(value,'$.workspace'),
        coalesce(json_extract(value,'$.runtimeSessionId'),json_extract(value,'$.nativeIdentity'),id),
        json_extract(value,'$.collaboration.creatorSessionId'),json_extract(value,'$.collaboration.requestId'),
        json_extract(value,'$.scheduledOrigin.sessionId'),json_extract(value,'$.scheduledOrigin.scheduleId'),
        json_extract(value,'$.scheduledOrigin.runId'),coalesce(json_extract(value,'$.interruptionRevision'),0),
        json_extract(value,'$.handoffOrigin'),json_extract(value,'$."$viewPayload"')
        FROM state_records WHERE kind='session' AND id=? AND value!='null'""", (identity,)).fetchone()
    if not row or not isinstance(row[1], str):
        return None
    owner = dict(zip(('id', 'workspace', 'nativeId', 'creator', 'request', 'scheduler', 'schedule', 'run', 'interruption'), row))
    if row[9] is not None:
        try:
            owner['handoff'] = json.loads(row[9]) if isinstance(row[9], str) else row[9]
        except ValueError:
            owner['handoff'] = {'invalid': row[9]}
    if row[10] is None:
        return owner
    # The manifest's exact immutable resource owns the saved view. Its mutable
    # view.json presentation can be newer than the committed transaction.
    from .resource_files import detach, read_detached
    try:
        reference = json.loads(row[10])
        if not isinstance(reference, dict) or not isinstance(reference.get('$resource'), str):
            raise ValueError('The saved access-policy reference is invalid.')
        saved = read_detached(detach(db, reference['$resource']))
        if not isinstance(saved, dict) or saved.get('id') != identity or not isinstance(saved.get('workspace'), str):
            raise ValueError('The saved access-policy owner is invalid.')
        creator, scheduler = saved.get('collaboration', {}), saved.get('scheduledOrigin', {})
        if not isinstance(creator, dict) or not isinstance(scheduler, dict):
            raise ValueError('The saved access-policy relationships are invalid.')
    except (OSError, ValueError):
        # Expected unavailable resource evidence is not permission revocation.
        # Retain its exact identity for diagnostics/revision, never use view.json.
        return {**owner, 'unavailable': row[10]}
    return {'id': identity, 'workspace': saved['workspace'],
            'nativeId': saved.get('runtimeSessionId') or saved.get('nativeIdentity') or identity,
            'creator': creator.get('creatorSessionId'), 'request': creator.get('requestId'),
            'scheduler': scheduler.get('sessionId'), 'schedule': scheduler.get('scheduleId'),
            'run': scheduler.get('runId'), 'interruption': saved.get('interruptionRevision', 0),
            'handoff': saved.get('handoffOrigin')}


def _native_owner(db, workspace, session_id, owner_session_id=None):
    """The directory locates aliases; only canonical records supply lineage."""
    owner = _owner(db, owner_session_id or session_id)
    if owner and owner['workspace'] == workspace and (owner['nativeId'] == session_id or owner.get('unavailable')):
        return owner
    if owner_session_id:
        return None  # A stale host identity is never a different owner's source.
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='history_local_rows'").fetchone():
        return None
    matches = db.execute('SELECT id FROM history_local_rows WHERE native_id=? AND workspace=? LIMIT 2',
        (session_id, workspace)).fetchall()
    if len(matches) != 1:
        return None
    owner = _owner(db, matches[0][0])
    return owner if owner and owner['workspace'] == workspace and (owner['nativeId'] == session_id or owner.get('unavailable')) else None


def policy(db, workspace, session_id, *, global_only=False, owner_session_id=None):
    """Resolve standing choices without loading history or copying permission.

    A receiving chat's explicit choice is independent. Otherwise source chat
    choices follow the task ahead of broad defaults. An explicit destination
    workspace choice wins at a cross-workspace source edge.
    """
    workspace = str(Path(workspace).expanduser().resolve())
    original = records = _records(db, workspace, session_id, global_only=global_only)
    lineage, seen = [], set()
    owner = None
    while True:
        scope = next((key for key, row in records.items() if row['mode'] != 'inherit'), None)
        # Keep the existing revision for independent choices and ordinary roots,
        # including retained Full operations from before source inheritance.
        def result(mode=None, source=None):
            value = {'mode': mode or (records[scope]['mode'] if scope else 'ask'),
                     'source': source or ('inherited' if lineage and scope in {'session', 'workspace'} else scope or 'default'),
                     'revision': digest({'overrides': original, 'lineage': lineage}) if lineage else digest(original),
                     'overrides': original}
            return value
        def unavailable(reason):
            # A broken source cannot supply authority or erase the operator's
            # independently applicable receiving workspace/app preference.
            lineage.append({'unavailable': reason, 'owner': owner})
            return result(next((row['mode'] for row in original.values()
                                if row['mode'] != 'inherit'), 'ask'), 'unavailable')
        if global_only or scope == 'session':
            return result()
        if owner is None:
            owner = _native_owner(db, workspace, session_id, owner_session_id)
        if owner is None:
            return result()
        if owner.get('unavailable'):
            return unavailable('source payload unavailable')
        if owner['id'] in seen:
            return unavailable('cycle')
        seen.add(owner['id'])
        handoff = owner.get('handoff')
        if sum(bool(value) for value in (owner['creator'], owner['scheduler'], handoff)) > 1:
            return unavailable('ambiguous source')
        if handoff:
            # Only canonical creation writes this pointer. Require its exact
            # durable operation as well; a peer message cannot invent lineage.
            if (not isinstance(handoff, dict) or any(not isinstance(handoff.get(key), str) or not handoff[key]
                    for key in ('sessionId', 'requestId', 'createCommandId')) or not db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='new_chat_handoffs'").fetchone()):
                return unavailable('missing handoff')
            relation = db.execute("""SELECT 1 FROM new_chat_handoffs WHERE id=? AND source=?
                AND json_extract(value,'$.mode')='full'
                AND json_extract(value,'$.targetSessionId')=?
                AND json_extract(value,'$.createCommandId')=?""",
                (handoff.get('requestId'), handoff.get('sessionId'), owner['id'], handoff.get('createCommandId'))).fetchone()
            if not relation:
                return unavailable('invalid handoff')
        source_id = owner['creator'] or owner['scheduler'] or (handoff or {}).get('sessionId')
        if not source_id:
            return result()
        source = _owner(db, source_id)
        edge = [owner['id'], owner['workspace'], owner['nativeId'],
                'commissioned' if owner['creator'] else 'handoff' if handoff else 'scheduled',
                source_id, owner['request'] if owner['creator'] else handoff if handoff else [owner['schedule'], owner['run']]]
        if source is None:
            lineage.append({'relationship': edge, 'source': None})
            return unavailable('missing source')
        if source['workspace'] != owner['workspace'] and records['workspace']['mode'] != 'inherit':
            return result(records['workspace']['mode'], 'inherited' if lineage else 'workspace')
        workspace, session_id = source['workspace'], source['nativeId']
        records = _records(db, workspace, session_id)
        # Source Stop invalidates a captured derived effect, while the saved
        # preference itself remains Full. Execution admission still owns Stop.
        lineage.append({'relationship': edge,
                        'source': [source['id'], workspace, session_id, source['interruption']],
                        'overrides': records})
        owner = source


def read_mode(home, workspace, session_id, *, owner_session_id=None):
    """Worker-side read: no app imports, scans, history loading or writes."""
    path = Path(home) / 'app.sqlite3'
    if not path.exists():
        return 'ask'
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        return policy(db, workspace, session_id, owner_session_id=owner_session_id)['mode']


FILE_KEYS = ('allowed_write_paths', 'denied_write_paths', 'allowed_read_paths', 'denied_read_paths')
SEARCH_KEYS = ('allowed_paths', 'denied_paths')


def policy_targets(row):
    config = row.setdefault('config', {})
    if row.get('module') == 'tool-search':
        return [(config, SEARCH_KEYS), *( (config.setdefault(name, {}), SEARCH_KEYS) for name in ('grep', 'glob'))]
    return [(config, FILE_KEYS)]


def walk_tools(bundle):
    def walk(rows, children):
        for row in rows:
            if isinstance(row, dict) and row.get('module') in {'tool-filesystem', 'tool-apply-patch', 'tool-image', 'tool-search'}:
                yield row
        for child in children.values():
            if isinstance(child, dict):
                yield from walk(child.get('tools', []), child.get('agents', {}))
    return walk(bundle.tools, bundle.agents)


def restore_file_policy(bundle):
    """Restore only widened paths; retain later non-policy configuration edits."""
    for row in walk_tools(bundle):
        originals = row.get('config', {}).pop('_unified_access_base', None)
        if originals is not None:
            for (target, names), original in zip(policy_targets(row), originals):
                for name in names:
                    target.pop(name, None)
                target.update(original)


def full_file_policy(bundle):
    """Full access overrides saved file ceilings, without changing their source."""
    roots = list(os.listdrives()) if os.name == 'nt' and hasattr(os, 'listdrives') else [Path.cwd().anchor]
    for row in walk_tools(bundle):
        targets = policy_targets(row)
        row['config']['_unified_access_base'] = [
            {name: copy.deepcopy(target[name]) for name in names if name in target} for target, names in targets]
        for target, names in targets:
            if names == SEARCH_KEYS:
                target.update(allowed_paths=None, denied_paths=[])
            else:
                target.update(allowed_write_paths=roots, denied_write_paths=[], allowed_read_paths=None, denied_read_paths=[])
    return bundle


def definitions(schema, string):
    sid = {'sessionId': string(200)}
    return {
        'access.get': ('Read this chat’s saved access mode and inherited defaults. Passive.', schema({**sid, 'scope': {'enum': ['app']}}, [])),
        'access.set': ('User-only: save Ask when needed or Full access for this chat, workspace, or app. Full access preauthorizes tool and configuration approvals; it never starts or replays work.',
            schema({**sid, 'scope': {'enum': ['session', 'workspace', 'app']},
                    'mode': {'enum': ['ask', 'full', 'inherit']}, 'expectedRevision': string(100)},
                   ['scope', 'mode', 'expectedRevision'])),
    }


class AccessMode:
    def __init__(self, service):
        self.service = service
        self._settling = set()
        self._settle_again = set()
        self.approval_timeout = 5

    def snapshot(self, session):
        return {'sessionId': session['id'], **policy(self.service.db, session['workspace'], execution_id(session), owner_session_id=session['id']),
                'fileAccessPending': bool(session.get('fileAccessPending'))}

    def full(self, session, revision=None):
        current = self.snapshot(session)
        return current['mode'] == 'full' and (revision is None or current['revision'] == revision)

    def capture_full(self, session, origin):
        """Capture standing authority; effect owners retain their own facts.

        The caller supplies the resolved owner and eligible trusted origin.
        This creates no grant or store entry and never hydrates a conversation.
        Delayed effects check their retained revision, not a fresh capture.
        """
        current = self.snapshot(session)
        if current['mode'] != 'full':
            return None
        authority = {'origin': origin, 'sessionId': session['id'],
                     'accessRevision': current['revision']}
        from .collaboration import BINDING, PRINCIPAL
        if origin == 'agent' or PRINCIPAL.get() or BINDING.get():
            binding, active = self.service.collaboration.active_binding(session, allow_descendant=True)
            inputs = binding.get('_inputBindings', [])
            if (not isinstance(inputs, list)
                    or any(not isinstance(row, dict) or not isinstance(row.get('inputId'), str)
                           or row['inputId'] not in active.get('inputIds', []) for row in inputs)):
                from .errors import AppError
                raise AppError('Full access requires current admitted input bindings when supplied.', 403)
            # The native worker supplies every assigned input ID. Missing/empty
            # transport cannot prove an empty assignment or borrow later IDs.
            ids = [row['inputId'] for row in inputs]
            authority.update(runtimeSessionId=PRINCIPAL.get(), generationId=active['id'],
                             inputIds=ids, inputCoverage='complete' if ids else 'unavailable')
        return authority

    async def dispatch(self, action, args, origin, command_id, caller, *, admission_check=None, expected_revision=None):
        from .errors import AppError
        if action == 'access.set' and origin not in {'ui', 'user'}:
            raise AppError('Only the user can change access mode.', 403)
        global_only = args.get('scope') == 'app' and not args.get('sessionId')
        if global_only and origin not in {'ui', 'user'}:
            raise AppError('Only the user can manage app-wide access defaults.', 403)
        if not global_only and not args.get('sessionId'):
            raise AppError('Choose a chat or the app-wide access settings.', 400)
        session = None if global_only else (
            self.service._session(args['sessionId']) if origin in {'ui', 'user'}
            else self.service.configuration_authority.source(args, origin, caller, allow_descendant=action == 'access.get'))
        def snapshot():
            return policy(self.service.db, '/', '', global_only=True) if global_only else self.snapshot(session)
        if action == 'access.get':
            return {'accepted': True, 'result': snapshot()}
        identity = command_id or str(uuid.uuid4())
        fingerprint = digest([action, args, session['id'] if session else None])
        async with self.service.lock:
            previous = self.service.db.execute('SELECT fingerprint,receipt FROM commands WHERE id=?', (identity,)).fetchone()
            if previous:
                if previous[0] != fingerprint:
                    raise AppError('This command ID was already used with different contents.', 409)
                return {**json.loads(previous[1]), 'duplicate': True}
            if admission_check is not None:
                admission_check()
            if expected_revision is not None and expected_revision != self.service.state['revision']:
                raise AppError('The app changed. Refresh its state and retry.', 409)
            if not global_only:
                session = self.service._session(args['sessionId'])
            current = snapshot()
            if current['revision'] != args['expectedRevision']:
                raise AppError('Access settings changed. Reload them before saving.', 409)
            key = keys('/', '')['app'] if global_only else keys(session['workspace'], execution_id(session))[args['scope']]
            affected = {row['id']: self.snapshot(row) for row in self.service.state['sessions']}
            record = {'mode': args['mode'], 'revision': current['overrides'][args['scope']]['revision'] + 1,
                      'updatedAt': time.time(), 'approvedBy': 'user', 'commandId': identity}
            fields = ('fileAccessPending', 'configurationPending', 'configurationPendingRevision')
            before = {row['id']: {name: row[name] for name in fields if name in row}
                      for row in self.service.state['sessions']}
            self.service.db.commit()
            try:
                self.service.db.execute('INSERT OR REPLACE INTO commands VALUES(?,?,?)',
                    (key, 'host-access-policy', json.dumps({'commandAction': 'access.record', 'result': record})))
                changed = set()
                for row in self.service.state['sessions']:
                    after = self.snapshot(row)
                    if after['revision'] != affected[row['id']]['revision']:
                        changed.add(row['id'])
                        if after['mode'] != affected[row['id']]['mode']:
                            row.update(fileAccessPending=True, configurationPending=True,
                                configurationPendingRevision=row.get('configurationPendingRevision', 0) + 1)
                        if after['mode'] == 'full':
                            self.service._task(self.settle(row['id']))
                receipt = {'accepted': True, 'commandAction': action, 'result': snapshot()}
                self.service.db.execute('INSERT INTO commands VALUES(?,?,?)', (identity, fingerprint, json.dumps(receipt)))
                self.service._publish_changes(sessions=changed or ({session['id']} if session else set()))
            except BaseException:
                self.service.db.rollback()
                if not self.service.db.execute('SELECT 1 FROM commands WHERE id=?', (identity,)).fetchone():
                    for row in self.service.state['sessions']:
                        for name in fields:
                            row.pop(name, None)
                        row.update(before[row['id']])
                raise
        for sid in changed:
            await self.service.refresh_configuration(sid)
        return receipt

    async def settle(self, sid):
        """Retry only resolution of the exact live waiter, never its operation."""
        if sid in self._settling:
            self._settle_again.add(sid)
            return
        self._settling.add(sid)
        try:
            attempt = 0
            while True:
                self._settle_again.discard(sid)
                retry = await self._settle_once(sid)
                if sid in self._settle_again:
                    continue
                if not retry or attempt >= 2:
                    return
                attempt += 1
                await asyncio.sleep(.25)
        finally:
            self._settling.discard(sid)

    async def _settle_once(self, sid):
        async with self.service.lock:
            session = self.service._session(sid)
            if not self.full(session):
                return False
            revision = self.snapshot(session)['revision']
            interruption = session.get('interruptionRevision', 0)
            replies = []
            authority = self.service.configuration_authority
            for approval in session.get('approvals', []):
                if approval.get('status') != 'pending' or approval.get('resolutionReason'):
                    continue
                identity = approval['id']
                if identity.startswith('capability:'):
                    grant = authority.record(identity)
                    if grant not in authority.eligible(session):
                        continue
                    if grant['status'] != 'active' or grant.get('accessRevision') != revision:
                        grant.update(status='active', revision=grant['revision'] + 1,
                            approvedBy='full-access', accessRevision=revision, decidedAt=time.time())
                        authority.save(grant)
                    if grant.get('nativeWait'):
                        replies.append((identity, 'allow'))
                    else:
                        approval['status'] = 'allow'
                else:
                    options = approval.get('options', [])
                    if (not isinstance(options, list) or not options
                            or any(not isinstance(value, str) for value in options)
                            or not set(options).issubset({'allow', 'deny', 'approve', 'reject'})):
                        continue
                    decision = next((value for value in ('allow', 'approve') if value in options), None)
                    if not decision:
                        continue
                    replies.append((identity, decision))
                approval.update(approvedBy='full-access', accessRevision=revision)
            self.service._publish_changes(sessions={sid})
        respond = getattr(self.service.runtime, 'approval', None)
        if not callable(respond):
            return False
        retry = False
        for identity, decision in replies:
            if not self.full(session, revision) or interruption != session.get('interruptionRevision', 0):
                return False
            error = None
            try:
                await asyncio.wait_for(respond(sid, identity, decision), self.approval_timeout)
            except (RuntimeError, ValueError, KeyError, OSError, TimeoutError):
                error = 'The saved Full access decision has not been acknowledged by the worker. It may already have arrived. No operation was replayed.'
                retry = True
            async with self.service.lock:
                approval = next((row for row in session.get('approvals', []) if row['id'] == identity), None)
                if approval and approval['status'] == 'pending':
                    if error:
                        approval['deliveryError'] = error
                    else:
                        approval.update(status=decision)
                        approval.pop('deliveryError', None)
                    self.service._publish_changes(sessions={sid})
        return retry
