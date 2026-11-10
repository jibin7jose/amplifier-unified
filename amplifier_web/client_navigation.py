"""Select an existing chat without rewriting the shared history catalog."""
import copy
import json


def selection_result(service, source_session_id):
    """Non-content evidence from the existing client selection owner."""
    identity = service.clients.current.get()
    client = service.clients.record()
    return {'clientId': identity, 'sourceSessionId': source_session_id,
            'selectedSessionId': client.get('selectedSessionId'),
            'selectedWorkspaceId': client.get('selectedWorkspaceId'),
            'selectionRevision': service.clients.selection_revision(identity),
            'status': 'selected', 'rendering': 'unverified'}


def saved_selection(service, command_id, source_session_id):
    from .errors import AppError
    row = service.db.execute('SELECT receipt FROM commands WHERE id=?', (command_id,)).fetchone()
    receipt = json.loads(row[0]) if row else {}
    result = receipt.get('result', {})
    if (not source_session_id or not receipt.get('accepted') or
            receipt.get('commandAction') != 'session.select' or
            result.get('sourceSessionId') != source_session_id or not result.get('clientId')):
        raise AppError('No selection receipt for this calling conversation and command.', 404)
    return receipt


def retry_client(service, command_id, source_session_id, requested_client):
    """Only a saved command may leave the caller; dispatch still checks its fingerprint."""
    from .errors import AppError
    if not command_id:
        return None
    try:
        saved = saved_selection(service, command_id, source_session_id)['result']
    except AppError as exc:
        if exc.status == 404:
            return None
        raise
    if requested_client not in (None, saved['clientId']):
        raise AppError('The saved navigation command targets another client.', 409)
    return saved['clientId']


def read_selection(service, command_id, source_session_id):
    """Read only metadata authorized by the caller's existing command receipt."""
    saved = saved_selection(service, command_id, source_session_id)['result']
    client = service.clients.records.get(saved['clientId'], {})
    connected = any(service.queue_clients.get(queue) == saved['clientId'] for queue in service.queues)
    current = {key: client.get(key) for key in
               ('selectedSessionId', 'selectedWorkspaceId', 'selectionRevision')}
    unchanged = all(current[key] == saved[key] for key in current)
    return {'commandId': command_id, 'clientId': saved['clientId'],
            'sourceSessionId': source_session_id, 'requestedSessionId': saved['selectedSessionId'],
            **current, 'connected': connected,
            'status': 'disconnected' if not connected else 'selected' if unchanged else 'changed',
            'rendering': 'unverified', 'revision': service._state['revision']}


def workspace_for(service, session):
    index = service.projections.sessions(service.state)
    return index.workspaces.get(session.get('workspaceId')) or next(
        (row for row in index.workspaces.values() if row.get('path') and row['path'] == session.get('workspace')), None)


def accepts(service, session):
    # Reinstating a removed folder or committing a new artifact is a shared
    # mutation. Keep those cases on the ordinary publication path.
    from .managed_chats import is_managed
    if not is_managed(session):
        workspace = workspace_for(service, session)
        if workspace is None or service.state.get('hiddenNativeWorkspaces'):
            return False
    canvas = service.state.get('canvas', {})
    if canvas.get('kind') and not canvas.get('placeholder') and not canvas.get('app'):
        return not canvas.get('_versionWrite') and any(row['id'] == canvas.get('id') for row in service.state.get('canvasArtifacts', []))
    return True


def select(service, session, command_id, fingerprint, *, include_state, caller_session_id=None):
    from .managed_chats import is_managed
    from .workspace_navigation import NAV_KEYS
    from .errors import AppError
    if session.get('_deleting'):
        raise AppError('This chat is being deleted. No new work was started.', 409)
    identity = service.clients.current.get()
    client = service.clients.record()
    previous = copy.deepcopy(client)
    revision = service._state['revision']
    receipt = {'accepted': True, 'revision': revision + 1, 'effects': []}
    try:
        service.clients.remember_canvas()
        canvas = client.get('canvas', {})
        artifact = next((row for row in service.state.get('canvasArtifacts', []) if row['id'] == canvas.get('id')), None)
        if artifact is not None:
            from .canvas_library import presentation
            presentation(service.state, artifact)['view'] = copy.deepcopy(canvas.get('view', {}))
        managed = is_managed(session)
        workspace = None if managed else workspace_for(service, session)['id']
        scope = (client.get('selectedSessionId'), client.get('selectedWorkspaceId'))
        client['selectedSessionId'] = session['id']
        client['selectedWorkspaceId'] = workspace
        client['view']['workSurface'] = 'chat'
        client['view']['unreadVisit'] = session.get('manualUnread')
        if managed:
            client['view'].update(navChatScope='all', navLocationFilter='managed', navWorkspaceList=False)
        elif client['view'].get('navLocationFilter') == 'managed':
            client['view']['navLocationFilter'] = 'all'
        if scope != (session['id'], workspace):
            service.clients.restore_canvas()
        for key in NAV_KEYS | {'navWorkspaceBrowseFor', 'navWorkspaceAncestorsOpen'}:
            client['view'].pop(key, None)
        service.clients.reconcile(identity)
        receipt.update(commandAction='session.select', result=selection_result(service, caller_session_id))
        service._state['revision'] = revision + 1
        service.clients.commit(identity, command_id, fingerprint, receipt)
    except Exception:
        service.db.rollback()
        service.clients.records[identity] = previous
        service.clients.dirty.add(identity)
        service._state['revision'] = revision
        service._client_snapshots.pop(identity, None)
        raise
    service._client_snapshots.pop(identity, None)
    service._client_snapshot_preferences.pop(identity, None)
    for key, frame in list(service._client_snapshots.items()):
        if frame['revision'] == revision:
            service._client_snapshots[key] = {**frame, 'revision': revision + 1}
    if service._browser_snapshot and service._browser_snapshot['revision'] == revision:
        service._browser_snapshot = {**service._browser_snapshot, 'revision': revision + 1}
    # Preserve catalog indexes, other clients, and any pending runtime commit.
    snapshot = None
    for queue in service.queues:
        if service.queue_clients.get(queue) != identity or service.queue_sessions.get(queue) is not None:
            continue
        if snapshot is None:
            snapshot = service.browser_state()
        if queue.full():
            queue.get_nowait()
        queue.put_nowait(snapshot)
    if client.get('kind') == 'web' or session.get('nativeProject'):
        service._task(service.history.refresh_session(session['id']))
    return {**receipt, **({'state': snapshot or service.browser_state()} if include_state else {})}
