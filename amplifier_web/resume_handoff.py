"""One-hop human provenance, never transferred ownership or human input.

Only fresh coordination.send calls mint these host-owned command fields. A
consumer reserves them in the same transaction as its fresh member input; the
existing unique humanResume authorization key fences alternate handoff IDs.
"""
import copy
import time

from amplifier_operations.coordination import fingerprint

from .peer_resume import latest_intent, question_settings


def direct_human(session):
    message = latest_intent(session)
    if (message and message.get('inputOrigin') in {'ui', 'user', 'voice'}
            and not message.get('questionId') and not message.get('scheduledRunId')
            and not message.get('peerEnvelope') and not message.get('sourceRequestId')
            and not message.get('hostAction')
            and message.get('inputId')):
        return message


def assignment_scope(app, owner, member):
    from .completed_followup import owner_assignment
    value = owner_assignment(app, owner, member)
    if (not value or owner['workspace'] != member['workspace']
            or member.get('sessionKind', 'root') != 'root'):
        raise ValueError('A resume handoff must name the actual commissioning owner and its current member.')
    return {'id': value['id'], 'revision': value['revision']}


def boundary(app, rows, captured=None):
    """Constant-size resident control witnesses for exactly three participants."""
    old = (captured or {}).get('settings')
    settings = question_settings(app, rows[0], rows[1], old)
    settings['sessions'].update(question_settings(app, rows[0], rows[2], old)['sessions'])
    return {'settings': settings, 'participants': {row['id']: {
        'workspace': row['workspace'],
        'nativeIdentity': row.get('runtimeSessionId') or row.get('nativeIdentity') or row['id'],
        'stop': row.get('interruptionRevision', 0),
        'human': (latest_intent(row) or {}).get('id'),
        'assignment': {key: (app.assignments.current(row) or {}).get(key)
                       for key in ('id', 'revision', 'status', 'ownerSessionId')},
    } for row in rows}}


def prepare(app, origin, owner, value, lineage):
    binding, active = app.collaboration.active_binding(origin)
    human = direct_human(origin)
    if (not human or human['id'] != value['sourceMessageId']
            or human['inputId'] not in lineage.get('sourceInputIds', [])):
        raise ValueError('Resume handoff requires the latest direct human input in the current native generation.')
    if len(human.get('text', '')) > 4000:
        raise ValueError('The complete human instruction exceeds the bounded resume handoff; use a concise direct human request.')
    member = app._session(value['memberSessionId'])
    if origin['workspace'] != owner['workspace'] or origin['workspace'] != member['workspace']:
        raise ValueError('Resume handoff origin, owner and member must share the same workspace.')
    if len({origin['id'], owner['id'], member['id']}) != 3:
        raise ValueError('Use ordinary owner resume for your own member; handoffs require three distinct roots.')
    assignment = assignment_scope(app, owner, member)
    if (member.get('interruptionRevision', 0) != value['expectedInterruptionRevision']
            or human.get('createdAt', 0) <= (member.get('lastInterruption') or {}).get('at', 0)):
        raise ValueError('The member Stop is newer than the human instruction or its selected revision changed.')
    return {'version': 1, 'sourceSessionId': origin['id'], 'ownerSessionId': owner['id'],
        'memberSessionId': member['id'], 'sourceMessageId': human['id'],
        'sourceInputId': human['inputId'], 'textDigest': fingerprint(human.get('text', '')),
        'assignment': assignment, 'boundary': boundary(app, (origin, owner, member)),
        # This committed witness may outlive normal source-turn completion.
        # Never substitute a later source generation to renew old authority.
        'sourceWitness': {'generationId': active['id'],
            'nativeIdentity': binding.get('_runtimeSessionId'),
            'inputId': human['inputId'], 'interruptionRevision': active.get('interruptionRevision', 0)},
        'consumption': {'status': 'available'}}


def participants(app, request):
    scope = request.get('resumeHandoff') or {}
    witness = scope.get('sourceWitness') or {}
    if (scope.get('version') != 1 or request.get('commandAction') != 'coordination.send'
            or request.get('origin') != 'agent' or request.get('mode') not in {'queue', 'auto'}
            or request.get('senderSessionId') != scope.get('sourceSessionId')
            or request.get('target', {}).get('sessionId') != scope.get('ownerSessionId')
            or not witness.get('generationId') or not witness.get('nativeIdentity')
            or witness.get('generationId') != request.get('sourceGenerationId')
            or witness.get('inputId') != scope.get('sourceInputId')
            or witness.get('inputId') not in request.get('sourceInputIds', [])
            or witness.get('interruptionRevision') != request.get('sourceInterruptionRevision')):
        raise ValueError('Choose an explicit host-issued resume handoff, not a historical peer message.')
    origin = app._session(scope['sourceSessionId'])
    owner = app.collaboration.request_target(origin, request)
    member = app._session(scope['memberSessionId'])
    if origin['workspace'] != owner['workspace'] or origin['workspace'] != member['workspace']:
        raise ValueError('Resume handoff origin, owner and member must still share the same workspace.')
    return scope, origin, owner, member


def guard(app, request, *, command_id=None):
    """Recheck through QUEUED and SUBMITTING, including final native admission."""
    scope, origin, owner, member = participants(app, request)
    if request.get('cancellation'):
        return 'The originating resume handoff was cancelled.'
    if request.get('expiresAt', 0) <= time.time():
        return 'The originating resume handoff expired before member admission.'
    consumption = scope.get('consumption') or {}
    if (consumption.get('status') != 'available'
            and (not command_id or consumption.get('requestId') != command_id)):
        return 'This handoff is already reserved; inspect its exact member resume receipt.'
    human = direct_human(origin)
    if (not human or human['id'] != scope['sourceMessageId']
            or human.get('inputId') != scope['sourceInputId']
            or fingerprint(human.get('text', '')) != scope['textDigest']
            or human.get('createdAt', 0) <= (member.get('lastInterruption') or {}).get('at', 0)):
        return 'The original human instruction or member Stop changed.'
    if (scope['boundary'] != boundary(app, (origin, owner, member), scope['boundary'])
            or scope['assignment'] != assignment_scope(app, owner, member)):
        return 'The resume handoff task, access, configuration, placement, assignment or human/Stop boundary changed.'
    if any(row.get('fileAccessPending') or app.assignments.blocked(row)
           for row in (origin, owner, member)):
        return 'A resume handoff participant has pending access or stopped assignment admission.'


def consume(app, owner, member, request_id):
    request = app.collaboration.receipt(request_id)
    scope, origin, actual_owner, actual_member = participants(app, request)
    if owner['id'] != actual_owner['id'] or member['id'] != actual_member['id']:
        raise ValueError('The handoff names a different commissioning owner or member.')
    if request.get('delivery') not in {'accepted', 'applied'}:
        raise ValueError('The handoff needs positive native admission, not queued, submitting or unknown delivery.')
    binding, active = app.collaboration.active_binding(owner)
    inputs = {row.get('inputId') for row in binding.get('_inputBindings', [])}
    if request.get('inputId') not in inputs.intersection(active.get('inputIds', [])):
        raise ValueError('The exact handoff must belong to the owner’s current authenticated native input.')
    if reason := guard(app, request):
        raise ValueError(reason)
    return origin, direct_human(origin), request


def reserve(app, request_id, command_id):
    """Caller holds the service lock and the fresh send's immediate transaction."""
    request = app.collaboration.receipt(request_id)
    if reason := guard(app, request):
        raise ValueError(reason)
    request['resumeHandoff']['consumption'] = {
        'status': 'reserved', 'requestId': command_id, 'reservedAt': time.time()}
    app.collaboration.save(request_id, request)


def view(app, scope):
    """No arbitrary claims or private source transcript in the peer envelope."""
    human = direct_human(app._session(scope['sourceSessionId']))
    return {**{key: copy.deepcopy(scope[key]) for key in (
        'version', 'sourceSessionId', 'sourceMessageId', 'sourceInputId',
        'ownerSessionId', 'memberSessionId', 'assignment')},
        'humanInstruction': {'text': human['text'], 'truncated': False,
            'readRef': {'action': 'coordination.read', 'args': {
                'sessionId': scope['sourceSessionId'], 'messageId': scope['sourceMessageId'],
                'scope': 'all', 'textLimit': 4000}}}}