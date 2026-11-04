"""Read-only derived indexes shared within one saved state generation.

AppService invalidates live facts on every save, including saves without a
revision change. Navigation indexes survive only when their complete semantic
key is unchanged. Client query keys are explicit; drafts never enter shared
projections. The full agent state path remains an uncached read of live state.
"""
import hashlib
import json
from collections import OrderedDict
from copy import deepcopy
from .performance_diagnostics import measured


def detail_facts(row):
    """Bounded shared facts: change means conservative all-client invalidation.

    Streaming recency is patched as a scalar. Settled ordering, notifications,
    membership and attention cannot be silently forwarded under a new revision.
    """
    from .browser_state import SUMMARY_FIELDS
    from .chat_navigation import navigation_activity
    from .cold_display import notifications
    from .session_navigation import is_agent_created
    fields = {key: row.get(key) for key in SUMMARY_FIELDS
              if key not in {'recentActivityAt', 'navigationActivityAt'}}
    fields.update({key: row.get(key) for key in
                   ('error', 'completion', 'manualUnread', 'approvals', 'questions', 'ownership', 'unreadCompletion')})
    from .session_activity import activity_flags
    fields['activityFlags'] = activity_flags(row)
    fields['navigationActivity'] = navigation_activity(row)
    fields['agentCreated'] = is_agent_created(row)
    fields['notifications'] = notifications(row)
    return json.dumps(fields, sort_keys=True, separators=(',', ':'))


class StateProjections:
    def __init__(self, resolver=None, *, service=None):
        self.service = service
        self.resolver = resolver
        self.values = {}
        self.previous_navigation = None
        self.detail_bodies = OrderedDict()
        self.observation_listener = None

    def invalidate(self, *, state=None, session_ids=None, detail_only=False):
        if self.observation_listener is not None:
            self.observation_listener(state, session_ids, detail_only)
        if session_ids is None:
            self.detail_bodies.clear()
        else:
            for identity in session_ids:
                self.detail_bodies.pop(identity, None)
        if detail_only and session_ids is not None:
            index = self.values.get(('session-index',))
            if index is not None and index.patch(state, session_ids):
                parents = {index.by_id[key].get('parentId') for key in session_ids
                           if key in index.by_id and not index._membership[key][2]} - {None}
                for key in list(self.values):
                    if key[0] == 'browser-navigation' and (key[1] in parents
                            or any(parent in str(key[-1]) for parent in parents)):
                        self.values.pop(key, None)
                # Streaming text does not alter navigation, attention or shell
                # evidence. Keep those immutable facts; detail is built anew.
                return
        if state is None:
            # An explicit unknown invalidation may follow direct global edits,
            # without a canonical commit epoch. It cannot reuse prior pages.
            resources = getattr(getattr(self.service, 'shell', None), 'resources', None)
            queries = getattr(resources, 'queries', None)
            if queries is not None:
                queries.invalidate_workspaces()
            self.values.clear()
            self.previous_navigation = None
            return
        # Keep only navigation results, not active sessions, notifications, or
        # worker pages. Those must observe each saved generation independently.
        if self.previous_navigation is None:
            retained = {key: value for key, value in self.values.items()
                        if key[0] in {'workspace-index', 'workspaces', 'chat-registry', 'chat-index', 'chats', 'directory-chats'}}
            self.previous_navigation = (self.values.get(('shell-data-key',)), retained)
        index = self.values.get(('session-index',))
        # Directory workspace entries carry their complete aggregate/registry
        # inputs. A chat title or worker update cannot invalidate those facts.
        self.values = {key: value for key, value in self.values.items()
                       if self.directory is not None and key[0] in
                       {'workspace-index', 'workspace-counts', 'workspaces', 'workspace-shortcuts', 'directory-chats', 'pinned-navigation'}}
        if index is not None and session_ids is not None and index.patch(state, session_ids):
            self.values[('session-index',)] = index

    def pins_changed(self, state):
        """A pin changes ordering, not chat facts or workspace membership.

        Reuse only already-current indexes. An earlier broader invalidation
        still owns revalidation; never revive its potentially stale rows.
        """
        if self.observation_listener is not None:
            self.observation_listener(state, set(), False)
        if self.previous_navigation is not None:
            self.invalidate(state=state, session_ids=set())
            return
        pins = {sid: i for i, sid in enumerate(state.get('pinnedSessionIds', []))}
        positions = self.sessions(state).positions
        retained = {}
        for key, value in self.values.items():
            if key[0] in {'session-index', 'attention', 'workspace-index', 'workspaces', 'chat-registry'}:
                retained[key] = value
            elif key[0] == 'chat-index':
                rows, scope, counts = value
                sort = scope.get('sort', 'activity')
                pin_first = key[-1]
                rows = [{**row, 'pinned': row['id'] in pins} for row in rows]
                rows.sort(key=lambda row: (not row['pinned'] if pin_first else False, pins[row['id']] if pin_first and row['pinned'] else
                    row['title'].lower() if sort == 'name' else
                    -(row['createdAt'] or 0) if sort == 'created' else -row['recentActivityAt'], positions[row['id']]))
                retained[key] = (rows, scope, counts)
        self.values = retained

    def workspace_pins_changed(self):
        """Workspace order changes no chat facts, queries or client pages."""
        self.values = {key: value for key, value in self.values.items()
                       if key[0] not in {'workspaces', 'pinned-navigation', 'workspace-shortcuts', 'shell-data-key'}}
        if self.previous_navigation is not None:
            previous, retained = self.previous_navigation
            self.previous_navigation = (previous, {key: value for key, value in retained.items()
                                                  if key[0] != 'workspaces'})

    def refresh_navigation(self, state):
        if self.previous_navigation is not None:
            previous, retained = self.previous_navigation
            self.previous_navigation = None
            # The key covers all roots, including off-page errors, permissions,
            # organization, workspace metadata, and stable navigation recency.
            if previous is not None and self.shell_key(state) == previous:
                self.values.update(retained)

    def get(self, key, build):
        if key not in self.values:
            # A long-lived revision can still receive many different queries.
            if len(self.values) >= 256:
                self.values.clear()
            self.values[key] = build()
        return self.values[key]

    def directory_read(self, key, operation, state, args, build):
        owner = getattr(getattr(getattr(self.service, 'shell', None), 'resources', None), 'queries', None)
        if key not in self.values and owner is not None and owner.enabled and owner.context is not None:
            return owner.read(key, operation, state, args)
        return self.get(key, build)

    def sessions(self, state):
        from .browser_state import SessionIndex
        index = self.get(('session-index',), lambda: SessionIndex(state, directory=self.directory))
        index.directory = self.directory
        return index

    @measured('browser.detail', session=True)
    def detail(self, row):
        """Reuse only unchanged bounded bodies; current scalar facts stay live.

        A scoped commit evicts its identities; an unknown save evicts all.
        No client draft/selection is retained in this shared body cache.
        """
        from .work_windows import web_context
        if self.service is not None and web_context(self.service):
            from .browser_detail import project
            transcript = getattr(getattr(self.service, 'history', None), 'windows', None)
            events = getattr(getattr(self.service, 'event_log_view', None), 'windows', None)
            # Window bodies have their own bounded generation/cache. Do not
            # reuse the authoritative full-tree body cache for this surface.
            return project(row, resolver=self.resolver,
                message_overlay=transcript.project(row) if transcript is not None else None,
                work_overlay=events.project(row) if events is not None else None)
        fields = ('messages', 'messageWindow', 'sharedHistoryUserTurnOffset',
                  'execution', 'executionWindow', 'workers', 'workerSummary', 'generations', 'historyActivity')
        body = self.detail_bodies.pop(row['id'], None)
        if body is None:
            from .browser_state import project
            projected = project(row, resolver=self.resolver)
            body = deepcopy({key: projected[key] for key in fields if key in projected})
        self.detail_bodies[row['id']] = body
        while len(self.detail_bodies) > 32:
            self.detail_bodies.popitem(last=False)
        result = {key: value for key, value in row.items()
                  if key not in fields and key != 'messageQuotes'}
        result.update(body)
        from .peer_attribution import display_annotations
        result['messageAnnotations'] = display_annotations(row)
        return result

    def attention(self, state, *, session_ids=None):
        from .attention import snapshot
        directory = self.directory
        if directory is not None:
            from .directory_attention import snapshot as directory_snapshot
            scope = None if session_ids is None else tuple(sorted(set(session_ids)))
            return self.get(('attention', scope), lambda: directory_snapshot(directory, state, scope))
        index = self.sessions(state)
        return self.get(('attention',), lambda: snapshot({
            **state, 'sessions': [index.by_id[key] for key in index.attention_ids]}))

    def attention_receipts(self, state, identities):
        if self.directory is not None:
            from .directory_attention import receipts
            return receipts(self.directory, state, identities)
        return [{key: item[key] for key in ('id', 'sessionId', 'fingerprint', 'read')}
                for item in self.attention(state)['items'] if item.get('sessionId') in identities
                and item['id'] in {prefix + item['sessionId'] for prefix in ('session:', 'completion:', 'unread:')}]

    @staticmethod
    def view_scope(state, keys):
        view = state.get('view', {})
        return json.dumps({key: view[key] for key in keys if key in view}, sort_keys=True)

    @classmethod
    def chat_scope(cls, state):
        return (state.get('selectedSessionId'), state.get('selectedWorkspaceId'),
                cls.view_scope(state, ('navChatScope', 'navSort', 'navFilter', 'navStatusFilter', 'navLocationFilter',
                                      'navArchive', 'navCollection', 'navChatPage', 'navPinnedPage', 'navShowAgentCreated',
                                      'navRecentLimit', 'navRecentRevision')))

    @classmethod
    def workspace_scope(cls, state):
        from .workspace_navigation import workspace_pins
        return (state.get('selectedWorkspaceId'), tuple(workspace_pins(state)),
                cls.view_scope(state, ('navWorkspaceBrowseFor', 'navWorkspacePath',
                                      'navWorkspaceFilter', 'navWorkspacePage', 'navWorkspaceMode', 'navWorkspaceArchive')))

    @property
    def directory(self):
        return getattr(self.service, 'directory', None)

    def workspace_index(self, state):
        from .workspace_navigation import _index
        archive = state.get('view', {}).get('navWorkspaceArchive', 'active')
        directory = self.directory
        key = ('workspace-index', archive, *self.workspace_dependencies(state)) if directory else ('workspace-index', archive)
        def build():
            aggregates = self.directory_read(('workspace-counts', *key[1:]), 'workspace_counts', state,
                {'archive': archive}, lambda: directory.workspace_counts(state, archive=archive)) if directory else None
            return _index(state, aggregates=aggregates)
        return self.get(key, build)

    def workspace_dependencies(self, state):
        resources = getattr(getattr(self.service, 'shell', None), 'resources', None)
        # Workspace summaries exclude archived chats. Both registry and archive
        # commits must refresh them; standalone owners have no commit publisher.
        fields = {'workspaces', 'conversationOrganization'}
        registry = (resources._epochs(fields) if resources is not None else
                    json.dumps({key: state.get(key) for key in sorted(fields)},
                               sort_keys=True, separators=(',', ':')))
        stamp = (self.directory.workspace_revision, registry)
        if getattr(self, '_workspace_dependencies', None) != stamp:
            workspace_keys = {'workspace-index', 'workspace-counts', 'workspaces', 'workspace-shortcuts', 'pinned-navigation'}
            self.values = {key: value for key, value in self.values.items() if key[0] not in workspace_keys}
            if self.previous_navigation is not None:
                previous, retained = self.previous_navigation
                self.previous_navigation = (previous, {key: value for key, value in retained.items()
                                                       if key[0] not in workspace_keys})
            self._workspace_dependencies = stamp
        return stamp

    def workspaces(self, state):
        if self.directory is None:
            self.refresh_navigation(state)
        from .workspace_navigation import _index, snapshot
        index = self.workspace_index(state)
        dependencies = self.workspace_dependencies(state) if self.directory else ()
        return self.get(('workspaces', *dependencies, *self.workspace_scope(state)), lambda: snapshot(state, index=index))

    def chat_catalog(self, state, *, section=None):
        self.refresh_navigation(state)
        from .chat_navigation import catalog, registry, snapshot
        view = state.get('view', {})
        workspace = None if view.get('navChatScope') == 'all' else state.get('selectedWorkspaceId')
        filters = {key: view.get(key) for key in ('navChatScope', 'navSort', 'navFilter', 'navStatusFilter', 'navLocationFilter', 'navArchive', 'navCollection')}
        if section:
            filters['navStatusFilter'] = 'all'
        catalog_state = {**state, 'view': {**view, 'navStatusFilter': 'all'}} if section else state
        registrations = self.get(('chat-registry',), lambda: registry(state))
        pin_first = section not in ('recent', 'shortcuts')
        index = self.get(('chat-index', workspace, json.dumps(filters, sort_keys=True), pin_first),
                         lambda: catalog(catalog_state, indexed=registrations, pin_first=pin_first))
        return index

    def root_navigation_dependencies(self, state):
        resources = getattr(getattr(self.service, 'shell', None), 'resources', None)
        fields = {'workspaces', 'pinnedSessionIds', 'conversationOrganization'}
        globals_key = (resources._epochs(fields) if resources is not None else
                      json.dumps({key: state.get(key) for key in sorted(fields)}, sort_keys=True))
        stamp = (self.directory.root_navigation_revision, globals_key)
        if getattr(self, '_root_navigation_dependencies', None) != stamp:
            root_keys = {'directory-chats', 'pinned-navigation', 'root-selection'}
            self.values = {key: value for key, value in self.values.items() if key[0] not in root_keys}
            if self.previous_navigation is not None:
                previous, retained = self.previous_navigation
                self.previous_navigation = (previous, {key: value for key, value in retained.items()
                                                       if key[0] not in root_keys})
            self._root_navigation_dependencies = stamp
        return stamp

    def chats(self, state, *, section=None):
        directory = self.directory
        if directory is not None:
            dependencies = self.root_navigation_dependencies(state)
            scope = self.navigation_page_scope(state, section=section)
            page = self.directory_read(('directory-chats', *dependencies, section, *scope),
                'chats', state, {'section': section}, lambda: directory.chats(state, section=section))
            if section in {'shortcuts', 'pinned'} and page['scope']['selectedSessionId'] != state.get('selectedSessionId'):
                # Selection is a receipt, except for the selected agent-created
                # root admitted by the Recent filter. Never mutate a shared page.
                return {**page, 'scope': {**page['scope'], 'selectedSessionId': state.get('selectedSessionId')}}
            return page
        from .chat_navigation import snapshot
        index = self.chat_catalog(state, section=section)
        return self.get(('chats', section, *self.chat_scope(state)), lambda: snapshot(state, indexed=index, section=section))

    def navigation_page_scope(self, state, *, section):
        selected, workspace, view = self.chat_scope(state)
        if self.directory is None or section not in {'shortcuts', 'pinned'}:
            return selected, workspace, view
        if section == 'pinned' or state.get('view', {}).get('navShowAgentCreated') is True:
            selected = None
        elif selected:
            def exception():
                sql, params = self.directory.effective(state, public_ids=[selected])
                row = self.directory.db.execute(sql +
                    " SELECT id FROM directory_rows WHERE id=? AND session_kind='root' AND agent_created LIMIT 1",
                    [*params, selected]).fetchone()
                return row[0] if row else None
            selected = self.get(('root-selection', self.directory.root_navigation_revision, selected), exception)
        if state.get('view', {}).get('navChatScope') == 'all':
            workspace = None
        # General pages retain selection: their saved-page match and inferred
        # workspace rank are selection-dependent. Shortcuts have no such page.
        return selected, workspace, view

    def pinned_navigation(self, state):
        from .pinned_navigation import ordered_ids
        dependencies = ((*self.root_navigation_dependencies(state), *self.workspace_dependencies(state))
                        if self.directory is not None else ())
        key = ('pinned-navigation', *dependencies, tuple(ordered_ids(state)),
               *self.navigation_page_scope(state, section='pinned'))
        return self.get(key, lambda: self._pinned_navigation(state, key=key))

    def _pinned_navigation(self, state, *, key=None):
        from .workspace_navigation import _index, snapshot as workspace_snapshot
        from .pinned_navigation import snapshot
        scoped = {**state, 'view': {**state.get('view', {}), 'navWorkspaceMode': 'recent',
                                   'navWorkspaceArchive': 'active', 'navWorkspaceFilter': '', 'navWorkspacePage': 1}}
        rows = []
        if state.get('pinnedWorkspaceIds'):
            index = self.workspace_index(scoped)
            rows = workspace_snapshot(scoped, index=index, pinned_only=True)['rows']
        workspace = state.get('selectedWorkspaceId')
        if state.get('view', {}).get('navChatScope') != 'all':
            rows = [row for row in rows if row['workspaceId'] == workspace]
        if self.directory is not None:
            from .history_directory_query import pinned_navigation
            if key is None:
                return pinned_navigation(self.directory, state, rows)
            return self.directory_read(key, 'pinned_navigation', state, {'workspaces': rows},
                lambda: pinned_navigation(self.directory, state, rows))
        return snapshot(state, self.chat_catalog(state, section='pinned')[0], rows)

    def workspace_shortcuts(self, state, *, workspace_id=None, exclude_pinned=False):
        from .workspace_navigation import _index, snapshot
        scoped = {**state, 'view': {**state.get('view', {}), 'navWorkspaceMode': 'recent',
                                   'navWorkspaceArchive': 'active', 'navWorkspaceFilter': '', 'navWorkspacePage': 1}}
        index = self.workspace_index(scoped)
        dependencies = self.workspace_dependencies(scoped) if self.directory else ()
        return self.get(('workspace-shortcuts', workspace_id, exclude_pinned, *dependencies, *self.workspace_scope(scoped)),
                        lambda: snapshot(scoped, index=index, workspace_id=workspace_id,
                                         exclude_pinned=exclude_pinned)['rows'])

    def browser(self, state, *, selected_context=False):
        from .browser_state import navigation, subagent_navigation
        from .browser_demand import attention_session_scope
        attention = self.attention(state, session_ids=attention_session_scope(state) if selected_context else None)
        scoped = {**state, 'attention': attention}
        if selected_context:
            result = {'attention': attention, 'browserResources': ['selectedContext']}
            if state.get('view', {}).get('panel') == 'subagent-history':
                key = ('browser-navigation', state.get('selectedSessionId'),
                       self.directory.navigation_revision if self.directory else None,
                       self.view_scope(state, ('subagentHistory',)))
                page = self.get(key, lambda: subagent_navigation(scoped, index=self.sessions(state), directory=self.directory))
                requested = state.get('view', {}).get('subagentHistory', {})
                requested = requested if isinstance(requested, dict) else {}
                requested_index = requested.get('index', 0)
                result['subagentNavigation'] = {**page, 'scope': {**page['scope'],
                    'index': max(0, requested_index) if type(requested_index) is int else 0}}
                result['browserResources'].append('subagentNavigation')
            return result
        # Layout/drafts do not affect navigation. Workspace controls belong to
        # their own explorer query, while worker history only affects this one.
        key = ('browser-navigation', *self.chat_scope(state), self.directory.navigation_revision if self.directory else None, self.view_scope(state, ('subagentHistory',)))
        navigation_state = self.get(key, lambda: navigation(scoped, chats=self.chats, index=self.sessions(state), directory=self.directory))
        return {**navigation_state, 'attention': attention, 'workspaceExplorer': self.workspaces(scoped)}

    @measured('navigation.catalog.key')
    def shell_key(self, state):
        """Invalidate shell queries for their data, not unrelated host telemetry.

        Include the whole navigation catalog: a change outside the browser's
        current page can affect a pinned or independently filtered shell module.
        Selection, canvas and selected-chat summaries are added by the client.
        """
        def build():
            from .attention import reviewed_session_errors
            from .chat_navigation import navigation_activity
            from .session_navigation import is_agent_created
            from .navigation_summary import activity, task_blocked
            attention = self.attention(state, session_ids=())
            reviewed_errors = reviewed_session_errors(attention)
            fields = ('id', 'title', 'description', 'status', 'workspace', 'workspaceId', 'location',
                      'titleSource', 'nativeNameSource', 'autoName', 'naming', 'configurationBusy',
                      'runtimeSessionId', 'nativeIdentity', 'createdAt')
            rows = [] if self.directory else [([row.get(key) for key in fields], bool(is_agent_created(row)), navigation_activity(row),
                     activity(row, bool(attention['sessions'].get(row['id'])),
                              error_reviewed=row['id'] in reviewed_errors,
                              blocked=task_blocked(state, row['id'])))
                    for row in self.sessions(state).roots]
            facts = [self.directory.navigation_revision if self.directory else None, [] if self.directory else rows, state.get('settings', {}).get('workspaces'), state.get('workspaceDefaults'), state.get('workspaces', []), state.get('pinnedSessionIds'), state.get('pinnedWorkspaceIds', []), state.get('pinnedItemIds', []),
                     state.get('pinOrderCustomized'), state.get('conversationOrganization'),
                     {key: attention.get(key) for key in ('total', 'unread', 'badgeUnread', 'sections', 'sessions')},
                     [[item['id'], item['fingerprint'], item['read']] for item in attention['items']
                      if item.get('sessionId') and item['id'] == 'session:' + item['sessionId']],
                     {key: state.get('sharedHistory', {}).get(key) for key in ('loading', 'refreshing', 'error')},
                     state.get('locationListing'), state.get('actionStatus', {}).get('locations.list'), state.get('actionStatus', {}).get('locations.create')]
            return hashlib.sha256(json.dumps(facts, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        return self.get(('shell-data-key',), build)
