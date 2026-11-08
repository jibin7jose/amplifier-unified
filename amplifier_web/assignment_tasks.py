"""Optional work responsibility in the existing assignment/command receipts."""
from __future__ import annotations

import copy
import base64
import json
import math
import time
import uuid

from amplifier_operations.coordination import fingerprint

LEGACY_MAX_TASKS = 128  # Accepted embedded maps; not a lifetime limit on task records.
RECORD = 'assignment.task.record'
ASSIGNMENT_RECORD = 'assignment.record.v2'
STORAGE = 'records-v1'
PAGE_BYTES = 9 * 1024  # Task summaries only; the retained member/control envelope is separate.
_TERMINAL = "json_extract(receipt,'$.result.status') IN ('completed','cancelled')"
_CREATED = "coalesce(json_extract(receipt,'$.result.createdAt'),0)"
_TASK_ID = "json_extract(receipt,'$.result.id')"


def definitions(schema, string):
    source = {'sessionId': string(200)}
    target = {**source, 'taskId': string(200)}
    revision = {**target, 'expectedRevision': {'type': 'integer', 'minimum': 1}}
    content = {'title': {**string(200), 'minLength': 1}, 'description': string(4000),
               'blockedReason': string(1000),
               'dependsOn': {'type': 'array', 'maxItems': 32, 'uniqueItems': True, 'items': string(200)}}
    claim = {**revision, 'claimId': string(200)}
    page = {**source, 'view': {'enum': ['active', 'history']},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 16}, 'after': string(2048)}
    return {
        'assignment.tasks.list': ('Read a bounded current page of task summaries; active is unfinished work, history is reported complete/cancelled. Follow nextAfter as after; refresh when coverage changed. Exact details and prerequisites use tasks.read. Claims and observed worker activity remain separate; omission is not completion. No execution or history loading.', schema(page, ['sessionId'])),
        'assignment.tasks.wait': ('Passively observe the same task page and visible holder activity; rechecks membership after every wait. Pass the same view/limit/after with afterVersion. No execution or history reads.', schema({**page, 'afterVersion': string(64), 'waitMs': {'type': 'integer', 'minimum': 0, 'maximum': 30000}}, ['sessionId', 'afterVersion'])),
        'assignment.tasks.read': ('Read one task and its current claim authority. A historical claim receipt alone does not authorize completion.', schema(target, ['sessionId', 'taskId'])),
        'assignment.tasks.create': ('Assignment owner creates a task. Dependencies must be existing assignment tasks. Does not commission or start work.', schema({**source, **content, 'expectedAssignmentRevision': {'type': 'integer', 'minimum': 1}}, ['sessionId', 'title', 'expectedAssignmentRevision'])),
        'assignment.tasks.update': ('Owner edits an unclaimed unfinished task, including its block and acyclic dependencies. Completed/cancelled tasks cannot be reopened.', schema({**revision, **content}, ['sessionId', 'taskId', 'expectedRevision'])),
        'assignment.tasks.claim': ('Atomically claim a ready assignment task for your own bound conversation. One concurrent claimant wins. Retain claimId and revision; claiming never starts execution or locks files.', schema(revision, ['sessionId', 'taskId', 'expectedRevision'])),
        'assignment.tasks.release': ('Holder or assignment owner explicitly releases this exact claim. Prior external effects may remain; release does not stop a process or imply safe replay.', schema(claim, ['sessionId', 'taskId', 'expectedRevision', 'claimId'])),
        'assignment.tasks.complete': ('Current holder reports completion of the exact valid claim. This is reported completion, not independently verified artifacts. Dependencies become ready but never execute automatically.', schema({**claim, 'report': {**string(4000), 'minLength': 1}, 'references': {'type': 'array', 'maxItems': 16, 'items': string(2000)}}, ['sessionId', 'taskId', 'expectedRevision', 'claimId', 'report'])),
        'assignment.tasks.cancel': ('Owner cancels an unfinished work item, preserving its claim and prior transition receipts. This does not stop live execution.', schema(revision, ['sessionId', 'taskId', 'expectedRevision'])),
        'assignment.tasks.reassign': ('Owner explicitly replaces responsibility with a new claim for a current member. Fences the old claim; does not stop prior effects or start new work. Inspect uncertain effects first.', schema({**revision, 'holderSessionId': string(200)}, ['sessionId', 'taskId', 'expectedRevision', 'holderSessionId'])),
    }


class AssignmentTasks:
    def __init__(self, assignments):
        self.assignments = assignments
        self.service = assignments.service
        self.collaboration = self.service.collaboration
        # Derived query support, never a second task/claim authority.
        self.service.db.execute("""CREATE INDEX IF NOT EXISTS assignment_task_claims ON commands(
            json_extract(receipt,'$.assignmentId'), json_extract(receipt,'$.result.claim.sessionId'))
            WHERE json_extract(receipt,'$.commandAction')='assignment.task.record'
            AND json_extract(receipt,'$.result.claim.sessionId') IS NOT NULL
            AND json_extract(receipt,'$.result.status') NOT IN ('completed','cancelled')""")
        self.service.db.execute(f"""CREATE INDEX IF NOT EXISTS assignment_task_pages ON commands(
            json_extract(receipt,'$.assignmentId'), ({_TERMINAL}), {_CREATED}, {_TASK_ID})
            WHERE json_extract(receipt,'$.commandAction')='assignment.task.record'""")

    def error(self, detail, status=409):
        self.collaboration.error(detail, status)

    @staticmethod
    def key(identity):
        return 'assignment-task-record:' + identity

    def _record(self, value, key, encoded):
        receipt = json.loads(encoded)
        task = receipt.get('result')
        if (receipt.get('commandAction') != RECORD or not isinstance(receipt.get('assignmentId'), str)
                or not isinstance(task, dict) or not isinstance(task.get('id'), str)
                or self.key(task['id']) != key):
            self.error('The assignment task record is unavailable.')
        # A well-formed item outside the requested assignment is absent in
        # that scope, as it was in the embedded map. Never expose its body.
        return task if receipt['assignmentId'] == value['id'] else None

    def get(self, value, identity):
        if not value:
            return None
        if value.get('taskStorage') != STORAGE:
            return copy.deepcopy(value.get('tasks', {}).get(identity))
        key = self.key(identity)
        row = self.service.db.execute('SELECT receipt FROM commands WHERE id=?', (key,)).fetchone()
        return self._record(value, key, row[0]) if row else None

    def unfinished_claims(self, value, holder=None):
        if value.get('taskStorage') != STORAGE:
            return [task for task in value.get('tasks', {}).values()
                    if task.get('claim') and task.get('status') not in {'completed', 'cancelled'}
                    and (holder is None or task['claim'].get('sessionId') == holder)]
        query = """SELECT id,receipt FROM commands INDEXED BY assignment_task_claims
            WHERE json_extract(receipt,'$.commandAction')='assignment.task.record'
            AND json_extract(receipt,'$.result.claim.sessionId') IS NOT NULL
            AND json_extract(receipt,'$.result.status') NOT IN ('completed','cancelled')
            AND json_extract(receipt,'$.assignmentId')=?"""
        args = [value['id']]
        if holder is not None:
            query += " AND json_extract(receipt,'$.result.claim.sessionId')=?"
            args.append(holder)
        query += ' ORDER BY rowid'
        return [self._record(value, key, encoded) for key, encoded in self.service.db.execute(query, args)]

    def save(self, value, task, *, new=False):
        key = self.key(task['id'])
        receipt = {'accepted': True, 'commandAction': RECORD,
                   'assignmentId': value['id'], 'result': task}
        if new:
            if self.service.db.execute('SELECT 1 FROM commands WHERE id=?', (key,)).fetchone():
                self.error('The assignment task record key is already in use.')
            self.collaboration.insert(key, fingerprint([RECORD, value['id'], task['id']]), receipt)
        else:
            if self.get(value, task['id']) is None:
                self.error('The assignment task record is unavailable.')
            self.collaboration.save(key, receipt)

    def convert(self, value):
        """Lazy conversion inside the caller's existing mutation transaction."""
        if value.get('taskStorage') == STORAGE:
            return
        tasks = value.get('tasks', {})
        if not isinstance(tasks, dict) or len(tasks) > LEGACY_MAX_TASKS:
            self.error('The legacy assignment task map is unavailable for bounded conversion.')
        for identity, task in tasks.items():
            if (not isinstance(identity, str) or not identity or not isinstance(task, dict)
                    or task.get('id') != identity or type(task.get('revision')) is not int
                    or task['revision'] < 1 or task.get('status') not in {'open', 'claimed', 'completed', 'cancelled'}
                    or not isinstance(task.get('dependsOn', []), list)
                    or len(task.get('dependsOn', [])) > 32
                    or any(not isinstance(key, str) or key not in tasks for key in task.get('dependsOn', []))):
                self.error('The legacy assignment task map is malformed; it was not converted.')
            if self.service.db.execute('SELECT 1 FROM commands WHERE id=?', (self.key(identity),)).fetchone():
                self.error('The assignment task record key is already in use.')
        for task in tasks.values():
            self.save(value, task, new=True)
        value.pop('tasks', None)
        value['taskStorage'] = STORAGE

    def member(self, value, sid, *, lookup=None):
        if value['members'].get(sid, {}).get('status') != 'bound':
            return None
        try:
            row = lookup(sid) if lookup else self.service._session(sid, hydrate=False)
        except Exception:
            return None
        if row is None:
            return None
        identity = row.get('assignmentId') or (row.get('collaboration') or {}).get('assignmentId')
        return row if identity == value['id'] else None

    def claim_status(self, value, task, *, lookup=None):
        claim = task.get('claim')
        if not claim:
            return None
        status, detail = 'current', 'Responsibility is retained; no execution is implied.'
        member = value['members'].get(claim['sessionId'], {})
        holder = self.member(value, claim['sessionId'], lookup=lookup)
        if value['status'] != 'active':
            status, detail = 'stale', 'The assignment was stopped. Inspect prior effects before explicitly releasing this claim.'
        elif member.get('status') != 'bound':
            status, detail = 'stale', 'The holder is no longer an assignment member. Explicit owner reconciliation is required.'
        elif holder is None:
            status, detail = 'unavailable', 'Current holder authority is unavailable. The task remains claimed.'
        elif holder.get('interruptionRevision', 0) != claim['interruptionRevision']:
            status, detail = 'stale', 'The holder was explicitly stopped after this claim. Release or reassign explicitly before continuing.'
        return {**claim, 'status': status, 'detail': detail}

    def readiness(self, value, task, *, tasks=None):
        blocked = [identity for identity in task.get('dependsOn', [])
                   if ((tasks.get(identity) if tasks is not None else self.get(value, identity)) or {}).get('status') != 'completed']
        return ('blocked' if blocked or task.get('blockedReason') else 'ready'), blocked

    def worker(self, value, task):
        """Observed holder state, never an implicit ownership transition."""
        claim = task.get('claim')
        if not claim or task.get('status') in {'completed', 'cancelled'}:
            return None
        holder = self.member(value, claim['sessionId'])
        if holder is None:
            return {'sessionId': claim['sessionId'], 'status': 'unavailable',
                    'label': 'Unavailable', 'attention': True, 'blockedReason': ''}
        from .navigation_summary import activity
        saved_task = holder.get('task') or self.service.coordination.task(holder['id'])
        status = holder.get('status', 'idle')
        facts = activity(holder, blocked=saved_task.get('status') == 'blocked')
        label = facts['label']
        if facts['kind'] == 'idle':
            label = {'paused': 'Task paused', 'completed': 'Task complete'}.get(saved_task.get('status'),
                {'stopped': 'Stopped', 'interrupted': 'Interrupted', 'cancelled': 'Cancelled'}.get(status, label))
        return {'sessionId': holder['id'], 'status': status, 'taskStatus': saved_task.get('status'),
                'label': label, 'attention': facts['kind'] == 'attention',
                'blockedReason': (saved_task.get('blockedReason') or '')[:1000]
                    if saved_task.get('status') == 'blocked' else ''}

    def project(self, value, task, *, tasks=None):
        readiness, blocked = self.readiness(value, task, tasks=tasks)
        return {**copy.deepcopy(task), 'readiness': readiness, 'blockedBy': blocked,
                'claim': self.claim_status(value, task), 'worker': self.worker(value, task)}

    def _page_rows(self, value, view, after, limit):
        terminal = view == 'history'
        if value.get('taskStorage') != STORAGE:
            # Legacy maps remain bounded and passive reads do not convert them.
            tasks = sorted(value.get('tasks', {}).values(), key=self._order)
            return [task for task in tasks
                    if (task['status'] in {'completed', 'cancelled'}) == terminal
                    and (after is None or self._order(task) > after)][:limit + 1]
        query = f"""SELECT id,receipt FROM commands INDEXED BY assignment_task_pages
            WHERE json_extract(receipt,'$.commandAction')='assignment.task.record'
            AND json_extract(receipt,'$.assignmentId')=? AND ({_TERMINAL})=?"""
        args = [value['id'], terminal]
        if after is not None:
            query += f' AND ({_CREATED}, {_TASK_ID}) > (?,?)'
            args.extend(after)
        query += f' ORDER BY {_CREATED}, {_TASK_ID} LIMIT ?'
        args.append(limit + 1)
        return [self._record(value, key, encoded) for key, encoded in self.service.db.execute(query, args)]

    @staticmethod
    def _order(task):
        return task.get('createdAt') or 0, task['id']

    def _after(self, token, scope, revision):
        if not token:
            return None, revision
        try:
            if not isinstance(token, str) or len(token) > 2048:
                raise ValueError()
            version, saved_scope, first_revision, created, identity = json.loads(
                base64.b64decode(token, altchars=b'-_', validate=True))
            if (version != 1 or saved_scope != scope or type(first_revision) is not int
                    or first_revision < 1 or type(created) not in (int, float)
                    or not math.isfinite(created) or not isinstance(identity, str) or not identity):
                raise ValueError()
            return (created, identity), first_revision
        except (ValueError, TypeError, UnicodeError, OverflowError):
            self.error('This task cursor is invalid or belongs to another page. Refresh tasks.', 409)

    def _summary(self, value, task, sid):
        readiness, blocked = self.readiness(value, task)
        worker = self.worker(value, task)
        if worker:
            reason = worker.get('blockedReason', '')
            worker = {**worker, 'blockedReason': reason[:160], 'blockedReasonTruncated': len(reason) > 160}
        reason = task.get('blockedReason', '')
        return {key: task[key] for key in ('id', 'revision', 'title', 'status')} | {
            'readiness': readiness, 'blockedCount': len(blocked), 'dependencyCount': len(task.get('dependsOn', [])),
            'blockedReason': reason[:160], 'blockedReasonTruncated': len(reason) > 160,
            'claim': self.claim_status(value, task), 'worker': worker,
            'detailRef': {'action': 'assignment.tasks.read', 'args': {'sessionId': sid, 'taskId': task['id']}}}

    def snapshot(self, source, task_id=None, *, view='active', after=None, limit=8):
        if view not in {'active', 'history'} or type(limit) is not int or not 1 <= limit <= 16:
            self.error('Choose an active/history task page with 1 to 16 items.', 400)
        value = self.assignments.current(source)
        if not value:
            if task_id:
                self.error('This conversation has no assignment task list.', 404)
            return {'assignmentId': None, 'tasks': [], 'members': [], 'canManage': False,
                    'view': view, 'nextAfter': None, 'version': 'unassigned'}
        if self.member(value, source['id']) is None:
            self.error('Read tasks only as a current bound assignment member.', 403)
        result = {'assignmentId': value['id'], 'assignmentRevision': value['revision'],
                  'ownerSessionId': value['ownerSessionId'], 'assignmentStatus': value['status'],
                  'canManage': source['id'] == value['ownerSessionId'],
                  'assignmentControl': {key: copy.deepcopy(value[key]) for key in
                      ('id', 'revision', 'status', 'ownerSessionId', 'limits', 'members', 'integration') if key in value},
                  'members': []}
        for sid in value['members']:
            row = self.member(value, sid)
            if row:
                result['members'].append({'sessionId': sid, 'label': row.get('title') or 'Conversation'})
        if task_id:
            task = self.get(value, task_id)
            if task is None:
                self.error('Task not found in this assignment.', 404)
            dependencies = {identity: self.get(value, identity) for identity in task.get('dependsOn', [])}
            result['task'] = self.project(value, task, tasks=dependencies)
            result['dependencies'] = [{'id': identity, 'available': item is not None,
                **({key: item[key] for key in ('title', 'status', 'revision')} if item else {})}
                for identity, item in dependencies.items()]
            return result
        scope = fingerprint([source['id'], source['workspace'], value['id'], view])
        seek, first_revision = self._after(after, scope, value['revision'])
        rows = self._page_rows(value, view, seek, limit)
        tasks = []
        for task in rows[:limit]:
            item = self._summary(value, task, source['id'])
            if len(json.dumps([*tasks, item], ensure_ascii=False).encode()) > PAGE_BYTES:
                if not tasks:
                    self.error('This task summary exceeds the page budget; read its exact detail.', 409)
                break
            tasks.append(item)
        next_after = None
        if len(rows) > len(tasks):
            created, identity = self._order(rows[len(tasks) - 1])
            next_after = base64.urlsafe_b64encode(json.dumps([1, scope, first_revision, created, identity]).encode()).decode()
        result.update(tasks=tasks, view=view, nextAfter=next_after,
            coverage={'kind': 'current-page', 'changed': first_revision != value['revision'],
                'detail': 'Current observations, not a frozen board. Refresh to include items that entered this view before the cursor.'})
        result['version'] = fingerprint([scope, after, limit, value['revision'], tasks, result['members']])
        return result

    def content(self, value, task, args):
        result = {key: args.get(key, task.get(key, [] if key == 'dependsOn' else ''))
                  for key in ('title', 'description', 'blockedReason', 'dependsOn')}
        if not result['title'].strip():
            self.error('Give the task a title.', 400)
        dependencies = result['dependsOn']
        if len(dependencies) != len(set(dependencies)) or any(key == task['id'] or self.get(value, key) is None for key in dependencies):
            self.error('Dependencies must be distinct existing tasks in this assignment, excluding this task.', 400)
        # A new identity cannot be reached from existing valid dependencies.
        # Unchanged edges cannot introduce a cycle. Only edited reachability
        # needs a traversal, never unrelated terminal history.
        if 'dependsOn' in task and dependencies != task['dependsOn']:
            pending, seen = list(dependencies), set()
            while pending:
                key = pending.pop()
                if key == task['id']:
                    self.error('Task dependencies cannot form a cycle.', 400)
                if key not in seen:
                    seen.add(key)
                    dependency = self.get(value, key)
                    if dependency is None:
                        self.error('A task dependency is unavailable.')
                    pending.extend(dependency.get('dependsOn', []))
        return copy.deepcopy(result)

    async def dispatch(self, action, args, origin, command_id, caller):
        source = self.collaboration.source({'sessionId': args['sessionId']}, origin, caller, hydrate=False)
        if source['id'] != args['sessionId']:
            self.error('Task actions belong to the calling conversation.', 403)
        identity = command_id or str(uuid.uuid4())
        digest = fingerprint([action, args, origin, source['id']])
        if action == 'assignment.tasks.wait':
            def read():
                current = self.collaboration.source({'sessionId': args['sessionId']}, origin, caller, hydrate=False)
                result = self.snapshot(current, **{key: args[key] for key in ('view', 'after', 'limit') if key in args})
                return {'accepted': True, 'result': result, 'changed': result['version'] != args['afterVersion']}
            return await self.service.coordination.signal.wait(read, wait_ms=args.get('waitMs', 30000))
        async with self.service.lock:
            source = self.collaboration.source({'sessionId': args['sessionId']}, origin, caller, hydrate=False)
            if source['id'] != args['sessionId']:
                self.error('Task actions belong to the calling conversation.', 403)
            if action in {'assignment.tasks.list', 'assignment.tasks.read'}:
                return {'accepted': True, 'result': self.snapshot(source, args.get('taskId'),
                    **{key: args[key] for key in ('view', 'after', 'limit') if key in args})}
            async with self.assignments.mutation(source, identity):
                if origin not in {'ui', 'user'}:
                    self.collaboration.active_binding(source)
                value = self.assignments.current(source)
                if not value or self.member(value, source['id']) is None:
                    self.error('Task changes require a current bound assignment member.', 403)
                duplicate = self.collaboration.duplicate(identity, digest)
                if duplicate:
                    # Reauthorize current membership before exposing any saved
                    # result. The old receipt never renews its historical claim.
                    return {**duplicate, 'historical': True}
                owner = source['id'] == value['ownerSessionId']
                verb = action.removeprefix('assignment.tasks.')
                if verb in {'create', 'update', 'cancel', 'reassign'} and not owner:
                    self.error('Only the commissioning owner can manage shared tasks.', 403)
                if value['status'] != 'active' and verb not in {'release', 'cancel'}:
                    self.error('The assignment is stopped; only explicit release or cancellation is available.')
                self.convert(value)
                if verb == 'create':
                    if args['expectedAssignmentRevision'] != value['revision']:
                        self.error('The assignment revision changed; refresh before creating a task.')
                    task = {'id': 'task:' + str(uuid.uuid5(uuid.NAMESPACE_URL, value['id'] + ':' + identity)),
                            'revision': 1, 'status': 'open', 'createdAt': time.time(), 'createdBy': source['id']}
                    task.update(self.content(value, task, args))
                else:
                    task = self.get(value, args['taskId'])
                    if not task:
                        self.error('Task not found in this assignment.', 404)
                    if task['revision'] != args['expectedRevision']:
                        self.error('The task revision changed; refresh and inspect its claim before acting.')
                    if task['status'] in {'completed', 'cancelled'}:
                        self.error('Completed and cancelled tasks cannot be changed or reopened.')
                    claim = task.get('claim')
                    if verb in {'release', 'complete'}:
                        if not claim or claim['id'] != args['claimId']:
                            self.error('The exact current claim ID is required; a previous claim cannot act.')
                        if source['id'] != claim['sessionId'] and not (owner and verb == 'release'):
                            self.error('Only the claim holder can report completion; holder or owner may release it.', 403)
                    if verb == 'update':
                        if claim:
                            self.error('Release the claim before editing this task or its dependencies.')
                        task.update(self.content(value, task, args))
                    elif verb in {'claim', 'reassign'}:
                        if verb == 'claim' and claim:
                            self.error('This task is already claimed. Stale claims require explicit reconciliation.')
                        if self.readiness(value, task)[0] != 'ready':
                            self.error('This task is blocked. Resolve its block and reported dependencies before claiming.')
                        holder_id = args['holderSessionId'] if verb == 'reassign' else source['id']
                        holder = self.member(value, holder_id)
                        if not holder or holder.get('status') in {'stopping', 'stopped'}:
                            self.error('Choose a current bound member whose conversation is not stopped.')
                        task['claim'] = {'id': 'claim:' + str(uuid.uuid5(uuid.NAMESPACE_URL, task['id'] + ':' + identity)),
                                         'sessionId': holder_id, 'interruptionRevision': holder.get('interruptionRevision', 0),
                                         'claimedAt': time.time(), 'declaredBy': source['id']}
                        task['status'] = 'claimed'
                    elif verb == 'release':
                        task.pop('claim')
                        task['status'] = 'open'
                    elif verb == 'complete':
                        if self.claim_status(value, task)['status'] != 'current':
                            self.error('This claim is stale or unavailable. Inspect prior effects, then explicitly release or reassign it.')
                        if not args['report'].strip():
                            self.error('Give a completion report.', 400)
                        task['status'] = 'completed'
                        task['completion'] = {'report': args['report'], 'references': args.get('references', []),
                                              'reportedBy': source['id'], 'reportedAt': time.time(),
                                              'verification': 'participant_report'}
                    elif verb == 'cancel':
                        task['status'] = 'cancelled'
                        task['cancelledBy'] = source['id']
                    else:
                        self.error('Unknown task transition.', 400)
                    task['revision'] += 1
                task['updatedAt'] = time.time()
                value['revision'] += 1
                self.save(value, task, new=verb == 'create')
                self.assignments.save(value)
                result = {'assignmentId': value['id'], 'assignmentRevision': value['revision'],
                          'task': self.project(value, task), 'observedAt': time.time()}
                receipt = {'accepted': True, 'commandAction': action, 'assignmentId': value['id'],
                           'result': result}
                self.collaboration.insert(identity, digest, receipt)
                self.service._publish_changes(sessions={source['id']})
                return receipt
