"""Persist attached-client geometry without rewriting shared conversation data."""
import copy
import json


DIMENSIONS = {'canvasWidth': 300, 'canvasOverviewWidth': 300, 'navWidth': 216}
SWITCHES = {'canvasFocused', 'canvasControlsPinned', 'canvasControlsExpanded',
            'toolbarMenuOpen', 'navPinned', 'navExpanded'}
KEYS = DIMENSIONS.keys() | SWITCHES


def accepts(patch):
    return isinstance(patch, dict) and bool(patch) and not patch.keys() - KEYS


def update(service, patch, command_id, fingerprint, *, include_state):
    from .errors import AppError
    for key, minimum in DIMENSIONS.items():
        if key in patch and (type(patch[key]) not in {int, float} or not minimum <= patch[key] <= 16384):
            raise AppError(f'{key} must be between {minimum} and 16384 pixels.')
    for key in SWITCHES:
        if key in patch and type(patch[key]) is not bool:
            raise AppError('Layout switches must be true or false.')
    identity = service.clients.current.get()
    client = service.clients.record()
    if all(client['view'].get(key) == value for key, value in patch.items()):
        # This validated no-op publishes no client frame. The shared revision
        # may include another chat's progress, which this client never receives.
        # Retain that fact in the retry receipt rather than waiting for its SSE.
        receipt = {'accepted': True, 'revision': service._state['revision'], 'effects': [],
                   '_stateIndependent': True}
        if command_id:
            service.db.execute('INSERT INTO commands VALUES (?,?,?)', (command_id, fingerprint, json.dumps(receipt)))
            service.db.commit()
        return {**receipt, **({'state': service.browser_state()} if include_state else {})}
    previous = copy.deepcopy(client)
    revision = service._state['revision']
    cached = service._client_snapshots.get(identity)
    receipt = {'accepted': True, 'revision': revision + 1, 'effects': []}
    try:
        client['view'].update(patch)
        service._state['revision'] = revision + 1
        # As with Canvas visibility, hostInstanceId bounds the shared revision
        # across restarts; the client view and retry receipt commit together.
        service.clients.commit(identity, command_id, fingerprint, receipt)
    except Exception:
        service.db.rollback()
        service.clients.records[identity] = previous
        service.clients.saved.pop(identity, None)
        service.clients.dirty.add(identity)
        service._state['revision'] = revision
        raise
    service._client_snapshots.pop(identity, None)
    if cached and cached['revision'] == revision:
        service._client_snapshots[identity] = {
            **cached, 'revision': revision + 1, 'view': {**cached['view'], **patch},
        }
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
    # Pending runtime progress keeps its scheduled publication and durability.
    return {**receipt, **({'state': snapshot or service.browser_state()} if include_state else {})}
