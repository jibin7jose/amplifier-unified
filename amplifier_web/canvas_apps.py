"""Conversation-owned interactive surfaces; no executable host capabilities in HTML.

Definitions are immutable resources. Identity, validated state, events and requests
belong to the conversation. Every mutation runs under AppService's action lock.
"""
import copy
import hashlib
import json
import time
import uuid

from jsonschema import Draft202012Validator, SchemaError, ValidationError

from .resource_files import put
from .state_storage import resource


def fail(message, status=400):
    from .errors import AppError
    raise AppError(message, status)


def bounded(value, limit=100_000):
    try:
        raw = json.dumps(value, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        fail('Surface data must be finite JSON.')
    if len(raw.encode()) > limit:
        fail('Surface data exceeds its size limit.')
    def walk(item, depth=0):
        if depth > 24:
            fail('Surface data is nested too deeply.')
        if isinstance(item, dict):
            if any(k in {'__proto__', 'constructor', 'prototype', '$resource'} for k in item):
                fail('Surface data contains a reserved key.')
            for v in item.values():
                walk(v, depth + 1)
        elif isinstance(item, list):
            for v in item:
                walk(v, depth + 1)
    walk(value)
    return copy.deepcopy(value)


def check_schema(spec):
    # References and regexes can fetch external documents, recurse indefinitely,
    # or consume unbounded CPU. This version deliberately excludes them.
    def walk(value):
        if isinstance(value, dict):
            if any(k in {'$ref', '$dynamicRef', 'pattern', 'patternProperties'} for k in value):
                fail('Surface schemas cannot use references or regular expressions.')
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(spec)
    try:
        Draft202012Validator.check_schema(spec)
    except SchemaError:
        fail('Invalid surface JSON schema.')


def validate(value, spec):
    try:
        Draft202012Validator(spec).validate(value)
    except ValidationError as exc:
        fail('Surface data does not match its schema: ' + exc.message[:300])


HOST_ACTIONS = {'theme.preview', 'theme.apply', 'theme.revert'}
from .themes import TOKENS as THEME_TOKENS, theme_input


def manifest(value):
    result = bounded(value, 32_000)
    if set(result) - {'version', 'stateSchema', 'events', 'requests', 'theme'} or result.get('version') != 1:
        fail('Use surface manifest version 1 with stateSchema, events, requests and theme.')
    if not isinstance(result.get('stateSchema'), dict) or result['stateSchema'].get('type') != 'object':
        fail('A surface stateSchema with type object is required.')
    check_schema(result['stateSchema'])
    if result.get('theme', 'inherit') not in {'inherit', 'accent-only', 'fixed'}:
        fail('Choose inherit, accent-only or fixed theme behavior.')
    for group in ('events', 'requests'):
        entries = result.setdefault(group, {})
        if not isinstance(entries, dict) or len(entries) > 32:
            fail('Declare at most 32 surface ' + group + '.')
        for name, entry in entries.items():
            if not 1 <= len(name) <= 80 or not isinstance(entry, dict):
                fail('Each surface operation needs a name and a declaration.')
            allowed = {'schema', 'updates', 'label'} if group == 'events' else {'schema', 'action', 'label'}
            if set(entry) - allowed or not isinstance(entry.get('schema'), dict):
                fail('Each surface operation needs a JSON schema.')
            check_schema(entry['schema'])
            if 'label' in entry and (not isinstance(entry['label'], str) or len(entry['label']) > 120):
                fail('Operation labels must be short text.')
            if group == 'requests' and entry.get('action') not in HOST_ACTIONS:
                fail('This surface host action is not supported.')
            updates = entry.get('updates', {})
            if not isinstance(updates, dict) or any(not isinstance(v, str) for v in updates.values()):
                fail('Event updates map state field names to payload field names.')
    return result


def definitions(schema, string):
    identity = {'id': string(100), 'sessionId': string(200), 'clientId': string(100)}
    cas = {**identity, 'expectedRevision': {'type': 'integer', 'minimum': 1},
           'expectedStateRevision': {'type': 'integer', 'minimum': 0}}
    required = ['id', 'expectedRevision', 'expectedStateRevision']
    actions = {
        'create': ('Create one conversation-owned interactive surface. Keep its returned id for every refinement.',
                   schema({'title': string(200), 'content': string(500_000), 'manifest': {'type': 'object'},
                           'initialState': {'type': 'object'}, 'sessionId': string(200), 'clientId': string(100)},
                          ['title', 'content', 'manifest', 'initialState'])),
        'inspect': ('Read surface state, declared events, pending host requests and revision history; does not select it.',
                    schema({**identity, 'includeSource': {'type': 'boolean'}, 'requestId': string(100)}, ['id'])),
        'revise': ('Refine this surface in its existing tab. Preserve compatible state; explicit state migration requires both revisions.',
                   schema({**cas, 'content': string(500_000), 'manifest': {'type': 'object'}, 'title': string(200),
                           'migratedState': {'type': 'object'}}, [*required, 'content'])),
        'restore': ('Restore a retained definition in the same tab, preserving current compatible state; never replay actions.',
                    schema({**cas, 'version': {'type': 'integer', 'minimum': 1}, 'migratedState': {'type': 'object'}}, [*required, 'version'])),
        'state': ('Merge validated top-level state fields. Reject stale user or agent edits.',
                  schema({**cas, 'patch': {'type': 'object'}}, [*required, 'patch'])),
        'event': ('Invoke a declared event using the same host-side state updates as the user interface.',
                  schema({**cas, 'name': string(80), 'payload': {'type': 'object'}}, [*required, 'name', 'payload'])),
        'request': ('Ask the host to preview, apply or revert a theme. Queues a reviewable request; does not execute it.',
                    schema({**cas, 'name': string(80), 'input': {'type': 'object'}}, [*required, 'name', 'input'])),
        'resolve': ('Approve or reject a pending host request. Agents may resolve changes already authorized by the user on an explicit attached clientId. Only the sandbox cannot call this action.',
                    schema({**cas, 'requestId': string(100), 'approve': {'type': 'boolean'}}, [*required, 'requestId', 'approve'])),
    }
    return {'canvas.apps.' + name: value for name, value in actions.items()}


def theme_fingerprint(theme):
    return hashlib.sha256(json.dumps(theme, sort_keys=True).encode()).hexdigest()


def theme_command(service, action, args):
    """Same implementation for ordinary controls, app_control and approved requests."""
    from .service import validate_theme
    view = service.state['view']
    if action in {'theme.apply', 'theme.preview'}:
        args = theme_input(service, args)
        validate_theme(args['css'])
        theme = {'name': args['name'] or 'Custom skin', 'css': args['css']}
        for key in ('definition', 'palettePatch'):
            if key in args:
                theme[key] = copy.deepcopy(args[key])
        if action == 'theme.preview':
            if service.clients.record() is None:
                fail('Attach a client for a theme preview.', 409)
            view.update(themeDraft=theme['css'], themeDraftName=theme['name'], themePreview=True)
        else:
            view['themeUndo'] = {'before': put(service.db, service.state['theme']), 'after': theme_fingerprint(theme)}
            service.state['theme'] = theme
            view['themePreview'] = False
    elif action == 'theme.revert':
        if view.get('themePreview'):
            view['themePreview'] = False
        else:
            undo = view.get('themeUndo')
            if not undo or undo['after'] != theme_fingerprint(service.state['theme']):
                fail('The applied theme changed, or there is no theme change to undo.', 409)
            service.state['theme'] = resource(service.db, undo['before']['$resource'])
            view.pop('themeUndo', None)


def sync(service):
    """Canonical shared surface state must never be overwritten by a client copy."""
    rows = {r['id']: r for r in service._state.get('canvasArtifacts', []) if r.get('app')}
    for record in [service._state, *service.clients.records.values()]:
        canvas = record.get('canvas', {})
        row = rows.get(canvas.get('id'))
        if row and canvas.get('selectedVersion') is None and (canvas.get('app', {}).get('revision'), canvas.get('app', {}).get('stateRevision'), canvas.get('contentResource')) != (row['app']['revision'], row['app']['stateRevision'], row['contentResource']):
            canvas.update({k: copy.deepcopy(row[k]) for k in ('title', 'app', 'contentResource')})
            canvas.pop('content', None)


def snapshot(row, db, include_source=False):
    result = {k: copy.deepcopy(row[k]) for k in ('id', 'title', 'sessionId', 'workspaceId', 'app')}
    from .canvas_versions import reference
    result['reference'] = reference(row)
    result['source'] = copy.deepcopy(row['body'])
    if include_source:
        result['content'] = resource(db, row['body']['$resource'])['content']
    return result


def command(service, action, args, origin):
    state, db = service.state, service.db
    sid = args.get('sessionId') or state.get('selectedSessionId')
    owner = service._session(sid, hydrate=False)
    if owner.get('historyReadOnlyReason') or owner.get('ownership', {}).get('status') in {'blocked', 'yielding', 'yielded', 'yield-failed', 'taking-over'}:
        fail('This conversation is read-only here.', 409)
    name = action.removeprefix('canvas.apps.')
    if name == 'create':
        spec = manifest(args['manifest'])
        value = bounded(args['initialState'])
        validate(value, spec['stateSchema'])
        body = put(db, {'content': args['content'], 'manifest': spec})
        workspace = next((w for w in state['workspaces'] if w['path'] == owner['workspace']), {})
        row = {'id': uuid.uuid4().hex, 'kind': 'canvas-app', 'title': args['title'], 'sessionId': sid,
               'workspaceId': workspace.get('id', owner.get('workspaceId')), 'createdAt': time.time(),
               'messageId': next((m['id'] for m in reversed(owner.get('messages', [])) if m.get('role') == 'user'), None),
               'body': body, 'contentResource': body, 'tabOpen': True,
               'app': {'revision': 1, 'stateRevision': 0, 'manifest': spec, 'state': value,
                       'versions': [{'version': 1, 'body': body, 'title': args['title'], 'stateResource': put(db, {'state': value, 'stateRevision': 0}), 'createdAt': time.time()}], 'events': [], 'requests': []}}
        from .canvas_versions import publication
        publication(row, state, 1)
        state.setdefault('canvasArtifacts', []).append(row)
        if sid == state.get('selectedSessionId') and row['workspaceId'] == state.get('selectedWorkspaceId'):
            from .canvas_library import load
            load(state, db, row['id'])
            state['view'].setdefault('canvasDraft', {}).update(library=False, open=False, browser=False)
        return snapshot(row, db)
    row = next((r for r in state.get('canvasArtifacts', []) if r['id'] == args['id'] and r.get('app')), None)
    if not row or row['sessionId'] != sid:
        fail('This surface is unavailable in the calling conversation.', 404)
    if name == 'inspect':
        result = snapshot(row, db, args.get('includeSource', False))
        result['views'] = [{'clientId': identity, 'viewId': key.split(':', 1)[0],
                            'dirty': bool(preference.get('dirty')), 'renderStatus': preference.get('activation')}
                           for identity, client in service.clients.records.items()
                           for key, preference in client.get('canvasViews', {}).get('preferences', {}).items()
                           if key == 'primary:' + row['id']]

        if args.get('requestId'):
            request = next((r for r in row['app']['requests'] if r['id'] == args['requestId']), None)
            if not request:
                fail('This host request is unavailable.', 404)
            result['requestInput'] = resource(db, request['input']['$resource'])
        return result
    if state.get('canvas', {}).get('id') == row['id'] and state['canvas'].get('selectedVersion') is not None and name not in {'revise', 'restore'}:
        fail('This saved version is read-only. Select Latest before changing the surface.', 409)
    app = copy.deepcopy(row['app'])
    if args['expectedRevision'] != app['revision'] or args['expectedStateRevision'] != app['stateRevision']:
        fail('The surface changed. Inspect it and retry with current revisions.', 409)
    if name in {'revise', 'restore'}:
        for client in service.clients.records.values():
            for key, preference in client.get('canvasViews', {}).get('preferences', {}).items():
                if key == 'primary:' + row['id'] and preference.get('dirty'):
                    fail('Finish or cancel the surface edit before refining it. Inspect this surface for dirty view client IDs; save its unfinished input before retrying.', 409)
        if name == 'restore':
            version = next((v for v in app['versions'] if v['version'] == args['version']), None)
            if not version:
                fail('That surface revision is no longer retained.', 404)
            definition = resource(db, version['body']['$resource'])
            spec, content, title = definition['manifest'], definition['content'], version['title']
        else:
            spec = manifest(args.get('manifest', app['manifest']))
            content, title = args['content'], args.get('title', row['title'])
        value = bounded(args.get('migratedState', app['state']))
        validate(value, spec['stateSchema'])
        body = put(db, {'content': content, 'manifest': spec})
        app.update(revision=app['revision'] + 1, manifest=spec, state=value)
        app['versions'] = app['versions'] + [{'version': app['revision'], 'body': body, 'title': title, 'stateResource': put(db, {'state': value, 'stateRevision': app['stateRevision'] + 1}), 'createdAt': time.time()}]
        from .canvas_versions import publication
        publication(row, state, app['revision'])
        for request in app['requests']:
            if request['status'] == 'pending':
                request['status'] = 'superseded'
        row.update(body=body, contentResource=body, title=title)
    elif name in {'state', 'event'}:
        if name == 'state':
            patch = bounded(args['patch'])
        else:
            entry = app['manifest']['events'].get(args['name'])
            if not entry:
                fail('This surface event is not declared.')
            payload = bounded(args['payload'], 16_000)
            validate(payload, entry['schema'])
            if any(field not in payload for field in entry.get('updates', {}).values()):
                fail('The event payload is missing an updated field.')
            patch = {key: payload[field] for key, field in entry.get('updates', {}).items()}
            app['events'] = (app['events'] + [{'name': args['name'], 'payload': payload, 'origin': origin, 'at': time.time()}])[-50:]
        value = bounded({**app['state'], **patch})
        validate(value, app['manifest']['stateSchema'])
        app['state'] = value
    elif name == 'request':
        entry = app['manifest']['requests'].get(args['name'])
        if not entry:
            fail('This host request is not declared.')
        if sum(r['status'] == 'pending' for r in app['requests']) >= 5:
            fail('Resolve the pending host requests first.', 409)
        payload = bounded(args['input'], 300_000)
        validate(payload, entry['schema'])
        from .service import ACTION_DEFINITIONS, validate_theme
        validate(payload, ACTION_DEFINITIONS[entry['action']][1])
        if entry['action'] in {'theme.preview', 'theme.apply'}:
            payload = bounded(theme_input(service, payload), 300_000)
            validate_theme(payload['css'])
        app['requests'] = (app['requests'] + [{'id': uuid.uuid4().hex, 'name': args['name'],
            'action': entry['action'], 'input': put(db, payload), 'status': 'pending', 'origin': origin,
            'summary': payload.get('name', 'Previous theme'), 'at': time.time(),
            'themeFingerprint': theme_fingerprint(state['theme'])}])
        pending = [r for r in app['requests'] if r['status'] == 'pending']
        completed = [r for r in app['requests'] if r['status'] != 'pending'][-(20 - len(pending)):]
        app['requests'] = sorted(pending + completed, key=lambda r: r['at'])
    elif name == 'resolve':
        if service.clients.record() is None:
            fail('Choose an attached client to resolve this host request.', 409)
        request = next((r for r in app['requests'] if r['id'] == args['requestId']), None)
        if not request or request['status'] != 'pending':
            fail('This host request was already resolved or superseded.', 409)
        if args['approve']:
            if request['themeFingerprint'] != theme_fingerprint(state['theme']):
                fail('The shell theme changed since this request. Create a fresh request.', 409)
            theme_command(service, request['action'], resource(db, request['input']['$resource']))
        request.update(status='applied' if args['approve'] else 'rejected', resolvedBy=origin)
    app['stateRevision'] += 1
    row['app'] = app
    sync(service)
    if name == 'restore' and state.get('canvas', {}).get('id') == row['id']:
        from .canvas_library import load
        load(state, db, row['id'], open_panel=state['canvas'].get('open', False))
    return snapshot(row, db)
