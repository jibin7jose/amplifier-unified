"""Versioned native UI packages and client-scoped shell compositions.

Native modules are trusted app code, not a security sandbox. Validation is a
host-run compatibility check against the exact content-addressed artifact.
Conversation data and runtime ownership remain with AppService/Foundation.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import uuid

from jsonschema import validate, ValidationError
from . import shell_components as components

API = '1.0'
PROFILE = 'trusted-native-navigation-v1'
IDENTITY = {'type': 'string', 'pattern': r'^[a-zA-Z0-9][a-zA-Z0-9._-]{0,99}$'}
CAPABILITIES = ['navigation.read', 'navigation.select', 'workspaces.manage', 'chats.manage', 'locations.read', 'history.refresh']
MANIFEST = {'type': 'object', 'additionalProperties': False, 'required': ['id', 'version', 'apiVersion', 'profile', 'stateSchema', 'capabilities'], 'properties': {
    'id': IDENTITY, 'version': {'type': 'string', 'pattern': r'^\d+\.\d+\.\d+$'},
    'apiVersion': {'const': API}, 'profile': {'const': PROFILE},
    'stateSchema': IDENTITY, 'capabilities': {'type': 'array', 'uniqueItems': True, 'maxItems': 6, 'contains': {'const': 'navigation.read'}, 'items': {'enum': CAPABILITIES}},
}}
RENDERER_MANIFEST = copy.deepcopy(MANIFEST)
RENDERER_MANIFEST['required'].append('resourceKinds')
RENDERER_MANIFEST['properties'].update({
    'profile': {'const': 'trusted-native-renderer-v1'},
    'label': {'type': 'string', 'minLength': 1, 'maxLength': 100},
    'capabilities': {'type': 'array', 'uniqueItems': True, 'maxItems': 3,
        'contains': {'const': 'canvas.resource.read'},
        'items': {'enum': ['canvas.resource.read', 'canvas.view.update', 'canvas.view.report']}},
    'resourceKinds': {'type': 'array', 'uniqueItems': True, 'minItems': 1, 'maxItems': 12,
        'items': {'enum': ['markdown', 'text', 'code', 'json', 'jsonl', 'image', 'html', 'babylon', 'mermaid', 'dot', 'a2ui', 'browser']}},
})
COMPONENT_MANIFEST = copy.deepcopy(MANIFEST)
COMPONENT_MANIFEST['required'].extend(['label', 'slots'])
COMPONENT_MANIFEST['properties'].update({
    'profile': {'const': components.PROFILE},
    'label': {'type': 'string', 'minLength': 1, 'maxLength': 100},
    'slots': {'type': 'array', 'minItems': 1, 'maxItems': len(components.SLOTS), 'uniqueItems': True,
              'items': {'enum': list(components.SLOTS)}},
    'capabilities': {'type': 'array', 'uniqueItems': True, 'maxItems': len(components.CAPABILITIES),
                     'contains': {'const': 'shell.read'}, 'items': {'enum': components.CAPABILITIES}},
})
MANIFEST = {'allOf': [
    {'type': 'object', 'properties': {'profile': {'enum': [PROFILE, 'trusted-native-renderer-v1', components.PROFILE]}}, 'required': ['profile']},
    {'if': {'properties': {'profile': {'const': PROFILE}}}, 'then': MANIFEST,
     'else': {'if': {'properties': {'profile': {'const': 'trusted-native-renderer-v1'}}}, 'then': RENDERER_MANIFEST, 'else': COMPONENT_MANIFEST}},
]}
INSTANCE = {'type': 'object', 'additionalProperties': False, 'required': ['id', 'package', 'slot'], 'properties': {
    'id': IDENTITY, 'package': {'type': 'string', 'maxLength': 100}, 'slot': {'enum': ['navigation', *components.SLOTS]},
    'scope': {'type': 'object', 'additionalProperties': False, 'properties': {'workspaceId': IDENTITY, 'mode': {'enum': ['follow', 'pinned', 'all']}}, 'required': ['mode']},
    'hideWhen': {'type': 'object', 'additionalProperties': False, 'required': ['instanceId', 'navChatScope'], 'properties': {'instanceId': IDENTITY, 'navChatScope': {'enum': ['all', 'workspace']}}},
}}
COMPOSITION = {'type': 'object', 'additionalProperties': False, 'required': ['instances', 'presentation'], 'properties': {
    'instances': {'type': 'array', 'maxItems': 64, 'items': INSTANCE},
    'disabledSlots': {'type': 'array', 'uniqueItems': True, 'items': {'enum': list(components.SLOTS)}},
    'presentation': {'type': 'object', 'additionalProperties': False, 'properties': {
        'scheme': {'enum': ['light', 'dark', 'system']}, 'layout': {'enum': ['balanced', 'conversation', 'work']},
        'executionDetail': {'enum': ['minimal', 'standard', 'detailed']},
        'executionVisibility': {'type': 'object', 'additionalProperties': False, 'properties': {
            scope: {'type': 'boolean'} for scope in ('step', 'turn', 'conversation')}},
        'executionMetrics': {'type': 'array', 'uniqueItems': True, 'maxItems': 10, 'items': {'enum': [
            'elapsedSeconds', 'grossInputTokens', 'outputTokens', 'grossTotalTokens',
            'cacheReadTokens', 'cacheWriteTokens', 'reasoningTokens', 'voiceSeconds', 'costUsd', 'calls']}},
        'conversationStatus': {'type': 'boolean'},
        'interfaceDetail': {'enum': ['minimal', 'standard', 'detailed']},
        'workStartView': {'enum': ['appearance', 'focus', 'brief', 'studio']},
        'collapseInterimReplies': {'type': 'boolean'},
        'planPlacement': {'enum': ['composer', 'inline', 'active', 'overview']},
        'decorations': {'type': 'boolean'},
        'density': {'enum': ['comfortable', 'compact']}, 'accent': {'type': 'string', 'pattern': '^#[0-9a-fA-F]{6}$'},
    }},
}}
DEFAULT = {'instances': [
    {'id': 'workspaces', 'package': 'builtin.workspaces', 'slot': 'navigation', 'hideWhen': {'instanceId': 'chats', 'navChatScope': 'all'}},
    {'id': 'chats', 'package': 'builtin.chats', 'slot': 'navigation'},
], 'presentation': {}}
BUILTINS = {name: {'id': name, 'version': '1.0.0', 'apiVersion': API, 'profile': PROFILE, 'stateSchema': 'navigation-v1', 'capabilities': CAPABILITIES}
            for name in ['builtin.workspaces', 'builtin.chats']}
BUILTINS.update(components.BUILTINS)
VIEW_KEYS = {'navSimple', 'navSectionsCollapsed', 'navRecentView', 'navShowAgentCreated', 'navRecentLimit', 'navPinnedPage', 'navSort', 'navLocationFilter', 'navWorkspaceList', 'navStatusFilter', 'navArchive', 'navCollection', 'navWorkspaceMode', 'navWorkspaceArchive', 'navFilter', 'navChatScope', 'navChatPage', 'navWorkspacePath', 'navWorkspaceFilter', 'navWorkspacePage', 'navWorkspaceAncestorsOpen', 'workspaceDraft', 'locationPicker'}
EDIT_STATE = {'type': 'object', 'additionalProperties': False, 'properties': {
    'mode': {'enum': ['add', 'rename', 'remove', 'chat-rename', 'chat-delete']}, 'id': {'type': 'string', 'maxLength': 200},
    'path': {'type': 'string', 'maxLength': 4000}, 'name': {'type': 'string', 'maxLength': 200},
}}
COMMAND_CAPABILITIES = {
    'session.select': 'navigation.select', 'workspace.select': 'navigation.select',
    'workspace.prepare': 'workspaces.manage', 'workspace.add': 'workspaces.manage', 'workspace.list': 'navigation.read',
    'workspace.create': 'workspaces.manage', 'workspace.rename': 'workspaces.manage', 'workspace.archive': 'workspaces.manage', 'workspace.restore': 'workspaces.manage', 'workspace.remove': 'workspaces.manage',
    'workspace.pin': 'workspaces.manage', 'workspace.pinOrder': 'workspaces.manage',
    'navigation.pinOrder': 'chats.manage',
    'session.draft': 'chats.manage', 'session.create': 'chats.manage', 'session.rename': 'chats.manage', 'session.naming': 'chats.manage', 'session.naming.backfill': 'chats.manage', 'session.delete': 'chats.manage', 'session.deletePreview': 'chats.manage', 'session.pin': 'chats.manage',
    'session.markUnread': 'chats.manage', 'session.archive': 'chats.manage', 'session.restore': 'chats.manage', 'session.pinOrder': 'chats.manage',
    'attention.read': 'chats.manage',
    'locations.create': 'workspaces.manage', 'locations.list': 'locations.read', 'history.refresh': 'history.refresh',
}


def definitions(schema, string):
    client = {'clientId': IDENTITY}
    generation = {'generation': {'type': 'integer', 'minimum': 0}}
    change = {**client, 'changeId': IDENTITY, 'expectedRevision': {'type': 'integer', 'minimum': 0}}
    return {
        'shell.inspect': ('Inspect client composition, package manifests, validation and browser activation evidence.', schema(client)),
        'shell.query': ('Read one module instance\'s bounded snapshot, using its declared data capabilities.', schema({**client, 'instanceId': IDENTITY})),
        'shell.packages.stage': ('Stage a trusted native navigation, component or artifact-renderer package; this does not execute or activate it.', schema({'manifest': MANIFEST, 'source': string(250000)})),
        'shell.packages.validate': ('Run host-owned import and browser lifecycle checks of the staged digest. Requires the local validator toolchain.', schema({'digest': string(64)})),
        'shell.changes.prepare': ('Validate a proposed client composition and return a reviewable change; does not activate it.', schema({**client, 'composition': COMPOSITION, 'expectedRevision': {'type': 'integer', 'minimum': 0}})),
        'shell.changes.preview': ('Preview a prepared composition in the target client. Browser activation is separately reported.', schema(change)),
        'shell.changes.apply': ('Apply a prepared composition at its expected revision; preserve compatible instances.', schema(change)),
        'shell.changes.revert': ('Restore the composition saved before this change, checking the current revision.', schema(change)),
        'shell.recover': ('Restore the default or last successfully rendered composition for this client.', schema({**client, 'target': {'enum': ['default', 'lastGood']}, 'expectedRevision': {'type': 'integer', 'minimum': 0}})),
        'shell.view.update': ('Update only this module instance\'s durable UI state, without changing conversation selection.', schema({**client, **generation, 'instanceId': IDENTITY, 'patch': {'type': 'object', 'maxProperties': 12}, 'dirty': {'type': 'boolean'}}, ['clientId', 'instanceId', 'patch'])),
        'shell.command': ('Execute a declared module capability through the normal app command handler.', schema({**client, **generation, 'instanceId': IDENTITY, 'action': string(100), 'args': {'type': 'object'}}, ['clientId', 'instanceId', 'action', 'args'])),
        'shell.report': ('Report rendered module status from a browser; display evidence, never package validation.', schema({**client, 'revision': {'type': 'integer'}, 'previewId': {'type': ['string', 'null'], 'maxLength': 100}, 'instances': {'type': 'object', 'maxProperties': 64, 'additionalProperties': {'enum': ['ready', 'error', 'loading', 'inactive']}}, 'message': string(2000)}, ['clientId', 'revision', 'instances'])),
    }


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def fail(message, status=400):
    from .errors import AppError
    raise AppError(message, status)


class ShellModules:
    def __init__(self, service):
        self.service = service
        self.db = service.db
        self.directory = service.data_dir / 'shell-packages'
        self.directory.mkdir(exist_ok=True, mode=0o700)
        self.db.execute('CREATE TABLE IF NOT EXISTS shell_records (kind TEXT, id TEXT, value TEXT NOT NULL, PRIMARY KEY(kind,id))')
        self.db.commit()
        self.validations = {}
        self.change_tokens = {}
        # Expanded prefixes belong to the attached document/module session,
        # never the durable records copied to a reload or another client.
        self.recent_limits = {}
        from .shell_resources import ShellResources
        self.resources = ShellResources(self)

    def get(self, kind, identity, default=None):
        row = self.db.execute('SELECT value FROM shell_records WHERE kind=? AND id=?', (kind, identity)).fetchone()
        return json.loads(row[0]) if row else copy.deepcopy(default)

    def put(self, kind, identity, value):
        self.db.execute('INSERT OR REPLACE INTO shell_records VALUES (?,?,?)', (kind, identity, encoded(value)))
        self.db.commit()

    def client(self, identity):
        saved = self.get('client', identity)
        if saved is not None:
            return saved
        # Migrate legacy navigation defaults once. Subsequent commands in
        # another client must not overwrite this client's filters or pages.
        baseline = {key: copy.deepcopy(value) for key, value in self.service.state['view'].items() if key in VIEW_KEYS | {'navWorkspaceBrowseFor'}}
        composition = copy.deepcopy(DEFAULT)
        appearance = self.get('preferences', 'appearance')
        if appearance is None:
            # One-time adoption of the most recently used committed appearance
            # from versions which only saved it per browser document.
            row = self.db.execute("SELECT value FROM shell_records WHERE kind='client' AND json_extract(value, '$.composition.presentation') != '{}' ORDER BY rowid DESC LIMIT 1").fetchone()
            appearance = json.loads(row[0])['composition']['presentation'] if row else {}
            if appearance:
                self.put('preferences', 'appearance', appearance)
        # Experimental transcript folding is a view choice, never inherited by
        # an unrelated browser from the shared look-and-feel defaults.
        composition['presentation'] = {key: value for key, value in appearance.items()
                                       if key != 'collapseInterimReplies'}
        saved = {'revision': 0, 'composition': composition, 'baselineView': baseline, 'views': {}, 'preview': None, 'lastGood': copy.deepcopy(composition), 'reported': None}
        self.put('client', identity, saved)
        return saved

    def notify(self, identity):
        # Ordinary snapshots retain this token if a full queue drops the shell
        # event. A host epoch also repairs reconnects after a restart.
        self.change_tokens[identity] = self.change_tokens.get(identity, 0) + 1
        self.service._client_snapshots.pop(identity, None)
        observed = None
        for queue in self.service.queues:
            if self.service.queue_sessions.get(queue) is not None:
                continue
            if self.service.queue_clients.get(queue) not in {None, identity}:
                continue
            if queue.full():
                queue.get_nowait()
            if self.resources.registered(identity) and self.service.queue_clients.get(queue) == identity:
                # A complete client snapshot survives bounded-queue coalescing.
                # The existing transport sends only changed resource leaves;
                # a dropped intermediate observation cannot lose a page update.
                if observed is None:
                    with self.service.clients.bind(identity):
                        observed = self.service.browser_state()
                queue.put_nowait(observed)
            else:
                queue.put_nowait({'shellClientId': identity})

    def change_token(self, identity):
        return f'{self.service.instance_id}:{self.change_tokens.get(identity, 0)}'

    def manifest(self, package, *, validated=True):
        if package in BUILTINS:
            return BUILTINS[package]
        record = self.get('package', package)
        if not record:
            fail('Unknown shell package.', 404)
        self.source(package)
        receipt = record.get('validation') or {}
        if validated and (receipt.get('status') != 'passed' or receipt.get('hostFingerprint') != self.host_fingerprint()):
            fail('Package requires current host validation.', 409)
        return record['manifest']

    def source(self, digest):
        if not re.fullmatch('[0-9a-f]{64}', digest):
            fail('Invalid package digest.')
        record = self.get('package', digest)
        path = self.directory / (digest + '.mjs')
        if not record or not path.is_file():
            fail('Shell package is missing.', 404)
        source = path.read_text()
        actual = hashlib.sha256((encoded(record['manifest']) + '\n' + source).encode()).hexdigest()
        if actual != digest:
            fail('Package contents changed after staging; stage and validate a new artifact.', 409)
        return source

    def host_fingerprint(self):
        # Receipts expire when the actual harness/runtime or validator changes.
        paths = [Path(__file__), Path(components.__file__), Path(__file__).with_name('canvas_views.py'), Path(__file__).with_name('shell_validator.mjs'), *sorted((Path(__file__).parent / 'static').rglob('*.js')), Path(__file__).parent / 'static/shell-validation.html']
        signature = [(str(path), path.stat().st_mtime_ns, path.stat().st_size) for path in paths if path.exists()]
        if getattr(self, '_host_signature', None) != signature:
            digest = hashlib.sha256((API + PROFILE).encode())
            for path in paths:
                if path.exists():
                    digest.update(path.read_bytes())
            self._host_signature, self._host_digest = signature, digest.hexdigest()
        return self._host_digest

    def instance(self, client, identity):
        composition = client['preview']['composition'] if client['preview'] else client['composition']
        instance = next((item for item in components.resolved(composition) if item['id'] == identity), None)
        if instance is None:
            fail('Module instance is not in this composition.', 404)
        return instance

    def check_composition(self, composition):
        instances = components.resolved(composition)
        ids = [item['id'] for item in instances]
        if len(set(ids)) != len(ids):
            fail('Module instance IDs must be unique, including inherited built-ins.')
        slots = {'navigation': {'maximum': 12}, **components.SLOTS}
        for name, slot in slots.items():
            rows = [item for item in instances if item['slot'] == name]
            if len(rows) > slot['maximum']:
                fail('Too many components in ' + name + '.')
            if rows and name in composition.get('disabledSlots', []):
                fail('A disabled slot cannot also contain components.')
        for item in instances:
            manifest = self.manifest(item['package'])
            if item['slot'] == 'navigation':
                if manifest['profile'] != PROFILE:
                    fail('The navigation slot requires a navigation package.')
                scope = item.get('scope', {})
                if scope.get('mode') == 'pinned' and not scope.get('workspaceId'):
                    fail('Pinned modules require a workspaceId.')
            elif manifest['profile'] != components.PROFILE or item['slot'] not in manifest['slots']:
                fail('This package does not support the requested component slot.')
            elif 'scope' in item or 'hideWhen' in item:
                fail('Navigation bindings are only supported in navigation slots.')

    def transition(self, client, composition):
        """Return a deferral, never discard a live form or incompatible state."""
        self.check_composition(composition)
        previous = client['preview']['composition'] if client['preview'] else client['composition']
        by_id = {item['id']: item for item in components.resolved(composition)}
        for new in by_id.values():
            saved = client['views'].get(new['id'], {})
            if saved.get('stateSchema') and saved['stateSchema'] != self.manifest(new['package'])['stateSchema']:
                return {'status': 'deferred', 'instanceId': new['id'], 'reason': 'State schema migration is not supported; use a new instance ID to keep the saved state intact.'}
        for old in components.resolved(previous):
            new = by_id.get(old['id'])
            view = client['views'].get(old['id'], {})
            changed = new != old
            if changed and (view.get('dirty') or view.get('view', {}).get('workspaceDraft', {}).get('mode')):
                return {'status': 'deferred', 'instanceId': old['id'], 'reason': 'Finish or cancel this module\'s edit first.'}
            if new and old['package'] != new['package']:
                before = self.manifest(old['package'], validated=False)
                after = self.manifest(new['package'])
                if before['stateSchema'] != after['stateSchema']:
                    return {'status': 'deferred', 'instanceId': old['id'], 'reason': 'State schema migration is not supported; the current module remains active.'}
        return None

    def inspect(self, identity, *, snapshots=False, recovery=False, navigation_resources=None, bundled=False):
        client = self.client(identity)
        composition = DEFAULT if recovery else client['preview']['composition'] if client['preview'] else client['composition']
        result = {'clientId': identity, 'apiVersion': API, **client, 'effectiveComposition': composition, 'resolvedInstances': components.resolved(composition),
                  'slots': {'navigation': {'label': 'Navigation', 'maximum': 12, 'defaults': ['builtin.workspaces', 'builtin.chats']}, **copy.deepcopy(components.SLOTS)},
                  'componentCommands': components.commands(), 'packages': {}}
        for instance in components.resolved(composition):
            package = instance['package']
            try:
                result['packages'][package] = {'manifest': self.manifest(package), 'url': None if package in BUILTINS else f'/api/shell/packages/{package}.mjs'}
            except Exception as exc:
                result['packages'][package] = {'error': str(exc)}
        if bundled:
            # Built-in controls consume the browser state stream. Descriptors
            # and activation generations are independent of their live data.
            result['instanceGenerations'] = {item['id']: client['views'].get(item['id'], {}).get('generation', 0)
                                             for item in components.resolved(composition)}
            result['hostInstanceId'] = self.service.instance_id
            result['shellBootstrapKey'] = self.resources.bootstrap_key(identity)
            result['shellResources'] = self.resources.snapshot(identity)
        elif snapshots:
            result['snapshots'] = {}
            for item in components.resolved(composition):
                if not result['packages'][item['package']].get('error'):
                    result['snapshots'][item['id']] = self.snapshot(client, item, identity=identity, navigation_resources=navigation_resources)
        else:
            result['registry'] = {key: value for key, value in BUILTINS.items()}
            result['staged'] = [json.loads(row[0]) for row in self.db.execute("SELECT value FROM shell_records WHERE kind='package' ORDER BY rowid DESC LIMIT 100")]
        return result

    def snapshot(self, client, instance, *, identity=None, navigation_resources=None):
        value = self.navigation(client, instance, identity=identity, navigation_resources=navigation_resources) if instance['slot'] == 'navigation' else components.snapshot(self, client, instance)
        return {**value, 'generation': client['views'].get(instance['id'], {}).get('generation', 0)}

    def recent_key(self, client, instance, identity=None):
        return (identity if identity is not None else self.service.clients.current.get(),
                instance['id'], instance['package'],
                client['views'].get(instance['id'], {}).get('generation', 0),
                encoded(instance.get('scope', {})))

    def prune_recent_limits(self, identity, client, composition):
        # A committed effective transition retires module ownership, not clients.
        # Resume copies and SSE disconnects do not prove a document has died.
        live = {self.recent_key(client, instance, identity)
                for instance in components.resolved(composition) if instance['slot'] == 'navigation'}
        for key in list(self.recent_limits):
            if key[0] == identity and key not in live:
                del self.recent_limits[key]

    def scoped_state(self, client, instance):
        state = self.service.state
        view = dict(client.get('baselineView', {}))
        view.update(client['views'].get(instance['id'], {}).get('view', {}))
        from .adaptive_work import WORK_QUERY_FIELDS
        view.update({key: state.get('view', {}).get(key) for key in WORK_QUERY_FIELDS})
        view['navStatusPages'] = state.get('view', {}).get('navStatusPages', {})
        view.setdefault('navWorkspaceMode', 'recent')
        scope = instance.get('scope', {})
        workspace_id = scope.get('workspaceId') if scope.get('mode') == 'pinned' else state.get('selectedWorkspaceId')
        if scope.get('mode') != 'pinned' and state.get('view', {}).get('workSurface') == 'workspace':
            workspace_id = state['view'].get('workWorkspaceId')
        if scope.get('mode') != 'pinned' and state.get('view', {}).get('workSurface') == 'chats':
            view['navChatScope'] = 'all'
        from .managed_chats import is_managed
        selected = next((s for s in state.get('sessions', []) if s['id'] == state.get('selectedSessionId')), {})
        if scope.get('mode') == 'all' or scope.get('mode') != 'pinned' and is_managed(selected) and view.get('navChatScope', 'workspace') == 'workspace' and not view.get('navWorkspaceList'):
            view['navChatScope'] = 'all'
        return {**state, 'selectedWorkspaceId': workspace_id, 'view': view}

    def navigation(self, client, instance, *, identity=None, navigation_resources=None, context_visible_ids=None,
                   _page_only=False):
        from .shell_navigation_demand import resources_for
        requested = resources_for(instance, navigation_resources)
        wants = lambda name: requested is None or name in requested
        from .conversation_library import projection as organization_projection
        from .chat_navigation import SIDEBAR_FILTER_KEYS
        from .workspace_navigation import workspace_pins
        from .pinned_navigation import ordered_ids
        state = self.service.state
        projections = self.service.projections
        scoped = self.scoped_state(client, instance)
        if not _page_only:
            scoped = {**scoped, 'attention': projections.attention(state, session_ids=())}
        view, workspace_id = scoped['view'], scoped['selectedWorkspaceId']
        from .chat_navigation import RECENT_LIMITS
        # Preserve existing initial preferences, but never migrate an expanded
        # prefix into a mass initial snapshot.
        saved_limit = view.get('navRecentLimit', 20)
        saved_limit = saved_limit if type(saved_limit) is int and saved_limit in RECENT_LIMITS else 20
        view['navRecentLimit'] = self.recent_limits.get(self.recent_key(client, instance, identity), saved_limit)
        chat_page = projections.chats(scoped) if wants('chatNavigation') else None
        workspace = next((row for row in state.get('workspaces', []) if row['id'] == workspace_id and row.get('available') is True), None)
        from .workspace_placement import listing
        home_view = {**view, 'navChatScope': 'all', 'navArchive': 'active', 'navCollection': None,
                     'navFilter': '', 'navStatusFilter': 'all', 'navLocationFilter': 'all', 'navSort': 'activity'}
        home_view.pop('navChatPage', None)
        pinned_scope = instance.get('scope', {}).get('mode') == 'pinned'
        home = None
        if wants('homeNavigation'):
            home = (chat_page if chat_page is not None else projections.chats(scoped)) if pinned_scope else projections.chats({**scoped, 'view': home_view})
        # These are views over the native history index, never another chat list.
        # Pins remain independent of either chat list's filters and pagination.
        sidebar_home = {**home_view, 'navChatScope': 'workspace' if pinned_scope else 'all'}
        # Controller identity must exist before the asynchronous page arrives.
        # Use the same available/path-qualified registration as chat queries;
        # an unavailable or unresolved workspace is explicitly null.
        recent_workspace = next((row for row in state.get('workspaces', [])
            if row['id'] == workspace_id and row.get('available') is True
            and isinstance(row.get('path'), str) and row['path']), None)
        recent_scope = {'mode': sidebar_home['navChatScope'],
            'workspaceId': recent_workspace['id'] if pinned_scope and recent_workspace else None,
            'filter': '', 'clientId': identity if identity is not None else self.service.clients.current.get(),
            'instanceId': instance['id'],
            'generation': client['views'].get(instance['id'], {}).get('generation', 0)}
        recent_view = {**sidebar_home, **view.get('navRecentView', {})}
        pins = projections.chats({**scoped, 'view': sidebar_home}, section='pinned') if wants('sidebarPinned') else None
        recent = projections.chats({**scoped, 'view': recent_view}, section='recent') if wants('sidebarRecent') else None
        search = projections.chats({**scoped, 'view': recent_view}, section='search') if wants('sidebarSearch') else None
        workspace_chats = projections.chats({**scoped, 'view': {**view, 'navChatScope': 'workspace'}}, section='workspace') if wants('sidebarWorkspace') else None
        recent_navigation = projections.chats({**scoped, 'view': sidebar_home}, section='shortcuts') if wants('recentNavigation') else None
        from .adaptive_work import work_home, work_home_context, work_status, STATUS_RESOURCES
        work = work_home(self.service, scoped, pinned_workspace_id=workspace_id if pinned_scope else None) if wants('workHome') and instance['package'] in BUILTINS else None
        # Bundled Status pages are explicit demand, not additions to third-party
        # full snapshots. Identical Home queries reuse the projection cache.
        status_pages = {name: work_status(self.service, scoped, status,
                                         pinned_workspace_id=workspace_id if pinned_scope else None)
                        for name, status in STATUS_RESOURCES.items()
                        if requested is not None and wants(name)}
        if recent_navigation is not None:
            # Identity is not part of the shared projection cache. Copy its scope,
            # so one client's fence cannot contaminate another's cached rows.
            recent_navigation = {**recent_navigation, 'scope': {**recent_navigation['scope'], **recent_scope}}
            recent_shortcuts = recent_navigation['items']
        pinned_navigation = projections.pinned_navigation({**scoped, 'view': sidebar_home}) if wants('pinnedNavigation') else None
        visible_ids = {row['id'] for page in (chat_page, home, pins, recent, search, workspace_chats, work)
                       if page is not None for row in page['items']} | {row['id'] for row in (recent_navigation['items'] if recent_navigation is not None else [])}
        visible_ids.update(item['row']['id'] for item in (pinned_navigation['items'] if pinned_navigation is not None else []) if item['kind'] == 'session')
        visible_ids.update(row['id'] for page in status_pages.values() for row in page['items'])
        # A context-only refresh observes the rows in retained active pages,
        # without rebuilding those pages just to recover their identities.
        if context_visible_ids is not None:
            visible_ids.update(context_visible_ids)
        # Exact review receipts for these bounded rows; never error bodies or
        # unrelated settings/permission notices from the full attention inbox.
        read_receipts = projections.attention_receipts(state, visible_ids) if not _page_only else []
        activity_by_id = {}
        if not _page_only and visible_ids:
            from .attention import reviewed_session_errors
            from .navigation_summary import activity, task_blocked
            from .navigation_waiting import task_for
            admitted = projections.sessions(state).by_id
            attention = scoped['attention']
            reviewed = reviewed_session_errors(attention)
            # Pending catalog pages retain membership/order, but their admitted
            # owners' current status is already available without another query
            # or any transcript/configuration hydration.
            for sid in sorted(visible_ids):
                row = admitted.get(sid)
                if row is not None:
                    activity_by_id[sid] = activity(row, bool(attention.get('sessions', {}).get(sid)),
                        error_reviewed=sid in reviewed, blocked=task_blocked(state, sid),
                        task=task_for(state, row), db=self.service.db)
        overview = ({'items': [copy.deepcopy(workspace)] if workspace else [], 'nextOffset': None} if pinned_scope else listing(self.service, {'query': '', 'offset': 0})) if wants('workspaceOverview') else None
        shortcuts = projections.workspace_shortcuts(scoped, workspace_id=workspace_id if pinned_scope else None) if wants('workspaceShortcuts') else None
        # Only summaries and the selected registration leave this query. No
        # transcripts, draft text, credentials, runtime mounts or full catalog.
        result = {} if _page_only else {'view': view, 'viewRevision': client['views'].get(instance['id'], {}).get('viewRevision', 0),
                'recentScope': recent_scope,
                'workHomePinnedScope': workspace_id if pinned_scope else None,
                'workHomeContext': work_home_context(scoped, pinned_workspace_id=workspace_id if pinned_scope else None),
                'navigationActivityById': activity_by_id,
                'selectedWorkspaceId': workspace_id, 'selectedSessionId': state.get('selectedSessionId'),
                'workspaces': [copy.deepcopy(workspace)] if workspace else [],
                'workspaceDefaults': copy.deepcopy(state.get('workspaceDefaults', {})),
                'settings': {'workspaces': copy.deepcopy(state.get('settings', {}).get('workspaces', {}))},
                'pinnedSessionIds': list(state.get('pinnedSessionIds', [])),
                'pinnedWorkspaceIds': workspace_pins(state),
                'pinnedItemIds': ordered_ids(state),
                'library': {'bounded': True, 'workspaceCount': sum(row.get('available') is True for row in state.get('workspaces', []))},
                'sharedHistory': {key: state.get('sharedHistory', {}).get(key) for key in ['loading', 'refreshing', 'error', 'issues', 'issueCount']},
                'attention': {'sessions': {sid: scoped['attention'].get('sessions', {}).get(sid, 0) for sid in visible_ids},
                              'items': read_receipts},
                'locationListing': copy.deepcopy(state.get('locationListing')) if 'locations.read' in self.manifest(instance['package'], validated=False)['capabilities'] else None,
                'actionStatus': {name: state.get('actionStatus', {}).get(name) for name in ('locations.list', 'locations.create')}}

        pages = {**status_pages, 'chatNavigation': chat_page, 'homeNavigation': home, 'workHome': work,
                 'pinnedNavigation': pinned_navigation, 'workspaceOverview': overview}
        result.update({key: value for key, value in pages.items() if value is not None})
        sidebar = {'recentView': {key: recent_view[key] for key in recent_view if key in SIDEBAR_FILTER_KEYS}}
        sidebar.update({key: page for key, page in {'pinned': pins, 'recent': recent,
                        'search': search, 'workspace': workspace_chats}.items() if page is not None})
        result['sidebarNavigation'] = sidebar
        if recent_navigation is not None:
            result.update(recentShortcuts=recent_shortcuts, recentNavigation=recent_navigation)
        if wants('workspaceShortcuts'):
            # The projection fences registered paths before paging. A path can
            # carry another pinned registration ID; retain it so unpin targets
            # that saved identity rather than hiding the same folder.
            result['workspaceShortcuts'] = shortcuts[:6]
            if requested is None:
                # Explicit/full APIs include the independent custom-module field.
                # The bundled client consumes only inclusive workspaceShortcuts.
                result['unpinnedWorkspaceShortcuts'] = projections.workspace_shortcuts(
                    scoped, workspace_id=workspace_id if pinned_scope else None, exclude_pinned=True)[:6]
        if wants('workspaceExplorer'):
            result['workspaceExplorer'] = projections.workspaces(scoped)
        if not _page_only:
            result['conversationOrganization'] = organization_projection(state,
                {row['id'] for row in chat_page['items']} if requested is None else visible_ids,
                directory=projections.directory)
        if requested is not None:
            result['navigationResources'] = sorted(requested)
        return result

    async def validate_package(self, digest):
        async with self.service.lock:
            self.source(digest)
            record = self.get('package', digest)
            fingerprint = self.host_fingerprint()
        root = Path(__file__).parent.parent
        toolchain = Path(os.environ.get('AMPLIFIER_SHELL_NODE_MODULES', root / 'frontend/node_modules')).resolve()
        node = shutil.which('node')
        if not node or not (toolchain / 'playwright').is_dir() or not (Path(__file__).parent / 'static/shell-validation.html').exists():
            fail('Host validation requires Node, Playwright with Chromium, and a production frontend build. No module was approved.', 503)
        process = await asyncio.create_subprocess_exec(node, str(Path(__file__).with_name('shell_validator.mjs')), str(toolchain), str(self.directory / (digest + '.mjs')), encoded(record['manifest']), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 45)
            result = json.loads(stdout) if process.returncode == 0 else {'status': 'failed', 'error': stderr.decode()[-2000:] or stdout.decode()[-2000:]}
        except TimeoutError:
            process.kill()
            await process.wait()
            result = {'status': 'failed', 'error': 'Host validation exceeded its 45 second limit.'}
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            raise
        except Exception as exc:
            result = {'status': 'failed', 'error': str(exc)}
        async with self.service.lock:
            self.source(digest)
            if fingerprint != self.host_fingerprint() or self.get('package', digest) != record:
                fail('Host or package changed during validation; retry.', 409)
            receipt = {'id': str(uuid.uuid4()), 'digest': digest, 'hostFingerprint': fingerprint, 'apiVersion': API, 'profile': record['manifest']['profile'], 'validatedAt': time.time(), **result}
            record['validation'] = receipt
            self.put('package', digest, record)
            if record['manifest']['profile'] == 'trusted-native-renderer-v1':
                self.service._publish_changes(globals=set())
        return receipt

    async def dispatch(self, action, args, origin, command_id):
        bound = self.service.clients.current.get()
        identity = args.get("clientId")
        if bound is None and identity in self.service.clients.records:
            with self.service.clients.bind(identity):
                return await self.dispatch(action, args, origin, command_id)
        if bound is not None and args.get("clientId", bound) != bound:
            fail("The shell command targets a different client.", 400)
        # The shared SQLite connection has one transaction owner. Only short
        # synchronous units take its lock; commands can recurse or wait on
        # external work and must never carry that lock into another dispatch.
        async with self.service.lock:
            cached = self.cached_command(action, args, origin, command_id)
            if cached is not None:
                return cached
            if action not in {'shell.command', 'shell.packages.validate'}:
                return self.dispatch_locked(action, args, origin, command_id)
            if action == 'shell.command':
                client = self.client(identity)
                instance = self.command_instance(client, args)
                generation = client['views'].get(instance['id'], {}).get('generation', 0)
                if instance['slot'] == 'navigation':
                    self.check_navigation_command(client, instance, args)
                elif args['action'] == 'presentation.update':
                    # Derive from the live effective composition and apply in
                    # one owner unit. A preview can change without changing the
                    # committed revision; a pre-await snapshot is not CAS.
                    receipt = self.presentation_command(client, instance, args, origin)
                    self.remember_command(action, args, origin, command_id, receipt)
                    return receipt
        if action == 'shell.packages.validate':
            digest = args['digest']
            if digest not in self.validations:
                task = asyncio.create_task(self.validate_package(digest))
                self.service.tasks.add(task)
                task.add_done_callback(self.service.tasks.discard)
                self.validations[digest] = task
                task.add_done_callback(lambda done: self.validations.pop(digest, None))
            receipt = {'accepted': True, 'result': await asyncio.shield(self.validations[digest])}
        elif instance['slot'] != 'navigation':
            receipt = await components.command(self, client, instance, args, origin, command_id)
        else:
            receipt = await self.service.dispatch(args['action'], args['args'], origin=origin, command_id=command_id)
        async with self.service.lock:
            cached = self.cached_command(action, args, origin, command_id)
            if cached is not None:
                return cached
            if action == 'shell.command':
                # Never overwrite a pre-await client snapshot. A replacement
                # invalidates the module's authority even if its ID survives.
                current = self.client(identity)
                composition = current['preview']['composition'] if current.get('preview') else current['composition']
                live = next((row for row in components.resolved(composition) if row['id'] == instance['id']), None)
                same = live == instance and current['views'].get(instance['id'], {}).get('generation', 0) == generation
                # A completed nested operation has its own durable outcome.
                # Replacement retires only its obsolete view follow-up; it
                # must not turn an already committed effect into a false 409.
                if same and instance['slot'] == 'navigation':
                    self.command_view(current, instance, args, identity)
                if 'state' in receipt:
                    receipt = {**receipt, 'state': self.service.browser_state()}
            self.remember_command(action, args, origin, command_id, receipt)
        return receipt

    def cached_command(self, action, args, origin, command_id):
        fingerprint = hashlib.sha256(encoded([action, args, origin]).encode()).hexdigest()
        cached = self.get('command', command_id) if command_id else None
        if cached:
            if cached['fingerprint'] != fingerprint:
                fail('Command ID was already used with different arguments.', 409)
            return {**cached['receipt'], **({'state': self.service.browser_state()} if cached.get('includeState') else {})}
        return None

    def remember_command(self, action, args, origin, command_id, receipt):
        if command_id:
            fingerprint = hashlib.sha256(encoded([action, args, origin]).encode()).hexdigest()
            self.put('command', command_id, {'fingerprint': fingerprint,
                'receipt': {key: value for key, value in receipt.items() if key != 'state'},
                'includeState': 'state' in receipt})

    def presentation_command(self, client, instance, args, origin):
        values = args.get('args', {})
        contract = components.commands()['presentation.update']
        if contract['capability'] not in self.manifest(instance['package'])['capabilities']:
            fail('Module has not declared this capability.', 403)
        try:
            validate(values, contract['schema'])
            validate(values.get('patch'), COMPOSITION['properties']['presentation'])
        except ValidationError as exc:
            fail('Invalid presentation patch: ' + exc.message)
        if set(values) != {'patch', 'expectedRevision'} or values['expectedRevision'] != client['revision']:
            fail('Inspect the current composition revision before updating presentation.', 409)
        effective = client['preview']['composition'] if client.get('preview') else client['composition']
        composition = {**effective, 'presentation': {**effective['presentation'], **values['patch']}}
        target = {'clientId': args['clientId'], 'expectedRevision': client['revision']}
        prepared = self.dispatch_locked('shell.changes.prepare', {**target, 'composition': composition}, origin, None)
        return self.dispatch_locked('shell.changes.apply', {**target, 'changeId': prepared['result']['id']}, origin, None)

    def command_instance(self, client, args):
        instance = self.instance(client, args['instanceId'])
        current = client['views'].get(instance['id'], {}).get('generation', 0)
        if (instance['slot'] != 'navigation' or 'generation' in args) and args.get('generation') != current:
            fail('This component was replaced. Read its current snapshot before acting.', 409)
        return instance

    def check_navigation_command(self, client, instance, args):
        capability = COMMAND_CAPABILITIES.get(args['action'])
        if not capability or capability not in self.manifest(instance['package'])['capabilities']:
            fail('Module has not declared this capability.', 403)
        if args['action'] == 'navigation.pinOrder' and 'workspaces.manage' not in self.manifest(instance['package'])['capabilities']:
            fail('Mixed pin ordering requires chat and workspace management capabilities.', 403)
        if args['action'] == 'attention.read':
            observed = args['args']
            ids, fingerprints = observed.get('ids'), observed.get('fingerprints')
            identity = args['clientId']
            if self.resources.registered(identity):
                # Use the current bounded pages, including later Status pages.
                # Rebuilding a full navigation snapshot can miss those rows.
                envelope = self.resources.snapshot(identity) or {}
                resources = envelope.get('instances', {}).get(instance['id'], {}).get('resources', {})
                record = 'context' if instance['package'] in BUILTINS else 'customSnapshot'
                context = resources.get(record, {}).get('value', {})
            else:
                context = self.navigation(client, instance, identity=identity)
            visible = {item['id'] for item in context.get('attention', {}).get('items', ())}
            if (set(observed) != {'ids', 'fingerprints'} or not isinstance(ids, list) or not ids
                    or any(not isinstance(sid, str) or sid not in visible for sid in ids)
                    or not isinstance(fingerprints, dict)
                    or any(not isinstance(fingerprints.get(sid), str) or not fingerprints[sid] for sid in ids)):
                fail('Only observed conversation notices in this module can be reviewed.', 403)

    def command_view(self, client, instance, args, identity):
        if args['action'] in {'workspace.select', 'workspace.create', 'workspace.add'} and instance['package'] == 'builtin.workspaces':
            composition = client['preview']['composition'] if client.get('preview') else client['composition']
            target = instance.get('hideWhen', {}).get('instanceId')
            if any(row['id'] == target and row['package'] == 'builtin.chats' for row in composition['instances']):
                item = client['views'].setdefault(target, {'view': {}, 'dirty': False})
                item['view'].update(navChatScope='workspace', navWorkspaceList=False, navFilter='', navStatusFilter='all')
                item['viewRevision'] = item.get('viewRevision', 0) + 1
                self.put('client', identity, client)
                self.notify(identity)
        if args['action'] == 'session.pin':
            item = client['views'].setdefault(args['instanceId'], {'view': {}, 'dirty': False})
            page = self.navigation(client, instance)['chatNavigation']
            item['view']['navChatPage'] = {**page['scope'], 'index': 0}
            item['viewRevision'] = item.get('viewRevision', 0) + 1
            self.put('client', identity, client)
            self.notify(identity)

    def dispatch_locked(self, action, args, origin, command_id):
        """Caller owns service.lock; no recursion or await in this unit."""
        identity = args.get('clientId')
        client = self.client(identity) if identity else None
        changed = False
        if action in {'shell.command', 'shell.view.update'}:
            self.command_instance(client, args)
        if action == 'shell.inspect':
            result = self.inspect(identity)
        elif action == 'shell.query':
            result = self.snapshot(client, self.instance(client, args['instanceId']), identity=identity)
        elif action == 'shell.packages.stage':
            if args['manifest']['id'].startswith('builtin.'):
                fail('The builtin namespace is reserved.')
            digest = hashlib.sha256((encoded(args['manifest']) + '\n' + args['source']).encode()).hexdigest()
            path = self.directory / (digest + '.mjs')
            if not self.get('package', digest):
                path.write_text(args['source'])
                path.chmod(0o600)
                self.put('package', digest, {'digest': digest, 'manifest': args['manifest'], 'validation': None})
            self.source(digest)
            result = self.get('package', digest)
        elif action == 'shell.view.update':
            instance = self.instance(client, args['instanceId'])
            patch = args['patch']
            if instance['slot'] == 'navigation':
                if set(patch) - VIEW_KEYS or len(encoded(patch)) > 16000:
                    fail('Unsupported or oversized module view state.')
                from .chat_navigation import view_patch as chat_patch
                from .workspace_navigation import view_patch as workspace_patch
                try:
                    if 'workspaceDraft' in patch:
                        validate(patch['workspaceDraft'], EDIT_STATE)
                    if 'locationPicker' in patch:
                        validate(patch['locationPicker'], {'type': ['object', 'null'], 'additionalProperties': False, 'properties': {'controlId': {'type': 'string', 'maxLength': 200}, 'path': {'type': 'string', 'maxLength': 4000}}})
                    if 'navWorkspaceAncestorsOpen' in patch:
                        validate(patch['navWorkspaceAncestorsOpen'], {'type': 'boolean'})
                    patch = chat_patch(workspace_patch(self.scoped_state(client, instance), patch))
                except (ValueError, ValidationError) as exc:
                    fail(str(exc))
            item = client['views'].setdefault(args['instanceId'], {'view': {}, 'dirty': False})
            if len(encoded({**item['view'], **patch})) > 16000:
                fail('Module view state exceeds 16 KB.')
            if instance['slot'] == 'navigation' and 'navRecentLimit' in patch:
                current = self.navigation(client, instance, identity=identity)['recentNavigation']
                requested = patch['navRecentLimit']
                # Count-bound requests, not a fixed library cap. One next step
                # remains admissible when the catalog shrinks during a flight.
                bound = max(((current['total'] + 19) // 20) * 20, current['limit'] + 20, 100)
                if requested > bound:
                    fail('Recent limit exceeds the current catalog or its next step.')
                key = self.recent_key(client, instance, identity)
                if requested > 100:
                    self.recent_limits[key] = requested
                else:
                    self.recent_limits.pop(key, None)
                patch = {**patch, 'navRecentLimit': min(100, requested)}
            item['view'].update(patch)
            if set(patch) & {'navShowAgentCreated', 'navRecentLimit'}:
                # Host-owned ordering fence for late read responses. This is
                # presentation only, not a task/input revision or replay token.
                item['view']['navRecentRevision'] = item['view'].get('navRecentRevision', 0) + 1
            if 'dirty' in args:
                item['dirty'] = args['dirty']
            item['viewRevision'] = item.get('viewRevision', 0) + 1
            self.put('client', identity, client)
            result = {'status': 'updated', 'shellView': {
                'instanceId': args['instanceId'], 'generation': item.get('generation', 0),
                'viewRevision': item['viewRevision'],
                'view': copy.deepcopy(self.scoped_state(client, instance)['view'])}}
            changed = True
        elif action == 'shell.report':
            preview_id = client['preview']['id'] if client['preview'] else None
            if args['revision'] != client['revision'] or args.get('previewId') != preview_id:
                result = {'status': 'stale'}
            else:
                effective = client['preview']['composition'] if client['preview'] else client['composition']
                ready = (all(args['instances'].get(item['id']) == 'ready' for item in effective['instances'])
                         and not any(value in {'error', 'loading'} for value in args['instances'].values()))
                client['reported'] = {**args, 'at': time.time(), 'status': 'ready' if ready else 'incomplete'}
                if ready and not client['preview']:
                    client['lastGood'] = client['composition']
                self.put('client', identity, client)
                result = client['reported']
        else:
            if args['expectedRevision'] != client['revision']:
                fail('Shell composition changed. Inspect and prepare against its current revision.', 409)
            if action == 'shell.changes.prepare':
                self.check_composition(args['composition'])
                change_id = str(uuid.uuid4())
                result = {'id': change_id, 'clientId': identity, 'baseRevision': client['revision'], 'before': client['composition'], 'composition': args['composition'], 'status': 'prepared'}
                self.put('change', change_id, result)
            else:
                if action == 'shell.recover':
                    composition = copy.deepcopy(DEFAULT if args['target'] == 'default' else client['lastGood'])
                    # Rendering recovery must preserve the committed appearance.
                    composition['presentation'] = copy.deepcopy(client['composition']['presentation'])
                else:
                    change = self.get('change', args['changeId'])
                    if not change or change['clientId'] != identity:
                        fail('Prepared change not found for this client.', 404)
                    if action != 'shell.changes.revert' and change['baseRevision'] != client['revision']:
                        fail('Prepared change is stale.', 409)
                    composition = change['before'] if action == 'shell.changes.revert' else change['composition']
                deferred = self.transition(client, composition) if action != 'shell.recover' else None
                if deferred:
                    return {'accepted': False, 'result': deferred, 'error': deferred['reason'], 'status': 409, 'code': 'shell_change_deferred'}
                if action == 'shell.recover':
                    self.check_composition(composition)
                previous = client['preview']['composition'] if client.get('preview') else client['composition']
                before = {item['id']: item for item in components.resolved(previous)}
                after = {item['id']: item for item in components.resolved(composition)}
                for instance_id in before.keys() | after.keys():
                    if before.get(instance_id) != after.get(instance_id) or action == 'shell.recover':
                        view = client['views'].setdefault(instance_id, {'view': {}, 'dirty': False})
                        view['generation'] = view.get('generation', 0) + 1
                        source = after.get(instance_id) or before.get(instance_id)
                        manifest = BUILTINS.get(source['package']) or (self.get('package', source['package']) or {}).get('manifest', {})
                        state_schema = manifest.get('stateSchema')
                        if action == 'shell.recover' and view.get('stateSchema') not in {None, state_schema}:
                            view['view'] = {}
                        if state_schema:
                            view['stateSchema'] = state_schema
                        if action == 'shell.recover':
                            view['dirty'] = False
                            view['view'].pop('workspaceDraft', None)
                            view['viewRevision'] = view.get('viewRevision', 0) + 1
                if action == 'shell.changes.preview':
                    client['preview'] = {'id': args['changeId'], 'composition': composition}
                else:
                    # Only a committed appearance change updates new-window defaults.
                    # Recovering an older tab must not overwrite a newer preference.
                    if action != 'shell.recover' and client['composition']['presentation'] != composition['presentation']:
                        self.put('preferences', 'appearance', {key: value for key, value in composition['presentation'].items()
                                                              if key != 'collapseInterimReplies'})
                    client['composition'] = composition
                    client['preview'] = None
                    client['revision'] += 1
                client['reported'] = None
                self.put('client', identity, client)
                self.prune_recent_limits(identity, client, composition)
                result = {'status': 'awaiting-browser', 'revision': client['revision'], 'composition': composition}
                changed = True
        receipt = {'accepted': True, 'result': result}
        self.remember_command(action, args, origin, command_id, receipt)
        if changed:
            self.notify(identity)
        return receipt
