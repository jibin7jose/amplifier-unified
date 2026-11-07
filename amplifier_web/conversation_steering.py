"""Generation-bound steering and explicit ordinary-message completion handoff."""
import copy
import json
import time

from .message_delivery import find_message
from .update_drain import UpdateDeferred


def recover_failed_generation(service, session, boundary):
    """Settle lost routing authority only while holding the native writer lease.

    Called for a new human message, under the service lock. Failure/idle state
    alone is not absence evidence, and no old input or native job is replayed.
    The caller holds ``boundary`` through its durable command publication.
    """
    from .service import AppError, settle_stream
    from .execution import interrupt_owned_workers, interrupt_unfinished
    active = session.get('collaborationGeneration') or {}
    if (session.get('status') != 'error' or not session.get('failure')
            or not active.get('id') or active.get('terminal')):
        return None
    prove = getattr(service.runtime, 'stopped_boundary', None)
    if not callable(prove):
        raise AppError('The failed run has not been confirmed stopped. Inspect its recovery before sending new work.', 409)
    try:
        proof = boundary.enter_context(prove(session))
    except (OSError, RuntimeError, ValueError) as exc:
        raise AppError(f'Could not confirm that the failed run has stopped. {exc}', 409) from None
    settle_stream(session)
    interrupt_unfinished(session)
    # Only lifecycle rows bound by this root's native pipe carry this identity.
    # Independent chats and legacy unbound rows retain their own authority.
    for owner in {worker['runtimeOwnerId'] for worker in session.get('workers', [])
                  if worker.get('runtimeOwnerId')}:
        interrupt_owned_workers(session, owner)
    active.update(terminal=True, completed=False,
                  detail='Native writer absence confirmed before a new human message. Prior outcomes remain unconfirmed.')
    return {**proof, 'generationId': active['id']}


def target(session, expected=None, allow_next_turn=False):
    from .service import AppError
    active = session.get('collaborationGeneration') or {}
    identity = active.get('id') if not active.get('terminal') else None
    if (allow_next_turn and expected is not None and active.get('id') == expected
            and active.get('terminal') and active.get('completed')):
        return None
    if expected is not None and identity != expected:
        raise AppError('The run you were steering has ended or changed. Your message was not sent.',
                       409, code='steering_target_changed')
    return identity


def record(service, session, input_id, disposition, reason=None, *, preserve_pending_post=False,
           mode=None, phase=None):
    message = find_message(session, input_id)
    if not message or not message.get('steering'):
        return
    steering = message['steering']
    # Runtime evidence can arrive before the transport acknowledgement.
    if steering['disposition'] in {'applied', 'next_turn'} or (
            steering['disposition'] in {'held', 'unknown'} and disposition == 'queued'):
        return
    steering['disposition'] = disposition
    if mode in {'native', 'request_boundary'}:
        steering['mode'] = mode
    # Late socket-send acknowledgement must not erase provider acceptance.
    # Neither proves application until a distinct successor exists.
    phases = {'sent': 0, 'accepted': 1, 'pending': 2, 'applied': 3}
    if phase in phases and phases[phase] >= phases.get(steering.get('phase'), -1):
        steering['phase'] = phase
    # Restart is not admission evidence. Keep an unresolved fence available for
    # an exact later held/applied observation, without replay or new promotion.
    if not (preserve_pending_post and (message.get('navigationPost') or {}).get('disposition') == 'pending'):
        service._post_disposition(session, message, 'accepted' if disposition in {'queued', 'applied', 'next_turn'}
                                  else 'rejected' if disposition == 'held' else 'retained')
    if disposition in {'queued', 'applied', 'next_turn'}:
        # Host observation times distinguish admission from context insertion.
        # Neither timestamp asserts that the model obeyed the instruction.
        steering.setdefault('contextAt' if disposition == 'applied' else 'queuedAt', time.time())
    if reason:
        steering['reason'] = reason
    else:
        steering.pop('reason', None)
    delivery = 'accepted' if disposition in {'queued', 'applied', 'next_turn'} else 'failed' if disposition == 'held' else 'unknown'
    if delivery == 'accepted':
        # Early runtime evidence may precede the awaited acknowledgement. Retire
        # these exact draft IDs in the same publication as positive steering.
        service.clients.consume_attachments(session, message.get('attachments', []))
    message['delivery'] = {'status': delivery}
    row = service.db.execute('SELECT receipt FROM commands WHERE id=?', (input_id,)).fetchone()
    if row:
        receipt = json.loads(row[0])
        receipt.update(delivery=delivery, steering=copy.deepcopy(steering))
        if delivery == 'accepted':
            receipt.setdefault('_deliveryStateRevision', service._state['revision'] + 1)
        service.db.execute('UPDATE commands SET receipt=? WHERE id=?', (json.dumps(receipt), input_id))


def observe(service, session, kind, payload):
    exited = (kind == 'runtime.ended' and not payload.get('backgroundOnly')
        and payload.get('sessionId') is not None
        and payload.get('sessionId') in {session['id'], session.get('runtimeSessionId')}
        and payload.get('rootSessionId', session['id']) == session['id'])
    if exited or kind == 'runtime.error' or (kind == 'runtime.generation' and
            payload.get('event') in {'generation.finished', 'generation.failed', 'generation.detached'} and
            payload.get('sessionId', session['id']) == session['id'] and
            payload.get('rootSessionId', session['id']) == session['id']):
        for message in session.get('messages', []):
            steering = message.get('steering') or {}
            if steering.get('disposition') not in {'sending', 'queued', 'unknown'}:
                continue
            if kind == 'runtime.generation' and steering.get('generationId') != payload.get('generation_id'):
                continue
            applied = kind == 'runtime.generation' and message['inputId'] in payload.get('input_ids', [])
            record(service, session, message['inputId'], 'applied' if applied else 'unknown',
                   None if applied else 'The run ended before steering delivery was confirmed. Nothing was resent.')
        return
    if kind != 'runtime.steering':
        return
    message = find_message(session, payload.get('input_id'))
    if not message or (message.get('steering') or {}).get('generationId') != payload.get('target_generation_id'):
        return
    disposition = {'steering.sent': 'queued', 'steering.accepted': 'queued', 'steering.pending': 'queued',
                   'steering.applied': 'applied', 'steering.held': 'held',
                   'steering.failed': 'held', 'steering.unknown': 'unknown',
                   'native.outcome_unknown': 'unknown'}.get(payload.get('event'))
    if disposition:
        record(service, session, message['inputId'], disposition, payload.get('reason'),
               mode=payload.get('delivery'), phase=payload.get('event', '').removeprefix('steering.'))


async def send(service, session, text, input_id, preserve_draft):
    message = find_message(session, input_id)
    result = None
    try:
        sender = getattr(service.runtime, 'steer', None)
        if sender is None:
            result = {'accepted': False, 'reason': 'This runtime does not support steering an active run.'}
        else:
            result = await sender(session, text, input_id, service.on_runtime_event)
        if not isinstance(result, dict) or type(result.get('accepted')) is not bool:
            raise RuntimeError('Steering acknowledgement was not recognized.')
        disposition = result.get('disposition', 'queued') if result['accepted'] else 'held'
        if disposition == 'next_turn' and not (result['accepted'] and message['steering'].get('allowNextTurn')):
            raise RuntimeError('Unexpected next-turn acknowledgement.')
        if disposition not in {'queued', 'applied', 'next_turn', 'held', 'unknown'}:
            raise RuntimeError('Steering disposition was not recognized.')
        reason = result.get('reason')
    except UpdateDeferred:
        from .update_drain import defer_send
        return await defer_send(service, session['id'], input_id, preserve_draft)
    except Exception:
        # A lost acknowledgement says nothing about effects. Do not mark the
        # running conversation failed, restart its worker, or replay the input.
        disposition, reason = 'unknown', 'Delivery could not be confirmed. This message will not be sent again automatically.'
    async with service.lock:
        current = service._session(session['id'])
        record(service, current, input_id, disposition, reason,
               mode=result.get('mode') if isinstance(result, dict) else None,
               phase=result.get('phase') if isinstance(result, dict) else None)
        saved = find_message(current, input_id)
        if saved['steering']['disposition'] in {'queued', 'applied', 'next_turn'}:
            service._clear_sent_draft(current, message, text, preserve_draft)
        service._publish_changes(sessions={current['id']}, globals={'view'})
        return {'delivery': saved['delivery']['status'], 'steering': copy.deepcopy(saved['steering'])}


async def submit(controls, runtime, args, activation, stop_epoch):
    """Worker boundary: preserve normal input preparation and exact ownership."""
    from .collaboration_input import _steering_capability
    from .message_interactions import prepare_input
    from amplifier_module_loop_live.runtime import Input

    epoch = stop_epoch()
    text = await prepare_input(controls.coordinator, args['text'], args.get('reply_context'),
                               max_chars=runtime.max_input_chars)
    # Preparation may yield while the manager finishes. Native admission is
    # synchronous up to queue insertion: recheck the exact terminal evidence
    # here, beside the owning inbox, rather than resend from the browser.
    if (args.get('allowNextTurn') is True and runtime.generation is None
            and not runtime.closed and not runtime.stop_requested and stop_epoch() == epoch
            and args['inputId'] not in runtime.accepted):
        terminal = next((event for event in reversed(runtime.events)
            if event.get('type') in {'generation.started', 'generation.finished',
                                    'generation.failed', 'generation.detached'}), None)
        if (terminal and terminal.get('type') == 'generation.finished'
                and terminal.get('generation_id') == args['targetGenerationId']
                and terminal.get('disposition') == 'manager_turn_finished'
                and not terminal.get('active_job_ids')):
            await runtime.submit(Input('user', text, id=args['inputId'],
                attachments=tuple(args.get('attachments', [])), activation=activation))
            return {'accepted': True, 'disposition': 'next_turn', 'inputId': args['inputId']}
    capability = _steering_capability(controls)
    if capability is None:
        return {'accepted': False, 'reason': 'This runtime does not support steering an active run.'}
    if (runtime.closed or getattr(runtime, 'steering_closed', False)
            or getattr(runtime, 'stop_requested', False) or stop_epoch() != epoch or
            (runtime.generation or {}).get('id') != args['targetGenerationId']):
        return {'accepted': False, 'reason': 'The run you were steering has ended or changed.'}
    # The capability checks the generation again before admission. User input
    # does not use the automatic-task continuation guard: a paused task must
    # still be able to receive the user's correction during its current run.
    result = await capability['submit'](Input('steer', text, id=args['inputId'],
        attachments=tuple(args.get('attachments', [])), activation=activation), args['targetGenerationId'])
    return {**result, 'mode': capability['mode']}
