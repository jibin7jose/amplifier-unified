"""Durable test admission in the host command journal, with exact effect receipts.

The host waits, executes a bounded check and stops its own environment. A model
does not poll peers for a slot. Restart recovery observes admitted operations;
it never replays them. Stopped environments and all evidence remain retained.
"""
from __future__ import annotations

import asyncio
import copy
from contextlib import closing
import json
import logging
import time
import uuid

from . import task_environments as environments
from . import test_environment_profiles as profiles
from . import test_capacity_settings as capacity_settings
from . import registered_test_runners as registered_runners
from . import test_execution_settings as execution_settings
from . import owned_history
from .collaboration import PROTOCOL
from amplifier_operations.coordination import fingerprint

ACTION = 'workspace.tests.submit'
PENDING = {'queued', 'starting', 'checking', 'stopping', 'removing'}
BOUND = ('workspace', 'workingDirectory', 'executionRevision', 'interruptionRevision')
log = logging.getLogger(__name__)


def normalized_binding(values):
    # Cold metadata spells the same defaults explicitly after a host restart.
    # Normalize old persisted receipts too; real moves and Stop revisions differ.
    workspace, directory, execution, interruption = values
    return [workspace, directory or workspace, 0 if execution is None else execution,
            0 if interruption is None else interruption]


def binding(source):
    return normalized_binding([source.get(key) for key in BOUND])


def definitions(schema, string):
    scope = {'workspaceId': string(100), 'sessionId': string(200)}
    return {
        ACTION: ('Queue one isolated check. The host waits for capacity, snapshots the working folder when admitted, runs the command once, stops its environment, exports source and artifacts and removes the owned environment. Source and results are retained; the full temporary workspace follows Storage retention unless keepWorkspace is true. Save useful generated changes to /artifacts. Continue independent work while waiting. Unknown effects never replay. A guarded completion notification can resume this chat.', schema({
            **scope, 'requestId': {**string(200), 'minLength': 1}, 'profileId': {'enum': [row['id'] for row in profiles.catalog()]},
            'command': {**string(16384), 'minLength': 1},
            'timeoutSeconds': {'type': 'integer', 'minimum': 1, 'maximum': profiles.MAX_SECONDS},
            'waitSeconds': {'type': 'integer', 'minimum': 0, 'maximum': profiles.MAX_SECONDS},
            'independent': {'type': 'boolean', 'description': 'Allow this check to overlap other independent checks in this chat within the global environment limit. Use only when no earlier check result is required. Default false preserves ordered execution.'},
            'keepWorkspace': {'type': 'boolean', 'description': 'Keep the full final test workspace. Otherwise new checks follow the saved temporary-workspace policy; export useful generated changes and evidence to /artifacts before the check ends.'},
            'notify': {'type': 'boolean'}}, [*scope, 'requestId', 'profileId', 'command'])),
        'workspace.tests.read': ('Read queued checks and retained outcomes. Full-access roots and their current delegated reviewers can inspect another registered chat in the same workspace, or explicitly use scope=all for another workspace. Use the producer’s sessionId and workspaceId. A recoveredOutcome separately reports exact later cleanup and the verified check result; the original status remains retained. Does not execute or replay a check.', schema({**scope, 'scope': owned_history.SCOPE,
            'offset': {'type': 'integer', 'minimum': 0}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20}}, list(scope))),
        'workspace.tests.cancel': ('Cancel this chat’s waiting check or request prompt termination of its admitted durable guest job. Cancellation is confirmed only after observed termination and owned cleanup. Evidence is retained.', schema({**scope,
            'requestId': string(200)}, [*scope, 'requestId'])),
        'workspace.tests.result': ('Read a bounded page of the exact recorded check output and its retained environment. Full-access roots and their current delegated reviewers can inspect peer evidence using the producer’s sessionId and workspaceId; another workspace requires explicit scope=all. A recoveredOutcome can confirm later cleanup without changing an unknown check into a pass. Never executes or repeats a check.', schema({**scope, 'scope': owned_history.SCOPE,
            'requestId': string(200), 'offset': {'type': 'integer', 'minimum': 0}}, [*scope, 'requestId'])),
    }


class TestQueue:
    __test__ = False

    def __init__(self, app):
        self.app = app
        self.tasks = {}
        self.runner = None
        self.closing = False
        self.admission = asyncio.Lock()
        self.backoff = {}
        self.wakeup = asyncio.Event()
        self.notification_cursor = 0
        self.active_cursor = 0
        self.job_wakeups = {}
        self.poll_seconds = 5
        from .test_reservation_maintenance import ReservationMaintenance
        self.reservations = ReservationMaintenance(app)
        app.db.execute("""CREATE INDEX IF NOT EXISTS workspace_test_queue ON commands(
            json_extract(receipt,'$.job.status'), json_extract(receipt,'$.senderSessionId'))
            WHERE json_extract(receipt,'$.commandAction')='workspace.tests.submit'""")
        app.db.commit()
        app.db.execute("""CREATE INDEX IF NOT EXISTS workspace_test_notifications ON commands(
            json_extract(receipt,'$.subscription.status'))
            WHERE json_extract(receipt,'$.commandAction')='workspace.tests.submit'""")
        app.db.commit()

    def rows(self, *, active=False, sid=None, offset=0, limit=64):
        terms = ["json_extract(receipt,'$.commandAction')=?"]
        params = [ACTION]
        if active:
            terms.append("json_extract(receipt,'$.job.status') IN ('queued','starting','checking','stopping','removing')")
        if sid:
            terms.append("json_extract(receipt,'$.senderSessionId')=?")
            params.append(sid)
        return [json.loads(row[0]) for row in self.app.db.execute(
            'SELECT receipt FROM commands WHERE ' + ' AND '.join(terms) + ' ORDER BY rowid ' + ('ASC' if active else 'DESC') + ' LIMIT ? OFFSET ?',
            (*params, limit, offset))]

    def pending_count(self, *, preparing_backend=None, sid=None):
        clause = "json_extract(receipt,'$.job.status') IN ('queued','starting','checking','stopping','removing')"
        params = [ACTION]
        if preparing_backend is not None:
            clause = "json_extract(receipt,'$.job.status')='starting' AND json_extract(receipt,'$.job.backendKey')=?"
            params.append(preparing_backend)
        if sid is not None:
            clause += " AND json_extract(receipt,'$.senderSessionId')=?"
            params.append(sid)
        return self.app.db.execute("SELECT COUNT(*) FROM commands WHERE json_extract(receipt,'$.commandAction')=? AND " + clause, params).fetchone()[0]

    def chat_available(self, receipt):
        """Ordered barriers plus explicit independent work; retained owners count once.

        Target selection never changes the per-chat budget. The allocator repeats
        the capacity check atomically across queue and manual environment actions.
        """
        identity, sid = receipt['requestId'], receipt['senderSessionId']
        independent = receipt['job'].get('independent') is True
        limits = execution_settings.concurrency(self.app)
        limit = limits['maxEnvironmentsPerChat']
        entries = self.app.db.execute("""SELECT id,coalesce(json_extract(receipt,'$.job.attempt'),0),
            coalesce(json_extract(receipt,'$.job.independent'),0) FROM commands
            WHERE json_extract(receipt,'$.commandAction')=?
            AND json_extract(receipt,'$.senderSessionId')=? AND id!=?
            AND json_extract(receipt,'$.job.status') IN ('queued','starting','checking','stopping','removing')
            AND (rowid<(SELECT rowid FROM commands WHERE id=?)
                 OR json_extract(receipt,'$.job.status')!='queued')""",
            (ACTION, sid, identity, identity)).fetchall()
        earlier = [{'requestId': item[0], 'attempt': item[1], 'independent': item[2] == 1} for item in entries]
        reason = None
        if earlier and (not independent or any(not row['independent'] for row in earlier)):
            reason = 'Waiting for an earlier check in this chat to finish and clean up.'
        with closing(environments.storage.connect(self.app.data_dir)) as db:
            owned = [dict(zip(('id', 'commandId', 'backendAdmitted', 'environmentState', 'pendingRequest', 'preparationPending'), row)) for row in db.execute("""SELECT id,json_extract(body,'$.commandId'),json_extract(body,'$.backendAdmitted'),
                json_extract(body,'$.environmentState'),json_extract(body,'$.pendingRequest'),json_extract(body,'$.preparationPending') FROM allocations WHERE root=?
                AND status!='retired' AND json_extract(body,'$.kind')=?""", (sid, environments.KIND))]
            total, root_total = db.execute("SELECT COUNT(*), coalesce(sum(root=?),0) FROM allocations WHERE status!='retired'", (sid,)).fetchone()
        command = identity+':start:'+str(receipt['job'].get('attempt', 0))
        own = [row for row in owned if row.get('commandId') == command and not row.get('backendAdmitted')]
        other_owners = [row for row in owned if row not in own]
        # Stopped files still consume storage and count against explicit owner
        # ceilings, but do not serialize new checks behind failed preservation.
        # Pending operations/preparation and live or unknown owners still wait.
        ordering_owners = [row for row in other_owners if row['environmentState'] != 'stopped'
                           or row['pendingRequest'] or row['preparationPending']]
        # Earlier queued checks reserve FIFO positions. Once allocated, count
        # the same command only once, even before its resourceId is linked.
        occupants = {row.get('commandId', row['id']) for row in other_owners}
        occupants.update(row['requestId']+':start:'+str(row['attempt']) for row in earlier)
        # A terminal queue receipt can still own a live/unknown environment.
        # Preserve its ordinary ordering barrier until confirmed stopped;
        # overlap requires the original check, not only the new one, to opt in.
        if reason is None and independent and ordering_owners:
            barrier = self.app.db.execute("""SELECT 1 FROM commands
                WHERE json_extract(receipt,'$.commandAction')=?
                AND json_extract(receipt,'$.senderSessionId')=? AND id!=?
                AND coalesce(json_extract(receipt,'$.job.independent'),0)!=1
                AND id||':start:'||coalesce(json_extract(receipt,'$.job.attempt'),0)
                    IN (SELECT value FROM json_each(?)) LIMIT 1""",
                (ACTION, sid, identity, json.dumps([row.get('commandId') for row in ordering_owners]))).fetchone()
            if barrier:
                reason = 'Waiting for an earlier check in this chat to finish and clean up.'
        if reason is None and ordering_owners and not independent:
            reason = 'Waiting for this chat’s previous test environment cleanup to be confirmed.'
        if reason is None and not own and limit and len(occupants) >= limit:
            reason = 'Waiting for this chat’s test environment capacity and cleanup to be confirmed.'
        if reason is None and not own and ((limits['maxOwnedResources'] and total >= limits['maxOwnedResources']) or (limits['maxOwnedResourcesPerChat'] and root_total >= limits['maxOwnedResourcesPerChat'])):
            reason = 'Waiting for retained resource owner capacity. Inspect retained resources or adjust Advanced execution limits in Settings.'
        if reason:
            if receipt['job'].get('detail') != reason:
                receipt['job']['detail'] = reason
                self.save(receipt)
            return False
        return True

    def active_page(self):
        # Rotate bounded keyset pages. Waiting old requests cannot hide work
        # beyond the first page, and deletion/status changes cannot skip rows.
        rows = self.app.db.execute("""SELECT rowid,receipt FROM commands
            WHERE json_extract(receipt,'$.commandAction')=?
            AND json_extract(receipt,'$.job.status') IN ('queued','starting','checking','stopping','removing')
            AND rowid>? ORDER BY rowid LIMIT 64""", (ACTION, self.active_cursor)).fetchall()
        if not rows and self.active_cursor:
            self.active_cursor = 0
            return self.active_page()
        self.active_cursor = rows[-1][0] if rows else 0
        return [json.loads(row[1]) for row in rows]

    def identity(self, sid, request):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, 'workspace-test:' + sid + ':' + request))

    def get(self, identity):
        row = self.app.collaboration.receipt(identity)
        if row.get('commandAction') != ACTION:
            self.app.collaboration.error('Queued test not found.', 404)
        return row

    def save(self, receipt):
        """Publish queue state while the caller holds the app writer lock."""
        receipt['job']['updatedAt'] = time.time()
        self.app.collaboration.save(receipt['requestId'], receipt)
        self.app.db.commit()

    def public(self, receipt):
        job = receipt['job']
        value = {key: copy.deepcopy(job[key]) for key in ('id', 'status', 'createdAt', 'updatedAt', 'expiresAt',
            'resourceId', 'independent', 'workspaceArchivePolicy', 'capacity', 'testMachine', 'detail', 'cancelRequested', 'source', 'timeoutSeconds', 'profileId', 'resourceRequest', 'timingRequest', 'guestJob') if key in job}
        # Full output lives in the existing exact operation receipt. Queue lists
        # cannot multiply a 32 KiB command result by the page size.
        for phase in ('launch', 'check', 'cleanup', 'removal'):
            if phase in job:
                result = job[phase]
                value[phase] = {key: result[key] for key in ('requestId', 'state', 'message') if key in result}
        value['notification'] = receipt['subscription']['status']
        from .outcome_acknowledgement import reference
        review = reference(receipt)
        if review is not None:
            value['notificationReview'] = review
        from .test_queue_recovery import outcome
        recovered = outcome(self.app, receipt)
        if recovered is not None:
            value['recoveredOutcome'] = recovered
        return value

    def summary(self, request):
        return {key: request[key] for key in ('requestId', 'state', 'message') if key in request}

    def guard(self, receipt):
        job = receipt['job']
        if job.get('cancelRequested'):
            raise ValueError('The queued check was cancelled; no new check will start.')
        if job['status'] == 'queued' and job.get('expiresAt') is not None and job['expiresAt'] <= time.time():
            raise ValueError('The saved check expired while waiting; no environment was started.')
        source = self.app._session(receipt['senderSessionId'], hydrate=False)
        if normalized_binding(job['binding']) != binding(source):
            raise ValueError('The conversation stopped or moved; its queued check will not start.')
        if self.app.assignments.blocked(source, receipt.get('assignmentId')):
            raise ValueError('The assignment stopped; its queued check will not start.')
        self.app.configuration_authority.check(job.get('authority'))
        config, _, _ = environments.configuration(self.app)
        retained = registered_runners.resolve_job(self.app, job, config) if job.get('runnerCandidates') else None
        if retained and (job.get('targetBound') or retained.get('originSnapshotVersion') == 1):
            config = retained
        elif not config or environments.config_key(config) != job['backendKey']:
            raise ValueError('The approved test backend changed; this request will not start on another backend.')
        capacity_settings.load(self.app)
        return config

    def continuation_guard(self, receipt):
        if receipt['job'].get('cancelRequested'):
            return 'The queued check was cancelled; its result remains available.'
        source = self.app._session(receipt['senderSessionId'], hydrate=False)
        if normalized_binding(receipt['job']['binding']) != binding(source):
            return 'The conversation stopped or moved after this check was requested.'
        return None

    def retire_unstarted_allocation(self, receipt):
        """Settle only the initial local journal commit, before any effect intent.

        The common environment lock fences effect(start). Its first local effect
        advances revision/status before touching storage or transferring source.
        Anything beyond that boundary remains owned for normal reconciliation.
        """
        job, sid = receipt['job'], receipt['senderSessionId']
        command = receipt['requestId']+':start:'+str(job.get('attempt', 0))
        with closing(environments.storage.connect(self.app.data_dir)) as db:
            found = db.execute("""SELECT id FROM allocations WHERE root=? AND status='prepared'
                AND json_extract(body,'$.kind')=? AND json_extract(body,'$.commandId')=?""",
                (sid, environments.KIND, command)).fetchone()
            if not found:
                return
            lock = environments.FileLock(str(self.app.data_dir / 'workspace-resources' / ('environment-'+found[0]+'.lock')), timeout=0)
            try:
                with lock, db:
                    db.execute('BEGIN IMMEDIATE')
                    row = environments.storage.get(db, sid, found[0])
                    workspace = next((w['id'] for w in self.app.state['workspaces'] if w['path'] == receipt['workspace']), None)
                    expected = {'commandId': command, 'owner': job['actor'], 'source': job['source'],
                        'workspaceId': workspace, 'backendKey': job['backendKey'], 'profileId': job['profileId'],
                        'resourceRequest': job.get('resourceRequest'), 'timingRequest': job.get('timingRequest'),
                        'revision': 1, 'status': 'prepared', 'environmentState': 'preparing'}
                    if any(row.get(key) != value for key, value in expected.items()):
                        return
                    if any(key in row for key in ('directoryIdentity', 'pendingRequest', 'backendAdmitted',
                            'preparationPending', 'preparedDependencies', 'snapshot', 'universeId', 'hostReservation')):
                        return
                    journal = self.app.operations.requests.journal
                    with journal.lock:
                        if journal.db.execute('SELECT 1 FROM operation_requests WHERE session_id=? AND id=?', (sid, command)).fetchone():
                            return
                        environments.storage.save(db, {**row, 'revision': 2, 'status': 'retired',
                            'environmentState': 'not-started'})
            except environments.LockTimeout:
                return  # An active owner must settle its own work.

    def finish(self, receipt, status, detail):
        if receipt['job']['status'] == 'queued' and not receipt['job'].get('resourceId'):
            self.retire_unstarted_allocation(receipt)
        receipt['job'].update(status=status, detail=detail)
        receipt['terminalOutcome'] = {'status': 'observed', 'kind': 'test', 'outcome': status,
            'qualified': False, 'text': detail + ' Inspect workspace.tests.read and the retained environment result. This is check evidence, not proof of product acceptance.',
            'outcomeRevision': 1}
        self.save(receipt)
        self.backoff.clear()
        self.wakeup.set()
        if receipt['subscription']['status'] == 'waiting':
            try:
                self.app.collaboration.enqueue_continuation(receipt)
            except Exception:
                # Retained results and notifications have separate commit
                # boundaries. tick retries delivery without replaying the test.
                pass

    async def dispatch(self, action, args, origin, caller, authority):
        owner = self.app.collaboration
        reading = action in {'workspace.tests.read', 'workspace.tests.result'}
        source = (owner.result_source(args, origin, caller) if reading
                  else owner.source(args, origin, caller, hydrate=False))
        if source['id'] != args['sessionId']:
            if not reading or not self.app.access_mode.full(source):
                owner.error('A queued check belongs to its calling root conversation.')
            source = owned_history.require(self.app, source['id'], args['sessionId'], owned_history.selected(args))
        workspace = next((w for w in self.app.state['workspaces'] if w['id'] == args['workspaceId']), None)
        if not workspace or workspace['path'] != source['workspace']:
            owner.error('Choose this conversation’s current workspace.')
        if action.endswith('.read'):
            size, offset = args.get('limit', 10), args.get('offset', 0)
            rows = self.rows(sid=source['id'], offset=offset, limit=size+1)
            return {'accepted': True, 'result': {'items': [self.public(r) for r in rows[:size]],
                'nextOffset': offset+size if len(rows)>size else None}}
        identity = self.identity(source['id'], args['requestId'])
        if action.endswith('.result'):
            receipt = self.get(identity)
            try:
                request = self.app.operations.requests.read(source['id'], identity+':execute')
            except ValueError:
                request = {'state': 'not-started'}
            output = request.get('result', {}).get('result', {})
            content = output.get('stdout', '') + ('\n'+output['stderr'] if output.get('stderr') else '')
            offset = args.get('offset', 0)
            row = environments.get(self.app.data_dir, source['id'], receipt['job']['resourceId']) if receipt['job'].get('resourceId') else None
            # Budget the encoded text, including JSON escaping and surrogate
            # pairs, leaving room for the operation and app_control envelope.
            page = content[offset:offset+6000]
            low, high = 0, len(page)
            while low < high:
                middle = (low+high+1)//2
                if len(json.dumps(page[:middle]).encode('utf-8')) <= 12000:
                    low = middle
                else:
                    high = middle-1
            page = page[:low]
            return {'accepted': True, 'result': {'check': self.public(receipt), 'operation': self.summary(request),
                'exitCode': output.get('exit_code'), 'text': page, 'offset': offset,
                'nextOffset': offset+len(page) if offset+len(page)<len(content) else None,
                'truncatedAtSource': bool(output.get('truncated') or output.get('stdout_truncated') or output.get('stderr_truncated')),
                'retention': environments.public(self.app.data_dir, row)['retention'] if row else None}}
        if action.endswith('.cancel'):
            row = None
            async with self.app.lock:
                if origin not in {'ui', 'user'}:
                    owner.active_binding(owner.source(args, origin, caller, hydrate=False))
                receipt = self.get(identity)
                if receipt['job']['status'] not in PENDING:
                    return {'accepted': True, 'result': self.public(receipt)}
                receipt['job']['cancelRequested'] = True
                receipt['subscription'].update(status='suppressed', open=False)
                if receipt['job']['status'] == 'queued':
                    self.finish(receipt, 'cancelled', 'Cancelled before environment admission. No check was started.')
                else:
                    self.save(receipt)
                    if receipt['job']['status'] == 'starting' and receipt['job'].get('resourceId'):
                        row = environments.get(self.app.data_dir, source['id'], receipt['job']['resourceId'])
                self.wakeup.set()
                self.wake_job(identity)
            if row is not None:
                await asyncio.to_thread(environments.cancel_preparation, self.app, row)
            return {'accepted': True, 'result': self.public(receipt)}
        async with self.app.lock:
            digest = fingerprint([ACTION, args, source['id'], origin])
            previous = self.app.db.execute('SELECT fingerprint,receipt FROM commands WHERE id=?', (identity,)).fetchone()
            if previous:
                if previous[0] != digest:
                    owner.error('This test request ID already has different arguments.', 409)
                return {'accepted': True, 'duplicate': True, 'result': self.public(json.loads(previous[1]))}
            if origin not in {'ui', 'user'}:
                owner.active_binding(source)
            if self.app.assignments.blocked(source):
                owner.error('This assignment is stopped.', 409)
            self.app.configuration_authority.check(authority)
            queue_policy, resource_request, timing_request = execution_settings.accepted(self.app, args['profileId'])
            seconds = profiles.validate_timeout(args['profileId'], args.get('timeoutSeconds'), timing_request)
            wait_seconds = args.get('waitSeconds', queue_policy['defaultWaitSeconds'])
            config, _, reason = environments.configuration(self.app)
            if not config:
                owner.error(reason, 409)
            if queue_policy['maxPendingPerChat'] and self.pending_count(sid=source['id']) >= queue_policy['maxPendingPerChat']:
                owner.error('This chat’s pending check limit has been reached. Inspect its checks or adjust Advanced execution limits in Settings.', 429)
            if queue_policy['maxPending'] and self.pending_count() >= queue_policy['maxPending']:
                owner.error('The saved queue limit has been reached. Inspect pending checks or adjust Advanced execution limits in Settings.', 429)
            notify = args.get('notify', True)
            if notify:
                owner.require_continuation()
                owner.require_inbox_slot(source['id'])
            lineage = owner.lineage(source, origin)
            now = time.time()
            receipt = {'accepted': True, 'commandAction': ACTION, 'requestId': identity,
                'senderSessionId': source['id'], 'target': {'sessionId': source['id']},
                'workspace': source['workspace'], 'protocol': PROTOCOL, 'origin': origin, **lineage,
                'causalDepth': owner.causal_depth(source, lineage, origin),
                'assignmentId': source.get('assignmentId'),
                'subscription': owner.saved_wait(source, identity, status='waiting' if notify else 'disabled'),
                'job': {'id': args['requestId'], 'independent': args.get('independent', False), 'status': 'queued', 'createdAt': now, 'updatedAt': now,
                    'expiresAt': now + wait_seconds if wait_seconds else None,
                    'source': source.get('workingDirectory') or source['workspace'],
                    'binding': binding(source), 'authority': authority,
                    'backendKey': environments.config_key(config), 'command': args['command'],
                    'timeoutSeconds': seconds, 'profileId': args['profileId'], 'resourceRequest': resource_request, 'timingRequest': timing_request, 'durableVersion': 1,
                    'actor': {'sessionId': source['id'],
                        'principal': source.get('runtimeSessionId') or source.get('nativeIdentity') or source['id']}}}
            from . import storage_cleanup, workspace_archive_retention
            receipt['job']['workspaceArchivePolicy'] = workspace_archive_retention.capture(storage_cleanup.policy(self.app), args.get('keepWorkspace', False))
            receipt['job']['runnerCandidates'] = registered_runners.retain_candidates(self.app, config, args['profileId'])
            owner.insert(identity, digest, receipt)
            self.app.db.commit()
        self.start()
        return {'accepted': True, 'result': self.public(receipt)}

    def start(self):
        if (self.runner is None or self.runner.done()) and not self.closing:
            self.runner = asyncio.create_task(self.loop())

    async def close(self):
        self.closing = True
        if self.runner:
            self.runner.cancel()
            await asyncio.gather(self.runner, return_exceptions=True)
        # An admitted thread retains its own operation receipt; cancellation of
        # the host coroutine must not invent confirmation or restart the effect.
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)

    async def loop(self):
        try:
            while not self.closing:
                try:
                    await self.tick()
                except Exception:
                    # A temporary journal/backend failure cannot silently kill
                    # durable waiting work. Exact operation receipts protect
                    # recovery from repeating any admitted effect.
                    log.exception('Queued test scheduler could not reconcile this pass')
                try:
                    await asyncio.wait_for(self.wakeup.wait(), timeout=5)
                    self.wakeup.clear()
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            pass

    async def tick(self):
        await self.reservations.tick()
        for identity, task in list(self.tasks.items()):
            if task.done():
                # Consume exceptions and release completed job working sets.
                if not task.cancelled():
                    task.exception()
                self.tasks.pop(identity, None)
        from . import test_runner_enrollment
        test_runner_enrollment.schedule(self)
        async with self.app.lock:
            notifications = self.app.db.execute("""SELECT rowid,receipt FROM commands
                    WHERE json_extract(receipt,'$.commandAction')='workspace.tests.submit'
                    AND json_extract(receipt,'$.terminalOutcome.status')='observed'
                    AND json_extract(receipt,'$.subscription.status') IN ('waiting','backpressured')
                    AND rowid>? ORDER BY rowid LIMIT 64""", (self.notification_cursor,)).fetchall()
            self.notification_cursor = notifications[-1][0] if notifications else 0
            for _, encoded in notifications:
                try:
                    self.app.collaboration.enqueue_continuation(json.loads(encoded))
                except Exception:
                    # The saved result survives a temporarily unavailable inbox.
                    pass
        from .test_runner_routing import RoutingPass
        starting = self.app.db.execute("""SELECT id,json_extract(receipt,'$.job.backendKey'),
            json_extract(receipt,'$.senderSessionId'),json_extract(receipt,'$.job.resourceId'),
            coalesce(json_extract(receipt,'$.job.attempt'),0) FROM commands
            WHERE json_extract(receipt,'$.commandAction')=?
            AND json_extract(receipt,'$.job.status')='starting'""", (ACTION,)).fetchall()
        with closing(environments.storage.connect(self.app.data_dir)) as db:
            admitted = set(db.execute("""SELECT root,id,json_extract(body,'$.backendKey'),
                json_extract(body,'$.commandId') FROM allocations WHERE status!='retired'
                AND json_extract(body,'$.kind')=? AND json_extract(body,'$.backendAdmitted')=1""", (environments.KIND,)))
        preparing, unadmitted = {}, {}
        for identity, key, sid, resource, attempt in starting:
            preparing[key] = preparing.get(key, 0) + 1
            # A delayed start acknowledgment must not add a second retained
            # slot for its already admitted owner. Missing/mismatched ownership
            # remains conservative; final native admission still checks atomically.
            if (sid, resource, key, identity + ':start:' + str(attempt)) not in admitted:
                unadmitted[key] = unadmitted.get(key, 0) + 1
        routing = RoutingPass(preparing, unadmitted)
        capacities = {}
        runner_observations = {}
        for receipt in self.active_page():
            identity = receipt['requestId']
            task = self.tasks.get(identity)
            if task and not task.done():
                continue
            if task:
                self.tasks.pop(identity)
            if receipt['job']['status'] != 'queued':
                self.tasks[identity] = asyncio.create_task(self.recover(identity))
                continue
            async with self.app.lock:
                receipt = self.get(identity)
                if receipt['job']['status'] != 'queued':
                    continue
                try:
                    if receipt['job'].get('expiresAt') is not None and receipt['job']['expiresAt'] <= time.time():
                        self.finish(receipt, 'expired', 'The saved check expired while waiting for capacity. No environment was started.')
                        continue
                    config = self.guard(receipt)
                except Exception as exc:
                    self.finish(receipt, 'cancelled', str(exc))
                    continue
                if not self.chat_available(receipt) or self.admission.locked():
                    continue
            choosing = bool(receipt['job'].get('runnerCandidates') and not receipt['job'].get('targetBound'))
            if choosing:
                selected = await asyncio.to_thread(registered_runners.select, self.app, receipt['job'], config, runner_observations, self.backoff, routing)
                async with self.app.lock:
                    receipt = self.get(identity)
                    if receipt['job']['status'] != 'queued' or receipt['job'].get('cancelRequested'):
                        continue
                    if not self.chat_available(receipt):
                        continue
                    if selected is None or not registered_runners.enabled(self.app, selected):
                        receipt['job']['detail'] = 'Waiting for room on an approved test machine.'
                        self.save(receipt)
                        continue
                    self.guard(receipt)
                    config = selected
            key = environments.config_key(config)
            if key not in capacities:
                retry, delay = self.backoff.get(key, (0, 5))
                if retry > time.time():
                    continue
                capacity = runner_observations.get(key)
                if capacity is None:
                    capacity = await asyncio.to_thread(environments.capacity, self.app, config)
                capacities[key] = capacity
                if not capacity.get('available'):
                    self.backoff[key] = (time.time()+delay, min(60, delay*2))
                else:
                    self.backoff.pop(key, None)
            capacity = capacities[key]
            async with self.app.lock:
                receipt = self.get(identity)
                task = self.tasks.get(identity)
                if receipt['job']['status'] != 'queued' or (task and not task.done()):
                    continue
                try:
                    self.guard(receipt)
                except Exception as exc:
                    self.finish(receipt, 'cancelled', str(exc))
                    continue
                if not self.chat_available(receipt):
                    continue
                if choosing and not registered_runners.enabled(self.app, config):
                    continue
                receipt['job']['capacity'] = capacity
                if not routing.room(key, config, capacity, routing.request(receipt['job'])):
                    self.save(receipt)
                    continue
                if choosing:
                    receipt['job'].update(backendKey=key, targetBound=True,
                        testMachine=({name: config['remoteRunner'][name] for name in ('id', 'name', 'machineId')} if config.get('remoteRunner') else None))
                # Bind only when scheduling; later candidates see this pass's
                # planned demand before physical reservations become visible.
                self.save(receipt)
                routing.scheduled(key, config, capacity, routing.request(receipt['job']))
                self.tasks[identity] = asyncio.create_task(self.run(identity, config))

    async def effect(self, receipt, config, action, *, cleanup=False, suffix=None, previous_request=None):
        job, app = receipt['job'], self.app
        row = environments.get(app.data_dir, receipt['senderSessionId'], job['resourceId'])
        if cleanup:
            active, _, _ = environments.configuration(app)
            config = environments.resolve_configuration(app, row, active)
        request_id = receipt['requestId'] + ':' + (suffix or action)
        if action == 'start':
            request_id += ':'+str(job.get('attempt', 0))
        try:
            return app.operations.requests.read(receipt['senderSessionId'], request_id)
        except ValueError:
            pass
        args = {'requestId': request_id, 'expectedRevision': row['revision'],
            'command': job['command'], 'timeoutSeconds': job['timeoutSeconds']}
        if previous_request is not None:
            args['previousRequestId'] = previous_request
        if action == 'execute' and job.get('durableVersion') == 1:
            args['durableJob'] = True
        request, fresh = (environments.begin_removal(app, row, 'workspace.environments.'+action, args, job['actor'])
                          if action in {'remove', 'finishCleanup'} else
                          app.operations.requests.begin(receipt['senderSessionId'],
                              'workspace.environments.'+action, args, job['actor']))
        if not fresh:
            return request
        loop = asyncio.get_running_loop()
        async def checked(capacity=False):
            async with app.lock:
                # Cleanup is part of the original admitted check, even when its
                # waiting authority is revoked. It only stops that exact owner;
                # it never starts work, changes settings or removes evidence.
                if not cleanup:
                    current_config = self.guard(self.get(receipt['requestId']))
                    if capacity:
                        return capacity_settings.apply(app, current_config)
                else:
                    active, _, _ = environments.configuration(app)
                    current = environments.get(app.data_dir, receipt['senderSessionId'], row['id'])
                    if environments.resolve_configuration(app, current, active) != config:
                        raise ValueError('The retained cleanup backend changed.')
        def admit():
            asyncio.run_coroutine_threadsafe(checked(), loop).result(timeout=10)
        def policy_admit():
            return asyncio.run_coroutine_threadsafe(checked(capacity=True), loop).result(timeout=10)
        result = await app.worktrees.finish_thread(environments.effect, app, row, action, args, config, request, admit, policy_admit)
        return result['request']

    async def run(self, identity, config):
        try:
            async with self.admission:
                async with self.app.lock:
                    receipt = self.get(identity)
                    if receipt['job']['status'] != 'queued':
                        return
                    config = self.guard(receipt)
                    if not self.chat_available(receipt):
                        return
                    if receipt['job'].get('targetBound') and not receipt['job'].get('resourceId') and not registered_runners.enabled(self.app, config):
                        receipt['job'].pop('targetBound', None)
                        receipt['job'].pop('testMachine', None)
                        receipt['job']['backendKey'] = receipt['job']['runnerCandidates'][0]
                        receipt['job']['detail'] = 'Waiting for an enabled test machine.'
                        self.save(receipt)
                        return
                    # Capture preparation inputs under the owner lock, then let
                    # local allocation and backend work run without holding it.
                    job = receipt['job']
                    workspace = next(w['id'] for w in self.app.state['workspaces'] if w['path'] == receipt['workspace'])
                row = await asyncio.to_thread(environments.prepare, self.app.data_dir,
                    receipt['senderSessionId'], workspace,
                    job['actor'], job['source'], job['backendKey'], identity+':start:'+str(job.get('attempt', 0)), config.get('installationId'), config, job.get('profileId', environments.PROFILE), job.get('resourceRequest'), job.get('timingRequest'), job.get('workspaceArchivePolicy'))
                async with self.app.lock:
                    # Cancellation may arrive while local allocation is persisted.
                    # Never overwrite the newer cancellation/subscription record.
                    receipt = self.get(identity)
                    job = receipt['job']
                    try:
                        self.guard(receipt)
                    except Exception:
                        environments.save(self.app.data_dir, row, status='retired', environmentState='not-started')
                        job['resourceId'] = row['id']
                        self.finish(receipt, 'cancelled', 'Cancelled before launch. The unused allocation was retired and local evidence retained.')
                        return
                    job.update(resourceId=row['id'], status='starting')
                    self.save(receipt)
            launch = await self.effect(receipt, config, 'start')
            async with self.app.lock:
                receipt = self.get(identity)
                receipt['job']['launch'] = self.summary(launch)
                if launch['state'] != 'completed':
                    if (launch.get('result') or {}).get('admissionDenied'):
                        # Proven refusal before launch is retryable. Retire only
                        # the unused logical allocation; its source stays on disk.
                        row = environments.get(self.app.data_dir, receipt['senderSessionId'], receipt['job']['resourceId'])
                        environments.save(self.app.data_dir, row, status='retired')
                        receipt['job'].pop('resourceId', None)
                        receipt['job']['attempt'] = receipt['job'].get('attempt', 0)+1
                        receipt['job']['status'] = 'queued'
                        if receipt['job'].get('runnerCandidates'):
                            receipt['job'].pop('targetBound', None)
                            receipt['job'].pop('testMachine', None)
                            receipt['job']['backendKey'] = receipt['job']['runnerCandidates'][0]
                        if launch['result'].get('machineResources'):
                            receipt['job']['capacity'] = {'available': False, 'machineResources': launch['result']['machineResources']}
                        self.save(receipt)
                        return
                    if launch['state'] == 'failed':
                        row = environments.get(self.app.data_dir, receipt['senderSessionId'], receipt['job']['resourceId'])
                        if not row.get('backendAdmitted') and not row.get('preparationPending'):
                            environments.save(self.app.data_dir, row, status='retired')
                    if launch['state'] != 'outcome_unknown':
                        self.finish(receipt, 'cancelled' if receipt['job'].get('cancelRequested') else 'failed',
                            'Environment admission did not complete. Inspect its exact saved receipt; it was not retried.')
                        return
            if launch['state'] == 'outcome_unknown':
                await self.recover(identity)
                return
            try:
                async with self.app.lock:
                    receipt = self.get(identity)
                    receipt['job']['launch'] = self.summary(launch)
                    self.guard(receipt)
                    receipt['job']['status'] = 'checking'
                    self.save(receipt)
                check = await self.effect(receipt, config, 'execute')
            except Exception as exc:
                check = {'state': 'cancelled', 'message': str(exc)}
            async with self.app.lock:
                receipt = self.get(identity)
                row = environments.get(self.app.data_dir, receipt['senderSessionId'], receipt['job']['resourceId'])
                durable = receipt['job'].get('durableVersion') == 1 and row.get('durableJob')
                if not durable:
                    receipt['job'].update(check=self.summary(check), status='stopping')
                    self.save(receipt)
            if durable:
                return await self.monitor(identity, config, check)
            await self.cleanup(receipt, config)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception('Queued check lifecycle interrupted; reconciling its recorded effects')
            async with self.app.lock:
                receipt = self.get(identity)
                if not receipt['job'].get('resourceId'):
                    if receipt['job']['status'] == 'queued' and not self.chat_available(receipt):
                        return
                    status = 'failed' if receipt['job']['status'] == 'queued' else 'unknown'
                    self.finish(receipt, status, 'The test lifecycle did not complete: '+str(exc)[:500]+'. No uncertain effect will be replayed.')
                    return
            # A failure between saving an effect and saving the queue's next
            # phase must still reconcile the owned environment outside the lock.
            await self.recover(identity)

    async def cleanup(self, receipt, config, *, recovery=False):
        identity = receipt['requestId']
        async with self.app.lock:
            receipt = self.get(identity)
            row = environments.get(self.app.data_dir, receipt['senderSessionId'], receipt['job']['resourceId'])
        if not row.get('backendAdmitted') or row.get('guestRemoved') or row.get('environmentState') == 'stopped':
            cleanup = {'state': 'completed', 'message': 'Observed no running owned environment.'}
        else:
            cleanup = await self.effect(receipt, config, 'stop', cleanup=True,
                suffix='recovery-stop' if recovery else None)
        async with self.app.lock:
            receipt = self.get(identity)
            receipt['job']['cleanup'] = self.summary(cleanup)
            if cleanup['state'] != 'completed':
                self.finish(receipt, 'unknown', 'Check finished, but stopping its environment was not confirmed. Evidence and ownership are retained; inspect before further cleanup.')
                return
            receipt['job']['status'] = 'removing'
            self.save(receipt)
        removal = await self.effect(receipt, config, 'remove', cleanup=True)
        async with self.app.lock:
            receipt = self.get(identity)
            preserve = (removal['state'] == 'failed' and removal.get('result', {}).get('effectAdmitted') is False
                        and removal['result'].get('phase') in {'preserving', 'verifying'}
                        and removal['result'].get('code') in {'preservation_failed', 'archive_verification_failed'})
            if preserve:
                # A definitive pre-teardown failure permits the separately
                # recorded preservation path, never replay of an unknown effect.
                receipt['job']['previousRemoval'] = self.summary(removal)
                self.save(receipt)
        if preserve:
            removal = await self.effect(receipt, config, 'finishCleanup', cleanup=True,
                previous_request=removal['requestId'])
        async with self.app.lock:
            receipt = self.get(identity)
            receipt['job']['removal'] = self.summary(removal)
            if removal['state'] != 'completed':
                self.finish(receipt, 'unknown', 'The environment is stopped, but evidence export or removal was not confirmed. Its retained files and exact ownership remain available.')
                return
            check = receipt['job'].get('check', {'state': 'outcome_unknown'})
            status = ('cancelled' if receipt['job'].get('cancelRequested') else
                      {'completed': 'passed', 'failed': 'failed', 'cancelled': 'cancelled'}.get(check['state'], 'unknown'))
            self.finish(receipt, status, 'The check outcome is '+status+'. Source, artifacts and operation receipts were retained before the owned environment was removed.')

    def wake_job(self, identity):
        event = self.job_wakeups.get(identity)
        if event is not None:
            event.set()

    async def monitor(self, identity, config, check=None):
        """Reattach observation after restart; only guest admission runs commands."""
        event = self.job_wakeups[identity] = asyncio.Event()
        try:
            return await self._monitor(identity, config, check, event)
        finally:
            if self.job_wakeups.get(identity) is event:
                self.job_wakeups.pop(identity, None)

    async def _monitor(self, identity, config, check, event):
        errors = 0
        while True:
            async with self.app.lock:
                receipt = self.get(identity)
                row = environments.get(self.app.data_dir, receipt['senderSessionId'], receipt['job']['resourceId'])
                observed = check is None or receipt['job'].get('cancelRequested')
            if observed:
                try:
                    check = await asyncio.to_thread(environments.observe_job, self.app, row, config,
                        cancel=bool(receipt['job'].get('cancelRequested')))
                    errors = 0
                except Exception as exc:
                    errors += 1
                    if errors >= 3:
                        async with self.app.lock:
                            self.finish(self.get(identity), 'unknown', 'The durable guest could not be observed: '+str(exc)[:300]+'. Its environment and evidence are preserved; no command or cleanup was replayed.')
                        return
                    await asyncio.sleep(1)
                    continue
            guest = check.get('result', {}).get('job', {})
            if observed and not guest:
                async with self.app.lock:
                    self.finish(self.get(identity), 'unknown', 'The backend returned no durable guest receipt. The environment is preserved; no command or cleanup was repeated.')
                return
            async with self.app.lock:
                receipt = self.get(identity)
                summary = self.summary(check)
                changed = receipt['job'].get('check') != summary
                receipt['job']['check'] = summary
                if guest:
                    compact = {key: guest[key] for key in ('id', 'state', 'terminal', 'startedAt', 'finishedAt', 'cancelRequested', 'reason') if key in guest}
                    changed |= receipt['job'].get('guestJob') != compact
                    receipt['job']['guestJob'] = compact
                if changed:
                    self.save(receipt)
                if guest.get('terminal') is True:
                    receipt['job']['status'] = 'stopping'
                    self.save(receipt)
                elif guest.get('state') == 'unknown':
                    self.finish(receipt, 'unknown', guest.get('reason', 'The exact guest job is unverified.')+' Its environment is preserved; the command and cleanup were not repeated.')
                    return
            if guest.get('terminal') is True:
                await self.cleanup(receipt, config)
                return
            # The start acknowledgement may have been lost after guest commit.
            # Observe the exact reserved identity, never call execute again.
            check = None
            try:
                await asyncio.wait_for(event.wait(), timeout=self.poll_seconds)
                event.clear()
            except TimeoutError:
                pass

    async def recover(self, identity):
        """Reconcile the exact owner and complete cleanup; never rerun the check."""
        try:
            async with self.app.lock:
                receipt = self.get(identity)
                active, _, _ = environments.configuration(self.app)
                row = environments.get(self.app.data_dir, receipt['senderSessionId'], receipt['job']['resourceId'])
                preparing = row.get('guestRemoved') and row.get('preparationPending')
                if preparing:
                    retained = environments.resolve_configuration(self.app, row, active)
            if preparing:
                row = await asyncio.to_thread(environments.resume_preparation_removal, self.app, row, retained)
            async with self.app.lock:
                receipt = self.get(identity)
                row = environments.get(self.app.data_dir, receipt['senderSessionId'], row['id'])
                active, _, _ = environments.configuration(self.app)
                if row['status'] == 'retired':
                    self.restore_operations(receipt)
                    check = receipt['job'].get('check', {})
                    removal = receipt['job'].get('removal', {})
                    status = {'completed': 'passed', 'failed': 'failed'}.get(check.get('state'), 'unknown') if removal.get('state') == 'completed' else 'unknown'
                    self.finish(receipt, 'cancelled' if receipt['job'].get('cancelRequested') else status,
                        'Recovered the recorded check and cleanup results. The owned environment is removed; no command was replayed.')
                    return
                config = environments.resolve_configuration(self.app, row, active)
                durable = (receipt['job'].get('durableVersion') == 1 and row.get('durableJob')
                           and receipt['job']['status'] not in {'stopping', 'removing'})
            if durable:
                await self.monitor(identity, config)
                return
            # read reconciles any prior abandoned pendingRequest and physical
            # state. It never starts an environment or installs source again.
            await asyncio.to_thread(environments.read, self.app, receipt['senderSessionId'], active)
            async with self.app.lock:
                receipt = self.get(identity)
                self.restore_operations(receipt)
                row = environments.get(self.app.data_dir, receipt['senderSessionId'], row['id'])
                if row.get('environmentState') not in {'running', 'stopped', 'unknown', 'not-started'}:
                    raise ValueError('The exact environment has not reached a cleanup boundary.')
                receipt['job'].setdefault('check', {'state': 'outcome_unknown',
                    'message': 'The interrupted lifecycle has no recorded check result; no check command was replayed.'})
                self.save(receipt)
            await self.cleanup(receipt, config, recovery=True)
        except Exception as exc:
            async with self.app.lock:
                self.finish(self.get(identity), 'unknown', 'Recovery could not confirm cleanup: '+str(exc)[:500]+'. All evidence and ownership remain retained; no command was replayed.')

    def restore_operations(self, receipt):
        for phase, operation in (('check', 'execute'), ('cleanup', 'stop'), ('removal', 'remove'), ('removal', 'finishCleanup')):
            try:
                receipt['job'][phase] = self.summary(self.app.operations.requests.read(receipt['senderSessionId'], receipt['requestId']+':'+operation))
            except ValueError:
                pass
