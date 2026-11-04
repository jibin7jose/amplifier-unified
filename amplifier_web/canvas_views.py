"""Client-local renderer choices for the selected artifact.

Saved artifacts remain in the library. Inline previews and the full Canvas
viewer address the same immutable definitions through this owner.
"""
from __future__ import annotations

import copy
import hashlib
import json
import uuid

from .shell_modules import IDENTITY, encoded, fail

PROFILE = 'trusted-native-renderer-v1'
KINDS = ['markdown', 'text', 'code', 'json', 'jsonl', 'image', 'pdf', 'html', 'babylon', 'mermaid', 'dot', 'a2ui', 'browser']
INLINE_KINDS = frozenset({'markdown', 'text', 'code', 'json', 'jsonl', 'image', 'html', 'babylon', 'mermaid', 'dot'})
CAPABILITIES = ['canvas.resource.read', 'canvas.view.update', 'canvas.view.report']
LABELS = {'markdown': 'Markdown', 'text': 'Plain text', 'code': 'Code', 'json': 'JSON', 'jsonl': 'JSON lines',
          'image': 'Image', 'gif': 'Retained GIF', 'pdf': 'PDF', 'html': 'HTML', 'babylon': '3D scene', 'mermaid': 'Mermaid', 'dot': 'Graphviz',
          'a2ui': 'Interactive controls', 'browser': 'Website', 'mcp-app': 'Tool app', 'canvas-app': 'Interactive surface', 'settings': 'Settings'}
BUILTINS = {'builtin.canvas.' + kind: {'id': 'builtin.canvas.' + kind, 'label': label,
    'version': '1.0.0', 'apiVersion': '1.0', 'profile': PROFILE, 'stateSchema': 'canvas-view-v1',
    'capabilities': CAPABILITIES, 'resourceKinds': [kind]} for kind, label in LABELS.items()}


def definitions(schema, string):
    target = {'viewId': string(100), 'resourceId': IDENTITY,
              'resourceRevision': string(64), 'generation': {'type': 'integer', 'minimum': 0}}
    actions = {
        'canvas.views.inline': ('Allocate a bounded renderer lease for an exact saved version published in a message OR attached to a question. Supply messageId or questionId plus immutable questionContentRevision, never both. Does not expand a card or start work; use canvas.select presentation:inline. presentationRequest fences the current card choice.', schema({'resourceId': IDENTITY, 'version': {'type': 'integer', 'minimum': 1}, 'messageId': IDENTITY, 'questionId': IDENTITY, 'questionContentRevision': {'type': 'integer', 'minimum': 1}, 'previewId': IDENTITY, 'presentationRequest': {'type': 'boolean'}}, ['resourceId', 'version', 'previewId'])),
        'canvas.views.release': ('Close this exact inline preview without changing the saved artifact or Canvas selection.', schema(target, list(target))),
        'canvas.views.imageDraft': ('Attach this exact saved image to the current client draft for a follow-up. Does not send, generate, or overwrite the original.', schema(target, list(target))),
        'canvas.views.reveal': ('Reveal the exact artifact file in the file manager on its named owning app host. It does not open the browser device file manager. Bind hostId and instanceId from fileReveal.', schema({**target,'hostId':string(200),'instanceId':string(100)}, [*target,'hostId','instanceId'])),
        'canvas.views.inspect': ('Inspect the selected artifact view and available renderers.', schema()),
        'canvas.views.loadVersion': ('Explicit UI-only replacement of an ordinary HTML/3D preview with one exact saved version. Rejects stale targets and dirty edits; preserves selection intent unless followLatest is explicit, but resets preview-local state.', schema({**target, 'version': {'type': 'integer', 'minimum': 1}, 'followLatest': {'type': 'boolean'}}, [*target, 'version'])),
        'canvas.views.renderer': ('Select a validated renderer for this exact artifact/view generation.', schema({**target, 'renderer': string(100)})),
        'canvas.views.recover': ('Restore this view to its built-in renderer; retain the saved artifact.', schema(target)),
        'canvas.views.command': ('Invoke an explicitly targeted renderer control without retargeting the selected conversation.', schema({**target, 'action': string(100), 'args': {'type': 'object'}}, [*target, 'action', 'args'])),
        'canvas.views.dirty': ('Declare unsaved renderer-local edits before a replacement or close.', schema({**target, 'dirty': {'type': 'boolean'}, 'editVersion': {'type': 'integer', 'minimum': 0}}, [*target, 'dirty'])),
        'canvas.views.status': ('Record browser mount evidence for this renderer; not a package validation receipt.', schema({**target, 'status': {'enum': ['loading', 'ready', 'error']}, 'message': string(2000)})),
        'canvas.views.observe': ('Report bounded, untrusted drawing and visible-control evidence for this exact view. Does not start an agent turn.', schema({**target,
            'revision': string(80), 'editVersion': {'type': 'integer', 'minimum': 0}, 'pending': {'type': 'boolean'},
            'status': {'enum': ['ready', 'pending', 'unavailable']}, 'image': string(470000), 'text': string(1800),
            'controls': {'type': 'array', 'maxItems': 16, 'items': schema({'label': string(100), 'value': string(100)}, ['label', 'value'])},
            'reason': string(200), 'checkpointId': string(100)}, [*target, 'revision', 'editVersion', 'pending', 'status'])),
    }
    for _, spec in actions.values():
        spec['properties']['clientId'] = IDENTITY
    return actions


class CanvasViews:
    def __init__(self, service):
        self.service = service

    def record(self):
        client = self.service.clients.record()
        if client is None:
            fail('Attach a client before addressing canvas views.', 409)
        return client.setdefault('canvasViews', {'preferences': {}})

    def presentation(self, identity, version):
        client = self.service.clients.record()
        return (client or {}).get('canvasViews', {}).get('presentations', {}).get(f'{identity}:{version}')

    def select_presentation(self, args, origin):
        """Validate the exact publication before changing client presentation."""
        from .canvas_library import scope
        from .canvas_versions import definition, latest
        row = self.artifact(args['id'])
        if not scope(self.service.state, row):
            fail('This artifact belongs to another chat.', 409)
        version = args.get('version') or latest(row)
        row = definition(row, version, self.service.db)
        destination = args.get('presentation', 'canvas')
        source = {}
        if destination == 'inline' or any(key in args for key in ('messageId', 'questionId', 'questionContentRevision')):
            if 'version' not in args:
                fail('A source-bound presentation needs an exact saved version.', 409)
            source = self.source_binding(row, version, args)
        if destination == 'inline' and row['kind'] not in INLINE_KINDS:
            fail('This artifact opens in Canvas.', 409)
        if args.get('resetPreview') and origin != 'ui':
            fail('Only an explicit user control can reset an interactive preview.', 403)
        self.choose(row, version, destination, source=source,
                    reset=args.get('resetPreview', False), origin=origin)
        return {'id': row['id'], 'version': version, 'presentation': destination}

    @staticmethod
    def validate_inline(row, version, message):
        if row['kind'] not in INLINE_KINDS:
            fail('This artifact opens in Canvas.', 409)
        if {'messageId': message, 'version': version} not in row.get('publications', []):
            fail('This version was not published in that message.', 409)

    def source_binding(self, row, version, args, *, legacy_publication=False):
        """One exact publication or immutable question reference, never both."""
        if 'questionId' in args:
            if 'messageId' in args or 'questionContentRevision' not in args:
                fail('Supply one question binding, not a message publication.', 409)
            self.service.questions.review_artifact(row['sessionId'], args['questionId'],
                args['questionContentRevision'], row['id'], version)
            return {key: args[key] for key in ('questionId', 'questionContentRevision')}
        if 'messageId' not in args or 'questionContentRevision' in args:
            fail('Supply a message publication or an exact question binding.', 409)
        publications = row.get('publications', [])
        if legacy_publication and not publications:
            from .canvas_versions import latest
            publications = [{'messageId': row.get('messageId'), 'version': latest(row)}]
        if {'messageId': args['messageId'], 'version': version} not in publications:
            fail('This version was not published in that message.', 409)
        return {'messageId': args['messageId']}

    @staticmethod
    def same_source(binding, source):
        return all(binding.get(key) == source.get(key)
                   for key in ('messageId', 'questionId', 'questionContentRevision'))

    def choose(self, row, version, destination, *, message_id=None, source=None, reset=False, origin='agent'):
        """One client/exact-version choice; dirty or stateful refusals are atomic.

        Ordinary HTML has no state-transfer protocol. A deliberate UI reset is
        required to move a mounted scriptable preview, never an agent assertion.
        This does not clear a renderer's dirty flag or touch another artifact.
        """
        from .service import AppError
        client = self.service.clients.record()
        if client is None:
            if destination == 'inline':
                fail('Attach a client to preview this publication inline.', 409)
            return
        record = self.record()
        source = source if source is not None else ({'messageId': message_id} if message_id else {})
        primary = client.get('canvas', {})
        same_primary = (primary.get('id') == row['id']
                        and (primary.get('selectedVersion') or primary.get('revision', 1)) == version)
        outgoing = [identity for identity, binding in record.get('inline', {}).items()
                    if binding['resourceId'] == row['id'] and binding['selectedVersion'] == version
                    and (destination != 'inline' or not self.same_source(binding, source))]
        if same_primary and destination != 'canvas' and (primary.get('open') or record.get('retained')):
            outgoing.append('primary')
        # Selecting a new version in the same primary frame is also a reset,
        # even when the request merely says "Latest".
        from .canvas_versions import ordinary_preview
        if (primary.get('id') == row['id'] and not same_primary
                and destination == 'canvas' and ordinary_preview(primary)
                and not primary.get('_presentationDisposed')
                and (primary.get('open') or record.get('retained'))):
            outgoing.append('primary')
        for identity in outgoing:
            preference = record.get('preferences', {}).get(identity + ':' + row['id'], {})
            if preference.get('dirty'):
                raise AppError('Finish or cancel this preview edit before changing its presentation.',
                               409, code='canvas_view_dirty')
            # An open/retained primary or allocated inline lease may already
            # execute before its asynchronous mount report reaches the server.
            if (row['kind'] in {'html', 'babylon'} or identity == 'primary' and ordinary_preview(primary)) and not (reset and origin == 'ui'):
                raise AppError('This interactive preview has local state. Keep it here, or explicitly '
                               'reset the preview to load the requested saved version or presentation.',
                               409, code='canvas_preview_reset_required')
        # Validate every outgoing owner before releasing any view.
        for identity in outgoing:
            if identity != 'primary':
                self.release_inline(identity)
        if same_primary and destination != 'canvas':
            primary['open'] = False
            primary['_presentationDisposed'] = True
            record.pop('retained', None)
            client['view']['canvasFocused'] = False
        elif same_primary:
            primary.pop('_presentationDisposed', None)
        record.setdefault('presentations', {})[f'{row["id"]}:{version}'] = {
            'id': row['id'], 'version': version, 'sessionId': row['sessionId'],
            'presentation': destination, **source}

    def guard_transition(self, action, args):
        """Reject parent actions before they can discard a mounted dirty view.

        This runs under the action lock, before selection, drafts, tabs, or
        external work change. View-specific replacements retain their existing
        deferred receipts; explicit recovery is the deliberate discard route.
        """
        from .service import AppError

        def check(client, view_ids):
            if not client:
                return
            views = client.get('canvasViews', {})
            for view_id in view_ids:
                if view_id == 'primary':
                    if not (client.get('canvas', {}).get('open') or views.get('retained')):
                        continue
                    identity = client.get('canvas', {}).get('id')
                else:
                    identity = views.get('inline', {}).get(view_id, {}).get('resourceId')
                if identity and views.get('preferences', {}).get(view_id + ':' + identity, {}).get('dirty'):
                    label = 'primary viewer' if view_id == 'primary' else 'inline preview'
                    raise AppError('Finish or cancel the ' + label + ' edit before leaving it. '
                                   'Use viewer recovery only to discard that edit.', 409, code='canvas_view_dirty')

        # Question state is shared. A close in any client (or by an agent) must
        # not strand another client's question-bound editor after its card moves.
        if action in {'question.answer', 'question.cancel', 'question.supersede'}:
            for client in self.service.clients.records.values():
                check(client, [identity for identity, binding in client.get('canvasViews', {}).get('inline', {}).items()
                               if binding.get('sessionId') == args['sessionId']
                               and binding.get('questionId') == args['id']])
            return
        # Deleting shared history can unmount another client's whole canvas.
        if action == 'session.delete':
            for client in self.service.clients.records.values():
                if client.get('selectedSessionId') == args['id']:
                    check(client, ['primary', *client.get('canvasViews', {}).get('inline', {})])
            return
        client = self.service.clients.record()
        if client is None:
            return
        if (action in {'session.draft', 'session.create', 'session.fork', 'message.edit',
                       'workspace.select', 'workspace.add', 'workspace.create', 'workspace.remove'}
                or action == 'session.select' and args['id'] != client.get('selectedSessionId')):
            check(client, list(client.get('canvasViews', {}).get('inline', {})))
        if action == 'canvas.close':
            check(client, ('primary',))
        elif (action in {'session.draft', 'session.create', 'session.fork', 'message.edit',
                         'workspace.select', 'workspace.add', 'workspace.create', 'workspace.remove'}
              or action == 'session.select' and args['id'] != client.get('selectedSessionId')
              or action == 'canvas.select' and args.get('presentation', 'canvas') == 'canvas' and (args['id'] != client.get('canvas', {}).get('id') or args.get('version') != client.get('canvas', {}).get('selectedVersion'))
              or action == 'canvas.tabClose' and args['id'] == client.get('canvas', {}).get('id')
              or action == 'view.update' and args.get('patch', {}).get('settingsCanvas') is True
              or action in {'canvas.show', 'canvas.openFile', 'canvas.openAttachment', 'canvas.openGif', 'canvas.apps.create', 'smartTools.open'} and args.get('presentation', 'canvas') == 'canvas' and args.get('sessionId', client.get('selectedSessionId')) == client.get('selectedSessionId')):
            check(client, ('primary',))

    def artifact(self, identity):
        row = next((r for r in self.service.state.get('canvasArtifacts', []) if r['id'] == identity), None)
        if row is None:
            fail('The saved artifact is unavailable.', 404)
        return row

    @staticmethod
    def revision(row):
        # View preferences, reports and transient tool context are not content.
        from .canvas_versions import ordinary_preview
        content = {key: row.get(key) for key in ('id', 'kind', 'body', 'url', 'revision', 'selectedVersion')}
        if ordinary_preview(row):
            content['selectedVersion'] = None  # Selection intent is not executable content.
        return hashlib.sha256(encoded({**content, **({'appRevision': row['app']['revision']} if row.get('app') else {})}).encode()).hexdigest()

    def preference(self, view_id, row):
        preferences = self.record()['preferences']
        key = view_id + ':' + row['id']
        if key not in preferences:
            preferences[key] = {'renderer': 'builtin.canvas.' + row['kind'], 'generation': 0,
                                'view': copy.deepcopy(row.get('view', {})), 'dirty': False, 'renderReports': {}}
        return preferences[key]

    def resolve(self, view_id):
        record = self.record()
        current = self.service.state.get('canvas', {})
        if view_id == 'primary':
            if not current.get('kind') or current.get('placeholder'):
                record['primaryBinding'] = None
                fail('The primary view has no selected artifact.', 404)
            identity = current['id']
        else:
            current = record.get('inline', {}).get(view_id)
            if not current:
                fail('Unknown canvas view.', 404)
            identity = current['resourceId']
        row = self.artifact(identity)
        if view_id != 'primary':
            if (row.get('sessionId') != self.service.state.get('selectedSessionId')
                    or row.get('workspaceId') != self.service.state.get('selectedWorkspaceId')):
                fail('This preview belongs to another conversation.', 409)
            if current.get('questionId'):
                self.service.questions.review_artifact(row['sessionId'], current['questionId'],
                    current['questionContentRevision'], identity, current['selectedVersion'])
        from .canvas_versions import view_definition
        row = view_definition(row, current, self.service.db)
        preference = self.preference(view_id, row)
        previous_binding = record.get(view_id + 'Binding')
        visible_binding = (previous_binding[2] if record.get('retained') and previous_binding else bool(current.get('open'))) if view_id == 'primary' else True
        binding = [identity, self.revision(row), visible_binding]
        if record.get(view_id + 'Binding') != binding:
            record[view_id + 'Binding'] = binding
            preference['generation'] += 1
            preference.pop('activation', None)
        return row, preference

    def render_identity(self, view_id, row, preference):
        # Automatic MCP pinning changes authority immediately, while retaining
        # the same immutable document's mounted DOM and unfinished local input.
        # Manual selection reloads canvas state and drops this private marker.
        retained = self.service.state.get('canvas', {}).get('_retainedMcpRender', {}) if view_id == 'primary' else {}
        if (row.get('kind') == 'mcp-app' and row.get('readOnlyVersion')
                and retained.get('version') == row.get('selectedVersion')
                and retained.get('body') == row.get('body')):
            return retained['identity']
        return self.revision(row) + ':' + str(preference['generation'])

    def target(self, args):
        row, preference = self.resolve(args['viewId'])
        if row['id'] != args['resourceId'] or self.revision(row) != args['resourceRevision'] or preference['generation'] != args['generation']:
            fail('This artifact view changed. Read it again before applying the action.', 409)
        return row, preference

    def manifest(self, renderer):
        result = BUILTINS.get(renderer) or self.service.shell.manifest(renderer)
        if result['profile'] != PROFILE:
            fail('Choose a canvas renderer package.')
        return result

    def choices(self, kind):
        result = [{'id': key, 'label': value['label'], 'manifest': value, 'url': None}
                  for key, value in BUILTINS.items() if kind in value['resourceKinds']]
        for digest, raw in self.service.db.execute("SELECT id,value FROM shell_records WHERE kind='package' ORDER BY rowid DESC"):
            package = json.loads(raw)
            manifest = package['manifest']
            if manifest.get('profile') != PROFILE or kind not in manifest.get('resourceKinds', []):
                continue
            try:
                self.manifest(digest)
            except Exception:
                continue
            result.append({'id': digest, 'label': manifest.get('label', manifest['id']), 'manifest': manifest,
                           'url': f'/api/shell/packages/{digest}.mjs'})
            if len(result) >= 50:
                break
        return result

    def summary(self, view_id):
        from .canvas_paths import paths
        from .canvas_reveal import status as reveal_status
        row, preference = self.resolve(view_id)
        choices = self.choices(row['kind'])
        renderer = next((r for r in choices if r['id'] == preference['renderer']), None)
        if renderer is None:
            # Newer packages may fill the bounded menu, but must never evict
            # a still-valid saved choice from a mounted view.
            try:
                manifest = self.manifest(preference['renderer'])
                if row['kind'] in manifest['resourceKinds']:
                    renderer = {'id': preference['renderer'], 'label': manifest.get('label', manifest['id']),
                                'manifest': manifest, 'url': f"/api/shell/packages/{preference['renderer']}.mjs"}
                    choices.append(renderer)
            except Exception:
                pass
        return {'viewId': view_id, 'resourceId': row['id'], 'resourceRevision': self.revision(row),
                'renderIdentity': self.render_identity(view_id, row, preference),
                'generation': preference['generation'], 'renderer': preference['renderer'], 'dirty': preference['dirty'],
                'resource': {k: row[k] for k in ('id', 'title', 'kind', 'sessionId', 'workspaceId', 'path') if k in row},
                'choices': choices, 'available': renderer is not None,
                'filePaths': paths(self.service.state, row),
                'fileReveal': reveal_status(self.service.instance_id) if row.get('path') else None,
                'selectedVersion': row.get('selectedVersion'),
                'runningVersion': row.get('runningVersion'),
                'previewRetained': row.get('previewRetained', False),
                'latestStateRevision': self.artifact(row['id']).get('app', {}).get('stateRevision'),
                'latestVersion': row.get('latestVersion', row.get('app', {}).get('revision', row.get('revision', 1))),
                'versions': [{k: item.get(k) for k in ('version', 'title', 'createdAt')} for item in row.get('app', {}).get('versions', row.get('versions', []))],
                'activation': preference.get('activation'),
                **({'app': copy.deepcopy(row['app'])} if row.get('app') else {}),
                'view': copy.deepcopy(self.service.state['canvas'].get('view', {})) if view_id == 'primary' else copy.deepcopy(preference['view']),
                'renderReports': copy.deepcopy(self.service.state['canvas'].get('renderReports', {})) if view_id == 'primary' else copy.deepcopy(preference['renderReports']),
                **{key: copy.deepcopy(preference[key]) for key in ('document', 'interaction', 'events') if key in preference}}

    def project(self):
        from .service import AppError
        views = []
        try:
            identities = ['primary', *self.record().get('inline', {})]
        except AppError:
            return {'views': []}
        for identity in identities:
            try:
                views.append(self.summary(identity))
            except AppError:
                pass
        presentations = [copy.deepcopy(value) for value in self.record().get('presentations', {}).values()
                         if value['sessionId'] == self.service.state.get('selectedSessionId')]
        return {'views': views, 'presentations': presentations}

    def open_inline(self, args):
        from .canvas_versions import definition
        row = self.artifact(args['resourceId'])
        if (row.get('sessionId') != self.service.state.get('selectedSessionId')
                or row.get('workspaceId') != self.service.state.get('selectedWorkspaceId')):
            fail('Open the conversation that contains this artifact first.', 409)
        saved = definition(row, args['version'], self.service.db)
        source = self.source_binding(row, args['version'], args, legacy_publication=True)
        if saved['kind'] not in INLINE_KINDS:
            fail('This artifact opens in Canvas.', 409)
        record = self.record()
        choice = self.presentation(row['id'], args['version'])
        if args.get('presentationRequest') and (not choice or choice['presentation'] != 'inline'
                                               or not self.same_source(choice, source)):
            fail('This inline presentation changed. Use the current artifact card.', 409)
        inline = record.setdefault('inline', {})
        for identity, binding in list(inline.items()):
            if binding.get('sessionId') != self.service.state.get('selectedSessionId'):
                self.release_inline(identity)
        binding = {'resourceId': row['id'], 'selectedVersion': args['version'],
                   **source, 'sessionId': row['sessionId'], 'previewId': args['previewId']}
        identity = next((key for key, value in inline.items() if value == binding), None)
        if identity is None:
            if 'questionId' in source and any(value.get('resourceId') == row['id']
                    and value.get('selectedVersion') == args['version'] and self.same_source(value, source)
                    for value in inline.values()):
                fail('Close the existing preview of this artifact before opening it at the question.', 409)
            if len(inline) >= 8:
                fail('Close a preview to open another here, or open this artifact in Canvas.', 409)
            identity = 'inline-' + str(uuid.uuid4())
            inline[identity] = binding
        return self.summary(identity)

    def release_inline(self, identity):
        record = self.record()
        binding = record.get('inline', {}).get(identity)
        if binding is None:
            return
        key = identity + ':' + binding['resourceId']
        if record['preferences'].get(key, {}).get('dirty'):
            fail('Finish or cancel this preview edit before closing it.', 409)
        record['preferences'].pop(key, None)
        record.pop(identity + 'Binding', None)
        del record['inline'][identity]

    def canvas(self, view_id):
        from .state_storage import resource
        row, preference = self.resolve(view_id)
        indirect = row.get('contentResource') and row['kind'] in {'html', 'babylon', 'canvas-app', 'pdf'}
        body = {} if indirect else resource(self.service.db, row['body']['$resource'])
        canvas = {**copy.deepcopy(row), **body, 'open': True, 'viewId': view_id,
                  'resourceRevision': self.revision(row), 'generation': preference['generation'],
                  'renderIdentity': self.render_identity(view_id, row, preference)}
        canvas.pop('body', None)
        if view_id == 'primary':
            live = self.service.state['canvas']
            canvas.update({key: copy.deepcopy(live[key]) for key in ('view', 'renderReports', 'document', 'interaction', 'mcp', 'events') if key in live})
        else:
            canvas.update({key: copy.deepcopy(preference[key]) for key in ('view', 'renderReports', 'document', 'interaction', 'events') if key in preference})
        if indirect:
            canvas.pop('content', None)
        else:
            canvas.pop('contentResource', None)
        return canvas

    def command(self, action, args, origin):
        """Called under the normal AppService action lock and receipt handling."""
        if action == 'canvas.views.loadVersion' and origin != 'ui':
            fail('Only an explicit user control can load a saved interactive preview.', 403)
        if action == 'canvas.views.inline':
            return self.open_inline(args), []
        if action == 'canvas.views.observe':
            return self.service.surface_context.observe(args), []
        if action == 'canvas.views.inspect':
            return self.project(), []
        row, preference = self.target(args)
        if action == 'canvas.views.loadVersion':
            from .canvas_versions import definition, ordinary_preview
            from .canvas_library import load, scope
            from .service import AppError
            if args['viewId'] != 'primary' or not ordinary_preview(row) or not scope(self.service.state, row):
                fail('Load a saved version from the primary HTML or 3D preview.', 409)
            if preference['dirty']:
                raise AppError('Finish or cancel this preview edit before loading a saved version.',
                               409, code='canvas_view_dirty')
            saved = definition(self.artifact(row['id']), args['version'], self.service.db)
            if not ordinary_preview(saved):
                fail('This saved version is not an ordinary HTML or 3D preview.', 409)
            if args['version'] != row['runningVersion']:
                # Exact target and saved definition are validated before any
                # presentation/selection mutation. The UI Load is the reset.
                self.choose(saved, args['version'], 'canvas', reset=True, origin=origin)
                current = self.service.state['canvas']
                follow = args.get('followLatest', current.get('selectedVersion') is None)
                selected = None if follow else args['version']
                view = copy.deepcopy(current.get('view', {}))
                load(self.service.state, self.service.db, row['id'],
                     open_panel=current.get('open', False), version=args['version'])
                self.service.state['canvas'].update(selectedVersion=selected,
                    readOnlyVersion=selected is not None, view=view,
                    previewRetained=selected is None and args['version'] < saved['latestVersion'])
                for key in ('document', 'interaction', 'events'):
                    preference.pop(key, None)
            elif 'followLatest' in args:
                self.service.state['canvas'].update(selectedVersion=None if args['followLatest'] else args['version'],
                    readOnlyVersion=not args['followLatest'],
                    previewRetained=args['followLatest'] and args['version'] < saved['latestVersion'])
            return self.summary('primary'), []
        if action == 'canvas.views.imageDraft':
            from .attachments import save, MAX_FILES
            from .canvas_downloads import filename
            canvas = self.canvas(args['viewId'])
            if canvas['kind'] != 'image' or row.get('sessionId') != self.service.state.get('selectedSessionId'):
                fail('Choose an image in this conversation first.', 409)
            draft = self.service.clients.attachments(self.service._session(row['sessionId']))
            if len(draft) >= MAX_FILES:
                fail('Attach up to 8 files per message.', 409)
            attachment = save(self.service.data_dir, filename(canvas), canvas['content'].split(';base64,', 1)[1])
            draft.append(attachment)
            return {'status': 'attached', 'attachmentId': attachment['id'], 'sent': False}, []
        if action == 'canvas.views.release':
            if args['viewId'] == 'primary':
                fail('Use the Canvas close control for the primary viewer.')
            self.release_inline(args['viewId'])
            return {'status': 'closed'}, []
        if action == 'canvas.views.renderer' and preference['dirty']:
            return {'status': 'deferred', 'reason': 'Finish or cancel this renderer edit first.'}, []
        if action in {'canvas.views.renderer', 'canvas.views.recover'}:
            renderer = args.get('renderer', 'builtin.canvas.' + row['kind'])
            manifest = self.manifest(renderer)
            if row['kind'] not in manifest['resourceKinds']:
                fail('This renderer does not support the artifact format.')
            if action != 'canvas.views.recover':
                try:
                    old = self.manifest(preference['renderer'])
                except Exception:
                    old = None
                if old and old['stateSchema'] != manifest['stateSchema']:
                    return {'status': 'deferred', 'reason': 'Renderer state migration is not supported.'}, []
            preference.update(renderer=renderer, generation=preference['generation'] + 1, dirty=False, renderReports={})
            preference.pop('activation', None)
        elif action == 'canvas.views.dirty':
            preference['dirty'] = args['dirty']
            if 'editVersion' in args:
                preference['editVersion'] = args['editVersion']
        elif action == 'canvas.views.status':
            preference['activation'] = {'status': args['status'], 'message': args['message']}
        elif action == 'canvas.views.command':
            return self.control(row, preference, args, origin)
        return {'status': 'updated'}, []

    def control(self, row, preference, target, origin):
        from .workspace_canvas import canvas_command
        from .service import ACTION_DEFINITIONS
        from jsonschema import validate, ValidationError
        action, args = target['action'], copy.deepcopy(target['args'])
        allowed = {'canvas.view', 'canvas.report', 'canvas.snapshot', 'canvas.interact', 'canvas.event', 'canvas.copy', 'canvas.copyPath', 'canvas.download', 'canvas.openExternal', 'canvas.reference'}
        if action not in allowed:
            fail('This operation is not a renderer capability.', 403)
        renderer = preference['renderer']
        try:
            manifest = self.manifest(renderer)
        except Exception:
            renderer = 'builtin.canvas.' + row['kind']
            manifest = BUILTINS[renderer]
        if preference.get('activation', {}).get('status') == 'error':
            renderer = 'builtin.canvas.' + row['kind']
            manifest = BUILTINS[renderer]
        if renderer not in BUILTINS:
            required = {'canvas.view': 'canvas.view.update', 'canvas.report': 'canvas.view.report', 'canvas.copyPath': 'canvas.resource.read'}.get(action)
            if not required or required not in manifest['capabilities']:
                fail('The renderer has not declared this capability.', 403)
        if action != 'canvas.event':
            if args.get('id', row['id']) != row['id']:
                fail('A renderer cannot retarget its artifact.')
            args['id'] = row['id']
        canvas = self.canvas(target['viewId'])
        try:
            if action == 'canvas.view' and renderer not in BUILTINS:
                patch = args.get('patch')
                if not isinstance(patch, dict) or len(encoded({**canvas.get('view', {}), **patch}).encode()) > 16000:
                    fail('Accumulated renderer view state must be an object of at most 16 KB.')
            else:
                validate(args, ACTION_DEFINITIONS[action][1])
        except ValidationError as exc:
            fail(exc.message)
        effects = []
        if action == 'canvas.reference':
            from .canvas_reference import command
            version = row.get('selectedVersion') or row.get('revision', 1)
            if args['version'] != version:
                fail('The selected document version changed. Select the text again.', 409)
            return command(self.service, args), []
        if action == 'canvas.copyPath':
            from .canvas_paths import copy_path
            return copy_path({**self.service.state, 'canvas': canvas}, args)
        if action in {'canvas.copy', 'canvas.download'}:
            if canvas['kind'] == 'gif':
                fail('Use Download original on the retained GIF.', 409)
            from .canvas_downloads import filename
            if action == 'canvas.copy' and canvas['kind'] == 'pdf':
                fail('Select text in the PDF to copy it, or download the original file.')
            content = canvas.get('url') if canvas['kind'] == 'browser' else canvas.get('content', json.dumps(canvas.get('surface', {}), indent=2))
            if canvas.get('contentResource') or (action == 'canvas.download' and canvas['kind'] in {'babylon', 'image'}):
                effects.append({'type': 'clipboard.url' if action == 'canvas.copy' else 'download.url',
                                'url': '/api/canvas/' + row['id'] + ('/source' if action == 'canvas.copy' else '/download') + '?version=' + str(row.get('selectedVersion') or row.get('app', {}).get('revision', row.get('revision', 1))),
                                'canvasId': row['id'], 'filename': filename(canvas)})
            else:
                effects.append({'type': 'clipboard.write' if action == 'canvas.copy' else 'download',
                                'content': content, 'filename': filename(canvas),
                                'mime': 'text/plain', 'canvasId': row['id']})
        elif action == 'canvas.openExternal':
            if canvas['kind'] != 'browser':
                fail('Select a website artifact first.')
            effects.append({'type': 'browser.open', 'url': canvas['url']})
        else:
            scoped = {**self.service.state, 'canvas': canvas}
            canvas_command(scoped, action, args, origin)
            for key in ('view', 'renderReports', 'document', 'interaction', 'events'):
                if key in canvas:
                    preference[key] = copy.deepcopy(canvas[key])
                    if target['viewId'] == 'primary':
                        self.service.state['canvas'][key] = copy.deepcopy(canvas[key])
        return {'status': 'updated'}, effects
