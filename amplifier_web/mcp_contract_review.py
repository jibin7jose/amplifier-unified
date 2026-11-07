"""Bounded, explicitly reviewed changes to an existing MCP App binding."""
from __future__ import annotations

import copy
import json

from .mcp_view_recovery import account, digest, revision, saved
from .smart_tools import _visible, configuration_key
from .state_storage import resource

MAX_DEFINITION_BYTES = 1_050_000
MAX_TOOLS = 500


def definitions(service, binding):
    tools = service.smart_tools.schemas.get(binding['serverId'], [])
    if len(tools) > MAX_TOOLS or len(json.dumps(tools).encode()) > MAX_DEFINITION_BYTES:
        from .errors import AppError
        raise AppError('This tool contract exceeds the bounded review size.', 409)
    by_name = {tool['name']: tool for tool in tools}
    launcher = by_name.get(binding['tool'])
    allowed = sorted(name for name in set(binding['allowedTools'])
                     if name in by_name and _visible(by_name[name], 'app'))
    return {'version': 1, 'launcher': copy.deepcopy(launcher),
            'tools': [copy.deepcopy(by_name[name]) for name in allowed],
            'catalogNames': sorted(by_name)}


def capture(service, binding):
    from .resource_files import put
    return put(service.db, definitions(service, binding))


def _guard(service, args):
    from .errors import AppError
    canvas, row = saved(service, args['canvasId'])
    binding = canvas['mcp']
    if canvas.get('readOnlyVersion'):
        raise AppError('This saved version is read-only. Open Latest before reviewing live changes.', 409)
    if args['expectedBindingRevision'] != revision(binding):
        raise AppError('This tool view changed. Review its current connection first.', 409)
    server = next((s for s in service.state['smartTools']['servers'] if s['id'] == binding['serverId']), None)
    if not server or configuration_key(server) != binding['configuration']:
        raise AppError('The server configuration changed. Review the connection in Settings.', 409, code='configuration_changed')
    if server.get('account', {}).get('status') in {'changed', 'accepted', 'unconfirmed'} or (
            binding.get('accountIdentity') != account(server)):
        raise AppError('The account changed or cannot be confirmed. Review the account in Settings.', 409, code='account_review_required')
    if server.get('status') not in {None, 'connected'} or server.get('catalogState') != 'current':
        raise AppError('Reconnect this server in Settings before reviewing its current actions.', 409, code='disconnected')
    current = definitions(service, binding)
    launcher = current['launcher']
    if not launcher or launcher.get('_meta', {}).get('ui', {}).get('resourceUri') != binding['resourceUri']:
        raise AppError('The launching action or app resource identity changed. Open a separately reviewed tool view.', 409, code='resource_changed')
    # Source availability is checked independently of live contract availability.
    from .mcp_view_recovery import source
    source(service, canvas['id'])
    return canvas, row, server, current


def _difference(before, after):
    fields = [('inputSchema', 'inputs'), ('outputSchema', 'outputs'),
              ('annotations', 'annotations'), ('_meta', 'visibility/metadata'),
              ('description', 'description'), ('title', 'title')]
    result = [label for key, label in fields if before.get(key) != after.get(key)]
    known = {key for key, _ in fields} | {'name'}
    if {k: v for k, v in before.items() if k not in known} != {k: v for k, v in after.items() if k not in known}:
        result.append('other definition fields')
    return result


async def prepare(service, args):
    """Read only: never connect, call the launcher, or persist a new grant."""
    async with service.lock:
        canvas, row, server, current = _guard(service, args)
        binding = copy.deepcopy(canvas['mcp'])
        target = (canvas['id'], canvas.get('revision', 1), canvas.get('selectedVersion'),
                  service.clients.current.get(), service.clients.selection_revision(service.clients.current.get()) if service.clients.current.get() else None)
        catalog_revision = server.get('catalogRevision')
    app = await service.smart_tools.read_app(binding['serverId'], binding['resourceUri'])
    async with service.lock:
        from .errors import AppError
        canvas, row, server, latest = _guard(service, args)
        now = (canvas['id'], canvas.get('revision', 1), canvas.get('selectedVersion'),
               service.clients.current.get(), service.clients.selection_revision(service.clients.current.get()) if service.clients.current.get() else None)
        if target != now or current != latest or catalog_revision != server.get('catalogRevision'):
            raise AppError('The tool view changed while reading. Review it again.', 409)
        previous = None
        try:
            previous = resource(service.db, binding['contractSnapshot']['$resource'])
            if (not isinstance(previous, dict) or previous.get('version') != 1
                    or not isinstance(previous.get('tools'), list)
                    or len(previous['tools']) > MAX_TOOLS
                    or not all(isinstance(tool, dict) and isinstance(tool.get('name'), str) for tool in previous['tools'])
                    or not isinstance(previous.get('catalogNames'), list)
                    or not all(isinstance(name, str) for name in previous['catalogNames'])
                    or not isinstance(previous.get('launcher'), (dict, type(None)))):
                previous = None
        except (KeyError, TypeError, ValueError, OSError):
            pass
        before = {t['name']: t for t in previous['tools']} if previous else {}
        after = {t['name']: t for t in current['tools']}
        changes = [{'name': name, 'fields': _difference(before[name], tool)}
                   for name, tool in after.items() if name in before and before[name] != tool]
        old_names = set(binding['allowedTools'])
        old_catalog = set(previous.get('catalogNames', [])) if previous else old_names
        from .mcp_view_recovery import source
        summary = {'previousDefinitionsAvailable': previous is not None,
                   'added': sorted(set(current['catalogNames']) - old_catalog),
                   'removed': sorted(old_names - set(after)), 'changed': changes,
                   'removedCatalog': sorted(old_catalog - set(current['catalogNames'])),
                   'launcherChanged': _difference(previous['launcher'], current['launcher']) if previous and previous.get('launcher') else [],
                   'allowedTools': sorted(after), 'newToolsGranted': [],
                   'tools': current['tools'],
                   'documentChanged': digest(source(service, canvas['id'])) != digest(app['html']),
                   'requestedCspChanged': binding.get('requestedCsp', {}) != app.get('csp', {}),
                   'requestedPermissionsChanged': binding.get('requestedPermissions', {}) != app.get('permissions', {}),
                   'requestedCsp': app.get('csp', {}), 'requestedPermissions': app.get('permissions', {}),
                   'configuration': 'unchanged', 'account': 'unchanged',
                   'permissionsGranted': False}
        proof = {'binding': args['expectedBindingRevision'], 'artifactRevision': canvas.get('revision', 1),
                 'reviewingClient': target[3], 'selectionRevision': target[4],
                 'catalogRevision': catalog_revision, 'definitions': current,
                 'document': digest(app['html']), 'csp': app.get('csp', {}), 'permissions': app.get('permissions', {})}
        return {'canvasId': canvas['id'], 'bindingRevision': args['expectedBindingRevision'],
                'fingerprint': digest(proof), 'changes': summary,
                'artifactRevision': canvas.get('revision', 1), 'catalogRevision': catalog_revision,
                'selectionRevision': target[-1]}, app, current


async def review(service, args):
    value, _, _ = await prepare(service, args)
    return value


async def apply(service, args):
    from .errors import AppError
    from .canvas_library import remember
    from .resource_files import put
    from .mcp_view_recovery import contract
    review, app, current = await prepare(service, args)
    if review['fingerprint'] != args['reviewedContract']:
        raise AppError('The reviewed actions or document changed. Review the changes again.', 409)
    replace = args.get('replaceDocument', False)
    if replace and args.get('discardLocalInput') is not True:
        raise AppError('Confirm that replacing this tool view may reset its unfinished input.', 409)
    async with service.lock:
        canvas, row, server, latest = _guard(service, args)
        owner = service.clients.current.get()
        if (current != latest or review['artifactRevision'] != canvas.get('revision', 1)
                or review['catalogRevision'] != server.get('catalogRevision')
                or review['selectionRevision'] != (service.clients.selection_revision(owner) if owner else None)):
            raise AppError('The tool contract changed. Review it again.', 409)
        if replace:
            from .canvas_versions import assert_clean
            assert_clean(service, canvas['id'])
            # Other clients keep their actual iframe and local input. Pin the
            # old immutable definition instead of auto-adopting replacement HTML.
            for record in [service._state, *service.clients.records.values()]:
                other = record.get('canvas', {})
                if other is not canvas and other.get('id') == canvas['id'] and other.get('selectedVersion') is None:
                    from .canvas_views import CanvasViews
                    from .canvas_versions import definition
                    old = definition(row, other.get('selectedVersion'), service.db)
                    preference = record.get('canvasViews', {}).get('preferences', {}).get('primary:' + canvas['id'], {})
                    other['_retainedMcpRender'] = {'version': other.get('revision', 1),
                        'body': copy.deepcopy(old['body']),
                        'identity': CanvasViews.revision(old) + ':' + str(preference.get('generation', 0))}
                    other.update(selectedVersion=other.get('revision', 1), latestVersion=canvas.get('revision', 1) + 1, readOnlyVersion=True)
            canvas['content'] = app['html']
            canvas.pop('contentResource', None)
            canvas.update(_versionWrite=True, _forceVersion=True)
        binding = canvas['mcp']
        binding.update(allowedTools=[t['name'] for t in current['tools']],
                       catalogRevision=server['catalogRevision'], accountIdentity=account(server),
                       contractSnapshot=put(service.db, current))
        if replace:
            binding.update(requestedCsp=copy.deepcopy(app.get('csp', {})),
                           requestedPermissions=copy.deepcopy(app.get('permissions', {})))
        binding['contractFingerprint'] = contract(service, binding)['fingerprint']
        # Contract-only acceptance must not let another latest client restore an
        # obsolete binding on a later ordinary presentation save.
        if not replace:
            for record in [service._state, *service.clients.records.values()]:
                other = record.get('canvas', {})
                if other is not canvas and other.get('id') == canvas['id'] and not other.get('readOnlyVersion'):
                    other['mcp'].update({key: copy.deepcopy(binding[key]) for key in (
                        'allowedTools', 'catalogRevision', 'accountIdentity', 'contractSnapshot', 'contractFingerprint')})
        remember(service.state, service.db)
        service._publish_changes(globals={'canvas', 'canvasArtifacts', 'view'})
        return {'canvasId': canvas['id'], 'revision': canvas.get('revision', 1),
                'documentUpdated': replace, 'allowedTools': binding['allowedTools'],
                'status': 'ready', 'message': 'Reviewed tool view connected. Previous calls were not replayed.'}
