"""One fresh peer input backed by the commissioning owner's human request."""
import copy
import time

from amplifier_operations.coordination import fingerprint


def latest_intent(session):
    """Include question answers as superseding input, not as implicit grants."""
    return next((row for row in reversed(session.get('messages', []))
                 if row.get('role') == 'user' and (row.get('questionId')
                 or row.get('inputOrigin') in {'ui', 'user', 'voice'})), None)


def human_input(app, session):
    """Resolve a direct human answer from its canonical record, never peer prose.

    Only the peer-resume path uses this resolver. In particular, a question
    receipt does not acquire configuration, schedule or tool-approval authority.
    Its exact input must still belong to the current transport-bound generation.
    """
    message = latest_intent(session)
    if not message or message.get('scheduledRunId'):
        return None, None
    if not message.get('questionId'):
        return message, None
    record = app.questions.for_input(session['id'], message.get('inputId'))
    if not record:
        return None, None
    answer = record['answer']
    provenance = answer.get('provenance') or {}
    prompt = f'Answer to “{record["prompt"]}”:\n{answer["text"]}\n\nFor: {record["dependency"]}'
    if (message.get('via') != 'question'
            or record['id'] != message['questionId']
            or record['revision'] != answer.get('questionRevision', 0) + 1
            or provenance.get('origin') not in {'ui', 'user'}
            or provenance.get('via') != 'ui'
            or provenance.get('messageId') or provenance.get('sourceRequestId')
            or message.get('answerProvenance') != provenance
            or message.get('text') != prompt
            or record['delivery'].get('status') == 'rejected'):
        return None, None
    # Delivery acknowledgements may settle while native preparation runs. Bind
    # immutable question/answer meaning, not mutable receipt projection fields.
    proof = {'id': record['id'], 'revision': record['revision'],
             'digest': fingerprint({key: record.get(key) for key in
                 ('prompt', 'dependency', 'options', 'contentRevision', 'artifacts', 'answer')})}
    return message, proof


def next_message_state(row):
    pending = row.get('pendingBundleSelection')
    return {'revision': row.get('nextMessageRevision', 0),
        'appliedRevision': row.get('appliedConfigurationRevision', -1),
        'bundle': row.get('bundle'), 'desiredBundle': (pending or {}).get('bundle'),
        'pendingBundle': fingerprint(pending) if pending else None,
        'pendingModel': fingerprint(row['pendingModelSelection']) if row.get('pendingModelSelection') else None}


def question_settings(app, source, target, captured=None):
    """Use existing control/placement and access-policy revisions, not payloads."""
    # AppService may run without a management adapter (including native clients).
    # Snapshot only resident boundary fields; never prepare or inspect a runtime.
    result = {'configurationRevision': app.state.get('configurationRevision', 0),
        'sessions': {row['id']: {
            'accessRevision': app.access_mode.snapshot(row)['revision'],
            'task': {key: (row.get('task') or app.coordination.task(row['id'])).get(key)
                     for key in ('id', 'revision', 'status', 'continuationEpoch')},
            'boundary': fingerprint({key: row.get(key) for key in
                ('workspace', 'workingDirectory', 'executionRevision', 'selection',
                 'configurationPendingRevision', 'recoveryControlRevision')}),
            'nextMessage': next_message_state(row),
            'controls': fingerprint({key: app.state.get('runtimeControl', {}).get(row['id'], {}).get(key)
                for key in ('configuration.apply', 'configuration.toggle', 'provider.select',
                            'provider.reset', 'mode.set', 'mode.clear', 'budget.set', 'budget.clear')}),
        } for row in (source, target)}}
    for sid, value in result['sessions'].items():
        old = (captured or {}).get('sessions', {}).get(sid, {}).get('nextMessage')
        now = value['nextMessage']
        if (old and old['revision'] > 0
                and old['revision'] == now['revision'] == now['appliedRevision']
                and old['appliedRevision'] < now['appliedRevision']
                and not now['pendingBundle'] and not now['pendingModel']
                and now['bundle'] == (old['desiredBundle'] or old['bundle'])):
            # The real runtime acknowledgement consumed exactly the already-saved
            # intent. A later edit, wrong bundle/revision or missing ack refuses.
            value['nextMessage'] = old
    return result


def latest_root_generation(target):
    return next((event for event in reversed(target.get('generations', []))
                 if event.get('sessionId', target['id']) == target['id']
                 and event.get('rootSessionId', target['id']) == target['id']), None)


def safety_halt(target):
    active_id = (target.get('collaborationGeneration') or {}).get('id')
    # A later detach of the same root does not erase its earlier safety halt.
    # This retained lifecycle list is bounded; do not inspect transcript bodies.
    retained_safety = active_id and any(
        event.get('sessionId', target['id']) == target['id']
        and event.get('rootSessionId', target['id']) == target['id']
        and event.get('generation_id') == active_id
        and event.get('event') in {'generation.failed', 'generation.detached'}
        and event.get('error_category') in {'content_filter', 'computer_capture_stop'}
        for event in target.get('generations', []))
    if retained_safety or (target.get('failure') or {}).get('category') in {'content_filter', 'computer_capture_stop'}:
        return ('A safety-related failure is still saved. Inspect it and resolve the halted operation; '
                'Stop does not clear it. A separate permitted human request can use normal chat input.')


def retained_terminal_evidence(target):
    """Witness the old terminal state, not delivery or success of its inputs.

    A fresh human request supplies authority. Native service observations need
    not have peer receipts; neither missing nor unknown receipts are repaired.
    """
    from .session_health import current_terminal_failure
    failure = target.get('failure') or {}
    active = target.get('collaborationGeneration') or {}
    terminal = latest_root_generation(target) or {}
    if (failure.get('generationId') or failure.get('category') != 'unknown'
            or not active.get('inputIds') or not current_terminal_failure(target)):
        return None
    return {'generation': copy.deepcopy(terminal)}


def prepare(app, source, target, value, lineage, boundary):
    origin = source
    handoff = None
    if value.get('sourceRequestId'):
        from .resume_handoff import consume
        origin, human, handoff = consume(app, source, target, value['sourceRequestId'])
        question = None
    else:
        human, question = human_input(app, source)
    if not handoff and (not human or human['id'] != value['sourceMessageId']
            or human.get('inputId') not in lineage.get('sourceInputIds', [])):
        raise ValueError('Resume requires the latest actual human request in the owner’s current admitted input.')
    assignment = app.assignments.current(source)
    if (not assignment or assignment['status'] != 'active'
            or assignment['ownerSessionId'] != source['id'] or source['id'] == target['id']
            or assignment['members'].get(target['id'], {}).get('status') != 'bound'
            or (app.assignments.current(target) or {}).get('id') != assignment['id']):
        raise ValueError('Resume only a current commissioned member of your active assignment.')
    if (value['expectedInterruptionRevision'] != target.get('interruptionRevision', 0)
            or human.get('createdAt', 0) <= (target.get('lastInterruption') or {}).get('at', 0)):
        raise ValueError('Stop changed or occurred after the human request; use a fresh human resume instruction.')
    if target.get('status') == 'stopping':
        raise ValueError('Wait for the recipient to settle before requesting resume.')
    if reason := safety_halt(target):
        raise ValueError(reason)
    recovery = None
    # A shutdown interruption is settled only when the old generation ended
    # and the existing native ownership probe proves no writer remains.
    interrupted = target.get('status') == 'interrupted'
    if interrupted or target.get('status') == 'error':
        failure = target.get('failure') or {}
        active = target.get('collaborationGeneration') or {}
        evidence = retained_terminal_evidence(target)
        prove = getattr(app.runtime, 'stopped_boundary', None)
        if ((not interrupted and not evidence
                and (not failure.get('generationId') or failure['generationId'] != active.get('id')))
                or not active.get('id') or not active.get('terminal') or not callable(prove)
                or target.get('configurationBusy')
                or any(w.get('status') in {'queued', 'starting', 'running', 'working', 'stopping'}
                       for w in target.get('workers', []))):
            raise ValueError('The recipient has not reached a recoverable stopped boundary.')
        try:
            proof = boundary.enter_context(prove(target))
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError(f'Could not confirm that the recipient has stopped. {exc}') from None
        recovery = {**proof, 'failure': copy.deepcopy(failure),
                    **({'generation': copy.deepcopy(active)} if interrupted else {}),
                    **({'terminalEvidence': evidence} if evidence else {})}
    key = fingerprint([origin['id'], human['id'], target['id'], target.get('interruptionRevision', 0)])
    previous = app.db.execute("""SELECT id FROM commands
        WHERE json_extract(receipt,'$.humanResume.authorizationKey')=?""", (key,)).fetchone()
    if previous:
        raise ValueError(f'This human resume request already has receipt {previous[0]}; inspect it instead of sending again.')
    return {
        'sourceMessageId': human['id'], 'sourceInputId': human['inputId'],
        'textDigest': fingerprint(human.get('text', '')),
        'assignmentId': assignment['id'], 'assignmentRevision': assignment['revision'],
        'targetHumanMessageId': (latest_intent(target) or {}).get('id'),
        'authorizationKey': key,
        **({'handoffRequestId': handoff['requestId'],
            'ownerGenerationId': lineage['sourceGenerationId']} if handoff else {}),
        **({'questionAnswer': question,
            'questionSettings': question_settings(app, source, target)}
           if question else {}),
        **({('interruptedRecovery' if interrupted else 'failedRecovery'): recovery} if recovery else {}),
    }


def guard(app, receipt, source, target):
    scope = receipt['humanResume']
    if scope.get('handoffRequestId'):
        if receipt.get('expiresAt', 0) <= time.time():
            return 'The handoff-backed member resume expired before input admission.'
        from .resume_handoff import guard as handoff_guard, participants, direct_human
        handoff = app.collaboration.receipt(scope['handoffRequestId'])
        if reason := handoff_guard(app, handoff, command_id=receipt['requestId']):
            return reason
        _, origin, owner, member = participants(app, handoff)
        if owner['id'] != source['id'] or member['id'] != target['id']:
            return 'The resume handoff endpoint changed.'
        if (source.get('collaborationGeneration') or {}).get('id') != scope['ownerGenerationId']:
            return 'The consuming owner generation changed before member admission.'
        human, question = direct_human(origin), None
    else:
        human, question = human_input(app, source)
    if scope.get('questionAnswer'):
        # The queue claim is not input admission. Preparation may outlive its
        # deadline; accepted/unknown operations never use this pending guard.
        if receipt.get('expiresAt', float('inf')) <= time.time():
            return 'The question-backed resume expired before input admission.'
        if scope.get('questionSettings') != question_settings(app, source, target, scope.get('questionSettings')):
            return 'The question-backed resume settings or placement changed.'
    recovered = scope.get('failedRecovery')
    interrupted = scope.get('interruptedRecovery')
    if reason := safety_halt(target):
        return reason
    if target.get('status') == 'interrupted' and not interrupted:
        return 'The interrupted recipient needs a fresh stopped-boundary check.'
    if interrupted and (interrupted['generation'] != target.get('collaborationGeneration')
                        or interrupted['failure'] != (target.get('failure') or {})):
        return 'The interrupted recipient changed before recovery.'
    if target.get('status') == 'error' and (not recovered or recovered['failure'] != target.get('failure')):
        return 'The recipient failure changed before recovery.'
    if recovered and recovered.get('terminalEvidence') and (
            recovered['failure'] != target.get('failure')
            or retained_terminal_evidence(target) != {
                'generation': recovered['terminalEvidence'].get('generation')}):
        # Older saved receipts may also retain peer input snapshots. Keep them
        # as history; the terminal witness is what this fresh admission checks.
        return 'The retained terminal generation changed before recovery.'
    assignment = app.assignments.current(source)
    if (not human or human['id'] != scope['sourceMessageId']
            or human.get('inputId') != scope['sourceInputId']
            or question != scope.get('questionAnswer')
            or fingerprint(human.get('text', '')) != scope['textDigest']
            or human.get('createdAt', 0) <= (target.get('lastInterruption') or {}).get('at', 0)
            or (latest_intent(target) or {}).get('id') != scope['targetHumanMessageId']):
        return 'The human resume instruction or recipient input changed.'
    if (not assignment or assignment['id'] != scope['assignmentId']
            or assignment['revision'] != scope['assignmentRevision']
            or assignment['status'] != 'active' or assignment['ownerSessionId'] != source['id']
            or assignment['members'].get(target['id'], {}).get('status') != 'bound'
            or (app.assignments.current(target) or {}).get('id') != assignment['id']):
        return 'The commissioned assignment changed before resume.'


def pending(receipt):
    # An admitted resume is not a reusable exemption for later replies/waits.
    return bool(receipt.get('humanResume') and receipt.get('delivery') in {'queued', 'submitting'})
