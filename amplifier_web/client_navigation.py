"""Select an existing chat without rewriting the shared history catalog."""
import copy


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


def select(service, session, command_id, fingerprint, *, include_state):
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
