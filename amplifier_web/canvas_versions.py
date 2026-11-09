"""Immutable saved definitions, client-local selection and explicit refinements.

Bodies live in the existing resource store. Nothing here executes tool work or
reads a file to reconstruct an older revision.
"""
import copy
import time

FIELDS = ('title', 'kind', 'path', 'workspacePath', 'url', 'body', 'contentResource', 'mcpState')
_CURRENT_MESSAGE = object()


def reference(row, version=None):
    from urllib.parse import urlencode
    return 'amplifier-canvas://artifact/' + row['id'] + '?' + urlencode({'session': row['sessionId'], 'version': version or latest(row)})


def latest(row):
    return row.get('app', {}).get('revision', row.get('revision', 1))


def ordinary_preview(row):
    return row.get('kind') in {'html', 'babylon'} and not row.get('app')


def view_definition(row, canvas, db=None):
    """Resolve the saved running definition, independently of Latest intent."""
    version = canvas.get('selectedVersion')
    if ordinary_preview(canvas):
        version = canvas.get('runningVersion', version or canvas.get('revision', 1))
    selected = definition(row, version, db)
    if ordinary_preview(selected):
        selected = copy.deepcopy(selected)
        selected['runningVersion'] = version or latest(row)
        selected['selectedVersion'] = canvas.get('selectedVersion')
        selected['readOnlyVersion'] = canvas.get('selectedVersion') is not None
        selected['latestVersion'] = latest(row)
        selected['previewRetained'] = selected['selectedVersion'] is None and selected['runningVersion'] < latest(row)
    return selected


def entries(row):
    return row.get('app', {}).get('versions', row.get('versions', []))


def definition(row, version=None, db=None):
    """Select a definition without changing canonical content or shared state."""
    if version is None:
        return row
    from .errors import AppError
    saved = next((item for item in entries(row) if item['version'] == version), None)
    if saved is None:
        if version == latest(row) and not entries(row):
            saved = row
        else:
            raise AppError('This saved artifact version is unavailable. Its history has not been replaced.', 404)
    result = copy.deepcopy(row)
    # Document snapshots replace the definition, including absent optional
    # fields. Otherwise an old inline body inherits the latest large-body URL.
    # Interactive app versions store only title/body; their kind is invariant.
    for key in (('body', 'contentResource') if row.get('app') else FIELDS):
        result.pop(key, None)
    result.update({key: copy.deepcopy(saved[key]) for key in FIELDS if key in saved})
    result['selectedVersion'] = version
    result['latestVersion'] = latest(row)
    result['readOnlyVersion'] = True
    if row.get('app'):
        result['body'] = copy.deepcopy(saved['body'])
        result['contentResource'] = copy.deepcopy(saved['body'])
        from .state_storage import resource
        snapshot = saved if not saved.get('stateResource') else {}
        body = {}
        if db is not None:
            try:
                if saved.get('stateResource'):
                    snapshot = resource(db, saved['stateResource']['$resource'])
                body = resource(db, saved['body']['$resource'])
            except (KeyError, ValueError, OSError, TypeError):
                pass  # Retain damaged references and label unavailable inputs.
        result['app'].update(revision=version, manifest=copy.deepcopy(snapshot.get('manifest', body.get('manifest', row['app']['manifest']))),
                             state=copy.deepcopy(snapshot.get('state', {})), stateRevision=snapshot.get('stateRevision', 0), requests=[], events=[])
        result['historicalStateUnavailable'] = 'state' not in snapshot
    else:
        result['revision'] = version
    return result


def message_id(state, session_id):
    session = next((s for s in state.get('sessions', []) if s['id'] == session_id), {})
    return next((m['id'] for m in reversed(session.get('messages', []))
                 if m.get('role') == 'user' and m.get('id')), None)


def publication(row, state, version, *, message=_CURRENT_MESSAGE):
    mid = message_id(state, row.get('sessionId')) if message is _CURRENT_MESSAGE else message
    if mid:
        link = {'messageId': mid, 'version': version}
        links = row.setdefault('publications', [])
        if link not in links:
            links.append(link)


def save(record, previous, state, *, force=False, message=_CURRENT_MESSAGE):
    """Append only content definitions, never renderer or connection chatter."""
    if previous:
        record['versions'] = copy.deepcopy(previous.get('versions') or [
            {**{k: copy.deepcopy(previous[k]) for k in FIELDS if k in previous},
             'version': previous.get('revision', 1), 'createdAt': previous.get('createdAt'),
             'messageId': previous.get('messageId')}])
        record['publications'] = copy.deepcopy(previous.get('publications') or [
            {'messageId': previous.get('messageId'), 'version': previous.get('revision', 1)}])
        unchanged = all(record.get(k) == previous.get(k) for k in FIELDS if k != 'mcpState')
        # A dashboard's saved result context is part of its immutable snapshot.
        if record.get('kind') == 'mcp-app':
            unchanged = unchanged and record.get('_presentationWrite') is not True
        record['revision'] = previous.get('revision', 1) + int(force or not unchanged)
        if unchanged and not force:
            publication(record, state, record['revision'], message=message)
            return
    else:
        record['versions'], record['publications'], record['revision'] = [], [], 1
    record['versions'].append({**{k: copy.deepcopy(record[k]) for k in FIELDS if k in record},
        'version': record['revision'], 'createdAt': time.time(),
        'messageId': message_id(state, record.get('sessionId')) if message is _CURRENT_MESSAGE else message})
    publication(record, state, record['revision'], message=message)


def match_file(state, canvas):
    """Only a proven canonical source path can reuse a document identity."""
    if not canvas.get('path') or not canvas.get('workspacePath'):
        return
    candidates = [r for r in state.get('canvasArtifacts', [])
                  if all(r.get(k) == canvas.get(k) for k in ('sessionId', 'workspaceId', 'workspacePath', 'path'))
                  and not r.get('versionGroupId') and not r.get('app')]
    if candidates:
        previous = min(candidates, key=lambda r: r.get('createdAt', 0))
        canvas.update(id=previous['id'], createdAt=previous.get('createdAt', time.time()))


def assert_clean(service, identity):
    from .errors import AppError
    for client in service.clients.records.values():
        if client.get('canvasViews', {}).get('preferences', {}).get('primary:' + identity, {}).get('dirty'):
            raise AppError('Finish or cancel the artifact edit before changing its saved version.', 409, code='canvas_view_dirty')


def definitions(schema, string):
    identity = {'id': string(100), 'sessionId': string(200), 'clientId': string(100)}
    return {
        'canvas.versions.inspect': ('Inspect saved definitions, exact-version links and bounded same-client quality feedback without selecting or executing them. saved is not rendered; quality not_checked/pending never means success.',
            schema({**identity, 'includeSource': {'type': 'boolean'}, 'version': {'type': 'integer', 'minimum': 1}}, ['id'])),
        'canvas.versions.revise': ('Save a new document definition in the same tab. Inspect first and supply its expectedRevision; never rewrites earlier versions. Use canvas.apps.revise for interactive surfaces.',
            schema({**identity, 'expectedRevision': {'type': 'integer', 'minimum': 1}, 'content': string(7000000), 'surface': {'type': 'object'}, 'title': string(200)}, ['id', 'expectedRevision'])),
        'canvas.versions.restore': ('Copy a saved document definition to a new latest revision. Does not rewrite history, files or execute tool work. Use canvas.apps.restore for interactive surfaces.',
            schema({**identity, 'expectedRevision': {'type': 'integer', 'minimum': 1}, 'version': {'type': 'integer', 'minimum': 1}}, ['id', 'expectedRevision', 'version']))}


def command(service, action, args, origin):
    from .errors import AppError
    from .canvas_library import load, remember
    from .state_storage import resource
    state = service.state
    sid = args.get('sessionId') or state.get('selectedSessionId')
    owner = service._session(sid, hydrate=False)
    row = next((r for r in state.get('canvasArtifacts', []) if r['id'] == args['id'] and r.get('sessionId') == sid), None)
    if row is None:
        raise AppError('This artifact is unavailable in the calling conversation.', 404)
    if action == 'canvas.versions.inspect':
        from .canvas_paths import paths
        from .canvas_downloads import filename
        version = args.get('version') or latest(row)
        # Inspect metadata without loading document bodies or historical app
        # state. includeSource remains the explicit body-read opt-in below.
        selected = definition(row, args.get('version'))
        result = {'id': row['id'], 'title': selected['title'], 'revision': latest(row), 'version': version,
                  'reference': reference(row, version), 'filePaths': paths(state, selected),
                  'versions': [{k: v.get(k) for k in ('version', 'title', 'createdAt', 'messageId')} for v in entries(row)]}
        if selected.get('kind') in {'text', 'markdown', 'code', 'html', 'mermaid', 'dot', 'json', 'jsonl', 'babylon'} and not selected.get('app'):
            result['download'] = {'url': '/api/canvas/' + row['id'] + '/download?version=' + str(version),
                                  'filename': filename(selected)}
        if args.get('includeSource'):
            result['source'] = resource(service.db, selected['body']['$resource'])
        from .canvas_feedback import saved
        result.update(saved(service, selected, version))
        return result
    if owner.get('historyReadOnlyReason') or owner.get('ownership', {}).get('status') in {'blocked', 'yielding', 'yielded', 'yield-failed', 'taking-over'}:
        raise AppError('This conversation is read-only here.', 409)
    if row.get('kind') == 'gif':
        raise AppError('Retained GIF references are immutable. Open the original GIF instead.', 409)
    if row.get('app') or row.get('kind') in {'mcp-app', 'browser'}:
        raise AppError('Use the surface or tool presentation contract to revise this artifact.', 409)
    if args['expectedRevision'] != latest(row):
        raise AppError('The artifact changed. Inspect it and retry with its latest revision.', 409)
    assert_clean(service, row['id'])
    if action == 'canvas.versions.restore':
        selected = definition(row, args['version'], service.db)
        canvas = {**copy.deepcopy(selected), **resource(service.db, selected['body']['$resource'])}
    else:
        from .workspace_canvas import canvas_command
        scoped = {**state, 'selectedSessionId': sid, 'selectedWorkspaceId': row.get('workspaceId')}
        payload = {key: args[key] for key in ('content', 'surface') if key in args}
        if ('surface' if row['kind'] == 'a2ui' else 'content') not in payload:
            raise AppError('Supply surface for A2UI or content for this saved document.')
        canvas_command(scoped, 'canvas.show', {'kind': row['kind'], **payload, 'title': args.get('title', row['title'])}, origin)
        canvas = scoped['canvas']
        for key in ('path', 'workspacePath'):
            if key in row:
                canvas[key] = row[key]
    for key in ('selectedVersion', 'latestVersion', 'readOnlyVersion', 'contentResource', 'body', 'versions', 'publications'):
        canvas.pop(key, None)
    canvas.update(id=row['id'], sessionId=sid, workspaceId=row.get('workspaceId'), createdAt=row.get('createdAt'), _versionWrite=True, _forceVersion=True)
    scoped = {**state, 'canvas': canvas}
    remember(scoped, service.db)
    if (state.get('canvas', {}).get('id') == row['id']
            and not ordinary_preview(state['canvas'])
            and (state['canvas'].get('selectedVersion') is None or action == 'canvas.versions.restore')):
        load(state, service.db, row['id'], open_panel=state['canvas'].get('open', False))
    from .canvas_feedback import saved
    return {'id': row['id'], 'revision': row['revision'], 'version': row['revision'],
            'reference': reference(row), **saved(service, row, row['revision'])}


def sync(service):
    """Passive Latest follows; executable previews discover without replacing."""
    from .canvas_library import load
    rows = {r['id']: r for r in service._state.get('canvasArtifacts', [])}
    for record in [service._state, *service.clients.records.values()]:
        canvas = record.get('canvas', {})
        row = rows.get(canvas.get('id'))
        if not row or row.get('app'):
            continue
        if ordinary_preview(canvas):
            canvas.setdefault('runningVersion', canvas.get('selectedVersion') or canvas.get('revision', 1))
            canvas.update(latestVersion=latest(row),
                          previewRetained=canvas.get('selectedVersion') is None and canvas['runningVersion'] < latest(row),
                          versions=copy.deepcopy(entries(row)), publications=copy.deepcopy(row.get('publications', [])))
            continue
        if canvas.get('selectedVersion') is not None:
            continue
        if canvas.get('revision', 1) != latest(row):
            scoped = {**service._state, **record}
            load(scoped, service.db, row['id'], open_panel=canvas.get('open', False))
            record['canvas'] = scoped['canvas']


def fork_definition(row, kept, db=None):
    """A fork at an earlier turn must not inherit later edits of that artifact."""
    links = row.get('publications')
    if not links:
        return copy.deepcopy(row)
    allowed = {link['version'] for link in links if link.get('messageId') in kept}
    versions = [copy.deepcopy(v) for v in entries(row) if v['version'] in allowed]
    if not versions:
        return None
    last = max(versions, key=lambda v: v['version'])
    cloned = definition(row, last['version'], db)
    for key in ('selectedVersion', 'latestVersion', 'readOnlyVersion', 'historicalStateUnavailable'):
        cloned.pop(key, None)
    cloned['publications'] = [copy.deepcopy(link) for link in links if link.get('messageId') in kept]
    if cloned.get('app'):
        cloned['app']['versions'] = versions
    else:
        cloned['versions'] = versions
    return cloned
