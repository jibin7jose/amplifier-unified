"""App policy over managed Git worktrees and explicit execution handoff."""
import asyncio
import copy
import json
import sqlite3
import time
import uuid
from pathlib import Path

from amplifier_worktrees import GitWorktrees
from amplifier_worktrees.git import atomic, common, digest

from .host_identity import local_host_identity, require_local_host
from . import manager_placement


class HandoffSuperseded(ValueError):
    """A current human/task anchor revoked unperformed placement work."""


def definitions(schema, string):
    sid = {'sessionId': {**string(200), 'description': 'This chat, or a current commissioned member of your owned active assignment for inspect/list/status/create/attach/handoff. Other worktree actions remain caller-owned.'}}
    identity = {**sid, 'id': string(200)}
    revision = {'expectedRevision': {'type': 'integer', 'minimum': 0}}
    repository = {'repositoryPath': {**string(4000), 'minLength': 1}}
    return {
        'worktree.inspect': ('Inspect a repository inside this task\'s workspace/execution folders. Use mode=clean and optional ref for lightweight committed-source inspection: returns expectedCommit without reading local changes or untracked files. Omitted mode retains full snapshot inspection for sourceRevision/carry_dirty. Keep the returned absolute repository root for later creation/attachment.', schema({**sid, **repository, 'mode': {'enum': ['clean', 'carry_dirty']}, 'ref': string(500)}, ['sessionId'])),
        'worktree.list': ('List checkouts associated with this task and durable handoff receipts.', schema(sid)),
        'worktree.status': ('Inspect a checkout, its original source manifest, conflicts and Git ownership.', schema(identity)),
        'worktree.create': ('Create a managed checkout. For clean creation, pass expectedCommit from mode=clean inspection (a full Git commit ID); optional ref must resolve to that commit. Local changes are neither scanned nor copied. Legacy sourceRevision is a full inspected snapshot digest, not a Git SHA, and remains required for carry_dirty. Supply exactly one binding. Does not move execution or configure dependencies. A direct commissioning manager may prepare a checkout for its idle member.', schema({**sid, **repository, 'sourceRevision': string(100), 'expectedCommit': string(64), 'mode': {'enum': ['clean', 'carry_dirty']}, 'ref': string(500), 'branch': string(500)}, ['sessionId'])),
        'worktree.attach': ('Associate an existing Git worktree from the selected repositoryPath (canonical home by default); does not copy history or start work. Attached folders cannot be deleted through managed cleanup.', schema({**sid, **repository, 'path': string(4000)}, ['sessionId', 'path'])),
        'worktree.archive': ('Retire an unused app-created checkout while retaining every file and its Git registration. Moves the whole checkout to private archive storage; does not reclaim disk space or create an immutable snapshot. Active or ambiguous ownership is refused.', schema({**identity, **revision})),
        'worktree.restore': ('Restore an archived checkout without sending input or moving conversation execution. Keeps dirty, ignored and unknown files. Refuses a destination collision.', schema({**identity, **revision})),
        'worktree.remove': ('Remove an app-created checkout only when it has no changed/untracked/ignored files and no task is executing there. Preserves branch, source, history and manifests.', schema({**identity, **revision})),
        'worktree.handoff': ('Move this task’s (or your idle commissioned member’s) execution folder to an associated checkout or back to its canonical home (id null). Returns a durable pending receipt. Finish the current turn; the host waits for tools and children, saves and releases the runtime, then continues active work in the destination. For a manager-directed move, the host instead notifies the manager automatically; inspect the completed placement before sending the member a new brief. Do not poll or ask the user to continue. Stop or changed task authority suppresses continuation. No input or uncertain effect is replayed. History, settings, drafts, voice and artifact identities remain unchanged.', schema({**sid, 'id': {'type': ['string', 'null']}, 'expectedExecutionRevision': {'type': 'integer', 'minimum': 0}}, ['sessionId', 'id', 'expectedExecutionRevision'])),
        'worktree.reconcile': ('After inspecting an unknown handoff, deliberately choose its source or target. Requires current receipt revision and user evidence, confirms writer release again, and never replays model input. Agents must cite the actual user finding.', schema({**identity, **revision, 'destination': {'enum': ['source', 'target']}, 'evidence': {**string(4000), 'minLength': 1}, 'sourceMessageId': string(200)}, ['sessionId', 'id', 'expectedRevision', 'destination', 'evidence'])),
    }


class Worktrees:
    def __init__(self, service):
        self.app = service
        self.git = GitWorktrees(service.data_dir / 'managed-worktrees', execution_host=local_host_identity())
        self.receipt_dir = service.data_dir / 'worktree-handoffs'
        self.receipt_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.jobs = set()
        self.mutations = asyncio.Lock()
        self._creation_paths = {}
        self._moving_paths = ()
        self._uncertain_paths = ()
        self._unverified_placements = set()
        self.recoverable = []
        saved_owners = {}
        for path in self.receipt_dir.glob('*.json'):
            record = json.loads(path.read_text())
            sid = record['sessionId']
            if sid not in saved_owners:
                saved_owners[sid] = self.saved_owner(sid, durable=True) or {}
            session = saved_owners[sid]
            committed = self.placement_committed(record, session)
            later_placement = session.get('executionRevision', 0) > record['executionRevision'] + 1
            inconsistent = record['phase'] == 'applied' and not committed and not later_placement
            source_retained = (session.get('executionRevision', 0) == record['executionRevision']
                and session.get('workspace') == record['historyHome']
                and (session.get('workingDirectory') or session.get('workspace')) == record['source'])
            notification_pending = record.get('resumeRequested') and record.get('continuation', {}).get('status') in {None, 'waiting', 'backpressured'}
            reconciliation_pending = (record.get('reconciles')
                and self.read(record['sessionId'], record['reconciles'])['phase'] == 'unknown')
            if record.get('boundaryVersion') == 1 and (
                    record['phase'] == 'pending' and record.get('step') == 'released' and record.get('release', {}).get('quiesced')
                    or record['phase'] == 'applied' and (
                        not inconsistent and (notification_pending or reconciliation_pending)
                        or source_retained and record.get('release', {}).get('quiesced'))):
                if inconsistent:
                    # The receipt replacement can precede the owner transaction.
                    # Restore the execution fence before retrying that placement.
                    record.update(phase='pending', step='released', revision=record['revision']+1,
                        detail='Runtime release is confirmed; execution placement still needs to be committed. No input was replayed.')
                    self.save(record)
                self.recoverable.append(record['id'])
            elif record['phase'] == 'pending' and record.get('step') in {'preflight', 'waiting'}:
                record.update(phase='rejected', revision=record['revision']+1,
                    detail='The app restarted before runtime release began. The source execution folder is unchanged.')
                session['configurationBusy'] = False
                self.save(record)
            elif record['phase'] == 'pending' or inconsistent:
                record.update(phase='unknown', revision=record['revision']+1, detail='The app restarted during handoff. No input was replayed; inspect and reconcile explicitly.')
                self.save(record)
            if (record.get('managerPlacement') and notification_pending
                    and record['phase'] in {'rejected', 'unknown'}):
                self.recoverable.append(record['id'])
        self.sync()
        self.bind_runtime()

    def saved_owner(self, sid, *, durable=False):
        """Read exact durable placement without admitting or hydrating its body."""
        resident = next((s for s in self.app.state['sessions'] if s['id'] == sid), None)
        if resident is not None and not durable:
            return resident
        from .state_records import load_owner
        saved = load_owner(self.app.db, sid)[0]
        indexed = self.app.db.execute('SELECT summary FROM history_local_rows WHERE id=?', (sid,)).fetchone()
        if indexed is not None:
            if saved is None:
                raise ValueError('Saved task placement is unavailable; preserve the checkout and inspect its owner.')
            metadata = json.loads(indexed[0])
            if not durable and 'workingDirectory' in metadata and 'executionRevision' in metadata:
                return metadata
            if (durable and saved.get('$native') and 'workingDirectory' not in saved
                    and 'executionRevision' not in saved):
                # Older native owner indexes omitted these scalars. Their exact
                # committed directory row was the retained placement record;
                # newer indexes persist it directly with the handoff identity.
                saved = {**saved, **{key: metadata[key] for key in
                    ('workingDirectory', 'executionRevision', 'executionHost', 'executionHandoffId')
                    if key in metadata}}
        if saved is not None:
            # Older directory rows may lack placement facts. Detach only this
            # owner's view; never admit it or materialize its lazy body fields.
            from .session_projection import hydrate
            context = {'sessions': [saved], 'runtimeControl': {}}
            hydrate(self.app.data_dir, context, self.app.db)
            return context['sessions'][0]
        return None

    @staticmethod
    def placement_committed(record, owner):
        """Only the committed owner proves this receipt's placement completed."""
        return bool(owner and owner.get('id') == record['sessionId']
            and owner.get('workspace') == record['historyHome']
            and owner.get('workingDirectory') == record['target']
            and owner.get('executionRevision', 0) == record['executionRevision'] + 1
            and (not record.get('executionHost') or owner.get('executionHost') == record['executionHost'])
            # Old persisted receipts predate the operation identity. Their
            # exact owner/home/host/path/revision remains migration evidence;
            # new receipts must also match the identity saved with placement.
            and (record.get('placementVersion') is None or
                 record.get('placementVersion') == 1 and owner.get('executionHandoffId') == record['id']))

    @staticmethod
    def restore_placement(session, owner):
        for key in ('workingDirectory', 'executionHost', 'executionRevision', 'executionHandoffId'):
            if key in owner:
                session[key] = copy.deepcopy(owner[key])
            else:
                session.pop(key, None)

    def resolve_placements(self, session_ids=None):
        """Do not publish speculative placement after an unreadable commit outcome."""
        pending = self._unverified_placements.copy()
        if session_ids is not None:
            pending.intersection_update(session_ids)
        for sid in pending:
            saved = self.saved_owner(sid, durable=True)
            if saved is None:
                raise ValueError('Saved execution placement is unavailable; its unknown handoff remains fenced.')
            current = next((row for row in self.app.state['sessions'] if row['id'] == sid), None)
            if current is not None:
                self.restore_placement(current, saved)
            self._unverified_placements.discard(sid)

    def reconcile_prior(self, record):
        if record.get('reconciles'):
            previous = self.read(record['sessionId'], record['reconciles'])
            if previous.get('reconciliation') != record['id']:
                previous.update(phase='reconciled', revision=previous['revision']+1, reconciliation=record['id'])
                self.save(previous)
                return True
        return False

    def occupied_paths(self):
        """Complete scalar placement proof for explicit checkout retirement."""
        residents = {row['id']: row for row in self.app.state['sessions']}
        paths = set()
        missing = self.app.db.execute(
            "SELECT l.id FROM history_local_rows l LEFT JOIN state_records r "
            "ON r.kind='session' AND r.id=l.id WHERE (r.value IS NULL OR r.value='null') "
            "AND l.id NOT IN (SELECT value FROM json_each(?)) LIMIT 1", (json.dumps(list(residents)),)).fetchone()
        if missing is not None:
            raise ValueError('Saved task placement is unavailable; preserve the checkout and inspect its owner.')
        for identity, workspace, execution in self.app.db.execute(
                "SELECT r.id,json_extract(r.value,'$.workspace'),json_extract(l.summary,'$.workingDirectory') "
                "FROM state_records r LEFT JOIN history_local_rows l ON l.id=r.id "
                "WHERE r.kind='session' AND r.value!='null'"):
            if identity not in residents:
                if execution is None:
                    owner = self.saved_owner(identity)
                    if owner is None:
                        raise ValueError('Saved task placement is unavailable; preserve the checkout and inspect its owner.')
                    execution = owner.get('workingDirectory') or owner.get('workspace')
                paths.update(path for path in (workspace, execution) if path)
        for row in residents.values():
            paths.update(path for path in (row.get('workspace'), row.get('workingDirectory')) if path)
        directory = self.app.directory
        sql, params = directory.effective()
        paths.update(path for path, in self.app.db.execute(
            sql + ' SELECT DISTINCT workspace FROM directory_rows WHERE workspace IS NOT NULL', params))
        return paths

    def execution_state(self, sid):
        session = self.app._session(sid)
        host = session.get('executionHost')
        host_matches = not host or (host.get('scope') == 'local' and host.get('id') == local_host_identity()['id'])
        return {'hostMatches': host_matches, 'executionHost': copy.deepcopy(host), 'directory': session.get('workingDirectory') or session['workspace'],
                'revision': session.get('executionRevision', 0),
                'interruptionRevision': session.get('interruptionRevision', 0),
                'fenced': any(row['phase'] in {'pending', 'unknown'} for row in session.get('worktreeHandoffs', [])) or
                    bool(getattr(self.app, 'portability', None) and self.app.portability.fenced(sid))}

    def bind_runtime(self):
        binding = getattr(self.app.runtime, 'bind_execution_state', None)
        if callable(binding): binding(self.execution_state)

    def start(self):
        # Production creates its runtime after AppService hydration. Install
        # the reader before accepting inputs, including restored unknown moves.
        self.bind_runtime()
        operations = getattr(self.app, 'operations', None)
        if operations and hasattr(operations, 'register_source'):
            operations.register_source('worktree', self.operation_records, self.operation_record)
        for identity in self.recoverable:
            record = json.loads((self.receipt_dir / (identity + '.json')).read_text())
            self.launch(record, copy.deepcopy(self.app._session(record['sessionId'])))
        self.recoverable.clear()

    def launch(self, record, session):
        job = asyncio.create_task(self.finish(record, session))
        self.jobs.add(job)
        job.add_done_callback(self.jobs.discard)

    def operation_records(self, sid):
        return [self.operation_shape(row) for row in self.receipts(sid)]

    def operation_record(self, sid, identity):
        try: return self.operation_shape(self.read(sid, identity.removeprefix('worktree:')))
        except ValueError: return None

    @staticmethod
    def operation_shape(row):
        return {'id': 'worktree:' + row['id'], 'sessionId': row['sessionId'], 'source': 'worktree', 'kind': 'checkout-handoff', 'state': {'pending': 'running', 'applied': 'completed', 'reconciled': 'completed', 'rejected': 'failed', 'unknown': 'outcome_unknown'}[row['phase']], 'revision': row['revision'], 'createdAt': row['createdAt'], 'updatedAt': row.get('updatedAt', row['createdAt']), 'controlAvailable': False, 'evidence': copy.deepcopy(row)}

    def receipts(self, sid):
        records = [json.loads(path.read_text()) for path in sorted(self.receipt_dir.glob('*.json'))]
        return [record for record in records if sid is None or record['sessionId'] == sid]
    def save(self, record): atomic(self.receipt_dir / (record['id'] + '.json'), record)
    def read(self, sid, identity):
        try: uuid.UUID(identity)
        except (ValueError, TypeError): raise ValueError('Invalid handoff identity') from None
        path = self.receipt_dir / (identity + '.json')
        if not path.exists(): raise ValueError('Unknown handoff')
        value = json.loads(path.read_text())
        if value['sessionId'] != sid: raise ValueError('This handoff belongs to another task')
        return value

    def record(self, sid, identity):
        record = self.git.get(identity)
        if record.get('sessionId') != sid: raise ValueError('This checkout belongs to another task')
        return record

    def inherited_binding(self, session, path, *, retained=None):
        """Recognize only the exact placement committed by task creation."""
        metadata = session.get('collaboration') or {}
        if not metadata.get('requestId'):
            raise ValueError('The folder is not this task’s canonical home or an associated checkout')
        request = self.app.collaboration.receipt(metadata.get('requestId'))
        if (request.get('commandAction') != 'coordination.create' or not request.get('accepted')
                or request.get('creationVersion') != 1 or request.get('protocol') != 2
                or request.get('sessionId') != session['id'] or request.get('workspace') != session['workspace']
                or metadata != request.get('result')
                or metadata.get('requestId') != request.get('requestId')
                or metadata.get('creatorSessionId') != request.get('senderSessionId')
                or metadata.get('workingDirectory') != path):
            raise ValueError('The inherited execution placement has no matching creation receipt')
        binding = {'kind': 'inherited', 'path': path, 'creationRequestId': request['requestId'],
                   'creatorSessionId': request['senderSessionId']}
        # The creator may itself have inherited this placement. The creation
        # receipt grants the exact path; this lookup only retains Git identity,
        # never grants ownership or a choice among another task's checkouts.
        if retained is not None:
            checkout = self.git.get(retained['checkoutId']) if retained.get('checkoutId') else None
            if checkout and checkout['path'] != path:
                raise ValueError('The inherited checkout folder changed; inspect it again')
        else:
            matches = [row for row in self.git.records() if row['path'] == path]
            checkout = min(matches, key=lambda row: (row['status'] != 'ready', not row['owned'], row['id'])) if matches else None
        if checkout:
            self.check_host(checkout)
            binding.update(checkoutId=checkout['id'], checkoutOwnerId=checkout['sessionId'], repository=checkout['repository'])
        return binding

    def placement_binding(self, session, path, checkout_id=None, *, inherited=False):
        """Bind an exact home or associated checkout, never an arbitrary folder."""
        if checkout_id is None and path == session['workspace']:
            return {'kind': 'home', 'path': path}
        if checkout_id is None:
            # Also resolves the source of older durable receipts. Only an exact
            # associated placement is recoverable; Git membership is not authority.
            checkout_id = next((row['id'] for row in session.get('worktrees', [])
                if row['path'] == path and row['status'] == 'ready'), None)
            if checkout_id is None:
                if inherited: return self.inherited_binding(session, path)
                raise ValueError('The folder is not this task’s canonical home or an associated checkout')
        checkout = self.record(session['id'], checkout_id)
        self.check_host(checkout)
        if checkout['status'] != 'ready': raise ValueError('The checkout is not ready; inspect its conflicts first')
        if checkout['path'] != path: raise ValueError('The checkout folder changed; inspect it again')
        return {'kind': 'checkout', 'path': path, 'checkoutId': checkout_id, 'repository': checkout['repository']}

    def check_binding(self, record, session):
        for side in ('target', 'source'):
            binding = record[side + 'Binding']
            expected = (self.inherited_binding(session, record[side], retained=binding) if binding['kind'] == 'inherited'
                        else self.placement_binding(session, record[side], binding.get('checkoutId')))
            if binding != expected: raise ValueError(f'The {side} checkout binding changed; inspect it again')

    def preflight(self, record, session):
        # Both ends form the durable handoff provenance. A valid destination
        # cannot authorize releasing a source replaced since receipt creation.
        for side in ('target', 'source'):
            binding = record[side + 'Binding']
            path = Path(record[side])
            if not path.is_dir() or path.resolve(strict=True) != path:
                raise ValueError(f'The {side} folder is unavailable or no longer its recorded canonical path')
            if binding.get('repository'):
                if binding['kind'] == 'inherited':
                    checkout = self.git.get(binding['checkoutId'])
                    if checkout['status'] != 'ready': raise ValueError('The inherited checkout is not ready; inspect it first')
                # Handoff changes execution placement, not file contents. Keep
                # identity validation independent of preservation manifest limits.
                root, repository = common(path)
                if str(root) != binding['path'] or str(repository) != binding['repository']:
                    raise ValueError(f'The {side} is no longer in the recorded repository')

    def require_available_path(self, path):
        """Fence admission without scanning dormant histories or Git records."""
        roots = (*self._moving_paths, *self._uncertain_paths)
        if roots:
            resolved = Path(path).expanduser().resolve()
            if any(resolved.is_relative_to(root) for root in roots):
                raise ValueError('This checkout is being moved or its move is unresolved; inspect it before starting a chat')

    def reserve_creation(self, *paths):
        for path in paths:
            self.require_available_path(path)
        token = object()
        self._creation_paths[token] = tuple(Path(path).expanduser().resolve() for path in paths)
        return token

    def release_creation(self, token):
        self._creation_paths.pop(token, None)

    @staticmethod
    def _occupied(paths, roots):
        return any(Path(path).expanduser().resolve().is_relative_to(root)
                   for path in set(paths) for root in roots)

    @staticmethod
    async def finish_thread(function, *args, on_cancel=None, **kwargs):
        """Keep path reservations until a filesystem effect actually finishes."""
        task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        cancelled = None
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as exc:
                # Cancelling the waiter cannot stop its filesystem thread.
                cancelled = exc
                if on_cancel:
                    on_cancel()
            except BaseException:
                if cancelled is None:
                    raise
                # A pre-admission cancellation may make the worker refuse.
                # Observe that failure below and preserve the caller's cancel.
                break
        if cancelled is not None:
            task.exception()
            raise cancelled
        return task.result()

    def _refresh_uncertain_paths(self, records):
        self._uncertain_paths = tuple(Path(path).resolve() for record in records
            for operation in record.get('archiveOperations', {}).values()
            if operation['status'] in {'pending', 'unknown'}
            for path in (operation['source'], operation['target']))

    async def retire(self, action, record, args, identity):
        roots = [Path(record['path']).resolve()]
        if action != 'worktree.remove':
            roots.append((self.git.directory / ('checkouts' if action == 'worktree.restore' else 'archives') / record['id']).resolve())
        try:
            async with self.app.lock:
                if any(path.is_relative_to(root) for paths in self._creation_paths.values() for path in paths for root in roots):
                    raise ValueError('A chat is being created in this checkout; wait for it to finish')
                # Claim before yielding. Session creation claims its path before
                # directory preparation and checks this fence again at admission.
                self._moving_paths = tuple(roots)
                occupied = self.occupied_paths()
                handoffs = [path for r in self.receipts(None)
                            if r['phase'] in {'pending', 'unknown'} for path in (r['source'], r['target'])]
            # Resolve aliases off the event loop; this examines only placement
            # metadata, never the sessions' messages or event logs.
            if await asyncio.to_thread(self._occupied, occupied, roots):
                raise ValueError('A task still uses this checkout; hand it back first')
            if await asyncio.to_thread(self._occupied, handoffs, roots):
                raise ValueError('An unresolved handoff still refers to this checkout')
            if action == 'worktree.remove':
                return await self.finish_thread(self.git.remove, args['id'], args['expectedRevision'], identity)
            return await self.finish_thread(self.git.relocate, args['id'], args['expectedRevision'], identity,
                                            restore=action == 'worktree.restore')
        finally:
            # Retain a durable uncertainty fence across request cancellation,
            # failures and restart; inspection can reconcile it without replay.
            self._refresh_uncertain_paths(self.git.records())
            self._moving_paths = ()

    def repository_path(self, session, value=None, *, attaching=False):
        execution = Path(session.get('workingDirectory') or session['workspace']).resolve()
        if value is None:
            return str(Path(session['workspace']).resolve() if attaching else execution)
        candidate = (execution / value).resolve()
        allowed = [execution, Path(session['workspace']).resolve(), *[
            Path(row['path']).resolve() for row in self.git.records()
            if row.get('sessionId') == session['id'] and row.get('status') == 'ready']]
        if not any(candidate.is_relative_to(root) for root in allowed):
            raise ValueError('Choose a repository inside this task\'s workspace, execution folder or associated checkout.')
        root, _ = common(candidate)
        if not any(root.is_relative_to(boundary) for boundary in allowed):
            raise ValueError('The Git root lies outside the selected task folders.')
        return str(root)

    @staticmethod
    def check_host(record):
        host = record.get('executionHost')
        if host:
            if host.get('scope') != 'local':
                raise ValueError('Cross-host execution is unavailable.')
            require_local_host(host.get('id'))

    def sync(self):
        records = self.git.records()
        self._refresh_uncertain_paths(records)
        handoffs = [json.loads(path.read_text()) for path in sorted(self.receipt_dir.glob('*.json'))]
        by_session, by_handoff, unresolved = {}, {}, set()
        for record in records:
            by_session.setdefault(record.get('sessionId'), []).append(record)
        for receipt in handoffs:
            sid = receipt['sessionId']
            by_handoff.setdefault(sid, []).append(receipt)
            if receipt['phase'] in {'pending', 'unknown'}:
                unresolved.add(sid)
        groups = (('worktrees', by_session), ('worktreeHandoffs', by_handoff))
        index = self.app.projections.sessions(self.app.state)
        owners = set(by_session) | set(by_handoff) | getattr(self, '_projected_ids', set())
        # First reconciliation clears stale saved projections too.
        if not hasattr(self, '_projected_ids'):
            owners.update(row['id'] for row in self.app.state['sessions'] if row.get('worktrees') or row.get('worktreeHandoffs'))
        self._projected_ids = set(by_session) | set(by_handoff)
        for sid in owners:
            session = index.by_id.get(sid)
            if session is None: continue
            for field, grouped in groups:
                if sid in grouped:
                    session[field] = list(grouped[sid])
                elif not isinstance(session.get(field), list) or session[field]:
                    session[field] = []
            # Only assert this fence: another configuration owner may still
            # be busy after a handoff resolves. Never clear its state here.
            if sid in unresolved:
                session['configurationBusy'] = True
        return owners

    def changed(self, session_ids=()):
        owners = self.sync() | set(session_ids)
        self.app._publish_changes(sessions=owners)
        operations = getattr(self.app, 'operations', None)
        if operations and hasattr(operations, 'notify'): operations.notify()

    async def dispatch(self, action, args, origin, command_id, caller=None):
        if action in {'worktree.inspect', 'worktree.list', 'worktree.status'}:
            return await self._dispatch(action, args, origin, command_id, caller)
        async with self.mutations:
            try:
                return await self._dispatch(action, args, origin, command_id, caller)
            except BaseException:
                async with self.app.lock: self.changed()
                raise

    def preparation_admission(self, action, sid, manager):
        if not manager:
            return None, None
        loop = asyncio.get_running_loop()
        caller_task = asyncio.current_task()
        cancelled = False

        def cancel():
            nonlocal cancelled
            cancelled = True

        async def commit(effect):
            async with self.app.lock:
                if cancelled or caller_task.cancelling():
                    raise ValueError('Checkout preparation was cancelled before admission.')
                manager_placement.check_preparation(self.app, action, sid, manager)
                # No await between authority and the first durable effect.
                # Only this short receipt write runs here, never slow Git.
                return effect()

        def admit(effect):
            # finish_thread owns this waiter until the actual effect settles.
            # A timeout must not leave a queued callback that commits later.
            return asyncio.run_coroutine_threadsafe(commit(effect), loop).result()
        return admit, cancel

    async def _dispatch(self, action, args, origin, command_id, caller=None):
        sid = args['sessionId']
        manager = None
        if origin == 'agent' and caller != sid:
            # A caller comes only from the authenticated bridge, never tool args.
            source, session = manager_placement.authorize(self.app, action, sid, caller)
            if action == 'worktree.handoff' and command_id:
                identity = str(uuid.uuid5(uuid.NAMESPACE_URL, 'worktree-handoff:' + command_id))
                if (self.receipt_dir / (identity + '.json')).exists():
                    prior = self.read(sid, identity)
                    if prior['signature'] != digest([action, args, origin, caller]):
                        raise ValueError('This handoff command already has different contents')
                    return {**prior, 'duplicate': True}
            manager = manager_placement.prepare(self.app, action, session, source)
        else:
            session = self.app._session(sid)
            if origin == 'agent' and action in manager_placement.WRITES:
                self.app.collaboration.source({}, origin, caller)
        if action in {'worktree.inspect', 'worktree.list'}:
            result = {'currentHost': local_host_identity(), 'executionHost': session.get('executionHost') or local_host_identity(), 'historyHome': session['workspace'], 'executionDirectory': session.get('workingDirectory') or session['workspace'], 'executionRevision': session.get('executionRevision', 0), 'settingsScope': session['workspace'], 'items': [r for r in self.git.records() if r.get('sessionId') == sid], 'handoffs': self.receipts(sid)}
            if action == 'worktree.inspect':
                source = await asyncio.to_thread(self.repository_path, session, args.get('repositoryPath'))
                result['repository'] = await asyncio.to_thread(self.git.inspect, source,
                    mode=args.get('mode'), ref=args.get('ref', 'HEAD'))
                result['repositorySelection'] = args.get('repositoryPath', '')
            async with self.app.lock:
                manager_placement.check_preparation(self.app, action, sid, manager)
                session['worktreeInspection'] = result
                self.changed({sid})
            return result
        if action == 'worktree.status':
            self.record(sid, args['id'])
            # A manager's status read cannot reconcile another chat's archive.
            result = await asyncio.to_thread(self.git.status, args['id'], reconcile=not manager)
            async with self.app.lock:
                manager_placement.check_preparation(self.app, action, sid, manager)
                session['worktreeStatus'] = result; self.changed({sid})
            return result
        self.check_host(session)
        identity = command_id or str(uuid.uuid4())
        if action in {'worktree.handoff', 'worktree.reconcile'}:
            return await self.handoff(action, args, origin, identity, manager)
        admit, cancel = self.preparation_admission(action, sid, manager)
        if action == 'worktree.create':
            source = await asyncio.to_thread(self.repository_path, session, args.get('repositoryPath'))
            result = await self.finish_thread(self.git.create, source, command_id=identity,
                expected_revision=args.get('sourceRevision'), expected_commit=args.get('expectedCommit'),
                mode=args.get('mode', 'clean'), ref=args.get('ref'), branch=args.get('branch'),
                session_id=sid, admit=admit, on_cancel=cancel)
        elif action == 'worktree.attach':
            source = await asyncio.to_thread(self.repository_path, session, args.get('repositoryPath'), attaching=True)
            result = await self.finish_thread(self.git.attach, args['path'], source=source, command_id=identity, session_id=sid, admit=admit, on_cancel=cancel)
        elif action in {'worktree.remove', 'worktree.archive', 'worktree.restore'}:
            record = self.record(sid, args['id'])
            self.check_host(record)
            result = await self.retire(action, record, args, identity)
        else: raise ValueError('Unknown worktree action')
        async with self.app.lock: self.changed()
        return result

    async def handoff(self, action, args, origin, command_id, manager=None):
        sid = args['sessionId']; session = self.app._session(sid)
        signature = digest([action, args, origin] + ([manager['sourceSessionId']] if manager else []))
        identity = str(uuid.uuid5(uuid.NAMESPACE_URL, 'worktree-handoff:' + command_id))
        path = self.receipt_dir / (identity + '.json')
        async with self.app.lock:
            manager_placement.check_preparation(self.app, action, sid, manager)
            self.resolve_placements({sid})
            if path.exists():
                record = self.read(sid, identity)
                if record['signature'] != signature: raise ValueError('This handoff command already has different contents')
                return {**record, 'duplicate': True}
            if any(row['phase'] == 'pending' for row in self.receipts(sid)):
                raise ValueError('A handoff is already pending; inspect its receipt before another request')
            previous = None
            if action == 'worktree.reconcile':
                previous = self.read(sid, args['id'])
                self.check_host(previous)
                if previous['revision'] != args['expectedRevision'] or previous['phase'] != 'unknown': raise ValueError('Inspect the current unknown handoff before reconciliation')
                if origin != 'ui':
                    source = next((m for m in session['messages'] if m['id'] == args.get('sourceMessageId') and m.get('role') == 'user' and m.get('inputOrigin') in {'ui', 'user', 'voice'} and not m.get('questionId') and not m.get('scheduledRunId')), None)
                    if not source or source.get('createdAt', 0) < previous['createdAt']: raise ValueError('Cite the user’s actual finding after this handoff')
                target = previous[args['destination']]
                if previous['historyHome'] != session['workspace']: raise ValueError('The canonical home changed; inspect the handoff before reconciliation')
                target_binding = copy.deepcopy(previous.get(args['destination'] + 'Binding') or self.placement_binding(session, target, inherited=True))
            else:
                if session.get('configurationBusy'): raise ValueError('Another configuration change or handoff is unresolved')
                if session.get('executionRevision', 0) != args['expectedExecutionRevision']: raise ValueError('The execution folder revision changed; inspect it again')
                target = self.record(sid, args['id'])['path'] if args.get('id') else session['workspace']
                target_binding = self.placement_binding(session, target, args.get('id'))
            record = {'id': identity, 'sessionId': sid, 'signature': signature, 'revision': 1, 'phase': 'pending', 'executionHost': local_host_identity(), 'source': session.get('workingDirectory') or session['workspace'], 'target': target, 'historyHome': session['workspace'], 'executionRevision': session.get('executionRevision', 0), 'createdAt': time.time(), 'inputsReplayed': False, 'origin': origin, 'detail': 'Saving and releasing the old runtime before changing its execution folder.', 'preserved': {'messageIds': [m['id'] for m in session['messages']], 'taskId': session.get('task', {}).get('id') if session.get('task') else None}, 'reconciles': previous['id'] if previous else None, 'evidence': args.get('evidence')}
            record['sourceBinding'] = self.placement_binding(session, record['source'], inherited=True)
            record['targetBinding'] = target_binding
            record['step'] = 'preflight'
            record['boundaryVersion'] = 1
            record['placementVersion'] = 1
            record['interruptionRevision'] = session.get('interruptionRevision', 0)
            record['humanMessageId'] = self.latest_human(session)
            task = session.get('task') or self.app.coordination.task(sid)
            record['taskBinding'] = {key: task.get(key) for key in ('id', 'revision')}
            active = session.get('collaborationGeneration') or {}
            record['resumeRequested'] = bool(active.get('id') and not active.get('terminal')
                and session.get('status') not in {'stopped', 'stopping', 'interrupted', 'error'})
            if record['resumeRequested']:
                # Capture the authenticated calling input now. Neither a later
                # worker generation nor the completion text creates authority.
                lineage = (self.app.collaboration.lineage(session, origin) if origin not in {'ui', 'user'} else {
                    'sourceGenerationId': active['id'],
                    'sourceInputIds': copy.deepcopy(active.get('inputIds', [])),
                    'sourceInterruptionRevision': session.get('interruptionRevision', 0)})
                record.update(lineage)
                # A UI placement choice does not elevate the running task's
                # peer inputs or reset their retained causal chain.
                record['causalDepth'] = self.app.collaboration.causal_depth(session, lineage, 'agent')
            if record['resumeRequested'] and not manager and not previous:
                from .handoff_admission import worker_instance
                instance = worker_instance(self.app, sid)
                if instance:
                    record['finishingTurn'] = {'workerInstance': instance,
                        'generationId': active['id'], 'inputIds': copy.deepcopy(active.get('inputIds', [])),
                        'configurationRevision': session.get('configurationPendingRevision', 0)}
                    from .collaboration import BINDING, PRINCIPAL
                    native_snapshot = (BINDING.get() or {}).get('_handoffSnapshot')
                    if (origin == 'agent' and PRINCIPAL.get() == (session.get('runtimeSessionId') or sid)
                            and isinstance(native_snapshot, dict)):
                        record['finishingTurn']['nativeDrain'] = copy.deepcopy(native_snapshot)
            if manager:
                source = self.app._session(manager['sourceSessionId'])
                self.app.collaboration.require_inbox_slot(source['id'])
                record['managerPlacement'] = manager
                record['resumeRequested'] = True
                record.update({key: manager[key] for key in
                    ('sourceGenerationId', 'sourceInputIds', 'sourceInterruptionRevision')})
                record['causalDepth'] = self.app.collaboration.causal_depth(source, manager, 'agent')
                record['managerWait'] = self.app.collaboration.saved_wait(source, 'worktree-wait:' + identity)
            self.save(record)
            if manager:
                self.continuation_request(record)
            session['configurationBusy'] = True
            self.changed()
            self.launch(record, copy.deepcopy(session))
            return record

    @staticmethod
    def latest_human(session):
        return next((m['id'] for m in reversed(session.get('messages', []))
                     if m.get('role') == 'user' and m.get('inputOrigin') in {'ui', 'user', 'voice'}), None)

    def authority_changed(self, record, *, continuation=False):
        if reason := manager_placement.guard(self.app, record, continuation=continuation):
            return reason
        session = self.app._session(record['sessionId'])
        if session.get('interruptionRevision', 0) != record.get('interruptionRevision', 0):
            return 'The user stopped this conversation after requesting the move.'
        if self.latest_human(session) != record.get('humanMessageId'):
            return 'New user input superseded the requested continuation.'
        task = session.get('task') or self.app.coordination.task(session['id'])
        if ({key: task.get(key) for key in ('id', 'revision')} != record.get('taskBinding')
                or continuation and not record.get('managerPlacement') and task.get('status') in {'paused', 'blocked', 'completed'}):
            return 'The saved task changed or is no longer active.'

    def continuation_guard(self, request):
        record = self.read(request.get('handoffSessionId', request['senderSessionId']), request['handoffId'])
        reason = self.authority_changed(record, continuation=True)
        if reason: return reason
        if record.get('managerPlacement') and record['phase'] in {'rejected', 'unknown'}:
            return None  # A failure observation does not authorize another move.
        session = self.app._session(record['sessionId'])
        if (record['phase'] != 'applied' or session.get('executionRevision', 0) != record['executionRevision'] + 1
                or session.get('workingDirectory') != record['target']):
            return 'The requested checkout is no longer the committed execution folder.'

    def suppress_continuation(self, record, reason):
        record['continuation'] = {'status': 'suppressed', 'detail': reason}
        self.save(record)
        if record.get('managerPlacement'):
            request = self.continuation_request(record)
            request['subscription'].update(status='suppressed', open=False, reason=reason)
            self.app.collaboration.save(request['requestId'], request)
            self.app.db.commit()

    def enqueue_continuation(self, record):
        if not record.get('resumeRequested') or record.get('continuation', {}).get('status') not in {None, 'waiting', 'backpressured'}:
            return
        reason = self.authority_changed(record, continuation=True)
        if reason:
            self.suppress_continuation(record, reason)
            return
        owner = self.app.collaboration
        request = self.continuation_request(record)
        identity = request['requestId']
        owner.enqueue_continuation(request)
        # Only the inbox's atomic claim establishes an enqueued input.
        record['continuation'] = {'status': request['subscription']['status'], 'requestId': identity}
        self.save(record)

    def continuation_request(self, record):
        owner = self.app.collaboration
        identity = 'worktree-wait:' + record['id']
        existing = self.app.db.execute('SELECT receipt FROM commands WHERE id=?', (identity,)).fetchone()
        if existing:
            request = json.loads(existing[0])
        else:
            from .collaboration import PROTOCOL
            manager = record.get('managerPlacement')
            requester = manager['sourceSessionId'] if manager else record['sessionId']
            session = self.app._session(requester)
            request = {'accepted': True, 'commandAction': 'worktree.handoff',
                'requestId': identity, 'handoffId': record['id'], 'senderSessionId': requester,
                'handoffSessionId': record['sessionId'],
                'target': {'sessionId': record['sessionId']}, 'workspace': record['historyHome'],
                'origin': record['origin'], 'protocol': PROTOCOL, 'causalDepth': record.get('causalDepth', 0),
                **{key: copy.deepcopy(record[key]) for key in
                   ('sourceGenerationId', 'sourceInputIds', 'sourceInterruptionRevision') if key in record},
                'subscription': copy.deepcopy(record['managerWait']) if manager else owner.saved_wait(session, identity),
                'terminalOutcome': {'status': 'observed', 'kind': 'observation', 'outcome': 'checkout-ready',
                    'qualified': False, 'outcomeRevision': 1,
                    'text': 'The requested checkout move completed after the old runtime saved and released its writer. '
                            'Continue the already authorized task using the committed execution folder. '
                            'Prior inputs and tool effects were not replayed. This observation grants no new authority.'}}
            if record['phase'] == 'pending':
                request.pop('terminalOutcome')
            owner.insert(identity, digest(['worktree-wait', record['id']]), request)
            self.app.db.commit()
        if record.get('managerPlacement') and record['phase'] != 'pending':
            request['terminalOutcome'] = {'status': 'observed', 'kind': 'observation',
                'qualified': False, 'outcomeRevision': 1, 'outcome': 'checkout-' + record['phase'],
                'text': f"The commissioned member's checkout placement is {record['phase']}. "
                        f"Read worktree.list for sessionId {record['sessionId']} to inspect the saved handoff "
                        f"{record['id']}. Send its next brief only after confirmed placement. "
                        "No member input was started or replayed."}
            owner.save(identity, request)
        return request

    async def finish(self, record, session):
        placement_attempted = False
        try:
            if record.get('managerPlacement') and record['phase'] in {'rejected', 'unknown'}:
                async with self.app.lock:
                    self.enqueue_continuation(record)
                return
            current = self.app._session(record['sessionId'])
            saved = self.saved_owner(record['sessionId'], durable=True) or {}
            if record['phase'] == 'applied' and (self.placement_committed(record, saved)
                    or saved.get('executionRevision', 0) > record['executionRevision'] + 1):
                async with self.app.lock:
                    if self.placement_committed(record, saved) and self.reconcile_prior(record):
                        current['configurationBusy'] = False
                    reason = self.continuation_guard({'senderSessionId': record['sessionId'], 'handoffId': record['id']})
                    if reason:
                        self.suppress_continuation(record, reason)
                    else:
                        self.enqueue_continuation(record)
                    self.changed()
                return
            released = record.get('step') == 'released' or record.get('release', {}).get('quiesced')
            if not released:
                record['step'] = 'preflight'
                self.save(record)
            # The pending receipt is durable independently of bridge delivery.
            # Quiescence never waits on a model-held command lock.
            await asyncio.sleep(0)
            self.check_host(record)
            self.check_binding(record, session)
            await asyncio.to_thread(self.preflight, record, session)
            if not self.app.runtime or not hasattr(self.app.runtime, 'quiesce_for_handoff'): raise ValueError('This runtime cannot confirm safe handoff')
            boundary = getattr(self.app.runtime, 'handoff_boundary', None)
            if callable(boundary) and not released:
                record['step'] = 'waiting'
                record['detail'] = 'Waiting for the current turn, tool replies and children to finish saving.'
                self.save(record)
                self.changed()
                while True:
                    reason = self.authority_changed(record)
                    if reason: raise HandoffSuperseded(reason)
                    from .handoff_drain import receipt_disposition
                    status = await boundary(record['sessionId'], record['id'], receipt=receipt_disposition(record))
                    if status.get('ready'):
                        if status.get('continuationAllowed') is False and not record.get('managerPlacement'):
                            record['resumeRequested'] = False
                            record['continuation'] = {'status': 'suppressed',
                                'detail': 'The saved goal ended or no settled runtime remains to continue. The move grants no new task budget.'}
                            self.save(record)
                        break
                    await asyncio.sleep(1)
                # The saved boundary may take a whole turn. Recheck actual Git
                # identity after waiting, before releasing the current writer.
                await asyncio.to_thread(self.preflight, record, self.app._session(record['sessionId']))
            # Persist before crossing the effect boundary. A lost release
            # response is uncertain; a failed read-only preflight is not.
            async with self.app.lock:
                current = self.app._session(record['sessionId'])
                self.check_host(current)
                self.check_binding(record, current)
                if (current.get('executionRevision', 0) != record['executionRevision']
                        or current['workspace'] != record['historyHome']
                        or (current.get('workingDirectory') or current['workspace']) != record['source']):
                    raise ValueError('The execution folder changed during handoff')
                reason = self.authority_changed(record)
                if reason: raise HandoffSuperseded(reason)
                if not released:
                    record['step'] = 'releasing'
                    self.save(record)
            evidence = await self.app.runtime.quiesce_for_handoff(session, record['id'])
            self.check_host(evidence)
            if not evidence.get('quiesced'): raise RuntimeError('Runtime release was not confirmed')
            record.update(step='released', release=evidence)
            self.save(record)
            # Release is another asynchronous boundary. A replaced checkout
            # must retain an unresolved receipt rather than become execution.
            await asyncio.to_thread(self.preflight, record, self.app._session(record['sessionId']))
            async with self.app.lock:
                current = self.app._session(record['sessionId'])
                self.check_binding(record, current)
                if current.get('executionRevision', 0) != record['executionRevision']: raise ValueError('The execution folder changed during handoff')
                reason = self.authority_changed(record)
                if reason: raise HandoffSuperseded(reason)
                placement_attempted = True
                current.update(workingDirectory=record['target'], executionHost=copy.deepcopy(record['executionHost']), executionRevision=record['executionRevision']+1, executionHandoffId=record['id'], configurationBusy=False, status='idle')
                current['ownership'] = {'status': 'available'}
                current.pop('lockOwner', None)
                record.update(phase='applied', revision=record['revision']+1, updatedAt=time.time(), release=evidence, detail='Execution folder changed after the saved runtime boundary. History remains in its original home; prior input was not replayed.')
                self.save(record)
                self.changed()
                if not self.placement_committed(record, self.saved_owner(record['sessionId'], durable=True)):
                    raise RuntimeError('Execution placement was not committed to its saved owner')
                self.reconcile_prior(record)
                current['configurationBusy'] = False
                self.enqueue_continuation(record)
                self.changed()
        except BaseException as exc:
            from .runtime import HandoffNotStartedError
            async with self.app.lock:
                try:
                    saved = self.saved_owner(record['sessionId'], durable=True)
                except (OSError, ValueError, sqlite3.Error):
                    saved = None
                if record['phase'] == 'applied' and self.placement_committed(record, saved):
                    record['continuation'] = {'status': 'waiting',
                        'detail': 'The checkout move is confirmed; its retained notification will be reconciled by its original identity. No input was replayed.'}
                elif record['phase'] == 'applied':
                    current = self.app._session(record['sessionId'])
                    if (saved is not None and placement_attempted and current.get('executionHandoffId') == record['id']
                            and current.get('executionRevision') == record['executionRevision'] + 1):
                        # A failed publication must not be committed accidentally
                        # by the error receipt's own projection update.
                        self.restore_placement(current, saved)
                    record.update(phase='unknown', revision=record['revision']+1,
                        detail='Execution placement was not confirmed in its saved owner. Inspect and reconcile; no input was replayed. ' + str(exc)[:1000])
                elif isinstance(exc, asyncio.CancelledError) and record.get('step') == 'released':
                    record['detail'] = 'Host shutdown paused a retained handoff boundary. No input was replayed.'
                elif record.get('step') in {'preflight', 'waiting'} or isinstance(exc, (HandoffNotStartedError, HandoffSuperseded)):
                    record.update(phase='rejected', revision=record['revision']+1,
                        detail=('Handoff was superseded; the source execution folder is unchanged. '
                                if isinstance(exc, HandoffSuperseded) else
                                'Handoff was rejected before runtime release. The source execution folder is unchanged. ') + str(exc)[:1000])
                    current = self.app._session(record['sessionId'])
                    current['configurationBusy'] = False
                    if record.get('release', {}).get('quiesced'):
                        current['ownership'] = {'status': 'available'}
                        current.pop('lockOwner', None)
                else:
                    record.update(phase='unknown', revision=record['revision']+1, detail='Handoff did not reach a confirmed boundary. Inspect and reconcile; no input was replayed. ' + str(exc)[:1000])
                self.save(record)
                if saved is None:
                    # Storage uncertainty cannot prove the candidate placement
                    # failed. Fence from the receipt without publishing a guess
                    # over an owner transaction that may already have committed.
                    self._unverified_placements.add(record['sessionId'])
                    self.sync()
                else:
                    self.changed()
            if isinstance(exc, asyncio.CancelledError): raise
        finally:
            if record.get('managerPlacement') and record['phase'] in {'rejected', 'unknown'}:
                async with self.app.lock:
                    self.enqueue_continuation(record)
            boundary = getattr(self.app.runtime, 'handoff_boundary', None)
            if record['phase'] == 'rejected' and callable(boundary):
                try:
                    await boundary(record['sessionId'], record['id'], cancel=True)
                except (RuntimeError, ValueError, OSError):
                    pass  # The retired worker cannot retain this temporary hook.

    async def close(self):
        for job in list(self.jobs): job.cancel()
        if self.jobs: await asyncio.gather(*self.jobs, return_exceptions=True)
