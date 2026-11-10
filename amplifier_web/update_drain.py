"""Cooperative app replacement using the existing update and input receipts.

Staging does not pause work. Only positively unsubmitted messages wait during
actual replacement and restart. Older pre-replacement holds can be withdrawn
through their existing receipts. No input or uncertain effect is replayed.
"""
import copy
import json
import uuid


class UpdateDeferred(Exception):
    """Raised only before transport admission, never after a partial write."""
    def __init__(self, attempt_id):
        super().__init__("Message saved while the app update finishes.")
        self.attempt_id = attempt_id


def held(service):
    return (service.state.get('updates', {}).get('drain') or {}).get('phase') in {'draining', 'replacing', 'releasing'}


def new_work(action, args):
    """The updater's existing new-work categories, shared by both phases."""
    return (action in {'question.answer', 'conversation.send', 'conversation.retry', 'session.takeover',
        'worker.spawn', 'worker.steer', 'worker.message', 'call.start', 'feedback.submit', 'feedback.comment',
        'feedback.get', 'feedback.reconcile', 'feedback.update', 'feedback.close', 'feedback.reopen',
        'feedback.attachments.review', 'feedback.attachments.add', 'feedback.attachments.reconcile'}
        or action == 'session.naming' and args.get('regenerate') or action == 'session.naming.backfill'
        or action.startswith('smartTools.') and action not in {'smartTools.context', 'smartTools.result'})


def input_gate(service, sid=None, identity=None):
    current = service.state.get('updates', {}).get('drain') or {}
    if (sid and identity and current.get('phase') == 'releasing'
            and current.get('releasingInput') == [sid, identity]):
        return
    if held(service):
        raise UpdateDeferred(service.state['updates']['drain']['attemptId'])


def retains_worker(service, runtime, sid):
    drain = service.state.get('updates', {}).get('drain') or {}
    boundary = drain.get('workers', {}).get(sid) or {}
    worker = getattr(runtime, 'workers', {}).get(sid) or {}
    return (drain.get('phase') in {'waiting', 'draining'} and boundary and not boundary.get('ready')
            and worker.get('runtimeOwnerId') == boundary.get('owner'))


def bind(service, runtime):
    if runtime is None:
        return
    runtime.update_input_gate = lambda sid=None, identity=None: input_gate(service, sid, identity)
    def message_guard(sid, message):
        proof = message.get('updateAdmission')
        if proof and proof['interruptionRevision'] != service._session(sid).get('interruptionRevision', 0):
            from .runtime import RuntimeStartupError
            raise RuntimeStartupError('Stopped after this message was saved. Nothing was sent.')
    runtime.update_message_guard = message_guard
    if retention := getattr(runtime, 'retention', None):
        existing = retention.protected
        retention.protected = lambda sid: existing(sid) or retains_worker(service, runtime, sid)


def binding(session):
    return {key: copy.deepcopy(session.get(key, default)) for key, default in (
        ('interruptionRevision', 0), ('executionRevision', 0), ('workspace', None),
        ('workingDirectory', None), ('configurationPendingRevision', 0))}


def wait_receipt(service, sid, identity):
    saved = service.db.execute('SELECT receipt FROM commands WHERE id=?', (identity,)).fetchone()
    receipt = json.loads(saved[0]) if saved else {}
    if receipt.get('commandAction') != 'task.waitForUpdate' or receipt.get('arguments', {}).get('sessionId') != sid:
        return
    boundary = receipt.get('boundary') or {}
    source = boundary.get('continuationSourceRequestId')
    if source:
        request = service.collaboration.receipt(source)
        wait = request.get('subscription') or {}
        revision = wait.get('lastClaimedRevision', 0)
        receipt['continuation'] = {'status': wait.get('status', 'unknown')}
        if revision:
            identity = service.collaboration.continuation_identity(wait, revision)
            receipt['continuation'].update(inputId=identity,
                resultRef={'action': 'coordination.result', 'args': {'requestId': identity}})
    elif boundary.get('continuationRequestId'):
        identity = boundary['continuationRequestId']
        receipt['continuation'] = {'inputId': identity,
            'resultRef': {'action': 'coordination.result', 'args': {'requestId': identity}}}
    return {'status': 'recorded', 'receipt': receipt}


def save_wait(service, row, status):
    """Keep the owner's existing command receipt after the global drain advances."""
    if not row.get('explicit'):
        return
    record = wait_receipt(service, row['sessionId'], row['commandId'])
    if record:
        receipt = record['receipt']
        receipt.update(status=status, boundary=copy.deepcopy(row))
        service.collaboration.save(row['commandId'], receipt)


def pending_waiters(current):
    workers = current.get('workers', {})
    if current.get('phase') == 'waiting':
        return workers
    # A different update may finish before a withdrawn wait can queue its
    # continuation. Carry that durable recovery through the next boundary.
    return {sid: row for sid, row in workers.items() if row.get('withdrawn')}


async def wait_for_update(service, args, command_id, *, origin, guard=None):
    """Enroll one caller, not all active work, in the existing native boundary."""
    from .errors import AppError
    from .update_restart import target
    if not isinstance(command_id, str) or not command_id:
        raise AppError('An update wait requires a stable command ID.', 400)
    manager = service.update_manager
    async with service.lock:
        if guard:
            guard()
        saved = service.db.execute('SELECT receipt FROM commands WHERE id=?', (command_id,)).fetchone()
        if saved:
            receipt = json.loads(saved[0])
            if receipt.get('commandAction') != 'task.waitForUpdate' or receipt.get('arguments') != args:
                raise AppError('This command ID already has different contents.', 409)
            return {'accepted': True, 'result': receipt, 'duplicate': True}
        if not manager or target(manager) != args['target']:
            raise AppError('The staged update or host changed. Inspect its current target.', 409)
        session = service._session(args['sessionId'])
        lineage = {}
        if origin not in {'ui', 'user'}:
            transport, active = service.collaboration.active_binding(session)
            worker_owner = (getattr(service.runtime, 'workers', {}).get(session['id']) or {}).get('runtimeOwnerId')
            if transport.get('_runtimeOwnerId') != worker_owner:
                raise AppError('The requesting runtime owner changed.', 409)
            inputs = transport.get('_inputBindings')
            if (isinstance(inputs, list) and 0 < len(inputs) <= 128
                    and all(isinstance(item, dict) and isinstance(item.get('inputId'), str)
                            and item['inputId'] and item['inputId'] in active.get('inputIds', [])
                            for item in inputs)):
                # Capture only the admitted transport assignment, before yield.
                # Missing proof can still wait, but cannot later supply authority.
                lineage = service.collaboration.lineage(session, origin)
        task = session.get('task') or service.coordination.task(session['id'])
        worker = getattr(service.runtime, 'workers', {}).get(session['id'])
        if (not task or task.get('status') != 'active' or task.get('revision') != args['expectedRevision']
                or session.get('status') != 'working' or not worker or not worker.get('runtimeOwnerId')
                or session.get('configurationBusy') or service.assignments.blocked(session)):
            raise AppError('An exact active task and its running owner are required.', 409)
        current = service.state['updates'].get('drain') or {}
        expected = {key: args['target'][key] for key in ('attemptId', 'revision', 'generation')}
        if current.get('phase') not in {None, 'released', 'waiting'} or (
                current.get('phase') == 'waiting' and current['target'] != expected):
            raise AppError('Another update boundary is still retained; inspect it before proceeding.', 409)
        if current.get('phase') != 'waiting':
            current = {'attemptId': expected['attemptId'], 'target': expected, 'phase': 'waiting',
                       'hostInstanceId': service.instance_id, 'workers': pending_waiters(current), 'messages': []}
        if session['id'] in current['workers']:
            raise AppError('This owner already has a saved update wait. Inspect updates.drain.', 409)
        from .task_binding import continuation_epoch
        row = {'sessionId': session['id'], 'owner': worker['runtimeOwnerId'],
            'boundaryId': str(uuid.uuid5(uuid.NAMESPACE_URL, 'update-wait:' + command_id)),
            'commandId': command_id, 'explicit': True, 'binding': binding(session),
            'updateTarget': expected, 'accessRevision': service.access_mode.snapshot(session)['revision'],
            'taskBinding': {'taskId': task['id'], 'taskRevision': task['revision'],
                            'taskContinuationEpoch': continuation_epoch(task)},
            'generationId': (session.get('collaborationGeneration') or {}).get('id'),
            'lastInputId': latest_input(session, service=service), 'ready': False,
            'initializing': True, 'waitingFor': ['native-boundary']}
        current['workers'][session['id']] = row
        service.state['updates']['drain'] = current
        receipt = {'commandAction': 'task.waitForUpdate', 'requestId': command_id,
                   'origin': origin, **copy.deepcopy(lineage),
                   'arguments': copy.deepcopy(args), 'status': 'waiting', 'admissionStatus': 'pending',
                   'boundaryId': row['boundaryId'],
                   'readRef': {'action': 'task.receipt', 'args': {'sessionId': session['id'], 'commandId': command_id}}}
        service.collaboration.insert(command_id, command_id, receipt)
        service._publish_changes(globals={'updates'})
    # The intent is durable before the native effect. Unknown admission is kept
    # as unknown, never retried as another boundary.
    try:
        result = await service.runtime.handoff_boundary(row['sessionId'], row['boundaryId'], update=True,
            receipt={'updateWait': {'owner': row['owner'], 'generationId': row['generationId'],
                                    **row['taskBinding']}})
    except BaseException:
        async with service.lock:
            row.update(waitingFor=['boundary-outcome-unknown'], unknown=True, initializing=False)
            receipt.update(status='unknown', admissionStatus='unknown', boundary=copy.deepcopy(row))
            service.collaboration.save(command_id, receipt)
            service._publish_changes(globals={'updates'})
        raise
    async with service.lock:
        row.update(result, initializing=False)
        receipt['admissionStatus'] = 'acknowledged'
        receipt['boundary'] = copy.deepcopy(result)
        service.collaboration.save(command_id, receipt)
        service._publish_changes(globals={'updates'})
    manager.wakeup.set()
    return {'accepted': True, 'result': {**receipt, 'boundary': copy.deepcopy(result)}}


async def observe_waiters(manager):
    """Inspect only enrolled owners; an idle event alone is not checkpoint proof."""
    service = manager.service
    current = service.state['updates'].get('drain') or {}
    phase = current.get('phase')
    if phase not in {'waiting', 'released'} or any(
            service.state['updates'].get(key) for key in ('pendingRestart', 'pendingReplacement')):
        return
    def retained(row):
        return (service.state['updates'].get('drain') is current
                and current.get('phase') == phase
                and current['workers'].get(row['sessionId']) is row)

    async def observe_checkpoint(sid, row, worker):
        if worker.get('runtimeOwnerId') != row['owner']:
            result = {'unknown': True, 'waitingFor': ['original-owner-unavailable']}
        else:
            try:
                result = await service.runtime.handoff_boundary(sid, row['boundaryId'], update=True,
                    receipt={'updateWait': {'owner': row['owner'], 'observeOnly': True}})
            except Exception:
                result = {'unknown': True, 'waitingFor': ['boundary-outcome-unknown']}
        async with service.lock:
            if retained(row):
                row.update(result)
                save_wait(service, row, 'unknown' if row.get('unknown') else
                          'checkpoint_ready' if row.get('ready') else 'waiting')
                service._publish_changes(globals={'updates'})

    for sid, row in list(current['workers'].items()):
        if not retained(row) or phase == 'released' and not row.get('withdrawn'):
            continue
        if row.get('initializing') and current.get('hostInstanceId') != service.instance_id:
            async with service.lock:
                if not retained(row):
                    continue
                row.update(initializing=False, unknown=True, waitingFor=['boundary-outcome-unknown'])
                save_wait(service, row, 'unknown')
                service._publish_changes(globals={'updates'})
        if row.get('initializing'):
            continue
        worker = getattr(service.runtime, 'workers', {}).get(sid) or {}
        if row.get('suppressed') and worker.get('runtimeOwnerId') != row['owner']:
            continue
        reason = row.get('suppressed') or continuation_guard(service, {'updateYield': row}, authority_only=True)
        pending = service.state['updates'].get('pendingApp') or {}
        changed = any(pending.get(key) != current['target'].get(key) for key in ('attemptId', 'revision', 'generation'))
        withdrawn = (changed or row.get('withdrawn')) and not reason and not row.get('unknown')
        if withdrawn and not row.get('ready'):
            # Keep the hook until the current turn has actually yielded. Merely
            # cancelling it can race that yield and strand an active objective.
            await observe_checkpoint(sid, row, worker)
            if not retained(row) or not row.get('ready') or row.get('unknown'):
                continue
        if (changed or row.get('withdrawn')) and not reason:
            reason = 'The staged update changed; this wait cannot follow a different target.'
        if reason:
            # A saved withdrawn row proves this exact native cancellation. A
            # storage retry (including after restart) must not repeat it.
            cancelled = row.get('withdrawn') is True
            if not cancelled and worker.get('runtimeOwnerId') == row['owner']:
                try:
                    result = await service.runtime.handoff_boundary(sid, row['boundaryId'], cancel=True,
                        receipt={'updateWait': {'owner': row['owner']}})
                    if result.get('cancelled') is not True:
                        raise RuntimeError('The original worker did not confirm cancellation of its update wait.')
                except Exception:
                    async with service.lock:
                        if not retained(row):
                            continue
                        raise
                cancelled = True
            async with service.lock:
                if not retained(row):
                    continue
                resume = (withdrawn and cancelled and not row.get('unknown')
                          and not continuation_guard(service, {'updateYield': row}, authority_only=True))
                if resume:
                    row['withdrawn'] = True
                else:
                    row['suppressed'] = reason
                save_wait(service, row, 'withdrawn' if resume else 'suppressed')
                if resume:
                    # Commit the confirmed checkpoint/cancellation first. Keep
                    # its slot if source creation or continuation publication
                    # fails, so an ordinary tick can finish the durable queue.
                    service._publish_changes(globals={'updates'})
                    queued = enqueue_goal(service, current, row)
                    save_wait(service, row, 'withdrawn')
                    if queued is False:
                        service._publish_changes(globals={'updates'})
                        continue
                if cancelled:
                    # Retain the old command receipt, not an active slot that
                    # prevents this owner from making a fresh explicit wait.
                    del current['workers'][sid]
                service._publish_changes(globals={'updates'})
            service._task(service.collaboration.drain(sid))
            continue
        if row.get('unknown'):
            continue
        if row.get('ready'):
            continue
        await observe_checkpoint(sid, row, worker)
    async with service.lock:
        if (service.state['updates'].get('drain') is current and current.get('phase') == 'waiting'
                and all(row.get('suppressed') for row in current['workers'].values())):
            current.update(phase='released', result='superseded')
            service._publish_changes(globals={'updates'})


def same_owner(service, row):
    session = service._session(row['sessionId'])
    return (binding(session) == row['binding']
            and not service.assignments.blocked(session))


def hold_message(service, session, message):
    """Called under the host lock, before this input's first transport attempt."""
    drain = service.state['updates']['drain']
    identity = message['inputId']
    key = [session['id'], identity]
    if key not in drain['messages']:
        drain['messages'].append(key)
    message.pop('steering', None)  # Only ordinary allow-next-turn messages enter.
    message['updateAdmission'] = {'interruptionRevision': session.get('interruptionRevision', 0)}
    message['delivery'] = {'status': 'queued_for_update', 'attemptId': drain['attemptId'],
                           'interruptionRevision': session.get('interruptionRevision', 0)}
    # A preceding dispatch may have published a not-yet-admitted turn before
    # the drain began. It is not native work and must not block its own update.
    turn = next((row for row in session.get('execution', {}).get('turns', []) if row['id'] == identity), None)
    if turn and not any(node.get('turnId') == identity for node in session['execution'].get('nodes', [])):
        turn['phase'] = 'queued'
    active = session.get('collaborationGeneration') or {}
    if not active.get('id') or active.get('terminal'):
        session['status'] = 'idle'
    receipt = service.db.execute('SELECT receipt FROM commands WHERE id=?', (identity,)).fetchone()
    if receipt:
        value = json.loads(receipt[0]); value['delivery'] = 'queued_for_update'
        service.db.execute('UPDATE commands SET receipt=? WHERE id=?', (json.dumps(value), identity))


async def prepare(manager, release):
    """Observe idle readiness without reserving workers or withholding input."""
    service = manager.service
    prior = service.state['updates'].get('drain') or {}
    if prior.get('phase') == 'draining':
        # Older hosts could freeze all work before a restart was possible.
        # Withdraw only that pre-replacement boundary using its saved receipts.
        # A released continuation may not have reached admission yet: do not
        # replace the host in the same observation that releases its work.
        await finish(manager, 'withdrawn')
        return False
    if prior.get('phase') == 'waiting':
        await observe_waiters(manager)
        if any(not row.get('ready') and not row.get('suppressed') for row in prior['workers'].values()):
            return False
    return not held(service) and not manager.app_busy()


def begin_replacement(manager, release):
    """Close admission under the final activation lock, never during staging."""
    service = manager.service
    identity = manager.diagnostics.state['attemptId']
    prior = service.state['updates'].get('drain') or {}
    workers = pending_waiters(prior)
    service.state['updates']['drain'] = {
        'attemptId': identity,
        'target': {'attemptId': identity, 'revision': release['revision'],
                   'generation': release.get('generation')},
        'phase': 'replacing', 'hostInstanceId': service.instance_id,
        'workers': workers, 'messages': []}


def latest_input(session, excluding_request=None, *, service=None):
    # The existing bounded message view is enough: a new saved input appears
    # before native generation events. Ignore only positively unsent inputs
    # and this update's own deterministic retained-outcome message.
    for message in reversed(session.get('messages', [])):
        if (message.get('role') != 'user' or not message.get('inputId')
                or message.get('delivery', {}).get('status') in {'failed', 'held'}
                or excluding_request and (message.get('peerEnvelope') or {}).get('replyToRequestId') == excluding_request):
            continue
        if service and message.get('peerEnvelope'):
            saved = service.db.execute('SELECT receipt FROM commands WHERE id=?', (message['inputId'],)).fetchone()
            if saved and json.loads(saved[0]).get('delivery') in {'queued', 'notified', 'suppressed', 'expired', 'not_sent'}:
                continue  # A saved, unsubmitted peer report cannot undo the owner's yield.
        return message['inputId']


def owner_waiting(service, sid):
    current = service.state.get('updates', {}).get('drain') or {}
    row = current.get('workers', {}).get(sid) or {}
    return (row.get('explicit') and not row.get('suppressed')
        and not continuation_guard(service, {'updateYield': row}, authority_only=True)
        and (current.get('phase') != 'released' or row['updateTarget'] != current.get('target')
             or not verified_target(service.update_manager, current)))


def continuation_guard(service, request, *, authority_only=False):
    row = request['updateYield']
    if not same_owner(service, row):
        return 'Stop, assignment or execution placement changed after the update boundary.'
    session = service._session(row['sessionId'])
    if 'generationId' in row and (session.get('collaborationGeneration') or {}).get('id') != row['generationId']:
        return 'New input has already continued this conversation after its saved update boundary.'
    if 'lastInputId' in row and latest_input(session, request.get('requestId'),
            service=service if row.get('explicit') else None) != row['lastInputId']:
        return 'New input has already been saved after this conversation’s update boundary.'
    task = session.get('task') or service.coordination.task(row['sessionId'])
    from .task_binding import matches
    if not matches(task, row['taskBinding']) or task.get('status') != 'active':
        return 'The saved task changed or is no longer active.'
    if session.get('configurationBusy'):
        return 'Conversation settings are still being applied.'
    if row.get('explicit'):
        if service.access_mode.snapshot(session)['revision'] != row.get('accessRevision'):
            return 'Access authority changed after the update wait.'
        if task.get('revision') != row['taskBinding']['taskRevision']:
            return 'The task was revised after the update wait.'
        if not authority_only and (row.get('suppressed') or row.get('unknown')):
            return row.get('suppressed') or 'The saved native boundary has an unknown outcome.'
        if request.get('terminalOutcome'):
            if row.get('withdrawn'):
                if not row.get('ready') or held(service) or any(
                        service.state['updates'].get(key) for key in ('pendingRestart', 'pendingReplacement')):
                    return 'The withdrawn wait has no checkpoint or replacement is still underway.'
                return
            manager = service.update_manager
            current = service.state['updates'].get('drain') or {}
            if (current.get('phase') != 'released' or row['updateTarget'] != current.get('target')
                    or not verified_target(manager, current)):
                return 'The exact healthy update and released drain are not confirmed.'


def verified_target(manager, drain):
    state = manager.service.state['updates']
    result = state.get('applicationResult') or {}
    return (result.get('phase') == 'restart-ack'
        and all(result.get(key) == drain.get('target', {}).get(key) for key in ('attemptId', 'revision'))
        and manager.running_identity.get('revision') == result.get('revision')
        and manager.running_identity.get('version') == result.get('version')
        and manager.service.instance_id != drain.get('hostInstanceId')
        and not any(state.get(key) for key in ('pendingApp', 'pendingRestart', 'pendingReplacement')))


def enqueue_goal(service, drain, row):
    goal, task = row.get('yieldedGoal'), row.get('task')
    if row.get('explicit') and (not row.get('ready') or not row.get('withdrawn') and (
            row['updateTarget'] != drain.get('target') or drain.get('result') != 'installed'
            or not verified_target(service.update_manager, drain))):
        return
    if not goal or not task or task.get('status') != 'active':
        return
    if goal.get('cap') is not None and goal.get('turns_used', 0) >= goal['cap']:
        return
    sid = row['sessionId']
    session = service._session(sid)
    from .task_binding import continuation_epoch
    proof = {**row, 'taskBinding': row.get('taskBinding') or {
        'taskId': task['id'], 'taskRevision': task['revision'],
        'taskContinuationEpoch': continuation_epoch(task)}}
    if continuation_guard(service, {'updateYield': proof}):
        return
    # Existing messages take precedence, without minting another wakeup.
    from .message_delivery import find_message
    if any(owner == sid and (find_message(session, identity) or {}).get('delivery', {}).get('status')
           in {'accepted', 'sending', 'unknown', 'queued_for_update'} for owner, identity in drain['messages']):
        return
    for identity, encoded in service.collaboration.fair_rows(sid):
        receipt = json.loads(encoded)
        if not service.collaboration.guard(receipt):
            if row.get('explicit'):
                row['continuationRequestId'] = receipt['requestId']
            return
    identity = ('update-wait-withdrawn:' + row['boundaryId'] if row.get('withdrawn') else
                'update-wait:' + drain['attemptId'] + ':' + sid)
    owner = service.collaboration
    saved = service.db.execute('SELECT receipt FROM commands WHERE id=?', (identity,)).fetchone()
    if saved:
        request = json.loads(saved[0])
    else:
        from .collaboration import PROTOCOL
        wait = owner.saved_wait(session, identity)
        wait.update(proof['taskBinding'])
        request = {'accepted': True, 'commandAction': 'updates.app', 'requestId': identity,
            'senderSessionId': sid, 'target': {'sessionId': sid}, 'workspace': session['workspace'],
            'origin': 'system', 'protocol': PROTOCOL, 'subscription': wait, 'updateYield': proof,
            'terminalOutcome': {'status': 'observed', 'kind': 'observation',
                'outcome': 'update-withdrawn' if row.get('withdrawn') else 'update-ready',
                'qualified': False, 'outcomeRevision': 1,
                'text': ('The requested update wait was withdrawn. ' if row.get('withdrawn') else
                         'The updated app is ready. ' if drain.get('result') == 'installed' else
                         'The app update was withdrawn before replacement. ') +
                        'Continue the existing task from its saved progress. '
                        'Keep its used budget and current instructions. Do not repeat previous inputs or completed or uncertain effects.'}}
        owner.insert(identity, identity, request)
        service.db.commit()
    owner.enqueue_continuation(request)
    if row.get('explicit'):
        row['continuationSourceRequestId'] = request['requestId']
        if request['subscription'].get('lastClaimedRevision'):
            row['continuationRequestId'] = owner.continuation_identity(
                request['subscription'], request['subscription']['lastClaimedRevision'])
    # A temporary inbox limit retains the waiter for a later ordinary tick.
    return request['subscription'].get('status') != 'backpressured'


async def finish(manager, reason):
    """Release known unattempted inputs, serially, after this attempt settles."""
    service = manager.service
    drain = service.state['updates'].get('drain')
    if (not drain or drain.get('phase') not in {'draining', 'replacing', 'releasing'}
            or getattr(manager, '_releasing_update_inputs', False)):
        return
    if reason == 'installed':
        result = service.state['updates'].get('applicationResult') or {}
        if any(result.get(key) != drain['target'].get(key) for key in ('attemptId', 'revision')):
            return
        if any(row.get('explicit') for row in drain['workers'].values()) and not verified_target(manager, drain):
            return
    else:
        from .updates import work_paused
        if work_paused(service.state):
            return  # Replacement uncertainty is not a presumed withdrawal.
    manager._releasing_update_inputs = True
    try:
        for sid, row in drain['workers'].items():
            if row.get('withdrawn'):
                continue  # This exact cancellation is already durably confirmed.
            worker = getattr(service.runtime, 'workers', {}).get(sid)
            if worker and worker.get('runtimeOwnerId') == row['owner']:
                await service.runtime.handoff_boundary(sid, row['boundaryId'], cancel=True)
        async with service.lock:
            drain.update(phase='releasing', result=reason)
            service._publish_changes(globals={'updates'})
        from .message_delivery import find_message
        from .execution import ensure_turn
        for sid, identity in drain['messages']:
            async with service.lock:
                session = service._session(sid)
                message = find_message(session, identity)
                if not message or message.get('delivery', {}).get('status') != 'queued_for_update':
                    continue
                if (message['delivery'].get('interruptionRevision') != session.get('interruptionRevision', 0)
                        or service.assignments.blocked(session)):
                    service._delivery(session, identity, 'held')
                    message['delivery']['reason'] = 'Stopped after this message was saved. Nothing was sent.'
                    service._publish_changes(sessions={sid})
                    continue
                # Claim only this first admission before the await. A crash
                # leaves this input uncertain and the remaining inputs queued.
                drain['releasingInput'] = [sid, identity]
                service._delivery(session, identity, 'sending')
                active = session.get('collaborationGeneration') or {}
                if active.get('id') and not active.get('terminal'):
                    message['steering'] = {'generationId': active['id'], 'allowNextTurn': True,
                                           'disposition': 'sending'}
                else:
                    session['status'] = 'working'
                    ensure_turn(session, identity, message['text'])
                service._publish_changes(sessions={sid}, globals={'updates'})
                captured = copy.deepcopy(session)
            try:
                await service._send(captured, message['text'], identity, preserve_draft=True)
            except Exception:
                # _send records its exact failure/unknown disposition. Never
                # retry that input or prevent other saved messages from settling.
                pass
        async with service.lock:
            for row in drain['workers'].values():
                if row.get('withdrawn'):
                    continue  # Ordinary observation retains/retries its original continuation.
                eligible = (reason == 'installed' and row.get('updateTarget') == drain['target']
                    and not continuation_guard(service, {'updateYield': row}))
                if row.get('explicit') and not eligible:
                    row['suppressed'] = (continuation_guard(service, {'updateYield': row})
                        or 'The exact requested update was not confirmed; no update continuation was authorized.')
                enqueue_goal(service, drain, row)
                save_wait(service, row, 'adopted' if eligible else 'suppressed')
            drain.pop('releasingInput', None)
            drain['phase'] = 'released'
            service._publish_changes(globals={'updates'})
        targets = service.db.execute("""SELECT DISTINCT json_extract(receipt,'$.target.sessionId') FROM commands
            WHERE json_extract(receipt,'$.commandAction')='coordination.send'
            AND json_extract(receipt,'$.delivery')='queued'""").fetchall()
        for (sid,) in targets:
            service._task(service.collaboration.drain(sid))
    finally:
        manager._releasing_update_inputs = False


async def defer_send(service, sid, identity, preserve_draft=False):
    from .message_delivery import find_message
    async with service.lock:
        session = service._session(sid)
        message = find_message(session, identity)
        if held(service):
            # Adopt the currently owning attempt only while its gate is active.
            # The earlier exception proves no transport attempt has occurred.
            hold_message(service, session, message)
            service._clear_sent_draft(session, message, message['text'], preserve_draft)
            service._publish_changes(sessions={sid}, globals={'updates'})
            return {'accepted': True, 'inputId': identity, 'delivery': 'queued_for_update'}
        # The gate released while we awaited the commit lock. Continue that
        # same first admission; never append to an already released drain.
        active = session.get('collaborationGeneration') or {}
        message.pop('steering', None)
        if active.get('id') and not active.get('terminal'):
            message['steering'] = {'generationId': active['id'], 'allowNextTurn': True,
                                   'disposition': 'sending'}
        captured = copy.deepcopy(session)
    return await service._send(captured, message['text'], identity, preserve_draft=preserve_draft)
