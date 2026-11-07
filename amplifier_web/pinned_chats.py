"""Commit a navigation preference without flushing unrelated session work."""
import copy
import json
import time


def update(service, args, command_id, fingerprint, origin, *, include_state):
    from .errors import AppError
    from .session_navigation import is_top_level
    from .state_records import save
    from .pinned_navigation import ordered_ids, keep_order

    target = service.projections.sessions(service.state).by_id.get(args['id'])
    if args['pinned'] and (target is None or not is_top_level(target)):
        raise AppError('Only existing top-level chats can be pinned.')
    keys = ('pinnedSessionIds', 'pinnedItemIds', 'events', 'revision')
    previous = {key: copy.deepcopy(service._state[key]) for key in keys if key in service._state}
    order = ordered_ids(service._state)
    view = service.state['view']
    old_view = copy.deepcopy(view)
    receipt = {'accepted': True, 'revision': previous['revision'] + 1, 'effects': []}
    references = {}
    native_ids = {target['id']} if target and target.get('historyManaged') else set()
    try:
        pins = [sid for sid in previous['pinnedSessionIds'] or [] if sid != args['id']]
        if args['pinned']:
            # Repeated pin commands retain their existing order.
            pins = list(previous['pinnedSessionIds'] or [])
            if args['id'] not in pins:
                pins.append(args['id'])
        service._state['pinnedSessionIds'] = pins
        keep_order(service._state, order)
        service._state['events'] = [*(previous['events'] or []),
            {'id': command_id, 'action': 'session.pin', 'origin': origin, 'at': time.time()}][-200:]
        service._state['revision'] = receipt['revision']
        view.pop('navChatPage', None)
        if command_id:
            service.db.execute('INSERT INTO commands VALUES (?,?,?)',
                               (command_id, fingerprint, json.dumps(receipt)))
        if native_ids:
            from .session_projection import persist
            persist(service.data_dir, service._state, {}, session_ids=native_ids,
                    by_id={target['id']: target}, references=references, scoped_result=True, db=service.db)
        changed_globals = save(service.db, service._state, references, native_ids, {'pinnedSessionIds', 'pinnedItemIds', 'events',
                               *({'view'} if service.clients.record() is None else set())})
        written = service.clients.save(defer_ack=True)
        service.db.commit()
        service.clients.acknowledge(written)
    except BaseException:
        service.db.rollback()
        for key in keys:
            if key in previous:
                service._state[key] = previous[key]
            else:
                service._state.pop(key, None)
        view.clear(); view.update(old_view)
        raise
    for sid in native_ids:
        if sid in references: service._session_projection_refs[sid] = references[sid]
        else: service._session_projection_refs.pop(sid, None)
    # No display hydration, canonical history write, or pending progress flush.
    service.shell.resources.invalidate(changed_globals)
    service.projections.pins_changed(service.state)
    service._browser_snapshot = None
    service._client_snapshots.clear()
    service._client_snapshot_preferences.clear()
    frames = {}
    for queue in service.queues:
        if service.queue_sessions.get(queue) is not None:
            continue
        client = service.queue_clients.get(queue)
        if client not in frames:
            with service.clients.bind(client):
                frames[client] = service.browser_state()
        if queue.full():
            queue.get_nowait()
        queue.put_nowait(frames[client])
    return {**receipt, **({'state': service.browser_state()} if include_state else {})}
