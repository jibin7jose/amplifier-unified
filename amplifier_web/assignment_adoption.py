"""Explicit responsibility for an existing chat, without invented creation history."""
import asyncio
import copy
import time
import uuid
from contextlib import ExitStack

from amplifier_operations.coordination import fingerprint
from .capacity import LIVE, usage_snapshot
from .management import Management
from .schedule_handoff import latest_human
from .task_continuity import _load_control


def reusable_retirement(app, source, target):
    """Only a proven same-owner retirement can replace a new human instruction."""
    from .assignment_retirement import require_finished, reuse_binding
    from .errors import AppError
    from .peer_resume import safety_halt
    from .task_binding import dependency_blocked
    value = app.assignments.current(source)
    member = value['members'].get(target['id'], {})
    if member.get('status') != 'retired' or member.get('retiredBySessionId') != source['id']:
        return False
    try:
        receipt = app.collaboration.receipt(member.get('retirementOperationId'))
    except AppError:
        return False
    retired = receipt.get('result', {}).get('assignment', {})
    if (receipt.get('accepted') is not True or receipt.get('commandAction') != 'assignment.retire'
            or receipt.get('senderSessionId') != source['id']
            or receipt.get('targetSessionId') != target['id']
            or receipt.get('assignmentId') != value['id']
            or retired.get('ownerSessionId') != source['id']
            or retired.get('members', {}).get(target['id']) != member):
        return False
    try:
        task = _load_control(app, target, current_only=True)['task'] or {}
    except ValueError:
        # Unavailable native state cannot prove standing reuse. Preserve the
        # ordinary human-instruction path, without substituting a cached task.
        return False
    if receipt.get('reuseBinding') != reuse_binding(app, source, target, task):
        return False
    source_task = source.get('task') or app.coordination.task(source['id'])
    if (source.get('status') in {'stopped', 'stopping', 'error', 'interrupted'}
            or source_task.get('status') == 'paused'
            or source_task.get('status') == 'blocked' and not dependency_blocked(source_task)
            or target.get('status') != 'idle' or target.get('failure') or safety_halt(target)):
        return False
    require_finished(app, target, value, task)
    return True


def inspect(app, source, target):
    assignment = app.assignments.current(source)
    if (not assignment or assignment['status'] != 'active'
            or assignment['ownerSessionId'] != source['id']):
        raise ValueError('Only the owner of an active assignment can add an existing chat.')
    if source['id'] == target['id']:
        raise ValueError('This chat already owns the assignment.')
    current = app.assignments.current(target)
    active = target.get('collaborationGeneration') or {}
    pending = target.get('pendingConfiguration') or {}
    reason = None
    if current:
        reason = 'This chat already belongs to an assignment. Detach it there before adding it here.'
    elif (target['id'] in assignment['members']
          and assignment['members'][target['id']]['status'] != 'retired'):
        reason = 'This assignment retains an earlier membership for that chat. Keep its history; use a separate assignment.'
    elif target['id'] in app.state.get('conversationOrganization', {}).get('archived', {}):
        reason = 'Unarchive the chat before adding it.'
    elif target.get('ownership', {}).get('status') in {'blocked', 'yielding', 'yielded', 'yield-failed', 'taking-over'}:
        reason = 'This chat is in use elsewhere or its ownership is changing. Resolve that before adding it.'
    elif (target.get('status') not in {'idle', 'stopped', 'error'}
            or active.get('id') and not active.get('terminal')
            or target['id'] in getattr(app.runtime, '_preparations', {})
            or any(row.get('phase') in LIVE and not row.get('endedAt')
                   for row in usage_snapshot(target)['receipts'])
            or any(row.get('status') in {'queued', 'starting', 'running', 'working', 'idle', 'stopping'}
                   for row in target.get('workers', []))):
        reason = 'Wait for this chat and its helpers to finish their current work before adding it.'
    elif (any(target.get(key) for key in ('configurationBusy', 'configurationPending', 'fileAccessPending'))
            or pending.get('phase') in {'queued', 'applying'}
            or Management.unresolved_configuration(target)):
        reason = 'Wait for this chat’s settings change to finish before adding it.'
    elif app.db.execute("""SELECT 1 FROM commands
            WHERE json_extract(receipt,'$.commandAction')='coordination.send'
            AND json_extract(receipt,'$.target.sessionId')=?
            AND json_extract(receipt,'$.delivery') IN ('queued','submitting','pending_steer') LIMIT 1""",
            (target['id'],)).fetchone():
        reason = 'This chat has queued input. Let it settle before changing responsibility.'
    token = fingerprint({
        'assignment': [assignment['id'], assignment['revision']],
        'target': target['id'], 'workspace': target['workspace'],
        'membership': target.get('assignmentId'), 'status': target.get('status'),
        'generation': active, 'human': (latest_human(target) or {}).get('id'),
        'stop': target.get('interruptionRevision', 0),
        'placement': [target.get('executionRevision'), target.get('workingDirectory')],
        'task': target.get('task') or app.coordination.task(target['id']), 'reason': reason,
    })
    return {'sessionId': target['id'], 'title': target.get('title') or 'Untitled chat',
            'eligible': reason is None, 'reason': reason, 'revision': token,
            'assignmentRevision': assignment['revision'],
            'effect': 'This manager can coordinate and stop future work in this chat. Earlier history and usage stay unchanged. Adding it does not send a message or restart work.'}


def checked(app, source, target, args, origin):
    """Revalidate the same review and authority after any idle retirement."""
    try:
        observation = inspect(app, source, target)
    except ValueError as exc:
        app.collaboration.error(str(exc), 409)
    if (not observation['eligible'] or observation['revision'] != args['expectedTargetRevision']
            or observation['assignmentRevision'] != args['expectedRevision']):
        app.collaboration.error(observation['reason'] or 'The chat or assignment changed. Review it before adding it.', 409)
    human = None
    full = None
    if origin not in {'ui', 'user'}:
        lineage = app.collaboration.lineage(source, origin)
        # lineage/source keep the strict root principal. Full never lets a native
        # child use the manager's identity or a later generation's input.
        full = app.access_mode.capture_full(source, origin)
        if full and reusable_retirement(app, source, target):
            return {'human': None, 'full': full}
        human = latest_human(source)
        if (not human or human['id'] != args.get('sourceMessageId')
                or human.get('questionId') or human.get('scheduledRunId')
                or human.get('inputId') not in lineage.get('sourceInputIds', [])
                or human.get('createdAt', 0) <= (target.get('lastInterruption') or {}).get('at', 0)):
            app.collaboration.error('Adding an existing chat requires the latest actual human instruction in the manager’s current input, after any Stop.', 409)
    return {'human': human, 'full': None}


async def retire_idle(app, source, target, args, origin, identity, digest, *, validate=checked):
    """Use ordinary acknowledged idle retirement, never ask a foreign owner to yield.

    Retirement preserves the chat's idle state and saved task. It can await
    worker events, so it must not hold the app lock. Admission and every review
    binding are checked again before retirement and before the membership commit.
    """
    runtime, sid = app.runtime, target['id']
    row = getattr(runtime, 'workers', {}).get(sid)
    if not row or row['process'].returncode is not None:
        return
    admission = getattr(runtime, '_admission', None)
    retention = getattr(runtime, 'retention', None)
    if not callable(admission) or retention is None:
        app.collaboration.error('Wait for this chat’s current work to finish before adding it.', 409)
    retirement = None
    async with admission(sid):
        async with app.lock:
            if app.collaboration.duplicate(identity, digest):
                return
            target = app.collaboration.authorize(source, {'sessionId': sid})
            validate(app, source, target, args, origin)
            row = runtime.workers.get(sid)
            if row and row['process'].returncode is None:
                if target.get('status') != 'idle' or not retention.eligible(row) or retention.protected(sid):
                    app.collaboration.error('Wait for this chat’s current work to finish before adding it.', 409)
                retirement = retention.schedule(sid, row)
    if retirement is not None:
        await asyncio.wait_for(asyncio.shield(retirement), 30)


async def dispatch(assignments, action, args, origin, command_id, caller):
    app, collaboration = assignments.service, assignments.service.collaboration
    source = collaboration.source({'sessionId': args['sessionId']}, origin, caller)
    if source['id'] != args['sessionId']:
        collaboration.error('Assignment actions belong to the calling conversation.')
    identity = command_id or str(uuid.uuid4())
    digest = fingerprint([action, args, origin, source['id']])
    async with app.lock:
        if action == 'assignment.adopt':
            duplicate = collaboration.duplicate(identity, digest)
            if duplicate:
                return duplicate
        target = collaboration.authorize(source, {'sessionId': args['targetSessionId']})
        if action == 'assignment.adoption':
            try:
                return {'accepted': True, 'result': inspect(app, source, target)}
            except ValueError as exc:
                collaboration.error(str(exc), 409)
        authority = checked(app, source, target, args, origin)
    def validate(app, source, target, args, origin):
        current = checked(app, source, target, args, origin)
        if current != authority:
            collaboration.error('The adoption authority changed. Review it before adding the chat.', 409)
        return current
    try:
        await retire_idle(app, source, target, args, origin, identity, digest, validate=validate)
    except (OSError, RuntimeError, ValueError) as exc:
        collaboration.error(f'Could not confirm that this chat has settled. {exc}', 409)
    async with app.lock:
        duplicate = collaboration.duplicate(identity, digest)
        if duplicate:
            return duplicate
        target = collaboration.authorize(source, {'sessionId': args['targetSessionId']})
        # Every eligible status needs the real native lease. The lease spans the
        # durable commit; an idle projection alone is never ownership evidence.
        with ExitStack() as boundary:
            prove = getattr(app.runtime, 'stopped_boundary', None)
            if not callable(prove):
                collaboration.error('This runtime cannot confirm that the earlier work has stopped.', 409)
            try:
                boundary.enter_context(prove(target))
            except (OSError, RuntimeError, ValueError) as exc:
                collaboration.error(f'Could not confirm that this chat has stopped. {exc}', 409)
            # Full reuse must observe saved task authority after acquiring the
            # lease too: another writer could have changed it since preflight.
            human = validate(app, source, target, args, origin)['human']
            async with assignments.mutation(source, identity, additional=(target['id'],)):
                previous = assignments.current(source)['members'].get(target['id'], {})
                assignments.reserve_member(source, target['id'], identity)
                value = assignments.current(source)
                membership = {'status': 'bound', 'role': 'adopted',
                              'adoptedBySessionId': source['id'], 'adoptionOperationId': identity,
                              'adoptedAt': time.time(), 'authorityOrigin': origin,
                              'sourceMessageId': human['id'] if human else None}
                if previous.get('status') == 'retired':
                    membership.update(joinedAt=time.time(),
                                      previousRetirementId=previous['retirementOperationId'])
                value['members'][target['id']] = membership
                target['assignmentId'] = value['id']
                assignments.save(value)
                receipt = {'accepted': True, 'commandAction': action, 'assignmentId': value['id'],
                           'senderSessionId': source['id'], 'targetSessionId': target['id'],
                           'workspace': source['workspace'], 'membership': copy.deepcopy(membership),
                           'result': {'assignment': copy.deepcopy(value), 'inputSent': False}}
                if authority['full']:
                    receipt['readmissionAuthority'] = authority['full']
                collaboration.insert(identity, digest, receipt)
                app._publish_changes(sessions={source['id'], target['id']})
        return receipt


def valid_membership(app, source, target, assignment, membership):
    """Placement authority comes from an adoption receipt, never creation fields."""
    receipt = app.collaboration.receipt(membership.get('adoptionOperationId'))
    return (assignment['ownerSessionId'] == source['id']
            and membership.get('adoptedBySessionId') == source['id']
            and receipt.get('accepted') is True
            and receipt.get('commandAction') == 'assignment.adopt'
            and receipt.get('assignmentId') == assignment['id']
            and receipt.get('senderSessionId') == source['id']
            and receipt.get('targetSessionId') == target['id']
            and receipt.get('workspace') == source['workspace']
            and receipt.get('membership') == membership)
