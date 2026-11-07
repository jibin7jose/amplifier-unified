"""Client presentation changes never rewrite conversation or artifact catalogs."""
import copy
import json


def update(service, args, command_id, fingerprint, *, include_state):
    from .errors import AppError
    identity = service.clients.current.get()
    if identity is None:
        raise AppError('Attach a client before changing Canvas visibility.', 409)
    client = service.clients.record()
    canvas = client.get('canvas', {})
    if args['sessionId'] != client.get('selectedSessionId') or args['canvasId'] != canvas.get('id'):
        raise AppError('The selected chat or artifact changed. Retry in the intended Canvas.', 409)
    if args['open'] and client.get('selectedSessionId') is None:
        raise AppError('Start a chat before opening Canvas.', 409, code='canvas_requires_session')
    if (canvas.get('open') == args['open'] and client.get('canvasViews', {}).get('retained')
            and (args['open'] or not client['view'].get('canvasFocused'))):
        receipt = {'accepted': True, 'revision': service._state['revision'], 'effects': []}
        if command_id:
            service.db.execute('INSERT INTO commands VALUES (?,?,?)', (command_id, fingerprint, json.dumps(receipt)))
            service.db.commit()
        return {**receipt, **({'state': service.browser_state()} if include_state else {})}
    cached = service._client_snapshots.get(identity)
    previous = copy.deepcopy(client)
    revision = service._state['revision']
    try:
        if args['open']:
            from .canvas_library import scope, restore
            if not scope(service.state, canvas) or canvas.get('placeholder'):
                # Cold recovery may load a saved source after the panel has
                # already painted. Warm visibility never revisits the library.
                restore(service.state, service.db, open_panel=True)
                canvas = client['canvas']
                cached = None
            if canvas.get('_presentationDisposed'):
                service.canvas_views.select_presentation({'id': canvas['id'],
                    'version': canvas.get('selectedVersion') or canvas.get('revision', 1)}, 'ui')
                cached = None
        views = service.canvas_views.record()
        # Preserve the renderer generation when hiding an already mounted view.
        # Reopening also freezes an existing hidden binding before it changes.
        views['retained'] = True
        canvas['open'] = args['open']
        if not args['open']:
            client['view']['canvasFocused'] = False
        service._state['revision'] = revision + 1
        service._client_snapshots.pop(identity, None)
        receipt = {'accepted': True, 'revision': revision + 1, 'effects': []}
        # Presentation is independently durable. The next ordinary publication
        # persists the shared revision; hostInstanceId bounds it across restarts.
        service.clients.commit(identity, command_id, fingerprint, receipt)
    except Exception:
        service.db.rollback()
        service.clients.records[identity] = previous
        service.clients.saved.pop(identity, None)
        service.clients.dirty.add(identity)
        service._state['revision'] = revision
        raise
    if cached and cached['revision'] == revision:
        service._client_snapshots[identity] = {
            **cached, 'revision': revision + 1,
            'canvas': {**cached['canvas'], 'open': args['open']},
            'view': {**cached['view'], **({} if args['open'] else {'canvasFocused': False})},
            'canvasWorkspace': service.canvas_views.project(),
        }
    # Unchanged client snapshots can retain their expensive history projections.
    for key, snapshot in list(service._client_snapshots.items()):
        if snapshot['revision'] == revision:
            service._client_snapshots[key] = {**snapshot, 'revision': revision + 1}
    if getattr(service, '_browser_snapshot', None) and service._browser_snapshot['revision'] == revision:
        service._browser_snapshot = {**service._browser_snapshot, 'revision': revision + 1}
    snapshot = None
    for queue in service.queues:
        if service.queue_clients.get(queue) != identity:
            continue
        session_id = service.queue_sessions.get(queue)
        current = service.session_state(session_id) if session_id is not None else service.browser_state()
        if session_id is None:
            snapshot = current
        if queue.full():
            queue.get_nowait()
        queue.put_nowait(current)
    return {**receipt, **({'state': snapshot or service.browser_state()} if include_state else {})}
