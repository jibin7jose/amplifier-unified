"""Bounded bundled-client resource ownership over canonical shell projections.

Values are detached once, then treated as immutable by the state transport.
Committed canonical writes invalidate global dependency epochs; session navigation freshness
belongs to the canonical directory's semantic revision, never host telemetry.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import time

from .shell_navigation_demand import NAVIGATION_RESOURCES
from .performance_diagnostics import count, measure

MAX_CLIENTS = 32
MAX_CLIENT_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_SAFE_INTEGER = 2**53 - 1
PAGE_FIELDS = {
    **{name: (name,) for name in NAVIGATION_RESOURCES if not name.startswith('sidebar')},
    'recentNavigation': ('recentNavigation', 'recentShortcuts'),
}
GLOBAL_KEYS = frozenset({'workspaces', 'settings', 'workspaceDefaults', 'pinnedSessionIds',
    'pinnedWorkspaceIds', 'pinnedItemIds', 'pinOrderCustomized', 'conversationOrganization',
    'attentionRead', 'sharedHistory', 'locationListing', 'actionStatus', 'hiddenNativeSessions',
    'hiddenNativeWorkspaces'})
CHAT_GLOBALS = GLOBAL_KEYS - {'sharedHistory', 'locationListing', 'actionStatus'}
CHAT_FILTERS = ('navChatScope', 'navSort', 'navFilter', 'navStatusFilter', 'navLocationFilter',
    'navArchive', 'navCollection', 'navChatPage', 'navShowAgentCreated', 'navGroup', 'navAge')
WORKSPACE_FILTERS = ('navWorkspaceBrowseFor', 'navWorkspacePath', 'navWorkspaceFilter',
    'navWorkspacePage', 'navWorkspaceMode', 'navWorkspaceArchive')


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def fail(message, status=400):
    from .errors import AppError
    raise AppError(message, status)


def scope_for(state):
    view = state.get('view', {})
    surface = view.get('workSurface') or 'chat'
    return [state.get('selectedSessionId'), state.get('selectedWorkspaceId'), surface,
            view.get('workWorkspaceId') if surface == 'workspace' else None,
            (view.get('workWorkspaceTab') or 'chats') if surface == 'workspace' else None]


def page_value(snapshot, name):
    if name.startswith('sidebar'):
        field = name.removeprefix('sidebar').lower()
        return {'sidebarNavigation': {field: snapshot['sidebarNavigation'][field]}}
    return {key: snapshot[key] for key in PAGE_FIELDS[name] if key in snapshot}


def context_value(snapshot):
    omitted = {field for fields in PAGE_FIELDS.values() for field in fields}
    result = {key: value for key, value in snapshot.items()
              if key not in omitted | {'navigationResources', 'generation', 'sidebarNavigation'}}
    result['sidebarNavigation'] = {'recentView': snapshot['sidebarNavigation']['recentView']}
    return result


def visible_ids(resources):
    result = set()
    for name, record in resources.items():
        if name in {'context', 'customSnapshot', 'workspaceOverview', 'workspaceExplorer', 'workspaceShortcuts'}:
            continue
        value = record['value']
        pages = list(value.get('sidebarNavigation', {}).values()) if name.startswith('sidebar') else [value.get(name)]
        for page in pages:
            for item in (page or {}).get('items', []):
                row = item.get('row') if item.get('kind') == 'session' else item
                if row and row.get('id') and (name != 'pinnedNavigation' or item.get('kind') == 'session'):
                    result.add(row['id'])
    return result


class ResourceLimit(Exception):
    pass


class ShellResources:
    def __init__(self, shell):
        self.shell = shell
        self.clients = {}
        self.epochs = {key: 0 for key in GLOBAL_KEYS}
        self.unknown_epoch = 0
        self.total_bytes = 0
        from .shell_query_owner import ShellQueryOwner
        self.queries = ShellQueryOwner(self)

    async def close(self):
        await self.queries.close()

    def invalidate(self, global_keys=None):
        """Called with actual changed global keys after a canonical commit."""
        if global_keys is None:
            self.unknown_epoch += 1
            self.queries.invalidate_workspaces()
        else:
            changed = set(global_keys) & GLOBAL_KEYS
            for key in changed:
                self.epochs[key] += 1
            if 'workspaces' in changed:
                self.queries.invalidate_workspaces()

    def _prune(self):
        service, now = self.shell.service, time.monotonic()
        active = {identity for queue, identity in getattr(service, 'queue_clients', {}).items()
                  if getattr(service, 'queue_sessions', {}).get(queue) is None}
        for identity, record in list(self.clients.items()):
            if identity in active:
                record['disconnected_since'] = None
            elif record.get('disconnected_since') is None:
                record['disconnected_since'] = now
            if (identity not in service.clients.records or identity not in active
                    and now - record['disconnected_since'] >= 60):
                self.total_bytes -= self.clients.pop(identity)['bytes']
                # Browser baselines can otherwise retain the retired envelope
                # and return it on an unchanged-revision reconnect. These are
                # disposable projections, not durable client views.
                for attribute in ('_client_snapshots', '_client_snapshot_preferences'):
                    getattr(service, attribute, {}).pop(identity, None)
                self.queries.prune()

    def registered(self, identity):
        self._prune()
        return identity in self.clients

    def list_clock(self, identity):
        from .chat_navigation import list_clock
        registration = self.clients.get(identity)
        return (list_clock() if registration is not None
                and registration['wanted'] & {'sidebarSearch', 'sidebarWorkspace'} else None)

    def _bound(self, identity):
        service = self.shell.service
        record = service.clients.records.get(identity)
        if service.clients.current.get() != identity or record is None or record.get('kind') != 'web':
            fail('Attach and bind this web client before requesting shell resources.', 409)
        return service.state

    def _composition(self, client, recovery):
        from .shell_modules import DEFAULT
        return DEFAULT if recovery else client['preview']['composition'] if client.get('preview') else client['composition']

    def _bootstrap(self, identity, client, composition, instances):
        facts = [self.shell.service.instance_id, identity, client.get('revision', 0),
                 (client.get('preview') or {}).get('id'), composition,
                 [(item['id'], item['package'], client.get('views', {}).get(item['id'], {}).get('generation', 0))
                  for item in instances]]
        return hashlib.sha256(encoded(facts).encode()).hexdigest()

    def _prepare(self, identity, recovery):
        from .shell_components import resolved
        client = self.shell.client(identity)
        composition = self._composition(client, recovery)
        instances = resolved(composition)
        return client, composition, instances, self._bootstrap(identity, client, composition, instances)

    def bootstrap_key(self, identity):
        self._bound(identity)
        registration = self.clients.get(identity)
        return self._prepare(identity, bool(registration and registration['recovery']))[3]

    def browser_projection(self, identity):
        """Prepare one frame's descriptor and resources from one client read."""
        state = self._bound(identity)
        registration = self.clients.get(identity)
        client, composition, instances, bootstrap = self._prepare(
            identity, bool(registration and registration['recovery']))
        # Keep descriptor availability independent of resource registration,
        # including the frame that retires an idle registration.
        result = {'shellBootstrapKey': bootstrap}
        self._prune()
        registration = self.clients.get(identity)
        if registration is not None and registration['scope'] == scope_for(state):
            result['shellResources'] = self._prepared_snapshot(identity, state=state, client=client,
                composition=composition, instances=instances, bootstrap=bootstrap)
        return result

    def register(self, identity, demand_id, resources, scope, recovery=False):
        state = self._bound(identity)
        self._prune()
        if type(demand_id) is not int or not 1 <= demand_id <= MAX_SAFE_INTEGER:
            fail('Supply a positive safe-integer shell demandId.')
        if (not isinstance(resources, (list, tuple, set, frozenset)) or len(resources) > 16
                or any(not isinstance(item, str) or item not in NAVIGATION_RESOURCES for item in resources)):
            fail('Invalid shell resources.')
        if (not isinstance(scope, list) or len(scope) != 5
                or any(value is not None and (not isinstance(value, str) or len(value) > 4000) for value in scope)):
            fail('Invalid shell scope.')
        if scope != scope_for(state):
            fail('Shell scope changed; request the current client scope.', 409)
        if type(recovery) is not bool:
            fail('Invalid recovery mode.')
        wanted = frozenset(resources)
        old = self.clients.get(identity)
        if old and (demand_id < old['demandId'] or demand_id == old['demandId'] and
                    (wanted != old['wanted'] or scope != old['scope'] or recovery != old['recovery'])):
            fail('Shell demand is stale or reuses an identity for different resources.', 409)
        if old and demand_id == old['demandId']:
            old['disconnected_since'] = time.monotonic()
            return
        for key in list(self.clients):
            if key not in self.shell.service.clients.records:
                self.total_bytes -= self.clients.pop(key)['bytes']
        if old is None and len(self.clients) >= MAX_CLIENTS:
            fail('Too many active shell resource clients; close an unused client before attaching another.', 429)
        self.clients[identity] = {'demandId': demand_id, 'wanted': wanted, 'scope': list(scope),
            'recovery': recovery, 'instances': old['instances'] if old else {},
            'keys': old['keys'] if old else {}, 'bytes': old['bytes'] if old else 0,
            'envelope': None, 'disconnected_since': time.monotonic()}

    def _epochs(self, keys):
        return self.unknown_epoch, tuple((key, self.epochs[key]) for key in sorted(keys))

    def _page_key(self, name, scoped, instance, navigation_revision, client, workspace_revision, root_navigation_revision):
        view = dict(scoped['view'])
        from .adaptive_work import STATUS_RESOURCES
        if name in STATUS_RESOURCES:
            pinned = instance.get('scope', {}).get('workspaceId') if instance.get('scope', {}).get('mode') == 'pinned' else None
            return (((root_navigation_revision, workspace_revision), None), self._epochs(CHAT_GLOBALS),
                    pinned, scoped.get('selectedSessionId'),
                    encoded({'index': (view.get('navStatusPages') or {}).get(STATUS_RESOURCES[name], 0)}))
        if name == 'workHome':
            from .adaptive_work import WORK_QUERY_FIELDS
            return (((root_navigation_revision, workspace_revision), None), self._epochs(CHAT_GLOBALS),
                    view.get('workHomeScope'), scoped.get('selectedSessionId'),
                    encoded({**{key: view.get(key) for key in WORK_QUERY_FIELDS},
                             'pinnedWorkspaceId': instance.get('scope', {}).get('workspaceId')
                             if instance.get('scope', {}).get('mode') == 'pinned' else None}))
        saved = view.get('navRecentLimit', 20)
        from .chat_navigation import RECENT_LIMITS
        saved = saved if type(saved) is int and saved in RECENT_LIMITS else 20
        view['navRecentLimit'] = self.shell.recent_limits.get(self.shell.recent_key(client, instance), saved)
        workspace = scoped.get('selectedWorkspaceId')
        mode = instance.get('scope', {}).get('mode')
        globals_key = self._epochs(CHAT_GLOBALS)
        if name in {'workspaceExplorer', 'workspaceShortcuts'}:
            navigation_revision = (workspace_revision, None)
            globals_key = self._epochs({'workspaces', 'pinnedWorkspaceIds', 'conversationOrganization'})
        elif name != 'workspaceOverview':
            navigation_revision = ((root_navigation_revision, workspace_revision)
                if name in {'pinnedNavigation', 'sidebarPinned'} else root_navigation_revision, None)
            fields = {'workspaces', 'pinnedSessionIds', 'conversationOrganization'}
            if name in {'pinnedNavigation', 'sidebarPinned'}:
                fields |= {'pinnedWorkspaceIds', 'pinnedItemIds', 'pinOrderCustomized'}
            globals_key = self._epochs(fields)
        if name in {'workspaceExplorer', 'workspaceShortcuts', 'workspaceOverview'}:
            filters = WORKSPACE_FILTERS if name != 'workspaceOverview' else ()
            return (navigation_revision, globals_key, workspace, mode,
                    encoded({key: view.get(key) for key in filters}))
        home = {**view, 'navChatScope': 'all', 'navArchive': 'active', 'navCollection': None,
                'navFilter': '', 'navStatusFilter': 'all', 'navLocationFilter': 'all', 'navSort': 'activity'}
        home.pop('navChatPage', None)
        sidebar = {**home, 'navChatScope': 'workspace' if mode == 'pinned' else 'all'}
        if name in {'sidebarRecent', 'sidebarSearch'}:
            query = {**sidebar, **view.get('navRecentView', {})}
        elif name in {'sidebarPinned', 'pinnedNavigation', 'recentNavigation'}:
            query = sidebar
        elif name == 'sidebarWorkspace':
            query = {**view, 'navChatScope': 'workspace'}
        elif name == 'homeNavigation' and mode != 'pinned':
            query = home
        else:
            query = view
        fields = (*CHAT_FILTERS, 'navPinnedPage', 'navRecentLimit', 'navRecentRevision')
        from .chat_navigation import list_clock
        return (navigation_revision, globals_key,
                workspace if query.get('navChatScope') != 'all' else None,
                scoped.get('selectedSessionId'), encoded({key: query.get(key) for key in fields}),
                list_clock() if name in {'sidebarSearch', 'sidebarWorkspace'} else None)

    @staticmethod
    def _record(value, previous, previous_revision=0):
        wire = encoded(value).encode()
        size = len(wire)
        if size > MAX_CLIENT_BYTES:
            raise ResourceLimit()
        digest = hashlib.sha256(wire).hexdigest()
        if previous is not None and digest == previous['_digest']:
            return previous
        return {'revision': previous['revision'] + 1 if previous else previous_revision + 1,
                'value': deepcopy(value), '_digest': digest, '_bytes': size}

    @staticmethod
    def _entry(generation, own, pending, prior):
        same = prior.get('generation') == generation and prior.get('resources') == own and prior.get('pending', {}) == pending
        versions = {**prior.get('versions', {}), **{key: value['revision'] for key, value in own.items()}}
        return {'generation': generation, 'resources': own, 'pending': pending, 'versions': versions,
            'stateRevision': prior.get('stateRevision', 0) + (not same)}

    def snapshot(self, identity):
        self._prune()
        state = self._bound(identity)
        registration = self.clients.get(identity)
        if registration is None or registration['scope'] != scope_for(state):
            return None
        client, composition, instances, bootstrap = self._prepare(identity, registration['recovery'])
        return self._prepared_snapshot(identity, state=state, client=client,
            composition=composition, instances=instances, bootstrap=bootstrap)

    def _prepared_snapshot(self, identity, *, state, client, composition, instances, bootstrap):
        registration = self.clients[identity]
        directory = getattr(self.shell.service, 'directory', None)
        semantic = directory.observation_revision if directory is not None else {}
        nav = (semantic.get('navigation', state.get('revision')), semantic.get('attention'))
        workspace_revision = semantic.get('workspace', state.get('revision'))
        root_navigation_revision = semantic.get('root_navigation', state.get('revision'))
        from .shell_modules import BUILTINS
        scoped_states = {item['id']: self.shell.scoped_state(client, item) for item in instances
                         if item['package'] in {'builtin.chats', 'builtin.workspaces'}}
        # The effective view also contains initial preferences and derives its
        # chat scope from the selected resident row. Neither is represented by
        # the composition revision or per-instance view revision alone.
        scoped_views = tuple((name, scoped.get('selectedWorkspaceId'), encoded(scoped['view']))
                             for name, scoped in scoped_states.items())
        builtin_only = all(item['package'] in BUILTINS for item in instances)
        recent = tuple((item['id'], self.shell.recent_limits.get(self.shell.recent_key(client, item)))
                       for item in instances
                       if item['slot'] == 'navigation')
        clock = self.list_clock(identity)
        stamp = (bootstrap, nav, workspace_revision, root_navigation_revision, self._epochs(GLOBAL_KEYS), encoded(client.get('views', {})),
                 scoped_views, tuple(registration['scope']), registration['wanted'], recent, clock,
                 state.get('revision') if any(item['package'] not in ('builtin.chats', 'builtin.workspaces')
                     and not item['package'].startswith('builtin.')
                     for item in composition['instances']) else None)
        budget_stamp = (stamp, self.total_bytes, MAX_CLIENT_BYTES, MAX_TOTAL_BYTES)
        if registration.get('failed_stamp') == budget_stamp:
            return registration['error_envelope']
        if builtin_only and registration.get('settled_stamp') == budget_stamp:
            # Pending completion/retry owns an independent lifecycle. Only a
            # completely settled envelope may bypass its per-resource path.
            self.queries.prune()
            count(self.shell.service, 'shell.resources.snapshot.hit')
            return registration['envelope']
        try:
            from .chat_navigation import frozen_list_clock
            with frozen_list_clock(clock):
                result = self._snapshot(identity, state=state, client=client, bootstrap=bootstrap,
                    navigation_revision=nav, workspace_revision=workspace_revision, root_navigation_revision=root_navigation_revision,
                    scoped_states=scoped_states, resolved_instances=instances)
        except ResourceLimit:
            result = {'hostInstanceId': self.shell.service.instance_id, 'clientId': identity,
                'demandId': registration['demandId'], 'bootstrapKey': bootstrap,
                'scope': list(registration['scope']), 'instances': {},
                'error': {'code': 'shell_resource_limit',
                    'message': 'Requested navigation exceeds the resource budget. Narrow the requested pages or close unused clients.'}}
            registration.pop('settled_stamp', None)
            registration.update(failed_stamp=budget_stamp, error_envelope=result)
            return result
        registration.pop('failed_stamp', None)
        registration.pop('error_envelope', None)
        if builtin_only and not any(item['pending'] for item in registration['instances'].values()):
            registration['settled_stamp'] = (stamp, self.total_bytes, MAX_CLIENT_BYTES, MAX_TOTAL_BYTES)
        else:
            registration.pop('settled_stamp', None)
        return result

    def _snapshot(self, identity, *, state, client, bootstrap, navigation_revision, workspace_revision, root_navigation_revision,
                  scoped_states, resolved_instances):
        registration = self.clients[identity]
        from .shell_modules import BUILTINS
        instances, keys = {}, {}
        for instance in resolved_instances:
            name = instance['id']
            generation = client.get('views', {}).get(name, {}).get('generation', 0)
            custom = instance['package'] not in BUILTINS
            if not custom and instance['package'] not in {'builtin.chats', 'builtin.workspaces'}:
                continue
            prior = registration['instances'].get(name, {})
            previous = prior.get('resources', {}) if prior.get('generation') == generation else {}
            previous_keys = registration['keys'].get(name, {}) if prior.get('generation') == generation else {}
            own, own_keys, pending = {}, {}, {}
            if custom:
                key = (bootstrap, state.get('revision'), navigation_revision, self._epochs(GLOBAL_KEYS),
                       encoded(client.get('views', {}).get(name, {})))
                value = previous.get('customSnapshot')
                if previous_keys.get('customSnapshot') != key:
                    # Optional package failure must not stop unrelated browser state.
                    # Match inspect's package-validation boundary, not a broad
                    # catch around arbitrary projection implementation failures.
                    try:
                        self.shell.manifest(instance['package'])
                    except Exception:
                        instances[name] = self._entry(generation, {}, {}, prior)
                        keys[name] = {}
                        continue
                    count(self.shell.service, 'shell.resources.custom.build')
                    with measure(self.shell.service, 'shell.resources.custom', synchronous=True):
                        snapshot = self.shell.snapshot(client, instance, identity=identity)
                        value = self._record({**snapshot, 'viewRevision':
                            client.get('views', {}).get(name, {}).get('viewRevision', 0)}, value,
                            prior.get('versions', {}).get('customSnapshot', 0))
                own['customSnapshot'], own_keys['customSnapshot'] = value, key
            else:
                scoped = scoped_states[name]
                dirty = set()
                for resource in registration['wanted']:
                    key = (bootstrap, self._page_key(resource, scoped, instance, navigation_revision, client, workspace_revision, root_navigation_revision))
                    own_keys[resource] = key
                    if previous_keys.get(resource) == key and resource not in prior.get('pending', {}):
                        own[resource] = previous[resource]
                    else:
                        dirty.add(resource)
                        diagnostics = getattr(self.shell.service, 'performance_diagnostics', None)
                        if diagnostics is not None:
                            from .shell_resource_diagnostics import rebuild_reasons
                            reasons, globals_changed = rebuild_reasons(previous_keys.get(resource), key,
                                workspace_page=resource in {'workspaceExplorer', 'workspaceShortcuts', 'workspaceOverview'})
                            diagnostics.record_resource_rebuild(resource, instance['package'], reasons, globals_changed)
                if dirty:
                    from .shell_query_owner import PendingQuery
                    for resource in sorted(dirty):
                        key = own_keys[resource]
                        try:
                            count(self.shell.service, 'shell.resources.pages.build')
                            with self.queries.resource(identity, name, resource, key), measure(
                                    self.shell.service, 'shell.resources.pages', synchronous=True):
                                built = self.shell.navigation(client, instance, identity=identity,
                                    navigation_resources=frozenset({resource}), _page_only=True)
                            own[resource] = self._record(page_value(built, resource), previous.get(resource),
                                prior.get('versions', {}).get(resource, 0))
                        except PendingQuery as wait:
                            pending[resource] = {'status': 'retry' if wait.retry_after else 'pending',
                                                 'retryAfterMs': wait.retry_after}
                            old_key = previous_keys.get(resource)
                            # Retain only a value for the same query/descriptor.
                            # Its explicit pending marker distinguishes old facts
                            # from a newly completed exact count/page.
                            if (resource in previous and old_key and old_key[0] == key[0]
                                    and old_key[1][2:] == key[1][2:]):
                                own[resource] = previous[resource]
                context_key = (bootstrap, self._epochs(GLOBAL_KEYS), navigation_revision,
                    encoded(scoped['view']), encoded(client.get('views', {}).get(name, {})),
                    tuple(registration['scope']), tuple(sorted((key, value['revision']) for key, value in own.items())))
                own_keys['context'] = context_key
                if previous_keys.get('context') == context_key:
                    own['context'] = previous['context']
                else:
                    count(self.shell.service, 'shell.resources.context.build')
                    with measure(self.shell.service, 'shell.resources.context', synchronous=True):
                        built = self.shell.navigation(client, instance, identity=identity,
                            navigation_resources=frozenset(), context_visible_ids=visible_ids(own))
                    own['context'] = self._record(context_value(built), previous.get('context'))
            instances[name] = self._entry(generation, own, pending, prior)
            keys[name] = own_keys
        size = sum(value['_bytes'] for instance in instances.values() for value in instance['resources'].values())
        if size > MAX_CLIENT_BYTES or self.total_bytes - registration['bytes'] + size > MAX_TOTAL_BYTES:
            raise ResourceLimit()
        envelope = {'hostInstanceId': self.shell.service.instance_id, 'clientId': identity,
            'demandId': registration['demandId'], 'bootstrapKey': bootstrap, 'scope': list(registration['scope']),
            'instances': {name: {'generation': item['generation'], 'stateRevision': item.get('stateRevision', 0),
                'pending': item.get('pending', {}), 'resources': {
                key: {'revision': value['revision'], 'value': value['value']} for key, value in item['resources'].items()}}
                for name, item in instances.items()}}
        self.total_bytes += size - registration['bytes']
        registration.update(instances=instances, keys=keys, bytes=size)
        self.queries.prune()
        if registration['envelope'] == envelope:
            return registration['envelope']
        registration['envelope'] = envelope
        return envelope
