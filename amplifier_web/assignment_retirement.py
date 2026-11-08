"""Close finished participation without dropping history or assignment spend."""
import copy
import time
import uuid
from contextlib import ExitStack, closing

from amplifier_operations.coordination import fingerprint
from .capacity import LIVE, usage_snapshot
from .management import Management
from .task_continuity import _load_control
from . import task_environments as environments


def checked(app, source, target, args, origin):
    assignments = app.assignments
    value = assignments.current(source)
    error = app.collaboration.error
    if origin not in {'ui', 'user'}:
        app.collaboration.active_binding(source)
    if (not value or value['status'] != 'active'
            or value['ownerSessionId'] != source['id']):
        error('Only the owner of an active assignment can retire a finished member.', 409)
    if value['revision'] != args['expectedRevision']:
        error('The assignment revision changed; inspect it before retiring a member.', 409)
    if (target['id'] == source['id'] or value['members'].get(target['id'], {}).get('status') != 'bound'
            or (assignments.current(target) or {}).get('id') != value['id']):
        error('Choose a current helper member, not the assignment owner.', 409)
    try:
        task = _load_control(app, target, current_only=True)['task'] or {}
    except ValueError as exc:
        error(str(exc), 409)
    require_finished(app, target, value, task)
    return value, task


def require_finished(app, target, value, task):
    """Check the same canonical task used by the retirement reuse binding."""
    error = app.collaboration.error
    active = target.get('collaborationGeneration') or {}
    if (target.get('status') not in {'idle', 'stopped'}
            or active.get('id') and not active.get('terminal')
            or target['id'] in getattr(app.runtime, '_preparations', {})
            or any(row.get('phase') in LIVE | {'outcome_unknown'} and not row.get('endedAt')
                   for row in usage_snapshot(target)['receipts'])
            or any(row.get('status') in {'queued', 'starting', 'running', 'working', 'idle', 'stopping'}
                   for row in target.get('workers', []))):
        error('Wait for this member and its helpers to finish; uncertain work must be reconciled before retirement.', 409)
    if (any(target.get(key) for key in ('configurationBusy', 'configurationPending', 'fileAccessPending'))
            or (target.get('pendingConfiguration') or {}).get('phase') in {'queued', 'applying'}
            or Management.unresolved_configuration(target)):
        error('Wait for this member’s settings change to finish before retiring it.', 409)
    if task.get('id') and task.get('status') != 'completed':
        error('Complete this member’s saved task before retiring its participation.', 409)
    if app.assignments.tasks.unfinished_claims(value, target['id']):
        error('Complete, release or reassign this member’s unfinished shared tasks before retiring it.', 409)
    if app.db.execute("""SELECT 1 FROM commands
            WHERE json_extract(receipt,'$.commandAction')='coordination.send'
            AND json_extract(receipt,'$.target.sessionId')=?
            AND json_extract(receipt,'$.delivery') IN ('queued','submitting','pending_steer','unknown') LIMIT 1""",
            (target['id'],)).fetchone():
        error('This member has pending or uncertain input. Reconcile it before retirement.', 409)
    if app.test_queue.rows(active=True, sid=target['id'], limit=1):
        error('Wait for this member’s isolated check and cleanup to settle before retirement.', 409)
    # A terminal queue receipt may still own a live or uncertain guest. Query
    # this chat's allocation owner directly; stopped evidence can be retained.
    with closing(environments.storage.connect(app.data_dir)) as db:
        if db.execute("""SELECT 1 FROM allocations WHERE root=? AND status!='retired'
                AND json_extract(body,'$.kind')=? AND (
                    coalesce(json_extract(body,'$.environmentState'),'unknown')
                        NOT IN ('stopped','not-started','removed')
                    OR json_extract(body,'$.pendingRequest') IS NOT NULL
                    OR json_extract(body,'$.pendingRecovery') IS NOT NULL
                    OR coalesce(json_extract(body,'$.preparationPending'),0)!=0) LIMIT 1""",
                (target['id'], environments.KIND)).fetchone():
            error('Wait for this member’s test environment to stop and reconcile uncertain operations before retirement.', 409)


def reuse_binding(app, source, target, task):
    """Freeze scope, not transcript bodies or a second authority record."""
    from .manager_placement import task_binding
    from .schedule_handoff import latest_human
    def context(session):
        return {
            'identity': [session.get(key) for key in (
                'id', 'workspace', 'runtimeSessionId', 'nativeIdentity',
                'workingDirectory', 'executionRevision', 'configurationPendingRevision')],
            'configuration': fingerprint({key: session.get(key) for key in (
                'configuration', 'bundle', 'model', 'provider')}),
            'stop': session.get('interruptionRevision', 0),
        }
    adoption = app.db.execute("""SELECT id FROM commands
        WHERE json_extract(receipt,'$.commandAction')='assignment.adopt'
        AND json_extract(receipt,'$.targetSessionId')=?
        ORDER BY rowid DESC LIMIT 1""", (target['id'],)).fetchone()
    return {
        'version': 1, 'source': context(source), 'sourceTask': task_binding(app, source),
        'target': context(target), 'targetAccess': app.access_mode.snapshot(target)['revision'],
        'targetTask': fingerprint(task),
        'targetGeneration': (target.get('collaborationGeneration') or {}).get('id'),
        'targetHuman': (latest_human(target) or {}).get('id'),
        'targetAdoption': adoption[0] if adoption else None,
    }


async def dispatch(assignments, action, args, origin, command_id, caller):
    from .assignment_adoption import retire_idle
    app, collaboration = assignments.service, assignments.service.collaboration
    source = collaboration.source({'sessionId': args['sessionId']}, origin, caller)
    if source['id'] != args['sessionId']:
        collaboration.error('Assignment actions belong to the calling conversation.')
    identity = command_id or str(uuid.uuid4())
    digest = fingerprint([action, args, origin, source['id']])
    async with app.lock:
        duplicate = collaboration.duplicate(identity, digest)
        if duplicate:
            return duplicate
        target = collaboration.authorize(source, {'sessionId': args['targetSessionId']})
        checked(app, source, target, args, origin)
    try:
        await retire_idle(app, source, target, args, origin, identity, digest, validate=checked)
    except (OSError, RuntimeError, ValueError) as exc:
        collaboration.error(f'Could not confirm that this chat has settled. {exc}', 409)
    async with app.lock:
        duplicate = collaboration.duplicate(identity, digest)
        if duplicate:
            return duplicate
        target = collaboration.authorize(source, {'sessionId': args['targetSessionId']})
        with ExitStack() as boundary:
            prove = getattr(app.runtime, 'stopped_boundary', None)
            if not callable(prove):
                collaboration.error('This runtime cannot confirm that the earlier work has stopped.', 409)
            try:
                boundary.enter_context(prove(target))
            except (OSError, RuntimeError, ValueError) as exc:
                collaboration.error(f'Could not confirm that this chat has stopped. {exc}', 409)
            # A native writer could have changed its task after preflight. This
            # observation owns both eligibility and the saved reuse fingerprint.
            value, task = checked(app, source, target, args, origin)
            async with assignments.mutation(source, identity, additional=(target['id'],)):
                previous = copy.deepcopy(value['members'][target['id']])
                value['members'][target['id']] = {**previous, 'status': 'retired',
                    'retiredAt': time.time(), 'retiredBySessionId': source['id'],
                    'retirementOperationId': identity}
                if value.get('integration', {}).get('responsibleSessionId') == target['id']:
                    value['integration'] = {'responsibleSessionId': None,
                        'declaredBy': source['id'], 'declaredAt': time.time()}
                value['revision'] += 1
                target.pop('assignmentId', None)
                assignments.save(value)
                receipt = {'accepted': True, 'commandAction': action, 'assignmentId': value['id'],
                    'senderSessionId': source['id'], 'targetSessionId': target['id'],
                    'previousMembership': previous,
                    'reuseBinding': reuse_binding(app, source, target, task),
                    'result': {'assignment': assignments.project(value, session_id=source['id']), 'inputSent': False,
                               'activeMemberCount': assignments.active_member_count(value)}}
                collaboration.insert(identity, digest, receipt)
                app._publish_changes(sessions={source['id'], target['id']})
        return receipt
