"""Bounded browser projections over a shared, nonresident history directory.

Directory queries provide pages/counts; SessionIndex owns admitted live rows.
Explicit full-state compatibility is separate from ordinary browser projections.
"""
from collections.abc import Callable
from typing import Any
from copy import deepcopy
from collections import OrderedDict
from .browser_detail import project
from .chat_navigation import ChatPage, snapshot as chat_snapshot, _matches, recent_activity
from .session_navigation import is_top_level
from .session_activity import navigation_busy

SUMMARY_FIELDS = ('location', 'id', 'title', 'titleSource', 'nativeNameSource', 'autoName', 'naming', 'configurationBusy', 'configurationPending', 'configurationRefresh', 'fileAccessPending', 'description', 'status', 'workspace', 'workingDirectory', 'executionRevision',
    'workspaceId', 'workspaceAvailable', 'bundle', 'createdAt', 'recentActivityAt', 'navigationActivityAt',
    'sessionKind', 'sessionPurpose', 'parentId', 'nativeParentId', 'nativeIdentity', 'nativeProject',
    'runtimeSessionId', 'historyManaged', 'historyLoaded', 'historyReadOnlyReason', 'nativeAvailable',
    'sharedHistoryTotal', 'turnCount', 'unreadCompletion', 'creationCommandId')
ACTIVE = {'starting', 'working', 'running', 'stopping'}


def summary(session):
    return {**{key: session[key] for key in SUMMARY_FIELDS if key in session},
            'messages': [], 'workers': [], 'approvals': []}


def direct_child(row, parent):
    if not parent or row.get('id') == parent.get('id') or is_top_level(row):
        return False
    if row.get('parentId') == parent.get('id'):
        return True
    aliases = {parent.get(key) for key in ('id', 'nativeIdentity', 'runtimeSessionId')} - {None}
    if row.get('nativeParentId') not in aliases:
        return False
    return (row.get('nativeProject') == parent.get('nativeProject') if row.get('nativeProject') and parent.get('nativeProject')
            else row.get('workspace') == parent.get('workspace'))


class SessionIndex:
    """Shared catalog facts for one saved generation; no client drafts or views."""
    def __init__(self, state, *, directory=None):
        self.directory = directory
        self.by_id = {}
        self.positions = {}
        self.parents = {}
        self.native_parents = {}
        self.active = set()
        self.notifications = []
        self.first_root = None
        self.roots = []
        self.attention_ids = set()
        self._membership = {}
        self.workspaces = {row['id']: row for row in state.get('workspaces', [])}
        self.workspace_positions = {row['id']: position
                                    for position, row in enumerate(state.get('workspaces', []))}
        for position, row in enumerate(state.get('sessions', [])):
            identity = row['id']
            self.by_id[identity] = row
            self.positions[identity] = position
            self._membership[identity] = (row.get('parentId'), row.get('nativeParentId'), is_top_level(row))
            if any(row.get(key) for key in ('questions', 'completion', 'manualUnread', 'error', 'approvals')):
                self.attention_ids.add(identity)
            if navigation_busy(row):
                self.active.add(identity)
            if is_top_level(row):
                self.roots.append(row)
                if self.first_root is None:
                    self.first_root = row
            else:
                self.parents.setdefault(row.get('parentId'), set()).add(identity)
                self.native_parents.setdefault(row.get('nativeParentId'), set()).add(identity)
            if row.get('sessionKind') != 'internal':
                from .cold_display import notifications
                self.notifications.extend(notifications(row))
        self.notifications.extend(state.get('scheduleNotifications', []))
        self.notifications.sort(key=lambda row: row.get('createdAt', 0))
        self.notifications = self.notifications[-100:]

    def patch(self, state, identities):
        """Update admitted rows without walking the complete session library."""
        rows = state.get('sessions', [])
        if len(self.workspaces) != len(state.get('workspaces', [])):
            self.workspaces = {row['id']: row for row in state.get('workspaces', [])}
            self.workspace_positions = {row['id']: position
                                        for position, row in enumerate(state.get('workspaces', []))}
        if len(rows) < len(self.positions):
            return False  # Unscoped removals require rebuilding the index.
        appended = rows[len(self.positions):]
        identities = set(identities) | {row['id'] for row in appended}
        roots_changed = False
        for row in appended:
            self.positions[row['id']] = len(self.positions)
            self.by_id[row['id']] = row
        for identity in identities:
            position = self.positions.get(identity)
            if position is None or position >= len(rows) or rows[position]['id'] != identity:
                return False
            row = rows[position]
            old = self._membership.get(identity)
            current = (row.get('parentId'), row.get('nativeParentId'), is_top_level(row))
            if old != current:
                roots_changed = roots_changed or bool(current[2] or (old and old[2]))
                if old:
                    self.parents.get(old[0], set()).discard(identity)
                    self.native_parents.get(old[1], set()).discard(identity)
                    if old[2]:
                        self.roots = [item for item in self.roots if item['id'] != identity]
                if current[2]:
                    self.roots.append(row)
                else:
                    self.parents.setdefault(current[0], set()).add(identity)
                    self.native_parents.setdefault(current[1], set()).add(identity)
                self._membership[identity] = current
            self.by_id[identity] = row
            self.active.discard(identity)
            if navigation_busy(row):
                self.active.add(identity)
            self.attention_ids.discard(identity)
            if any(row.get(key) for key in ('questions', 'completion', 'manualUnread', 'error', 'approvals')):
                self.attention_ids.add(identity)
        if roots_changed:
            self.roots.sort(key=lambda row: self.positions[row['id']])
        self.first_root = self.roots[0] if self.roots else None
        # Notifications are bounded presentation facts; only changed rows can
        # contribute new assistant messages. Preserve independent schedule rows.
        self.notifications = [row for row in self.notifications if row.get('sessionId') not in identities]
        for identity in identities:
            row = self.by_id[identity]
            if row.get('sessionKind') != 'internal':
                from .cold_display import notifications
                self.notifications.extend(notifications(row))
        self.notifications.sort(key=lambda row: row.get('createdAt', 0))
        self.notifications = self.notifications[-100:]
        return True

    def children(self, parent):
        if not parent:
            return []
        candidates = set(self.parents.get(parent.get('id'), ()))
        for alias in (parent.get('id'), parent.get('nativeIdentity'), parent.get('runtimeSessionId')):
            if alias is not None:
                candidates.update(self.native_parents.get(alias, ()))
        return [self.by_id[key] for key in sorted(candidates, key=self.positions.__getitem__)
                if direct_child(self.by_id[key], parent)]


def navigation(state, *, chats: Callable[[dict[str, Any]], ChatPage] = chat_snapshot,
               index=None, directory=None):
    index = index or SessionIndex(state)
    chat = chats(state)
    header_view = {**state.get('view', {}), 'navChatScope': 'workspace', 'navFilter': '', 'navArchive': 'active', 'navCollection': None}
    scope = {'mode': 'workspace', 'workspaceId': state.get('selectedWorkspaceId'),
             'filter': '', 'selectedSessionId': state.get('selectedSessionId')}
    header_view['navChatPage'] = {**scope, 'index': 0}
    header = chats({**state, 'view': header_view})
    selected = index.by_id.get(state.get('selectedSessionId'))
    if selected and is_top_level(selected) and selected.get('workspaceId') == scope['workspaceId'] and all(row['id'] != selected['id'] for row in header['items']):
        header = {**header, 'items': header['items'][:99] + [{**summary(selected), 'pinned': selected['id'] in state.get('pinnedSessionIds', [])}]}
    workers = subagent_navigation(state, index=index, directory=directory)
    return {'chatNavigation': chat, 'headerChatNavigation': {'items': header['items'], 'total': header['total']},
            'subagentNavigation': workers}


def subagent_navigation(state, *, index=None, directory=None):
    index = index or SessionIndex(state)
    requested = state.get('view', {}).get('subagentHistory', {})
    requested = requested if isinstance(requested, dict) else {}
    parent = requested.get('sessionId') or state.get('selectedSessionId')
    query = requested.get('filter', '')
    query = query if isinstance(query, str) else ''
    parent_row = index.by_id.get(parent, {})
    if directory is not None:
        parent_row = parent_row or directory.resolve(parent) or {}
        workers = directory.children(state, parent_row, query, requested.get('index', 0))
        workers['scope']['sessionId'] = parent
    else:
        all_children = index.children(parent_row)
        children = [row for row in all_children if _matches((row.get('title', ''), row.get('description', ''), row['id'], row.get('nativeIdentity', '')), query)]
        children.sort(key=lambda row: -recent_activity(row))
        pages = max(1, (len(children) + 49) // 50)
        index = requested.get('index', 0)
        index = max(0, min(pages - 1, index)) if type(index) is int else 0
        start, end = index * 50, min(len(children), (index + 1) * 50)
        workers = {'items': [summary(row) for row in children[start:end]], 'total': len(children), 'unfilteredTotal': len(all_children),
                   'index': index, 'pages': pages, 'start': start, 'end': end,
                   'scope': {'sessionId': parent, 'filter': query}}
    return workers


class SnapshotCopies:
    """Reuse detached, read-only sections, never references into mutable state.

    Keep one previous frame per recent browser, not a history of revisions. A
    full value comparison detects in-place edits and saves without a revision
    change. Independent browsers never share this cache entry.
    """
    def __init__(self, limit=8):
        self.limit = limit
        self.frames = OrderedDict()

    def detach(self, value, identity):
        previous = self.frames.pop(identity, {})
        result = {key: previous[key] if key in previous and previous[key] == item
                  else deepcopy(item) for key, item in value.items()}
        # Callers add client-specific top-level fields after detaching. Keep a
        # separate dictionary so those overlays cannot change this baseline.
        self.frames[identity] = dict(result)
        while len(self.frames) > self.limit:
            self.frames.popitem(last=False)
        return result


def snapshot(state, derived, *, session_id=None, index=None, copies=None, client_id=None,
             detail_project=None, demand=False, directory=None):
    index = index or SessionIndex(state)
    directory = directory or index.directory
    result = dict(state)
    result.update(derived)
    from .appearance_activity import snapshot as appearance_activity
    result['appActivity'] = appearance_activity(state, index)
    if 'browserResources' in derived:
        # The bundled shell owns navigation pages. Do not accidentally retain a
        # prior full-state projection when constructing a selected-chat frame.
        for name in ('chatNavigation', 'headerChatNavigation', 'workspaceExplorer', 'subagentNavigation'):
            if name not in derived:
                result.pop(name, None)
    visible = {row['id'] for name in ('chatNavigation', 'headerChatNavigation', 'subagentNavigation')
               for row in derived.get(name, {}).get('items', [])}
    selected = state.get('selectedSessionId')
    selected_row = index.by_id.get(selected) or (directory.resolve(selected) if directory else None) or {}
    visible.add(selected_row.get('parentId'))
    full = {selected, session_id, state.get('voice', {}).get('sessionId')}
    if client_id is None:
        full.update(index.active)  # Legacy unbound readers retain compatibility.
    visible.update(full)
    workers = derived.get('subagentNavigation')
    if workers is not None:
        visible.add(workers['scope']['sessionId'])
    from .browser_demand import worker_history_count
    count_requested = 'browserResources' not in derived or worker_history_count(state)
    selected_children = ((workers['unfilteredTotal'] if workers is not None and workers['scope']['sessionId'] == selected
                          else directory.child_count(state, selected_row) if directory else len(index.children(selected_row)))
                         if selected_row and count_requested else None)
    detail_project = detail_project or project
    visible_rows = dict(index.by_id)
    if directory is not None:
        visible_rows.update({row['id']:row for row in directory.summary_ids(visible - visible_rows.keys(), state=state)})
    result['sessions'] = [{**((row if row['id']==session_id else detail_project(row)) if row['id'] in full and row['id'] in index.by_id else summary(row)),
                           **({'subagentCount': selected_children} if row['id'] == selected and count_requested else {})}
                          for row in (visible_rows[key] for key in sorted(visible & visible_rows.keys(), key=lambda key: (index.positions.get(key, len(index.positions)), key)))]
    from .conversation_library import projection as organization_projection
    result['conversationOrganization'] = organization_projection(state, visible, directory=directory)
    # Report history is read through bounded coordination cursors, never copied
    # into every browser progress snapshot. The worker's latest report remains.
    result['sessions'] = [{**row, 'workers': [{key: value for key, value in worker.items() if key != 'reportReceipts'}
                           for worker in row.get('workers', [])]} for row in result['sessions']]
    # Only the selected owner's current, indexed waits; no transcript or peer
    # configuration loads, and this read never consumes a return notification.
    if directory is not None and selected_row:
        from .navigation_summary import activity
        from .navigation_waiting import task_for, target_details, saved_task
        selected_task = task_for(state, selected_row) or saved_task(directory.db, selected_row['id'])
        result['selectedActivity'] = {**activity(selected_row, task=selected_task, db=directory.db),
                                      'sessionId': selected_row['id']}
        if result['selectedActivity'].get('waiting'):
            result['selectedActivity']['waiting'] = target_details(directory.db, result['selectedActivity']['waiting'])
    workspace_ids = {row.get('workspaceId') for row in result['sessions']} | {
        state.get('selectedWorkspaceId'), state.get('view', {}).get('workWorkspaceId')}
    explorer = derived.get('workspaceExplorer', {})
    workspace_ids.update(row.get('workspaceId') for row in explorer.get('rows', []))
    result['workspaces'] = [index.workspaces[key] for key in
                            sorted(workspace_ids & index.workspaces.keys(),
                                   key=index.workspace_positions.__getitem__)]
    result['runtimeControl'] = {key: value for key, value in state.get('runtimeControl', {}).items() if key in full}
    result['canvasArtifacts'] = [row for row in state.get('canvasArtifacts', [])
                                 if row.get('sessionId') == selected or row.get('id') == state.get('canvas', {}).get('id')]
    result['devices'] = {key: {field: row[field] for field in ('clientId', 'updatedAt', 'online', 'viewport', 'capabilities') if field in row}
                         for key, row in state.get('devices', {}).items()}
    result['notificationMessages'] = [{key: value for key, value in row.items()
                                      if key != 'text' or state.get('notificationSettings', {}).get('preview', True)}
                                     for row in index.notifications]
    result['library'] = {'workspaceCount': len(state.get('workspaces', [])),
                         'bounded': True, 'detailPath': '/api/state/detail'}
    if 'browserResources' not in derived:
        # Explicit and independent readers retain the complete library contract.
        # Bundled selected-context frames do not consume these fields: a native
        # cache write must not make every publication count the whole directory
        # or search dormant roots for an unused continuation suggestion.
        selected_root = selected_row if selected_row and is_top_level(selected_row) else None
        first = directory.select("session_kind='root'", limit=1,
                                 order='created DESC, ordinal DESC, id', state=state) if directory and not selected_root else []
        continuation = selected_root or (first[0] if first else index.first_root)
        result['library'].update(sessionCount=directory.root_count(state) if directory else len(index.roots),
                                 continueSessionId=continuation['id'] if continuation else None)
    for key in ('attentionRead', 'nativePresentation', 'conversationExports'):
        result.pop(key, None)
    if demand and client_id is not None and session_id is None:
        from .browser_demand import project as demand_project
        result = demand_project(result)
    return copies.detach(result, client_id) if copies is not None else deepcopy(result)
