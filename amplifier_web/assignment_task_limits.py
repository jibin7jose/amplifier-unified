"""Manager task controls, using the member's existing native task controller.

Corrections and accepted contribution closeout require no member model turn.
They never send input, clear Stop or reset usage.
"""
import copy

from amplifier_operations.coordination import fingerprint


def definitions(schema, string):
    target = {'sessionId': string(200), 'targetSessionId': string(200)}
    return {
        'assignment.task.read': ('Read one current assignment member’s saved objective, status, limit and used turns, or an exact manager task-control receipt. Passive: does not start its worker.',
            schema({**target, 'commandId': string(200)}, ['sessionId', 'targetSessionId'])),
        'assignment.task.complete': ('Owner records acceptance of a settled member’s exact saved contribution using existing task completion. Requires evidence covering that objective, not just a passing diagnostic. No member model turn or input; preserves Stop, used turns, history, resources and unresolved operation/question records. Does not retire membership or declare unrelated incidents resolved. Inspect the original command receipt after an uncertain response.',
            schema({**target, 'expectedAssignmentRevision': {'type': 'integer', 'minimum': 1},
                'taskId': string(200), 'expectedRevision': {'type': 'integer', 'minimum': 1},
                'evidence': {'type': 'array', 'minItems': 1, 'maxItems': 100,
                    'items': {**string(4000), 'minLength': 1}}},
                ['sessionId', 'targetSessionId', 'expectedAssignmentRevision', 'taskId',
                 'expectedRevision', 'evidence'])),
        'assignment.task.setLimit': ('Owner explicitly revises only a current member’s saved task turn limit, even when exhausted. Null removes that limit. Preserve deliberate user limits unless the user authorized their revision. Increasing/removing an existing limit requires saved Full access or the actual user message/answered question already authorizing it; do not ask again for an answer already saved. Preserve used turns, task identity, evidence, Stop/pause and all other budgets. Sends no input; continue separately. Read the saved receipt after an uncertain response instead of submitting a new operation.',
            schema({**target, 'expectedAssignmentRevision': {'type': 'integer', 'minimum': 1},
                'taskId': string(200), 'expectedRevision': {'type': 'integer', 'minimum': 1},
                'maxTurns': {'type': ['integer', 'null'], 'minimum': 1},
                'correction': {**string(4000), 'minLength': 1},
                'sourceMessageId': string(200), 'questionId': string(200),
                'expectedQuestionRevision': {'type': 'integer', 'minimum': 1}},
                ['sessionId', 'targetSessionId', 'expectedAssignmentRevision', 'taskId',
                 'expectedRevision', 'maxTurns', 'correction'])),
    }


def scope(owner, args, origin, caller):
    app, collaboration = owner.service, owner.service.collaboration
    source = collaboration.source({'sessionId': args['sessionId']}, origin, caller, hydrate=False)
    if source['id'] != args['sessionId']:
        collaboration.error('Assignment actions belong to the calling conversation.')
    target = collaboration.authorize(source, {'sessionId': args['targetSessionId']}, hydrate=False)
    value = owner.current(source)
    if (not value or value['status'] != 'active' or value['ownerSessionId'] != source['id']
            or source['id'] == target['id']
            or value['members'].get(target['id'], {}).get('status') != 'bound'
            or (owner.current(target) or {}).get('id') != value['id']):
        collaboration.error('Only the current assignment owner can manage this bound member’s saved task.', 409)
    return source, target, value


def saved(app, target):
    from .task_continuity import _load_control
    try:
        return _load_control(app, target)
    except ValueError as exc:
        app.collaboration.error(str(exc), 409)


def readback(source, target, value, state, receipt=None):
    task = state.get('task') or {}
    goal = state.get('goal') or task.get('goalState') or {}
    return {'assignmentId': value['id'], 'assignmentRevision': value['revision'],
        'targetSessionId': target['id'], 'task': {key: task.get(key) for key in
            ('id', 'revision', 'status', 'objective', 'maxTurns')}, 'turnsUsed': goal.get('turns_used', 0),
        'receipt': copy.deepcopy(receipt), 'inputSent': False,
        'readRef': {'action': 'assignment.task.read', 'args': {
            'sessionId': source['id'], 'targetSessionId': target['id']}}}


def authorization(app, source, args, origin):
    """Use existing user-owned policy/answer records, never model-authored prose."""
    if origin in {'ui', 'user'}:
        return {'kind': 'user'}
    mode = app.access_mode.snapshot(source)
    if mode['mode'] == 'full':
        return {'kind': 'full-access', 'revision': mode['revision']}
    if args.get('sourceMessageId') and args.get('questionId'):
        app.collaboration.error('Cite either the original user message or the saved answered question.', 400)
    if args.get('questionId'):
        record = app.questions.store.get(source['id'], args['questionId'])
        answer = app.questions.answer_for_dependency(source['id'], record['id'])
        provenance = answer.get('provenance') or {}
        if (record['revision'] != args.get('expectedQuestionRevision')
                or not (provenance.get('origin') in {'ui', 'user'}
                    or provenance.get('inputOrigin') in {'ui', 'user', 'voice'}
                    and provenance.get('messageId'))):
            app.collaboration.error('Use the exact saved question revision with a verified user answer.', 409)
        return {'kind': 'question', 'id': record['id'], 'revision': record['revision'],
                'sha256': fingerprint(answer)}
    if args.get('sourceMessageId'):
        from .schedule_handoff import human_messages
        message = next((row for row in human_messages(source)
                        if row['id'] == args['sourceMessageId']
                        and not row.get('questionId') and not row.get('scheduledRunId')), None)
        if message:
            return {'kind': 'human-message', 'id': message['id'],
                    'sha256': fingerprint({key: message.get(key) for key in
                        ('text', 'inputId', 'inputOrigin', 'createdAt')})}
    app.collaboration.error('Increasing this saved limit needs its existing user authorization: cite the user message or answered question, or use saved Full access.', 409)


def mutation_state(source, target, value):
    """Recheck the same owner/location/Stop/settings boundary after preparation."""
    return {'assignmentId': value['id'], 'assignmentRevision': value['revision'],
        'source': [source['id'], source['workspace'], source.get('interruptionRevision', 0),
                   source.get('configurationPendingRevision', 0)],
        'target': [target['id'], target['workspace'], target.get('interruptionRevision', 0),
                   target.get('executionRevision'), target.get('workingDirectory'),
                   target.get('runtimeSessionId'), target.get('nativeIdentity'),
                   target.get('configurationPendingRevision', 0)]}


def receipt_for(state, identity, source, target):
    receipt = (state.get('taskReceipts', {}).get(identity) or {}).get('receipt')
    proof = ((receipt or {}).get('managerLimit')
             or (receipt or {}).get('managerCompletion') or {})
    if receipt and (proof.get('ownerSessionId') != source['id']
                    or proof.get('targetSessionId') != target['id']):
        raise ValueError('This command ID belongs to a different task operation.')
    return receipt


async def dispatch(owner, action, args, origin, command_id, caller):
    from .management import Management
    from .task_continuity import dispatch as task_dispatch, publish_task_observation
    app = owner.service
    async with app.lock:
        source, target, value = scope(owner, args, origin, caller)
        state = saved(app, target)
        identity = args.get('commandId') if action == 'assignment.task.read' else command_id
        try:
            receipt = receipt_for(state, identity, source, target) if identity else None
        except ValueError as exc:
            app.collaboration.error(str(exc), 409)
        if action == 'assignment.task.read':
            result = readback(source, target, value, state, receipt)
            if identity and receipt is None:
                result['receiptStatus'] = 'not_observed'
            return {'accepted': True, 'result': result}
        if not isinstance(identity, str) or not identity or len(identity) > 200:
            app.collaboration.error('Manager task controls require a stable command ID.', 400)
        request_digest = fingerprint([action, args, origin, source['id']])
        if receipt:
            proof = receipt.get('managerLimit') or receipt.get('managerCompletion') or {}
            if proof.get('requestFingerprint') != request_digest:
                app.collaboration.error('This command ID was already used with different contents.', 409)
            publication, _ = publish_task_observation(app, target['id'], {
                'task': state['task'], 'history': state.get('taskHistory', []), 'goal': state.get('goal')})
            return {'accepted': True, 'duplicate': True,
                    'result': {**readback(source, target, value, state, receipt), 'publication': publication}}
        task = state.get('task') or {}
        if (value['revision'] != args['expectedAssignmentRevision']
                or task.get('id') != args['taskId'] or task.get('revision') != args['expectedRevision']):
            app.collaboration.error('The assignment or saved task changed. Read its current state before changing it.', 409)
        completing = action == 'assignment.task.complete'
        old, new = task.get('maxTurns'), args.get('maxTurns')
        relaxed = not completing and old is not None and (new is None or new > old)
        authority = authorization(app, source, args, origin) if relaxed else {'kind': 'not-increased'}
        captured = mutation_state(source, target, value)

        def guard():
            current_source, current_target, current = scope(owner, args, origin, caller)
            if origin not in {'ui', 'user'}:
                app.collaboration.active_binding(current_source)
            if mutation_state(current_source, current_target, current) != captured:
                app.collaboration.error('The assignment, Stop or execution location changed before the task control.', 409)
            if (current_target.get('configurationBusy')
                    or Management.unresolved_configuration(current_target)):
                app.collaboration.error('The member’s settings change is unresolved; its saved task was not changed.', 409)
            if relaxed and authorization(app, current_source, args, origin) != authority:
                app.collaboration.error('The saved authorization changed before the task control.', 409)

        guard()
        proof = {'ownerSessionId': source['id'], 'targetSessionId': target['id'],
            'assignmentId': value['id'], 'assignmentRevision': value['revision'],
            'requestFingerprint': request_digest}
        if not completing:
            proof['authorization'] = authority
        forwarded = ({'condition': task['objective'], 'evidence': args['evidence'],
                      '_managerCompletion': proof} if completing else
                     {'maxTurns': new, 'correction': args['correction'], '_managerLimit': proof})
    # Native task CAS owns the write and its receipt. No provider request or
    # input is needed; a lost response is inspected through assignment.task.read.
    result = await task_dispatch(app, 'task.complete' if completing else 'task.update', {
        'sessionId': target['id'], 'expectedTaskId': args['taskId'],
        'expectedRevision': args['expectedRevision'], **forwarded,
    }, origin, identity, False, guard=guard)
    result['result']['currentRef'] = {'action': 'assignment.task.read', 'args': {
        'sessionId': source['id'], 'targetSessionId': target['id'], 'commandId': identity}}
    result['result'].update(inputSent=False, targetSessionId=target['id'])
    return result
