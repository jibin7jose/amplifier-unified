"""Assignment authority in command receipts; usage remains in execution receipts."""
from __future__ import annotations

import copy
import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager, AsyncExitStack

from amplifier_operations.coordination import fingerprint
from .capacity import LIVE, aggregate, evaluate, number, usage_snapshot

DEFAULTS = dict(maxOpenTasks=None, maxMembers=None, maxConcurrentCalls=None,
                maxTotalTokens=None, maxCostUsd=None, reserveTokens=0,
                reserveCostUsd=0, unknownPolicy='pause', includeEstimatedCost=False)


def definitions(schema, string):
    target = {'sessionId': string(200)}
    revision = {**target, 'expectedRevision': {'type': 'integer', 'minimum': 0}}
    limits = {key: {'type': ['integer', 'null'], 'minimum': 1}
              for key in ('maxOpenTasks', 'maxMembers', 'maxConcurrentCalls')}
    limits.update(maxTotalTokens={'type': ['integer', 'null'], 'minimum': 1},
                  maxCostUsd={'type': ['number', 'null'], 'exclusiveMinimum': 0},
                  reserveTokens={'type': 'integer', 'minimum': 0},
                  reserveCostUsd={'type': 'number', 'minimum': 0},
                  unknownPolicy={'enum': ['pause', 'allow']}, includeEstimatedCost={'type': 'boolean'})
    from .assignment_tasks import definitions as task_definitions
    from .assignment_task_limits import definitions as limit_definitions
    return {
        **task_definitions(schema, string),
        **limit_definitions(schema, string),
        'assignment.brief': ('Read a compact paged current-member brief with blockers and latest direct-request outcome links. Passive and bounded; no history, worker admission or permission changes. Previews do not replace exact constraints or evidence.', schema({**target, 'after': string(2048), 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 16}}, ['sessionId'])),
        'assignment.read': ('Inspect this explicit assignment, its members and aggregate usage without starting workers. Independent peers are not members.', schema(target, ['sessionId'])),
        'assignment.start': ('Start an assignment for this unassigned root. New commissioned chats join automatically. Adding an existing chat requires explicit adoption.', schema(target, ['sessionId'])),
        'assignment.adoption': ('Review adding one settled existing workspace chat to this manager’s assignment. Does not start work or change membership.', schema({**target, 'targetSessionId': string(200)}, ['sessionId', 'targetSessionId'])),
        'assignment.adopt': ('Explicitly add a reviewed existing chat to this owner’s active assignment. A current Full-access owner root may readmit its own unchanged finished retired member with a verified retirement receipt, without a human message. Other agent adoption requires the current human instruction; a shared workspace is not authority. Preserves history, usage and budgets, sends no input, and does not clear Stop or task pause. Send a deliberate fresh follow-up separately; old requests never replay.', schema({**revision, 'targetSessionId': string(200), 'expectedTargetRevision': string(200), 'sourceMessageId': string(200)}, ['sessionId', 'targetSessionId', 'expectedRevision', 'expectedTargetRevision'])),
        'assignment.set': ('Owner sets revision-bound aggregate limits. Null maxOpenTasks/maxMembers/maxConcurrentCalls means no count limit. maxMembers counts current bound and reserved members, not retired history. Per-call reservations are allowances, not exact provider spend caps. Existing effects may finish.', schema({**revision, **limits}, ['sessionId', 'expectedRevision'])),
        'assignment.integration': ('Owner explicitly assigns integration responsibility to a current member, or clears it. Does not start work, enroll peers or grant execution authority.', schema({**revision, 'responsibleSessionId': {'type': ['string', 'null'], 'minLength': 1, 'maxLength': 200}}, ['sessionId', 'expectedRevision', 'responsibleSessionId'])),
        'assignment.stop': ('Owner closes assignment admission before requesting interruption of still-bound roots and native children. Does not stop independent peers or remove resources.', schema(revision, ['sessionId', 'expectedRevision'])),
        'assignment.resume': ('User reopens a settled stopped assignment for new requests, preserving members, shared tasks and cumulative usage. Does not send input, resume paused tasks or replay queued work.', schema(revision, ['sessionId', 'expectedRevision'])),
        'assignment.detach': ('User detaches this idle conversation after assignment work settles, retaining attributed usage. Does not resume or send input.', schema(revision, ['sessionId', 'expectedRevision'])),
        'assignment.retire': ('Owner closes a finished member’s participation and releases its member slot. Requires settled work and completed local/shared tasks. Retains history, resources and cumulative usage; sends no input. The same chat can later be reviewed and added again with assignment.adoption/adopt.', schema({**revision, 'targetSessionId': string(200)}, ['sessionId', 'targetSessionId', 'expectedRevision'])),
    }


class Assignments:
    def __init__(self, service):
        self.service = service
        self.interrupting = set()
        from .assignment_tasks import AssignmentTasks
        self.tasks = AssignmentTasks(self)
        service.db.execute("""CREATE INDEX IF NOT EXISTS assignment_requests ON commands(
            json_extract(receipt,'$.assignmentId'),json_extract(receipt,'$.commandAction'))""")
        service.db.execute("""CREATE INDEX IF NOT EXISTS assignment_adoption_target ON commands(
            json_extract(receipt,'$.targetSessionId'))
            WHERE json_extract(receipt,'$.commandAction')='assignment.adopt'""")
        service.db.execute("""CREATE INDEX IF NOT EXISTS assignment_interruptions ON commands(
            json_extract(receipt,'$.interruptionsPending'))
            WHERE json_extract(receipt,'$.commandAction')='assignment.stop'""")
        for identity, encoded in service.db.execute("""SELECT id,receipt FROM commands
                WHERE json_extract(receipt,'$.commandAction')='assignment.stop'
                AND json_extract(receipt,'$.interruptionsPending')>0""").fetchall():
            receipt = json.loads(encoded)
            if 'requested' in receipt.get('interruptions', {}).values():
                receipt['interruptions'] = {sid: 'unknown' if value == 'requested' else value
                                            for sid, value in receipt['interruptions'].items()}
                receipt['interruptionsPending'] = 0
                service.collaboration.save(identity, receipt)
        service.db.commit()

    def load(self, identity):
        receipt = self.service.collaboration.receipt(identity)
        if receipt.get('commandAction') != 'assignment.record':
            self.service.collaboration.error('The assignment record is unavailable.', 409)
        return receipt['result']

    def current(self, session):
        identity = session.get('assignmentId') or (session.get('collaboration') or {}).get('assignmentId')
        if not identity:
            return None
        value = self.load(identity)
        member = value['members'].get(session['id'])
        if not member:
            self.service.collaboration.error('The assignment membership is unavailable.', 409)
        return value if member['status'] not in {'detached', 'retired'} else None

    def save(self, value):
        self.service.collaboration.save(value['id'], {
            'accepted': True, 'commandAction': 'assignment.record', 'result': value})

    @asynccontextmanager
    async def mutation(self, source, identity, *, additional=()):
        """Restore only touched metadata if durable commit fails before effects."""
        # The service lock excludes another operation. Its preceding retained
        # receipts must not become part of this operation's rollback group.
        if self.service.db.in_transaction:
            self.service.db.commit()
        assignment = self.current(source)
        members = set((assignment or {}).get('members', {})) | {source['id'], *additional}
        keys = ('assignmentId', 'interruptionRevision', 'lastInterruption', 'status')
        rows = {}
        for sid in members:
            try:
                rows[sid] = self.service._session(sid, hydrate=False)
            except Exception:
                continue
        before = {sid: {key: copy.deepcopy(row[key]) for key in keys if key in row} for sid, row in rows.items()}
        try:
            yield
        except BaseException:
            committed = not self.service.db.in_transaction and self.service.db.execute(
                'SELECT 1 FROM commands WHERE id=?', (identity,)).fetchone()
            if not committed:
                self.service.db.rollback()
                for sid, values in before.items():
                    for key in keys:
                        if key in values:
                            rows[sid][key] = values[key]
                        else:
                            rows[sid].pop(key, None)
            raise

    def ensure(self, source, operation):
        value = self.current(source)
        if value:
            if value['status'] != 'active':
                self.service.collaboration.error('This assignment is stopped. Inspect it before starting unrelated work.', 409)
            return value
        identity = 'assignment:' + str(uuid.uuid5(uuid.NAMESPACE_URL, source['id'] + ':' + operation))
        value = dict(id=identity, ownerSessionId=source['id'], revision=1, status='active',
                     createdAt=time.time(), limits=dict(DEFAULTS),
                     members={source['id']: {'status': 'bound', 'role': 'owner'}})
        self.service.collaboration.insert(identity, fingerprint(['assignment', source['id'], operation]),
            {'accepted': True, 'commandAction': 'assignment.record', 'result': value})
        source['assignmentId'] = identity
        return value

    def blocked(self, session=None, identity=None):
        value = self.load(identity) if identity else self.current(session)
        return 'This assignment was stopped; automatic work is suppressed.' if value and value['status'] != 'active' else None

    def require_settled(self, value):
        """Resume cannot race outstanding Stop or revive unobserved runtime work."""
        # A durable recovery proved these exact historical Stop outcomes while
        # holding native ownership. Do not reacquire a former member's writer
        # after detachment: it may now be doing unrelated independent work.
        settled = set()
        for (encoded,) in self.service.db.execute("""SELECT receipt FROM commands
                WHERE json_extract(receipt,'$.commandAction')='assignment.resume'
                AND json_extract(receipt,'$.assignmentId')=?""", (value['id'],)):
            receipt = json.loads(encoded)
            for proof in receipt.get('result', {}).get('stopRecovery', {}).get('members', []):
                if (receipt.get('accepted') is True and proof.get('workerAbsent') is True
                        and proof.get('inputsReplayed') is False and proof.get('ownershipAcquisitionId')):
                    settled.update((proof.get('sessionId'), stop) for stop in proof.get('stopRequestIds', []))
        unknown = {}
        for identity, encoded in self.service.db.execute("""SELECT id,receipt FROM commands
                WHERE json_extract(receipt,'$.commandAction')='assignment.stop'
                AND json_extract(receipt,'$.assignmentId')=?""", (value['id'],)):
            receipt = json.loads(encoded)
            if (identity in self.interrupting or receipt.get('interruptionsPending') or
                    any(status not in {'returned', 'unknown'} for status in receipt.get('interruptions', {}).values())):
                self.service.collaboration.error('The assignment stop has not settled. Try continuing after it finishes.', 409)
            for sid, status in receipt.get('interruptions', {}).items():
                if status == 'unknown' and (sid, identity) not in settled:
                    unknown.setdefault(sid, []).append(identity)
        for sid, member in value['members'].items():
            if member['status'] != 'bound':
                continue
            session = self.service._session(sid)
            if (self.current(session) or {}).get('id') != value['id']:
                continue
            if (session.get('status') in LIVE | {'working', 'busy', 'stopping'}
                    or ((session.get('collaborationGeneration') or {}).get('id') and not session['collaborationGeneration'].get('terminal'))
                    or any(row.get('phase') in LIVE and not row.get('endedAt') for row in usage_snapshot(session)['receipts'])
                    or any(row.get('status') in {'queued', 'starting', 'running', 'working', 'idle', 'stopping'} for row in session.get('workers', []))
                    or sid in getattr(self.service.runtime, '_preparations', {})):
                self.service.collaboration.error('Wait for assignment chats and their workers to settle before resuming.', 409)

        return unknown

    def require_request(self, source, target, mode):
        value = self.current(source)
        if not value or mode == 'notify':
            return
        if (self.current(target) or {}).get('id') != value['id'] and any(
                value['limits'][key] is not None for key in ('maxTotalTokens', 'maxCostUsd')):
            self.service.collaboration.error('This independent chat cannot share a request-scoped assignment budget with this adapter. Use a commissioned helper within the budget, or an informational notify that does not start work.', 409)

    def external_requests(self, value):
        rows = self.service.db.execute("""SELECT receipt FROM commands
            WHERE json_extract(receipt,'$.commandAction')='coordination.send'
            AND json_extract(receipt,'$.assignmentId')=?
            AND json_extract(receipt,'$.delivery') IN ('queued','submitting','pending_steer','accepted','applied')
            AND json_extract(receipt,'$.response.status') IS NOT 'sealed'
            ORDER BY rowid DESC""", (value['id'],))
        result = []
        for (encoded,) in rows:
            request = json.loads(encoded)
            target = request['target']['sessionId']
            if target not in value['members'] or value['members'][target]['status'] == 'detached':
                result.append({key: request[key] for key in ('requestId', 'target', 'delivery')})
                if len(result) == 65:
                    break
        return {'requests': result[:64], 'truncated': len(result) > 64,
                'interruptionSupported': False,
                'detail': 'Pending assignment input is suppressed. Already admitted work in independent chats is retained; this adapter cannot interrupt only that request, so those conversations are not stopped.'}

    def reserve_member(self, source, sid, operation):
        value = self.ensure(source, operation)
        if sid in value['members'] and value['members'][sid]['status'] != 'retired':
            return value['id']
        limits = value['limits']
        if limits['maxMembers'] is not None and self.active_member_count(value) >= limits['maxMembers']:
            self.service.collaboration.error('The assignment member limit is reached. Retire a finished member to release a slot; its history and usage stay retained.', 429)
        if limits['maxOpenTasks'] is not None:
            pending = {row[0] for row in self.service.db.execute("""SELECT DISTINCT
                json_extract(receipt,'$.target.sessionId') FROM commands
                WHERE json_extract(receipt,'$.commandAction')='coordination.send'
                AND json_extract(receipt,'$.delivery') IN ('queued','submitting','pending_steer')""")}
            opened = 0
            for key, member in value['members'].items():
                if key == value['ownerSessionId'] or member['status'] in {'detached', 'retired', 'not_created'}:
                    continue
                try:
                    row = self.service._session(key, hydrate=False)
                except Exception:
                    row = None
                active = (row or {}).get('collaborationGeneration') or {}
                task = (row or {}).get('task') or self.service.coordination.task(key)
                if (member['status'] == 'reserved' or row is None or row.get('status') in LIVE | {'busy', 'stopping'} or key in pending
                        or active.get('id') and not active.get('terminal')
                        or task.get('status') in {'active', 'running', 'working'}
                        or key in getattr(self.service.runtime, '_preparations', {})):
                    opened += 1
            if opened >= limits['maxOpenTasks']:
                self.service.collaboration.error('The assignment open-task limit is reached, including nested commissioned work.', 429)
        value['members'][sid] = {'status': 'reserved', 'role': 'commissioned',
                                'creatorSessionId': source['id'], 'creationOperationId': operation}
        value['revision'] += 1
        self.save(value)
        return value['id']

    @staticmethod
    def active_member_count(value):
        return sum(member['status'] in {'bound', 'reserved'} for member in value['members'].values())

    def request_membership_reason(self, receipt):
        """Old assignment inputs never renew when a finished chat is readmitted."""
        if not receipt.get('assignmentId'):
            return None
        value = self.load(receipt['assignmentId'])
        target = (receipt.get('target') or {}).get('sessionId') or receipt.get('targetSessionId')
        if receipt.get('commandAction') == 'coordination.create':
            target = receipt.get('sessionId')
        for sid in (receipt.get('senderSessionId'), target):
            member = value['members'].get(sid, {})
            if member.get('status') == 'retired':
                return 'This assignment member was retired; earlier requests remain inspectable.'
            if member.get('joinedAt', 0) > receipt.get('acceptedAt', 0):
                return 'This request belongs to an earlier assignment membership; send fresh work after readmission.'
        return None

    def usage(self, value):
        calls, missing = [], []
        for sid in value['members']:
            try:
                session = self.service._session(sid)
            except Exception:
                if value['members'][sid]['status'] not in {'not_created', 'reserved'}:
                    missing.append(sid)
                continue
            for row in usage_snapshot(session)['receipts']:
                if row.get('assignmentId') == value['id']:
                    calls.append({**row, 'memberSessionId': sid})
        return {'receipts': calls, 'metrics': aggregate(calls), 'calls': len(calls),
                'missingMembers': missing, 'coverage': 'supported host-admitted calls since enrollment; provider-internal retries and external work may be unreported'}

    def admission(self, session, row):
        value = self.current(session)
        if not value:
            return {'allowed': True}
        reason = self.blocked(identity=value['id'])
        limits = value['limits']
        if all(limits[key] is None for key in ('maxConcurrentCalls', 'maxTotalTokens', 'maxCostUsd')):
            # With no call or spend ceiling, historical usage cannot deny this
            # admission. Keep retained-member availability checks, but do not
            # hydrate every member or rebuild their accounting on each call.
            missing = []
            for sid, member in value['members'].items():
                try:
                    self.service._session(sid, hydrate=False)
                except Exception:
                    if member['status'] not in {'not_created', 'reserved'}:
                        missing.append(sid)
            usage = {'metrics': {}, 'receipts': [], 'missingMembers': missing}
        else:
            usage = self.usage(value)
        policy = {**limits, 'enabled': True}
        result = evaluate(policy, usage)
        reasons = list(result['reasons'])
        renewable = []
        if reason:
            reasons.append(reason)
        pending = [call for call in usage['receipts'] if call.get('phase') in LIVE | {'outcome_unknown'} and not call.get('endedAt')]
        unknown = sum(call.get('phase') == 'outcome_unknown' for call in pending)
        if limits['maxConcurrentCalls'] is not None and len(pending) >= limits['maxConcurrentCalls']:
            # Unknown calls keep their slots. Other, live calls can still free
            # capacity unless the unknown reservations alone fill the ceiling.
            (reasons if unknown >= limits['maxConcurrentCalls'] else renewable).append('The assignment concurrent model-call limit is reached.')
        for metric, maximum, reserve in [('grossTotalTokens', 'maxTotalTokens', 'reserveTokens'), ('costUsd', 'maxCostUsd', 'reserveCostUsd')]:
            limit = limits[maximum]
            if limit is None:
                continue
            recorded = usage['metrics'][metric]['value'] or 0
            if metric == 'costUsd' and not limits['includeEstimatedCost']:
                recorded -= usage['metrics'][metric]['estimatedValue']
            held = sum(call.get('assignmentReservation', {}).get(reserve, 0) for call in pending)
            if recorded + held + limits[reserve] > limit:
                # Only held in-flight allowances can renew. Recorded spend,
                # an oversized next reservation and unknown outcomes cannot.
                destination = renewable if pending and not unknown and recorded + limits[reserve] <= limit else reasons
                destination.append('The assignment has insufficient unreserved ' + metric + ' capacity.')
        if usage['missingMembers'] and limits['unknownPolicy'] == 'pause':
            reasons.append('Assignment usage is unavailable for a retained member.')
        waiting = bool(renewable) and not reasons
        reasons += renewable
        if not reasons:
            row.update(assignmentId=value['id'], assignmentRevision=value['revision'],
                       assignmentReservation={key: limits[key] for key in ('reserveTokens', 'reserveCostUsd')})
        return {'allowed': not reasons, 'waiting': waiting, 'reasons': reasons, 'assignmentId': value['id'], 'assignmentRevision': value['revision']}

    def snapshot(self, session):
        value = self.current(session)
        if not value:
            return {'assignment': None}
        usage = self.usage(value)
        return {'assignment': value, 'usage': {key: item for key, item in usage.items() if key != 'receipts'},
                'inFlightCalls': sum(row.get('phase') in LIVE and not row.get('endedAt') for row in usage['receipts']),
                **({'independentWork': self.external_requests(value)} if value['status'] == 'stopped' else {}),
                'enforcement': 'Supported model admission only. Reservations are allowances; actual calls and effects may exceed them.'}

    async def dispatch(self, action, args, origin, command_id, caller):
        if action.startswith('assignment.tasks.'):
            return await self.tasks.dispatch(action, args, origin, command_id, caller)
        identity = command_id or str(uuid.uuid4())
        try:
            return await self._dispatch(action, args, origin, identity, caller)
        finally:
            # A lost response after commit must not strand known requested
            # interruption. Recovery after process loss never replays it.
            if action == 'assignment.stop' and not self.service.db.in_transaction and identity not in self.interrupting:
                row = self.service.db.execute('SELECT receipt FROM commands WHERE id=?', (identity,)).fetchone()
                receipt = json.loads(row[0]) if row else {}
                stops = [sid for sid, status in receipt.get('interruptions', {}).items() if status == 'requested']
                if receipt.get('commandAction') == action and stops:
                    self.interrupting.add(identity)
                    self.service._task(self.interrupt(identity, stops))

    async def _dispatch(self, action, args, origin, command_id, caller):
        if action in {'assignment.task.read', 'assignment.task.setLimit', 'assignment.task.complete'}:
            from .assignment_task_limits import dispatch
            return await dispatch(self, action, args, origin, command_id, caller)
        if action == 'assignment.retire':
            from .assignment_retirement import dispatch
            return await dispatch(self, action, args, origin, command_id, caller)
        if action in {'assignment.adoption', 'assignment.adopt'}:
            from .assignment_adoption import dispatch
            return await dispatch(self, action, args, origin, command_id, caller)
        if action == 'assignment.brief':
            from .assignment_brief import dispatch
            return dispatch(self, args, origin, caller)
        collaboration = self.service.collaboration
        source = collaboration.source({'sessionId': args['sessionId']}, origin, caller)
        if source['id'] != args['sessionId']:
            collaboration.error('Assignment actions belong to the calling conversation.')
        if action == 'assignment.read':
            return {'accepted': True, 'result': self.snapshot(source)}
        identity = command_id or str(uuid.uuid4())
        digest = fingerprint([action, args, origin, source['id']])
        stops, recovery = [], []
        async with self.service.lock, self.mutation(source, identity), AsyncExitStack() as boundary:
            duplicate = collaboration.duplicate(identity, digest)
            if duplicate:
                return duplicate
            if origin not in {'ui', 'user'}:
                collaboration.active_binding(source)
            value = self.current(source)
            if action == 'assignment.start':
                value = self.ensure(source, identity)
            else:
                if not value or args.get('expectedRevision') != value['revision']:
                    collaboration.error('The assignment revision changed; inspect it before editing.', 409)
                if action != 'assignment.detach' and source['id'] != value['ownerSessionId']:
                    collaboration.error('Only the commissioning owner can control this assignment.')
                if action == 'assignment.resume':
                    if origin not in {'ui', 'user'}:
                        collaboration.error('Only the user can resume a stopped assignment.')
                    if value['status'] != 'stopped':
                        collaboration.error('Only a stopped assignment can resume.', 409)
                    unknown = self.require_settled(value)
                    for sid, receipts in sorted(unknown.items()):
                        prove = getattr(self.service.runtime, 'stopped_boundary', None)
                        if prove is None:
                            collaboration.error('This runtime cannot confirm that the earlier stop has settled.', 409)
                        try:
                            evidence = boundary.enter_context(prove(self.service._session(sid)))
                        except (OSError, RuntimeError, ValueError) as exc:
                            collaboration.error(f'Could not confirm that this chat has stopped. {exc}', 409)
                        recovery.append({'stopRequestIds': receipts, **evidence})
                    value.update(status='active', resumedAt=time.time(), resumedBy=origin)
                elif action == 'assignment.integration':
                    responsible = args['responsibleSessionId']
                    if value['status'] != 'active':
                        collaboration.error('A stopped assignment cannot assign integration work.', 409)
                    if responsible is not None:
                        member = value['members'].get(responsible, {})
                        target = self.service._session(responsible, hydrate=False)
                        if member.get('status') != 'bound' or (self.current(target) or {}).get('id') != value['id']:
                            collaboration.error('Choose a current commissioned member; independent chats are not enrolled.', 409)
                    value['integration'] = {'responsibleSessionId': responsible, 'declaredBy': source['id'], 'declaredAt': time.time()}
                elif action == 'assignment.set':
                    if value['status'] != 'active':
                        collaboration.error('A stopped assignment cannot be resumed by changing limits.', 409)
                    limits = {**value['limits'], **{key: args[key] for key in DEFAULTS if key in args}}
                    for key in ('maxOpenTasks', 'maxMembers', 'maxConcurrentCalls'):
                        val = limits[key]
                        if val is None:
                            continue
                        if type(val) is not int or val < 1:
                            collaboration.error('Assignment counts must be positive integers or null for no count limit.', 400)
                    for key in ('maxTotalTokens', 'maxCostUsd', 'reserveTokens', 'reserveCostUsd'):
                        val = limits[key]
                        if val is None and key.startswith('max'):
                            continue
                        if not number(val) or val < 0 or (key.startswith('max') and val == 0) or ('Tokens' in key and type(val) is not int):
                            collaboration.error('Assignment allowances must be valid nonnegative numbers.', 400)
                    if limits['unknownPolicy'] not in {'pause', 'allow'} or type(limits['includeEstimatedCost']) is not bool:
                        collaboration.error('Invalid assignment unknown-usage policy.', 400)
                    value['limits'] = limits
                elif action == 'assignment.detach':
                    if origin not in {'ui', 'user'}:
                        collaboration.error('Only the user can detach a conversation from assignment authority.')
                    if (source.get('status') in LIVE | {'busy', 'stopping'}
                            or any(row.get('phase') in LIVE and not row.get('endedAt') for row in usage_snapshot(source)['receipts'])
                            or any(row.get('status') in {'starting', 'running', 'idle', 'stopping'} for row in source.get('workers', []))
                            or source['id'] in getattr(self.service.runtime, '_preparations', {})):
                        collaboration.error('Wait for this conversation to settle before detaching.', 409)
                    if source['id'] == value['ownerSessionId'] and value['status'] != 'stopped':
                        collaboration.error('Stop the assignment before detaching its owner.', 409)
                    value['members'][source['id']]['status'] = 'detached'
                    source.pop('assignmentId', None)
                elif action == 'assignment.stop':
                    value['status'] = 'stopped'
                    value['stoppedAt'] = time.time()
                    for sid, member in value['members'].items():
                        if member['status'] in {'detached', 'retired', 'not_created'}:
                            continue
                        try:
                            target = self.service._session(sid)
                        except Exception:
                            continue  # Its retained creation is fenced by the assignment.
                        if (self.current(target) or {}).get('id') != value['id']:
                            continue
                        target['interruptionRevision'] = target.get('interruptionRevision', 0) + 1
                        target['lastInterruption'] = {'commandId': identity, 'origin': origin, 'at': time.time(), 'assignmentId': value['id']}
                        target['status'] = 'stopping'
                        stops.append(sid)
                value['revision'] += 1
                self.save(value)
            receipt = {'accepted': True, 'commandAction': action, 'assignmentId': value['id'],
                       'result': {'assignment': copy.deepcopy(value)},
                       'interruptions': {sid: 'requested' for sid in stops}, 'interruptionsPending': len(stops)}
            if recovery:
                receipt['result']['stopRecovery'] = {'assignmentRevision': args['expectedRevision'], 'members': recovery}
            if action == 'assignment.stop':
                receipt['result']['independentWork'] = self.external_requests(value)
            collaboration.insert(identity, digest, receipt)
            self.service._publish_changes(sessions={source['id'], *stops})
        return receipt

    async def interrupt(self, identity, stops):
        async def one(sid):
            try:
                await self.service._stop(sid)
                outcome = 'returned'  # This is not proof that external effects were undone.
            except Exception:
                outcome = 'unknown'
            async with self.service.lock:
                receipt = self.service.collaboration.receipt(identity)
                receipt['interruptions'][sid] = outcome
                receipt['interruptionsPending'] = sum(status == 'requested' for status in receipt['interruptions'].values())
                self.service.collaboration.save(identity, receipt)
                self.service.db.commit()
        try:
            await asyncio.gather(*(one(sid) for sid in stops))
        finally:
            self.interrupting.discard(identity)

    def summary(self, session):
        value = self.current(session)
        if value:
            return {key: value[key] for key in ('id', 'revision', 'status', 'ownerSessionId', 'limits')} | {'memberCount': len(value['members']), 'activeMemberCount': self.active_member_count(value), 'integration': value.get('integration')}
