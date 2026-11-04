"""Browser-only view contracts. Durable and agent state retain complete evidence.

A panel change is a normal scoped view update, so its next snapshot atomically
brings the requested data. Closing it retires that data from the browser and
its stream baseline; revisiting reads the current server cache, not the network
provider. No omission here changes persistence or action receipts.
"""


def pick(value, fields):
    return {key: value[key] for key in fields if key in value}


def _task_presentation(task, sid):
    """Keep current task fields; recorded history has explicit read owners."""
    if not isinstance(task, dict):
        return task
    result = {key: value for key, value in task.items()
              if key not in {'changes', 'goalState', 'originalObjective'}}
    if isinstance(task.get('changes'), list):
        result['changeCount'] = len(task['changes'])
    identity, revision = task.get('id'), task.get('revision')
    if isinstance(identity, str) and identity:
        result['historyRef'] = {'action': 'task.history',
                                'args': {'sessionId': sid, 'taskId': identity}}
        original = isinstance(task.get('originalObjective'), str)
        result['originalObjectiveAvailable'] = original
        if original and type(revision) is int and revision > 0:
            result['originalObjectiveRef'] = {'action': 'task.detail', 'args': {
                'sessionId': sid, 'taskId': identity, 'expectedRevision': revision,
                'field': 'originalObjective'}}
    return result


def _task_control_presentation(control, sid):
    if not isinstance(control, dict):
        return control
    result = {key: value for key, value in control.items() if key != 'history'}
    if 'task' in control:
        result['task'] = _task_presentation(control['task'], sid)
    if isinstance(control.get('history'), list):
        result['historyCount'] = len(control['history'])
        result['historyRef'] = {'action': 'task.history', 'args': {'sessionId': sid}}
    return result


def settings_page(view, canvas=None):
    canvas = canvas or {}
    in_canvas = (not view.get('panel') and canvas.get('open')
                 and canvas.get('kind') == 'settings'
                 and not view.get('canvasDraft', {}).get('library'))
    if not in_canvas and view.get('panel') not in {'settings', 'appearance'}:
        return None
    if view.get('panel') == 'appearance':
        return 'appearance'
    expanded = view.get('settingsExpanded') or []
    return next(iter(expanded), None) or {'capabilities': 'app-bundles', 'maintenance': 'updates'}.get(view.get('settingsSection'), 'ai-connections')


def worker_history_count(state):
    """Whether the bundled view can display the selected chat's child count.

    Chat work and Canvas overview expose the history button. Canvas is kept
    conservative here: its client-owned artifact views are projected later, so
    an open non-settings Canvas may need the count. Ordinary chat streaming
    with Canvas closed must not query dormant worker history on every frame.
    """
    view = state.get('view', {})
    panel = view.get('panel')
    if panel in {'chat-work', 'subagent-history'}:
        return True
    if panel == 'runtime' and view.get('runtimeDraft', {}).get('tab') == 'direction':
        return True
    return (view.get('workSurface', 'chat') == 'chat'
            and bool(state.get('canvas', {}).get('open'))
            and not view.get('settingsCanvas'))


def attention_session_scope(state):
    """Session notice bodies needed by this view; counts remain global."""
    view = state.get('view', {})
    page = settings_page(view, state.get('canvas'))
    if not page and view.get('workSurface') == 'workspace' and view.get('workWorkspaceTab') == 'details' and view.get('workWorkspaceSettings'):
        page = view['workWorkspaceSettings']
    if view.get('panel') == 'activity' or page == 'chats':
        return None
    identity = state.get('selectedSessionId')
    return (identity,) if identity else ()


def project(result):
    view = result.get('view', {})
    panel, page = view.get('panel'), settings_page(view, result.get('canvas'))
    if not page and view.get('workSurface') == 'workspace' and view.get('workWorkspaceTab') == 'details' and view.get('workWorkspaceSettings'):
        page = view['workWorkspaceSettings']
    runtime = panel in {'runtime', 'session-details', 'chat-work', 'chat-models',
                        'chat-context', 'chat-computer'}
    # Only selected conversation receipts participate in automatic acknowledgement.
    # Full reviewed notices remain available in the Activity and relevant Settings view.
    attention = result.get('attention', {})
    result['attention'] = {**attention,
        'workspaces': {key: value for key, value in attention.get('workspaces', {}).items() if value},
        'items': [item for item in attention.get('items', []) if panel == 'activity'
                  or result.get('selectedSessionId') and item.get('sessionId') == result['selectedSessionId']
                  or page and item.get('page') == page
                  or panel == 'feedback' and item.get('requestId')]}
    updates = result.get('updates', {})
    if page != 'updates':
        result['updates'] = pick(updates, ('phase', 'error', 'detail', 'available', 'appAvailable',
            'pendingApp', 'pendingRelease', 'pendingRestart', 'pendingReplacement', 'installedAt',
            'featureResults', 'adoption'))
        result['updates']['application'] = pick(updates.get('application', {}),
            ('id', 'label', 'kind', 'current', 'latest', 'status', 'canInstall'))
        if panel == 'activity':
            result['updates']['diagnostics'] = {'failureDispositions': [
                pick(row, ('id', 'at', 'target', 'occurrences', 'status', 'recoveryVerified'))
                for row in updates.get('diagnostics', {}).get('failureDispositions', [])[-20:]]}
    smart = result.get('smartTools', {})
    if page not in {'smart-tools', 'tool-connections'}:
        result['smartTools'] = {'servers': [pick(row, ('id', 'name', 'status', 'capabilities'))
                                         for row in smart.get('servers', [])]}
    catalog_settings = page in {'ai-connections', 'overview', 'image-generation', 'providers', 'routing', 'voice', 'defaults', 'desktop'}
    from .model_catalogs import project as project_models
    project_models(result, settings=catalog_settings, runtime=runtime)
    # Setup operation receipts, matrices and diagnostic metadata are page resources.
    if not catalog_settings:
        result['setup'] = pick(result.get('setup', {}), ('providers', 'providerCatalogs', 'metadata',
            'providersWorkspace', 'providersRequestedWorkspace', 'providersLocation', 'providersLoadedAt'))
    if not runtime:
        result['runtimeControl'] = {sid: pick(control, ('configuration.catalog', 'configuration.providers'))
                                    for sid, control in result.get('runtimeControl', {}).items()}
        result['sessions'] = [{**{key: value for key, value in row.items()
                                  if key not in {'configuration', 'collaborationMessageAnchors', 'surfaceInputs'}},
                               **({'task': pick(row['task'], ('id', 'status', 'revision', 'appliedRevision', 'objective', 'blockedReason'))}
                                  if row.get('task') else {})}
                              for row in result.get('sessions', [])]
    else:
        # Runtime panels consume current task fields, not accumulated native
        # verification or old task bodies. Project both aliases before snapshot
        # detachment/serialization; never modify the canonical saved records.
        result['sessions'] = [{**row, 'task': _task_presentation(row['task'], row['id'])}
                              if 'task' in row else row for row in result.get('sessions', [])]
        result['runtimeControl'] = {
            sid: {**control, 'task.get': _task_control_presentation(control['task.get'], sid)}
            if 'task.get' in control else control
            for sid, control in result.get('runtimeControl', {}).items()}
    if page != 'history':
        result['sharedHistory'] = {key: value for key, value in result.get('sharedHistory', {}).items() if key != 'issues'}
    resources = {'registry': {'registries'}, 'bundleDiscovery': {'add-bundles'},
                 'workspaceStarters': {'workspace-starters', 'workspaces'},
                 'diagnostics': {'diagnostics'}, 'maintenance': {'repair', 'reset'},
                 'feedback': set()}
    for key, pages in resources.items():
        if page not in pages and not (key == 'feedback' and panel == 'feedback') and not (key == 'workspaceStarters' and view.get('workSurface') in {'home', 'workspace'}):
            result.pop(key, None)
    # The event journal and past command results are inspected via their existing
    # bounded read APIs, not eagerly replayed into every web client.
    for key in ('events', 'managementResults', 'canvasLibraryMigration', 'defaultBundleMigration',
                'sharedVoiceMigration', 'clientViewsMigrated', 'hiddenNativeSessions'):
        result.pop(key, None)
    if not view.get('locationPicker') and not (view.get('workSurface') == 'workspace' and view.get('workWorkspaceTab') == 'files'):
        result.pop('locationListing', None)
    return result
