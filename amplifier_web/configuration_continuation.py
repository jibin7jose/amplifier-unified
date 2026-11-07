"""Own-task settings waits, using native checkpoints and ordinary peer admission.

The immutable configuration request owns intent. Its private boundary receipt
owns observation; the existing command ledger owns the single continuation.
Neither an idle projection nor a successful settings write is a checkpoint.
"""
import copy
import json
import logging

from .task_binding import continuation_epoch, matches
from .update_drain import latest_input
from .errors import AppError


def capture(service, session, caller, origin):
    """Host provenance only, captured before the management queue can wait."""
    if origin != 'agent' or caller != session['id']:
        return None
    task = session.get('task') or service.coordination.task(session['id'])
    worker = getattr(service.runtime, 'workers', {}).get(session['id']) or {}
    generation = session.get('collaborationGeneration') or {}
    if (not task
            or task.get('status') != 'active' or not worker.get('runtimeOwnerId')
            or not generation.get('id') or generation.get('terminal')):
        return None
    return {'owner': worker['runtimeOwnerId'], 'generationId': generation['id'],
        'taskId': task['id'], 'taskRevision': task['revision'],
        'taskContinuationEpoch': continuation_epoch(task),
        'lastInputId': latest_input(session, service=service),
        'assignmentId': session.get('assignmentId')}


def intent_reason(service, record):
    request = record['request']
    intent = request['source'].get('continuation')
    session = service._session(request['sessionId'])
    if not intent:
        return 'This settings operation did not enroll an active task.'
    if session.get('pendingConfiguration', {}).get('operationId') != record['id']:
        return 'A different settings operation now owns this conversation.'
    if service.management.configuration_target_binding(session) != request['source']['target']:
        return 'The conversation was stopped or moved after the settings request.'
    if intent.get('assignmentId') != session.get('assignmentId') or service.assignments.blocked(session):
        return 'The assignment changed or no longer permits continuation.'
    task = session.get('task') or service.coordination.task(session['id'])
    if not task or task.get('status') != 'active' or not matches(task, intent):
        return 'The task changed or is no longer active.'
    if (session.get('collaborationGeneration') or {}).get('id') != intent['generationId']:
        return 'New input already continued this conversation.'
    if latest_input(session, 'configuration-wait:' + record['id'], service=service) != intent['lastInputId']:
        return 'New input takes precedence over the saved settings continuation.'
    try:
        service.configuration_authority.check(request['authority'])
        for authority in record.get('resumptions', []):
            service.configuration_authority.check(authority)
    except (ValueError, AppError) as exc:
        return str(exc)


def continuation_guard(service, request):
    try:
        record = service.management.configuration_record(request['senderSessionId'], request['configurationOperationId'])
        if reason := intent_reason(service, record):
            return reason
        state, boundary = record['state'], record.get('boundary', {})
        if not boundary.get('ready') or not boundary.get('yieldedGoal'):
            return 'The original native settings checkpoint is not confirmed.'
        if not (state['phase'] == 'ready' or state['phase'] == 'error' and state['effect'] == 'none'):
            return 'The original settings outcome is not settled.'
    except (OSError, ValueError, KeyError, TypeError, AppError):
        return 'The original settings operation cannot be verified.'


async def settle_worker(management, sid, operation):
    """Observe the original native checkpoint; existing settings apply owns restart."""
    service = management.service
    async with service.lock:
        record = management.configuration_record(sid, operation)
        intent = record['request']['source'].get('continuation')
        if not intent or record.get('boundary', {}).get('ready'):
            return
        if reason := intent_reason(service, record):
            raise ValueError(reason)
        row = service.runtime.workers.get(sid)
        if row is None or row['process'].returncode is not None:
            recover_checkpoint(management, service._session(sid), record)
            return
        expected = copy.deepcopy(intent)
        # The operation ID is the stable native marker. A lost reply is observed
        # through that same marker, never through a second settings operation.
        receipt = {'configurationWait': expected}
    observed = await service.runtime.handoff_boundary(sid, operation, receipt=receipt, configuration=True)
    async with service.lock:
        record = management.configuration_record(sid, operation)
        management.check_configuration_operation(service._session(sid), record)
        if reason := intent_reason(service, record):
            raise ValueError(reason)
        if observed != record.get('boundary'):
            management.configuration_transition(service._session(sid), operation, boundary=observed,
                detail='Waiting for current tools and workers; this task will continue automatically.')
            service._publish_changes(sessions={sid})


def recover_checkpoint(management, session, record):
    """Recover a lost reply only from the same saved native checkpoint.

    Caller holds the host lock; the existing writer lease excludes another
    process through this observation and commit. Absence alone is not proof.
    """
    from .task_continuity import _load_control
    service = management.service
    with service.runtime.stopped_boundary(session):
        saved = _load_control(service, session)
        proof = saved.get('configurationCheckpoint') or {}
        intent = record['request']['source']['continuation']
        task, goal = saved.get('task'), saved.get('goal')
        boundary = proof.get('boundary') or {}
        if (proof.get('operationId') != record['id'] or proof.get('intent') != intent
                or not boundary.get('ready') or not boundary.get('yieldedGoal')
                or not task or task.get('status') != 'active' or not matches(task, intent)
                or not goal or goal.get('condition') != task.get('objective')
                or boundary['yieldedGoal'] != {key: goal.get(key)
                    for key in ('task_id', 'cap', 'turns_used', 'continuations')}):
            raise ValueError('The original native settings checkpoint is unavailable; no work was replayed.')
        management.check_configuration_operation(session, record)
        if reason := intent_reason(service, record):
            raise ValueError(reason)
        management.configuration_transition(session, record['id'], boundary=boundary)
        service._publish_changes(sessions={session['id']})


async def release_boundary(management, sid, operation):
    """Release only this failed/cancelled operation's original worker marker."""
    service = management.service
    try:
        record = management.configuration_record(sid, operation)
        intent = record['request']['source'].get('continuation')
        state = record['state']
        if not intent or state['effect'] != 'none' or state['phase'] not in {'error', 'cancelled'}:
            return
        row = service.runtime.workers.get(sid)
        if not row or row.get('runtimeOwnerId') != intent['owner'] or row['process'].returncode is not None:
            return
        await service.runtime.handoff_boundary(sid, operation, cancel=True,
            receipt={'configurationWait': intent}, configuration=True)
    except (OSError, ValueError, RuntimeError):
        # No new operation or replacement worker is contacted. Preserve the
        # original failure; the native guard also releases on changed intent.
        logging.getLogger(__name__).warning('Could not release the original settings boundary', exc_info=True)


def publish_outcome(management, session, operation):
    """Under the host writer lock (or startup); duplicates reuse the same input."""
    service = management.service
    try:
        record = management.configuration_record(session['id'], operation)
    except (OSError, ValueError, AppError):
        # Recovery retains the original failure; an unreadable operation cannot
        # authorize a follow-up and must not mask the original command outcome.
        return
    intent = record['request']['source'].get('continuation')
    boundary = record.get('boundary', {})
    goal = boundary.get('yieldedGoal')
    state = record['state']
    if (not intent or not boundary.get('ready') or not goal
            or goal.get('cap') is not None and goal.get('turns_used', 0) >= goal['cap']
            or intent_reason(service, record)
            or not (state['phase'] == 'ready' or state['phase'] == 'error' and state['effect'] == 'none')):
        return
    identity = 'configuration-wait:' + operation
    owner = service.collaboration
    saved = service.db.execute('SELECT receipt FROM commands WHERE id=?', (identity,)).fetchone()
    if saved:
        request = json.loads(saved[0])
    else:
        # An already admissible input consumes readiness; don't add a second
        # model turn just to acknowledge that settings were applied.
        if any(not owner.guard(json.loads(encoded)) for _, encoded in owner.fair_rows(session['id'])):
            return
        from .collaboration import PROTOCOL
        wait = owner.saved_wait(session, identity)
        wait.update({key: intent[key] for key in ('taskId', 'taskRevision', 'taskContinuationEpoch')})
        ready = state['phase'] == 'ready'
        text = ('The requested settings are applied and verified.' if ready else
                'The settings change failed without applying a change: ' + state.get('error', 'Inspect the saved operation.'))
        request = {'accepted': True, 'commandAction': 'configuration.apply', 'requestId': identity,
            'senderSessionId': session['id'], 'target': {'sessionId': session['id']},
            'workspace': session['workspace'], 'assignmentId': intent.get('assignmentId'),
            'origin': 'system', 'protocol': PROTOCOL, 'subscription': wait,
            'configurationOperationId': operation,
            'terminalOutcome': {'status': 'observed', 'kind': 'observation',
                'outcome': 'configuration-ready' if ready else 'configuration-failed',
                'qualified': False, 'outcomeRevision': 1,
                'text': text + ' Continue the existing objective from its saved progress and used budget. '
                    'Do not repeat previous inputs or completed or uncertain effects.'}}
        owner.insert(identity, identity, request)
        service.db.commit()
    owner.enqueue_continuation(request)
